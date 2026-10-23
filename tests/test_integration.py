from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation.integration import (  # noqa: E402
    API_VERSION,
    DESCRIPTOR_SCHEMA_VERSION,
    SPEC_SHA256,
    SPEC_SOURCE_ID,
    descriptor_input_hash,
    validate_file_api_descriptor,
    validate_file_api_file,
)
from power_rail_validation.integration import file_api  # noqa: E402
from power_rail_validation.reporting import build_report_model  # noqa: E402


FIXTURES = REPO_ROOT / "tests" / "fixtures" / "integration"
SPEC_PATH = SRC_ROOT / "power_rail_validation" / "integration" / "file_api_contract.json"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def rebind(value: dict) -> dict:
    value["input_hash"] = descriptor_input_hash(value)
    return value


def codes(result: dict) -> set[str]:
    return {item["reason_code"] for item in result["issues"]}


class FileApiFixtureTests(unittest.TestCase):
    def test_valid_rep01_descriptor_and_exact_artifact_bytes_pass(self) -> None:
        result = validate_file_api_file(FIXTURES / "valid.json")
        self.assertEqual("PASS", result["status"])
        self.assertEqual("FILE_API_DESCRIPTOR_VALID", result["reason_code"])
        self.assertEqual(2, len(result["artifact_results"]))
        self.assertEqual({"PASS"}, {item["status"] for item in result["artifact_results"]})
        self.assertEqual([], result["artifact_gaps"])

    def test_valid_rep03_descriptor_uses_same_pinned_contract(self) -> None:
        result = validate_file_api_file(FIXTURES / "rep03-valid.json")
        self.assertEqual("PASS", result["status"])
        self.assertEqual("REP-03", result["descriptor_identity"]["producer"])
        self.assertEqual("REP-02", result["descriptor_identity"]["consumer"])

    def test_multiple_configuration_profile_pairs_are_preserved(self) -> None:
        result = validate_file_api_file(FIXTURES / "multiple-profiles.json")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(3, result["descriptor_identity"]["configuration_profile_count"])
        self.assertEqual(3, len(result["artifact_results"]))

    def test_declared_missing_source_and_artifact_are_not_run_without_fake_hashes(self) -> None:
        result = validate_file_api_file(FIXTURES / "missing-artifact.json")
        self.assertEqual("NOT_RUN", result["status"])
        self.assertEqual(
            {"EXTERNAL_ARTIFACT_NOT_PROVIDED", "EXTERNAL_SOURCE_NOT_PROVIDED"},
            codes(result),
        )
        self.assertEqual(2, len(result["artifact_gaps"]))
        self.assertTrue(all("sha256" not in gap for gap in result["artifact_gaps"]))
        self.assertEqual({"MISSING"}, {gap["access_status"] for gap in result["artifact_gaps"]})

    def test_omitting_artifact_root_is_explicit_not_run(self) -> None:
        value = load_fixture("valid.json")
        result = validate_file_api_descriptor(value)
        self.assertEqual("NOT_RUN", result["status"])
        self.assertEqual({"ARTIFACT_BYTES_NOT_VERIFIED"}, codes(result))
        self.assertTrue(all("sha256" not in gap for gap in result["artifact_gaps"]))

    def test_available_artifact_missing_from_disk_is_blocked_and_gap_has_no_hash(self) -> None:
        value = load_fixture("valid.json")
        value["configurations"][0]["artifacts"][0]["file_name"] = "artifacts/not-present.json"
        rebind(value)
        result = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_FILE_MISSING", codes(result))
        gap = next(item for item in result["artifact_gaps"] if item["artifact_id"] == "REP01-CONFIG-A")
        self.assertNotIn("sha256", gap)

    def test_artifact_hash_mismatch_blocks_integrity_claim(self) -> None:
        value = load_fixture("valid.json")
        value["configurations"][0]["artifacts"][0]["sha256"] = "0" * 64
        rebind(value)
        result = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_HASH_MISMATCH", codes(result))


