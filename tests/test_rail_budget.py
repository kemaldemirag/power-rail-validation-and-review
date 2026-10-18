from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

from power_rail_validation.contracts import sha256_hexdigest
from power_rail_validation.rail_budget import (
    FORMULA_VERSION,
    PACKAGE_VERSION,
    ROUNDING_MODE,
    ROUNDING_SIGNIFICANT_DIGITS,
    RULE_ID,
    TOOL_NAME,
    calculate_rail_budget,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "rail_budget"
NOMINAL_EVIDENCE = REPO_ROOT / "evidence" / "REP02-D2" / "nominal-result.json"


def load_case(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def issue_codes(result: dict) -> set[str]:
    return {item["code"] for item in result["issues"]}


class RailBudgetCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nominal_case = load_case("nominal.json")

    def test_hand_calculated_fixture_matches_all_core_formulas(self) -> None:
        input_value = self.nominal_case["input"]
        expected = self.nominal_case["expected"]

        result = calculate_rail_budget(input_value)

        self.assertEqual(expected["status"], result["status"])
        self.assertEqual(expected["reason_code"], result["reason_code"])
        self.assertEqual(FORMULA_VERSION, result["formula_version"])
        self.assertEqual(RULE_ID, result["rule_id"])
        self.assertEqual(TOOL_NAME, result["tool_name"])
        self.assertEqual(PACKAGE_VERSION, result["tool_version"])
        self.assertEqual(PACKAGE_VERSION, result["package_version"])
        self.assertEqual(ROUNDING_MODE, result["calculation_policy"]["rounding_mode"])
        self.assertEqual(
            ROUNDING_SIGNIFICANT_DIGITS,
            result["calculation_policy"]["significant_digits"],
        )

        for profile, value in expected["total_current_A"].items():
            self.assertEqual({"state": "KNOWN", "value": value, "unit": "A"}, result["metrics"]["total_current"][profile])
        for metric_name, expected_group in (
            ("output_power", expected["output_power_W"]),
            ("input_power", expected["input_power_W"]),
            ("loss_power", expected["loss_power_W"]),
        ):
            for profile, value in expected_group.items():
                self.assertEqual(
                    {"state": "KNOWN", "value": value, "unit": "W"},
                    result["metrics"][metric_name][profile],
                )
        self.assertEqual(expected["derated_capacity_A"], result["metrics"]["derated_capacity"]["value"])
        self.assertEqual(expected["effective_capacity_A"], result["metrics"]["effective_capacity"]["value"])
        self.assertEqual(expected["capacity_utilization_percent"], result["metrics"]["capacity_utilization"]["value"])
        self.assertEqual(expected["capacity_margin_A"], result["metrics"]["capacity_margin"]["value"])
        self.assertEqual(4.0, result["metrics"]["capacity_power"]["value"])
        self.assertEqual(1.0, result["metrics"]["power_margin"]["value"])

    def test_result_is_bound_to_exact_input_and_versioned_sources(self) -> None:
        input_value = self.nominal_case["input"]
        result = calculate_rail_budget(input_value)

        self.assertEqual(sha256_hexdigest(input_value), result["input_hash"])
        self.assertEqual(input_value, result["calculation_inputs"])
        self.assertEqual(
            [
                {"source_id": "SRC-DERATING-RULE", "version": "fixture-1"},
                {"source_id": "SRC-LOAD-DATASHEET", "version": "fixture-1"},
                {"source_id": "SRC-REGULATOR-DATASHEET", "version": "fixture-1"},
            ],
            result["source_refs"],
        )

    def test_nominal_evidence_is_exact_current_engine_output(self) -> None:
        with NOMINAL_EVIDENCE.open("r", encoding="utf-8") as handle:
            recorded = json.load(handle)

        self.assertEqual(
            calculate_rail_budget(self.nominal_case["input"]),
            recorded,
        )

    def test_same_input_is_deterministic_and_not_mutated(self) -> None:
        input_value = self.nominal_case["input"]
        before = copy.deepcopy(input_value)

        first = calculate_rail_budget(input_value)
        second = calculate_rail_budget(copy.deepcopy(input_value))

        self.assertEqual(first, second)
        self.assertEqual(before, input_value)
        json.dumps(first, allow_nan=False)

    def test_array_order_hash_sensitivity_is_explicit(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        reordered = copy.deepcopy(input_value)
        reordered["sources"].reverse()

        original_result = calculate_rail_budget(input_value)
        reordered_result = calculate_rail_budget(reordered)

        self.assertEqual("PASS", original_result["status"])
        self.assertEqual("PASS", reordered_result["status"])
        self.assertNotEqual(original_result["input_hash"], reordered_result["input_hash"])
        self.assertIn(
            "ARRAY_ORDER_IS_HASH_SIGNIFICANT_BY_D1_CANONICALIZATION",
            original_result["limitations"],
        )

    def test_overload_is_fail_with_negative_margin(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Imax"]["value"] = 0.7

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertEqual("OVER_DERATED_CAPACITY", result["reason_code"])
        self.assertEqual(112.5, result["metrics"]["capacity_utilization"]["value"])
        self.assertEqual(-0.1, result["metrics"]["capacity_margin"]["value"])
        self.assertIn("OVER_DERATED_CAPACITY", issue_codes(result))

    def test_exact_capacity_boundary_is_pass(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Imax"]["value"] = 0.6

        result = calculate_rail_budget(input_value)

        self.assertEqual("PASS", result["status"])
        self.assertEqual(100.0, result["metrics"]["capacity_utilization"]["value"])
        self.assertEqual(0.0, result["metrics"]["capacity_margin"]["value"])

    def test_out_of_tolerance_voltage_is_fail(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["rail"]["nominal_voltage"]["value"] = 5.5

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertEqual("VOLTAGE_OUT_OF_TOLERANCE", result["reason_code"])
        self.assertIn("VOLTAGE_OUT_OF_TOLERANCE", issue_codes(result))

    def test_unknown_limit_fixture_fails_closed_without_hiding_known_totals(self) -> None:
        case = load_case("unknown-limit.json")

        result = calculate_rail_budget(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        self.assertEqual(case["expected"]["reason_code"], result["reason_code"])
        self.assertEqual("KNOWN", result["metrics"]["total_current"]["Imax"]["state"])
        self.assertEqual("KNOWN", result["metrics"]["output_power"]["Pmax"]["state"])
        self.assertEqual("UNKNOWN", result["metrics"]["capacity_utilization"]["state"])
        self.assertEqual("UNKNOWN", result["metrics"]["capacity_margin"]["state"])

    def test_unknown_voltage_limit_cannot_pass(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["rail"]["voltage_limits"]["maximum"] = {
            "state": "UNKNOWN",
            "value": None,
            "unit": "V",
        }

        result = calculate_rail_budget(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual("VOLTAGE_LIMIT_UNKNOWN", result["reason_code"])
        self.assertEqual("KNOWN", result["metrics"]["output_power"]["Pmax"]["state"])

    def test_missing_imin_is_blocked_and_is_never_inferred(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        del input_value["loads"][0]["currents"]["Imin"]

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("REQUIRED_INPUT_MISSING", issue_codes(result))
        self.assertEqual("UNKNOWN", result["metrics"]["total_current"]["Imin"]["state"])

    def test_negative_current_requires_explicit_backfeed_permission(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Imin"]["value"] = -0.1

        rejected = calculate_rail_budget(input_value)
        self.assertEqual("FAIL", rejected["status"])
        self.assertIn("NEGATIVE_CURRENT_NOT_PERMITTED", issue_codes(rejected))
        self.assertIn("REVERSE_EFFICIENCY_MODEL_REQUIRED", issue_codes(rejected))
        self.assertEqual("UNKNOWN", rejected["metrics"]["input_power"]["Pmin"]["state"])
        self.assertEqual("UNKNOWN", rejected["metrics"]["loss_power"]["Pmin"]["state"])

        input_value["loads"][0]["backfeed_permitted"] = True
        accepted = calculate_rail_budget(input_value)
        self.assertEqual("UNKNOWN", accepted["status"])
        self.assertEqual("REVERSE_EFFICIENCY_MODEL_REQUIRED", accepted["reason_code"])
        self.assertEqual(-0.05, accepted["metrics"]["total_current"]["Imin"]["value"])
        self.assertEqual(-0.25, accepted["metrics"]["output_power"]["Pmin"]["value"])
        self.assertEqual("UNKNOWN", accepted["metrics"]["input_power"]["Pmin"]["state"])
        self.assertEqual("UNKNOWN", accepted["metrics"]["loss_power"]["Pmin"]["state"])
        self.assertIn("SIGNED_BACKFEED_STATIC_ALGEBRA_ONLY", accepted["limitations"])
        self.assertIn("REVERSE_EFFICIENCY_MODEL_NOT_DEFINED", accepted["limitations"])

    def test_zero_voltage_is_fail(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["rail"]["nominal_voltage"]["value"] = 0
        input_value["rail"]["voltage_limits"]["minimum"]["value"] = 0

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("NON_POSITIVE_VOLTAGE", issue_codes(result))

    def test_zero_capacity_is_fail_before_division(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["capacity"]["rated_current"]["value"] = 0

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("CAPACITY_RANGE_INVALID", issue_codes(result))
        self.assertEqual("UNKNOWN", result["metrics"]["capacity_utilization"]["state"])

    def test_unit_mismatch_is_fail_even_when_number_is_present(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Imax"]["unit"] = "mA"

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("UNIT_MISMATCH", issue_codes(result))

    def test_absent_efficiency_is_unknown_and_no_default_is_substituted(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        del input_value["efficiency"]

        result = calculate_rail_budget(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual("EFFICIENCY_UNKNOWN", result["reason_code"])
        self.assertEqual("KNOWN", result["metrics"]["output_power"]["Pmax"]["state"])
        self.assertEqual("UNKNOWN", result["metrics"]["input_power"]["Pmax"]["state"])
        self.assertEqual("UNKNOWN", result["metrics"]["loss_power"]["Pmax"]["state"])
        self.assertIn("EFFICIENCY_NOT_PROVIDED", result["limitations"])

    def test_explicit_efficiency_assumption_computes_but_cannot_pass(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["efficiency"]["provenance"] = "ASSUMPTION"

        result = calculate_rail_budget(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("PROVENANCE_UNCERTAIN", issue_codes(result))
        self.assertEqual(3.75, result["metrics"]["input_power"]["Pmax"]["value"])
        self.assertIn("EFFICIENCY_IS_ASSUMPTION", result["limitations"])

    def test_blocked_source_fixture_cannot_support_pass(self) -> None:
        case = load_case("blocked-source.json")

        result = calculate_rail_budget(case["input"])

        self.assertEqual(case["expected"]["status"], result["status"])
        for code in case["expected"]["reason_codes"]:
            self.assertIn(code, issue_codes(result))

    def test_conflicted_source_blocks_otherwise_numeric_result(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["sources"][0]["classification"] = "CONFLICT"

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_CONFLICT", issue_codes(result))

    def test_unresolved_required_source_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["source_refs"] = ["SRC-NOT-PRESENT"]

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_REF_UNRESOLVED", issue_codes(result))

    def test_missing_or_multiple_capacity_selection_is_blocked(self) -> None:
        missing = copy.deepcopy(self.nominal_case["input"])
        del missing["capacity"]
        self.assertEqual("BLOCKED", calculate_rail_budget(missing)["status"])

        multiple = copy.deepcopy(self.nominal_case["input"])
        multiple["capacity"] = [multiple["capacity"], copy.deepcopy(multiple["capacity"])]
        result = calculate_rail_budget(multiple)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("TYPE_OBJECT", issue_codes(result))

    def test_non_finite_value_fails_closed_instead_of_raising(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Imax"]["value"] = math.inf

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", issue_codes(result))
        self.assertIn("FINITE_NUMBER_REQUIRED", issue_codes(result))

    def test_deep_invalid_json_shape_fails_closed_instead_of_raising(self) -> None:
        medium_nested: object = 0
        for _ in range(500):
            medium_nested = [medium_nested]

        medium_result = calculate_rail_budget({"unexpected": medium_nested})

        self.assertEqual("BLOCKED", medium_result["status"])
        self.assertIsNone(medium_result["calculation_inputs"])
        self.assertIn("INPUT_COPY_UNREPRESENTABLE", issue_codes(medium_result))

        deeply_nested: object = 0
        for _ in range(1100):
            deeply_nested = [deeply_nested]

        result = calculate_rail_budget({"unexpected": deeply_nested})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["input_hash"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", issue_codes(result))

    def test_wrong_provenance_types_fail_closed_instead_of_raising(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["provenance"] = []
        input_value["sources"][0]["classification"] = {}

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROVENANCE_INVALID", issue_codes(result))
        self.assertIn("SOURCE_CLASSIFICATION_INVALID", issue_codes(result))

    def test_large_integer_overload_is_not_rounded_into_pass(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        demand = 10**28 + 1
        capacity = 10**28
        input_value["loads"] = [copy.deepcopy(input_value["loads"][0])]
        for profile in ("Imin", "Ityp", "Imax"):
            input_value["loads"][0]["currents"][profile]["value"] = demand
        input_value["capacity"]["rated_current"]["value"] = capacity
        input_value["capacity"]["current_limit"]["value"] = capacity
        input_value["derating"]["factor"]["value"] = 1
        input_value["efficiency"]["factor"]["value"] = 1

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("OVER_DERATED_CAPACITY", issue_codes(result))
        self.assertEqual(-1.0, result["metrics"]["capacity_margin"]["value"])

    def test_large_finite_derived_values_remain_json_serializable(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["rail"]["nominal_voltage"]["value"] = 1e308
        input_value["rail"]["voltage_limits"]["minimum"]["value"] = 1e308
        input_value["rail"]["voltage_limits"]["maximum"]["value"] = 1e308
        for load in input_value["loads"]:
            for profile in ("Imin", "Ityp", "Imax"):
                load["currents"][profile]["value"] = 1
        input_value["capacity"]["rated_current"]["value"] = 3
        input_value["capacity"]["current_limit"]["value"] = 3
        input_value["derating"]["factor"]["value"] = 1
        input_value["efficiency"]["factor"]["value"] = 1

        result = calculate_rail_budget(input_value)

        self.assertEqual("PASS", result["status"])
        json.dumps(result, allow_nan=False)
        self.assertIsInstance(result["metrics"]["output_power"]["Pmax"]["value"], int)

    def test_nonrepresentable_tiny_derived_value_blocks_result(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"] = [copy.deepcopy(input_value["loads"][0])]
        input_value["rail"]["nominal_voltage"]["value"] = 5e-324
        input_value["rail"]["voltage_limits"]["minimum"]["value"] = 5e-324
        input_value["rail"]["voltage_limits"]["maximum"]["value"] = 5e-324
        for profile in ("Imin", "Ityp", "Imax"):
            input_value["loads"][0]["currents"][profile]["value"] = 5e-324
        input_value["capacity"]["rated_current"]["value"] = 1e-323
        input_value["capacity"]["current_limit"]["value"] = 1e-323
        input_value["derating"]["factor"]["value"] = 1
        input_value["efficiency"]["factor"]["value"] = 1

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("NUMERIC_RESULT_UNREPRESENTABLE", result["reason_code"])
        json.dumps(result, allow_nan=False)

    def test_decimal_boundary_uses_decimal_sum_not_binary_float_sum(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        for profile in ("Imin", "Ityp", "Imax"):
            input_value["loads"][0]["currents"][profile]["value"] = 0.1
            input_value["loads"][1]["currents"][profile]["value"] = 0.2
        input_value["capacity"]["rated_current"]["value"] = 0.3
        input_value["capacity"]["current_limit"]["value"] = 0.3
        input_value["derating"]["factor"]["value"] = 1

        result = calculate_rail_budget(input_value)

        self.assertEqual("PASS", result["status"])
        self.assertEqual(0.3, result["metrics"]["total_current"]["Imax"]["value"])
        self.assertEqual(0.0, result["metrics"]["capacity_margin"]["value"])

    def test_mixed_exponent_sum_does_not_hide_overload(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        for profile in ("Imin", "Ityp", "Imax"):
            input_value["loads"][0]["currents"][profile]["value"] = 1
            input_value["loads"][1]["currents"][profile]["value"] = 1e-100
        input_value["capacity"]["rated_current"]["value"] = 1
        input_value["capacity"]["current_limit"]["value"] = 1
        input_value["derating"]["factor"]["value"] = 1
        input_value["efficiency"]["factor"]["value"] = 1

        result = calculate_rail_budget(input_value)

        self.assertEqual("FAIL", result["status"])
        self.assertIn("OVER_DERATED_CAPACITY", issue_codes(result))
        self.assertEqual(-1e-100, result["metrics"]["capacity_margin"]["value"])

    def test_unexpected_nested_current_profile_is_blocked(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["currents"]["Iburst"] = {
            "state": "KNOWN",
            "value": 99,
            "unit": "A",
        }

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("UNEXPECTED_FIELD", issue_codes(result))

    def test_d1_rail_and_source_identity_fields_are_enforced(self) -> None:
        invalid_rail = copy.deepcopy(self.nominal_case["input"])
        invalid_rail["rail"]["schema_version"] = None
        result = calculate_rail_budget(invalid_rail)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RAIL_SCHEMA_VERSION_INVALID", issue_codes(result))

        invalid_source = copy.deepcopy(self.nominal_case["input"])
        invalid_source["sources"][0]["entity_type"] = []
        result = calculate_rail_budget(invalid_source)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_ENTITY_TYPE_INVALID", issue_codes(result))

        invalid_ids = copy.deepcopy(self.nominal_case["input"])
        invalid_ids["rail"]["rail_id"] = "bad id"
        invalid_ids["loads"][0]["load_id"] = "bad load"
        for load in invalid_ids["loads"]:
            load["rail_id"] = "bad id"
        invalid_ids["capacity"]["regulator_id"] = "bad regulator"
        invalid_ids["capacity"]["rail_id"] = "bad id"
        result = calculate_rail_budget(invalid_ids)
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(
            {
                "RAIL_ID_INVALID",
                "LOAD_ID_INVALID",
                "LOAD_RAIL_ID_INVALID",
                "REGULATOR_ID_INVALID",
                "CAPACITY_RAIL_ID_INVALID",
            }.issubset(issue_codes(result))
        )

    def test_source_version_must_be_pinned_and_nonblank(self) -> None:
        for version in (
            "   ",
            "latest",
            "UNKNOWN",
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
                input_value = copy.deepcopy(self.nominal_case["input"])
                input_value["sources"][0]["version"] = version
                result = calculate_rail_budget(input_value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertTrue(
                    {"SOURCE_VERSION_MISSING", "SOURCE_VERSION_FLOATING"}
                    & issue_codes(result)
                )

    def test_unused_source_is_still_structurally_validated(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        extra_source = copy.deepcopy(input_value["sources"][0])
        extra_source.update(
            {
                "source_id": "SRC-UNUSED",
                "classification": [],
                "version": "latest",
                "access_status": "MISSING",
                "content_hash": {
                    "state": "UNKNOWN",
                    "algorithm": "SHA-256",
                    "value": None,
                },
            }
        )
        input_value["sources"].append(extra_source)

        result = calculate_rail_budget(input_value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_CLASSIFICATION_INVALID", issue_codes(result))
        self.assertIn("SOURCE_VERSION_FLOATING", issue_codes(result))

        for path, invalid_value, expected_code in (
            (("access_status",), [], "SOURCE_ACCESS_STATUS_INVALID"),
            (("content_hash", "state"), [], "SOURCE_HASH_STATE_INVALID"),
        ):
            with self.subTest(path=path):
                malformed = copy.deepcopy(self.nominal_case["input"])
                target = malformed["sources"][0]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = invalid_value
                malformed_result = calculate_rail_budget(malformed)
                self.assertEqual("BLOCKED", malformed_result["status"])
                self.assertIn(expected_code, issue_codes(malformed_result))

    def test_derived_provenance_without_lineage_cannot_support_pass(self) -> None:
        input_value = copy.deepcopy(self.nominal_case["input"])
        input_value["loads"][0]["provenance"] = "DERIVED"

        result = calculate_rail_budget(input_value)

        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("PROVENANCE_UNCERTAIN", issue_codes(result))


if __name__ == "__main__":
    unittest.main()
