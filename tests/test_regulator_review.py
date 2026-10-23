from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "regulator_review"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation.contracts.canonical import sha256_hexdigest  # noqa: E402
from power_rail_validation.regulator_review import (  # noqa: E402
    ENGINE_VERSION,
    RULE_SET_VERSION,
    review_regulator,
)
from power_rail_validation.reporting import build_report_model  # noqa: E402
from power_rail_validation.regulator_review import reviewer as reviewer_module  # noqa: E402


def load_nominal() -> dict:
    return json.loads((FIXTURE_ROOT / "nominal-synthetic.json").read_text(encoding="utf-8"))


def check(result: dict, rule_id: str) -> dict:
    return next(item for item in result["checks"] if item["rule_id"] == rule_id)


def set_path(document: dict, path: list, value: object) -> None:
    cursor = document
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


def load_descriptor(name: str) -> tuple[dict, dict]:
    descriptor = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    document = load_nominal()
    for patch in descriptor["patches"]:
        set_path(document, patch["path"], patch["value"])
    return document, descriptor


class NominalRegulatorReviewTests(unittest.TestCase):
    def test_nominal_synthetic_fixture_passes_every_rule_without_hardware_claim(self) -> None:
        result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("REGULATOR_REVIEW_COMPLETE", result["reason_code"])
        self.assertEqual(ENGINE_VERSION, result["engine_version"])
        self.assertEqual(RULE_SET_VERSION, result["rule_set_version"])
        self.assertEqual("REP02.REGULATOR.REVIEW:RR-SYNTHETIC-NOMINAL", result["component_id"])
        self.assertTrue(result["checks"])
        self.assertEqual({"PASS"}, {item["status"] for item in result["checks"]})
        self.assertIn("SYNTHETIC_FIXTURE_ONLY", result["limitations"])
        self.assertIn("SYNTHETIC_PASS_IS_NOT_PROJECT_OR_HARDWARE_PASS", result["claim_limits"])
        self.assertIn("TRANSIENT_EVIDENCE_IS_NOT_LOOP_STABILITY_EVIDENCE", result["limitations"])
        self.assertTrue(
            all(f"CLAIM_LIMIT:{value}" in result["limitations"] for value in result["claim_limits"])
        )

    def test_review_recomputes_public_rail_budget_and_binds_subject(self) -> None:
        result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        self.assertEqual("PASS", result["rail_budget_result"]["status"])
        self.assertEqual(0.6, result["rail_budget_result"]["metrics"]["total_current"]["Imax"]["value"])
        self.assertEqual("PASS", check(result, "REP02.REGULATOR.RAIL_BUDGET.BINDING")["status"])
        self.assertEqual(0.6, check(result, "REP02.REGULATOR.IOUT.RECOMMENDED_OPERATING")["observed"]["value"])

    def test_same_input_is_deterministic_and_not_mutated(self) -> None:
        document = load_nominal()
        original = copy.deepcopy(document)
        first = review_regulator(document, artifact_root=REPO_ROOT)
        second = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual(original, document)
        self.assertEqual(first, second)
        self.assertEqual(sha256_hexdigest(document), first["input_hash"])
        self.assertEqual(sha256_hexdigest(first), sha256_hexdigest(second))

    def test_result_is_detached_from_all_input_source_aliases(self) -> None:
        document = load_nominal()
        result = review_regulator(document, artifact_root=REPO_ROOT)
        result_source = next(item for item in result["sources"] if item["source_id"] == "SRC-RR-DATASHEET")
        expected_hash = result_source["content_hash"]["value"]
        document["sources"][0]["content_hash"]["value"] = "f" * 64
        document["sources"][0]["title"] = {"mutable": True}
        self.assertEqual(expected_hash, result_source["content_hash"]["value"])
        self.assertIsInstance(result_source["title"], str)
        json.dumps(result, allow_nan=False, sort_keys=True)

    def test_result_is_finite_json_and_reporter_compatible(self) -> None:
        result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        encoded = json.dumps(result, allow_nan=False, sort_keys=True)
        self.assertNotIn("NaN", encoded)
        model = build_report_model(result)
        self.assertEqual("PASS", model["status"])
        self.assertEqual("REP02.REGULATOR.REVIEW", model["findings"][0]["rule_id"])
        self.assertEqual(result["input_hash"], model["findings"][0]["input_hash"])

    def test_fixture_source_hashes_match_real_bytes(self) -> None:
        document = load_nominal()
        by_id = {item["source_id"]: item for item in document["sources"]}
        expected = {
            "SRC-RR-DATASHEET": "synthetic-exact-part-source.json",
            "SRC-RR-MODEL": "synthetic-model.lib",
            "SRC-RR-TRANSIENT": "synthetic-transient-result.json",
            "SRC-RR-STABILITY": "synthetic-stability-result.json",
            "SRC-RR-RAIL-FIXTURE": "synthetic-rail-budget-source.json",
        }
        for source_id, filename in expected.items():
            with self.subTest(source_id=source_id):
                digest = hashlib.sha256((FIXTURE_ROOT / filename).read_bytes()).hexdigest()
                self.assertEqual(digest, by_id[source_id]["content_hash"]["value"])

    def test_derating_uses_piecewise_linear_interpolation(self) -> None:
        result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual("PASS", item["status"])
        self.assertAlmostEqual(1.5333333333333334, item["limits"]["derated_maximum"]["value"])

    def test_pinned_rule_catalog_hash_and_completeness(self) -> None:
        path = SRC_ROOT / "power_rail_validation" / "regulator_review" / "review-rules.json"
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        ids = [item["rule_id"] for item in payload["rules"]]
        self.assertEqual(reviewer_module.RULE_SOURCE_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(reviewer_module._CATALOG_RULE_IDS), set(ids))
        self.assertEqual(list(reviewer_module._STATUS_ORDER), payload["status_precedence"])
        self.assertEqual(reviewer_module.RULE_SOURCE_SHA256, reviewer_module._rule_source()["content_hash"])


