"""Generate deterministic REP-02 D3 closeout evidence.

The generator deliberately uses the public regulator-review, file-API, and
reporting APIs.  It creates only synthetic/offline evidence; live REP-01 and
REP-03 adapters, project hardware, and unresolved project sources remain
outside the claimed scope.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
REGULATOR_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "regulator_review"
INTEGRATION_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "integration"
OUTPUT_ROOT = REPO_ROOT / "evidence" / "REP02-D3"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from power_rail_validation import __version__ as PACKAGE_VERSION  # noqa: E402
from power_rail_validation.integration import (  # noqa: E402
    API_VERSION,
    DESCRIPTOR_SCHEMA_VERSION,
    ENGINE_VERSION as INTEGRATION_ENGINE_VERSION,
    descriptor_input_hash,
    validate_file_api_descriptor,
    validate_file_api_file,
)
from power_rail_validation.regulator_review import (  # noqa: E402
    ENGINE_VERSION as REGULATOR_ENGINE_VERSION,
    RESULT_SCHEMA_VERSION as REGULATOR_RESULT_SCHEMA_VERSION,
    RULE_SET_VERSION,
    review_regulator,
)
from power_rail_validation.reporting import (  # noqa: E402
    REPORTER_VERSION,
    REPORT_SCHEMA_VERSION,
    build_report_model,
    render_json_report,
    render_markdown_report,
)


EVIDENCE_SCHEMA_VERSION = "1.0.0"
PROVENANCE_SCHEMA_VERSION = "1.0.0"
STATUS_ORDER = ("BLOCKED", "FAIL", "UNKNOWN", "NOT_RUN", "PASS")
EXPECTED_REGULATOR_CASES = (
    "RR-SYNTHETIC-NOMINAL",
    "CURRENT_WORKSPACE_GAP_PROFILE",
    "TRANSIENT_PRESENT_STABILITY_MISSING",
    "VIN_RECOMMENDED_FAIL_ABS_PASS",
    "MISSING_TEMPERATURE",
    "CONFLICTING_EXACT_SOURCE",
    "MODEL_MISSING",
    "MODEL_OUT_OF_BOUNDS",
    "TRANSIENT_IS_NOT_STABILITY",
)
EXPECTED_INTEGRATION_CASES = (
    "REP01_VALID",
    "REP03_VALID",
    "MULTIPLE_PROFILES",
    "MISSING_ARTIFACT",
    "INCOMPATIBLE_API",
    "INCOMPATIBLE_SCHEMA",
    "MALFORMED_JSON",
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError("expected a JSON object: " + path.as_posix())
    return value


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _write_json(path: Path, value: Any) -> None:
    text = _json_text(value)
    decoded = json.loads(text)
    if decoded != value:
        raise RuntimeError("JSON round-trip changed output: " + path.as_posix())
    _write_text(path, text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _set_path(document: MutableMapping[str, Any], path: Sequence[Any], value: Any) -> None:
    if not path:
        raise RuntimeError("patch path must not be empty")
    cursor: Any = document
    for part in path[:-1]:
        if not isinstance(part, (str, int)):
            raise RuntimeError("patch path elements must be strings or integers")
        cursor = cursor[part]
    cursor[path[-1]] = copy.deepcopy(value)


def _patched(base: Mapping[str, Any], patches: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    document = copy.deepcopy(base)
    for patch in patches:
        path = patch.get("path")
        if not isinstance(path, list):
            raise RuntimeError("patch descriptor path must be an array")
        _set_path(document, path, patch.get("value"))
    return document


def _check(result: Mapping[str, Any], rule_id: str) -> Mapping[str, Any]:
    for item in result.get("checks", []):
        if item.get("rule_id") == rule_id:
            return item
    raise RuntimeError("regulator result is missing rule: " + rule_id)


def _issue_codes(result: Mapping[str, Any]) -> List[str]:
    return sorted({str(item["reason_code"]) for item in result.get("issues", [])})


def _status_counts(results: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    material = tuple(results)
    return {
        status: sum(item.get("status") == status for item in material)
        for status in STATUS_ORDER
    }


def _verify_live_adapters_not_pass(results: Iterable[Mapping[str, Any]]) -> None:
    for result in results:
        for adapter in result.get("adapter_results", []):
            if adapter.get("status") not in {"NOT_RUN", "BLOCKED"}:
                raise RuntimeError(
                    "live adapter status escaped fail-closed boundary: "
                    + str(adapter)
                )


def _regulator_case(
    case_id: str,
    fixture: str,
    document: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    input_snapshot = copy.deepcopy(document)
    result = review_regulator(document, artifact_root=REPO_ROOT)
    if document != input_snapshot:
        raise RuntimeError("regulator review mutated input for " + case_id)

    verification: Dict[str, Any] = {
        "expected_status": expected["status"],
        "observed_status": result["status"],
        "status_match": result["status"] == expected["status"],
    }
    rule_id = expected.get("rule_id")
    if rule_id is not None:
        rule_result = _check(result, str(rule_id))
        verification.update(
            {
                "expected_rule_id": rule_id,
                "expected_rule_status": expected["rule_status"],
                "observed_rule_status": rule_result["status"],
                "expected_reason_code": expected["reason_code"],
                "observed_reason_code": rule_result["reason_code"],
                "rule_status_match": rule_result["status"] == expected["rule_status"],
                "reason_code_match": rule_result["reason_code"] == expected["reason_code"],
            }
        )
    expectation_match = all(
        value is True for key, value in verification.items() if key.endswith("_match")
    )
    record = {
        "case_id": case_id,
        "fixture": fixture,
        "expectation_match": expectation_match,
        "verification": verification,
        "technical_result": result,
    }
    return record, result


def _regulator_suite() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    nominal_path = REGULATOR_FIXTURES / "nominal-synthetic.json"
    nominal = _load_json(nominal_path)
    nominal_snapshot = copy.deepcopy(nominal)
    records: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}

    record, result = _regulator_case(
        "RR-SYNTHETIC-NOMINAL",
        _relative(nominal_path),
        nominal,
        {"status": "PASS"},
    )
    records.append(record)
    by_id[record["case_id"]] = result

    descriptor_specs = (
        (
            "workspace-gap-profile.json",
            "CURRENT_WORKSPACE_GAP_PROFILE",
            {
                "status": "BLOCKED",
                "rule_id": "REP02.REGULATOR.MODEL.PROVENANCE",
                "rule_status": "BLOCKED",
                "reason_code": "MODEL_MISSING",
            },
        ),
        (
            "transient-present-stability-missing.json",
            "TRANSIENT_PRESENT_STABILITY_MISSING",
            {
                "status": "BLOCKED",
                "rule_id": "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE",
                "rule_status": "BLOCKED",
                "reason_code": "STABILITY_EVIDENCE_MISSING",
            },
        ),
    )
    for file_name, case_id, expected in descriptor_specs:
        descriptor_path = REGULATOR_FIXTURES / file_name
        descriptor = _load_json(descriptor_path)
        document = _patched(nominal, descriptor["patches"])
        record, result = _regulator_case(
            case_id,
            _relative(descriptor_path),
            document,
            expected,
        )
        record["descriptor_expectation"] = copy.deepcopy(descriptor.get("expected", {}))
        records.append(record)
        by_id[case_id] = result

    negative_path = REGULATOR_FIXTURES / "negative-cases.json"
    catalog = _load_json(negative_path)
    for case in catalog["cases"]:
        document = _patched(
            nominal,
            ({"path": case["path"], "value": case["value"]},),
        )
        case_id = str(case["case_id"])
        # Every negative fixture blocks the aggregate review.  Some targeted
        # rules intentionally remain FAIL or UNKNOWN; their downstream model
        # or evidence dependencies then fail closed rather than allowing a
        # partial project-level decision.
        expected_overall = "BLOCKED"
        record, result = _regulator_case(
            case_id,
            _relative(negative_path) + "#" + case_id,
            document,
            {
                "status": expected_overall,
                "rule_id": case["expected_rule"],
                "rule_status": case["expected_status"],
                "reason_code": case["expected_reason"],
            },
        )
        records.append(record)
        by_id[case_id] = result

    if nominal != nominal_snapshot:
        raise RuntimeError("fixture patching mutated nominal regulator input")
    if tuple(item["case_id"] for item in records) != EXPECTED_REGULATOR_CASES:
        raise RuntimeError("regulator closeout case inventory drifted")
    verification_pass = all(item["expectation_match"] for item in records)
    suite = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "suite_id": "REP02-D3-WF-02-05-REGULATOR-CLOSEOUT",
        "verification_status": "PASS" if verification_pass else "FAIL",
        "expectation_policy": (
            "Fixture expectation matching is verification metadata and never "
            "changes the technical result status."
        ),
        "package_version": PACKAGE_VERSION,
        "engine_version": REGULATOR_ENGINE_VERSION,
        "result_schema_version": REGULATOR_RESULT_SCHEMA_VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "case_count": len(records),
        "technical_status_counts": _status_counts(
            item["technical_result"] for item in records
        ),
        "scope_status": {
            "WF-02-05": "IMPLEMENTED_SYNTHETIC",
            "GAP-02": "OPEN",
            "GAP-03": "OPEN",
            "G1": "BLOCKED",
        },
        "cases": records,
        "claim_limits": [
            "SYNTHETIC_FIXTURES_ONLY",
            "SYNTHETIC_PASS_IS_NOT_PROJECT_OR_HARDWARE_PASS",
            "NO_LIVE_REP01_OR_REP03_INTEGRATION_CLAIM",
            "NO_PHYSICAL_HARDWARE_OR_LAB_VALIDATION",
            "G1_SOURCE_GATE_BLOCKED",
        ],
    }
    if suite["verification_status"] != "PASS":
        raise RuntimeError("regulator closeout expectations did not match")
    return suite, by_id


def _integration_case(
    case_id: str,
    fixture: str,
    result: Mapping[str, Any],
    expected_status: str,
    expected_codes: Sequence[str],
) -> Dict[str, Any]:
    codes = _issue_codes(result)
    expected_code_set = sorted(expected_codes)
    expectation_match = (
        result.get("status") == expected_status
        and all(code in codes for code in expected_code_set)
    )
    return {
        "case_id": case_id,
        "fixture": fixture,
        "expectation_match": expectation_match,
        "verification": {
            "expected_status": expected_status,
            "observed_status": result.get("status"),
            "required_reason_codes": expected_code_set,
            "observed_reason_codes": codes,
            "status_match": result.get("status") == expected_status,
            "required_reason_codes_present": all(
                code in codes for code in expected_code_set
            ),
        },
        "technical_result": copy.deepcopy(result),
    }


def _integration_suite() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    file_specs = (
        ("valid.json", "REP01_VALID", "PASS", ()),
        ("rep03-valid.json", "REP03_VALID", "PASS", ()),
        ("multiple-profiles.json", "MULTIPLE_PROFILES", "PASS", ()),
        (
            "missing-artifact.json",
            "MISSING_ARTIFACT",
            "NOT_RUN",
            ("EXTERNAL_ARTIFACT_NOT_PROVIDED", "EXTERNAL_SOURCE_NOT_PROVIDED"),
        ),
    )
    for file_name, case_id, expected_status, expected_codes in file_specs:
        fixture_path = INTEGRATION_FIXTURES / file_name
        result = validate_file_api_file(fixture_path)
        record = _integration_case(
            case_id,
            _relative(fixture_path),
            result,
            expected_status,
            expected_codes,
        )
        records.append(record)
        by_id[case_id] = copy.deepcopy(result)

    valid_path = INTEGRATION_FIXTURES / "valid.json"
    incompatible = _load_json(valid_path)
    incompatible_snapshot = copy.deepcopy(incompatible)
    incompatible["api_version"] = "2.0.0"
    incompatible["input_hash"] = descriptor_input_hash(incompatible)
    rebound_snapshot = copy.deepcopy(incompatible)
    incompatible_result = validate_file_api_descriptor(
        incompatible,
        artifact_root=INTEGRATION_FIXTURES,
    )
    if incompatible != rebound_snapshot:
        raise RuntimeError("file-API validation mutated incompatible descriptor")
    if incompatible_snapshot["input_hash"] == incompatible["input_hash"]:
        raise RuntimeError("incompatible API descriptor was not hash-rebound")
    record = _integration_case(
        "INCOMPATIBLE_API",
        _relative(valid_path) + "#api_version=2.0.0;input_hash=rebound",
        incompatible_result,
        "BLOCKED",
        ("API_VERSION_INCOMPATIBLE",),
    )
    records.append(record)
    by_id[record["case_id"]] = copy.deepcopy(incompatible_result)

    incompatible_schema = _load_json(valid_path)
    incompatible_schema_snapshot = copy.deepcopy(incompatible_schema)
    incompatible_schema["schema_version"] = "2.0.0"
    incompatible_schema["input_hash"] = descriptor_input_hash(
        incompatible_schema
    )
    rebound_schema_snapshot = copy.deepcopy(incompatible_schema)
    incompatible_schema_result = validate_file_api_descriptor(
        incompatible_schema,
        artifact_root=INTEGRATION_FIXTURES,
    )
    if incompatible_schema != rebound_schema_snapshot:
        raise RuntimeError("file-API validation mutated incompatible schema descriptor")
    if incompatible_schema_snapshot["input_hash"] == incompatible_schema["input_hash"]:
        raise RuntimeError("incompatible schema descriptor was not hash-rebound")
    record = _integration_case(
        "INCOMPATIBLE_SCHEMA",
        _relative(valid_path) + "#schema_version=2.0.0;input_hash=rebound",
        incompatible_schema_result,
        "BLOCKED",
        ("SCHEMA_VERSION_INCOMPATIBLE",),
    )
    records.append(record)
    by_id[record["case_id"]] = copy.deepcopy(incompatible_schema_result)

    malformed_path = INTEGRATION_FIXTURES / "malformed.json"
    malformed_result = validate_file_api_file(malformed_path)
    record = _integration_case(
        "MALFORMED_JSON",
        _relative(malformed_path),
        malformed_result,
        "BLOCKED",
        ("MALFORMED_JSON",),
    )
    records.append(record)
    by_id[record["case_id"]] = copy.deepcopy(malformed_result)

    if tuple(item["case_id"] for item in records) != EXPECTED_INTEGRATION_CASES:
        raise RuntimeError("integration closeout case inventory drifted")
    _verify_live_adapters_not_pass(item["technical_result"] for item in records)
    verification_pass = all(item["expectation_match"] for item in records)
    suite = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "suite_id": "REP02-D3-WF-02-06-OFFLINE-INTEGRATION-CLOSEOUT",
        "verification_status": "PASS" if verification_pass else "FAIL",
        "expectation_policy": (
            "Fixture expectation matching is verification metadata and never "
            "changes the technical result status."
        ),
        "package_version": PACKAGE_VERSION,
        "api_version": API_VERSION,
        "descriptor_schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "engine_version": INTEGRATION_ENGINE_VERSION,
        "case_count": len(records),
        "technical_status_counts": _status_counts(
            item["technical_result"] for item in records
        ),
        "scope_status": {
            "WF-02-06_OFFLINE_FILE_API": "IMPLEMENTED_SYNTHETIC",
            "REP-01_LIVE_ADAPTER": "NOT_RUN",
            "REP-03_LIVE_ADAPTER": "NOT_RUN",
        },
        "cases": records,
        "claim_limits": [
            "DESCRIPTOR_STRUCTURE_AND_FILE_INTEGRITY_ONLY",
            "SYNTHETIC_FIXTURES_ONLY",
            "LIVE_ADAPTERS_NOT_RUN",
            "NO_REP01_OR_REP03_SEMANTIC_MAPPING_CLAIM",
            "NO_HARDWARE_COMPLIANCE_CLAIM",
        ],
    }
    if suite["verification_status"] != "PASS":
        raise RuntimeError("integration closeout expectations did not match")
    return suite, by_id


def _source_catalog(nominal: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    candidates = list(nominal.get("sources", []))
    rail_input = nominal.get("rail_budget_input", {})
    if isinstance(rail_input, dict):
        candidates.extend(rail_input.get("sources", []))
    for source in candidates:
        source_id = str(source["source_id"])
        material = copy.deepcopy(source)
        previous = records.get(source_id)
        if previous is None:
            records[source_id] = material
            continue
        binding_fields = ("version", "locator", "access_status", "content_hash")
        if any(previous.get(key) != material.get(key) for key in binding_fields):
            raise RuntimeError("conflicting fixture source record: " + source_id)
        # The outer regulator catalog may carry richer role/manufacturer
        # metadata than the rail-budget subdocument.  Keep that richer record
        # after proving both views bind the same bytes and version.
        if len(material) > len(previous):
            records[source_id] = material
    return records


def _provenance_register() -> Dict[str, Any]:
    nominal = _load_json(REGULATOR_FIXTURES / "nominal-synthetic.json")
    catalog = _source_catalog(nominal)
    synthetic_records: List[Dict[str, Any]] = []
    for source_id in sorted(catalog):
        source = catalog[source_id]
        locator = source.get("locator")
        if not isinstance(locator, str):
            raise RuntimeError("synthetic source has no locator: " + source_id)
        source_path = REPO_ROOT / Path(locator)
        declared_hash = source.get("content_hash", {}).get("value")
        observed_hash = _sha256(source_path)
        if declared_hash != observed_hash:
            raise RuntimeError("synthetic source hash mismatch: " + source_id)
        source_role = source.get("source_role")
        if source_id == "SRC-RR-MODEL":
            record_kind = "SYNTHETIC_MODEL"
        elif source_role in {"TRANSIENT_RESULT", "STABILITY_RESULT"}:
            record_kind = "SYNTHETIC_RESULT_EVIDENCE"
        else:
            record_kind = "SYNTHETIC_SOURCE"
        synthetic_records.append(
            {
                "record_id": source_id,
                "record_kind": record_kind,
                "access_status": "AVAILABLE",
                "path": _relative(source_path),
                "version": source["version"],
                "sha256": observed_hash,
                "hash_verification": "PASS",
                "classification": source.get("classification"),
                "source_role": source_role,
                "manufacturer": source.get("manufacturer"),
                "part_number": source.get("part_number"),
                "document_revision": source.get("document_revision"),
                "page_section": source.get("page_section"),
                "license_status": source.get("license_status"),
                "claim_scope": "SYNTHETIC_FIXTURE_ONLY",
            }
        )

    required_ids = {
        "SRC-RR-DATASHEET",
        "SRC-RR-MODEL",
        "SRC-RR-RAIL-FIXTURE",
        "SRC-RR-STABILITY",
        "SRC-RR-TRANSIENT",
    }
    observed_ids = {item["record_id"] for item in synthetic_records}
    if observed_ids != required_ids:
        raise RuntimeError(
            "synthetic provenance inventory drifted: "
            + ", ".join(sorted(observed_ids))
        )

    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "register_id": "REP02-D3-SOURCE-MODEL-PROVENANCE",
        "status": "BLOCKED",
        "reason_code": "PROJECT_SOURCE_AND_MODEL_REQUIREMENTS_OPEN",
        "hash_policy": {
            "algorithm": "SHA-256",
            "scope": "EXACT_FILE_BYTES",
            "missing_record_policy": "NO_SYNTHESIZED_PATH_VERSION_OR_HASH",
        },
        "synthetic_records": synthetic_records,
        "project_requirements": [
            {
                "requirement_id": "PROJECT-EXACT-PART-REGULATOR-SOURCE",
                "record_kind": "PROJECT_SOURCE",
                "gap_id": "GAP-02",
                "gap_status": "OPEN",
                "access_status": "MISSING",
                "path": None,
                "version": None,
                "sha256": None,
                "reason_code": "EXACT_PART_SOURCE_NOT_PROVIDED",
                "gate_id": "G1",
                "gate_status": "BLOCKED",
            },
            {
                "requirement_id": "PROJECT-EXACT-PART-REGULATOR-MODEL",
                "record_kind": "PROJECT_MODEL",
                "gap_id": "GAP-02",
                "gap_status": "OPEN",
                "access_status": "MISSING",
                "path": None,
                "version": None,
                "sha256": None,
                "reason_code": "EXACT_PART_MODEL_NOT_PROVIDED",
                "gate_id": "G1",
                "gate_status": "BLOCKED",
            },
            {
                "requirement_id": "PROJECT-SEQUENCING-TIMING-SOURCE",
                "record_kind": "PROJECT_SOURCE",
                "gap_id": "GAP-03",
                "gap_status": "OPEN",
                "access_status": "MISSING",
                "path": None,
                "version": None,
                "sha256": None,
                "reason_code": "MANUFACTURER_TIMING_SOURCE_NOT_PROVIDED",
                "gate_id": "G1",
                "gate_status": "BLOCKED",
            },
        ],
        "gap_status": {"GAP-02": "OPEN", "GAP-03": "OPEN"},
        "hardware_gate_status": {
            "G1": "BLOCKED",
            "G2": "NOT_OPENED",
            "G3": "NOT_OPENED",
            "G4": "NOT_OPENED",
            "G5": "NOT_OPENED",
            "G6": "NOT_OPENED",
        },
        "claim_limits": [
            "SYNTHETIC_SOURCE_AND_MODEL_BYTES_ONLY",
            "NO_PROJECT_EXACT_PART_SOURCE_HASH",
            "NO_PROJECT_EXACT_PART_MODEL_HASH",
            "NO_G1_TO_G6_HARDWARE_GATE_APPROVAL",
        ],
    }


def _report_model(
    regulator_results: Mapping[str, Mapping[str, Any]],
    integration_results: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    components = [
        copy.deepcopy(regulator_results["RR-SYNTHETIC-NOMINAL"]),
        copy.deepcopy(regulator_results["CURRENT_WORKSPACE_GAP_PROFILE"]),
        copy.deepcopy(integration_results["REP01_VALID"]),
        copy.deepcopy(integration_results["MISSING_ARTIFACT"]),
    ]
    _verify_live_adapters_not_pass(components)
    model = build_report_model(components)
    if model["schema_version"] != REPORT_SCHEMA_VERSION:
        raise RuntimeError("report schema version drifted")
    for adapter in model["adapter_limitations"]:
        if adapter["status"] not in {"NOT_RUN", "BLOCKED"}:
            raise RuntimeError("common reporter promoted a live adapter")
    return model


def _build_outputs() -> Dict[str, Any]:
    regulator_suite, regulator_results = _regulator_suite()
    integration_suite, integration_results = _integration_suite()
    provenance = _provenance_register()
    report_model = _report_model(regulator_results, integration_results)
    report_json = render_json_report(report_model)
    report_markdown = render_markdown_report(report_model)
    if json.loads(report_json) != report_model:
        raise RuntimeError("JSON reporter output differs from the report model")
    if not report_markdown.endswith("\n"):
        raise RuntimeError("Markdown reporter output has no final newline")
    return {
        "regulator-review-results.json": regulator_suite,
        "integration-results.json": integration_suite,
        "source-model-provenance-register.json": provenance,
        "report-model-closeout.json": report_model,
        "report-closeout.md": report_markdown,
    }


def main() -> int:
    first = _build_outputs()
    second = _build_outputs()
    if first != second:
        raise RuntimeError("D3 closeout generation is not deterministic")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for name, value in first.items():
        path = OUTPUT_ROOT / name
        if name.endswith(".json"):
            _write_json(path, value)
        else:
            _write_text(path, value)

    summary = {
        "generator": _relative(Path(__file__)),
        "package_version": PACKAGE_VERSION,
        "reporter_version": REPORTER_VERSION,
        "output_sha256": {
            name: _sha256(OUTPUT_ROOT / name) for name in sorted(first)
        },
    }
    sys.stdout.write(_json_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
