"""Deterministic report-model, renderer, and evidence-manifest support.

This is the first WF-02-06 slice.  It accepts already-produced component
results, binds every normalized finding to its technical input and evidence,
and renders JSON and Markdown from one report model.  It does not call live
REP-01/REP-03 services and it does not coerce module-local results into the D1
finding schema.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest


REPORTER_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = "1.0.0"
MANIFEST_SCHEMA_VERSION = "1.0.0"

_STATUSES = ("BLOCKED", "FAIL", "UNKNOWN", "NOT_RUN", "PASS")
_STATUS_RANK = {status: index for index, status in enumerate(reversed(_STATUSES))}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_FLOATING_VERSION_TOKENS = frozenset(
    {
        "current",
        "develop",
        "development",
        "head",
        "latest",
        "main",
        "master",
        "nightly",
        "stable",
        "tbd",
        "tip",
        "trunk",
        "unknown",
        "unspecified",
    }
)
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "reporter_version",
        "package_version",
        "generated_at",
        "status",
        "status_counts",
        "component_results",
        "findings",
        "adapter_limitations",
        "limitations",
        "hash_policy",
        "report_model_hash",
    }
)
_FINDING_FIELDS = frozenset(
    {
        "finding_id",
        "finding_kind",
        "technical_locator",
        "rule_id",
        "status",
        "reason_code",
        "message",
        "source_refs",
        "input_hash",
        "tool_name",
        "tool_version",
        "package_version",
        "technical_result_hash",
    }
)
_SOURCE_REF_FIELDS = frozenset({"source_id", "version", "sha256"})
_COMPONENT_FIELDS = frozenset(
    {
        "component_id",
        "rule_id",
        "status",
        "input_hash",
        "tool_name",
        "tool_version",
        "package_version",
        "technical_result_hash",
    }
)
_ADAPTER_FIELDS = frozenset({"adapter_id", "status", "reason_code", "message"})
_HASH_POLICY_FIELDS = frozenset(
    {"algorithm", "canonicalization", "technical_result_scope", "report_model_scope"}
)
_ISSUE_FIELDS = frozenset(
    {
        "finding_id",
        "fault_id",
        "severity",
        "status",
        "code",
        "reason_code",
        "path",
        "message",
        "rule_id",
        "source_refs",
    }
)
_MANIFEST_CATEGORIES = ("inputs", "sources", "models", "tools", "outputs")
_MANIFEST_ENTRY_FIELDS = frozenset({"id", "path", "sha256", "version"})
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "reporter_version",
        "generated_at",
        "inputs",
        "sources",
        "models",
        "tools",
        "outputs",
        "hash_policy",
        "manifest_hash",
    }
)


class ReportValidationError(ValueError):
    """Raised when a report or manifest cannot be produced safely."""


def _fail(path: str, message: str) -> None:
    raise ReportValidationError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "expected an object")
    return value


def _sequence(value: Any, path: str, *, nonempty: bool = False) -> Sequence[Any]:
    if not isinstance(value, list):
        _fail(path, "expected an array")
    if nonempty and not value:
        _fail(path, "array must not be empty")
    return value


def _closed(value: Mapping[str, Any], allowed: Iterable[str], path: str) -> None:
    unexpected = sorted((key for key in value if key not in set(allowed)), key=repr)
    if unexpected:
        _fail(path, f"unexpected field {unexpected[0]!r}")


def _required(value: Mapping[str, Any], fields: Iterable[str], path: str) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        _fail(path, f"missing required field {missing[0]!r}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "expected a non-empty string")
    return value


def _identifier(value: Any, path: str) -> str:
    text = _text(value, path)
    if _ID_RE.fullmatch(text) is None:
        _fail(path, "expected a stable identifier")
    return text


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(path, "expected a lowercase 64-character SHA-256 digest")
    return value


def _status(value: Any, path: str) -> str:
    if not isinstance(value, str) or value not in _STATUSES:
        _fail(path, "expected BLOCKED, FAIL, UNKNOWN, NOT_RUN, or PASS")
    return value


def _is_floating_version(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _FLOATING_VERSION_TOKENS:
        return True
    if any(marker in normalized for marker in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    parts = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    if any(part in {"branch", "branches", "heads"} for part in parts):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _version(value: Any, path: str) -> str:
    text = _text(value, path)
    if text != text.strip():
        _fail(path, "version must not contain leading or trailing whitespace")
    if _is_floating_version(text):
        _fail(path, "version must be pinned rather than a moving alias or range")
    return text


def _timestamp(value: Optional[str], path: str) -> Optional[str]:
    if value is None:
        return None
    text = _text(value, path)
    if _RFC3339_RE.fullmatch(text) is None:
        _fail(path, "expected an RFC 3339 timestamp with timezone")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        _fail(path, "expected an RFC 3339 timestamp with timezone")
    if parsed.tzinfo is None:
        _fail(path, "timestamp must include a timezone")
    return text


def _ensure_json_value(value: Any, path: str = "$") -> None:
    """Reject cycles, non-finite values, non-string keys, and non-JSON types."""

    active: set[int] = set()

    def visit(item: Any, item_path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                _fail(item_path, "non-finite numbers are not valid report data")
            return
        if isinstance(item, (list, dict)):
            identity = id(item)
            if identity in active:
                _fail(item_path, "cyclic data is not valid report data")
            active.add(identity)
            try:
                if isinstance(item, list):
                    for index, child in enumerate(item):
                        visit(child, f"{item_path}[{index}]")
                else:
                    for key, child in item.items():
                        if not isinstance(key, str):
                            _fail(item_path, "JSON object keys must be strings")
                        visit(child, f"{item_path}.{key}")
            finally:
                active.remove(identity)
            return
        _fail(item_path, f"unsupported JSON type {type(item).__name__}")

    visit(value, path)


def _hash_value(value: Any, path: str) -> str:
    try:
        _ensure_json_value(value, path)
        return sha256_hexdigest(value)
    except ReportValidationError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        _fail(path, f"cannot be canonicalized: {type(exc).__name__}")
    raise AssertionError("unreachable")


def _content_hash(value: Any, path: str) -> str:
    if isinstance(value, str):
        return _sha256(value, path)
    item = _mapping(value, path)
    _closed(item, {"state", "algorithm", "value"}, path)
    _required(item, {"state", "algorithm", "value"}, path)
    if item["state"] != "KNOWN" or item["algorithm"] != "SHA-256":
        _fail(path, "source content hash must be KNOWN SHA-256")
    return _sha256(item["value"], f"{path}.value")


def _catalog_sources(result: Mapping[str, Any], path: str) -> Dict[str, Tuple[str, str]]:
    candidates: List[Tuple[Any, str]] = []
    if "sources" in result:
        candidates.append((result["sources"], f"{path}.sources"))
    calculation_inputs = result.get("calculation_inputs")
    if isinstance(calculation_inputs, dict) and "sources" in calculation_inputs:
        candidates.append((calculation_inputs["sources"], f"{path}.calculation_inputs.sources"))

    catalog: Dict[str, Tuple[str, str]] = {}
    for candidate, candidate_path in candidates:
        for index, source_value in enumerate(_sequence(candidate, candidate_path)):
            source_path = f"{candidate_path}[{index}]"
            source = _mapping(source_value, source_path)
            source_id = _identifier(source.get("source_id"), f"{source_path}.source_id")
            version = _version(source.get("version"), f"{source_path}.version")
            digest = _content_hash(source.get("content_hash"), f"{source_path}.content_hash")
            previous = catalog.get(source_id)
            if previous is not None:
                qualifier = "conflicts with" if previous != (version, digest) else "duplicates"
                _fail(source_path, f"source {source_id!r} {qualifier} another catalog record")
            catalog[source_id] = (version, digest)
    return catalog


def _normalize_source_refs(
    value: Any,
    catalog: Mapping[str, Tuple[str, str]],
    path: str,
) -> List[Dict[str, str]]:
    refs = _sequence(value, path, nonempty=True)
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for index, ref_value in enumerate(refs):
        ref_path = f"{path}[{index}]"
        if isinstance(ref_value, str):
            source_id = _identifier(ref_value, ref_path)
            catalog_value = catalog.get(source_id)
            if catalog_value is None:
                _fail(ref_path, "source identifier does not resolve to the embedded source catalog")
            ref: Mapping[str, Any] = {
                "source_id": source_id,
                "version": catalog_value[0],
                "sha256": catalog_value[1],
            }
        else:
            ref = _mapping(ref_value, ref_path)
        allowed = {"source_id", "version", "content_hash", "sha256"}
        _closed(ref, allowed, ref_path)
        _required(ref, {"source_id", "version"}, ref_path)
        source_id = _identifier(ref["source_id"], f"{ref_path}.source_id")
        version = _version(ref["version"], f"{ref_path}.version")
        if source_id in seen:
            _fail(ref_path, f"duplicate source reference {source_id!r}")
        seen.add(source_id)

        direct_hashes: List[str] = []
        if "content_hash" in ref:
            direct_hashes.append(_content_hash(ref["content_hash"], f"{ref_path}.content_hash"))
        if "sha256" in ref:
            direct_hashes.append(_sha256(ref["sha256"], f"{ref_path}.sha256"))
        if len(set(direct_hashes)) > 1:
            _fail(ref_path, "content_hash and sha256 disagree")

        catalog_value = catalog.get(source_id)
        if catalog_value is not None and catalog_value[0] != version:
            _fail(ref_path, "source version conflicts with the embedded source catalog")
        digest = direct_hashes[0] if direct_hashes else (catalog_value[1] if catalog_value else None)
        if digest is None:
            _fail(ref_path, "source reference has no verifiable content hash")
        if catalog_value is not None and catalog_value[1] != digest:
            _fail(ref_path, "source hash conflicts with the embedded source catalog")
        normalized.append({"source_id": source_id, "version": version, "sha256": digest})
    return sorted(normalized, key=lambda item: (item["source_id"], item["version"], item["sha256"]))


def _finding_id(payload: Mapping[str, Any]) -> str:
    return "RPT-" + sha256_hexdigest(payload)[:24].upper()


def _component_id(result: Mapping[str, Any], rule_id: str, input_hash: str) -> str:
    if "component_id" in result:
        return _identifier(result["component_id"], "$.component_id")
    return f"{rule_id}:{input_hash[:16]}"


def _component_rule_id(result: Mapping[str, Any], path: str) -> str:
    """Select an explicit rule, or the reviewed sequencing adapter rule.

    The WF-02-03 result envelope predates reporting and identifies its rule set
    and FSM but has no single top-level rule_id.  The reporter assigns a
    module-local summary rule only when both versioned identifiers are present;
    individual sequence issues may still carry their own rule_id.  This is not
    a conversion to the D1 finding schema.
    """

    if "rule_id" in result:
        return _identifier(result["rule_id"], f"{path}.rule_id")
    if "engine_version" in result and "rule_set_version" in result and "fault_results" in result:
        _version(result["engine_version"], f"{path}.engine_version")
        _version(result["rule_set_version"], f"{path}.rule_set_version")
        return "REP02.FAULT_INJECTION.CAMPAIGN"
    if "fsm_version" in result and "rule_set_version" in result:
        _version(result["fsm_version"], f"{path}.fsm_version")
        _version(result["rule_set_version"], f"{path}.rule_set_version")
        return "REP02.SEQUENCING.FSM"
    _fail(path, "missing required field 'rule_id'")
    raise AssertionError("unreachable")


def _normalize_component(result_value: Any, path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    result = _mapping(result_value, path)
    if "generated_at" in result:
        _fail(f"{path}.generated_at", "supply generated_at explicitly to build_report_model")
    technical_result_hash = _hash_value(result, path)
    required = {
        "status",
        "reason_code",
        "input_hash",
        "tool_name",
        "package_version",
    }
    _required(result, required, path)
    rule_id = _component_rule_id(result, path)
    technical_status = _status(result["status"], f"{path}.status")
    reason_code = _identifier(result["reason_code"], f"{path}.reason_code")
    input_hash = _sha256(result["input_hash"], f"{path}.input_hash")
    tool_name = _identifier(result["tool_name"], f"{path}.tool_name")
    tool_version_value = result.get("tool_version", result.get("engine_version"))
    if tool_version_value is None:
        _fail(path, "missing required field 'tool_version' or 'engine_version'")
    tool_version = _version(tool_version_value, f"{path}.tool_version")
    package_version = _version(result["package_version"], f"{path}.package_version")
    catalog = _catalog_sources(result, path)
    source_refs_value = result["source_refs"] if "source_refs" in result else sorted(catalog)
    source_refs = _normalize_source_refs(source_refs_value, catalog, f"{path}.source_refs")

    shared = {
        "rule_id": rule_id,
        "source_refs": source_refs,
        "input_hash": input_hash,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "package_version": package_version,
        "technical_result_hash": technical_result_hash,
    }
    summary_payload: Dict[str, Any] = {
        **shared,
        "finding_kind": "COMPONENT_RESULT",
        "technical_locator": "$",
        "status": technical_status,
        "reason_code": reason_code,
        "message": f"{rule_id} returned {technical_status}: {reason_code}.",
    }
    summary = {"finding_id": _finding_id(summary_payload), **summary_payload}
    findings = [summary]

    issue_collections = []
    if "issues" in result:
        issue_collections.append(("issues", result["issues"]))
    if "findings" in result:
        issue_collections.append(("findings", result["findings"]))
    for collection_name, collection_value in issue_collections:
        for index, issue_value in enumerate(_sequence(collection_value, f"{path}.{collection_name}")):
            issue_path = f"{path}.{collection_name}[{index}]"
            issue = _mapping(issue_value, issue_path)
            _closed(issue, _ISSUE_FIELDS, issue_path)
            issue_status_value = issue.get("status", issue.get("severity"))
            issue_reason_value = issue.get("reason_code", issue.get("code"))
            if issue_status_value is None or issue_reason_value is None:
                _fail(issue_path, "issue requires status/severity and reason_code/code")
            issue_status = _status(issue_status_value, f"{issue_path}.status")
            issue_reason = _identifier(issue_reason_value, f"{issue_path}.reason_code")
            if "status" in issue and "severity" in issue and issue["status"] != issue["severity"]:
                _fail(issue_path, "status and severity disagree")
            if "reason_code" in issue and "code" in issue and issue["reason_code"] != issue["code"]:
                _fail(issue_path, "reason_code and code disagree")
            issue_rule = _identifier(issue.get("rule_id", rule_id), f"{issue_path}.rule_id")
            message = _text(issue.get("message"), f"{issue_path}.message")
            issue_refs = source_refs
            if "source_refs" in issue:
                issue_refs = _normalize_source_refs(issue["source_refs"], catalog, f"{issue_path}.source_refs")
            issue_payload: Dict[str, Any] = {
                **shared,
                "finding_kind": "COMPONENT_ISSUE",
                "technical_locator": f"$.{collection_name}[{index}]",
                "rule_id": issue_rule,
                "status": issue_status,
                "reason_code": issue_reason,
                "message": message,
                "source_refs": issue_refs,
            }
            findings.append({"finding_id": _finding_id(issue_payload), **issue_payload})

    limitations: List[str] = []
    for index, limitation in enumerate(_sequence(result.get("limitations", []), f"{path}.limitations")):
        limitations.append(_text(limitation, f"{path}.limitations[{index}]"))

    component = {
        "component_id": _component_id(result, rule_id, input_hash),
        "rule_id": rule_id,
        "status": technical_status,
        "input_hash": input_hash,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "package_version": package_version,
        "technical_result_hash": technical_result_hash,
    }
    return component, findings, limitations


def _overall_status(statuses: Iterable[str]) -> str:
    material = tuple(statuses)
    if not material:
        _fail("$.findings", "at least one finding is required")
    return max(material, key=lambda item: _STATUS_RANK[item])


def _report_hash_payload(model: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in model.items() if key not in {"generated_at", "report_model_hash"}}


def build_report_model(
    component_results: Union[Mapping[str, Any], Sequence[Mapping[str, Any]]],
    *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize one component result or a non-empty list into a report model.

    ``generated_at`` is optional, must be explicitly supplied, and is excluded
    from both technical-result and report-model hashes.  The caller's values
    are never modified.
    """

    generated = _timestamp(generated_at, "$.generated_at")
    if isinstance(component_results, dict):
        inputs: Sequence[Any] = [component_results]
    elif isinstance(component_results, list):
        inputs = _sequence(component_results, "$", nonempty=True)
    else:
        _fail("$", "expected a component result object or a non-empty array")

    components: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    limitations: set[str] = {
        "LIVE_REP01_REP03_ADAPTERS_NOT_IMPLEMENTED",
        "MODULE_LOCAL_RESULTS_ARE_NOT_COERCED_TO_D1_FINDINGS",
    }
    for index, value in enumerate(inputs):
        component, component_findings, component_limitations = _normalize_component(value, f"$[{index}]")
        components.append(component)
        findings.extend(component_findings)
        limitations.update(component_limitations)

    components.sort(key=lambda item: (item["component_id"], item["technical_result_hash"]))
    if len({item["component_id"] for item in components}) != len(components):
        _fail("$.component_results", "duplicate component_id; values must be unique")
    if len({item["technical_result_hash"] for item in components}) != len(components):
        _fail("$.component_results", "duplicate technical component result")
    findings.sort(
        key=lambda item: (
            item["rule_id"],
            _STATUS_RANK[item["status"]],
            item["reason_code"],
            item["finding_kind"],
            item["finding_id"],
        )
    )
    if len({item["finding_id"] for item in findings}) != len(findings):
        _fail("$.findings", "duplicate normalized finding")

    counts = {status: sum(item["status"] == status for item in findings) for status in _STATUSES}
    model: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "reporter_version": REPORTER_VERSION,
        "package_version": PACKAGE_VERSION,
        "status": _overall_status(item["status"] for item in findings),
        "status_counts": counts,
        "component_results": components,
        "findings": findings,
        "adapter_limitations": [
            {
                "adapter_id": "REP-01",
                "status": "NOT_RUN",
                "reason_code": "LIVE_ADAPTER_NOT_IMPLEMENTED",
                "message": "Live REP-01 integration was not executed in this reporting slice.",
            },
            {
                "adapter_id": "REP-03",
                "status": "NOT_RUN",
                "reason_code": "LIVE_ADAPTER_NOT_IMPLEMENTED",
                "message": "Live REP-03 integration was not executed in this reporting slice.",
            },
        ],
        "limitations": sorted(limitations),
        "hash_policy": {
            "algorithm": "SHA-256",
            "canonicalization": "REP02_SORTED_MINIFIED_UTF8_JSON",
            "technical_result_scope": "EXACT_COMPONENT_RESULT_EXCLUDING_EXPLICIT_REPORT_GENERATED_AT",
            "report_model_scope": "ALL_REPORT_MODEL_FIELDS_EXCEPT_GENERATED_AT_AND_REPORT_MODEL_HASH",
        },
    }
    if generated is not None:
        model["generated_at"] = generated
    model["report_model_hash"] = sha256_hexdigest(_report_hash_payload(model))
    _validate_report_model(model)
    return copy.deepcopy(model)