class LimitSeparationTests(unittest.TestCase):
    def test_output_voltage_must_be_known_and_exactly_bound_to_rail_nominal(self) -> None:
        unknown = load_nominal()
        unknown["operating_point"]["output_voltage"] = {"state": "UNKNOWN", "value": None, "unit": "V"}
        unknown_result = review_regulator(unknown, artifact_root=REPO_ROOT)
        self.assertEqual(
            ("BLOCKED", "OUTPUT_VOLTAGE_UNKNOWN"),
            tuple(check(unknown_result, "REP02.REGULATOR.OUTPUT_VOLTAGE.BINDING")[key] for key in ("status", "reason_code")),
        )

        mismatch = load_nominal()
        mismatch["operating_point"]["output_voltage"]["value"] = 5.000000000000001
        mismatch_result = review_regulator(mismatch, artifact_root=REPO_ROOT)
        self.assertEqual(
            ("BLOCKED", "OUTPUT_VOLTAGE_BINDING_MISMATCH"),
            tuple(check(mismatch_result, "REP02.REGULATOR.OUTPUT_VOLTAGE.BINDING")[key] for key in ("status", "reason_code")),
        )

    def test_absolute_maximum_pass_does_not_promote_recommended_range(self) -> None:
        document = load_nominal()
        document["operating_point"]["input_voltage"]["value"] = 40
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("PASS", check(result, "REP02.REGULATOR.VIN.ABSOLUTE_MAXIMUM")["status"])
        recommended = check(result, "REP02.REGULATOR.VIN.RECOMMENDED_OPERATING")
        self.assertEqual("FAIL", recommended["status"])
        self.assertEqual("RECOMMENDED_RANGE_EXCEEDED", recommended["reason_code"])

    def test_absolute_maximum_violation_fails(self) -> None:
        document = load_nominal()
        document["operating_point"]["input_voltage"]["value"] = 43
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.VIN.ABSOLUTE_MAXIMUM")
        self.assertEqual(("FAIL", "ABS_MAX_EXCEEDED"), (item["status"], item["reason_code"]))

    def test_recomputed_load_can_fail_recommended_current_while_absolute_passes(self) -> None:
        document = load_nominal()
        document["rail_budget_input"]["loads"][0]["currents"]["Imax"]["value"] = 2.1
        document["rail_budget_input"]["capacity"]["rated_current"]["value"] = 4
        document["rail_budget_input"]["capacity"]["current_limit"]["value"] = 3.5
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("PASS", check(result, "REP02.REGULATOR.IOUT.ABSOLUTE_MAXIMUM")["status"])
        self.assertEqual("FAIL", check(result, "REP02.REGULATOR.IOUT.RECOMMENDED_OPERATING")["status"])

    def test_missing_temperature_remains_unknown_and_is_not_zero(self) -> None:
        document = load_nominal()
        document["operating_point"]["ambient_temperature"] = {"state": "UNKNOWN", "value": None, "unit": "degC"}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.TEMPERATURE.RECOMMENDED_OPERATING")
        self.assertEqual(("UNKNOWN", "OPERATING_VALUE_UNKNOWN"), (item["status"], item["reason_code"]))
        self.assertEqual("UNKNOWN", check(result, "REP02.REGULATOR.IOUT.DERATING")["status"])

    def test_junction_temperature_is_not_inferred_from_ambient(self) -> None:
        document = load_nominal()
        document["operating_point"]["junction_temperature"] = {"state": "UNKNOWN", "value": None, "unit": "degC"}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.TEMPERATURE.ABSOLUTE_MAXIMUM")
        self.assertEqual("UNKNOWN", item["status"])
        self.assertIsNone(item["observed"]["value"])

    def test_derating_limit_violation_fails(self) -> None:
        document = load_nominal()
        document["operating_point"]["ambient_temperature"]["value"] = 85
        document["rail_budget_input"]["loads"][0]["currents"]["Imax"]["value"] = 1.1
        document["rail_budget_input"]["loads"][1]["currents"]["Imax"]["value"] = 0.2
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual(("FAIL", "DERATED_IOUT_EXCEEDED"), (item["status"], item["reason_code"]))

    def test_derating_curve_outside_domain_is_blocked(self) -> None:
        document = load_nominal()
        document["operating_point"]["ambient_temperature"]["value"] = 90
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual(("BLOCKED", "DERATING_CURVE_NOT_APPLICABLE"), (item["status"], item["reason_code"]))

    def test_limit_envelope_must_match_byte_bound_datasheet_values(self) -> None:
        document = load_nominal()
        document["operating_point"]["input_voltage"]["value"] = 100
        document["limits"]["absolute_maximum"]["input_voltage_max"]["value"] = 200
        document["limits"]["recommended_operating"]["input_voltage_max"]["value"] = 200
        document["model"]["validity_bounds"]["input_voltage_max"]["value"] = 200
        result = review_regulator(document, artifact_root=REPO_ROOT)
        for rule_id in (
            "REP02.REGULATOR.VIN.ABSOLUTE_MAXIMUM",
            "REP02.REGULATOR.VIN.RECOMMENDED_OPERATING",
        ):
            with self.subTest(rule_id=rule_id):
                item = check(result, rule_id)
                self.assertEqual(
                    ("BLOCKED", "DATASHEET_LIMIT_BINDING_MISMATCH"),
                    (item["status"], item["reason_code"]),
                )
        self.assertEqual("BLOCKED", result["status"])

    def test_derating_envelope_must_match_byte_bound_datasheet_points(self) -> None:
        document = load_nominal()
        document["limits"]["derating"]["points"][2]["maximum_output_current"]["value"] = 99
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual(
            ("BLOCKED", "DATASHEET_DERATING_BINDING_MISMATCH"),
            (item["status"], item["reason_code"]),
        )

    def test_unknown_derating_point_blocks_instead_of_being_silently_skipped(self) -> None:
        document = load_nominal()
        document["limits"]["derating"]["points"][1]["maximum_output_current"] = {
            "state": "UNKNOWN",
            "value": None,
            "unit": "A",
        }
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual(("BLOCKED", "DERATING_CURVE_INVALID"), (item["status"], item["reason_code"]))
        self.assertIn("DERATING_POINT_NOT_KNOWN", {issue["reason_code"] for issue in result["issues"]})

        invalid = load_nominal()
        invalid["limits"]["derating"]["points"][1]["ambient_temperature"]["unit"] = "K"
        invalid_result = review_regulator(invalid, artifact_root=REPO_ROOT)
        invalid_item = check(invalid_result, "REP02.REGULATOR.IOUT.DERATING")
        self.assertEqual(("BLOCKED", "DERATING_CURVE_INVALID"), (invalid_item["status"], invalid_item["reason_code"]))


