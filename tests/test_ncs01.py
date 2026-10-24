from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from power_rail_validation.cli import EXIT_USAGE_OR_INPUT
from power_rail_validation.ncs01 import Ncs01InputError, evaluate_ncs01, main
from power_rail_validation.reporting import build_report_model


REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = REPO_ROOT / "examples" / "nightstand" / "ncs01-nightstand-v1.json"


def load_case() -> dict:
    with CASE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def local_fixture(root: Path) -> dict:
    payload = b"NCS-01 synthetic source bytes\n"
    source = root / "evidence.docx"
    source.write_bytes(payload)
    case = load_case()
    case["sources"][0]["locator"] = "evidence.docx"
    case["sources"][0]["content_hash"]["value"] = hashlib.sha256(payload).hexdigest()
    return case


def issue_codes(result: dict) -> set[str]:
    return {item["reason_code"] for item in result["issues"]}


class Ncs01EvaluationTests(unittest.TestCase):
    def test_case_binds_handoff_source_without_promoting_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = evaluate_ncs01(local_fixture(root), root)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("PRIMARY_LABEL_ARTIFACT_MISSING", result["reason_code"])
        self.assertEqual(45, result["facts"]["label_total_power"]["value"])
        self.assertEqual("HANDOFF_ONLY", result["facts"]["label_total_power"]["provenance"])
        self.assertEqual("PASS", result["sources"][0]["verification_status"])
        self.assertEqual(
            result["sources"][0]["content_hash"]["value"],
            result["sources"][0]["observed_sha256"],
        )
        self.assertEqual(
            "FORBIDDEN_WITHOUT_SOURCE_BOUND_VOLTAGE_AND_CURRENT_PROFILE",
            result["calculation_policy"]["label_power_conversion"],
        )

    def test_scenario_boundaries_preserve_fail_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = evaluate_ncs01(local_fixture(root), root)

        scenarios = {item["scenario_id"]: item for item in result["scenario_results"]}
        self.assertEqual("UNKNOWN", scenarios["S-CAPACITY-UNKNOWN"]["status"])
        self.assertEqual("FAIL", scenarios["S-CAPACITY-44W"]["status"])
        self.assertEqual(-1, scenarios["S-CAPACITY-44W"]["gross_margin"]["value"])
        self.assertEqual("UNKNOWN", scenarios["S-CAPACITY-45W"]["status"])
        self.assertEqual(0, scenarios["S-CAPACITY-45W"]["gross_margin"]["value"])
        self.assertEqual("UNKNOWN", scenarios["S-CAPACITY-60W"]["status"])
        self.assertEqual(15, scenarios["S-CAPACITY-60W"]["gross_margin"]["value"])
        self.assertTrue(all(not item["hardware_pass"] for item in scenarios.values()))

    def test_recommendation_is_separate_from_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = evaluate_ncs01(local_fixture(root), root)

        recommendation = result["recommendation"]
        self.assertEqual("HYBRID", recommendation["preferred_provisional"])
        self.assertEqual("ENGINEERING_RULE", recommendation["basis"])
        self.assertEqual("READY_MODULE", recommendation["fallback"])
        self.assertEqual("CUSTOM_PCB", recommendation["future_option"])
        self.assertEqual("BLOCKED", recommendation["verification_status"])
        self.assertEqual("NOT_RUN", recommendation["test_status"])
        self.assertTrue(all(not item["eligible"] for item in result["architecture_assessment"]))

    def test_missing_source_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = load_case()
            case["sources"][0]["locator"] = "missing.docx"
            result = evaluate_ncs01(case, root)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SOURCE_UNAVAILABLE", issue_codes(result))
        self.assertEqual("BLOCKED", result["sources"][0]["verification_status"])

    def test_source_hash_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["sources"][0]["content_hash"]["value"] = "0" * 64
            result = evaluate_ncs01(case, root)

        self.assertIn("SOURCE_HASH_MISMATCH", issue_codes(result))
        self.assertEqual("BLOCKED", result["sources"][0]["verification_status"])

    def test_case_defining_label_power_cannot_drift_from_45_watts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for watts in (44, 46, 100):
                case = local_fixture(root)
                case["facts"]["label_total_power"]["value"] = watts
                with self.subTest(watts=watts), self.assertRaisesRegex(
                    Ncs01InputError, "case-defining 45 W label"
                ):
                    evaluate_ncs01(case, root)

    def test_case_claim_limits_are_closed_and_cannot_be_weakened(self) -> None:
        canonical = load_case()["claim_limits"]
        variants = [
            ["HARDWARE_PASS"],
            canonical[:-1],
            canonical + ["EXTRA_LIMIT"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for claim_limits in variants:
                case = local_fixture(root)
                case["claim_limits"] = claim_limits
                with self.subTest(claim_limits=claim_limits), self.assertRaisesRegex(
                    Ncs01InputError, "exact NCS-01 safety claim-limit set and order"
                ):
                    evaluate_ncs01(case, root)

    def test_result_is_deterministic_and_input_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            original = copy.deepcopy(case)
            first = evaluate_ncs01(case, root)
            second = evaluate_ncs01(case, root)

        self.assertEqual(original, case)
        self.assertEqual(first, second)
        self.assertEqual(first["input_hash"], second["input_hash"])

    def test_reporter_accepts_preferred_status_without_aggregating_portfolio_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = evaluate_ncs01(local_fixture(root), root)

        report = build_report_model(result)
        present = {status for status, count in report["status_counts"].items() if count}
        self.assertEqual("BLOCKED", report["status"])
        self.assertTrue({"BLOCKED", "UNKNOWN", "NOT_RUN"}.issubset(present))
        self.assertNotIn("FAIL", present)
        self.assertIn("FAIL", {item["status"] for item in result["scenario_results"]})
        self.assertNotIn("CAPACITY_BELOW_LABEL_LOAD", issue_codes(result))

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["sources"][0]["locator"] = "../outside.docx"
            with self.assertRaisesRegex(Ncs01InputError, "safe relative POSIX path"):
                evaluate_ncs01(case, root)

    def test_known_gap_cannot_hide_a_numeric_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            scenario = case["scenarios"][0]
            scenario["capacity"] = {
                "state": "KNOWN",
                "value": 100,
                "unit": "W",
                "provenance": "ASSUMPTION",
            }
            with self.assertRaisesRegex(Ncs01InputError, "known-gap scenario must remain UNKNOWN"):
                evaluate_ncs01(case, root)

    def test_known_assumptions_cannot_promote_product_checks_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["facts"]["measured_continuous_power"].update(
                {"state": "KNOWN", "value": 45, "provenance": "ASSUMPTION"}
            )
            case["facts"]["pd_port_count"].update(
                {"state": "KNOWN", "value": 2, "provenance": "ASSUMPTION"}
            )
            case["facts"]["pd_profiles"].update(
                {"state": "KNOWN", "value": ["PROFILE-PORT1-A"], "provenance": "ASSUMPTION"}
            )
            case["facts"]["simultaneous_power_policy"].update(
                {"state": "KNOWN", "value": "POLICY-A", "provenance": "ASSUMPTION"}
            )
            result = evaluate_ncs01(case, root)

        checks = {item["check_id"]: item for item in result["verification_status_vector"]}
        self.assertEqual("UNKNOWN", checks["LOAD_DEFINITION"]["status"])
        self.assertEqual("UNKNOWN", checks["PORT_PROFILE"]["status"])
        self.assertEqual("UNKNOWN", checks["SIMULTANEOUS_POWER_POLICY"]["status"])
        self.assertEqual(
            {"PROVENANCE_NOT_SUFFICIENT_FOR_PASS"},
            {
                checks["LOAD_DEFINITION"]["reason_code"],
                checks["PORT_PROFILE"]["reason_code"],
                checks["SIMULTANEOUS_POWER_POLICY"]["reason_code"],
            },
        )

    def test_known_profile_and_policy_shapes_are_representable_when_source_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["sources"][0]["classification"] = "SOURCE_EXPLICIT"
            case["sources"][0]["supports"] = [
                "LABEL_TOTAL_POWER_45W_PRIMARY_TEST",
                "MEASURED_LOAD_TEST",
                "PD_PORT_COUNT_TEST",
                "PD_PROFILE_REFS_TEST",
                "SIMULTANEOUS_POLICY_REF_TEST",
            ]
            case["facts"]["label_total_power"].update(
                {
                    "provenance": "SOURCE_EXPLICIT",
                    "basis": "LABEL_TOTAL_POWER_45W_PRIMARY_TEST",
                }
            )
            case["facts"]["measured_continuous_power"].update(
                {
                    "state": "KNOWN",
                    "value": 45,
                    "provenance": "SOURCE_EXPLICIT",
                    "basis": "MEASURED_LOAD_TEST",
                }
            )
            case["facts"]["pd_port_count"].update(
                {
                    "state": "KNOWN",
                    "value": 2,
                    "provenance": "SOURCE_EXPLICIT",
                    "basis": "PD_PORT_COUNT_TEST",
                }
            )
            case["facts"]["pd_profiles"].update(
                {
                    "state": "KNOWN",
                    "value": ["PROFILE-PORT1-A", "PROFILE-PORT2-A"],
                    "provenance": "SOURCE_EXPLICIT",
                    "basis": "PD_PROFILE_REFS_TEST",
                }
            )
            case["facts"]["simultaneous_power_policy"].update(
                {
                    "state": "KNOWN",
                    "value": "POLICY-A",
                    "provenance": "SOURCE_EXPLICIT",
                    "basis": "SIMULTANEOUS_POLICY_REF_TEST",
                }
            )
            result = evaluate_ncs01(case, root)

        checks = {item["check_id"]: item for item in result["verification_status_vector"]}
        self.assertEqual("UNKNOWN", checks["SOURCE_BINDING"]["status"])
        self.assertEqual(
            "PRIMARY_SOURCE_BYTES_BOUND_CONTENT_NOT_PARSED",
            checks["SOURCE_BINDING"]["reason_code"],
        )
        self.assertEqual("PASS", checks["LOAD_DEFINITION"]["status"])
        self.assertEqual("UNKNOWN", checks["PORT_PROFILE"]["status"])
        self.assertEqual(
            "PD_PROFILE_REFERENCES_BOUND_VALUES_NOT_MODELED",
            checks["PORT_PROFILE"]["reason_code"],
        )
        self.assertEqual("UNKNOWN", checks["SIMULTANEOUS_POWER_POLICY"]["status"])
        self.assertEqual(
            "SIMULTANEOUS_POLICY_REFERENCE_BOUND_VALUES_NOT_MODELED",
            checks["SIMULTANEOUS_POWER_POLICY"]["reason_code"],
        )
        self.assertEqual("NOT_RUN", checks["MODEL_REPRODUCIBILITY"]["status"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn(
            "MEASUREMENT_INTEGRITY_INSTRUMENT_AND_CALIBRATION_NOT_BOUND",
            issue_codes(result),
        )
        self.assertIn(
            "PRIMARY_LABEL_SOURCE_BYTES_BOUND_CONTENT_NOT_EXTRACTED",
            result["limitations"],
        )
        self.assertNotIn("PRIMARY_LABEL_ARTIFACT_NOT_LOCAL", result["limitations"])
        self.assertNotIn(
            "45_W_IS_HANDOFF_ONLY_LABEL_SUM_NOT_A_MEASUREMENT",
            result["limitations"],
        )
        self.assertTrue(result["next_action"]["description"].startswith("Validate the bound"))

    def test_architecture_cannot_omit_mandatory_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["architectures"][1]["required_evidence"] = ["MODEL_REPRODUCIBILITY"]
            with self.assertRaisesRegex(Ncs01InputError, "mandatory set mismatch"):
                evaluate_ncs01(case, root)

    def test_preferred_fallback_and_future_roles_must_be_pairwise_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["architectures"][0]["future_option"] = True
            case["architectures"][2]["future_option"] = False
            with self.assertRaisesRegex(Ncs01InputError, "must be distinct"):
                evaluate_ncs01(case, root)

    def test_source_claim_basis_must_be_declared_in_supports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["sources"][0]["supports"] = ["UNRELATED_CLAIM"]
            with self.assertRaisesRegex(Ncs01InputError, "declare the supported claim"):
                evaluate_ncs01(case, root)

    def test_source_version_must_match_reporter_pinned_version_contract(self) -> None:
        invalid_versions = (
            " latest ",
            "main",
            "1.x",
            ">=1.0",
            "branch/dev",
            "releases/latest",
            "refs/tags/latest",
            "release/stable",
            "latest,release",
            "stable#build",
            "refs(tags)latest",
            "1.2 - 1.4",
            "1.2 – 1.4",
            "1.2–1.4",
            "1.2—1.4",
            "1.2 to 1.4",
            "v1..v2",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in invalid_versions:
                with self.subTest(version=version):
                    case = local_fixture(root)
                    case["sources"][0]["version"] = version
                    with self.assertRaisesRegex(
                        Ncs01InputError,
                        "version must not contain|version must be pinned",
                    ):
                        evaluate_ncs01(case, root)

    def test_known_pd_profile_set_must_be_identifier_array(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = local_fixture(root)
            case["facts"]["pd_profiles"].update(
                {"state": "KNOWN", "value": 3, "provenance": "ASSUMPTION"}
            )
            with self.assertRaisesRegex(Ncs01InputError, "expected an array"):
                evaluate_ncs01(case, root)

    def test_cli_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_file = root / "duplicate.json"
            case_file.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}', encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([str(case_file), "--source-root", str(root)])

        self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("duplicate JSON object key", stderr.getvalue())

    def test_cli_case_returns_blocked_exit_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_path = root / "case.json"
            case_path.write_text(
                json.dumps(local_fixture(root), ensure_ascii=False),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([str(case_path), "--source-root", str(root)])

        result = json.loads(stdout.getvalue())
        self.assertEqual(3, exit_code)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("PRIMARY_LABEL_ARTIFACT_MISSING", result["reason_code"])
        self.assertEqual("", stderr.getvalue())

    def test_subprocess_stdout_is_utf8_under_legacy_locale_encoding(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        environment["PYTHONIOENCODING"] = "cp1254"
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "power_rail_validation.ncs01",
                    str(CASE_PATH),
                    "--source-root",
                    directory,
                ],
                cwd=REPO_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(3, completed.returncode, completed.stderr)
        self.assertEqual(b"", completed.stderr)
        result = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        self.assertIn("Kayıt", result["sources"][0]["locator"])


if __name__ == "__main__":
    unittest.main()
