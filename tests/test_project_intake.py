from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional
import unittest
from unittest import mock

import power_rail_validation.project_intake.validator as intake_validator
from power_rail_validation.project_intake import (
    descriptor_input_hash,
    validate_project_intake,
)
from power_rail_validation.reporting import build_report_model


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "project_intake"
READY_PATH = FIXTURE_ROOT / "ready-static-buck-pin.json"


def ready() -> dict:
    value = json.loads(READY_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def rehash(value: dict) -> dict:
    value["input_hash"] = descriptor_input_hash(value)
    return value


def codes(result: dict) -> set[str]:
    return {item["reason_code"] for item in result["issues"]}


def artifact(value: dict, role: str) -> dict:
    return next(item for item in value["artifacts"] if item["role"] == role)


def project_descriptor(value: dict, revision: str) -> dict:
    value["scope"] = "PROJECT"
    value["rep01"]["git_revision"] = {"state": "KNOWN", "value": revision}
    for item in value["artifacts"]:
        role = item["role"]
        if role == "REGULATOR_DATASHEET":
            item["origin"] = "MANUFACTURER"
        elif role == "REGULATOR_MODEL":
            item["origin"] = "AUTHORIZED_MODEL_PROVIDER"
        else:
            item["origin"] = "REP01_PROJECT"
        if "rep01_git_revision" in item["bindings"]:
            item["bindings"]["rep01_git_revision"] = revision
    return rehash(value)


class ProjectIntakeReadyTests(unittest.TestCase):
    def test_ready_fixture_is_deterministic_pass_without_mutation(self) -> None:
        value = ready()
        original = copy.deepcopy(value)
        first = validate_project_intake(value, artifact_root=FIXTURE_ROOT)
        second = validate_project_intake(value, artifact_root=FIXTURE_ROOT)

        self.assertEqual(original, value)
        self.assertEqual(first, second)
        self.assertEqual("PASS", first["status"])
        self.assertEqual("INTAKE_EXECUTION_READY", first["reason_code"])
        self.assertEqual([], first["issues"])
        self.assertEqual(value["input_hash"], first["input_hash"])
        self.assertEqual({"PASS"}, {item["status"] for item in first["artifact_results"]})

    def test_pass_is_readiness_only_and_downstream_stays_not_run(self) -> None:
        result = validate_project_intake(ready(), artifact_root=FIXTURE_ROOT)

        self.assertEqual({"NOT_RUN"}, {item["status"] for item in result["downstream_evaluations"]})
        self.assertIn("NO_ELECTRICAL_COMPLIANCE_CLAIM", result["limitations"])
        self.assertIn("NO_HARDWARE_VALIDATION_CLAIM", result["limitations"])
        self.assertIn("NO_G1_G6_CLOSURE_CLAIM", result["limitations"])
        self.assertIn("NO_ARTIFACT_SEMANTIC_EXTRACTION", result["limitations"])
        self.assertIn("DECLARED_ORIGIN_NOT_AUTHENTICATED", result["limitations"])
        self.assertIn("SYNTHETIC_TEST_ONLY", result["limitations"])
        self.assertEqual("NOT_RUN", result["project_provenance"]["status"])

    def test_runtime_claim_limits_match_packaged_contract(self) -> None:
        contract_path = (
            REPO_ROOT
            / "src"
            / "power_rail_validation"
            / "project_intake"
            / "project_intake_contract.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        result = validate_project_intake(ready(), artifact_root=FIXTURE_ROOT)

        self.assertEqual(
            set(contract["claim_boundary"]),
            set(result["limitations"]) - {"SYNTHETIC_TEST_ONLY"},
        )

    def test_result_is_directly_reporter_compatible(self) -> None:
        result = validate_project_intake(ready(), artifact_root=FIXTURE_ROOT)
        report = build_report_model(result)

        self.assertEqual("PASS", report["status"])
        self.assertEqual("REP02.PROJECT_INTAKE.READINESS", report["component_results"][0]["rule_id"])

    def test_model_is_not_required_for_static_buck_pin(self) -> None:
        result = validate_project_intake(ready(), artifact_root=FIXTURE_ROOT)

        self.assertNotIn("REGULATOR_MODEL", result["required_artifact_roles"])
        self.assertEqual("PASS", result["status"])

    def test_supporting_artifact_is_verified_but_not_a_readiness_role(self) -> None:
        result = validate_project_intake(ready(), artifact_root=FIXTURE_ROOT)
        supporting = next(
            item for item in result["artifact_results"] if item["role"] == "REP01_OPEN_DECISIONS"
        )

        self.assertEqual("PASS", supporting["status"])
        self.assertFalse(supporting["readiness_role"])
        self.assertNotIn("REP01_OPEN_DECISIONS", result["required_artifact_roles"])


class ProjectIntakeFailClosedTests(unittest.TestCase):
    def run_value(self, value: dict, root: Optional[Path] = FIXTURE_ROOT) -> dict:
        return validate_project_intake(value, artifact_root=root)

    def test_artifact_root_is_explicit_and_required(self) -> None:
        result = self.run_value(ready(), None)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_ROOT_REQUIRED", codes(result))

    def test_descriptor_self_hash_mismatch_blocks(self) -> None:
        value = ready()
        value["descriptor_id"] = "REP02-D5-TAMPERED"
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("INPUT_HASH_MISMATCH", codes(result))

    def test_transient_request_requires_a_model(self) -> None:
        value = ready()
        value["requested_capabilities"].append("TRANSIENT")
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("REGULATOR_MODEL", result["required_artifact_roles"])
        self.assertIn("REQUIRED_ARTIFACT_ROLE_MISSING", codes(result))

    def test_supporting_role_cannot_replace_a_required_role(self) -> None:
        value = ready()
        value["artifacts"] = [
            item for item in value["artifacts"] if item["role"] != "REGULATOR_DATASHEET"
        ]
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("REQUIRED_ARTIFACT_ROLE_MISSING", codes(result))

    def test_unknown_mandatory_semantic_is_unknown_without_default(self) -> None:
        value = ready()
        value["operating_envelope"]["load_max_a"] = {
            "state": "UNKNOWN",
            "unit": "A",
            "reason_code": "REP01_LOAD_LIMIT_UNAVAILABLE",
        }
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("UNKNOWN", result["status"])
        self.assertIn("MANDATORY_SEMANTIC_UNKNOWN", codes(result))
        self.assertEqual({"UNKNOWN"}, {item["status"] for item in result["capability_results"]})

    def test_conflicting_mandatory_semantic_blocks(self) -> None:
        value = ready()
        value["operating_envelope"]["load_max_a"] = {
            "state": "CONFLICT",
            "unit": "A",
            "reason_code": "REP01_LOAD_CONFLICT",
            "candidates": [1.0, 1.2],
        }
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANDATORY_SEMANTIC_CONFLICT", codes(result))

    def test_unknown_and_short_rep01_revision_block(self) -> None:
        variants = (
            {"state": "UNKNOWN", "reason_code": "REP01_REVISION_UNAVAILABLE"},
            {"state": "KNOWN", "value": "abc123"},
        )
        expected = ("REVISION_UNKNOWN", "REVISION_NOT_PINNED")
        for revision, code in zip(variants, expected):
            value = ready()
            value["rep01"]["git_revision"] = revision
            rehash(value)
            with self.subTest(code=code):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, codes(result))

    def test_exact_regulator_identity_rejects_placeholder(self) -> None:
        variants = (
            ("manufacturer", "TBD Semiconductor"),
            ("manufacturer", "TBD, Semiconductor"),
            ("part_number", "UNKNOWN-PART"),
            ("part_number", "UNKNOWN#PART"),
            ("part_number", "N/A-001"),
            ("part_number", "N/A(001)"),
        )
        for field, placeholder in variants:
            value = ready()
            value["regulator"][field]["value"] = placeholder
            for item in value["artifacts"]:
                if field in item["bindings"]:
                    item["bindings"][field] = placeholder
            rehash(value)
            with self.subTest(field=field, placeholder=placeholder):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("IDENTITY_NOT_EXACT", codes(result))

    def test_cross_binding_mismatch_and_missing_binding_block(self) -> None:
        mutations = []
        mismatch = ready()
        artifact(mismatch, "REGULATOR_DATASHEET")["bindings"]["part_number"] = "OTHER-001"
        mutations.append((mismatch, "ARTIFACT_BINDING_MISMATCH"))
        missing = ready()
        del artifact(missing, "REP01_RAIL_DEMANDS")["bindings"]["rail_id"]
        mutations.append((missing, "ARTIFACT_BINDING_MISSING"))
        for value, code in mutations:
            rehash(value)
            with self.subTest(code=code):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, codes(result))

    def test_hash_mismatch_and_missing_file_block(self) -> None:
        mutations = []
        bad_hash = ready()
        artifact(bad_hash, "REGULATOR_DATASHEET")["sha256"] = "0" * 64
        mutations.append((bad_hash, "ARTIFACT_HASH_MISMATCH"))
        missing = ready()
        artifact(missing, "REGULATOR_DATASHEET")["path"] = "artifacts/missing-datasheet.txt"
        mutations.append((missing, "ARTIFACT_FILE_MISSING"))
        for value, code in mutations:
            rehash(value)
            with self.subTest(code=code):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, codes(result))

    def test_linked_artifact_path_is_rejected_before_byte_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-intake-link-", dir=REPO_ROOT) as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE_ROOT / "artifacts", root / "artifacts")
            with mock.patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path.name == "regulator-datasheet.txt",
            ):
                result = self.run_value(ready(), root)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_PATH_UNRESOLVED", codes(result))

    def test_unsafe_relative_path_blocks(self) -> None:
        value = ready()
        artifact(value, "REGULATOR_DATASHEET")["path"] = "../regulator-datasheet.txt"
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_PATH_UNSAFE", codes(result))

    def test_metadata_relabel_cannot_promote_test_fixture_to_project(self) -> None:
        value = project_descriptor(ready(), "a" * 40)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_ARTIFACT_ROOT_NOT_GIT_TOPLEVEL", codes(result))
        self.assertEqual("BLOCKED", result["project_provenance"]["status"])

    @unittest.skipUnless(shutil.which("git"), "Git is required for PROJECT provenance test")
    def test_clean_committed_git_snapshot_can_pass_project_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".tmp-intake-git-", dir=REPO_ROOT) as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE_ROOT / "artifacts", root / "artifacts")
            subprocess.run(
                ["git", "init", "-q", str(root)], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(root), "add", "artifacts"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=REP02 Test",
                    "-c",
                    "user.email=rep02-test@example.invalid",
                    "commit",
                    "-qm",
                    "synthetic intake snapshot",
                ],
                check=True,
                capture_output=True,
            )
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                encoding="ascii",
            ).stdout.strip()
            value = project_descriptor(ready(), revision)
            result = self.run_value(value, root)

            self.assertEqual("PASS", result["status"])
            self.assertEqual("PASS", result["project_provenance"]["status"])
            self.assertEqual("CLEAN", result["project_provenance"]["working_tree"])
            self.assertEqual(
                len(value["artifacts"]),
                result["project_provenance"]["committed_artifacts_verified"],
            )
            self.assertEqual(
                {"VERIFIED"},
                {
                    item["committed_artifact_status"]
                    for item in result["artifact_results"]
                },
            )

    @unittest.skipUnless(shutil.which("git"), "Git is required for PROJECT provenance test")
    def test_project_git_provenance_failures_have_distinct_reason_codes(self) -> None:
        cases = (
            "PROJECT_GIT_HEAD_MISMATCH",
            "PROJECT_GIT_WORKTREE_DIRTY",
            "PROJECT_GIT_UNTRACKED_FILES",
            "PROJECT_ARTIFACT_NOT_COMMITTED",
            "PROJECT_COMMITTED_ARTIFACT_MISMATCH",
        )
        for expected_code in cases:
            with self.subTest(reason_code=expected_code), tempfile.TemporaryDirectory(
                prefix=".tmp-intake-git-negative-", dir=REPO_ROOT
            ) as directory:
                root = Path(directory)
                shutil.copytree(FIXTURE_ROOT / "artifacts", root / "artifacts")
                (root / "README.md").write_text("synthetic project\n", encoding="utf-8")
                subprocess.run(
                    ["git", "init", "-q", str(root)], check=True, capture_output=True
                )
                subprocess.run(
                    ["git", "-C", str(root), "add", "."],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "-c",
                        "user.name=REP02 Test",
                        "-c",
                        "user.email=rep02-test@example.invalid",
                        "commit",
                        "-qm",
                        "synthetic intake snapshot",
                    ],
                    check=True,
                    capture_output=True,
                )
                revision = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="ascii",
                ).stdout.strip()
                value = project_descriptor(ready(), revision)

                if expected_code == "PROJECT_GIT_HEAD_MISMATCH":
                    value = project_descriptor(ready(), "0" * 40)
                elif expected_code == "PROJECT_GIT_WORKTREE_DIRTY":
                    (root / "README.md").write_text(
                        "synthetic project\nmodified\n", encoding="utf-8"
                    )
                elif expected_code == "PROJECT_GIT_UNTRACKED_FILES":
                    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
                elif expected_code == "PROJECT_ARTIFACT_NOT_COMMITTED":
                    item = artifact(value, "REGULATOR_DATASHEET")
                    source = root / item["path"]
                    target = root / "artifacts" / "uncommitted-datasheet.txt"
                    shutil.copyfile(source, target)
                    item["path"] = "artifacts/uncommitted-datasheet.txt"
                    rehash(value)
                else:
                    item = artifact(value, "REGULATOR_DATASHEET")
                    target = root / item["path"]
                    target.write_bytes(target.read_bytes() + b"modified\n")
                    item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
                    rehash(value)

                result = self.run_value(value, root)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_code, codes(result))

    def test_moving_document_revision_and_bad_datasheet_license_block(self) -> None:
        mutations = []
        for field, version in (
            ("document_revision", "latest"),
            ("document_revision", "1.x"),
            ("version", "releases/latest"),
            ("document_revision", "refs/tags/latest"),
            ("version", "release/stable"),
            ("version", "latest,release"),
            ("version", "stable#build"),
            ("document_revision", "refs(tags)latest"),
            ("document_revision", "1.2 - 1.4"),
            ("document_revision", "1.2 – 1.4"),
            ("document_revision", "1.2–1.4"),
            ("document_revision", "1.2—1.4"),
            ("document_revision", "1.2 to 1.4"),
            ("document_revision", "v1..v2"),
        ):
            moving = ready()
            artifact(moving, "REGULATOR_DATASHEET")[field] = version
            mutations.append((moving, "VERSION_NOT_PINNED"))
        license_gap = ready()
        artifact(license_gap, "REGULATOR_DATASHEET")["license_status"] = "NOT_APPLICABLE"
        mutations.append((license_gap, "LICENSE_STATUS_REQUIRED"))
        for value, code in mutations:
            rehash(value)
            with self.subTest(code=code):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(code, codes(result))

    def test_gap_cannot_carry_a_hash_or_provenance_metadata(self) -> None:
        for field, value_to_add in (
            ("sha256", "0" * 64),
            ("origin", "REP01_PROJECT"),
            ("document_revision", "1"),
        ):
            value = ready()
            value["artifact_gaps"][0][field] = value_to_add
            rehash(value)
            with self.subTest(field=field):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("UNEXPECTED_FIELD", codes(result))

    def test_available_and_gap_declarations_for_same_role_conflict(self) -> None:
        value = ready()
        value["artifact_gaps"].append(
            {
                "gap_id": "GAP-DATASHEET-CONFLICT",
                "role": "REGULATOR_DATASHEET",
                "reason_code": "CONTRADICTORY_DECLARATION",
            }
        )
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ARTIFACT_AVAILABILITY_CONFLICT", codes(result))

    def test_duplicate_required_role_is_ambiguous(self) -> None:
        value = ready()
        duplicate = copy.deepcopy(artifact(value, "REGULATOR_DATASHEET"))
        duplicate["artifact_id"] = "SRC-TEST-REGULATOR-DATASHEET-COPY"
        duplicate["path"] = "artifacts/regulator-datasheet-copy.txt"
        value["artifacts"].append(duplicate)
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("REQUIRED_ARTIFACT_ROLE_AMBIGUOUS", codes(result))

    def test_byte_identical_content_across_readiness_roles_blocks(self) -> None:
        value = ready()
        configuration = artifact(value, "REP01_CONFIGURATION")
        profile = artifact(value, "REP01_POWER_PROFILE")
        profile["path"] = "artifacts/rep01-configuration-copy.json"
        profile["sha256"] = configuration["sha256"]
        rehash(value)
        result = self.run_value(value)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_ARTIFACT_CONTENT_ACROSS_ROLES", codes(result))

    def test_contract_source_failure_remains_reporter_compatible(self) -> None:
        with mock.patch.object(intake_validator, "SPEC_SHA256", "0" * 64):
            result = self.run_value(ready())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("CONTRACT_SOURCE_UNAVAILABLE", codes(result))
        source_ids = {item["source_id"] for item in result["sources"]}
        self.assertNotIn("REP02-PROJECT-INTAKE-CONTRACT", source_ids)
        self.assertIn("REP02-PROJECT-INTAKE-DESCRIPTOR", source_ids)
        for issue in result["issues"]:
            self.assertTrue(set(issue["source_refs"]).issubset(source_ids))
        report = build_report_model(result)
        self.assertEqual("BLOCKED", report["status"])

    def test_operating_envelope_order_conflict_blocks(self) -> None:
        value = ready()
        value["operating_envelope"]["vin_min_v"]["value"] = 6.0
        rehash(value)
        result = self.run_value(value)
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("OPERATING_ENVELOPE_ORDER_CONFLICT", codes(result))

    def test_large_numbers_preserve_exact_order_and_never_overflow(self) -> None:
        variants = (
            (9007199254740993, 9007199254740992, 9007199254740994),
            (10**400 + 1, 10**400, 10**400 + 2),
        )
        for minimum, nominal, maximum in variants:
            value = ready()
            value["operating_envelope"]["vin_min_v"]["value"] = minimum
            value["operating_envelope"]["vin_nom_v"]["value"] = nominal
            value["operating_envelope"]["vin_max_v"]["value"] = maximum
            rehash(value)
            with self.subTest(digits=len(str(minimum))):
                result = self.run_value(value)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("OPERATING_ENVELOPE_ORDER_CONFLICT", codes(result))


if __name__ == "__main__":
    unittest.main()