class FileApiCompatibilityTests(unittest.TestCase):
    def evaluate(self, value: dict) -> dict:
        rebind(value)
        return validate_file_api_descriptor(value, artifact_root=FIXTURES)

    def test_api_and_schema_versions_are_pinned_to_contract(self) -> None:
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        self.assertEqual(API_VERSION, spec["api_version"])
        self.assertEqual(DESCRIPTOR_SCHEMA_VERSION, spec["schema_version"])
        self.assertEqual("EXACT", spec["compatibility_policy"]["api_version_match"])
        self.assertEqual("EXACT", spec["compatibility_policy"]["schema_version_match"])
        self.assertTrue(spec["live_adapter_policy"]["pass_forbidden"])
        self.assertEqual({"NOT_RUN", "BLOCKED"}, set(spec["live_adapter_policy"]["allowed_statuses"]))

    def test_incompatible_api_version_is_blocked(self) -> None:
        value = load_fixture("valid.json")
        value["api_version"] = "2.0.0"
        result = self.evaluate(value)
        self.assertIn("API_VERSION_INCOMPATIBLE", codes(result))
        self.assertEqual("BLOCKED", result["status"])

    def test_incompatible_schema_version_is_blocked(self) -> None:
        value = load_fixture("valid.json")
        value["schema_version"] = "2.0.0"
        result = self.evaluate(value)
        self.assertIn("SCHEMA_VERSION_INCOMPATIBLE", codes(result))

    def test_endpoint_api_version_must_also_be_compatible(self) -> None:
        value = load_fixture("valid.json")
        value["producer"]["api_version"] = "2.0.0"
        result = self.evaluate(value)
        self.assertIn("ENDPOINT_API_VERSION_INCOMPATIBLE", codes(result))

    def test_moving_version_alias_is_blocked(self) -> None:
        value = load_fixture("valid.json")
        value["producer"]["schema_version"] = "latest"
        result = self.evaluate(value)
        self.assertIn("VERSION_NOT_PINNED", codes(result))

    def test_compatibility_error_behavior_cannot_be_softened(self) -> None:
        value = load_fixture("valid.json")
        value["compatibility"]["hash_mismatch_status"] = "PASS"
        result = self.evaluate(value)
        self.assertIn("COMPATIBILITY_POLICY_MISMATCH", codes(result))

    def test_claim_boundary_cannot_be_expanded_or_reduced(self) -> None:
        value = load_fixture("valid.json")
        value["claim_limits"].append("LIVE_INTEGRATION_VALIDATED")
        result = self.evaluate(value)
        self.assertIn("CLAIM_LIMITS_MISMATCH", codes(result))

    def test_live_adapters_never_report_pass(self) -> None:
        results = [
            validate_file_api_file(FIXTURES / "valid.json"),
            validate_file_api_file(FIXTURES / "missing-artifact.json"),
        ]
        incompatible = load_fixture("valid.json")
        incompatible["api_version"] = "2.0.0"
        results.append(self.evaluate(incompatible))
        for result in results:
            with self.subTest(status=result["status"]):
                self.assertTrue(
                    all(item["status"] in {"NOT_RUN", "BLOCKED"} for item in result["adapter_results"])
                )
                self.assertNotIn("PASS", {item["status"] for item in result["adapter_results"]})


