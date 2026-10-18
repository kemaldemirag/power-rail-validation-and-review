from __future__ import annotations

import copy
import json
import math
import unittest
from decimal import Decimal
from pathlib import Path

from power_rail_validation.contracts import sha256_hexdigest
from power_rail_validation.sequencing import (
    ENGINE_VERSION,
    FSM_VERSION,
    RULE_SET_VERSION,
    SORTING_POLICY,
    TOOL_NAME,
    evaluate_sequencing,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sequencing"
SYNTHETIC_RULE_SOURCE = FIXTURES / "synthetic-rules-source.json"


def _timestamp_for_offset(value: object) -> str:
    decimal = Decimal(str(value))
    whole = int(decimal)
    fraction = decimal - Decimal(whole)
    fraction_text = ""
    if fraction:
        fraction_text = format(fraction, "f")[1:]
    return f"2026-10-18T12:00:{whole:02d}{fraction_text}Z"


def _set_event_offset(event: dict, value: object) -> None:
    event["time_offset"] = {"state": "KNOWN", "value": value, "unit": "s"}
    event["occurred_at"] = _timestamp_for_offset(value)


def load_case(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        case = json.load(handle)
    if "base" not in case:
        return case
    with (FIXTURES / case["base"]).open("r", encoding="utf-8") as handle:
        base = json.load(handle)
    input_value = copy.deepcopy(base["input"])
    input_value["scenario_id"] = case["scenario_id"]
    if "scenario_kind" in case:
        input_value["scenario_kind"] = case["scenario_kind"]
    if "keep_event_ids" in case:
        keep = set(case["keep_event_ids"])
        input_value["events"] = [event for event in input_value["events"] if event["event_id"] in keep]
    if "replace_events" in case:
        input_value["events"] = copy.deepcopy(case["replace_events"])
    if "event_offsets" in case:
        for event in input_value["events"]:
            if event["event_id"] in case["event_offsets"]:
                _set_event_offset(event, case["event_offsets"][event["event_id"]])
    if "append_clone" in case:
        specification = case["append_clone"]
        source = next(event for event in input_value["events"] if event["event_id"] == specification["source_event_id"])
        clone = copy.deepcopy(source)
        clone["event_id"] = specification["event_id"]
        _set_event_offset(clone, specification["offset"])
        input_value["events"].append(clone)
    if "unknown_maximum_rule_id" in case:
        rule = next(rule for rule in input_value["timing_rules"] if rule["rule_id"] == case["unknown_maximum_rule_id"])
        rule["maximum_delay"] = {"state": "UNKNOWN", "value": None, "unit": "s"}
    if "observation_end_offset" in case:
        input_value["observation_end_offset"] = {
            "state": "KNOWN",
            "value": case["observation_end_offset"],
            "unit": "s",
        }
        input_value["observation_end_at"] = _timestamp_for_offset(
            case["observation_end_offset"]
        )
    return {"input": input_value, "expected": case["expected"]}


def issue_codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def event_by_id(input_value: dict, event_id: str) -> dict:
    return next(event for event in input_value["events"] if event["event_id"] == event_id)


def rule_by_id(input_value: dict, rule_id: str) -> dict:
    return next(rule for rule in input_value["timing_rules"] if rule["rule_id"] == rule_id)


class SequencingEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.normal = load_case("normal.json")["input"]

    def test_normal_fixture_passes_with_versioned_deterministic_trace(self) -> None:
        expected = load_case("normal.json")["expected"]

        result = evaluate_sequencing(self.normal)

        self.assertEqual(expected["status"], result["status"])
        self.assertEqual(expected["reason_code"], result["reason_code"])
        self.assertEqual(expected["final_state"], result["final_state"])
        self.assertEqual("SEQ-NORMAL-SYNTHETIC-001", result["scenario_id"])
        self.assertEqual("STARTUP", result["scenario_kind"])
        self.assertEqual(FSM_VERSION, result["fsm_version"])
        self.assertEqual(RULE_SET_VERSION, result["rule_set_version"])
        self.assertEqual(ENGINE_VERSION, result["tool_version"])
        self.assertEqual(TOOL_NAME, result["tool_name"])
        self.assertEqual(4, len(result["trace"]))
        self.assertEqual(["EV-VIN", "EV-EN", "EV-PGOOD", "EV-RESET"], [entry["event_id"] for entry in result["trace"]])
        self.assertTrue(all(entry["outcome"] == "PASS" for entry in result["trace"]))
        self.assertEqual(["RAIL-IN"], result["trace"][0]["expected_event"]["subject_refs"])
        self.assertEqual(["RAIL-IN"], result["trace"][0]["subject_refs"])
        self.assertEqual("2026-10-18T12:00:01Z", result["observation"]["end_at"])
        self.assertEqual(["SRC-SEQ-RULES"], result["observation"]["source_refs"])
        self.assertIn("SYNTHETIC_TIMING_ONLY", result["limitations"])
        self.assertIn("GAP_03_OPEN", result["limitations"])

    def test_result_binds_exact_original_input_and_source_revision(self) -> None:
        result = evaluate_sequencing(self.normal)

        self.assertEqual(sha256_hexdigest(self.normal), result["input_hash"])
        self.assertEqual(
            [
                {
                    "source_id": "SRC-SEQ-RULES",
                    "version": "fixture-1",
                    "content_hash": "fc77ce66e85ad5f03019774f3135476d98cd9bc197422a7b00830d8c13b8278a",
                }
            ],
            result["source_refs"],
        )

    def test_synthetic_rule_source_hash_and_rule_content_are_real(self) -> None:
        with SYNTHETIC_RULE_SOURCE.open("r", encoding="utf-8") as handle:
            source_artifact = json.load(handle)
        source_record = self.normal["sources"][0]

        self.assertEqual(
            sha256_hexdigest(source_artifact),
            source_record["content_hash"]["value"],
        )
        self.assertEqual(source_artifact["timing_rules"], self.normal["timing_rules"])
        self.assertEqual(source_artifact["power_off_rule"], self.normal["power_off_rule"])
        self.assertEqual(
            "tests/fixtures/sequencing/synthetic-rules-source.json",
            source_record["locator"],
        )

    def test_same_input_is_deterministic_json_finite_and_not_mutated(self) -> None:
        before = copy.deepcopy(self.normal)

        first = evaluate_sequencing(self.normal)
        second = evaluate_sequencing(copy.deepcopy(self.normal))

        self.assertEqual(first, second)
        self.assertEqual(before, self.normal)
        json.dumps(first, allow_nan=False)

    def test_wrong_order_fixture_fails(self) -> None:
        case = load_case("wrong-order.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertIn(case["expected"]["reason_code_present"], issue_codes(result))
        violation = next(entry for entry in result["trace"] if entry["reason_code"] == "EVENT_SEQUENCE_VIOLATION")
        self.assertEqual("ORDER", violation["timing"]["violated_bound"])
        self.assertEqual("SEQ.VIN_VALID", violation["rule_id"])

    def test_timeout_fixture_fails_after_deadline(self) -> None:
        case = load_case("timeout.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertEqual(case["expected"]["final_state"], result["final_state"])
        self.assertIn(case["expected"]["reason_code_present"], issue_codes(result))
        timeout = result["trace"][-1]
        self.assertEqual("MAXIMUM", timeout["timing"]["violated_bound"])
        self.assertEqual(0.7, timeout["timing"]["measured"]["value"])

    def test_missing_at_exact_inclusive_deadline_is_unknown(self) -> None:
        case = load_case("missing-pgood.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertIn(case["expected"]["reason_code_present"], issue_codes(result))
        self.assertEqual("NOT_YET_EXCEEDED", result["trace"][-1]["timing"]["violated_bound"])

    def test_missing_just_before_deadline_unknown_and_just_after_fails(self) -> None:
        input_value = load_case("missing-pgood.json")["input"]
        input_value["observation_end_offset"]["value"] = 0.499999
        input_value["observation_end_at"] = _timestamp_for_offset(0.499999)
        before = evaluate_sequencing(input_value)
        input_value["observation_end_offset"]["value"] = 0.500001
        input_value["observation_end_at"] = _timestamp_for_offset(0.500001)
        after = evaluate_sequencing(input_value)

        self.assertEqual("UNKNOWN", before["status"])
        self.assertIn("OBSERVATION_WINDOW_INCOMPLETE", issue_codes(before))
        self.assertEqual("FAIL", after["status"])
        self.assertIn("REQUIRED_EVENT_TIMEOUT", issue_codes(after))

    def test_exact_minimum_and_maximum_boundaries_pass(self) -> None:
        at_minimum = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(at_minimum, "EV-PGOOD"), 0.2)
        _set_event_offset(event_by_id(at_minimum, "EV-RESET"), 0.3)
        at_maximum = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(at_maximum, "EV-PGOOD"), 0.5)
        _set_event_offset(event_by_id(at_maximum, "EV-RESET"), 0.9)

        minimum_result = evaluate_sequencing(at_minimum)
        maximum_result = evaluate_sequencing(at_maximum)

        self.assertEqual("PASS", minimum_result["status"])
        self.assertEqual("PASS", maximum_result["status"])
        self.assertEqual(0.1, minimum_result["trace"][2]["timing"]["measured"]["value"])
        self.assertEqual(0.4, maximum_result["trace"][2]["timing"]["measured"]["value"])

    def test_just_below_minimum_and_just_above_maximum_fail(self) -> None:
        below = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(below, "EV-PGOOD"), 0.199999)
        _set_event_offset(event_by_id(below, "EV-RESET"), 0.3)
        above = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(above, "EV-PGOOD"), 0.500001)
        _set_event_offset(event_by_id(above, "EV-RESET"), 0.7)

        below_result = evaluate_sequencing(below)
        above_result = evaluate_sequencing(above)

        self.assertEqual("FAIL", below_result["status"])
        self.assertIn("TIMING_BELOW_MINIMUM", issue_codes(below_result))
        self.assertEqual("MINIMUM", below_result["trace"][2]["timing"]["violated_bound"])
        self.assertEqual("FAIL", above_result["status"])
        self.assertIn("TIMING_ABOVE_MAXIMUM", issue_codes(above_result))
        self.assertEqual("MAXIMUM", above_result["trace"][2]["timing"]["violated_bound"])

    def test_known_bound_violation_is_not_hidden_by_other_unknown_bound(self) -> None:
        below = copy.deepcopy(self.normal)
        pgood_rule = rule_by_id(below, "SEQ.PGOOD")
        pgood_rule["maximum_delay"] = {"state": "UNKNOWN", "value": None, "unit": "s"}
        _set_event_offset(event_by_id(below, "EV-PGOOD"), 0.15)
        _set_event_offset(event_by_id(below, "EV-RESET"), 0.3)

        above = copy.deepcopy(self.normal)
        pgood_rule = rule_by_id(above, "SEQ.PGOOD")
        pgood_rule["minimum_delay"] = {"state": "UNKNOWN", "value": None, "unit": "s"}
        _set_event_offset(event_by_id(above, "EV-PGOOD"), 0.500001)
        _set_event_offset(event_by_id(above, "EV-RESET"), 0.7)

        below_result = evaluate_sequencing(below)
        above_result = evaluate_sequencing(above)

        self.assertEqual("FAIL", below_result["status"])
        self.assertIn("TIMING_BELOW_MINIMUM", issue_codes(below_result))
        self.assertEqual("FAIL", above_result["status"])
        self.assertIn("TIMING_ABOVE_MAXIMUM", issue_codes(above_result))

    def test_decimal_subtraction_preserves_decision_changing_low_order_digit(self) -> None:
        input_value = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(input_value, "EV-VIN"), 1e-100)
        _set_event_offset(event_by_id(input_value, "EV-EN"), 1)
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 1.1)
        _set_event_offset(event_by_id(input_value, "EV-RESET"), 1.2)
        en_rule = rule_by_id(input_value, "SEQ.EN")
        en_rule["minimum_delay"]["value"] = 1
        en_rule["maximum_delay"]["value"] = 1.5
        input_value["observation_end_offset"]["value"] = 2
        input_value["observation_end_at"] = _timestamp_for_offset(2)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("TIMING_BELOW_MINIMUM", issue_codes(result))
        en_trace = next(entry for entry in result["trace"] if entry["event_id"] == "EV-EN")
        self.assertEqual("MINIMUM", en_trace["timing"]["violated_bound"])
        self.assertEqual(
            "0." + "9" * 100,
            en_trace["timing"]["measured"]["decimal_text"],
        )

    def test_duplicate_semantic_transition_fixture_fails(self) -> None:
        case = load_case("duplicate-event.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertIn(case["expected"]["reason_code_present"], issue_codes(result))
        duplicate = next(entry for entry in result["trace"] if entry["reason_code"] == "DUPLICATE_TRANSITION")
        self.assertEqual("DUPLICATE", duplicate["timing"]["violated_bound"])

    def test_terminal_duplicate_and_generic_order_have_distinct_rule_ids(self) -> None:
        duplicate_input = copy.deepcopy(self.normal)
        duplicate = copy.deepcopy(event_by_id(duplicate_input, "EV-EN"))
        duplicate["event_id"] = "EV-EN-AFTER-RUNNING"
        _set_event_offset(duplicate, 0.6)
        duplicate_input["events"].append(duplicate)

        generic_input = copy.deepcopy(self.normal)
        generic = copy.deepcopy(event_by_id(generic_input, "EV-EN"))
        generic.update(
            {
                "event_id": "EV-OTHER-AFTER-RUNNING",
                "event_type": "REVIEW",
                "event_kind": "OTHER",
                "state": "ACTIVE",
                "subject_refs": ["MISC-SUBJECT"],
            }
        )
        _set_event_offset(generic, 0.6)
        generic_input["events"].append(generic)

        duplicate_result = evaluate_sequencing(duplicate_input)
        generic_result = evaluate_sequencing(generic_input)

        duplicate_trace = next(entry for entry in duplicate_result["trace"] if entry["event_id"] == "EV-EN-AFTER-RUNNING")
        generic_trace = next(entry for entry in generic_result["trace"] if entry["event_id"] == "EV-OTHER-AFTER-RUNNING")
        self.assertEqual("DUPLICATE_TRANSITION", duplicate_trace["reason_code"])
        self.assertEqual("SEQ.EN", duplicate_trace["rule_id"])
        self.assertEqual("EVENT_SEQUENCE_VIOLATION", generic_trace["reason_code"])
        self.assertEqual("REP02.SEQ.ORDER.TERMINAL", generic_trace["rule_id"])

    def test_duplicate_event_id_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        clone = copy.deepcopy(input_value["events"][1])
        _set_event_offset(clone, 0.15)
        input_value["events"].append(clone)

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("EVENT_ID_DUPLICATE", issue_codes(result))

    def test_duplicate_rule_id_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["timing_rules"][1]["rule_id"] = input_value["timing_rules"][0]["rule_id"]

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RULE_ID_DUPLICATE", issue_codes(result))

    def test_wrong_polarity_fails_with_expected_rule(self) -> None:
        input_value = copy.deepcopy(self.normal)
        event_by_id(input_value, "EV-PGOOD")["state"] = "DEASSERTED"

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("WRONG_EVENT_POLARITY", issue_codes(result))
        entry = next(entry for entry in result["trace"] if entry["event_id"] == "EV-PGOOD")
        self.assertEqual("SEQ.PGOOD", entry["rule_id"])
        self.assertEqual("STATE", entry["timing"]["violated_bound"])

    def test_subject_binding_is_an_exact_set_for_every_startup_transition(self) -> None:
        cases = (
            ("EV-VIN", "SEQ.VIN_VALID", ["RAIL-IN"]),
            ("EV-EN", "SEQ.EN", ["CTRL-EN"]),
            ("EV-PGOOD", "SEQ.PGOOD", ["RAIL-A"]),
            ("EV-RESET", "SEQ.RESET_RELEASE", ["CTRL-RESET"]),
        )
        for event_id, rule_id, expected_refs in cases:
            with self.subTest(event_id=event_id):
                input_value = copy.deepcopy(self.normal)
                event_by_id(input_value, event_id)["subject_refs"] = ["WRONG-SUBJECT"]

                result = evaluate_sequencing(input_value)

                self.assertEqual("FAIL", result["status"])
                self.assertIn("SUBJECT_BINDING_MISMATCH", issue_codes(result))
                entry = next(entry for entry in result["trace"] if entry["event_id"] == event_id)
                self.assertEqual(rule_id, entry["rule_id"])
                self.assertEqual(expected_refs, entry["expected_event"]["subject_refs"])
                self.assertEqual("SUBJECT", entry["timing"]["violated_bound"])

    def test_simultaneous_and_duplicate_matching_are_subject_binding_aware(self) -> None:
        simultaneous = copy.deepcopy(self.normal)
        simultaneous["simultaneous_policy"] = "ALLOW_ZERO_MIN_EXPECTED_ORDER"
        rule_by_id(simultaneous, "SEQ.PGOOD")["minimum_delay"]["value"] = 0
        _set_event_offset(event_by_id(simultaneous, "EV-PGOOD"), 0.1)
        event_by_id(simultaneous, "EV-PGOOD")["subject_refs"] = ["WRONG-SUBJECT"]

        duplicate = copy.deepcopy(self.normal)
        extra = copy.deepcopy(event_by_id(duplicate, "EV-EN"))
        extra["event_id"] = "EV-EN-WRONG-SUBJECT"
        extra["subject_refs"] = ["WRONG-SUBJECT"]
        _set_event_offset(extra, 0.15)
        duplicate["events"].append(extra)

        simultaneous_result = evaluate_sequencing(simultaneous)
        duplicate_result = evaluate_sequencing(duplicate)

        self.assertEqual("FAIL", simultaneous_result["status"])
        self.assertIn("SIMULTANEOUS_EVENT_CONFLICT", issue_codes(simultaneous_result))
        self.assertEqual("FAIL", duplicate_result["status"])
        self.assertNotIn("DUPLICATE_TRANSITION", issue_codes(duplicate_result))

    def test_simultaneous_events_are_rejected_by_default(self) -> None:
        input_value = copy.deepcopy(self.normal)
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 0.1)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("SIMULTANEOUS_EVENT_CONFLICT", issue_codes(result))

    def test_simultaneous_expected_chain_passes_only_when_profile_and_zero_min_allow(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["simultaneous_policy"] = "ALLOW_ZERO_MIN_EXPECTED_ORDER"
        rule_by_id(input_value, "SEQ.PGOOD")["minimum_delay"]["value"] = 0
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 0.1)

        result = evaluate_sequencing(input_value)

        self.assertEqual("PASS", result["status"])
        self.assertEqual(["EV-VIN", "EV-EN", "EV-PGOOD", "EV-RESET"], [entry["event_id"] for entry in result["trace"]])
        self.assertEqual(0, result["trace"][2]["timing"]["measured"]["value"])

    def test_simultaneous_allow_policy_still_rejects_nonzero_minimum(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["simultaneous_policy"] = "ALLOW_ZERO_MIN_EXPECTED_ORDER"
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 0.1)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("SIMULTANEOUS_EVENT_CONFLICT", issue_codes(result))

    def test_occurred_at_and_offset_coordinate_conflict_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        event_by_id(input_value, "EV-PGOOD")["occurred_at"] = "2026-10-18T12:00:00.31Z"

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TIME_COORDINATE_CONFLICT", issue_codes(result))

    def test_observation_horizon_is_evidence_bound_and_coordinate_checked(self) -> None:
        conflict = copy.deepcopy(self.normal)
        conflict["observation_end_at"] = "2026-10-18T12:00:01.000001Z"
        unresolved = copy.deepcopy(self.normal)
        unresolved["observation_source_refs"] = ["SRC-NOT-PRESENT"]
        mismatched = copy.deepcopy(self.normal)
        mismatched["observation_provenance"] = "SOURCE_EXPLICIT"

        conflict_result = evaluate_sequencing(conflict)
        unresolved_result = evaluate_sequencing(unresolved)
        mismatched_result = evaluate_sequencing(mismatched)

        self.assertEqual("BLOCKED", conflict_result["status"])
        self.assertIn("OBSERVATION_TIME_COORDINATE_CONFLICT", issue_codes(conflict_result))
        self.assertEqual("BLOCKED", unresolved_result["status"])
        self.assertIn("SOURCE_REF_UNRESOLVED", issue_codes(unresolved_result))
        self.assertEqual("BLOCKED", mismatched_result["status"])
        self.assertIn(
            "OBSERVATION_SOURCE_CLASSIFICATION_MISMATCH",
            issue_codes(mismatched_result),
        )

    def test_observation_only_source_is_gated_and_emitted(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["sources"].append(
            {
                "schema_version": "1.0.0",
                "entity_type": "source",
                "source_id": "SRC-OBSERVATION",
                "classification": "SOURCE_EXPLICIT",
                "title": "Synthetic observation clock capture",
                "version": "capture-2026-10-18",
                "locator": "tests/fixtures/sequencing/normal.json#observation",
                "access_status": "AVAILABLE",
                "content_hash": {
                    "state": "KNOWN",
                    "algorithm": "SHA-256",
                    "value": "f" * 64,
                },
            }
        )
        input_value["observation_provenance"] = "SOURCE_EXPLICIT"
        input_value["observation_source_refs"] = ["SRC-OBSERVATION"]

        result = evaluate_sequencing(input_value)

        self.assertEqual("PASS", result["status"])
        by_id = {item["source_id"]: item for item in result["source_refs"]}
        self.assertIn("SRC-OBSERVATION", by_id)
        self.assertEqual("capture-2026-10-18", by_id["SRC-OBSERVATION"]["version"])
        self.assertEqual(["SRC-OBSERVATION"], result["observation"]["source_refs"])

    def test_invalid_time_unit_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        rule_by_id(input_value, "SEQ.PGOOD")["maximum_delay"]["unit"] = "ms"

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TIME_UNIT_INVALID", issue_codes(result))

    def test_negative_time_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        rule_by_id(input_value, "SEQ.EN")["minimum_delay"]["value"] = -0.1

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("NEGATIVE_TIME_INVALID", issue_codes(result))

    def test_nonfinite_time_blocks_and_cannot_be_hashed(self) -> None:
        input_value = copy.deepcopy(self.normal)
        rule_by_id(input_value, "SEQ.PGOOD")["maximum_delay"]["value"] = math.inf

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("FINITE_NUMBER_REQUIRED", issue_codes(result))
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", issue_codes(result))

    def test_maximum_below_minimum_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        rule_by_id(input_value, "SEQ.PGOOD")["maximum_delay"]["value"] = 0.05

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TIMING_WINDOW_INVALID", issue_codes(result))

    def test_unresolved_required_source_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["events"][0]["source_refs"] = ["SRC-NOT-PRESENT"]

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_REF_UNRESOLVED", issue_codes(result))

    def test_signal_kind_event_type_profile_is_enforced(self) -> None:
        startup = copy.deepcopy(self.normal)
        event_by_id(startup, "EV-PGOOD")["event_type"] = "POWER_STATE"
        power_off = load_case("power-off.json")["input"]
        power_off["events"][0]["event_type"] = "SIGNAL_TRANSITION"

        startup_result = evaluate_sequencing(startup)
        power_off_result = evaluate_sequencing(power_off)

        self.assertEqual("FAIL", startup_result["status"])
        self.assertIn("EVENT_TYPE_INCOMPATIBLE", issue_codes(startup_result))
        startup_trace = next(entry for entry in startup_result["trace"] if entry["event_id"] == "EV-PGOOD")
        self.assertEqual("FAIL", startup_trace["outcome"])
        self.assertEqual("EVENT_TYPE_INCOMPATIBLE", startup_trace["reason_code"])
        self.assertEqual("EVENT_TYPE", startup_trace["timing"]["violated_bound"])
        self.assertEqual("FAIL", power_off_result["status"])
        self.assertIn("EVENT_TYPE_INCOMPATIBLE", issue_codes(power_off_result))
        self.assertEqual("EVENT_TYPE_INCOMPATIBLE", power_off_result["trace"][0]["reason_code"])
        self.assertEqual("REP02.SEQ.POWER_OFF", power_off_result["trace"][0]["rule_id"])

    def test_trusted_event_provenance_must_match_every_source_classification(self) -> None:
        input_value = copy.deepcopy(self.normal)
        event_by_id(input_value, "EV-PGOOD")["provenance"] = "SOURCE_EXPLICIT"

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("EVENT_SOURCE_CLASSIFICATION_MISMATCH", issue_codes(result))

    def test_unavailable_or_hash_unknown_required_source_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        source = input_value["sources"][0]
        source["access_status"] = "MISSING"
        source["content_hash"] = {"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_UNAVAILABLE", issue_codes(result))
        self.assertIn("SOURCE_HASH_UNKNOWN", issue_codes(result))

    def test_source_version_must_be_pinned(self) -> None:
        for version in (
            " ",
            "latest",
            "HEAD",
            "origin/main",
            "nightly/2026-10-18",
            "1.x",
            "1.*",
            "^1.2.3",
            ">=1.2",
            "~1.2",
            "branch/feature",
            "refs/heads/release",
        ):
            with self.subTest(version=version):
                input_value = copy.deepcopy(self.normal)
                input_value["sources"][0]["version"] = version
                result = evaluate_sequencing(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertTrue({"SOURCE_VERSION_MISSING", "SOURCE_VERSION_FLOATING"} & issue_codes(result))

    def test_conflicted_required_source_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["sources"][0]["classification"] = "CONFLICT"

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_CONFLICT", issue_codes(result))

    def test_unknown_event_time_yields_unknown_not_timeout(self) -> None:
        input_value = copy.deepcopy(self.normal)
        event_by_id(input_value, "EV-PGOOD")["time_offset"] = {"state": "UNKNOWN", "value": None, "unit": "s"}

        result = evaluate_sequencing(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("EVENT_TIME_UNKNOWN", issue_codes(result))
        self.assertNotIn("REQUIRED_EVENT_TIMEOUT", issue_codes(result))

    def test_unknown_event_state_yields_unknown_not_timeout(self) -> None:
        input_value = copy.deepcopy(self.normal)
        event_by_id(input_value, "EV-PGOOD")["state"] = "UNKNOWN"

        result = evaluate_sequencing(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("EVENT_STATE_UNKNOWN", issue_codes(result))
        self.assertNotIn("REQUIRED_EVENT_TIMEOUT", issue_codes(result))

    def test_unrelated_indeterminate_event_does_not_suppress_definite_timeout(self) -> None:
        input_value = load_case("timeout.json")["input"]
        unrelated = copy.deepcopy(self.normal["events"][0])
        unrelated.update(
            {
                "event_id": "EV-UNRELATED-UNKNOWN-TIME",
                "event_type": "REVIEW",
                "event_kind": "OTHER",
                "state": "ACTIVE",
                "subject_refs": ["MISC-SUBJECT"],
                "time_offset": {"state": "UNKNOWN", "value": None, "unit": "s"},
            }
        )
        input_value["events"].append(unrelated)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("REQUIRED_EVENT_TIMEOUT", issue_codes(result))

    def test_wrong_subject_indeterminate_event_does_not_suppress_timeout(self) -> None:
        input_value = load_case("timeout.json")["input"]
        candidate = copy.deepcopy(event_by_id(self.normal, "EV-PGOOD"))
        candidate["event_id"] = "EV-PGOOD-WRONG-SUBJECT-UNKNOWN-TIME"
        candidate["subject_refs"] = ["WRONG-SUBJECT"]
        candidate["time_offset"] = {"state": "UNKNOWN", "value": None, "unit": "s"}
        input_value["events"].append(candidate)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("REQUIRED_EVENT_TIMEOUT", issue_codes(result))

    def test_indeterminate_candidate_before_anchor_does_not_suppress_timeout(self) -> None:
        input_value = load_case("timeout.json")["input"]
        input_value["observation_end_offset"] = {
            "state": "KNOWN",
            "value": 1,
            "unit": "s",
        }
        input_value["observation_end_at"] = _timestamp_for_offset(1)
        candidate = copy.deepcopy(event_by_id(self.normal, "EV-PGOOD"))
        candidate["event_id"] = "EV-PGOOD-UNKNOWN-BEFORE-ANCHOR"
        candidate["state"] = "UNKNOWN"
        _set_event_offset(candidate, 0.05)
        input_value["events"].append(candidate)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("REQUIRED_EVENT_TIMEOUT", issue_codes(result))

    def test_indeterminate_candidate_after_future_event_does_not_hide_wrong_order(self) -> None:
        input_value = copy.deepcopy(self.normal)
        pgood = event_by_id(input_value, "EV-PGOOD")
        pgood["state"] = "UNKNOWN"
        _set_event_offset(pgood, 0.4)
        _set_event_offset(event_by_id(input_value, "EV-RESET"), 0.3)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("EVENT_SEQUENCE_VIOLATION", issue_codes(result))
        self.assertNotIn("EVENT_ORDER_INDETERMINATE", issue_codes(result))
        reset_trace = next(
            entry for entry in result["trace"] if entry["event_id"] == "EV-RESET"
        )
        self.assertEqual("FAIL", reset_trace["outcome"])
        self.assertEqual("ORDER", reset_trace["timing"]["violated_bound"])

    def test_unknown_window_fixture_cannot_pass(self) -> None:
        case = load_case("unknown-window.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertIn(case["expected"]["reason_code_present"], issue_codes(result))
        self.assertEqual(case["expected"]["final_state"], result["final_state"])
        entry = next(entry for entry in result["trace"] if entry["event_id"] == "EV-PGOOD")
        self.assertEqual("UNKNOWN", entry["outcome"])

    def test_assumption_and_unverified_derived_provenance_cannot_support_pass(self) -> None:
        assumed_rule = copy.deepcopy(self.normal)
        rule_by_id(assumed_rule, "SEQ.PGOOD")["provenance"] = "ASSUMPTION"
        derived_event = copy.deepcopy(self.normal)
        event_by_id(derived_event, "EV-PGOOD")["provenance"] = "DERIVED"

        assumed_result = evaluate_sequencing(assumed_rule)
        derived_result = evaluate_sequencing(derived_event)

        self.assertEqual("UNKNOWN", assumed_result["status"])
        self.assertIn("PROVENANCE_UNCERTAIN", issue_codes(assumed_result))
        self.assertEqual("UNKNOWN", derived_result["status"])
        self.assertIn("PROVENANCE_UNCERTAIN", issue_codes(derived_result))

    def test_synthetic_pass_requires_hash_bound_engineering_rule_source(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["sources"][0]["classification"] = "SOURCE_EXPLICIT"

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("LIMIT_SOURCE_CLASSIFICATION_MISMATCH", issue_codes(result))

    def test_sourced_limit_violation_fails_against_source_explicit_rule(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["sources"].append(
            {
                "schema_version": "1.0.0",
                "entity_type": "source",
                "source_id": "SRC-PGOOD-SOURCED-LIMIT",
                "classification": "SOURCE_EXPLICIT",
                "title": "Synthetic stand-in for a pinned sourced PGOOD limit",
                "version": "datasheet-rev-A",
                "locator": "tests/fixtures/sequencing/normal.json#sourced-limit-test",
                "access_status": "AVAILABLE",
                "content_hash": {
                    "state": "KNOWN",
                    "algorithm": "SHA-256",
                    "value": "e" * 64,
                },
            }
        )
        rule = rule_by_id(input_value, "SEQ.PGOOD")
        rule["limit_origin"] = "SOURCED"
        rule["provenance"] = "SOURCE_EXPLICIT"
        rule["source_refs"] = ["SRC-PGOOD-SOURCED-LIMIT"]
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 0.500001)
        _set_event_offset(event_by_id(input_value, "EV-RESET"), 0.7)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("TIMING_ABOVE_MAXIMUM", issue_codes(result))
        pgood_trace = next(entry for entry in result["trace"] if entry["event_id"] == "EV-PGOOD")
        self.assertEqual("SEQ.PGOOD", pgood_trace["rule_id"])
        self.assertEqual("MAXIMUM", pgood_trace["timing"]["violated_bound"])
        self.assertIn(
            "SRC-PGOOD-SOURCED-LIMIT",
            {item["source_id"] for item in result["source_refs"]},
        )

    def test_invalid_source_metadata_is_sanitized_in_json_safe_output(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["sources"][0]["version"] = {"unexpected": "object"}
        input_value["sources"][0]["content_hash"]["value"] = math.nan

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["source_refs"][0]["version"])
        self.assertIsNone(result["source_refs"][0]["content_hash"])
        json.dumps(result, allow_nan=False)

    def test_json_object_key_order_does_not_change_hash_or_result(self) -> None:
        reordered = {key: self.normal[key] for key in reversed(list(self.normal))}

        original_result = evaluate_sequencing(self.normal)
        reordered_result = evaluate_sequencing(reordered)

        self.assertEqual(original_result, reordered_result)

    def test_unsorted_event_array_has_same_technical_outcome_and_canonical_trace(self) -> None:
        unsorted_input = copy.deepcopy(self.normal)
        unsorted_input["events"] = [
            unsorted_input["events"][2],
            unsorted_input["events"][0],
            unsorted_input["events"][3],
            unsorted_input["events"][1],
        ]

        normal_result = evaluate_sequencing(self.normal)
        unsorted_result = evaluate_sequencing(unsorted_input)

        self.assertEqual("PASS", unsorted_result["status"])
        self.assertEqual(normal_result["final_state"], unsorted_result["final_state"])
        self.assertEqual(
            [entry["event_id"] for entry in normal_result["trace"]],
            [entry["event_id"] for entry in unsorted_result["trace"]],
        )
        self.assertNotEqual(normal_result["input_hash"], unsorted_result["input_hash"])
        self.assertEqual([1, 3, 0, 2], [entry["input_index"] for entry in unsorted_result["trace"]])
        self.assertEqual([0, 1, 2, 3], [entry["processing_index"] for entry in unsorted_result["trace"]])

    def test_zero_delay_and_decimal_point_one_plus_point_two_boundaries_are_exact(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["simultaneous_policy"] = "ALLOW_ZERO_MIN_EXPECTED_ORDER"
        rule_by_id(input_value, "SEQ.EN")["minimum_delay"]["value"] = 0
        rule_by_id(input_value, "SEQ.EN")["maximum_delay"]["value"] = 0
        _set_event_offset(event_by_id(input_value, "EV-EN"), 0)
        rule_by_id(input_value, "SEQ.PGOOD")["minimum_delay"]["value"] = 0.3
        rule_by_id(input_value, "SEQ.PGOOD")["maximum_delay"]["value"] = 0.3
        _set_event_offset(event_by_id(input_value, "EV-PGOOD"), 0.3)

        result = evaluate_sequencing(input_value)

        self.assertEqual("PASS", result["status"])
        self.assertEqual(0, result["trace"][1]["timing"]["measured"]["value"])
        self.assertEqual(0.3, result["trace"][2]["timing"]["measured"]["value"])

    def test_deep_invalid_input_fails_closed_without_exception(self) -> None:
        nested: object = 0
        for _ in range(1200):
            nested = [nested]

        result = evaluate_sequencing({"unexpected": nested})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", issue_codes(result))

    def test_power_off_fixture_terminates_without_invented_shutdown_sequence(self) -> None:
        case = load_case("power-off.json")

        result = evaluate_sequencing(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertEqual(case["expected"]["reason_code"], result["reason_code"])
        self.assertEqual(case["expected"]["final_state"], result["final_state"])
        self.assertEqual("SEQ-POWER-OFF-001", result["scenario_id"])
        self.assertEqual("POWER_OFF", result["scenario_kind"])
        self.assertEqual("POWER_OFF_ENTERED_TERMINATES_WITHOUT_ASSUMED_SHUTDOWN_ORDER", result["policy"]["power_off_policy"])
        self.assertIn("POWER_OFF_SIGNAL_ORDER_NOT_INFERRED", result["limitations"])

    def test_power_off_subject_binding_is_enforced_in_both_scenario_profiles(self) -> None:
        for scenario_kind in ("POWER_OFF", "STARTUP"):
            with self.subTest(scenario_kind=scenario_kind):
                input_value = load_case("power-off.json")["input"]
                input_value["scenario_kind"] = scenario_kind
                input_value["events"][0]["subject_refs"] = ["UNRELATED-DOMAIN"]

                result = evaluate_sequencing(input_value)

                self.assertEqual("FAIL", result["status"])
                self.assertIn(
                    "POWER_OFF_SUBJECT_BINDING_MISMATCH",
                    issue_codes(result),
                )
                entry = result["trace"][0]
                self.assertEqual("REP02.SEQ.POWER_OFF", entry["rule_id"])
                self.assertEqual(
                    ["SYSTEM-POWER"],
                    entry["expected_event"]["subject_refs"],
                )
                self.assertEqual(["UNRELATED-DOMAIN"], entry["subject_refs"])
                self.assertEqual("SUBJECT", entry["timing"]["violated_bound"])

    def test_power_off_rule_is_mandatory_and_source_provenance_gated(self) -> None:
        missing = copy.deepcopy(self.normal)
        del missing["power_off_rule"]
        mismatched = copy.deepcopy(self.normal)
        mismatched["power_off_rule"]["provenance"] = "SOURCE_EXPLICIT"
        unresolved = copy.deepcopy(self.normal)
        unresolved["power_off_rule"]["source_refs"] = ["SRC-NOT-PRESENT"]

        missing_result = evaluate_sequencing(missing)
        mismatched_result = evaluate_sequencing(mismatched)
        unresolved_result = evaluate_sequencing(unresolved)

        self.assertEqual("BLOCKED", missing_result["status"])
        self.assertIn("POWER_OFF_RULE_REQUIRED", issue_codes(missing_result))
        self.assertEqual("BLOCKED", mismatched_result["status"])
        self.assertIn(
            "POWER_OFF_RULE_SOURCE_CLASSIFICATION_MISMATCH",
            issue_codes(mismatched_result),
        )
        self.assertEqual("BLOCKED", unresolved_result["status"])
        self.assertIn("SOURCE_REF_UNRESOLVED", issue_codes(unresolved_result))

    def test_power_off_wrong_polarity_and_event_after_power_off_fail(self) -> None:
        wrong = load_case("power-off.json")["input"]
        wrong["events"][0]["state"] = "EXITED"
        after = load_case("power-off.json")["input"]
        extra = copy.deepcopy(self.normal["events"][0])
        extra["event_id"] = "EV-AFTER-OFF"
        _set_event_offset(extra, 0.06)
        after["events"].append(extra)

        wrong_result = evaluate_sequencing(wrong)
        after_result = evaluate_sequencing(after)

        self.assertEqual("FAIL", wrong_result["status"])
        self.assertIn("POWER_OFF_POLARITY_INVALID", issue_codes(wrong_result))
        self.assertEqual("FAIL", after_result["status"])
        self.assertIn("UNEXPECTED_EVENT_IN_POWER_OFF_SCENARIO", issue_codes(after_result))

    def test_power_off_is_a_failure_inside_startup_scenario(self) -> None:
        input_value = load_case("power-off.json")["input"]
        input_value["scenario_kind"] = "STARTUP"

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("UNEXPECTED_POWER_OFF", issue_codes(result))
        self.assertEqual("POWERED_OFF", result["final_state"])

    def test_power_off_profile_applies_semantics_before_unknown_time(self) -> None:
        unknown_time = load_case("power-off.json")["input"]
        unknown_time["events"][0]["time_offset"] = {
            "state": "UNKNOWN",
            "value": None,
            "unit": "s",
        }
        wrong_polarity = copy.deepcopy(unknown_time)
        wrong_polarity["events"][0]["state"] = "EXITED"
        unrelated = copy.deepcopy(unknown_time)
        unrelated["events"][0].update(
            {
                "event_type": "REVIEW",
                "event_kind": "OTHER",
                "state": "ACTIVE",
                "subject_refs": ["MISC-SUBJECT"],
            }
        )

        unknown_result = evaluate_sequencing(unknown_time)
        polarity_result = evaluate_sequencing(wrong_polarity)
        unrelated_result = evaluate_sequencing(unrelated)

        self.assertEqual("UNKNOWN", unknown_result["status"])
        self.assertIn("EVENT_TIME_UNKNOWN", issue_codes(unknown_result))
        self.assertEqual("FAIL", polarity_result["status"])
        self.assertIn("POWER_OFF_POLARITY_INVALID", issue_codes(polarity_result))
        self.assertEqual("FAIL", unrelated_result["status"])
        self.assertIn("UNEXPECTED_EVENT_IN_POWER_OFF_SCENARIO", issue_codes(unrelated_result))

    def test_power_off_duplicate_trace_uses_power_off_policy_rule(self) -> None:
        input_value = load_case("power-off.json")["input"]
        duplicate = copy.deepcopy(input_value["events"][0])
        duplicate["event_id"] = "EV-POWER-OFF-REPEAT"
        _set_event_offset(duplicate, 0.06)
        input_value["events"].append(duplicate)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        entry = next(entry for entry in result["trace"] if entry["event_id"] == "EV-POWER-OFF-REPEAT")
        self.assertEqual("EVENT_AFTER_POWER_OFF", entry["reason_code"])
        self.assertEqual("REP02.SEQ.POWER_OFF", entry["rule_id"])

    def test_power_off_semantic_duplicate_fails_even_when_time_is_unknown(self) -> None:
        input_value = load_case("power-off.json")["input"]
        duplicate = copy.deepcopy(input_value["events"][0])
        duplicate["event_id"] = "EV-POWER-OFF-UNKNOWN-TIME-REPEAT"
        duplicate["time_offset"] = {
            "state": "UNKNOWN",
            "value": None,
            "unit": "s",
        }
        input_value["events"].append(duplicate)

        result = evaluate_sequencing(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("EVENT_AFTER_POWER_OFF", issue_codes(result))
        entry = next(
            item
            for item in result["trace"]
            if item["event_id"] == "EV-POWER-OFF-UNKNOWN-TIME-REPEAT"
        )
        self.assertEqual("FAIL", entry["outcome"])
        self.assertEqual("DUPLICATE", entry["timing"]["violated_bound"])
        self.assertEqual("REP02.SEQ.POWER_OFF", entry["rule_id"])

    def test_event_beyond_observation_end_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.normal)
        input_value["observation_end_offset"]["value"] = 0.4
        input_value["observation_end_at"] = _timestamp_for_offset(0.4)

        result = evaluate_sequencing(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("EVENT_AFTER_OBSERVATION_END", issue_codes(result))

    def test_contract_is_closed_and_policies_are_explicit(self) -> None:
        unexpected = copy.deepcopy(self.normal)
        unexpected["scenario_type"] = "STARTUP"
        wrong_sorting = copy.deepcopy(self.normal)
        wrong_sorting["sorting_policy"] = "INPUT_ORDER"
        wrong_simultaneous = copy.deepcopy(self.normal)
        wrong_simultaneous["simultaneous_policy"] = "INPUT_ORDER"
        wrong_kind = copy.deepcopy(self.normal)
        wrong_kind["scenario_kind"] = "SHUTDOWN"

        unexpected_result = evaluate_sequencing(unexpected)
        sorting_result = evaluate_sequencing(wrong_sorting)
        simultaneous_result = evaluate_sequencing(wrong_simultaneous)
        kind_result = evaluate_sequencing(wrong_kind)

        self.assertEqual("BLOCKED", unexpected_result["status"])
        self.assertIn("UNEXPECTED_FIELD", issue_codes(unexpected_result))
        self.assertEqual("BLOCKED", sorting_result["status"])
        self.assertIn("SORTING_POLICY_INVALID", issue_codes(sorting_result))
        self.assertEqual("BLOCKED", simultaneous_result["status"])
        self.assertIn("SIMULTANEOUS_POLICY_INVALID", issue_codes(simultaneous_result))
        self.assertEqual("BLOCKED", kind_result["status"])
        self.assertIn("SCENARIO_KIND_INVALID", issue_codes(kind_result))
        self.assertEqual(SORTING_POLICY, evaluate_sequencing(self.normal)["policy"]["sorting_policy"])


if __name__ == "__main__":
    unittest.main()
