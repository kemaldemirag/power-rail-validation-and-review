from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation.contracts.canonical import sha256_hexdigest  # noqa: E402
from power_rail_validation.reporting import (  # noqa: E402
    REPORTER_VERSION,
    ReportValidationError,
    build_evidence_manifest,
    build_report_model,
    render_json_report,
    render_markdown_report,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def component(
    status: str = "PASS",
    *,
    rule_id: str = "REP02.TEST.RULE",
    reason_code: str = "TECHNICAL_RESULT",
    input_hash: str = HASH_A,
) -> dict:
    return {
        "rule_id": rule_id,
        "status": status,
        "reason_code": reason_code,
        "source_refs": [
            {
                "source_id": "SRC-TEST",
                "version": "fixture-1",
                "content_hash": HASH_B,
            }
        ],
        "input_hash": input_hash,
        "tool_name": "power-rail-validation",
        "tool_version": "0.3.0",
        "package_version": "0.3.0",
        "issues": [],
        "limitations": ["SYNTHETIC_TEST_ONLY"],
    }


def manifest_entry(identifier: str, path: str, payload: bytes, version: str = "1.0.0") -> dict:
    return {
        "id": identifier,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "version": version,
    }


class ReportModelTests(unittest.TestCase):
    def test_one_result_builds_auditable_model_without_mutating_input(self) -> None:
        value = component()
        original = copy.deepcopy(value)
        model = build_report_model(value)
        self.assertEqual(original, value)
        self.assertEqual("PASS", model["status"])
        self.assertEqual(REPORTER_VERSION, model["reporter_version"])
        self.assertEqual(sha256_hexdigest(value), model["component_results"][0]["technical_result_hash"])
        self.assertEqual(
            {"source_id": "SRC-TEST", "version": "fixture-1", "sha256": HASH_B},
            model["findings"][0]["source_refs"][0],
        )
        self.assertEqual(
            sha256_hexdigest(
                {key: item for key, item in model.items() if key not in {"generated_at", "report_model_hash"}}
            ),
            model["report_model_hash"],
        )

    def test_component_list_is_order_independent(self) -> None:
        first = component(rule_id="REP02.A", input_hash=HASH_A)
        second = component(status="UNKNOWN", rule_id="REP02.B", input_hash=HASH_B)
        self.assertEqual(build_report_model([first, second]), build_report_model([second, first]))

    def test_status_precedence_is_blocked_fail_unknown_not_run_pass(self) -> None:
        expected = [
            (["PASS"], "PASS"),
            (["PASS", "NOT_RUN"], "NOT_RUN"),
            (["NOT_RUN", "UNKNOWN"], "UNKNOWN"),
            (["UNKNOWN", "FAIL"], "FAIL"),
            (["FAIL", "BLOCKED"], "BLOCKED"),
        ]
        for statuses, aggregate in expected:
            values = [
                component(status, rule_id=f"REP02.RULE{index}", input_hash=f"{index + 1:064x}")
                for index, status in enumerate(statuses)
            ]
            with self.subTest(statuses=statuses):
                self.assertEqual(aggregate, build_report_model(values)["status"])

    def test_technical_fail_is_not_converted_by_expectation_match(self) -> None:
        value = component("FAIL", reason_code="INJECTED_FAULT_OBSERVED")
        value["expectation"] = {"status": "FAIL", "match": True}
        model = build_report_model(value)
        self.assertEqual("FAIL", model["status"])
        self.assertEqual("FAIL", model["findings"][0]["status"])
        self.assertEqual("INJECTED_FAULT_OBSERVED", model["findings"][0]["reason_code"])

    def test_issue_findings_preserve_more_severe_status(self) -> None:
        value = component("PASS")
        value["issues"] = [
            {
                "severity": "FAIL",
                "code": "DEEP_FAILURE",
                "path": "$.metrics",
                "message": "A technical metric failed.",
            }
        ]
        model = build_report_model(value)
        self.assertEqual("FAIL", model["status"])
        self.assertEqual({"PASS": 1, "FAIL": 1}, {key: count for key, count in model["status_counts"].items() if count})

    def test_json_and_markdown_have_identical_finding_triples(self) -> None:
        value = component("FAIL")
        value["issues"] = [
            {
                "status": "UNKNOWN",
                "reason_code": "MEASUREMENT_UNKNOWN",
                "message": "Measurement | is not available.\nNo default applied.",
            }
        ]
        model = build_report_model(value)
        parsed = json.loads(render_json_report(model))
        json_triples = [
            (item["rule_id"], item["status"], item["reason_code"]) for item in parsed["findings"]
        ]
        markdown = render_markdown_report(model)
        table_rows = [line for line in markdown.splitlines() if line.startswith("| RPT-")]
        markdown_triples = []
        for row in table_rows:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            markdown_triples.append(tuple(cells[1:4]))
        self.assertEqual(json_triples, markdown_triples)
        self.assertEqual(
            [item["finding_id"] for item in parsed["findings"]],
            [row.strip("|").split("|")[0].strip() for row in table_rows],
        )
        for finding in parsed["findings"]:
            self.assertIn(f"- `{finding['finding_id']}`:", markdown)
        self.assertIn("Measurement \\| is not available. No default applied.", markdown)

    def test_generated_at_is_explicit_and_outside_all_technical_hashes(self) -> None:
        value = component()
        first = build_report_model(value, generated_at="2026-10-19T10:00:00+03:00")
        second = build_report_model(value, generated_at="2026-10-19T11:00:00+03:00")
        self.assertEqual(first["report_model_hash"], second["report_model_hash"])
        self.assertEqual(
            first["component_results"][0]["technical_result_hash"],
            second["component_results"][0]["technical_result_hash"],
        )
        embedded = component()
        embedded["generated_at"] = "2026-10-19T10:00:00+03:00"
        with self.assertRaisesRegex(ReportValidationError, "explicitly"):
            build_report_model(embedded)

    def test_live_adapters_remain_explicit_not_run_limitations(self) -> None:
        model = build_report_model(component())
        self.assertEqual({"REP-01", "REP-03"}, {item["adapter_id"] for item in model["adapter_limitations"]})
        self.assertEqual({"NOT_RUN"}, {item["status"] for item in model["adapter_limitations"]})
        self.assertIn("MODULE_LOCAL_RESULTS_ARE_NOT_COERCED_TO_D1_FINDINGS", model["limitations"])

    def test_rail_style_source_reference_resolves_from_embedded_catalog(self) -> None:
        value = component()
        value["source_refs"] = [{"source_id": "SRC-TEST", "version": "fixture-1"}]
        value["calculation_inputs"] = {
            "sources": [
                {
                    "source_id": "SRC-TEST",
                    "version": "fixture-1",
                    "content_hash": {"state": "KNOWN", "algorithm": "SHA-256", "value": HASH_B},
                }
            ]
        }
        self.assertEqual(HASH_B, build_report_model(value)["findings"][0]["source_refs"][0]["sha256"])

    def test_versioned_sequencing_result_gets_module_local_summary_rule(self) -> None:
        value = component()
        del value["rule_id"]
        value["fsm_version"] = "REP02-SEQ-FSM-1.0.0"
        value["rule_set_version"] = "REP02-SEQ-RULESET-1.0.0"
        model = build_report_model(value)
        self.assertEqual("REP02.SEQUENCING.FSM", model["findings"][0]["rule_id"])

    def test_fault_result_adapter_expands_source_ids_and_engine_version(self) -> None:
        value = component("FAIL", reason_code="FAULT_SCENARIO_COMPLETE")
        del value["rule_id"]
        del value["tool_version"]
        del value["source_refs"]
        del value["issues"]
        value.update(
            {
                "engine_version": "0.1.0",
                "rule_set_version": "REP02-FI-RULESET-1.0.0",
                "fault_results": [{"status": "FAIL"}],
                "sources": [
                    {
                        "source_id": "SRC-TEST",
                        "version": "fixture-1",
                        "content_hash": HASH_B,
                    }
                ],
                "findings": [
                    {
                        "finding_id": "FI-FINDING-0001",
                        "fault_id": "FAULT-1",
                        "rule_id": "FI.BROWNOUT.THRESHOLD",
                        "status": "FAIL",
                        "reason_code": "BROWNOUT_EFFECT_TRIGGERED",
                        "path": "$.faults[0]",
                        "message": "Technical fault effect was observed.",
                        "source_refs": ["SRC-TEST"],
                    }
                ],
            }
        )
        model = build_report_model(value)
        self.assertEqual("FAIL", model["status"])
        self.assertEqual("0.1.0", model["component_results"][0]["tool_version"])
        self.assertEqual(
            {"REP02.FAULT_INJECTION.CAMPAIGN", "FI.BROWNOUT.THRESHOLD"},
            {item["rule_id"] for item in model["findings"]},
        )
        self.assertEqual(
            {HASH_B},
            {ref["sha256"] for item in model["findings"] for ref in item["source_refs"]},
        )

    def test_deep_invalid_and_missing_fields_fail_closed(self) -> None:
        mutations = []
        missing = component()
        del missing["tool_version"]
        mutations.append(missing)
        bad_status = component()
        bad_status["status"] = "EXPECTED_FAIL"
        mutations.append(bad_status)
        bad_hash = component()
        bad_hash["input_hash"] = "A" * 64
        mutations.append(bad_hash)
        moving_version = component()
        moving_version["source_refs"][0]["version"] = "latest"
        mutations.append(moving_version)
        missing_source_hash = component()
        del missing_source_hash["source_refs"][0]["content_hash"]
        mutations.append(missing_source_hash)
        issue_extra = component()
        issue_extra["issues"] = [
            {"severity": "FAIL", "code": "BAD", "message": "bad", "future": True}
        ]
        mutations.append(issue_extra)
        empty_issue_sources = component()
        empty_issue_sources["issues"] = [
            {"severity": "UNKNOWN", "code": "NO_EVIDENCE", "message": "unknown", "source_refs": []}
        ]
        mutations.append(empty_issue_sources)
        null_component_sources = component()
        null_component_sources["source_refs"] = None
        null_component_sources["sources"] = [
            {"source_id": "SRC-TEST", "version": "fixture-1", "content_hash": HASH_B}
        ]
        mutations.append(null_component_sources)
        null_component_id = component()
        null_component_id["component_id"] = None
        mutations.append(null_component_id)
        for index, value in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ReportValidationError):
                build_report_model(value)

    def test_source_mismatch_and_duplicate_fail_closed(self) -> None:
        mismatch = component()
        mismatch["sources"] = [
            {
                "source_id": "SRC-TEST",
                "version": "fixture-1",
                "content_hash": HASH_C,
            }
        ]
        with self.assertRaisesRegex(ReportValidationError, "conflicts"):
            build_report_model(mismatch)
        duplicate = component()
        duplicate["source_refs"].append(copy.deepcopy(duplicate["source_refs"][0]))
        with self.assertRaisesRegex(ReportValidationError, "duplicate"):
            build_report_model(duplicate)

    def test_duplicate_component_and_empty_list_fail_closed(self) -> None:
        value = component()
        with self.assertRaises(ReportValidationError):
            build_report_model([])
        with self.assertRaisesRegex(ReportValidationError, "duplicate"):
            build_report_model([value, copy.deepcopy(value)])

    def test_nonfinite_non_json_and_cycles_fail_closed(self) -> None:
        nonfinite = component()
        nonfinite["metric"] = math.nan
        with self.assertRaisesRegex(ReportValidationError, "non-finite"):
            build_report_model(nonfinite)
        non_json = component()
        non_json["metric"] = {1, 2}
        with self.assertRaisesRegex(ReportValidationError, "unsupported JSON"):
            build_report_model(non_json)
        cyclic = component()
        cyclic["cycle"] = cyclic
        with self.assertRaisesRegex(ReportValidationError, "cyclic"):
            build_report_model(cyclic)
        deep = component()
        nested = None
        for _ in range(1500):
            nested = [nested]
        deep["deep"] = nested
        with self.assertRaisesRegex(ReportValidationError, "cannot be canonicalized"):
            build_report_model(deep)

    def test_renderer_rejects_tampered_hash_and_unexpected_field(self) -> None:
        tampered = build_report_model(component())
        tampered["findings"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(ReportValidationError, "counts|precedence|hash|finding_id"):
            render_json_report(tampered)
        unexpected = build_report_model(component())
        unexpected["future"] = "not in closed contract"
        with self.assertRaisesRegex(ReportValidationError, "unexpected"):
            render_markdown_report(unexpected)

    def test_markdown_escapes_untrusted_links_images_and_html(self) -> None:
        value = component("UNKNOWN")
        value["issues"] = [
            {
                "severity": "UNKNOWN",
                "code": "UNTRUSTED_MESSAGE",
                "message": "![probe](https://attacker.invalid/pixel) <script>alert(1)</script>",
            }
        ]
        markdown = render_markdown_report(build_report_model(value))
        self.assertNotIn("![probe](https://attacker.invalid/pixel)", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertIn(r"\!\[probe\]\(https://attacker.invalid/pixel\)", markdown)


class EvidenceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=REPO_ROOT)
        self.base = Path(self.temp.name)
        self.payloads = {
            "input.json": b"input\n",
            "source.json": b"source\n",
            "model.dat": b"model\n",
            "tool.py": b"tool\n",
            "output.json": b"output\n",
        }
        for path, payload in self.payloads.items():
            (self.base / path).write_bytes(payload)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def entries(self) -> dict:
        return {
            "inputs": [manifest_entry("INPUT-1", "input.json", self.payloads["input.json"])],
            "sources": [manifest_entry("SOURCE-1", "source.json", self.payloads["source.json"])],
            "models": [],
            "tools": [manifest_entry("TOOL-1", "tool.py", self.payloads["tool.py"])],
            "outputs": [manifest_entry("OUTPUT-1", "output.json", self.payloads["output.json"])],
        }

    def test_manifest_binds_file_bytes_path_id_hash_and_version(self) -> None:
        entries = self.entries()
        original = copy.deepcopy(entries)
        manifest = build_evidence_manifest(entries, base_dir=self.base)
        self.assertEqual(original, entries)
        self.assertEqual([], manifest["models"])
        self.assertEqual("input.json", manifest["inputs"][0]["path"])
        self.assertEqual(
            hashlib.sha256(self.payloads["input.json"]).hexdigest(),
            manifest["inputs"][0]["sha256"],
        )
        expected = sha256_hexdigest(
            {key: value for key, value in manifest.items() if key not in {"generated_at", "manifest_hash"}}
        )
        self.assertEqual(expected, manifest["manifest_hash"])

    def test_manifest_generated_at_is_explicit_and_outside_hash(self) -> None:
        first = build_evidence_manifest(
            self.entries(), base_dir=self.base, generated_at="2026-10-19T10:00:00Z"
        )
        second = build_evidence_manifest(
            self.entries(), base_dir=self.base, generated_at="2026-10-19T11:00:00Z"
        )
        self.assertEqual(first["manifest_hash"], second["manifest_hash"])

    def test_manifest_accepts_a_pinned_model_when_one_is_used(self) -> None:
        entries = self.entries()
        entries["models"] = [
            manifest_entry("MODEL-1", "model.dat", self.payloads["model.dat"], "fixture-1")
        ]
        manifest = build_evidence_manifest(entries, base_dir=self.base)
        self.assertEqual("MODEL-1", manifest["models"][0]["id"])

    def test_empty_missing_unexpected_and_bad_hash_fail_closed(self) -> None:
        cases = []
        empty = self.entries()
        empty["outputs"] = []
        cases.append(empty)
        missing = self.entries()
        del missing["tools"]
        cases.append(missing)
        missing_models = self.entries()
        del missing_models["models"]
        cases.append(missing_models)
        unexpected = self.entries()
        unexpected["future"] = []
        cases.append(unexpected)
        bad_hash = self.entries()
        bad_hash["inputs"][0]["sha256"] = HASH_A
        cases.append(bad_hash)
        entry_extra = self.entries()
        entry_extra["inputs"][0]["size"] = 6
        cases.append(entry_extra)
        for index, value in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ReportValidationError):
                build_evidence_manifest(value, base_dir=self.base)

    def test_duplicate_id_or_path_and_version_range_fail_closed(self) -> None:
        duplicate_id = self.entries()
        duplicate_id["outputs"][0]["id"] = "INPUT-1"
        with self.assertRaisesRegex(ReportValidationError, "duplicate manifest id"):
            build_evidence_manifest(duplicate_id, base_dir=self.base)
        duplicate_path = self.entries()
        duplicate_path["outputs"][0]["path"] = "input.json"
        duplicate_path["outputs"][0]["sha256"] = hashlib.sha256(self.payloads["input.json"]).hexdigest()
        with self.assertRaisesRegex(ReportValidationError, "duplicate manifest path"):
            build_evidence_manifest(duplicate_path, base_dir=self.base)
        moving = self.entries()
        moving["tools"][0]["version"] = ">=0.1"
        with self.assertRaisesRegex(ReportValidationError, "pinned"):
            build_evidence_manifest(moving, base_dir=self.base)

    def test_absolute_traversal_missing_file_and_invalid_timestamp_fail_closed(self) -> None:
        paths = (
            "../input.json",
            str((self.base / "input.json").resolve()),
            "C:/absolute/input.json",
            "nested//input.json",
            "missing.json",
        )
        for path in paths:
            value = self.entries()
            value["inputs"][0]["path"] = path.replace("\\", "/")
            with self.subTest(path=path), self.assertRaises(ReportValidationError):
                build_evidence_manifest(value, base_dir=self.base)
        with self.assertRaisesRegex(ReportValidationError, "timezone"):
            build_evidence_manifest(
                self.entries(), base_dir=self.base, generated_at="2026-10-19T10:00:00"
            )
        with self.assertRaisesRegex(ReportValidationError, "RFC 3339"):
            build_evidence_manifest(
                self.entries(), base_dir=self.base, generated_at="2026-10-19T10:00:00+03"
            )


if __name__ == "__main__":
    unittest.main()
