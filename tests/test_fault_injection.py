from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts import sha256_hexdigest
from power_rail_validation.fault_injection import (
    ENGINE_VERSION,
    RULE_SET_VERSION,
    TOOL_NAME,
    run_fault_scenario,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "fault_injection"
RULE_SOURCE = FIXTURES / "synthetic-fault-rules-source.json"


def _merge(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def load_case(name: str) -> dict:
    descriptor = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if "base" in descriptor:
        base = load_case(descriptor["base"])
        input_value = copy.deepcopy(base["input"])
        input_value["scenario_id"] = descriptor["scenario_id"]
        input_value["generation"]["fixture_id"] = descriptor["fixture_id"]
        if "overlap_policy" in descriptor:
            input_value["overlap_policy"] = descriptor["overlap_policy"]
        if "faults" in descriptor:
            input_value["faults"] = copy.deepcopy(descriptor["faults"])
        if "faults_from" in descriptor:
            input_value["faults"] = []
            for source_name in descriptor["faults_from"]:
                input_value["faults"].extend(copy.deepcopy(load_case(source_name)["input"]["faults"]))
        if "patch" in descriptor:
            target = next(item for item in input_value["faults"] if item["fault_id"] == descriptor["patch"]["fault_id"])
            _merge(target, {key: value for key, value in descriptor["patch"].items() if key != "fault_id"})
        return {"input": input_value, "expected": descriptor["expected"]}

    input_value = copy.deepcopy(descriptor["input"])
    baseline = descriptor["baseline_fixtures"]
    for key, relative_path in baseline.items():
        source = json.loads((FIXTURES / relative_path).resolve().read_text(encoding="utf-8"))
        input_value[f"{key}_input" if key == "rail_budget" else "sequencing_input"] = source["input"]
    return {"input": input_value, "expected": descriptor["expected"]}


def finding_codes(result: dict) -> set[str]:
    return {item["reason_code"] for item in result["findings"]}


def fault_by_id(input_value: dict, fault_id: str) -> dict:
    return next(item for item in input_value["faults"] if item["fault_id"] == fault_id)


def target_by_id(input_value: dict, target_id: str) -> dict:
    return next(item for item in input_value["targets"] if item["target_id"] == target_id)


def _known_time(value: object) -> dict:
    return {"state": "KNOWN", "value": value, "unit": "s"}


def _unknown_time() -> dict:
    return {"state": "UNKNOWN", "value": None, "unit": "s"}


class FaultInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brownout = load_case("brownout.json")["input"]
        self.restart = load_case("restart.json")["input"]

    def test_required_single_fault_fixture_matrix(self) -> None:
        cases = (
            ("brownout.json", "BROWNOUT_EFFECT_TRIGGERED"),
            ("stuck-low.json", "STUCK_LOW_EFFECT_TRIGGERED"),
            ("delay.json", "DELAY_EFFECT_TRIGGERED"),
            ("rail-loss.json", "RAIL_LOSS_EFFECT_TRIGGERED"),
            ("restart.json", "RESTART_REQUEST_ACCEPTED"),
            ("unknown-threshold.json", "FAULT_EFFECT_UNKNOWN"),
        )
        for filename, reason in cases:
            with self.subTest(filename=filename):
                case = load_case(filename)
                result = run_fault_scenario(case["input"])
                fault_result = result["fault_results"][0]
                self.assertEqual(case["expected"]["status"], result["status"])
                self.assertEqual(case["expected"]["fault_status"], fault_result["status"])
                self.assertEqual(reason, fault_result["effect"]["reason_code"])
                self.assertTrue(fault_result["expectation"]["matches"])

    def test_recovery_combo_and_fault_during_restart_fixtures(self) -> None:
        for filename in ("recovery-combo.json", "fault-during-restart.json"):
            with self.subTest(filename=filename):
                case = load_case(filename)
                result = run_fault_scenario(case["input"])
                observed = {item["fault_id"]: item["status"] for item in result["fault_results"]}
                self.assertEqual(case["expected"]["status"], result["status"])
                self.assertEqual(case["expected"]["fault_statuses"], observed)
                self.assertNotIn("FAULT_OVERLAP_NOT_ALLOWED", finding_codes(result))

    def test_versions_hash_baselines_and_claim_limits_are_published(self) -> None:
        result = run_fault_scenario(self.brownout)

        self.assertEqual(ENGINE_VERSION, result["engine_version"])
        self.assertEqual(RULE_SET_VERSION, result["rule_set_version"])
        self.assertEqual(TOOL_NAME, result["tool_name"])
        self.assertEqual(PACKAGE_VERSION, result["package_version"])
        self.assertEqual(ENGINE_VERSION, result["versions"]["fault_engine"])
        self.assertEqual(RULE_SET_VERSION, result["versions"]["fault_rule_set"])
        self.assertEqual("REP02-RB-1.0.0", result["versions"]["rail_budget_formula"])
        self.assertEqual("REP02-SEQ-FSM-1.0.0", result["versions"]["sequencing_fsm"])
        self.assertEqual(sha256_hexdigest(self.brownout), result["input_hash"])
        self.assertEqual(["PASS", "PASS"], [item["status"] for item in result["engine_runs"]])
        self.assertEqual("PASS", result["baseline_results"]["rail_budget"]["status"])
        self.assertEqual("PASS", result["baseline_results"]["sequencing"]["status"])
        self.assertIn("NO_ANALOG_TRANSIENT_PHYSICS_MODEL", result["limitations"])
        self.assertIn("PASS_MEANS_ONLY_VERSIONED_TECHNICAL_FAULT_AND_RECOVERY_RULES_WERE_SATISFIED", result["claim_limits"])
        self.assertIn("EXPECTED_STATUS_MATCH_IS_SEPARATE_AND_DOES_NOT_CHANGE_TECHNICAL_STATUS", result["claim_limits"])
        self.assertIn("RESULT_IS_NOT_EMC_ISO_AEC_OR_FIELD_VALIDATION", result["claim_limits"])

    def test_fault_context_is_not_inserted_into_sequencing_events(self) -> None:
        before_count = len(self.brownout["sequencing_input"]["events"])
        result = run_fault_scenario(self.brownout)

        self.assertEqual(before_count, len(self.brownout["sequencing_input"]["events"]))
        self.assertEqual(before_count, len(result["baseline_results"]["sequencing"]["trace"]))
        self.assertNotIn("BROWNOUT", [item["event_id"] for item in result["baseline_results"]["sequencing"]["trace"]])
        self.assertIn("FAULT_CONTEXT_NOT_ADDED_TO_SEQUENCING_EVENTS", result["limitations"])

    def test_trace_is_observable_and_has_required_rule_fields(self) -> None:
        result = run_fault_scenario(self.brownout)

        self.assertEqual(["INJECTION", "EFFECT", "RECOVERY"], [item["phase"] for item in result["trace"]])
        for index, entry in enumerate(result["trace"]):
            self.assertEqual(index, entry["trace_index"])
            self.assertIn("event", entry)
            self.assertIn("rule_id", entry)
            self.assertIn("expected", entry)
            self.assertIn("observed", entry)
            self.assertIn("violated_bound", entry)
        self.assertEqual("THRESHOLD", result["trace"][1]["violated_bound"])
        self.assertEqual("RECOVERY_WITHIN_DEADLINE", result["trace"][2]["reason_code"])

    def test_same_input_is_deterministic_json_safe_and_not_mutated(self) -> None:
        before = copy.deepcopy(self.brownout)

        first = run_fault_scenario(self.brownout)
        second = run_fault_scenario(copy.deepcopy(self.brownout))

        self.assertEqual(first, second)
        self.assertEqual(before, self.brownout)
        json.dumps(first, allow_nan=False)
        self.assertNotIn("generated_at", first)

    def test_json_object_key_order_is_hash_and_result_invariant(self) -> None:
        reordered = {key: self.brownout[key] for key in reversed(self.brownout)}
        self.assertEqual(run_fault_scenario(self.brownout), run_fault_scenario(reordered))

    def test_real_rule_source_hash_and_profiles_are_bound(self) -> None:
        artifact = json.loads(RULE_SOURCE.read_text(encoding="utf-8"))
        record = self.brownout["sources"][0]

        self.assertEqual(sha256_hexdigest(artifact), record["content_hash"]["value"])
        self.assertEqual(RULE_SET_VERSION, artifact["rule_set_version"])
        self.assertEqual(sorted(item["fault_type"] for item in artifact["effect_profiles"]), sorted(["BROWNOUT", "STUCK_LOW", "DELAY", "RAIL_LOSS", "RESTART"]))
        self.assertEqual(record["content_hash"]["value"], run_fault_scenario(self.brownout)["sources"][0]["content_hash"])

    def test_expected_status_is_separate_and_does_not_change_technical_status(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["faults"][0]["expected_status"] = "PASS"

        result = run_fault_scenario(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertEqual("FAIL", result["fault_results"][0]["status"])
        self.assertFalse(result["fault_results"][0]["expectation"]["matches"])

    def test_successful_recovery_does_not_mask_known_fault_violation(self) -> None:
        result = run_fault_scenario(self.brownout)

        self.assertEqual("FAIL", result["fault_results"][0]["effect"]["status"])
        self.assertEqual("PASS", result["fault_results"][0]["recovery"]["status"])
        self.assertEqual("FAIL", result["status"])

    def test_restart_complete_recovery_can_pass(self) -> None:
        result = run_fault_scenario(self.restart)

        self.assertEqual("PASS", result["status"])
        self.assertEqual("PASS", result["fault_results"][0]["effect"]["status"])
        self.assertEqual("PASS", result["fault_results"][0]["recovery"]["status"])
        self.assertEqual(["FAULT_SCENARIO_COMPLETE"], result["reason_codes"])

    def test_recovery_exact_closed_deadline_passes_and_just_after_fails(self) -> None:
        at_deadline = copy.deepcopy(self.restart)
        at_deadline["faults"][0]["recovery"]["completion_time"] = _known_time(0.9)
        after = copy.deepcopy(at_deadline)
        after["faults"][0]["recovery"]["completion_time"] = _known_time(0.900001)

        exact_result = run_fault_scenario(at_deadline)
        after_result = run_fault_scenario(after)

        self.assertEqual("PASS", exact_result["status"])
        self.assertEqual("RECOVERY_WITHIN_DEADLINE", exact_result["fault_results"][0]["recovery"]["reason_code"])
        self.assertEqual("FAIL", after_result["status"])
        self.assertEqual("RECOVERY_DEADLINE_EXCEEDED", after_result["fault_results"][0]["recovery"]["reason_code"])

    def test_missing_recovery_at_exact_deadline_is_unknown_then_fails_after(self) -> None:
        exact = copy.deepcopy(self.restart)
        recovery = exact["faults"][0]["recovery"]
        recovery["completion"] = {"state": "KNOWN", "value": False}
        recovery["completion_time"] = _unknown_time()
        exact["observation"]["end_time"] = _known_time(0.9)
        after = copy.deepcopy(exact)
        after["observation"]["end_time"] = _known_time(0.900001)

        exact_result = run_fault_scenario(exact)
        after_result = run_fault_scenario(after)

        self.assertEqual("UNKNOWN", exact_result["status"])
        self.assertEqual("RECOVERY_WINDOW_INCOMPLETE", exact_result["fault_results"][0]["recovery"]["reason_code"])
        self.assertEqual("FAIL", after_result["status"])
        self.assertEqual("RECOVERY_MISSING_AFTER_DEADLINE", after_result["fault_results"][0]["recovery"]["reason_code"])

    def test_recovery_before_fault_end_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["faults"][0]["recovery"]["completion_time"] = _known_time(0.65)

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RECOVERY_BEFORE_FAULT_END", finding_codes(result))

    def test_recovery_after_observation_horizon_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.restart)
        recovery = input_value["faults"][0]["recovery"]
        recovery["completion_time"] = _known_time(2.1)
        recovery["maximum_delay"] = _known_time(2)

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RECOVERY_AFTER_OBSERVATION_END", finding_codes(result))

    def test_unknown_fault_start_or_duration_cannot_pass(self) -> None:
        for field in ("start_time", "duration"):
            with self.subTest(field=field):
                input_value = copy.deepcopy(self.restart)
                input_value["faults"][0][field] = _unknown_time()
                result = run_fault_scenario(input_value)
                self.assertEqual("UNKNOWN", result["status"])
                self.assertIn("FAULT_START_TIME_UNKNOWN" if field == "start_time" else "FAULT_DURATION_UNKNOWN", finding_codes(result))

    def test_zero_duration_is_blocked_except_for_explicit_instantaneous_restart(self) -> None:
        for filename in ("brownout.json", "stuck-low.json", "delay.json", "rail-loss.json"):
            with self.subTest(filename=filename):
                input_value = load_case(filename)["input"]
                input_value["faults"][0]["duration"] = _known_time(0)
                result = run_fault_scenario(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("ZERO_DURATION_NOT_ALLOWED", finding_codes(result))

        restart = copy.deepcopy(self.restart)
        restart["faults"][0]["duration"] = _known_time(0)
        result = run_fault_scenario(restart)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("POSITIVE_EXCEPT_INSTANTANEOUS_RESTART_MAY_BE_ZERO", result["policy"]["duration"])

    def test_none_recovery_unknown_placeholders_do_not_mask_pass(self) -> None:
        input_value = copy.deepcopy(self.restart)
        recovery = input_value["faults"][0]["recovery"]
        recovery.update(
            {
                "mode": "NONE",
                "required": False,
                "completion": {"state": "UNKNOWN", "value": None},
                "completion_time": _unknown_time(),
                "maximum_delay": _unknown_time(),
            }
        )

        result = run_fault_scenario(input_value)

        self.assertEqual("PASS", result["status"])
        self.assertEqual("RECOVERY_NOT_REQUIRED", result["fault_results"][0]["recovery"]["reason_code"])
        self.assertNotIn("RECOVERY_TIME_UNKNOWN", finding_codes(result))
        self.assertNotIn("RECOVERY_DEADLINE_UNKNOWN", finding_codes(result))

    def test_stuck_low_unknown_polarity_is_unknown(self) -> None:
        input_value = load_case("stuck-low.json")["input"]
        target_by_id(input_value, "CTRL-EN")["polarity"] = "UNKNOWN"
        input_value["faults"][0]["expected_status"] = "UNKNOWN"

        result = run_fault_scenario(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual("STUCK_LOW_POLARITY_UNKNOWN", result["fault_results"][0]["effect"]["reason_code"])

    def test_stuck_low_not_applicable_signal_polarity_is_blocked(self) -> None:
        input_value = load_case("stuck-low.json")["input"]
        target_by_id(input_value, "CTRL-EN")["polarity"] = "NOT_APPLICABLE"

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TARGET_SIGNAL_POLARITY_REQUIRED", finding_codes(result))

    def test_unknown_threshold_fixture_is_unknown_not_fail(self) -> None:
        result = run_fault_scenario(load_case("unknown-threshold.json")["input"])
        self.assertEqual("UNKNOWN", result["status"])
        self.assertNotIn("BROWNOUT_EFFECT_TRIGGERED", result["reason_codes"])

    def test_exact_effect_threshold_does_not_violate_open_operator(self) -> None:
        for filename in ("brownout.json", "delay.json"):
            with self.subTest(filename=filename):
                input_value = load_case(filename)["input"]
                threshold = input_value["faults"][0]["effect_rule"]["threshold"]["value"]
                input_value["faults"][0]["severity"]["value"] = threshold
                input_value["faults"][0]["expected_status"] = "PASS"
                result = run_fault_scenario(input_value)
                self.assertEqual("PASS", result["status"])
                self.assertEqual("FAULT_THRESHOLD_NOT_VIOLATED", result["fault_results"][0]["effect"]["reason_code"])

    def test_low_order_decimal_changes_effect_decision(self) -> None:
        exact = copy.deepcopy(self.brownout)
        exact["faults"][0]["severity"]["value"] = 0.8
        exact["faults"][0]["expected_status"] = "PASS"
        below = copy.deepcopy(exact)
        below["faults"][0]["severity"]["value"] = 0.7999999999999999
        below["faults"][0]["expected_status"] = "FAIL"

        self.assertEqual("PASS", run_fault_scenario(exact)["status"])
        self.assertEqual("FAIL", run_fault_scenario(below)["status"])

    def test_closed_schema_rejects_unexpected_root_and_nested_fields(self) -> None:
        for mutator in (
            lambda value: value.update({"generated_at": "now"}),
            lambda value: value["faults"][0].update({"notes": "outside contract"}),
            lambda value: value["faults"][0]["severity"].update({"tolerance": 0.1}),
            lambda value: value["faults"][0]["recovery"].update({"retry": 1}),
        ):
            with self.subTest(mutator=mutator):
                input_value = copy.deepcopy(self.brownout)
                mutator(input_value)
                result = run_fault_scenario(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("UNEXPECTED_FIELD", finding_codes(result))

    def test_schema_and_fixed_generation_are_enforced(self) -> None:
        wrong_schema = copy.deepcopy(self.brownout)
        wrong_schema["schema_version"] = "2.0.0"
        seeded = copy.deepcopy(self.brownout)
        seeded["generation"].update({"mode": "SEEDED", "seed": 42})
        wrong_runner = copy.deepcopy(self.brownout)
        wrong_runner["generation"]["runner_version"] = "latest"

        self.assertIn("SCHEMA_VERSION_INVALID", finding_codes(run_fault_scenario(wrong_schema)))
        self.assertIn("SEEDED_GENERATION_UNSUPPORTED", finding_codes(run_fault_scenario(seeded)))
        self.assertIn("RUNNER_VERSION_MISMATCH", finding_codes(run_fault_scenario(wrong_runner)))

    def test_unresolved_and_nonexact_target_bindings_are_blocked(self) -> None:
        unresolved = copy.deepcopy(self.brownout)
        unresolved["faults"][0]["target"]["target_id"] = "NOT-PRESENT"
        nonexact = copy.deepcopy(self.brownout)
        target_by_id(nonexact, "VDD_5V")["baseline_ref"] = "OTHER"

        self.assertIn("FAULT_TARGET_UNRESOLVED", finding_codes(run_fault_scenario(unresolved)))
        nonexact_codes = finding_codes(run_fault_scenario(nonexact))
        self.assertIn("TARGET_BINDING_NOT_EXACT", nonexact_codes)
        self.assertIn("TARGET_UNRESOLVED", nonexact_codes)

    def test_target_type_must_match_its_baseline_binding_domain(self) -> None:
        rail_as_load = copy.deepcopy(self.brownout)
        target = target_by_id(rail_as_load, "VDD_5V")
        target.update(
            {
                "target_id": "LOAD-MCU",
                "target_type": "RAIL",
                "baseline_binding": "RAIL_BUDGET_LOAD",
                "baseline_ref": "LOAD-MCU",
            }
        )
        rail_as_load["faults"][0]["target"] = {
            "target_id": "LOAD-MCU",
            "target_type": "RAIL",
        }

        system_as_rail = copy.deepcopy(self.restart)
        target = target_by_id(system_as_rail, "SYSTEM-POWER")
        target.update(
            {
                "target_id": "VDD_5V",
                "target_type": "SYSTEM",
                "baseline_binding": "RAIL_BUDGET_RAIL",
                "baseline_ref": "VDD_5V",
            }
        )
        system_as_rail["faults"][0]["target"] = {
            "target_id": "VDD_5V",
            "target_type": "SYSTEM",
        }

        for input_value in (rail_as_load, system_as_rail):
            with self.subTest(target=input_value["faults"][0]["target"]):
                result = run_fault_scenario(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("TARGET_TYPE_BINDING_MISMATCH", finding_codes(result))

    def test_sequencing_subject_signal_cannot_be_relabelled_as_system(self) -> None:
        input_value = copy.deepcopy(self.restart)
        system_target = target_by_id(input_value, "SYSTEM-POWER")
        system_target.update({"target_id": "CTRL-EN", "baseline_ref": "CTRL-EN"})
        input_value["faults"][0]["target"] = {"target_id": "CTRL-EN", "target_type": "SYSTEM"}

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TARGET_SEQUENCE_SUBJECT_TYPE_MISMATCH", finding_codes(result))

    def test_sequencing_subject_system_cannot_be_relabelled_as_signal(self) -> None:
        input_value = load_case("stuck-low.json")["input"]
        system_target = target_by_id(input_value, "SYSTEM-POWER")
        system_target.update({"target_type": "SIGNAL", "polarity": "ACTIVE_HIGH"})
        input_value["faults"][0]["target"] = {"target_id": "SYSTEM-POWER", "target_type": "SIGNAL"}

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TARGET_SEQUENCE_SUBJECT_TYPE_MISMATCH", finding_codes(result))

    def test_conflicting_sequencing_subject_semantics_are_blocked(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["sequencing_input"]["power_off_rule"]["expected_event"]["subject_refs"] = ["CTRL-EN"]
        system_target = target_by_id(input_value, "SYSTEM-POWER")
        system_target.update({"target_id": "CTRL-EN", "baseline_ref": "CTRL-EN"})
        input_value["faults"][0]["target"] = {"target_id": "CTRL-EN", "target_type": "SYSTEM"}

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TARGET_SEQUENCE_SUBJECT_TYPE_CONFLICT", finding_codes(result))

    def test_other_event_kind_cannot_establish_exact_subject_type(self) -> None:
        for event_kind in ("OTHER", [], {}):
            with self.subTest(event_kind=event_kind):
                input_value = copy.deepcopy(self.restart)
                input_value["sequencing_input"]["power_off_rule"]["expected_event"]["event_kind"] = event_kind

                result = run_fault_scenario(input_value)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("TARGET_SEQUENCE_SUBJECT_KIND_UNSUPPORTED", finding_codes(result))

    def test_non_signal_targets_require_not_applicable_polarity(self) -> None:
        target_profiles = (
            ("RAIL", "VDD_5V", "RAIL_BUDGET_RAIL"),
            ("LOAD", "LOAD-MCU", "RAIL_BUDGET_LOAD"),
            ("REGULATOR", "REG-5V", "RAIL_BUDGET_REGULATOR"),
            ("SYSTEM", "SYSTEM-POWER", "SEQUENCING_SUBJECT"),
        )
        for target_type, target_id, binding in target_profiles:
            for polarity in ("ACTIVE_HIGH", "ACTIVE_LOW", "UNKNOWN"):
                with self.subTest(target_type=target_type, polarity=polarity):
                    input_value = copy.deepcopy(self.brownout)
                    if target_type in {"RAIL", "SYSTEM"}:
                        target = target_by_id(input_value, target_id)
                    else:
                        target = copy.deepcopy(target_by_id(input_value, "VDD_5V"))
                        input_value["targets"].append(target)
                    target.update(
                        {
                            "target_id": target_id,
                            "target_type": target_type,
                            "baseline_binding": binding,
                            "baseline_ref": target_id,
                            "polarity": polarity,
                        }
                    )

                    result = run_fault_scenario(input_value)

                    self.assertEqual("BLOCKED", result["status"])
                    self.assertIn("TARGET_NON_SIGNAL_POLARITY_INVALID", finding_codes(result))
                    self.assertEqual(
                        "SIGNAL_ACTIVE_HIGH_ACTIVE_LOW_OR_UNKNOWN_NON_SIGNAL_NOT_APPLICABLE",
                        result["policy"]["target_polarity"],
                    )

    def test_fault_target_type_must_match_profile(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["faults"][0]["target"] = {"target_id": "CTRL-EN", "target_type": "SIGNAL"}
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FAULT_TARGET_TYPE_MISMATCH", finding_codes(result))

    def test_duplicate_fault_and_target_ids_are_blocked(self) -> None:
        variants = []
        duplicate_fault = load_case("recovery-combo.json")["input"]
        duplicate_fault["faults"][1]["fault_id"] = duplicate_fault["faults"][0]["fault_id"]
        variants.append((duplicate_fault, "FAULT_ID_DUPLICATE"))
        duplicate_target = copy.deepcopy(self.brownout)
        duplicate_target["targets"].append(copy.deepcopy(duplicate_target["targets"][0]))
        variants.append((duplicate_target, "TARGET_DUPLICATE"))
        for input_value, code in variants:
            with self.subTest(code=code):
                result = run_fault_scenario(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, finding_codes(result))

    def test_versioned_effect_and_recovery_rules_may_be_reused_across_distinct_fault_instances(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        second = copy.deepcopy(input_value["faults"][0])
        second["fault_id"] = "FAULT-BROWNOUT-002"
        second["start_time"] = _known_time(1.1)
        second["recovery"]["completion_time"] = _known_time(1.4)
        input_value["faults"].append(second)

        result = run_fault_scenario(input_value)

        self.assertNotIn("DUPLICATE_FAULT", finding_codes(result))
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(2, len(result["fault_results"]))

    def test_semantically_duplicate_fault_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        clone = copy.deepcopy(input_value["faults"][0])
        clone["fault_id"] = "FAULT-BROWNOUT-CLONE"
        clone["recovery"]["policy_id"] = "FI.RECOVERY.BROWNOUT.CLONE"
        input_value["faults"].append(clone)
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_FAULT", finding_codes(result))

    def test_signed_zero_cannot_evade_instantaneous_restart_duplicate_detection(self) -> None:
        input_value = copy.deepcopy(self.restart)
        first = input_value["faults"][0]
        first["start_time"] = _known_time(0.0)
        first["duration"] = _known_time(0.0)
        first["recovery"]["completion_time"] = _known_time(0.1)
        second = copy.deepcopy(first)
        second["fault_id"] = "FAULT-RESTART-SIGNED-ZERO"
        second["start_time"] = _known_time(-0.0)
        second["duration"] = _known_time(-0.0)
        input_value["faults"].append(second)
        input_value["overlap_policy"] = "ALLOW_RESTART_RECOVERY"

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_FAULT", finding_codes(result))

    def test_overlapping_faults_reject_by_default(self) -> None:
        input_value = load_case("recovery-combo.json")["input"]
        input_value["overlap_policy"] = "REJECT"
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FAULT_OVERLAP_NOT_ALLOWED", finding_codes(result))

    def test_restart_overlap_allowance_does_not_allow_two_nonrestart_faults(self) -> None:
        input_value = load_case("fault-during-restart.json")["input"]
        second = copy.deepcopy(load_case("delay.json")["input"]["faults"][0])
        input_value["faults"].append(second)
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FAULT_OVERLAP_NOT_ALLOWED", finding_codes(result))

    def test_touching_half_open_intervals_do_not_overlap(self) -> None:
        input_value = load_case("recovery-combo.json")["input"]
        input_value["overlap_policy"] = "REJECT"
        restart = fault_by_id(input_value, "FAULT-RESTART-001")
        restart["start_time"] = _known_time(0.8)
        restart["recovery"]["completion_time"] = _known_time(1.0)
        result = run_fault_scenario(input_value)
        self.assertNotIn("FAULT_OVERLAP_NOT_ALLOWED", finding_codes(result))
        self.assertEqual("FAIL", result["status"])

    def test_unknown_overlap_is_unknown_not_silently_allowed(self) -> None:
        input_value = load_case("recovery-combo.json")["input"]
        input_value["faults"][1]["start_time"] = _unknown_time()
        result = run_fault_scenario(input_value)
        self.assertEqual("UNKNOWN", result["fault_results"][1]["status"])
        self.assertIn("FAULT_OVERLAP_INDETERMINATE", finding_codes(result))

    def test_fault_after_observation_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["faults"][0]["start_time"] = _known_time(2.1)
        input_value["faults"][0]["recovery"]["completion_time"] = _known_time(2.3)
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FAULT_AFTER_OBSERVATION_END", finding_codes(result))

    def test_fault_extending_beyond_horizon_is_unknown_for_restart(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["faults"][0]["duration"] = _known_time(2)
        input_value["faults"][0]["recovery"]["completion"] = {"state": "UNKNOWN", "value": None}
        input_value["faults"][0]["recovery"]["completion_time"] = _unknown_time()
        result = run_fault_scenario(input_value)
        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("FAULT_EXTENDS_BEYOND_OBSERVATION", finding_codes(result))

    def test_unknown_observation_horizon_cannot_support_recovery_pass(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["observation"]["end_time"] = _unknown_time()
        result = run_fault_scenario(input_value)
        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("OBSERVATION_END_UNKNOWN", finding_codes(result))

    def test_blocked_baseline_propagates_blocked(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        del input_value["rail_budget_input"]["rail"]
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BASELINE_RAIL_BUDGET_BLOCKED", finding_codes(result))

    def test_relevant_baseline_fail_blocks_fault_attribution(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["rail_budget_input"]["capacity"]["rated_current"]["value"] = 0.1
        input_value["rail_budget_input"]["capacity"]["current_limit"]["value"] = 0.1
        result = run_fault_scenario(input_value)
        self.assertEqual("FAIL", result["baseline_results"]["rail_budget"]["status"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BASELINE_RAIL_BUDGET_FAIL_ATTRIBUTION_BLOCKED", finding_codes(result))

    def test_relevant_baseline_unknown_makes_causality_unknown(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["sequencing_input"]["timing_rules"][2]["maximum_delay"] = {"state": "UNKNOWN", "value": None, "unit": "s"}
        result = run_fault_scenario(input_value)
        self.assertEqual("UNKNOWN", result["baseline_results"]["sequencing"]["status"])
        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("BASELINE_SEQUENCING_UNKNOWN", finding_codes(result))

    def test_relevant_unknown_baseline_downgrades_apparent_fault_violation_to_causal_unknown(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["rail_budget_input"]["capacity"]["current_limit"] = {
            "state": "UNKNOWN",
            "value": None,
            "unit": "A",
        }
        input_value["faults"][0]["expected_status"] = "UNKNOWN"

        result = run_fault_scenario(input_value)

        self.assertEqual("UNKNOWN", result["baseline_results"]["rail_budget"]["status"])
        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual("UNKNOWN", result["fault_results"][0]["effect"]["status"])
        self.assertEqual("BASELINE_RAIL_BUDGET_UNKNOWN", result["fault_results"][0]["effect"]["reason_code"])

    def test_causal_unknown_baseline_does_not_mask_recovery_or_horizon_invalidity(self) -> None:
        variants = []
        before_end = copy.deepcopy(self.restart)
        before_end["faults"][0]["recovery"]["completion_time"] = _known_time(0.65)
        variants.append((before_end, "RECOVERY_BEFORE_FAULT_END"))

        after_observation = copy.deepcopy(self.restart)
        after_observation["faults"][0]["recovery"]["completion_time"] = _known_time(2.1)
        after_observation["faults"][0]["recovery"]["maximum_delay"] = _known_time(2)
        variants.append((after_observation, "RECOVERY_AFTER_OBSERVATION_END"))

        outside_horizon = copy.deepcopy(self.restart)
        outside_horizon["faults"][0]["start_time"] = _known_time(2.1)
        outside_horizon["faults"][0]["recovery"]["completion_time"] = _known_time(2.3)
        outside_horizon["faults"][0]["recovery"]["maximum_delay"] = _known_time(0.2)
        variants.append((outside_horizon, "FAULT_AFTER_OBSERVATION_END"))

        for input_value, code in variants:
            with self.subTest(code=code):
                input_value["sequencing_input"]["timing_rules"][2]["maximum_delay"] = {
                    "state": "UNKNOWN",
                    "value": None,
                    "unit": "s",
                }
                result = run_fault_scenario(input_value)
                self.assertEqual("UNKNOWN", result["baseline_results"]["sequencing"]["status"])
                self.assertEqual("BLOCKED", result["status"])
                self.assertEqual("BLOCKED", result["fault_results"][0]["status"])
                self.assertIn(code, finding_codes(result))

    def test_unrelated_baseline_fail_does_not_block_restart(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["rail_budget_input"]["capacity"]["rated_current"]["value"] = 0.1
        input_value["rail_budget_input"]["capacity"]["current_limit"]["value"] = 0.1
        result = run_fault_scenario(input_value)
        self.assertEqual("FAIL", result["baseline_results"]["rail_budget"]["status"])
        self.assertEqual("PASS", result["status"])

    def test_source_version_hash_access_and_refs_are_gated(self) -> None:
        variants = []
        floating = copy.deepcopy(self.brownout)
        floating["sources"][0]["version"] = "latest"
        variants.append((floating, "SOURCE_VERSION_FLOATING"))
        bad_hash = copy.deepcopy(self.brownout)
        bad_hash["sources"][0]["content_hash"]["value"] = "bad"
        variants.append((bad_hash, "SOURCE_HASH_VALUE_INVALID"))
        missing = copy.deepcopy(self.brownout)
        missing["sources"][0]["access_status"] = "MISSING"
        variants.append((missing, "SOURCE_UNAVAILABLE"))
        unresolved = copy.deepcopy(self.brownout)
        unresolved["faults"][0]["source_refs"] = ["SRC-NOT-PRESENT"]
        variants.append((unresolved, "SOURCE_REF_UNRESOLVED"))
        for input_value, code in variants:
            with self.subTest(code=code):
                result = run_fault_scenario(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, finding_codes(result))

    def test_provenance_classification_mismatch_blocks(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["faults"][0]["provenance"] = "SOURCE_EXPLICIT"
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_CLASSIFICATION_MISMATCH", finding_codes(result))

    def test_assumption_fault_and_recovery_cannot_support_pass(self) -> None:
        assumed_fault = copy.deepcopy(self.restart)
        assumed_fault["faults"][0]["provenance"] = "ASSUMPTION"
        assumed_fault["faults"][0]["source_refs"] = ["SRC-FI-RULES"]
        assumed_recovery = copy.deepcopy(self.restart)
        assumed_recovery["faults"][0]["recovery"]["provenance"] = "ASSUMPTION"
        assumed_optional = copy.deepcopy(self.restart)
        assumed_optional_recovery = assumed_optional["faults"][0]["recovery"]
        assumed_optional_recovery.update(
            {
                "mode": "NONE",
                "required": False,
                "completion": {"state": "UNKNOWN", "value": None},
                "completion_time": _unknown_time(),
                "maximum_delay": _unknown_time(),
                "provenance": "ASSUMPTION",
            }
        )

        fault_result = run_fault_scenario(assumed_fault)
        recovery_result = run_fault_scenario(assumed_recovery)
        optional_result = run_fault_scenario(assumed_optional)

        self.assertEqual("UNKNOWN", fault_result["status"])
        self.assertIn("PROVENANCE_UNCERTAIN", finding_codes(fault_result))
        self.assertEqual("UNKNOWN", recovery_result["status"])
        self.assertEqual("RECOVERY_PROVENANCE_UNCERTAIN", recovery_result["fault_results"][0]["recovery"]["reason_code"])
        self.assertEqual("UNKNOWN", optional_result["status"])
        self.assertEqual("UNKNOWN", optional_result["fault_results"][0]["recovery"]["status"])
        self.assertEqual("RECOVERY_PROVENANCE_UNCERTAIN", optional_result["fault_results"][0]["recovery"]["reason_code"])

    def test_untrusted_fault_or_effect_provenance_cannot_create_definite_failure(self) -> None:
        assumed_effect = copy.deepcopy(self.brownout)
        assumed_effect["faults"][0]["effect_rule"]["provenance"] = "ASSUMPTION"
        assumed_effect["faults"][0]["expected_status"] = "UNKNOWN"
        unknown_fault = copy.deepcopy(self.brownout)
        unknown_fault["faults"][0]["provenance"] = "UNKNOWN"
        unknown_fault["faults"][0]["expected_status"] = "UNKNOWN"

        effect_result = run_fault_scenario(assumed_effect)
        fault_result = run_fault_scenario(unknown_fault)

        self.assertEqual("UNKNOWN", effect_result["status"])
        self.assertEqual("UNKNOWN", effect_result["fault_results"][0]["effect"]["status"])
        self.assertEqual("EFFECT_RULE_PROVENANCE_UNCERTAIN", effect_result["fault_results"][0]["effect"]["reason_code"])
        self.assertNotIn("BROWNOUT_EFFECT_TRIGGERED", effect_result["reason_codes"])
        self.assertEqual("UNKNOWN", fault_result["status"])
        self.assertEqual("FAULT_PROVENANCE_UNCERTAIN", fault_result["fault_results"][0]["effect"]["reason_code"])

    def test_untrusted_observation_cannot_create_missing_recovery_failure(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["observation"]["provenance"] = "UNKNOWN"
        input_value["faults"][0]["recovery"]["completion"] = {
            "state": "KNOWN",
            "value": False,
        }
        input_value["faults"][0]["recovery"]["completion_time"] = _unknown_time()
        input_value["faults"][0]["expected_status"] = "UNKNOWN"

        result = run_fault_scenario(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("OBSERVATION_PROVENANCE_UNCERTAIN", finding_codes(result))
        self.assertEqual("RECOVERY_TIMING_UNKNOWN", result["fault_results"][0]["recovery"]["reason_code"])
        self.assertNotIn("RECOVERY_MISSING_AFTER_DEADLINE", result["reason_codes"])

    def test_recovery_completion_state_and_time_must_be_coherent(self) -> None:
        for completion in (
            {"state": "KNOWN", "value": False},
            {"state": "UNKNOWN", "value": None},
        ):
            with self.subTest(completion=completion):
                input_value = copy.deepcopy(self.restart)
                input_value["faults"][0]["recovery"]["completion"] = completion
                input_value["faults"][0]["recovery"]["completion_time"] = _known_time(0.8)

                result = run_fault_scenario(input_value)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("RECOVERY_COMPLETION_TIME_CONFLICT", finding_codes(result))

    def test_invalid_source_metadata_is_sanitized_in_json_safe_output(self) -> None:
        input_value = copy.deepcopy(self.brownout)
        input_value["sources"][0]["version"] = {"bad": "object"}
        input_value["sources"][0]["content_hash"]["value"] = math.nan
        result = run_fault_scenario(input_value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["sources"][0]["version"])
        self.assertIsNone(result["sources"][0]["content_hash"])
        json.dumps(result, allow_nan=False)

    def test_nonfinite_cycle_and_deep_inputs_fail_closed_json_safe(self) -> None:
        nonfinite = copy.deepcopy(self.brownout)
        nonfinite["faults"][0]["start_time"]["value"] = math.nan
        cycle: dict = {}
        cycle["self"] = cycle
        nested: object = 0
        for _ in range(1200):
            nested = [nested]
        for value in (nonfinite, cycle, {"unexpected": nested}):
            with self.subTest(kind=type(value).__name__):
                result = run_fault_scenario(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIsNone(result["input_hash"])
                self.assertIn("INPUT_HASH_UNCOMPUTABLE", finding_codes(result))
                json.dumps(result, allow_nan=False)

    def test_pathological_integer_time_fails_closed_without_precision_or_memory_escape(self) -> None:
        input_value = copy.deepcopy(self.restart)
        input_value["faults"][0]["duration"]["value"] = 10**20000

        result = run_fault_scenario(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", finding_codes(result))
        json.dumps(result, allow_nan=False)

    def test_wrong_units_types_and_negative_times_block(self) -> None:
        variants = []
        wrong_unit = copy.deepcopy(self.brownout)
        wrong_unit["faults"][0]["duration"]["unit"] = "ms"
        variants.append((wrong_unit, "TIME_UNIT_INVALID"))
        negative = copy.deepcopy(self.brownout)
        negative["faults"][0]["start_time"]["value"] = -1
        variants.append((negative, "NEGATIVE_TIME"))
        boolean = copy.deepcopy(self.brownout)
        boolean["faults"][0]["severity"]["value"] = True
        variants.append((boolean, "MEASURE_VALUE_INVALID"))
        for value, code in variants:
            with self.subTest(code=code):
                result = run_fault_scenario(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, finding_codes(result))

    def test_type_labelled_severity_values_are_closed(self) -> None:
        variants = []
        ratio = copy.deepcopy(self.brownout)
        ratio["faults"][0]["severity"]["value"] = 1.1
        variants.append((ratio, "FAULT_SEVERITY_VALUE_INVALID"))
        stuck = load_case("stuck-low.json")["input"]
        stuck["faults"][0]["severity"]["value"] = "HIGH"
        variants.append((stuck, "FAULT_SEVERITY_VALUE_INVALID"))
        loss = load_case("rail-loss.json")["input"]
        loss["faults"][0]["effect_rule"]["threshold"]["value"] = "AVAILABLE"
        variants.append((loss, "EFFECT_THRESHOLD_VALUE_INVALID"))
        restart = copy.deepcopy(self.restart)
        restart["faults"][0]["severity"]["value"] = "MAYBE"
        variants.append((restart, "FAULT_SEVERITY_VALUE_INVALID"))
        delay = load_case("delay.json")["input"]
        delay["faults"][0]["effect_rule"]["threshold"]["value"] = -0.1
        variants.append((delay, "EFFECT_THRESHOLD_VALUE_INVALID"))
        for value, code in variants:
            with self.subTest(code=code):
                result = run_fault_scenario(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, finding_codes(result))

    def test_trace_is_sorted_by_decimal_time_not_lexical_text(self) -> None:
        input_value = load_case("recovery-combo.json")["input"]
        first = fault_by_id(input_value, "FAULT-BROWNOUT-001")
        second = fault_by_id(input_value, "FAULT-RESTART-001")
        first["start_time"], first["duration"] = _known_time(10), _known_time(0.1)
        first["recovery"]["completion_time"] = _known_time(10.2)
        second["start_time"], second["duration"] = _known_time(2), _known_time(0.1)
        second["recovery"]["completion_time"] = _known_time(2.2)
        input_value["observation"]["end_time"] = _known_time(11)

        result = run_fault_scenario(input_value)

        injection_ids = [entry["fault_id"] for entry in result["trace"] if entry["phase"] == "INJECTION"]
        self.assertEqual(["FAULT-RESTART-001", "FAULT-BROWNOUT-001"], injection_ids)


if __name__ == "__main__":
    unittest.main()