class SourceAndModelGateTests(unittest.TestCase):
    def test_artifact_root_is_optional_at_api_but_omission_fails_closed(self) -> None:
        result = review_regulator(load_nominal())
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_ROOT_REQUIRED", {item["reason_code"] for item in result["issues"]})
        self.assertEqual({"REP02-RR-RULES"}, {item["source_id"] for item in result["sources"]})

    def test_available_source_requires_safe_relative_posix_locator_and_matching_bytes(self) -> None:
        unsafe = load_nominal()
        unsafe["sources"][0]["locator"] = "../synthetic-exact-part-source.json"
        unsafe_result = review_regulator(unsafe, artifact_root=REPO_ROOT)
        self.assertIn("SOURCE_LOCATOR_UNSAFE", {item["reason_code"] for item in unsafe_result["issues"]})
        self.assertNotIn("SRC-RR-DATASHEET", {item["source_id"] for item in unsafe_result["sources"]})

        mismatched = load_nominal()
        mismatched["sources"][0]["content_hash"]["value"] = "0" * 64
        mismatched_result = review_regulator(mismatched, artifact_root=REPO_ROOT)
        self.assertIn("SOURCE_BYTES_HASH_MISMATCH", {item["reason_code"] for item in mismatched_result["issues"]})
        self.assertNotIn("SRC-RR-DATASHEET", {item["source_id"] for item in mismatched_result["sources"]})

    def test_exported_source_records_are_schema_sanitized(self) -> None:
        document = load_nominal()
        document["sources"][0]["content_hash"]["unexpected"] = {"input_alias": True}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        exported = next(item for item in result["sources"] if item["source_id"] == "SRC-RR-DATASHEET")
        self.assertEqual({"state", "algorithm", "value"}, set(exported["content_hash"]))
        self.assertNotIn("unexpected", json.dumps(exported, sort_keys=True))

    def test_nested_rail_sources_are_exactly_bound_and_exported(self) -> None:
        result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.RAIL_BUDGET.SOURCE_BINDING")
        self.assertEqual(("PASS", "RAIL_SOURCE_BINDING_VERIFIED"), (item["status"], item["reason_code"]))
        outer_ids = {entry["source_id"] for entry in result["sources"]}
        self.assertTrue({"SRC-RR-DATASHEET", "SRC-RR-RAIL-FIXTURE"}.issubset(outer_ids))

        mismatch = load_nominal()
        mismatch["rail_budget_input"]["sources"][1]["version"] = "fixture-rail-2.0.0"
        mismatch_result = review_regulator(mismatch, artifact_root=REPO_ROOT)
        mismatch_item = check(mismatch_result, "REP02.REGULATOR.RAIL_BUDGET.SOURCE_BINDING")
        self.assertEqual(("BLOCKED", "RAIL_SOURCE_BINDING_MISMATCH"), (mismatch_item["status"], mismatch_item["reason_code"]))

    def test_rail_budget_values_and_entity_refs_are_bound_to_artifact_bytes(self) -> None:
        mutations = []

        rail_voltage = load_nominal()
        rail_voltage["operating_point"]["output_voltage"]["value"] = 12
        rail_voltage["rail_budget_input"]["rail"]["nominal_voltage"]["value"] = 12
        rail_voltage["rail_budget_input"]["rail"]["voltage_limits"]["minimum"]["value"] = 11.4
        rail_voltage["rail_budget_input"]["rail"]["voltage_limits"]["maximum"]["value"] = 12.6
        mutations.append(("rail_voltage", rail_voltage, "RAIL_ARTIFACT_BINDING_MISMATCH"))

        load_current = load_nominal()
        load_current["rail_budget_input"]["loads"][0]["currents"]["Imax"]["value"] = 0.3
        mutations.append(("load_current", load_current, "RAIL_ARTIFACT_BINDING_MISMATCH"))

        capacity = load_nominal()
        capacity["rail_budget_input"]["capacity"]["rated_current"]["value"] = 100
        capacity["rail_budget_input"]["capacity"]["current_limit"]["value"] = 100
        mutations.append(("capacity", capacity, "RAIL_ARTIFACT_BINDING_MISMATCH"))

        missing_ref = load_nominal()
        missing_ref["rail_budget_input"]["efficiency"]["source_refs"] = ["SRC-RR-DATASHEET"]
        mutations.append(("missing_ref", missing_ref, "RAIL_ARTIFACT_SOURCE_REFS_MISMATCH"))

        for name, document, expected_reason in mutations:
            with self.subTest(name=name):
                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, "REP02.REGULATOR.RAIL_BUDGET.SOURCE_BINDING")
                self.assertEqual(("BLOCKED", expected_reason), (item["status"], item["reason_code"]))
                self.assertEqual("BLOCKED", result["status"])

    def test_datasheet_page_section_is_bound_to_artifact_section_map(self) -> None:
        document = load_nominal()
        document["sources"][0]["page_section"] = "section 999"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.SOURCE.EXACT_PART")
        self.assertEqual(
            ("BLOCKED", "DATASHEET_SECTION_BINDING_MISMATCH"),
            (item["status"], item["reason_code"]),
        )

    def test_rail_budget_source_provenance_is_bound_to_exact_review_subject(self) -> None:
        mutations = (
            ("manufacturer", "OTHER-MFR", "SOURCE_PART_MISMATCH"),
            ("part_number", "OTHER-PART", "SOURCE_PART_MISMATCH"),
            ("publisher_type", "THIRD_PARTY", "RAIL_SOURCE_PROVENANCE_UNVERIFIED"),
            ("license_status", "RESTRICTED", "RAIL_SOURCE_PROVENANCE_UNVERIFIED"),
        )
        for field, value, expected_reason in mutations:
            with self.subTest(field=field):
                document = load_nominal()
                source = next(
                    item for item in document["sources"]
                    if item["source_id"] == "SRC-RR-RAIL-FIXTURE"
                )
                source[field] = value
                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, "REP02.REGULATOR.RAIL_BUDGET.SOURCE_BINDING")
                self.assertEqual(("BLOCKED", expected_reason), (item["status"], item["reason_code"]))
                self.assertEqual("BLOCKED", result["status"])

    def test_missing_exact_source_is_blocked_without_fake_hash_in_report_catalog(self) -> None:
        document = load_nominal()
        source = document["sources"][0]
        source["access_status"] = "MISSING"
        source["content_hash"] = {"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.SOURCE.EXACT_PART")
        self.assertEqual(("BLOCKED", "SOURCE_UNAVAILABLE"), (item["status"], item["reason_code"]))
        self.assertNotIn("SRC-RR-DATASHEET", {entry["source_id"] for entry in result["sources"]})
        self.assertIn("SRC-RR-DATASHEET", {entry["required_source_id"] for entry in result["evidence_gaps"]})
        build_report_model(result)

    def test_conflicting_source_is_blocked(self) -> None:
        document = load_nominal()
        document["sources"][0]["classification"] = "CONFLICT"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.SOURCE.EXACT_PART")
        self.assertEqual(("BLOCKED", "SOURCE_CONFLICT"), (item["status"], item["reason_code"]))

    def test_unpinned_source_version_is_blocked(self) -> None:
        document = load_nominal()
        document["sources"][0]["version"] = "latest"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MOVING_VERSION_FORBIDDEN", {item["reason_code"] for item in result["issues"]})

    def test_source_model_and_analysis_versions_reject_moving_aliases_and_path_segments(self) -> None:
        aliases = (
            "snapshot",
            "SNAPSHOT",
            "dev",
            "next",
            "canary",
            "edge",
            "rolling",
            "vNext",
            "release",
            "origin/main",
            "refs/tags/latest",
        )
        for alias in aliases:
            with self.subTest(kind="source_version", alias=alias):
                document = load_nominal()
                document["sources"][0]["version"] = alias
                result = review_regulator(document, artifact_root=REPO_ROOT)
                self.assertIn(
                    "MOVING_VERSION_FORBIDDEN",
                    {item["reason_code"] for item in result["issues"]},
                )

        mutations = (
            ("source_revision", lambda doc: doc["sources"][2].__setitem__("document_revision", "latest")),
            ("model_version", lambda doc: doc["model"].__setitem__("version", "dev")),
            (
                "analysis_tool_version",
                lambda doc: doc["analyses"]["transient"]["tool"].__setitem__("version", "next"),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(kind=name):
                document = load_nominal()
                mutate(document)
                result = review_regulator(document, artifact_root=REPO_ROOT)
                self.assertIn(
                    "MOVING_VERSION_FORBIDDEN",
                    {item["reason_code"] for item in result["issues"]},
                )

    def test_exact_source_part_mismatch_is_blocked(self) -> None:
        document = load_nominal()
        document["sources"][0]["part_number"] = "DIFFERENT-PART"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("SOURCE_PART_MISMATCH", check(result, "REP02.REGULATOR.SOURCE.EXACT_PART")["reason_code"])

    def test_model_missing_is_blocked(self) -> None:
        document = load_nominal()
        document["model"]["access_status"] = "MISSING"
        document["model"]["file_hash"] = {"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.MODEL.PROVENANCE")
        self.assertEqual(("BLOCKED", "MODEL_MISSING"), (item["status"], item["reason_code"]))

    def test_model_hash_mismatch_is_blocked(self) -> None:
        document = load_nominal()
        document["model"]["file_hash"]["value"] = "a" * 64
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("MODEL_HASH_MISMATCH", check(result, "REP02.REGULATOR.MODEL.PROVENANCE")["reason_code"])

    def test_model_version_must_match_source_version_and_document_revision(self) -> None:
        for source_field in ("version", "document_revision"):
            with self.subTest(source_field=source_field):
                document = load_nominal()
                model_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-MODEL")
                model_source[source_field] = "fixture-model-2.0.0"
                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, "REP02.REGULATOR.MODEL.PROVENANCE")
                self.assertEqual(("BLOCKED", "MODEL_VERSION_MISMATCH"), (item["status"], item["reason_code"]))

    def test_model_license_and_origin_are_gated(self) -> None:
        for field, value, reason in (
            ("license_status", "UNKNOWN", "MODEL_LICENSE_UNVERIFIED"),
            ("origin", "THIRD_PARTY", "MODEL_ORIGIN_UNVERIFIED"),
        ):
            with self.subTest(field=field):
                document = load_nominal()
                document["model"][field] = value
                result = review_regulator(document, artifact_root=REPO_ROOT)
                self.assertEqual(reason, check(result, "REP02.REGULATOR.MODEL.PROVENANCE")["reason_code"])

    def test_model_bounds_missing_blocks_applicability(self) -> None:
        document = load_nominal()
        document["model"]["validity_bounds"]["output_current_max"] = {"state": "UNKNOWN", "value": None, "unit": "A"}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.MODEL.APPLICABILITY")
        self.assertEqual(("BLOCKED", "MODEL_BOUNDS_MISSING"), (item["status"], item["reason_code"]))

    def test_model_out_of_bounds_fails_applicability_and_blocks_dependent_evidence(self) -> None:
        document = load_nominal()
        document["model"]["validity_bounds"]["input_voltage_max"]["value"] = 10
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("FAIL", check(result, "REP02.REGULATOR.MODEL.APPLICABILITY")["status"])
        self.assertEqual("BLOCKED", check(result, "REP02.REGULATOR.TRANSIENT.EVIDENCE")["status"])
        self.assertEqual("MODEL_NOT_APPLICABLE", check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")["reason_code"])

    def test_model_bounds_cannot_exceed_byte_bound_datasheet_envelope(self) -> None:
        document = load_nominal()
        document["model"]["validity_bounds"]["input_voltage_max"]["value"] = 200
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.MODEL.PROVENANCE")
        self.assertEqual(
            ("BLOCKED", "MODEL_BOUNDS_SOURCE_MISMATCH"),
            (item["status"], item["reason_code"]),
        )

    def test_model_identity_and_version_are_bound_to_model_file_header(self) -> None:
        document = load_nominal()
        model_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-MODEL")
        document["model"]["version"] = "fixture-model-9.0.0"
        model_source["version"] = "fixture-model-9.0.0"
        model_source["document_revision"] = "fixture-model-9.0.0"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.MODEL.PROVENANCE")
        self.assertEqual(
            ("BLOCKED", "MODEL_ARTIFACT_BINDING_MISMATCH"),
            (item["status"], item["reason_code"]),
        )

    def test_model_file_identity_and_subcircuit_structure_fail_closed(self) -> None:
        model_path = FIXTURE_ROOT / "synthetic-model.lib"
        original = model_path.read_text(encoding="utf-8")
        cases = (
            (
                "second_conflicting_header",
                original + "\n* part: OTHER-PART; version: fixture-model-1.0.0\n",
                "MODEL_ARTIFACT_AMBIGUOUS",
            ),
            (
                "no_subcircuit",
                "* SYNTHETIC\n* part: RR-TEST-5V; version: fixture-model-1.0.0\n",
                "MODEL_ARTIFACT_STRUCTURE_INVALID",
            ),
            (
                "wrong_subcircuit_identity",
                "* SYNTHETIC\n"
                "* part: RR-TEST-5V; version: fixture-model-1.0.0\n"
                ".SUBCKT OTHER_PART VIN VOUT GND\n"
                "R_FIXTURE VIN VOUT 1000000\n"
                ".ENDS OTHER_PART\n",
                "MODEL_ARTIFACT_BINDING_MISMATCH",
            ),
            (
                "mismatched_subcircuit_end",
                "* SYNTHETIC\n"
                "* part: RR-TEST-5V; version: fixture-model-1.0.0\n"
                ".SUBCKT RR_TEST_5V VIN VOUT GND\n"
                "R_FIXTURE VIN VOUT 1000000\n"
                ".ENDS OTHER_PART\n",
                "MODEL_ARTIFACT_STRUCTURE_INVALID",
            ),
            (
                "duplicate_exact_part_subcircuit",
                "* SYNTHETIC\n"
                "* part: RR-TEST-5V; version: fixture-model-1.0.0\n"
                ".SUBCKT RR_TEST_5V VIN VOUT GND\n"
                ".ENDS RR_TEST_5V\n"
                ".SUBCKT RR_TEST_5V VIN VOUT GND\n"
                ".ENDS RR_TEST_5V\n",
                "MODEL_ARTIFACT_AMBIGUOUS",
            ),
        )
        for name, content, expected_reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
                artifact_path = Path(temp_dir) / "model.lib"
                with artifact_path.open("w", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                document = load_nominal()
                model_source = next(
                    item for item in document["sources"] if item["source_id"] == "SRC-RR-MODEL"
                )
                model_source["locator"] = artifact_path.relative_to(REPO_ROOT).as_posix()
                model_source["content_hash"]["value"] = digest
                document["model"]["file_hash"]["value"] = digest

                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, "REP02.REGULATOR.MODEL.PROVENANCE")
                self.assertEqual(
                    ("BLOCKED", expected_reason),
                    (item["status"], item["reason_code"]),
                )
                self.assertEqual("BLOCKED", result["status"])


class TransientStabilitySeparationTests(unittest.TestCase):
    def test_project_scope_cannot_pass_without_verified_live_binding(self) -> None:
        document = load_nominal()
        document["fixture_scope"] = "PROJECT"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_SCOPE_LIVE_BINDING_UNAVAILABLE", result["reason_codes"])
        self.assertIn("PROJECT_SCOPE_LIVE_INTEGRATION_NOT_VERIFIED", result["limitations"])

    def test_transient_evidence_never_satisfies_stability_rule(self) -> None:
        document = load_nominal()
        document["analyses"]["loop_stability"]["evidence_kind"] = "TIME_DOMAIN_TRANSIENT"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("PASS", check(result, "REP02.REGULATOR.TRANSIENT.EVIDENCE")["status"])
        item = check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")
        self.assertEqual(("BLOCKED", "TRANSIENT_NOT_STABILITY_EVIDENCE"), (item["status"], item["reason_code"]))

    def test_transient_present_stability_missing_is_explicitly_blocked(self) -> None:
        document, descriptor = load_descriptor("transient-present-stability-missing.json")
        result = review_regulator(document, artifact_root=REPO_ROOT)
        expected = descriptor["expected"]
        self.assertEqual(expected["transient_status"], check(result, "REP02.REGULATOR.TRANSIENT.EVIDENCE")["status"])
        self.assertEqual(expected["stability_reason"], check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")["reason_code"])
        self.assertEqual(expected["overall_status"], result["status"])
        build_report_model(result)

    def test_workspace_gap_profile_blocks_without_inventing_hashes(self) -> None:
        document, descriptor = load_descriptor("workspace-gap-profile.json")
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual(descriptor["expected"]["overall_status"], result["status"])
        self.assertEqual("BLOCKED", check(result, "REP02.REGULATOR.SOURCE.EXACT_PART")["status"])
        self.assertEqual("MODEL_MISSING", check(result, "REP02.REGULATOR.MODEL.PROVENANCE")["reason_code"])
        self.assertNotIn("SRC-RR-DATASHEET", {item["source_id"] for item in result["sources"]})

    def test_not_run_is_not_reported_as_pass(self) -> None:
        document = load_nominal()
        analysis = document["analyses"]["transient"]
        analysis["execution_status"] = "NOT_RUN"
        analysis["result_hash"] = {"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}
        analysis["tool"] = {"state": "UNKNOWN", "name": None, "version": None}
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.TRANSIENT.EVIDENCE")
        self.assertEqual(("NOT_RUN", "ANALYSIS_NOT_RUN"), (item["status"], item["reason_code"]))

    def test_analysis_result_hash_must_match_evidence_source(self) -> None:
        document = load_nominal()
        document["analyses"]["transient"]["result_hash"]["value"] = "b" * 64
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("ANALYSIS_HASH_MISMATCH", check(result, "REP02.REGULATOR.TRANSIENT.EVIDENCE")["reason_code"])

    def test_analysis_kind_tool_and_status_are_bound_to_evidence_bytes(self) -> None:
        mutations = (
            ("evidence_kind", "PHASE_GAIN_MARGIN"),
            ("tool_name", "invented-tool"),
            ("execution_status", "FAIL"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                document = load_nominal()
                analysis = document["analyses"]["loop_stability"]
                if field == "tool_name":
                    analysis["tool"]["name"] = value
                else:
                    analysis[field] = value
                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")
                self.assertEqual(
                    ("BLOCKED", "ANALYSIS_EVIDENCE_BINDING_MISMATCH"),
                    (item["status"], item["reason_code"]),
                )

    def test_analysis_source_version_and_revision_are_bound_to_evidence_bytes(self) -> None:
        cases = (
            ("SRC-RR-TRANSIENT", "REP02.REGULATOR.TRANSIENT.EVIDENCE"),
            ("SRC-RR-STABILITY", "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE"),
        )
        for source_id, rule_id in cases:
            with self.subTest(source_id=source_id):
                document = load_nominal()
                source = next(item for item in document["sources"] if item["source_id"] == source_id)
                source["version"] = "arbitrary-pinned-9.9.9"
                source["document_revision"] = "arbitrary-pinned-9.9.9"
                result = review_regulator(document, artifact_root=REPO_ROOT)
                item = check(result, rule_id)
                self.assertEqual(
                    ("BLOCKED", "ANALYSIS_EVIDENCE_BINDING_MISMATCH"),
                    (item["status"], item["reason_code"]),
                )

    def test_stability_cannot_reuse_transient_locator(self) -> None:
        document = load_nominal()
        transient_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-TRANSIENT")
        stability_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-STABILITY")
        stability_source["locator"] = transient_source["locator"]
        stability_source["content_hash"] = copy.deepcopy(transient_source["content_hash"])
        document["analyses"]["loop_stability"]["result_hash"] = copy.deepcopy(transient_source["content_hash"])
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")
        self.assertEqual(("BLOCKED", "TRANSIENT_STABILITY_LOCATOR_REUSED"), (item["status"], item["reason_code"]))

    def test_stability_cannot_reuse_transient_exact_bytes_at_another_locator(self) -> None:
        document = load_nominal()
        transient_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-TRANSIENT")
        stability_source = next(item for item in document["sources"] if item["source_id"] == "SRC-RR-STABILITY")
        stability_source["locator"] = "tests/fixtures/regulator_review/synthetic-transient-result-copy.json"
        stability_source["content_hash"] = copy.deepcopy(transient_source["content_hash"])
        document["analyses"]["loop_stability"]["result_hash"] = copy.deepcopy(transient_source["content_hash"])
        result = review_regulator(document, artifact_root=REPO_ROOT)
        item = check(result, "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE")
        self.assertEqual(("BLOCKED", "TRANSIENT_STABILITY_BYTES_REUSED"), (item["status"], item["reason_code"]))


class InputValidationTests(unittest.TestCase):
    def test_negative_case_catalog_targets_expected_rule_and_reason(self) -> None:
        catalog = json.loads((FIXTURE_ROOT / "negative-cases.json").read_text(encoding="utf-8"))
        for case in catalog["cases"]:
            with self.subTest(case_id=case["case_id"]):
                document = load_nominal()
                set_path(document, case["path"], case["value"])
                item = check(review_regulator(document, artifact_root=REPO_ROOT), case["expected_rule"])
                self.assertEqual(case["expected_status"], item["status"])
                self.assertEqual(case["expected_reason"], item["reason_code"])

    def test_unexpected_field_blocks(self) -> None:
        document = load_nominal()
        document["silent_default"] = 1
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("UNEXPECTED_FIELD", result["reason_codes"])

    def test_wrong_unit_blocks(self) -> None:
        document = load_nominal()
        document["operating_point"]["input_voltage"]["unit"] = "mV"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertIn("UNIT_MISMATCH", {item["reason_code"] for item in result["issues"]})

    def test_duplicate_source_identifier_blocks(self) -> None:
        document = load_nominal()
        document["sources"].append(copy.deepcopy(document["sources"][0]))
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertIn("DUPLICATE_SOURCE_ID", {item["reason_code"] for item in result["issues"]})

    def test_rail_budget_binding_mismatch_blocks(self) -> None:
        document = load_nominal()
        document["rail_budget_input"]["capacity"]["regulator_id"] = "REG-DIFFERENT"
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", check(result, "REP02.REGULATOR.RAIL_BUDGET.BINDING")["status"])

    def test_non_finite_value_fails_closed_without_serialization_error(self) -> None:
        document = load_nominal()
        document["operating_point"]["input_voltage"]["value"] = math.nan
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertEqual("INPUT_HASH_UNCOMPUTABLE", result["reason_code"])
        json.dumps(result, allow_nan=False)

    def test_cyclic_input_fails_closed(self) -> None:
        document = load_nominal()
        document["cycle"] = document
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", result["reason_codes"])
        json.dumps(result, allow_nan=False, sort_keys=True)

    def test_shared_container_alias_fails_closed(self) -> None:
        document = load_nominal()
        shared_hash = document["sources"][0]["content_hash"]
        document["rail_budget_input"]["sources"][0]["content_hash"] = shared_hash
        result = review_regulator(document, artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertEqual("INPUT_GRAPH_ALIAS_FORBIDDEN", result["reason_code"])
        self.assertIn(
            "INPUT_GRAPH_ALIAS_FORBIDDEN",
            {item["reason_code"] for item in result["issues"]},
        )
        self.assertTrue(
            all(f"CLAIM_LIMIT:{value}" in result["limitations"] for value in result["claim_limits"])
        )
        json.dumps(result, allow_nan=False, sort_keys=True)

    def test_rule_catalog_hash_fallback_preserves_claim_limit_projection(self) -> None:
        with mock.patch.object(reviewer_module, "RULE_SOURCE_SHA256", "0" * 64):
            result = review_regulator(load_nominal(), artifact_root=REPO_ROOT)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("INPUT_VALIDATION_BLOCKED", result["reason_code"])
        self.assertTrue(
            all(f"CLAIM_LIMIT:{value}" in result["limitations"] for value in result["claim_limits"])
        )
        json.dumps(result, allow_nan=False, sort_keys=True)

    def test_required_field_and_result_order_is_cross_process_hash_seed_deterministic(self) -> None:
        script = "\n".join(
            (
                "import hashlib, json, pathlib, sys",
                "from power_rail_validation.regulator_review import review_regulator",
                "root = pathlib.Path(sys.argv[1]).resolve()",
                "doc = json.loads((root / 'tests/fixtures/regulator_review/nominal-synthetic.json').read_text(encoding='utf-8'))",
                "[doc['regulator'].pop(name) for name in ('regulator_id', 'manufacturer', 'part_number')]",
                "result = review_regulator(doc, artifact_root=root)",
                "raw = json.dumps(result, allow_nan=False, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')",
                "print(hashlib.sha256(raw).hexdigest())",
            )
        )
        observed = []
        for seed in ("1", "77", "4294967295"):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONPATH"] = str(SRC_ROOT)
            completed = subprocess.run(
                [sys.executable, "-c", script, str(REPO_ROOT)],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            observed.append(completed.stdout.strip())
        self.assertEqual(1, len(set(observed)))

    def test_all_fixture_json_files_parse(self) -> None:
        for path in FIXTURE_ROOT.glob("*.json"):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
