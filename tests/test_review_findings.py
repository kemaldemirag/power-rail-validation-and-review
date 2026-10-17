"""Executable reproductions of the D1 review findings.

Each ``expectedFailure`` test asserts the behaviour the D1 product contract
requires and currently fails against the validator.  When a finding is fixed,
unittest reports the test as an "unexpected success"; remove the decorator then
so the test becomes an ordinary regression guard.

Tests without the decorator characterise current behaviour that the review
flagged as a documented risk rather than a defect.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation.contracts import (  # noqa: E402
    configuration_sha256,
    load_json,
    validate_document,
)


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


class ReviewFindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.value = load_json(FIXTURES / "valid.json")
        self.collections = self.value["request"]["collections"]
        self.finding = self.value["response"]["findings"][0]

    def codes(self) -> set[str]:
        digest = configuration_sha256(self.value["request"])
        self.value["request"]["configuration_sha256"] = digest
        self.value["response"]["input_configuration_sha256"] = digest
        for finding in self.value["response"]["findings"]:
            finding["input_hash"] = digest
        return {issue.code for issue in validate_document(self.value)}

    def test_baseline_fixture_is_valid(self) -> None:
        self.assertEqual(set(), self.codes())

    # R-01: product contract rule 8 — ASSUMPTION/UNKNOWN cannot support PASS.
    @unittest.expectedFailure
    def test_r01_pass_from_assumption_rail_limits_is_rejected(self) -> None:
        self.collections["rails"][1]["provenance"] = "ASSUMPTION"
        self.assertNotEqual(set(), self.codes())

    @unittest.expectedFailure
    def test_r01_pass_from_unknown_rail_limits_is_rejected(self) -> None:
        self.collections["rails"][1]["provenance"] = "UNKNOWN"
        self.assertNotEqual(set(), self.codes())

    # R-02: a declared KNOWN limit must resolve even when not required.
    @unittest.expectedFailure
    def test_r02_unresolvable_optional_known_limit_is_rejected(self) -> None:
        self.finding["limit_context"] = {
            "required": False,
            "state": "KNOWN",
            "refs": ["rail:DOES_NOT_EXIST.voltage_limits.minimum"],
        }
        self.assertNotEqual(set(), self.codes())

    # R-03: limit refs must belong to the finding subject and cover both sides.
    @unittest.expectedFailure
    def test_r03_limit_of_another_rail_is_rejected(self) -> None:
        self.finding["limit_context"]["refs"] = [
            "rail:VIN_12V.voltage_limits.minimum",
            "rail:VIN_12V.voltage_limits.maximum",
        ]
        self.assertNotEqual(set(), self.codes())

    @unittest.expectedFailure
    def test_r03_single_sided_limit_is_rejected_for_voltage_rule(self) -> None:
        self.finding["limit_context"]["refs"] = ["rail:VDD_3V3.voltage_limits.minimum"]
        self.assertNotEqual(set(), self.codes())

    # R-04: schema $defs/id pattern is not enforced by the Python validator.
    @unittest.expectedFailure
    def test_r04_identifier_outside_schema_pattern_is_rejected(self) -> None:
        source = self.collections["sources"][0]
        self.assertNotEqual(set(), {issue.code for issue in validate_document({**source, "source_id": " bad id!"})})

    # R-05: schema idRefs declare uniqueItems.
    @unittest.expectedFailure
    def test_r05_duplicate_source_refs_are_rejected(self) -> None:
        self.collections["rails"][1]["source_refs"] = ["SRC-DATASHEET-001", "SRC-DATASHEET-001"]
        self.assertNotEqual(set(), self.codes())

    # R-06: dotted identifiers are schema-valid but break limit resolution.
    @unittest.expectedFailure
    def test_r06_dotted_rail_identifier_resolves_its_limits(self) -> None:
        self.collections["rails"][1]["rail_id"] = "VDD.3V3"
        self.collections["loads"][0]["rail_id"] = "VDD.3V3"
        self.collections["regulators"][0]["output_rail_id"] = "VDD.3V3"
        self.collections["events"][0]["subject_refs"] = ["rail:VDD.3V3"]
        self.finding["subject_ref"]["entity_id"] = "VDD.3V3"
        self.finding["limit_context"]["refs"] = [
            "rail:VDD.3V3.voltage_limits.minimum",
            "rail:VDD.3V3.voltage_limits.maximum",
        ]
        self.assertEqual(set(), self.codes())

    # R-07: timestamps must be RFC 3339, independent of the Python version.
    def test_r07_iso8601_only_forms_depend_on_python_version(self) -> None:
        for timestamp in ("20261017T120000+0300", "2026-W42-6T12:00+03:00"):
            with self.subTest(timestamp=timestamp):
                value = copy.deepcopy(self.value)
                value["response"]["generated_at"] = timestamp
                issues = validate_document(value)
                if sys.version_info >= (3, 11):
                    # fromisoformat accepts these non-RFC 3339 ISO 8601 forms from 3.11.
                    self.assertEqual([], issues, "finding R-07 appears fixed")
                else:
                    self.assertNotEqual([], issues)

    @unittest.expectedFailure
    def test_r07_lowercase_z_is_accepted(self) -> None:
        self.value["response"]["generated_at"] = "2026-10-17T09:01:00z"
        self.assertEqual(set(), self.codes())

    @unittest.expectedFailure
    def test_r07_rfc3339_space_separator_is_rejected(self) -> None:
        self.value["response"]["generated_at"] = "2026-10-17 12:01:00+03:00"
        self.assertNotEqual(set(), self.codes())

    def test_r07_fractional_seconds_depend_on_python_version(self) -> None:
        self.value["response"]["generated_at"] = "2026-10-17T09:01:00.5Z"
        codes = self.codes()
        if sys.version_info >= (3, 11):
            self.assertEqual(set(), codes)
        else:
            self.assertIn("DATETIME_FORMAT", codes, "finding R-07 appears fixed")

    # R-08: empty subject_refs reports a source-reference code.
    @unittest.expectedFailure
    def test_r08_empty_subject_refs_uses_subject_code(self) -> None:
        self.collections["events"][0]["subject_refs"] = []
        self.assertNotIn("EMPTY_SOURCE_REFS", self.codes())

    # R-09: inputs_used is untyped and resolves against every collection.
    @unittest.expectedFailure
    def test_r09_source_id_is_not_an_evaluation_input(self) -> None:
        self.value["response"]["checks"][0]["inputs_used"] = ["SRC-DATASHEET-001"]
        self.assertNotEqual(set(), self.codes())

    # R-10: cross-field consistency is not checked.
    @unittest.expectedFailure
    def test_r10_summary_fail_with_only_passing_evaluations_is_rejected(self) -> None:
        self.value["response"]["summary_status"] = "FAIL"
        self.assertNotEqual(set(), self.codes())

    @unittest.expectedFailure
    def test_r10_regulator_feeding_its_own_input_is_rejected(self) -> None:
        self.collections["regulators"][0]["input_ref"] = "VDD_3V3"
        self.assertNotEqual(set(), self.codes())

    @unittest.expectedFailure
    def test_r10_negative_load_current_is_rejected(self) -> None:
        self.collections["loads"][0]["peak_current"]["value"] = -9
        self.assertNotEqual(set(), self.codes())

    # R-11 (documented in D1-DEC-004): hash depends on number spelling.
    def test_r11_integer_and_float_spellings_hash_differently(self) -> None:
        request = self.value["request"]
        float_digest = configuration_sha256(request)
        request["collections"]["rails"][0]["nominal_voltage"]["value"] = 12
        self.assertNotEqual(float_digest, configuration_sha256(request))

    # R-12: message says "only", but one unsafe source among safe ones trips it.
    def test_r12_one_unsafe_source_blocks_pass_despite_safe_source(self) -> None:
        unsafe = copy.deepcopy(self.collections["sources"][0])
        unsafe["source_id"] = "SRC-ASSUMED-001"
        unsafe["classification"] = "ASSUMPTION"
        self.collections["sources"].append(unsafe)
        self.finding["source_refs"].append({"source_id": "SRC-ASSUMED-001", "version": "rev-C"})
        self.assertIn("PASS_FROM_UNSAFE_SOURCE", self.codes())


if __name__ == "__main__":
    unittest.main()