class FileApiIdentityAndPathTests(unittest.TestCase):
    def evaluate(self, value: dict) -> dict:
        rebind(value)
        return validate_file_api_descriptor(value, artifact_root=FIXTURES)

    def test_duplicate_artifact_identifier_is_blocked(self) -> None:
        value = load_fixture("valid.json")
        duplicate = copy.deepcopy(value["configurations"][0]["artifacts"][0])
        duplicate["file_name"] = "artifacts/rep01-configuration-b.json"
        duplicate["sha256"] = hashlib.sha256((FIXTURES / duplicate["file_name"]).read_bytes()).hexdigest()
        value["configurations"][0]["artifacts"].append(duplicate)
        result = self.evaluate(value)
        self.assertIn("DUPLICATE_ARTIFACT_ID", codes(result))

    def test_duplicate_artifact_path_is_blocked_case_insensitively(self) -> None:
        value = load_fixture("valid.json")
        duplicate = copy.deepcopy(value["configurations"][0]["artifacts"][0])
        duplicate["artifact_id"] = "REP01-CONFIG-A-COPY"
        duplicate["file_name"] = duplicate["file_name"].upper()
        value["configurations"][0]["artifacts"].append(duplicate)
        result = self.evaluate(value)
        self.assertIn("DUPLICATE_ARTIFACT_PATH", codes(result))

    def test_duplicate_configuration_profile_identity_is_blocked(self) -> None:
        value = load_fixture("multiple-profiles.json")
        value["configurations"][1]["profile_id"] = "PROFILE-NOMINAL"
        result = self.evaluate(value)
        self.assertIn("DUPLICATE_CONFIGURATION_PROFILE", codes(result))

    def test_path_traversal_and_windows_paths_are_blocked(self) -> None:
        for file_name in ("../outside.json", "artifacts\\outside.json", "C:/outside.json", "/outside.json"):
            value = load_fixture("valid.json")
            value["configurations"][0]["artifacts"][0]["file_name"] = file_name
            result = self.evaluate(value)
            with self.subTest(file_name=file_name):
                self.assertIn("PATH_TRAVERSAL_BLOCKED", codes(result))
                self.assertEqual("BLOCKED", result["status"])


