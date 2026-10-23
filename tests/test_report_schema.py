from __future__ import annotations

import copy
import json
import sys
import unittest
from importlib import resources
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation.reporting import (  # noqa: E402
    REPORTER_VERSION,
    REPORT_SCHEMA_VERSION,
    ReportValidationError,
    build_report_model,
    render_json_report,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def component() -> dict:
    return {
        "rule_id": "REP02.REPORT_SCHEMA.TEST",
        "status": "PASS",
        "reason_code": "CONTRACT_SAMPLE",
        "source_refs": [
            {
                "source_id": "SRC-REPORT-SCHEMA",
                "version": "fixture-1",
                "content_hash": HASH_B,
            }
        ],
        "input_hash": HASH_A,
        "tool_name": "power-rail-validation",
        "tool_version": "0.1.0",
        "package_version": "0.4.0",
        "issues": [],
        "limitations": ["SYNTHETIC_CONTRACT_SAMPLE"],
    }


def load_packaged_schema() -> dict:
    schema_resource = resources.files("power_rail_validation.contracts").joinpath(
        "schemas/report-model.schema.json"
    )
    return json.loads(schema_resource.read_text(encoding="utf-8"))


class PublicReportSchemaTests(unittest.TestCase):
    def test_schema_is_packaged_versioned_and_draft_2020_12(self) -> None:
        schema = load_packaged_schema()
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual(
            "https://contracts.rep02.invalid/v1/report-model.schema.json",
            schema["$id"],
        )
        self.assertEqual(REPORT_SCHEMA_VERSION, schema["properties"]["schema_version"]["const"])
        self.assertEqual(REPORTER_VERSION, schema["properties"]["reporter_version"]["const"])

    def test_schema_field_sets_match_the_public_model(self) -> None:
        schema = load_packaged_schema()
        model = build_report_model(component(), generated_at="2026-10-24T12:00:00+03:00")

        self.assertEqual(set(model), set(schema["properties"]))
        self.assertEqual(set(model) - {"generated_at"}, set(schema["required"]))

        component_schema = schema["$defs"]["componentResult"]
        self.assertEqual(set(model["component_results"][0]), set(component_schema["properties"]))
        self.assertEqual(set(component_schema["properties"]), set(component_schema["required"]))

        finding_schema = schema["$defs"]["reportFinding"]
        self.assertEqual(set(model["findings"][0]), set(finding_schema["properties"]))
        self.assertEqual(set(finding_schema["properties"]), set(finding_schema["required"]))

        adapter_schema = schema["$defs"]["adapterLimitation"]
        self.assertEqual(set(model["adapter_limitations"][0]), set(adapter_schema["properties"]))
        self.assertEqual(set(adapter_schema["properties"]), set(adapter_schema["required"]))

    def test_every_object_shape_is_closed_and_all_refs_are_local(self) -> None:
        schema = load_packaged_schema()

        def visit(node: object, path: str = "$") -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(False, node.get("additionalProperties"), path)
                ref = node.get("$ref")
                if ref is not None:
                    self.assertIsInstance(ref, str, path)
                    self.assertTrue(ref.startswith("#/$defs/"), path)
                    self.assertIn(ref.removeprefix("#/$defs/"), schema["$defs"], path)
                for key, child in node.items():
                    visit(child, f"{path}.{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    visit(child, f"{path}[{index}]")

        visit(schema)

    def test_report_findings_remain_distinct_from_d1_finding_entities(self) -> None:
        schema = load_packaged_schema()
        required = set(schema["$defs"]["reportFinding"]["required"])
        self.assertTrue(
            {"finding_kind", "technical_locator", "technical_result_hash", "tool_name"}.issubset(
                required
            )
        )
        self.assertTrue(
            {"schema_version", "entity_type", "subject_ref", "limit_context"}.isdisjoint(required)
        )
        model = build_report_model(component())
        self.assertIn("MODULE_LOCAL_RESULTS_ARE_NOT_COERCED_TO_D1_FINDINGS", model["limitations"])

    def test_runtime_still_rejects_fields_outside_the_closed_contract(self) -> None:
        model = build_report_model(component())
        tampered = copy.deepcopy(model)
        tampered["findings"][0]["subject_ref"] = {
            "entity_type": "rail",
            "entity_id": "RAIL-1",
        }
        with self.assertRaisesRegex(ReportValidationError, "unexpected field"):
            render_json_report(tampered)


if __name__ == "__main__":
    unittest.main()
