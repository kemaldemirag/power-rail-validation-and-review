from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from power_rail_validation.cli import (
    EXIT_CODES,
    EXIT_INTERNAL,
    EXIT_USAGE_OR_INPUT,
    main,
)
from power_rail_validation.contracts import sha256_hexdigest
from power_rail_validation.project_intake import descriptor_input_hash


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def fault_input() -> dict:
    descriptor = load_json(FIXTURES / "fault_injection" / "brownout.json")
    assert isinstance(descriptor, dict)
    value = copy.deepcopy(descriptor["input"])
    baseline = descriptor["baseline_fixtures"]
    rail = load_json((FIXTURES / "fault_injection" / baseline["rail_budget"]).resolve())
    sequence = load_json((FIXTURES / "fault_injection" / baseline["sequencing"]).resolve())
    assert isinstance(rail, dict) and isinstance(sequence, dict)
    value["rail_budget_input"] = rail["input"]
    value["sequencing_input"] = sequence["input"]
    return value


def invoke(argv: list[str], stdin_value: str = "") -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with (
        mock.patch("sys.stdin", StringIO(stdin_value)),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CliCommandTests(unittest.TestCase):
    def test_validate_contract_passes_and_emits_deterministic_json(self) -> None:
        exit_code, stdout, stderr = invoke(
            ["validate", str(FIXTURES / "contracts" / "valid.json")]
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("CONTRACT_VALID", result["reason_code"])
        self.assertRegex(result["input_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual("", stderr)

    def test_validate_contract_failure_is_a_blocked_engine_result(self) -> None:
        exit_code, stdout, stderr = invoke(
            ["validate", str(FIXTURES / "contracts" / "wrong-type.json")]
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["BLOCKED"], exit_code)
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["issues"])
        self.assertEqual("", stderr)

    def test_validate_file_api_defaults_artifact_root_to_input_parent(self) -> None:
        exit_code, stdout, stderr = invoke(
            [
                "validate",
                str(FIXTURES / "integration" / "valid.json"),
                "--kind",
                "file-api",
            ]
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", result["status"])
        self.assertEqual({"PASS"}, {item["status"] for item in result["artifact_results"]})
        self.assertEqual("", stderr)

    def test_rail_budget_reads_stdin_and_returns_pass(self) -> None:
        descriptor = load_json(FIXTURES / "rail_budget" / "nominal.json")
        assert isinstance(descriptor, dict)

        exit_code, stdout, stderr = invoke(
            ["rail-budget", "-"], json.dumps(descriptor["input"])
        )

        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", json.loads(stdout)["status"])
        self.assertEqual("", stderr)

    def test_intake_requires_explicit_root_and_returns_reportable_pass(self) -> None:
        fixture_root = FIXTURES / "project_intake"
        exit_code, stdout, stderr = invoke(
            [
                "intake",
                str(fixture_root / "ready-static-buck-pin.json"),
                "--artifact-root",
                str(fixture_root),
            ]
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("INTAKE_EXECUTION_READY", result["reason_code"])
        self.assertEqual({"NOT_RUN"}, {item["status"] for item in result["downstream_evaluations"]})
        self.assertEqual("", stderr)

    def test_intake_large_integer_order_conflict_is_blocked_without_internal_error(self) -> None:
        fixture_root = FIXTURES / "project_intake"
        descriptor = load_json(fixture_root / "ready-static-buck-pin.json")
        assert isinstance(descriptor, dict)
        descriptor["operating_envelope"]["vin_min_v"]["value"] = 10**400
        descriptor["operating_envelope"]["vin_nom_v"]["value"] = 10**400 - 1
        descriptor["operating_envelope"]["vin_max_v"]["value"] = 10**400
        descriptor["input_hash"] = descriptor_input_hash(descriptor)

        exit_code, stdout, stderr = invoke(
            ["intake", "-", "--artifact-root", str(fixture_root)],
            json.dumps(descriptor),
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["BLOCKED"], exit_code)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn(
            "OPERATING_ENVELOPE_ORDER_CONFLICT",
            {item["reason_code"] for item in result["issues"]},
        )
        self.assertEqual("", stderr)

    def test_sequence_reads_stdin_and_returns_pass(self) -> None:
        descriptor = load_json(FIXTURES / "sequencing" / "normal.json")
        assert isinstance(descriptor, dict)

        exit_code, stdout, stderr = invoke(
            ["sequence", "-"], json.dumps(descriptor["input"])
        )

        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", json.loads(stdout)["status"])
        self.assertEqual("", stderr)

    def test_inject_fault_preserves_fail_status_and_exit_code(self) -> None:
        exit_code, stdout, stderr = invoke(
            ["inject-fault", "-"], json.dumps(fault_input())
        )

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["FAIL"], exit_code)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual("", stderr)

    def test_review_passes_only_with_explicit_artifact_root(self) -> None:
        exit_code, stdout, stderr = invoke(
            [
                "review",
                str(FIXTURES / "regulator_review" / "nominal-synthetic.json"),
                "--artifact-root",
                str(REPO_ROOT),
            ]
        )

        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", json.loads(stdout)["status"])
        self.assertEqual("", stderr)

    def test_report_builds_the_common_model_without_an_implicit_timestamp(self) -> None:
        descriptor = load_json(FIXTURES / "rail_budget" / "nominal.json")
        assert isinstance(descriptor, dict)
        rail_exit, rail_stdout, rail_stderr = invoke(
            ["rail-budget", "-"], json.dumps(descriptor["input"])
        )
        self.assertEqual(0, rail_exit)
        self.assertEqual("", rail_stderr)

        exit_code, stdout, stderr = invoke(["report", "-"], rail_stdout)

        result = json.loads(stdout)
        self.assertEqual(EXIT_CODES["PASS"], exit_code)
        self.assertEqual("PASS", result["status"])
        self.assertNotIn("generated_at", result)
        self.assertEqual("", stderr)

    def test_validate_output_is_directly_reportable(self) -> None:
        validate_exit, validate_stdout, validate_stderr = invoke(
            ["validate", str(FIXTURES / "contracts" / "valid.json")]
        )
        self.assertEqual(EXIT_CODES["PASS"], validate_exit)
        self.assertEqual("", validate_stderr)

        report_exit, report_stdout, report_stderr = invoke(
            ["report", "-"], validate_stdout
        )

        result = json.loads(report_stdout)
        self.assertEqual(EXIT_CODES["PASS"], report_exit)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("", report_stderr)

    def test_same_input_has_byte_identical_stdout(self) -> None:
        descriptor = load_json(FIXTURES / "sequencing" / "normal.json")
        assert isinstance(descriptor, dict)
        payload = json.dumps(descriptor["input"])

        first = invoke(["sequence", "-"], payload)
        second = invoke(["sequence", "-"], payload)

        self.assertEqual(first, second)


class CliFailureTests(unittest.TestCase):
    def test_malformed_duplicate_and_nonfinite_json_are_input_errors(self) -> None:
        values = (
            "{",
            '{"value":1,"value":2}',
            '{"value":NaN}',
            '{"value":1e9999}',
            '{"value":' + ("1" * 5000) + "}",
            '{"value":"\\ud800"}',
            '{"\\udfff":"value"}',
        )
        for value in values:
            with self.subTest(value=value):
                exit_code, stdout, stderr = invoke(["rail-budget", "-"], value)
                self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
                self.assertEqual("", stdout)
                self.assertTrue(stderr.startswith("error: "))

    def test_missing_arguments_use_usage_exit_without_json(self) -> None:
        exit_code, stdout, stderr = invoke([])

        self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("error:", stderr)

    def test_artifact_root_is_rejected_for_contract_validation(self) -> None:
        exit_code, stdout, stderr = invoke(
            [
                "validate",
                str(FIXTURES / "contracts" / "valid.json"),
                "--artifact-root",
                str(REPO_ROOT),
            ]
        )

        self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("only valid", stderr)

    def test_intake_without_explicit_artifact_root_is_a_usage_error(self) -> None:
        exit_code, stdout, stderr = invoke(
            ["intake", str(FIXTURES / "project_intake" / "ready-static-buck-pin.json")]
        )

        self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("--artifact-root", stderr)

    def test_report_contract_rejection_is_an_input_error(self) -> None:
        exit_code, stdout, stderr = invoke(["report", "-"], "{}")

        self.assertEqual(EXIT_USAGE_OR_INPUT, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("report input rejected", stderr)

    def test_unexpected_engine_exception_is_sanitized_as_internal_error(self) -> None:
        with mock.patch(
            "power_rail_validation.cli.calculate_rail_budget",
            side_effect=RuntimeError("sensitive implementation detail"),
        ):
            exit_code, stdout, stderr = invoke(["rail-budget", "-"], "{}")

        self.assertEqual(EXIT_INTERNAL, exit_code)
        self.assertEqual("", stdout)
        self.assertEqual("internal error: RuntimeError\n", stderr)
        self.assertNotIn("sensitive", stderr)

    def test_all_statuses_use_the_published_exit_map(self) -> None:
        for status, expected in EXIT_CODES.items():
            with self.subTest(status=status), mock.patch(
                "power_rail_validation.cli.calculate_rail_budget",
                return_value={"status": status},
            ):
                exit_code, stdout, stderr = invoke(["rail-budget", "-"], "{}")
                self.assertEqual(expected, exit_code)
                self.assertEqual(status, json.loads(stdout)["status"])
                self.assertEqual("", stderr)


class ModuleEntrypointTests(unittest.TestCase):
    def test_python_m_entrypoint_uses_the_same_cli(self) -> None:
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        source_path = str(REPO_ROOT / "src")
        environment["PYTHONPATH"] = (
            source_path if not existing else source_path + os.pathsep + existing
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "power_rail_validation",
                "validate",
                str(FIXTURES / "contracts" / "valid.json"),
            ],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("PASS", json.loads(completed.stdout)["status"])
        self.assertEqual("", completed.stderr)

    def test_subprocess_stdio_is_utf8_even_under_legacy_locale_encoding(self) -> None:
        descriptor = load_json(FIXTURES / "rail_budget" / "nominal.json")
        assert isinstance(descriptor, dict)
        payload = copy.deepcopy(descriptor["input"])
        payload["sources"][0]["title"] = "Synthetic 😀 data"
        input_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        source_path = str(REPO_ROOT / "src")
        environment["PYTHONPATH"] = (
            source_path if not existing else source_path + os.pathsep + existing
        )
        environment["PYTHONIOENCODING"] = "cp1254"

        with tempfile.TemporaryDirectory(prefix=".tmp-cli-", dir=REPO_ROOT) as temporary:
            input_path = Path(temporary) / "unicode-input.json"
            input_path.write_bytes(input_bytes)
            commands = (
                (["rail-budget", "-"], input_bytes),
                (["rail-budget", str(input_path)], None),
            )
            for arguments, stdin_bytes in commands:
                with self.subTest(input=arguments[-1]):
                    completed = subprocess.run(
                        [sys.executable, "-m", "power_rail_validation", *arguments],
                        cwd=REPO_ROOT,
                        env=environment,
                        input=stdin_bytes,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual(b"", completed.stderr)
                    result = json.loads(completed.stdout.decode("utf-8", errors="strict"))
                    self.assertEqual(payload, result["calculation_inputs"])
                    self.assertEqual(sha256_hexdigest(payload), result["input_hash"])


if __name__ == "__main__":
    unittest.main()