class FileApiRobustnessTests(unittest.TestCase):
    def test_malformed_json_is_blocked(self) -> None:
        result = validate_file_api_file(FIXTURES / "malformed.json")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual({"MALFORMED_JSON"}, codes(result))
        self.assertRegex(result["input_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual("CANONICAL_LOAD_ERROR_ENVELOPE", result["input_binding"]["kind"])
        self.assertEqual(
            hashlib.sha256((FIXTURES / "malformed.json").read_bytes()).hexdigest(),
            result["input_binding"]["raw_file_sha256"],
        )

    def test_missing_descriptor_file_has_explicit_load_error_binding(self) -> None:
        result = validate_file_api_file(FIXTURES / "not-present-descriptor.json")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual({"DESCRIPTOR_FILE_UNAVAILABLE"}, codes(result))
        self.assertRegex(result["input_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual("MISSING", result["input_binding"]["raw_file_access_status"])
        self.assertIsNone(result["input_binding"]["raw_file_sha256"])

    def test_duplicate_json_keys_are_rejected_before_validation(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            path.write_text('{"api_version":"1.0.0","api_version":"1.0.0"}', encoding="utf-8")
            result = validate_file_api_file(path)
        self.assertEqual({"DUPLICATE_JSON_KEY"}, codes(result))

    def test_nonfinite_json_literals_and_overflow_are_blocked(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity", "1e9999"):
            with self.subTest(token=token), tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
                path = Path(temp_dir) / "nonfinite.json"
                path.write_text('{"value":' + token + "}", encoding="utf-8")
                result = validate_file_api_file(path)
                self.assertIn("NON_FINITE_JSON_NUMBER", codes(result))

    def test_nonfinite_in_memory_value_is_json_safe_blocked_result(self) -> None:
        value = load_fixture("valid.json")
        value["future"] = math.nan
        result = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", codes(result))
        self.assertIsNone(result["input_hash"])
        json.dumps(result, allow_nan=False)

    def test_cyclic_input_is_blocked_without_recursion_escape(self) -> None:
        value = load_fixture("valid.json")
        value["cycle"] = value
        result = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("INPUT_HASH_UNCOMPUTABLE", codes(result))
        json.dumps(result, allow_nan=False)

    def test_validation_does_not_mutate_input(self) -> None:
        value = load_fixture("valid.json")
        original = copy.deepcopy(value)
        validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertEqual(original, value)

    def test_result_and_input_hash_are_deterministic(self) -> None:
        value = load_fixture("valid.json")
        reordered = {key: value[key] for key in reversed(tuple(value))}
        self.assertEqual(descriptor_input_hash(value), descriptor_input_hash(reordered))
        first = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        second = validate_file_api_descriptor(copy.deepcopy(value), artifact_root=FIXTURES)
        self.assertEqual(first, second)

    def test_descriptor_hash_mismatch_is_blocked_but_actual_hash_is_reported(self) -> None:
        value = load_fixture("valid.json")
        value["descriptor_id"] = "SYNTH-REP01-HANDOFF-TAMPERED"
        result = validate_file_api_descriptor(value, artifact_root=FIXTURES)
        self.assertIn("DESCRIPTOR_HASH_MISMATCH", codes(result))
        self.assertEqual(descriptor_input_hash(value), result["input_hash"])

    def test_package_contract_is_the_only_source_and_uses_exact_file_hash(self) -> None:
        result = validate_file_api_file(FIXTURES / "valid.json")
        self.assertEqual([SPEC_SOURCE_ID], [item["source_id"] for item in result["sources"]])
        self.assertEqual(hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest(), result["sources"][0]["content_hash"])
        self.assertEqual(SPEC_SHA256, result["sources"][0]["content_hash"])
        self.assertEqual(result["sources"], result["source_refs"])

    def test_missing_package_contract_source_blocks_without_synthesized_hash(self) -> None:
        with mock.patch.object(file_api, "_SPEC_PATH", FIXTURES / "not-present-contract.json"):
            result = validate_file_api_file(FIXTURES / "valid.json")
        self.assertIn("CONTRACT_SPEC_UNAVAILABLE", codes(result))
        self.assertEqual([], result["sources"])
        self.assertEqual([], result["source_refs"])

    def test_tampered_or_truncated_contract_source_is_rejected_by_pinned_byte_hash(self) -> None:
        truncated = {"contract_id": SPEC_SOURCE_ID, "api_version": API_VERSION}
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "file_api_contract.json"
            path.write_text(json.dumps(truncated), encoding="utf-8")
            with mock.patch.object(file_api, "_SPEC_PATH", path):
                result = validate_file_api_file(FIXTURES / "valid.json")
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("CONTRACT_SPEC_UNAVAILABLE", codes(result))
        self.assertEqual([], result["sources"])

    def test_valid_result_is_accepted_by_common_reporter(self) -> None:
        result = validate_file_api_file(FIXTURES / "valid.json")
        model = build_report_model(result)
        self.assertEqual("PASS", model["status"])
        self.assertEqual("REP02.INTEGRATION.FILE_API", model["component_results"][0]["rule_id"])
        self.assertEqual(SPEC_SOURCE_ID, model["findings"][0]["source_refs"][0]["source_id"])
        for claim_limit in result["claim_limits"]:
            self.assertIn("CLAIM_LIMIT:" + claim_limit, model["limitations"])

    def test_gap_and_integrity_block_results_are_also_reporter_compatible(self) -> None:
        gap_result = validate_file_api_file(FIXTURES / "missing-artifact.json")
        blocked_value = load_fixture("valid.json")
        blocked_value["configurations"][0]["artifacts"][0]["sha256"] = "0" * 64
        rebind(blocked_value)
        blocked_result = validate_file_api_descriptor(blocked_value, artifact_root=FIXTURES)
        self.assertEqual("NOT_RUN", build_report_model(gap_result)["status"])
        self.assertEqual("BLOCKED", build_report_model(blocked_result)["status"])

    def test_all_file_load_errors_are_reporter_compatible(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            base = Path(temp_dir)
            paths = {
                "duplicate": base / "duplicate.json",
                "nonfinite": base / "nonfinite.json",
                "encoding": base / "encoding.json",
                "missing": base / "missing.json",
            }
            paths["duplicate"].write_text('{"a":1,"a":2}', encoding="utf-8")
            paths["nonfinite"].write_text('{"value":NaN}', encoding="utf-8")
            paths["encoding"].write_bytes(b"\xff\xfe")
            results = [validate_file_api_file(path) for path in paths.values()]
        self.assertEqual(4, len(results))
        for result in results:
            with self.subTest(code=result["reason_code"]):
                self.assertRegex(result["input_hash"], r"^[0-9a-f]{64}$")
                self.assertEqual("BLOCKED", build_report_model(result)["status"])


if __name__ == "__main__":
    unittest.main()