def _validate_source_ref(value: Any, path: str) -> None:
    source = _mapping(value, path)
    _closed(source, _SOURCE_REF_FIELDS, path)
    _required(source, _SOURCE_REF_FIELDS, path)
    _identifier(source["source_id"], f"{path}.source_id")
    _version(source["version"], f"{path}.version")
    _sha256(source["sha256"], f"{path}.sha256")


def _expected_finding_order(item: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (
        item["rule_id"],
        _STATUS_RANK[item["status"]],
        item["reason_code"],
        item["finding_kind"],
        item["finding_id"],
    )


def _validate_report_model(model_value: Any) -> Mapping[str, Any]:
    model = _mapping(model_value, "$")
    _closed(model, _REPORT_FIELDS, "$")
    required = _REPORT_FIELDS - {"generated_at"}
    _required(model, required, "$")
    if model["schema_version"] != REPORT_SCHEMA_VERSION:
        _fail("$.schema_version", "unsupported report schema version")
    if model["reporter_version"] != REPORTER_VERSION:
        _fail("$.reporter_version", "reporter version mismatch")
    if _version(model["package_version"], "$.package_version") != PACKAGE_VERSION:
        _fail("$.package_version", "report model package version mismatch")
    _status(model["status"], "$.status")
    if "generated_at" in model:
        _timestamp(model["generated_at"], "$.generated_at")

    findings = _sequence(model["findings"], "$.findings", nonempty=True)
    finding_ids: set[str] = set()
    for index, value in enumerate(findings):
        path = f"$.findings[{index}]"
        finding = _mapping(value, path)
        _closed(finding, _FINDING_FIELDS, path)
        _required(finding, _FINDING_FIELDS, path)
        finding_id = _identifier(finding["finding_id"], f"{path}.finding_id")
        if finding_id in finding_ids:
            _fail(path, "duplicate finding_id")
        finding_ids.add(finding_id)
        if finding["finding_kind"] not in {"COMPONENT_RESULT", "COMPONENT_ISSUE"}:
            _fail(f"{path}.finding_kind", "unrecognized finding kind")
        _text(finding["technical_locator"], f"{path}.technical_locator")
        _identifier(finding["rule_id"], f"{path}.rule_id")
        _status(finding["status"], f"{path}.status")
        _identifier(finding["reason_code"], f"{path}.reason_code")
        _text(finding["message"], f"{path}.message")
        refs = _sequence(finding["source_refs"], f"{path}.source_refs", nonempty=True)
        ref_ids: set[str] = set()
        for ref_index, ref in enumerate(refs):
            _validate_source_ref(ref, f"{path}.source_refs[{ref_index}]")
            source_id = ref["source_id"]
            if source_id in ref_ids:
                _fail(f"{path}.source_refs[{ref_index}]", "duplicate source_id")
            ref_ids.add(source_id)
        if list(refs) != sorted(refs, key=lambda item: (item["source_id"], item["version"], item["sha256"])):
            _fail(f"{path}.source_refs", "source references are not in canonical order")
        _sha256(finding["input_hash"], f"{path}.input_hash")
        _identifier(finding["tool_name"], f"{path}.tool_name")
        _version(finding["tool_version"], f"{path}.tool_version")
        _version(finding["package_version"], f"{path}.package_version")
        _sha256(finding["technical_result_hash"], f"{path}.technical_result_hash")
        if finding_id != _finding_id({key: item for key, item in finding.items() if key != "finding_id"}):
            _fail(f"{path}.finding_id", "finding_id does not match finding content")

    if list(findings) != sorted(findings, key=_expected_finding_order):
        _fail("$.findings", "findings are not in canonical order")

    components = _sequence(model["component_results"], "$.component_results", nonempty=True)
    component_ids: set[str] = set()
    component_hashes: set[str] = set()
    component_by_hash: Dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(components):
        path = f"$.component_results[{index}]"
        component = _mapping(value, path)
        _closed(component, _COMPONENT_FIELDS, path)
        _required(component, _COMPONENT_FIELDS, path)
        component_id = _identifier(component["component_id"], f"{path}.component_id")
        if component_id in component_ids:
            _fail(path, "duplicate component_id")
        component_ids.add(component_id)
        _identifier(component["rule_id"], f"{path}.rule_id")
        _status(component["status"], f"{path}.status")
        _sha256(component["input_hash"], f"{path}.input_hash")
        _identifier(component["tool_name"], f"{path}.tool_name")
        _version(component["tool_version"], f"{path}.tool_version")
        _version(component["package_version"], f"{path}.package_version")
        technical_hash = _sha256(component["technical_result_hash"], f"{path}.technical_result_hash")
        if technical_hash in component_hashes:
            _fail(path, "duplicate technical_result_hash")
        component_hashes.add(technical_hash)
        component_by_hash[technical_hash] = component

    if list(components) != sorted(
        components, key=lambda item: (item["component_id"], item["technical_result_hash"])
    ):
        _fail("$.component_results", "components are not in canonical order")

    summary_hashes: set[str] = set()
    for index, finding in enumerate(findings):
        path = f"$.findings[{index}]"
        component = component_by_hash.get(finding["technical_result_hash"])
        if component is None:
            _fail(f"{path}.technical_result_hash", "does not resolve to a component result")
        for field in ("input_hash", "tool_name", "tool_version", "package_version"):
            if finding[field] != component[field]:
                _fail(f"{path}.{field}", "does not match the bound component result")
        if finding["finding_kind"] == "COMPONENT_RESULT":
            technical_hash = finding["technical_result_hash"]
            if technical_hash in summary_hashes:
                _fail(path, "duplicate component summary finding")
            summary_hashes.add(technical_hash)
            if finding["rule_id"] != component["rule_id"] or finding["status"] != component["status"]:
                _fail(path, "component summary rule or status does not match technical result")
    if summary_hashes != component_hashes:
        _fail("$.findings", "every component requires exactly one summary finding")

    counts = _mapping(model["status_counts"], "$.status_counts")
    _closed(counts, _STATUSES, "$.status_counts")
    _required(counts, _STATUSES, "$.status_counts")
    for status_name, count in counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            _fail(f"$.status_counts.{status_name}", "count must be a non-negative integer")
    expected_counts = {status: sum(item["status"] == status for item in findings) for status in _STATUSES}
    if counts != expected_counts:
        _fail("$.status_counts", "counts do not match findings")
    if model["status"] != _overall_status(item["status"] for item in findings):
        _fail("$.status", "status does not match finding precedence")

    adapters = _sequence(model["adapter_limitations"], "$.adapter_limitations", nonempty=True)
    adapter_ids: set[str] = set()
    for index, value in enumerate(adapters):
        path = f"$.adapter_limitations[{index}]"
        adapter = _mapping(value, path)
        _closed(adapter, _ADAPTER_FIELDS, path)
        _required(adapter, _ADAPTER_FIELDS, path)
        adapter_id = _identifier(adapter["adapter_id"], f"{path}.adapter_id")
        if adapter_id in adapter_ids:
            _fail(path, "duplicate adapter_id")
        adapter_ids.add(adapter_id)
        status = _status(adapter["status"], f"{path}.status")
        if status not in {"NOT_RUN", "BLOCKED"}:
            _fail(f"{path}.status", "unimplemented live adapters must be NOT_RUN or BLOCKED")
        _identifier(adapter["reason_code"], f"{path}.reason_code")
        _text(adapter["message"], f"{path}.message")

    limitations = _sequence(model["limitations"], "$.limitations", nonempty=True)
    for index, value in enumerate(limitations):
        _text(value, f"$.limitations[{index}]")
    if len(set(limitations)) != len(limitations) or list(limitations) != sorted(limitations):
        _fail("$.limitations", "limitations must be unique and in canonical order")
    policy = _mapping(model["hash_policy"], "$.hash_policy")
    _closed(policy, _HASH_POLICY_FIELDS, "$.hash_policy")
    _required(policy, _HASH_POLICY_FIELDS, "$.hash_policy")
    expected_policy = {
        "algorithm": "SHA-256",
        "canonicalization": "REP02_SORTED_MINIFIED_UTF8_JSON",
        "technical_result_scope": "EXACT_COMPONENT_RESULT_EXCLUDING_EXPLICIT_REPORT_GENERATED_AT",
        "report_model_scope": "ALL_REPORT_MODEL_FIELDS_EXCEPT_GENERATED_AT_AND_REPORT_MODEL_HASH",
    }
    if policy != expected_policy:
        _fail("$.hash_policy", "hash policy does not match the reporting contract")
    expected_hash = sha256_hexdigest(_report_hash_payload(model))
    actual_hash = _sha256(model["report_model_hash"], "$.report_model_hash")
    if actual_hash != expected_hash:
        _fail("$.report_model_hash", "hash does not match report model")
    _ensure_json_value(model)
    return model


def render_json_report(report_model: Mapping[str, Any]) -> str:
    """Render a validated report model as deterministic, finite JSON."""

    model = _validate_report_model(report_model)
    return json.dumps(model, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _markdown_cell(value: Any) -> str:
    escaped = html.escape(str(value), quote=False)
    escaped = escaped.replace("\\", "\\\\")
    for marker in ("!", "[", "]", "(", ")", "|", "`"):
        escaped = escaped.replace(marker, "\\" + marker)
    return escaped.replace("\r", " ").replace("\n", " ")


def render_markdown_report(report_model: Mapping[str, Any]) -> str:
    """Render the same validated model used by :func:`render_json_report`."""

    model = _validate_report_model(report_model)
    lines = [
        "# REP-02 Validation Report",
        "",
        f"- Status: `{model['status']}`",
        f"- Report model SHA-256: `{model['report_model_hash']}`",
        f"- Reporter version: `{model['reporter_version']}`",
    ]
    if "generated_at" in model:
        lines.append(f"- Generated at: `{model['generated_at']}`")
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| Finding ID | Rule ID | Status | Reason code | Message | Input SHA-256 | Technical result SHA-256 | Tool | Tool version | Package version |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for finding in model["findings"]:
        values = (
            finding["finding_id"],
            finding["rule_id"],
            finding["status"],
            finding["reason_code"],
            finding["message"],
            finding["input_hash"],
            finding["technical_result_hash"],
            finding["tool_name"],
            finding["tool_version"],
            finding["package_version"],
        )
        lines.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
    lines.extend(["", "## Source bindings", ""])
    for finding in model["findings"]:
        refs = ", ".join(
            f"{ref['source_id']}@{ref['version']} ({ref['sha256']})" for ref in finding["source_refs"]
        )
        lines.append(f"- `{finding['finding_id']}`: {_markdown_cell(refs)}")
    lines.extend(["", "## Live adapter limitations", ""])
    for adapter in model["adapter_limitations"]:
        lines.append(
            f"- `{adapter['adapter_id']}` — `{adapter['status']}` / "
            f"`{adapter['reason_code']}`: {_markdown_cell(adapter['message'])}"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- `{_markdown_cell(value)}`" for value in model["limitations"])
    return "\n".join(lines) + "\n"


def _manifest_hash_payload(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in manifest.items() if key not in {"generated_at", "manifest_hash"}}


def _relative_path(value: Any, path: str) -> str:
    text = _text(value, path)
    if "\\" in text:
        _fail(path, "manifest paths must use '/' separators")
    if re.match(r"^[A-Za-z]:/", text):
        _fail(path, "manifest path must be relative rather than drive-qualified")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or text.startswith("/") or any(part in {"", ".", ".."} for part in candidate.parts):
        _fail(path, "manifest path must be a normalized relative path without traversal")
    normalized = candidate.as_posix()
    if normalized != text:
        _fail(path, "manifest path must already be normalized")
    return normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail("$.path", f"cannot read manifest file: {type(exc).__name__}")
    return digest.hexdigest()


def build_evidence_manifest(
    entries: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    base_dir: Union[str, Path],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a closed, file-verified evidence manifest.

    Inputs, sources, tools, and outputs must be non-empty.  ``models`` must be
    present but may be an empty array so this slice records the absence of a
    physical or analytical model explicitly rather than silently omitting it.
    """

    spec = _mapping(entries, "$")
    _closed(spec, _MANIFEST_CATEGORIES, "$")
    _required(spec, _MANIFEST_CATEGORIES, "$")
    generated = _timestamp(generated_at, "$.generated_at")
    if not isinstance(base_dir, (str, Path)):
        _fail("$.base_dir", "base_dir must be a path string or Path")
    base = Path(base_dir).resolve()
    if not base.is_dir():
        _fail("$.base_dir", "base_dir must name an existing directory")

    normalized: Dict[str, List[Dict[str, str]]] = {}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_resolved_paths: set[str] = set()
    for category in _MANIFEST_CATEGORIES:
        items: List[Dict[str, str]] = []
        category_values = _sequence(
            spec[category],
            f"$.{category}",
            nonempty=category != "models",
        )
        for index, entry_value in enumerate(category_values):
            entry_path = f"$.{category}[{index}]"
            entry = _mapping(entry_value, entry_path)
            _closed(entry, _MANIFEST_ENTRY_FIELDS, entry_path)
            _required(entry, _MANIFEST_ENTRY_FIELDS, entry_path)
            identifier = _identifier(entry["id"], f"{entry_path}.id")
            relative = _relative_path(entry["path"], f"{entry_path}.path")
            supplied = _sha256(entry["sha256"], f"{entry_path}.sha256")
            version = _version(entry["version"], f"{entry_path}.version")
            if identifier in seen_ids:
                _fail(entry_path, f"duplicate manifest id {identifier!r}")
            if relative in seen_paths:
                _fail(entry_path, f"duplicate manifest path {relative!r}")
            seen_ids.add(identifier)
            seen_paths.add(relative)
            resolved = (base / Path(*PurePosixPath(relative).parts)).resolve()
            try:
                resolved.relative_to(base)
            except ValueError:
                _fail(f"{entry_path}.path", "resolved path escapes base_dir")
            resolved_key = str(resolved).casefold()
            if resolved_key in seen_resolved_paths:
                _fail(entry_path, "duplicate resolved manifest path")
            seen_resolved_paths.add(resolved_key)
            if not resolved.is_file():
                _fail(f"{entry_path}.path", "manifest path does not name a file")
            observed = _file_sha256(resolved)
            if supplied != observed:
                _fail(f"{entry_path}.sha256", f"hash mismatch; observed {observed}")
            items.append({"id": identifier, "path": relative, "sha256": supplied, "version": version})
        normalized[category] = sorted(items, key=lambda item: (item["id"], item["path"]))

    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "reporter_version": REPORTER_VERSION,
        **normalized,
        "hash_policy": {
            "algorithm": "SHA-256",
            "canonicalization": "REP02_SORTED_MINIFIED_UTF8_JSON",
            "file_hash_scope": "EXACT_FILE_BYTES",
            "manifest_hash_scope": "ALL_MANIFEST_FIELDS_EXCEPT_GENERATED_AT_AND_MANIFEST_HASH",
        },
    }
    if generated is not None:
        manifest["generated_at"] = generated
    manifest["manifest_hash"] = sha256_hexdigest(_manifest_hash_payload(manifest))
    _validate_manifest(manifest)
    return copy.deepcopy(manifest)


def _validate_manifest(manifest_value: Any) -> None:
    manifest = _mapping(manifest_value, "$")
    _closed(manifest, _MANIFEST_FIELDS, "$")
    _required(manifest, _MANIFEST_FIELDS - {"generated_at"}, "$")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        _fail("$.schema_version", "unsupported manifest schema version")
    if manifest["reporter_version"] != REPORTER_VERSION:
        _fail("$.reporter_version", "reporter version mismatch")
    if "generated_at" in manifest:
        _timestamp(manifest["generated_at"], "$.generated_at")
    for category in _MANIFEST_CATEGORIES:
        category_values = _sequence(
            manifest[category],
            f"$.{category}",
            nonempty=category != "models",
        )
        for index, value in enumerate(category_values):
            path = f"$.{category}[{index}]"
            entry = _mapping(value, path)
            _closed(entry, _MANIFEST_ENTRY_FIELDS, path)
            _required(entry, _MANIFEST_ENTRY_FIELDS, path)
            _identifier(entry["id"], f"{path}.id")
            _relative_path(entry["path"], f"{path}.path")
            _sha256(entry["sha256"], f"{path}.sha256")
            _version(entry["version"], f"{path}.version")
    policy = _mapping(manifest["hash_policy"], "$.hash_policy")
    expected_policy_fields = {"algorithm", "canonicalization", "file_hash_scope", "manifest_hash_scope"}
    _closed(policy, expected_policy_fields, "$.hash_policy")
    _required(policy, expected_policy_fields, "$.hash_policy")
    actual = _sha256(manifest["manifest_hash"], "$.manifest_hash")
    expected = sha256_hexdigest(_manifest_hash_payload(manifest))
    if actual != expected:
        _fail("$.manifest_hash", "hash does not match manifest")
    _ensure_json_value(manifest)


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "REPORTER_VERSION",
    "REPORT_SCHEMA_VERSION",
    "ReportValidationError",
    "build_evidence_manifest",
    "build_report_model",
    "render_json_report",
    "render_markdown_report",
]
