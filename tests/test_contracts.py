from __future__ import annotations

import copy
import csv
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

from power_rail_validation.contracts import (  # noqa: E402
    ContractValidationError,
    assert_valid,
    canonical_json_bytes,
    canonical_json_text,
    configuration_hash_payload,
    configuration_sha256,
    load_json,
    sha256_hexdigest,
    validate_document,
    validate_file,
)


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
SCHEMAS = SRC_ROOT / "power_rail_validation" / "contracts" / "schemas"


class ContractFixtureTests(unittest.TestCase):
    def issue_codes(self, fixture_name: str) -> set[str]:
        return {issue.code for issue in validate_file(FIXTURES / fixture_name)}

    def rebind_request_hash(self, value: dict) -> str:
        digest = configuration_sha256(value["request"])
        value["request"]["configuration_sha256"] = digest
        value["response"]["input_configuration_sha256"] = digest
        for finding in value["response"]["findings"]:
            finding["input_hash"] = digest
        return digest

    def test_valid_handoff_accepts_all_six_entities_and_zero(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        self.assertEqual([], validate_document(value))
        self.assertEqual(
            0,
            value["request"]["collections"]["loads"][0]["operating_current"]["value"],
            "known zero must not collapse into null/unknown",
        )

    def test_missing_source_is_explicit_and_valid(self) -> None:
        value = load_json(FIXTURES / "missing.json")
        self.assertEqual([], validate_document(value))
        self.assertEqual("MISSING", value["access_status"])
        self.assertEqual({"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}, value["content_hash"])

    def test_missing_required_field_is_rejected(self) -> None:
        value = load_json(FIXTURES / "valid.json")["request"]["collections"]["sources"][0]
        del value["access_status"]
        self.assertIn("MISSING_FIELD", {item.code for item in validate_document(value)})

    def test_entity_schema_version_is_pinned(self) -> None:
        value = load_json(FIXTURES / "valid.json")["request"]["collections"]["rails"][0]
        value["schema_version"] = "999.not-semver"
        self.assertIn("SCHEMA_VERSION_UNSUPPORTED", {item.code for item in validate_document(value)})

    def test_request_contract_version_is_pinned(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["contract_version"] = "2.0.0"
        self.rebind_request_hash(value)
        value["response"]["contract_version"] = "2.0.0"
        self.assertIn("CONTRACT_VERSION_UNSUPPORTED", {item.code for item in validate_document(value)})

    def test_unitless_bare_number_is_rejected(self) -> None:
        self.assertIn("QUANTITY_OBJECT_REQUIRED", self.issue_codes("wrong-type.json"))

    def test_invalid_field_unit_is_rejected(self) -> None:
        value = load_json(FIXTURES / "valid.json")["request"]["collections"]["regulators"][0]
        value["switching_frequency"]["unit"] = "kHz"
        self.assertIn("UNIT_ENUM", {item.code for item in validate_document(value)})

    def test_unknown_required_limit_cannot_pass(self) -> None:
        self.assertIn("PASS_WITHOUT_KNOWN_LIMIT", self.issue_codes("unknown-limit.json"))

    def test_conflicting_source_cannot_support_pass(self) -> None:
        self.assertIn("PASS_FROM_UNSAFE_SOURCE", self.issue_codes("conflict.json"))

    def test_assumption_source_alone_cannot_support_pass(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["sources"][0]["classification"] = "ASSUMPTION"
        self.rebind_request_hash(value)
        self.assertIn("PASS_FROM_UNSAFE_SOURCE", {item.code for item in validate_document(value)})

    def test_malformed_json_has_stable_issue(self) -> None:
        self.assertEqual({"MALFORMED_JSON"}, self.issue_codes("malformed.json"))

    def test_hash_mismatch_is_a_hard_failure(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["input_configuration_sha256"] = "d" * 64
        self.assertIn("HASH_MISMATCH", {item.code for item in validate_document(value)})

    def test_response_contract_version_must_match_request(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["contract_version"] = "2.0.0"
        self.assertIn("CONTRACT_VERSION_MISMATCH", {item.code for item in validate_document(value)})

    def test_check_requires_reason_code(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        del value["response"]["checks"][0]["reason_code"]
        self.assertIn("MISSING_FIELD", {item.code for item in validate_document(value)})

    def test_request_hash_is_recomputed_from_defined_material(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        self.assertEqual(value["request"]["configuration_sha256"], configuration_sha256(value["request"]))
        value["request"]["collections"]["loads"][0]["peak_current"]["value"] = 0.81
        self.assertIn(
            "REQUEST_CONFIGURATION_HASH_MISMATCH",
            {item.code for item in validate_document(value)},
        )

    def test_hash_encoding_failure_is_a_hard_failure(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["metadata"] = math.nan
        self.assertIn(
            "REQUEST_HASH_UNCOMPUTABLE",
            {item.code for item in validate_document(value)},
        )

    def test_non_finite_json_literals_are_rejected_during_load(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
                path = Path(temp_dir) / "non-finite.json"
                path.write_text('{"value": ' + token + "}", encoding="utf-8")
                self.assertEqual(
                    {"NON_FINITE_JSON_NUMBER"},
                    {item.code for item in validate_file(path)},
                )

    def test_duplicate_json_keys_are_rejected_during_load(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "duplicate-key.json"
            path.write_text('{"request": {}, "request": {}}', encoding="utf-8")
            self.assertEqual(
                {"DUPLICATE_JSON_KEY"},
                {item.code for item in validate_file(path)},
            )

    def test_unexpected_request_field_is_rejected_instead_of_escaping_hash_scope(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["requested_analyses"] = ["LOAD_STEP"]
        self.assertIn("UNEXPECTED_FIELD", {item.code for item in validate_document(value)})

    def test_unhashable_source_classification_and_model_license_do_not_crash(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["sources"][0]["classification"] = []
        self.rebind_request_hash(value)
        value["response"]["model_records"] = [
            {
                "model_id": "MODEL-ENUM-TYPE",
                "source": "SRC-DATASHEET-001",
                "sha256": "f" * 64,
                "license_status": [],
                "model_type": "SYNTHETIC",
                "validity_bounds": {"temperature": "declared"},
                "access_status": "AVAILABLE",
            }
        ]
        value["response"]["checks"][0]["model_refs"] = ["MODEL-ENUM-TYPE"]
        codes = {item.code for item in validate_document(value)}
        self.assertIn("PROVENANCE_ENUM", codes)
        self.assertIn("LICENSE_STATUS_ENUM", codes)
        self.assertIn("PASS_FROM_UNUSABLE_MODEL", codes)

    def test_load_rail_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["loads"][0]["rail_id"] = "NO_SUCH_RAIL"
        self.rebind_request_hash(value)
        self.assertIn("RAIL_REF_UNRESOLVED", {item.code for item in validate_document(value)})

    def test_regulator_input_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["regulators"][0]["input_ref"] = "NO_SUCH_INPUT"
        self.rebind_request_hash(value)
        self.assertIn("RAIL_REF_UNRESOLVED", {item.code for item in validate_document(value)})

    def test_finding_subject_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["findings"][0]["subject_ref"]["entity_id"] = "NO_SUCH_RAIL"
        self.assertIn("SUBJECT_REF_UNRESOLVED", {item.code for item in validate_document(value)})

    def test_evaluation_input_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["checks"][0]["inputs_used"] = ["NO_SUCH_INPUT"]
        self.assertIn("EVALUATION_INPUT_UNRESOLVED", {item.code for item in validate_document(value)})

    def test_unhashable_finding_source_identifier_does_not_crash(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["findings"][0]["source_refs"][0]["source_id"] = []
        self.assertIn("TYPE_STRING", {item.code for item in validate_document(value)})

    def test_large_exponent_that_decodes_to_infinity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "overflow-number.json"
            path.write_text('{"value": 1e9999}', encoding="utf-8")
            self.assertIn("NON_FINITE_NUMBER", {item.code for item in validate_file(path)})

    def test_entity_field_outside_closed_schema_is_rejected(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["rails"][0]["future_technical_field"] = "unexpected"
        self.rebind_request_hash(value)
        self.assertIn("UNEXPECTED_FIELD", {item.code for item in validate_document(value)})

    def test_finding_is_bound_to_input_hash(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["findings"][0]["input_hash"] = "e" * 64
        self.assertIn("FINDING_INPUT_HASH_MISMATCH", {item.code for item in validate_document(value)})

    def test_summary_pass_cannot_hide_non_pass_result(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["checks"][0]["status"] = "NOT_RUN"
        self.assertIn("PASS_SCOPE_VIOLATION", {item.code for item in validate_document(value)})

    def test_wrong_nested_enum_type_returns_issue_instead_of_crashing(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["findings"][0]["status"] = []
        self.assertIn("STATUS_ENUM", {item.code for item in validate_document(value)})

    def test_unhashable_entity_and_quantity_enums_return_issues(self) -> None:
        rail = load_json(FIXTURES / "valid.json")["request"]["collections"]["rails"][0]
        rail["entity_type"] = []
        self.assertIn("ENTITY_TYPE", {item.code for item in validate_document(rail)})
        rail["entity_type"] = "rail"
        rail["nominal_voltage"]["state"] = []
        self.assertIn("QUANTITY_STATE", {item.code for item in validate_document(rail)})

    def test_pass_cannot_use_unavailable_source(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        source = value["request"]["collections"]["sources"][0]
        source["access_status"] = "MISSING"
        source["content_hash"] = {"state": "UNKNOWN", "algorithm": "SHA-256", "value": None}
        self.rebind_request_hash(value)
        codes = {item.code for item in validate_document(value)}
        self.assertIn("PASS_FROM_UNAVAILABLE_SOURCE", codes)

    def test_pass_check_evidence_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["checks"][0]["evidence_refs"] = ["SRC-NOT-PRESENT"]
        self.assertIn(
            "EVALUATION_EVIDENCE_UNRESOLVED",
            {item.code for item in validate_document(value)},
        )

    def test_entity_source_reference_must_resolve(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["rails"][0]["source_refs"] = ["SRC-NOT-PRESENT"]
        self.rebind_request_hash(value)
        self.assertIn("SOURCE_REF_UNRESOLVED", {item.code for item in validate_document(value)})

    def test_duplicate_entity_identifier_is_rejected(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["request"]["collections"]["rails"].append(
            copy.deepcopy(value["request"]["collections"]["rails"][0])
        )
        self.rebind_request_hash(value)
        self.assertIn("DUPLICATE_ENTITY_ID", {item.code for item in validate_document(value)})

    def test_pass_cannot_use_unavailable_or_unbounded_model(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["model_records"] = [
            {
                "model_id": "MODEL-1",
                "source": "SRC-DATASHEET-001",
                "sha256": "f" * 64,
                "license_status": "UNKNOWN",
                "model_type": "SYNTHETIC",
                "validity_bounds": {},
                "access_status": "MISSING",
            }
        ]
        value["response"]["checks"][0]["model_refs"] = ["MODEL-1"]
        self.assertIn("PASS_FROM_UNUSABLE_MODEL", {item.code for item in validate_document(value)})

    def test_claim_scope_cannot_expand(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["claim_limits"].append("TRANSIENT_STABILITY")
        self.assertIn("CLAIM_SCOPE_EXPANDED", {item.code for item in validate_document(value)})

    def test_response_cannot_drop_request_claim_or_constraint(self) -> None:
        value = load_json(FIXTURES / "valid.json")
        value["response"]["claim_limits"] = ["POWER_BUDGET"]
        self.assertIn("CLAIM_SCOPE_REDUCED", {item.code for item in validate_document(value)})

    def test_assert_valid_raises_structured_exception(self) -> None:
        with self.assertRaises(ContractValidationError) as context:
            assert_valid(load_json(FIXTURES / "wrong-type.json"))
        self.assertTrue(context.exception.issues)


class CanonicalJsonTests(unittest.TestCase):
    def test_sorted_minified_utf8_contract(self) -> None:
        value = {"z": 0, "a": "ölçüm", "nested": {"b": 2, "a": 1}}
        expected = '{"a":"ölçüm","nested":{"a":1,"b":2},"z":0}'
        self.assertEqual(expected, canonical_json_text(value))
        self.assertEqual(expected.encode("utf-8"), canonical_json_bytes(value))
        self.assertEqual(hashlib.sha256(expected.encode("utf-8")).hexdigest(), sha256_hexdigest(value))

    def test_non_finite_number_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json_text({"value": math.nan})

    def test_configuration_hash_ignores_key_order_but_tracks_technical_change(self) -> None:
        request = load_json(FIXTURES / "valid.json")["request"]
        reordered = {key: request[key] for key in reversed(tuple(request))}
        self.assertEqual(configuration_hash_payload(request), configuration_hash_payload(reordered))
        self.assertEqual(configuration_sha256(request), configuration_sha256(reordered))
        changed = copy.deepcopy(request)
        changed["collections"]["rails"][0]["nominal_voltage"]["value"] = 3.31
        self.assertNotEqual(configuration_sha256(request), configuration_sha256(changed))


class JsonSchemaTests(unittest.TestCase):
    def test_six_d1_entity_schemas_use_draft_2020_12(self) -> None:
        expected = {"rail", "load", "regulator", "source", "event", "finding"}
        observed = set()
        for name in expected:
            path = SCHEMAS / f"{name}.schema.json"
            with path.open("r", encoding="utf-8") as handle:
                schema = json.load(handle)
            self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
            self.assertEqual("1.0.0", schema["properties"]["schema_version"]["const"])
            observed.add(path.name.removesuffix(".schema.json"))
        self.assertEqual(expected, observed)

    def test_all_local_schema_refs_resolve(self) -> None:
        def visit(node, refs):
            if isinstance(node, dict):
                if isinstance(node.get("$ref"), str):
                    refs.append(node["$ref"])
                for child in node.values():
                    visit(child, refs)
            elif isinstance(node, list):
                for child in node:
                    visit(child, refs)

        for path in SCHEMAS.glob("*.schema.json"):
            with path.open("r", encoding="utf-8") as handle:
                schema = json.load(handle)
            refs = []
            visit(schema, refs)
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"{path.name}: unsupported ref {ref}")
                self.assertIn(ref.removeprefix("#/$defs/"), schema.get("$defs", {}), path.name)

    def test_finding_schema_requires_auditable_fields(self) -> None:
        with (SCHEMAS / "finding.schema.json").open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        required = set(schema["required"])
        self.assertTrue(
            {
                "rule_id",
                "status",
                "reason_code",
                "source_refs",
                "input_hash",
                "tool_version",
                "package_version",
            }.issubset(required)
        )
        self.assertEqual(
            {"PASS", "FAIL", "UNKNOWN", "BLOCKED", "NOT_RUN"},
            set(schema["$defs"]["status"]["enum"]),
        )
        self.assertEqual(
            1,
            schema["$defs"]["limitContext"]["allOf"][0]["then"]["properties"]["refs"]["minItems"],
        )

    def test_provenance_enum_is_complete(self) -> None:
        with (SCHEMAS / "rail.schema.json").open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertEqual(
            {
                "SOURCE_EXPLICIT",
                "DERIVED",
                "ENGINEERING_RULE",
                "ASSUMPTION",
                "UNKNOWN",
                "CONFLICT",
            },
            set(schema["$defs"]["provenance"]["enum"]),
        )

    def test_event_schema_carries_sequence_semantics(self) -> None:
        with (SCHEMAS / "event.schema.json").open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertTrue({"event_kind", "state", "time_offset"}.issubset(set(schema["required"])))
        self.assertTrue(
            {"SIGNAL_TRANSITION", "POWER_STATE", "FAULT"}.issubset(
                set(schema["properties"]["event_type"]["enum"])
            )
        )
        self.assertTrue(
            {"VIN_VALID", "EN", "PGOOD", "RESET", "POWER_OFF", "BROWNOUT", "RAIL_LOSS", "RESTART"}.issubset(
                set(schema["properties"]["event_kind"]["enum"])
            )
        )

    def test_source_schema_never_silently_omits_availability_or_hash_state(self) -> None:
        with (SCHEMAS / "source.schema.json").open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertTrue({"access_status", "content_hash"}.issubset(set(schema["required"])))
        self.assertEqual(
            {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"},
            set(schema["properties"]["access_status"]["enum"]),
        )

    def test_field_dictionary_covers_all_entities(self) -> None:
        dictionary_path = SRC_ROOT / "power_rail_validation" / "contracts" / "field_dictionary.csv"
        with dictionary_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            [
                "entity_type",
                "field_path",
                "required",
                "data_type",
                "allowed_values/unit",
                "unknown_policy",
                "description",
            ],
            list(rows[0]),
        )
        self.assertEqual(
            {"rail", "load", "regulator", "source", "event", "finding"},
            {row["entity_type"] for row in rows},
        )


if __name__ == "__main__":
    unittest.main()

