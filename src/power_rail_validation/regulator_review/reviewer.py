"""Deterministic, fail-closed regulator review engine for WF-02-05.

The engine reviews versioned records; it does not run SPICE, estimate a hidden
junction temperature, or infer loop stability from a transient waveform.  The
input contract is intentionally module-local because the D1 ``regulator``
entity does not contain exact-part, temperature, derating, or model fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest
from power_rail_validation.rail_budget import calculate_rail_budget


ENGINE_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = "1.0.0"
RULE_SET_VERSION = "REP02-RR-RULESET-1.0.0"
RULE_ID = "REP02.REGULATOR.REVIEW"
TOOL_NAME = "power-rail-validation"
RULE_SOURCE_ID = "REP02-RR-RULES"
RULE_SOURCE_SHA256 = "92ab4059050fb5d381fa4b7c7cbf2301212c6956be0255d39f8da15ccb85187b"

INPUT_RULE = "REP02.REGULATOR.REVIEW.INPUT"
RAIL_BUDGET_RULE = "REP02.REGULATOR.RAIL_BUDGET.BASELINE"
BINDING_RULE = "REP02.REGULATOR.RAIL_BUDGET.BINDING"
OUTPUT_VOLTAGE_BINDING_RULE = "REP02.REGULATOR.OUTPUT_VOLTAGE.BINDING"
RAIL_SOURCE_BINDING_RULE = "REP02.REGULATOR.RAIL_BUDGET.SOURCE_BINDING"
EXACT_SOURCE_RULE = "REP02.REGULATOR.SOURCE.EXACT_PART"
VIN_ABS_RULE = "REP02.REGULATOR.VIN.ABSOLUTE_MAXIMUM"
VIN_REC_RULE = "REP02.REGULATOR.VIN.RECOMMENDED_OPERATING"
IOUT_ABS_RULE = "REP02.REGULATOR.IOUT.ABSOLUTE_MAXIMUM"
IOUT_REC_RULE = "REP02.REGULATOR.IOUT.RECOMMENDED_OPERATING"
TJ_ABS_RULE = "REP02.REGULATOR.TEMPERATURE.ABSOLUTE_MAXIMUM"
TA_REC_RULE = "REP02.REGULATOR.TEMPERATURE.RECOMMENDED_OPERATING"
DERATING_RULE = "REP02.REGULATOR.IOUT.DERATING"
MODEL_PROVENANCE_RULE = "REP02.REGULATOR.MODEL.PROVENANCE"
MODEL_APPLICABILITY_RULE = "REP02.REGULATOR.MODEL.APPLICABILITY"
TRANSIENT_RULE = "REP02.REGULATOR.TRANSIENT.EVIDENCE"
STABILITY_RULE = "REP02.REGULATOR.LOOP_STABILITY.EVIDENCE"

_CATALOG_RULE_IDS = (
    RULE_ID,
    INPUT_RULE,
    RAIL_BUDGET_RULE,
    BINDING_RULE,
    OUTPUT_VOLTAGE_BINDING_RULE,
    RAIL_SOURCE_BINDING_RULE,
    EXACT_SOURCE_RULE,
    VIN_ABS_RULE,
    VIN_REC_RULE,
    IOUT_ABS_RULE,
    IOUT_REC_RULE,
    TJ_ABS_RULE,
    TA_REC_RULE,
    DERATING_RULE,
    MODEL_PROVENANCE_RULE,
    MODEL_APPLICABILITY_RULE,
    TRANSIENT_RULE,
    STABILITY_RULE,
)

_STATUS_ORDER = ("BLOCKED", "FAIL", "UNKNOWN", "NOT_RUN", "PASS")
_STATUS_RANK = {status: len(_STATUS_ORDER) - index for index, status in enumerate(_STATUS_ORDER)}
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FLOATING_VERSIONS = frozenset(
    {
        "canary",
        "current",
        "dev",
        "develop",
        "development",
        "edge",
        "head",
        "latest",
        "main",
        "master",
        "next",
        "nightly",
        "release",
        "rolling",
        "snapshot",
        "stable",
        "tip",
        "trunk",
        "unknown",
        "unspecified",
        "vnext",
    }
)


@dataclass(frozen=True)
class _Issue:
    status: str
    reason_code: str
    path: str
    message: str
    rule_id: str = INPUT_RULE
    source_refs: Tuple[str, ...] = (RULE_SOURCE_ID,)

    def as_dict(self, available_source_ids: Iterable[str]) -> Dict[str, Any]:
        available = frozenset(available_source_ids)
        refs = tuple(sorted({ref for ref in self.source_refs if ref in available}))
        if RULE_SOURCE_ID in available and RULE_SOURCE_ID not in refs:
            refs = tuple(sorted(refs + (RULE_SOURCE_ID,)))
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "path": self.path,
            "message": self.message,
            "rule_id": self.rule_id,
            "source_refs": list(refs),
        }


@dataclass(frozen=True)
class _Measure:
    state: str
    value: Optional[Decimal]
    unit: str


@dataclass(frozen=True)
class _HashRecord:
    state: str
    value: Optional[str]


class _DuplicateJsonKey(ValueError):
    pass


def _rule_source() -> Dict[str, Any]:
    path = Path(__file__).with_name("review-rules.json")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != RULE_SOURCE_SHA256:
        raise ValueError("embedded regulator rule catalog byte hash mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if (
        payload.get("schema_version") != "1.0.0"
        or payload.get("rule_set_version") != RULE_SET_VERSION
        or payload.get("source_id") != RULE_SOURCE_ID
        or payload.get("status_precedence") != list(_STATUS_ORDER)
    ):
        raise ValueError("embedded regulator rule catalog identity mismatch")
    rules = payload.get("rules")
    if not isinstance(rules, list) or any(not isinstance(item, dict) for item in rules):
        raise ValueError("embedded regulator rule catalog rules are invalid")
    rule_ids = [item.get("rule_id") for item in rules]
    if (
        any(not isinstance(rule_id, str) for rule_id in rule_ids)
        or len(rule_ids) != len(set(rule_ids))
        or set(rule_ids) != set(_CATALOG_RULE_IDS)
    ):
        raise ValueError("embedded regulator rule catalog is incomplete or duplicated")
    return {
        "schema_version": "1.0.0",
        "entity_type": "source",
        "source_id": RULE_SOURCE_ID,
        "classification": "ENGINEERING_RULE",
        "title": payload.get("title", "REP-02 regulator review rules"),
        "version": RULE_SET_VERSION,
        "locator": "power_rail_validation/regulator_review/review-rules.json",
        "access_status": "AVAILABLE",
        "content_hash": digest,
        "source_role": "RULE_CATALOG",
        "publisher_type": "INTERNAL_RULE",
        "manufacturer": "REP-02",
        "part_number": "NOT_APPLICABLE",
        "document_revision": RULE_SET_VERSION,
        "page_section": "rules",
        "license_status": "DECLARED",
    }


def _add(
    issues: MutableSequence[_Issue],
    status: str,
    reason_code: str,
    path: str,
    message: str,
    rule_id: str = INPUT_RULE,
    source_refs: Iterable[str] = (RULE_SOURCE_ID,),
) -> None:
    issues.append(_Issue(status, reason_code, path, message, rule_id, tuple(source_refs)))


def _reject(value: Mapping[str, Any], allowed: Iterable[str], path: str, issues: MutableSequence[_Issue]) -> None:
    allowed_set = frozenset(allowed)
    for key in sorted((key for key in value if key not in allowed_set), key=repr):
        _add(issues, "BLOCKED", "UNEXPECTED_FIELD", f"{path}.{key}", "field is outside the regulator-review input contract")


def _require(value: Mapping[str, Any], fields: Iterable[str], path: str, issues: MutableSequence[_Issue]) -> None:
    for name in sorted(fields):
        if name not in value:
            _add(issues, "BLOCKED", "REQUIRED_INPUT_MISSING", f"{path}.{name}", "required field is absent")


def _mapping(value: Any, path: str, issues: MutableSequence[_Issue]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _add(issues, "BLOCKED", "OBJECT_REQUIRED", path, "expected an object")
        return {}
    return value


def _text(value: Any, path: str, issues: MutableSequence[_Issue], code: str = "TEXT_REQUIRED") -> Optional[str]:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _add(issues, "BLOCKED", code, path, "expected a non-empty string without surrounding whitespace")
        return None
    return value


def _identifier(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    text = _text(value, path, issues, "IDENTIFIER_INVALID")
    if text is not None and _ID_RE.fullmatch(text) is None:
        _add(issues, "BLOCKED", "IDENTIFIER_INVALID", path, "expected a stable identifier")
        return None
    return text


def _enum(value: Any, allowed: Iterable[str], path: str, issues: MutableSequence[_Issue], code: str) -> Optional[str]:
    allowed_values = frozenset(allowed)
    try:
        valid = value in allowed_values
    except TypeError:
        valid = False
    if not valid:
        _add(issues, "BLOCKED", code, path, f"expected one of {sorted(allowed_values)}")
        return None
    return value


def _is_floating_version(value: str) -> bool:
    normalized = value.casefold()
    if normalized in _FLOATING_VERSIONS:
        return True
    if any(marker in normalized for marker in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    parts = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    if any(part in _FLOATING_VERSIONS or part in {"branch", "branches", "heads"} for part in parts):
        return True
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", normalized) if token)
    if any(token in _FLOATING_VERSIONS for token in tokens):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _version(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    text = _text(value, path, issues, "VERSION_REQUIRED")
    if text is not None and _is_floating_version(text):
        _add(issues, "BLOCKED", "MOVING_VERSION_FORBIDDEN", path, "version must be pinned")
        return None
    return text


def _refs(value: Any, path: str, issues: MutableSequence[_Issue]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _add(issues, "BLOCKED", "SOURCE_REFS_REQUIRED", path, "at least one source reference is required")
        return ()
    refs: List[str] = []
    for index, item in enumerate(value):
        ref = _identifier(item, f"{path}[{index}]", issues)
        if ref is not None:
            refs.append(ref)
    if len(set(refs)) != len(refs):
        _add(issues, "BLOCKED", "DUPLICATE_SOURCE_REF", path, "source references must be unique")
    return tuple(sorted(set(refs)))


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _measure(value: Any, unit: str, path: str, issues: MutableSequence[_Issue]) -> _Measure:
    item = _mapping(value, path, issues)
    _reject(item, {"state", "value", "unit"}, path, issues)
    _require(item, {"state", "value", "unit"}, path, issues)
    state = _enum(item.get("state"), {"KNOWN", "UNKNOWN"}, f"{path}.state", issues, "QUANTITY_STATE_INVALID")
    observed_unit = item.get("unit")
    if observed_unit != unit:
        _add(issues, "BLOCKED", "UNIT_MISMATCH", f"{path}.unit", f"expected unit {unit!r}")
    measured = item.get("value")
    if state == "KNOWN":
        numeric = _decimal(measured)
        if numeric is None:
            _add(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", f"{path}.value", "KNOWN quantity requires a finite number")
            return _Measure("INVALID", None, unit)
        return _Measure("KNOWN", numeric, unit)
    if state == "UNKNOWN":
        if measured is not None:
            _add(issues, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{path}.value", "UNKNOWN quantity requires null")
            return _Measure("INVALID", None, unit)
        return _Measure("UNKNOWN", None, unit)
    return _Measure("INVALID", None, unit)


def _hash_record(value: Any, path: str, issues: MutableSequence[_Issue]) -> _HashRecord:
    item = _mapping(value, path, issues)
    _reject(item, {"state", "algorithm", "value"}, path, issues)
    _require(item, {"state", "algorithm", "value"}, path, issues)
    state = _enum(item.get("state"), {"KNOWN", "UNKNOWN"}, f"{path}.state", issues, "HASH_STATE_INVALID")
    if item.get("algorithm") != "SHA-256":
        _add(issues, "BLOCKED", "HASH_ALGORITHM_INVALID", f"{path}.algorithm", "expected SHA-256")
    digest = item.get("value")
    if state == "KNOWN":
        if not isinstance(digest, str) or _SHA_RE.fullmatch(digest) is None:
            _add(issues, "BLOCKED", "HASH_INVALID", f"{path}.value", "expected a lowercase 64-character SHA-256 digest")
            return _HashRecord("INVALID", None)
        return _HashRecord("KNOWN", digest)
    if state == "UNKNOWN":
        if digest is not None:
            _add(issues, "BLOCKED", "UNKNOWN_HASH_MUST_BE_NULL", f"{path}.value", "UNKNOWN hash requires null")
            return _HashRecord("INVALID", None)
        return _HashRecord("UNKNOWN", None)
    return _HashRecord("INVALID", None)


def _quantity_output(value: Optional[Decimal], unit: str) -> Dict[str, Any]:
    if value is None:
        return {"state": "UNKNOWN", "value": None, "unit": unit}
    if value == value.to_integral_value():
        numeric: Union[int, float] = int(value)
    else:
        numeric = float(format(value.normalize(), "f"))
    return {"state": "KNOWN", "value": numeric, "unit": unit}


def _artifact_root(value: Optional[Union[str, Path]], issues: MutableSequence[_Issue]) -> Optional[Path]:
    if value is None:
        _add(
            issues,
            "BLOCKED",
            "ARTIFACT_ROOT_REQUIRED",
            "$.artifact_root",
            "AVAILABLE source claims require an explicit artifact root",
        )
        return None
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        _add(issues, "BLOCKED", "ARTIFACT_ROOT_INVALID", "$.artifact_root", "artifact root must be a directory path")
        return None
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        _add(issues, "BLOCKED", "ARTIFACT_ROOT_INVALID", "$.artifact_root", "artifact root cannot be resolved")
        return None
    if not root.is_dir():
        _add(issues, "BLOCKED", "ARTIFACT_ROOT_INVALID", "$.artifact_root", "artifact root is not a directory")
        return None
    return root


def _safe_artifact_path(locator: Any, root: Optional[Path]) -> Optional[Path]:
    if root is None or not isinstance(locator, str) or not locator or locator != locator.strip():
        return None
    if "\\" in locator or "\x00" in locator:
        return None
    pure = PurePosixPath(locator)
    if (
        pure.is_absolute()
        or pure.as_posix() != locator
        or any(part in {"", ".", ".."} or ":" in part for part in pure.parts)
    ):
        return None
    try:
        candidate = root.joinpath(*pure.parts).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _verify_source_bytes(
    item: Mapping[str, Any],
    digest: _HashRecord,
    path: str,
    root: Optional[Path],
    issues: MutableSequence[_Issue],
) -> Tuple[bool, Optional[bytes]]:
    if item.get("access_status") != "AVAILABLE":
        return False, None
    locator = item.get("locator")
    candidate = _safe_artifact_path(locator, root)
    if candidate is None:
        reason = "SOURCE_LOCATOR_UNSAFE" if root is not None else "SOURCE_BYTES_UNVERIFIED"
        message = "source locator must be a safe relative POSIX file path under artifact_root" if root is not None else "source bytes cannot be verified without artifact_root"
        _add(issues, "BLOCKED", reason, f"{path}.locator", message)
        return False, None
    if digest.state != "KNOWN" or digest.value is None:
        return False, None
    try:
        payload = candidate.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
    except OSError:
        _add(issues, "BLOCKED", "SOURCE_BYTES_UNAVAILABLE", f"{path}.locator", "source file bytes cannot be read")
        return False, None
    if actual != digest.value:
        _add(
            issues,
            "BLOCKED",
            "SOURCE_BYTES_HASH_MISMATCH",
            f"{path}.content_hash.value",
            "declared SHA-256 does not match the source file bytes",
        )
        return False, None
    return True, payload


def _strict_json_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(token: str) -> None:
    raise ValueError(token)


def _finite_json_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_json_tree(item) for item in value)
    if isinstance(value, dict):
        return all(_finite_json_tree(item) for item in value.values())
    return value is None or isinstance(value, (str, bool, int))


def _source_semantics(
    role: Optional[str],
    payload: Optional[bytes],
    path: str,
    issues: MutableSequence[_Issue],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if payload is None:
        return None, None
    if role == "MODEL":
        try:
            return None, payload.decode("utf-8")
        except UnicodeDecodeError:
            _add(
                issues,
                "BLOCKED",
                "SOURCE_CONTENT_SCHEMA_INVALID",
                f"{path}.locator",
                "model source bytes must be valid UTF-8 for semantic binding",
            )
            return None, None
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, dict) or not _finite_json_tree(value):
            raise ValueError("source content must be a finite JSON object")
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, _DuplicateJsonKey):
        _add(
            issues,
            "BLOCKED",
            "SOURCE_CONTENT_SCHEMA_INVALID",
            f"{path}.locator",
            "byte-verified source must contain a finite, duplicate-free JSON object",
        )
        return None, None
    return value, None


def _source_index(
    value: Any,
    issues: MutableSequence[_Issue],
    artifact_root: Optional[Path],
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, list):
        _add(issues, "BLOCKED", "SOURCES_REQUIRED", "$.sources", "sources must be an array")
        return {}
    allowed = {
        "schema_version", "entity_type", "source_id", "classification", "title", "version", "locator",
        "access_status", "content_hash", "source_role", "publisher_type", "manufacturer", "part_number",
        "document_revision", "page_section", "license_status",
    }
    required = allowed
    result: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(value):
        path = f"$.sources[{index}]"
        item = _mapping(raw, path, issues)
        _reject(item, allowed, path, issues)
        _require(item, required, path, issues)
        if item.get("schema_version") != "1.0.0" or item.get("entity_type") != "source":
            _add(issues, "BLOCKED", "SOURCE_SCHEMA_INVALID", path, "expected source schema_version 1.0.0 and entity_type source")
        source_id = _identifier(item.get("source_id"), f"{path}.source_id", issues)
        classification = _enum(
            item.get("classification"),
            {"SOURCE_EXPLICIT", "DERIVED", "ENGINEERING_RULE", "ASSUMPTION", "UNKNOWN", "CONFLICT"},
            f"{path}.classification", issues, "SOURCE_CLASSIFICATION_INVALID",
        )
        texts = {
            name: _text(item.get(name), f"{path}.{name}", issues)
            for name in ("title", "locator", "manufacturer", "part_number", "page_section")
        }
        version = _version(item.get("version"), f"{path}.version", issues)
        document_revision = _version(
            item.get("document_revision"),
            f"{path}.document_revision",
            issues,
        )
        access = _enum(item.get("access_status"), {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"}, f"{path}.access_status", issues, "ACCESS_STATUS_INVALID")
        role = _enum(item.get("source_role"), {"DATASHEET", "MODEL", "RAIL_BUDGET", "TRANSIENT_RESULT", "STABILITY_RESULT"}, f"{path}.source_role", issues, "SOURCE_ROLE_INVALID")
        publisher = _enum(item.get("publisher_type"), {"MANUFACTURER", "INTERNAL_TOOL", "THIRD_PARTY", "UNKNOWN"}, f"{path}.publisher_type", issues, "PUBLISHER_TYPE_INVALID")
        license_status = _enum(item.get("license_status"), {"VERIFIED", "DECLARED", "UNKNOWN", "CONFLICT", "RESTRICTED"}, f"{path}.license_status", issues, "LICENSE_STATUS_INVALID")
        content_hash = _hash_record(item.get("content_hash"), f"{path}.content_hash", issues)
        if access == "AVAILABLE" and content_hash.state != "KNOWN":
            _add(issues, "BLOCKED", "SOURCE_HASH_UNKNOWN", f"{path}.content_hash", "AVAILABLE source requires a known SHA-256")
        if access in {"MISSING", "UNKNOWN"} and content_hash.state == "KNOWN":
            _add(issues, "BLOCKED", "UNAVAILABLE_SOURCE_HAS_HASH", f"{path}.content_hash", "MISSING or UNKNOWN source must not claim available bytes")
        if source_id is not None:
            if source_id in result:
                _add(issues, "BLOCKED", "DUPLICATE_SOURCE_ID", f"{path}.source_id", "source identifier is duplicated")
            else:
                normalized: Dict[str, Any] = {
                    "schema_version": item.get("schema_version"),
                    "entity_type": item.get("entity_type"),
                    "source_id": source_id,
                    "classification": classification,
                    "title": texts["title"],
                    "version": version,
                    "locator": texts["locator"],
                    "access_status": access,
                    "content_hash": {
                        "state": content_hash.state,
                        "algorithm": "SHA-256",
                        "value": content_hash.value,
                    },
                    "source_role": role,
                    "publisher_type": publisher,
                    "manufacturer": texts["manufacturer"],
                    "part_number": texts["part_number"],
                    "document_revision": document_revision,
                    "page_section": texts["page_section"],
                    "license_status": license_status,
                    "_hash": content_hash,
                }
                byte_verified, payload = _verify_source_bytes(item, content_hash, path, artifact_root, issues)
                artifact_document, artifact_text = _source_semantics(role, payload, path, issues)
                normalized["_byte_verified"] = byte_verified
                normalized["_artifact_document"] = artifact_document
                normalized["_artifact_text"] = artifact_text
                result[source_id] = normalized
    return result


def _available_source_output(index: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    output = [_rule_source()]
    for source_id in sorted(index):
        item = index[source_id]
        digest = item.get("_hash")
        if (
            item.get("access_status") != "AVAILABLE"
            or not isinstance(digest, _HashRecord)
            or digest.state != "KNOWN"
            or item.get("_byte_verified") is not True
        ):
            continue
        output.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"_hash", "_byte_verified", "_artifact_document", "_artifact_text"}
            }
        )
    return output


def _source_requirements(index: Mapping[str, Mapping[str, Any]], requested: Iterable[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for source_id in sorted(set(requested)):
        item = index.get(source_id)
        if item is None:
            records.append({"source_id": source_id, "resolution": "UNRESOLVED", "access_status": "UNKNOWN", "hash_state": "UNKNOWN", "byte_verification": "NOT_VERIFIED"})
            continue
        digest = item.get("_hash")
        records.append(
            {
                "source_id": source_id,
                "resolution": "RESOLVED",
                "access_status": item.get("access_status"),
                "hash_state": digest.state if isinstance(digest, _HashRecord) else "INVALID",
                "byte_verification": "VERIFIED" if item.get("_byte_verified") is True else "NOT_VERIFIED",
                "version": item.get("version"),
                "source_role": item.get("source_role"),
            }
        )
    return records


def _gate_sources(
    refs: Sequence[str],
    index: Mapping[str, Mapping[str, Any]],
    regulator: Mapping[str, Any],
    expected_role: str,
    *,
    manufacturer_only: bool,
) -> Tuple[str, str, str, Tuple[str, ...]]:
    available: List[str] = []
    if not refs:
        return "BLOCKED", "SOURCE_REFS_REQUIRED", "required source references are absent", ()
    for ref in refs:
        source = index.get(ref)
        if source is None:
            return "BLOCKED", "SOURCE_UNAVAILABLE", f"source {ref!r} does not resolve", tuple(available)
        if source.get("classification") == "CONFLICT" or source.get("license_status") == "CONFLICT":
            return "BLOCKED", "SOURCE_CONFLICT", f"source {ref!r} is marked CONFLICT", tuple(available)
        if source.get("access_status") != "AVAILABLE":
            return "BLOCKED", "SOURCE_UNAVAILABLE", f"source {ref!r} is not AVAILABLE", tuple(available)
        digest = source.get("_hash")
        if not isinstance(digest, _HashRecord) or digest.state != "KNOWN":
            return "BLOCKED", "SOURCE_HASH_UNKNOWN", f"source {ref!r} has no verified SHA-256", tuple(available)
        if source.get("_byte_verified") is not True:
            return "BLOCKED", "SOURCE_BYTES_UNVERIFIED", f"source {ref!r} bytes were not verified under artifact_root", tuple(available)
        if source.get("version") is None or _is_floating_version(str(source.get("version"))):
            return "BLOCKED", "SOURCE_VERSION_UNPINNED", f"source {ref!r} does not have a pinned version", tuple(available)
        if source.get("source_role") != expected_role:
            return "BLOCKED", "SOURCE_ROLE_MISMATCH", f"source {ref!r} is not a {expected_role} record", tuple(available)
        if source.get("manufacturer") != regulator.get("manufacturer") or source.get("part_number") != regulator.get("part_number"):
            return "BLOCKED", "SOURCE_PART_MISMATCH", f"source {ref!r} is not bound to the exact review subject", tuple(available)
        if manufacturer_only:
            if source.get("classification") != "SOURCE_EXPLICIT" or source.get("publisher_type") != "MANUFACTURER":
                return "BLOCKED", "SOURCE_ORIGIN_UNVERIFIED", f"source {ref!r} is not an explicit manufacturer source", tuple(available)
            if source.get("license_status") not in {"VERIFIED", "DECLARED"}:
                return "BLOCKED", "SOURCE_LICENSE_UNVERIFIED", f"source {ref!r} license status is not usable", tuple(available)
        elif source.get("classification") not in {"SOURCE_EXPLICIT", "DERIVED"}:
            return "BLOCKED", "EVIDENCE_CLASSIFICATION_UNSAFE", f"source {ref!r} is not explicit or derived evidence", tuple(available)
        available.append(ref)
    return "PASS", "SOURCE_PROVENANCE_VERIFIED", "all required source records are available and provenance-bound", tuple(sorted(available))


def _datasheet_binding_gate(
    gate: Tuple[str, str, str, Tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    regulator: Mapping[str, Any],
    *,
    absolute: Optional[Mapping[str, _Measure]] = None,
    recommended: Optional[Mapping[str, _Measure]] = None,
    derating_points: Optional[Sequence[Tuple[Decimal, Decimal]]] = None,
) -> Tuple[str, str, str, Tuple[str, ...]]:
    if gate[0] != "PASS":
        return gate
    for ref in gate[3]:
        source = sources[ref]
        document = source.get("_artifact_document")
        if not isinstance(document, dict):
            return (
                "BLOCKED",
                "DATASHEET_CONTENT_SCHEMA_INVALID",
                f"datasheet source {ref!r} has no usable byte-bound JSON content",
                gate[3],
            )
        if (
            document.get("manufacturer") != regulator.get("manufacturer")
            or document.get("part_number") != regulator.get("part_number")
            or document.get("manufacturer") != source.get("manufacturer")
            or document.get("part_number") != source.get("part_number")
            or document.get("document_revision") != source.get("document_revision")
            or document.get("document_revision") != source.get("version")
        ):
            return (
                "BLOCKED",
                "DATASHEET_IDENTITY_BINDING_MISMATCH",
                f"datasheet source {ref!r} content does not match its exact-part source envelope",
                gate[3],
            )

        page_sections = document.get("page_sections")
        required_sections = {"absolute_maximum", "recommended_operating", "derating"}
        if not isinstance(page_sections, dict) or set(page_sections) != required_sections:
            return (
                "BLOCKED",
                "DATASHEET_SECTION_SCHEMA_INVALID",
                f"datasheet source {ref!r} has no complete byte-bound section map",
                gate[3],
            )
        section_numbers: List[int] = []
        for section_name in sorted(required_sections):
            section_value = page_sections.get(section_name)
            match = re.fullmatch(r"section ([1-9][0-9]*)", section_value) if isinstance(section_value, str) else None
            if match is None:
                return (
                    "BLOCKED",
                    "DATASHEET_SECTION_SCHEMA_INVALID",
                    f"datasheet source {ref!r} contains an invalid byte-bound section locator",
                    gate[3],
                )
            section_numbers.append(int(match.group(1)))
        ordered_sections = sorted(set(section_numbers))
        if (
            len(ordered_sections) != len(section_numbers)
            or ordered_sections != list(range(ordered_sections[0], ordered_sections[-1] + 1))
        ):
            return (
                "BLOCKED",
                "DATASHEET_SECTION_SCHEMA_INVALID",
                f"datasheet source {ref!r} section locators must form one exact closed range",
                gate[3],
            )
        expected_page_section = (
            f"section {ordered_sections[0]}"
            if len(ordered_sections) == 1
            else f"sections {ordered_sections[0]}-{ordered_sections[-1]}"
        )
        if source.get("page_section") != expected_page_section:
            return (
                "BLOCKED",
                "DATASHEET_SECTION_BINDING_MISMATCH",
                f"datasheet source {ref!r} page/section locator does not match its byte-bound section map",
                gate[3],
            )

        if absolute is not None:
            payload = document.get("absolute_maximum")
            expected = {
                "input_voltage_V": absolute["input_voltage_max"].value,
                "output_current_A": absolute["output_current_max"].value,
                "junction_temperature_degC": absolute["junction_temperature_max"].value,
            }
            if not isinstance(payload, dict) or any(
                measure is None or _decimal(payload.get(key)) != measure
                for key, measure in expected.items()
            ):
                return (
                    "BLOCKED",
                    "DATASHEET_LIMIT_BINDING_MISMATCH",
                    "absolute-maximum envelope values do not exactly match the byte-bound datasheet content",
                    gate[3],
                )

        if recommended is not None:
            payload = document.get("recommended_operating")
            vin = payload.get("input_voltage_V") if isinstance(payload, dict) else None
            ambient = payload.get("ambient_temperature_degC") if isinstance(payload, dict) else None
            matches = (
                isinstance(payload, dict)
                and isinstance(vin, list)
                and len(vin) == 2
                and isinstance(ambient, list)
                and len(ambient) == 2
                and recommended["input_voltage_min"].value is not None
                and recommended["input_voltage_max"].value is not None
                and recommended["output_current_max"].value is not None
                and recommended["ambient_temperature_min"].value is not None
                and recommended["ambient_temperature_max"].value is not None
                and _decimal(vin[0]) == recommended["input_voltage_min"].value
                and _decimal(vin[1]) == recommended["input_voltage_max"].value
                and _decimal(payload.get("output_current_A")) == recommended["output_current_max"].value
                and _decimal(ambient[0]) == recommended["ambient_temperature_min"].value
                and _decimal(ambient[1]) == recommended["ambient_temperature_max"].value
            )
            if not matches:
                return (
                    "BLOCKED",
                    "DATASHEET_LIMIT_BINDING_MISMATCH",
                    "recommended-operating envelope values do not exactly match the byte-bound datasheet content",
                    gate[3],
                )

        if derating_points is not None:
            payload = document.get("derating_points")
            observed: List[Tuple[Optional[Decimal], Optional[Decimal]]] = []
            if isinstance(payload, list):
                for point in payload:
                    if not isinstance(point, dict):
                        observed.append((None, None))
                    else:
                        observed.append(
                            (
                                _decimal(point.get("ambient_temperature_degC")),
                                _decimal(point.get("maximum_output_current_A")),
                            )
                        )
            if observed != list(derating_points):
                return (
                    "BLOCKED",
                    "DATASHEET_DERATING_BINDING_MISMATCH",
                    "derating envelope points do not exactly match the byte-bound datasheet content",
                    gate[3],
                )
    return gate


def _model_artifact_binding_gate(
    gate: Tuple[str, str, str, Tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    model: Mapping[str, Any],
) -> Tuple[str, str, str, Tuple[str, ...]]:
    if gate[0] != "PASS":
        return gate
    header = re.compile(
        r"^\s*\*\s*part\s*:\s*([^;\r\n]+);\s*version\s*:\s*([^\s\r\n]+)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    directive = re.compile(
        r"^\s*\.(SUBCKT|ENDS)\b(?:[ \t]+([^\s;]+))?[^\r\n]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for ref in gate[3]:
        source = sources[ref]
        text = source.get("_artifact_text")
        matches = list(header.finditer(text)) if isinstance(text, str) else []
        if not matches:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_SCHEMA_INVALID",
                f"model source {ref!r} has no parseable byte-bound part/version header",
                gate[3],
            )
        if len(matches) != 1:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_AMBIGUOUS",
                f"model source {ref!r} must contain exactly one byte-bound part/version header",
                gate[3],
            )
        match = matches[0]
        artifact_part, artifact_version = (item.strip() for item in match.groups())
        if (
            artifact_part != model.get("part_number")
            or artifact_part != source.get("part_number")
            or artifact_version != model.get("version")
            or artifact_version != source.get("version")
            or artifact_version != source.get("document_revision")
        ):
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_BINDING_MISMATCH",
                f"model source {ref!r} bytes do not match the model/source identity envelope",
                gate[3],
            )

        expected_subcircuit = re.sub(r"[^A-Za-z0-9_$.:]", "_", artifact_part)
        active_subcircuit: Optional[str] = None
        exact_part_definitions = 0
        directives = list(directive.finditer(text)) if isinstance(text, str) else []
        if not directives:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_STRUCTURE_INVALID",
                f"model source {ref!r} has no byte-bound .SUBCKT/.ENDS structure",
                gate[3],
            )
        for item in directives:
            kind = item.group(1).upper()
            name = item.group(2)
            if not name:
                return (
                    "BLOCKED",
                    "MODEL_ARTIFACT_STRUCTURE_INVALID",
                    f"model source {ref!r} has an unnamed {kind} directive",
                    gate[3],
                )
            if kind == "SUBCKT":
                if active_subcircuit is not None:
                    return (
                        "BLOCKED",
                        "MODEL_ARTIFACT_AMBIGUOUS",
                        f"model source {ref!r} has nested or overlapping .SUBCKT definitions",
                        gate[3],
                    )
                active_subcircuit = name
                if name.casefold() == expected_subcircuit.casefold():
                    exact_part_definitions += 1
            else:
                if active_subcircuit is None or name.casefold() != active_subcircuit.casefold():
                    return (
                        "BLOCKED",
                        "MODEL_ARTIFACT_STRUCTURE_INVALID",
                        f"model source {ref!r} has an unmatched .ENDS directive",
                        gate[3],
                    )
                active_subcircuit = None
        if active_subcircuit is not None:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_STRUCTURE_INVALID",
                f"model source {ref!r} has an unterminated .SUBCKT definition",
                gate[3],
            )
        if exact_part_definitions == 0:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_BINDING_MISMATCH",
                f"model source {ref!r} has no exact-part .SUBCKT/.ENDS definition",
                gate[3],
            )
        if exact_part_definitions != 1:
            return (
                "BLOCKED",
                "MODEL_ARTIFACT_AMBIGUOUS",
                f"model source {ref!r} has multiple exact-part .SUBCKT definitions",
                gate[3],
            )
    return gate


def _model_bounds_gate(
    bounds: Mapping[str, _Measure],
    recommended_gate: Tuple[str, str, str, Tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
) -> Tuple[str, str, str, Tuple[str, ...]]:
    if recommended_gate[0] != "PASS":
        return recommended_gate
    for ref in recommended_gate[3]:
        document = sources[ref].get("_artifact_document")
        payload = document.get("recommended_operating") if isinstance(document, dict) else None
        vin = payload.get("input_voltage_V") if isinstance(payload, dict) else None
        ambient = payload.get("ambient_temperature_degC") if isinstance(payload, dict) else None
        if (
            not isinstance(vin, list)
            or len(vin) != 2
            or not isinstance(ambient, list)
            or len(ambient) != 2
        ):
            return (
                "BLOCKED",
                "MODEL_BOUNDS_SOURCE_INVALID",
                "model validity bounds cannot be bound to the byte-bound datasheet content",
                recommended_gate[3],
            )
        source_values = {
            "input_voltage_min": _decimal(vin[0]),
            "input_voltage_max": _decimal(vin[1]),
            "output_current_max": _decimal(payload.get("output_current_A")),
            "ambient_temperature_min": _decimal(ambient[0]),
            "ambient_temperature_max": _decimal(ambient[1]),
        }
        if any(item is None for item in source_values.values()) or any(
            measure.state != "KNOWN" or measure.value is None
            for measure in bounds.values()
        ):
            return (
                "BLOCKED",
                "MODEL_BOUNDS_SOURCE_INVALID",
                "model and byte-bound datasource bounds must be finite and known",
                recommended_gate[3],
            )
        assert all(item is not None for item in source_values.values())
        assert all(measure.value is not None for measure in bounds.values())
        within = (
            bounds["input_voltage_min"].value >= source_values["input_voltage_min"]
            and bounds["input_voltage_max"].value <= source_values["input_voltage_max"]
            and bounds["output_current_max"].value <= source_values["output_current_max"]
            and bounds["ambient_temperature_min"].value >= source_values["ambient_temperature_min"]
            and bounds["ambient_temperature_max"].value <= source_values["ambient_temperature_max"]
            and bounds["input_voltage_min"].value <= bounds["input_voltage_max"].value
            and bounds["ambient_temperature_min"].value <= bounds["ambient_temperature_max"].value
        )
        if not within:
            return (
                "BLOCKED",
                "MODEL_BOUNDS_SOURCE_MISMATCH",
                "model validity bounds exceed the byte-bound exact-part operating envelope",
                recommended_gate[3],
            )
    return (
        "PASS",
        "MODEL_BOUNDS_SOURCE_VERIFIED",
        "model validity bounds are contained by the byte-bound exact-part operating envelope",
        recommended_gate[3],
    )


def _analysis_artifact_binding_gate(
    gate: Tuple[str, str, str, Tuple[str, ...]],
    sources: Mapping[str, Mapping[str, Any]],
    record: Mapping[str, Any],
    regulator: Mapping[str, Any],
) -> Tuple[str, str, str, Tuple[str, ...]]:
    if gate[0] != "PASS":
        return gate
    tool = record.get("tool")
    for ref in gate[3]:
        source = sources[ref]
        document = source.get("_artifact_document")
        artifact_tool = document.get("tool") if isinstance(document, dict) else None
        if not isinstance(document, dict) or not isinstance(artifact_tool, dict):
            return (
                "BLOCKED",
                "ANALYSIS_EVIDENCE_SCHEMA_INVALID",
                f"analysis evidence {ref!r} has no usable byte-bound JSON record",
                gate[3],
            )
        artifact_version = document.get("version")
        artifact_revision = document.get("document_revision")
        if (
            document.get("analysis_kind") != record.get("evidence_kind")
            or document.get("part_number") != regulator.get("part_number")
            or document.get("part_number") != source.get("part_number")
            or not isinstance(artifact_version, str)
            or not isinstance(artifact_revision, str)
            or _is_floating_version(artifact_version)
            or _is_floating_version(artifact_revision)
            or artifact_version != source.get("version")
            or artifact_revision != source.get("document_revision")
            or artifact_version != artifact_revision
            or document.get("status") != record.get("execution_status")
            or not isinstance(tool, dict)
            or artifact_tool.get("name") != tool.get("name")
            or artifact_tool.get("version") != tool.get("version")
        ):
            return (
                "BLOCKED",
                "ANALYSIS_EVIDENCE_BINDING_MISMATCH",
                "analysis kind, status, tool, record version/revision, and part identity must exactly match byte-bound evidence",
                gate[3],
            )
    return gate


def _check(
    rule_id: str,
    status: str,
    reason_code: str,
    message: str,
    source_refs: Iterable[str],
    *,
    observed: Optional[Dict[str, Any]] = None,
    limits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "rule_id": rule_id,
        "status": status,
        "reason_code": reason_code,
        "message": message,
        "source_refs": sorted(set((RULE_SOURCE_ID,) + tuple(source_refs))),
    }
    if observed is not None:
        result["observed"] = observed
    if limits is not None:
        result["limits"] = limits
    return result


def _source_blocked_check(rule_id: str, gate: Tuple[str, str, str, Tuple[str, ...]]) -> Dict[str, Any]:
    return _check(rule_id, gate[0], gate[1], gate[2], gate[3])


def _raw_known_quantity(value: Any, unit: str) -> Optional[Decimal]:
    if not isinstance(value, dict) or value.get("state") != "KNOWN" or value.get("unit") != unit:
        return None
    return _decimal(value.get("value"))


def _output_voltage_binding_check(operating: _Measure, rail_input: Any) -> Dict[str, Any]:
    if operating.state != "KNOWN" or operating.value is None:
        return _check(
            OUTPUT_VOLTAGE_BINDING_RULE,
            "BLOCKED",
            "OUTPUT_VOLTAGE_UNKNOWN",
            "reviewed output voltage must be KNOWN before rail identity can be accepted",
            (),
        )
    rail = rail_input.get("rail") if isinstance(rail_input, dict) else None
    nominal = _raw_known_quantity(rail.get("nominal_voltage") if isinstance(rail, dict) else None, "V")
    if nominal is None:
        return _check(
            OUTPUT_VOLTAGE_BINDING_RULE,
            "BLOCKED",
            "RAIL_NOMINAL_VOLTAGE_UNKNOWN",
            "bound rail nominal voltage must be a KNOWN finite value",
            (),
            observed=_quantity_output(operating.value, "V"),
        )
    if operating.value != nominal:
        return _check(
            OUTPUT_VOLTAGE_BINDING_RULE,
            "BLOCKED",
            "OUTPUT_VOLTAGE_BINDING_MISMATCH",
            "reviewed output voltage does not exactly equal the rail nominal voltage",
            (),
            observed=_quantity_output(operating.value, "V"),
            limits={"required_exact_value": _quantity_output(nominal, "V")},
        )
    return _check(
        OUTPUT_VOLTAGE_BINDING_RULE,
        "PASS",
        "OUTPUT_VOLTAGE_BINDING_VERIFIED",
        "reviewed output voltage exactly equals the rail nominal voltage",
        (),
        observed=_quantity_output(operating.value, "V"),
        limits={"required_exact_value": _quantity_output(nominal, "V")},
    )


def _rail_source_binding_check(
    rail_input: Any,
    rail_result: Mapping[str, Any],
    outer_sources: Mapping[str, Mapping[str, Any]],
    regulator: Mapping[str, Any],
) -> Dict[str, Any]:
    if rail_result.get("status") == "BLOCKED":
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_BUDGET_BASELINE_BLOCKED",
            "nested rail-budget source use cannot be accepted while its baseline validation is BLOCKED",
            (),
        )
    nested_records = rail_input.get("sources") if isinstance(rail_input, dict) else None
    used_records = rail_result.get("source_refs")
    if not isinstance(nested_records, list) or not isinstance(used_records, list) or not used_records:
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_SOURCE_CATALOG_UNAVAILABLE",
            "nested rail-budget source use cannot be bound to the outer source catalog",
            (),
        )
    nested_by_id: Dict[str, Mapping[str, Any]] = {}
    for item in nested_records:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str) or item["source_id"] in nested_by_id:
            return _check(
                RAIL_SOURCE_BINDING_RULE,
                "BLOCKED",
                "RAIL_SOURCE_CATALOG_INVALID",
                "nested rail-budget source catalog is malformed or duplicated",
                (),
            )
        nested_by_id[item["source_id"]] = item

    verified_refs: List[str] = []
    fields = ("source_id", "version", "locator", "access_status", "classification", "content_hash")
    normalized_used: List[Tuple[str, Any]] = []
    for item in used_records:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            return _check(
                RAIL_SOURCE_BINDING_RULE,
                "BLOCKED",
                "RAIL_SOURCE_USE_INVALID",
                "nested rail-budget result contains an invalid source-use record",
                tuple(verified_refs),
            )
        normalized_used.append((item["source_id"], item.get("version")))
    if len({source_id for source_id, _ in normalized_used}) != len(normalized_used):
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_SOURCE_USE_DUPLICATED",
            "nested rail-budget result repeats a used source identifier",
            tuple(verified_refs),
        )

    for source_id, used_version in sorted(normalized_used):
        nested = nested_by_id.get(source_id)
        outer = outer_sources.get(source_id)
        if nested is None or outer is None:
            return _check(
                RAIL_SOURCE_BINDING_RULE,
                "BLOCKED",
                "RAIL_SOURCE_OUTER_BINDING_MISSING",
                f"used nested source {source_id!r} has no exact outer source binding",
                tuple(verified_refs),
            )
        if used_version != nested.get("version") or any(nested.get(field) != outer.get(field) for field in fields):
            return _check(
                RAIL_SOURCE_BINDING_RULE,
                "BLOCKED",
                "RAIL_SOURCE_BINDING_MISMATCH",
                f"used nested source {source_id!r} differs from the outer source record",
                tuple(verified_refs),
            )
        if outer.get("_byte_verified") is not True:
            return _check(
                RAIL_SOURCE_BINDING_RULE,
                "BLOCKED",
                "RAIL_SOURCE_BYTES_UNVERIFIED",
                f"used nested source {source_id!r} was not byte-verified under artifact_root",
                tuple(verified_refs),
            )
        verified_refs.append(source_id)

    rail_sources = [
        outer_sources[source_id]
        for source_id in verified_refs
        if outer_sources[source_id].get("source_role") == "RAIL_BUDGET"
    ]
    if len(rail_sources) != 1:
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_ARTIFACT_SOURCE_REQUIRED",
            "exactly one used byte-verified RAIL_BUDGET artifact is required",
            tuple(verified_refs),
        )
    rail_source = rail_sources[0]
    rail_source_id = rail_source.get("source_id")
    artifact = rail_source.get("_artifact_document")
    if not isinstance(rail_source_id, str) or not isinstance(artifact, dict):
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_ARTIFACT_SCHEMA_INVALID",
            "the byte-verified RAIL_BUDGET artifact is not a usable JSON object",
            tuple(verified_refs),
        )
    rail_gate = _gate_sources(
        (rail_source_id,),
        outer_sources,
        regulator,
        "RAIL_BUDGET",
        manufacturer_only=False,
    )
    if rail_gate[0] != "PASS":
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            rail_gate[0],
            rail_gate[1],
            rail_gate[2],
            tuple(verified_refs),
        )
    if (
        rail_source.get("publisher_type") != "INTERNAL_TOOL"
        or rail_source.get("license_status") not in {"VERIFIED", "DECLARED"}
    ):
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_SOURCE_PROVENANCE_UNVERIFIED",
            "RAIL_BUDGET evidence requires an internal-tool publisher and a usable license declaration",
            tuple(verified_refs),
        )

    rail = rail_input.get("rail") if isinstance(rail_input, dict) else None
    loads = rail_input.get("loads") if isinstance(rail_input, dict) else None
    capacity = rail_input.get("capacity") if isinstance(rail_input, dict) else None
    derating = rail_input.get("derating") if isinstance(rail_input, dict) else None
    efficiency = rail_input.get("efficiency") if isinstance(rail_input, dict) else None
    entities: List[Any] = [rail, capacity, derating, efficiency]
    if isinstance(loads, list):
        entities.extend(loads)
    if any(
        not isinstance(entity, dict)
        or not isinstance(entity.get("source_refs"), list)
        or rail_source_id not in entity["source_refs"]
        for entity in entities
    ):
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_ARTIFACT_SOURCE_REFS_MISMATCH",
            "every rail-budget entity must explicitly reference the byte-bound RAIL_BUDGET artifact",
            tuple(verified_refs),
        )

    artifact_limits = artifact.get("voltage_limits_V")
    rail_limits = rail.get("voltage_limits") if isinstance(rail, dict) else None
    identity_and_scalar_match = (
        isinstance(rail, dict)
        and isinstance(capacity, dict)
        and isinstance(derating, dict)
        and isinstance(efficiency, dict)
        and isinstance(artifact_limits, list)
        and len(artifact_limits) == 2
        and artifact.get("rail_id") == rail.get("rail_id")
        and artifact.get("rail_id") == capacity.get("rail_id")
        and artifact.get("regulator_id") == capacity.get("regulator_id")
        and artifact.get("manufacturer") == regulator.get("manufacturer")
        and artifact.get("manufacturer") == rail_source.get("manufacturer")
        and artifact.get("part_number") == regulator.get("part_number")
        and artifact.get("part_number") == rail_source.get("part_number")
        and artifact.get("version") == rail_source.get("version")
        and artifact.get("version") == rail_source.get("document_revision")
        and _decimal(artifact.get("nominal_voltage_V"))
        == _raw_known_quantity(rail.get("nominal_voltage"), "V")
        and isinstance(rail_limits, dict)
        and _decimal(artifact_limits[0])
        == _raw_known_quantity(rail_limits.get("minimum"), "V")
        and _decimal(artifact_limits[1])
        == _raw_known_quantity(rail_limits.get("maximum"), "V")
        and _decimal(artifact.get("rated_current_A"))
        == _raw_known_quantity(capacity.get("rated_current"), "A")
        and _decimal(artifact.get("current_limit_A"))
        == _raw_known_quantity(capacity.get("current_limit"), "A")
        and _decimal(artifact.get("derating_factor"))
        == _raw_known_quantity(derating.get("factor"), "1")
        and _decimal(artifact.get("efficiency_factor"))
        == _raw_known_quantity(efficiency.get("factor"), "1")
    )
    artifact_loads = artifact.get("loads_A")
    input_loads: Dict[str, Mapping[str, Any]] = {}
    if isinstance(loads, list):
        for load in loads:
            if (
                not isinstance(load, dict)
                or not isinstance(load.get("load_id"), str)
                or load["load_id"] in input_loads
            ):
                identity_and_scalar_match = False
                continue
            input_loads[load["load_id"]] = load
    if not isinstance(artifact_loads, dict) or set(artifact_loads) != set(input_loads):
        identity_and_scalar_match = False
    else:
        for load_id, load in input_loads.items():
            artifact_load = artifact_loads.get(load_id)
            currents = load.get("currents")
            if (
                not isinstance(artifact_load, dict)
                or not isinstance(currents, dict)
                or artifact_load.get("rail_id") != load.get("rail_id")
                or artifact_load.get("backfeed_permitted") is not load.get("backfeed_permitted")
                or any(
                    _decimal(artifact_load.get(profile))
                    != _raw_known_quantity(currents.get(profile), "A")
                    for profile in ("Imin", "Ityp", "Imax")
                )
            ):
                identity_and_scalar_match = False
                break
    if not identity_and_scalar_match:
        return _check(
            RAIL_SOURCE_BINDING_RULE,
            "BLOCKED",
            "RAIL_ARTIFACT_BINDING_MISMATCH",
            "rail identity, voltage, loads, capacity, derating, and efficiency must exactly match the byte-bound RAIL_BUDGET artifact",
            tuple(verified_refs),
        )
    return _check(
        RAIL_SOURCE_BINDING_RULE,
        "PASS",
        "RAIL_SOURCE_BINDING_VERIFIED",
        "every rail-budget entity and value exactly matches its byte-verified outer source and RAIL_BUDGET artifact",
        tuple(verified_refs),
    )


def _closed_range_check(
    rule_id: str,
    observed: _Measure,
    minimum: Optional[_Measure],
    maximum: _Measure,
    gate: Tuple[str, str, str, Tuple[str, ...]],
    fail_reason: str,
) -> Dict[str, Any]:
    if gate[0] != "PASS":
        return _source_blocked_check(rule_id, gate)
    if observed.state != "KNOWN":
        return _check(rule_id, "UNKNOWN", "OPERATING_VALUE_UNKNOWN", "operating value is unknown", gate[3], observed=_quantity_output(None, observed.unit))
    if maximum.state != "KNOWN" or (minimum is not None and minimum.state != "KNOWN"):
        return _check(rule_id, "BLOCKED", "SOURCE_LIMIT_UNKNOWN", "required sourced limit is unknown", gate[3], observed=_quantity_output(observed.value, observed.unit))
    assert observed.value is not None and maximum.value is not None
    inside = observed.value <= maximum.value
    if minimum is not None:
        assert minimum.value is not None
        inside = minimum.value <= observed.value <= maximum.value
    limits = {"maximum": _quantity_output(maximum.value, maximum.unit)}
    if minimum is not None:
        limits["minimum"] = _quantity_output(minimum.value, minimum.unit)
    if inside:
        return _check(rule_id, "PASS", "LIMIT_SATISFIED", "operating value is inside the sourced closed limit", gate[3], observed=_quantity_output(observed.value, observed.unit), limits=limits)
    return _check(rule_id, "FAIL", fail_reason, "operating value is outside the sourced closed limit", gate[3], observed=_quantity_output(observed.value, observed.unit), limits=limits)


def _interpolated_limit(points: Sequence[Tuple[Decimal, Decimal]], temperature: Decimal) -> Optional[Decimal]:
    for point_temp, point_limit in points:
        if temperature == point_temp:
            return point_limit
    for index in range(len(points) - 1):
        left_temp, left_limit = points[index]
        right_temp, right_limit = points[index + 1]
        if left_temp <= temperature <= right_temp:
            with localcontext() as context:
                context.prec = 50
                ratio = (temperature - left_temp) / (right_temp - left_temp)
                return left_limit + ratio * (right_limit - left_limit)
    return None


def _analysis_check(
    rule_id: str,
    record: Mapping[str, Any],
    expected_kinds: Iterable[str],
    evidence_role: str,
    sources: Mapping[str, Mapping[str, Any]],
    regulator: Mapping[str, Any],
    model_ready: bool,
    model_applicable: bool,
) -> Dict[str, Any]:
    status = record.get("execution_status")
    refs = record.get("source_refs", ())
    if status == "NOT_RUN":
        return _check(rule_id, "NOT_RUN", "ANALYSIS_NOT_RUN", "analysis was not executed", ())
    if status in {"BLOCKED", "UNKNOWN"}:
        reason = "STABILITY_EVIDENCE_MISSING" if rule_id == STABILITY_RULE else "TRANSIENT_EVIDENCE_MISSING"
        return _check(rule_id, status, reason, "analysis evidence is not available", ())
    if not model_ready:
        return _check(rule_id, "BLOCKED", "MODEL_PROVENANCE_BLOCKED", "analysis cannot be accepted without usable model provenance", ())
    if not model_applicable:
        return _check(rule_id, "BLOCKED", "MODEL_NOT_APPLICABLE", "analysis cannot be accepted outside model validity bounds", ())
    kind = record.get("evidence_kind")
    if kind not in frozenset(expected_kinds):
        reason = "TRANSIENT_NOT_STABILITY_EVIDENCE" if rule_id == STABILITY_RULE else "TRANSIENT_EVIDENCE_KIND_INVALID"
        return _check(rule_id, "BLOCKED", reason, "evidence kind does not satisfy this rule", ())
    gate = _gate_sources(tuple(refs) if isinstance(refs, tuple) else (), sources, regulator, evidence_role, manufacturer_only=False)
    if gate[0] != "PASS":
        return _source_blocked_check(rule_id, gate)
    result_hash = record.get("result_hash")
    if not isinstance(result_hash, _HashRecord) or result_hash.state != "KNOWN":
        return _check(rule_id, "BLOCKED", "ANALYSIS_HASH_UNKNOWN", "analysis result has no known SHA-256", gate[3])
    source_hashes = {
        sources[ref]["_hash"].value
        for ref in gate[3]
        if isinstance(sources[ref].get("_hash"), _HashRecord)
    }
    if result_hash.value not in source_hashes:
        return _check(rule_id, "BLOCKED", "ANALYSIS_HASH_MISMATCH", "analysis result hash does not match its evidence source", gate[3])
    tool = record.get("tool", {})
    if tool.get("state") != "KNOWN" or not tool.get("name") or not tool.get("version") or _is_floating_version(str(tool.get("version"))):
        return _check(rule_id, "BLOCKED", "ANALYSIS_TOOL_UNVERIFIED", "analysis tool name and pinned version are required", gate[3])
    gate = _analysis_artifact_binding_gate(gate, sources, record, regulator)
    if gate[0] != "PASS":
        return _source_blocked_check(rule_id, gate)
    if status == "FAIL":
        return _check(rule_id, "FAIL", "RECORDED_ANALYSIS_FAILED", "provenance-bound analysis record reports FAIL", gate[3])
    if status != "PASS":
        return _check(rule_id, "BLOCKED", "ANALYSIS_STATUS_INVALID", "analysis status is not usable", gate[3])
    reason = "STABILITY_EVIDENCE_PROVENANCE_VERIFIED" if rule_id == STABILITY_RULE else "TRANSIENT_EVIDENCE_PROVENANCE_VERIFIED"
    return _check(rule_id, "PASS", reason, "analysis record and provenance are complete for this evidence rule", gate[3])


def _evidence_separation_check(
    transient: Mapping[str, Any],
    stability: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    transient_refs = transient.get("source_refs")
    stability_refs = stability.get("source_refs")
    if not isinstance(transient_refs, tuple) or not isinstance(stability_refs, tuple):
        return None
    transient_sources = [sources[ref] for ref in transient_refs if ref in sources]
    stability_sources = [sources[ref] for ref in stability_refs if ref in sources]
    transient_locators = {item.get("locator") for item in transient_sources if isinstance(item.get("locator"), str)}
    stability_locators = {item.get("locator") for item in stability_sources if isinstance(item.get("locator"), str)}
    all_refs = tuple(sorted(set(transient_refs + stability_refs)))
    if transient_locators & stability_locators:
        return _check(
            STABILITY_RULE,
            "BLOCKED",
            "TRANSIENT_STABILITY_LOCATOR_REUSED",
            "loop-stability evidence must not reuse a transient evidence locator",
            all_refs,
        )
    transient_hashes = {
        digest.value
        for item in transient_sources
        for digest in (item.get("_hash"),)
        if item.get("_byte_verified") is True
        and isinstance(digest, _HashRecord)
        and digest.state == "KNOWN"
        and digest.value is not None
    }
    stability_hashes = {
        digest.value
        for item in stability_sources
        for digest in (item.get("_hash"),)
        if item.get("_byte_verified") is True
        and isinstance(digest, _HashRecord)
        and digest.state == "KNOWN"
        and digest.value is not None
    }
    if transient_hashes & stability_hashes:
        return _check(
            STABILITY_RULE,
            "BLOCKED",
            "TRANSIENT_STABILITY_BYTES_REUSED",
            "loop-stability evidence must not reuse the exact transient evidence bytes",
            all_refs,
        )
    return None


def _base_result(input_hash: Optional[str]) -> Dict[str, Any]:
    try:
        rule_source = _rule_source()
        sources = [rule_source]
        source_refs = [RULE_SOURCE_ID]
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        sources = []
        source_refs = []
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "component_id": "REP02-REGULATOR-REVIEW",
        "rule_id": RULE_ID,
        "engine_version": ENGINE_VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "tool_name": TOOL_NAME,
        "tool_version": ENGINE_VERSION,
        "package_version": PACKAGE_VERSION,
        "status": "BLOCKED",
        "reason_code": "INPUT_VALIDATION_BLOCKED",
        "reason_codes": ["INPUT_VALIDATION_BLOCKED"],
        "input_hash": input_hash,
        "source_refs": source_refs,
        "sources": sources,
        "review_subject": {},
        "rail_budget_result": None,
        "checks": [],
        "issues": [],
        "model_records": [],
        "source_requirements": [],
        "evidence_gaps": [],
        "limitations": [
            "MODULE_LOCAL_REVIEW_RESULT_NOT_D1_RESPONSE",
            "STATIC_AND_RECORDED_EVIDENCE_REVIEW_ONLY",
            "TRANSIENT_EVIDENCE_IS_NOT_LOOP_STABILITY_EVIDENCE",
            "NO_PHYSICAL_THERMAL_EMC_ISO_AEC_OR_FIELD_VALIDATION",
            "LIVE_REP01_REP03_ADAPTERS_NOT_RUN",
        ],
        "claim_limits": [
            "NO_EXACT_PART_APPROVAL_FROM_SOFTWARE_RESULT_ALONE",
            "NO_SPICE_EXECUTION_BY_REGULATOR_REVIEW_ENGINE",
            "NO_LOOP_STABILITY_INFERENCE_FROM_TRANSIENT",
            "NO_HARDWARE_GATE_CHANGE",
        ],
        "hash_policy": {
            "algorithm": "SHA-256",
            "canonicalization": "REP02_SORTED_MINIFIED_UTF8_JSON",
            "input_scope": "ENTIRE_MODULE_LOCAL_REVIEW_DOCUMENT",
        },
    }


def _project_claim_limits(result: Dict[str, Any]) -> None:
    claim_limits = sorted(set(result.get("claim_limits", [])))
    limitations = list(result.get("limitations", []))
    limitations.extend(f"CLAIM_LIMIT:{value}" for value in claim_limits)
    result["claim_limits"] = claim_limits
    result["limitations"] = sorted(set(limitations))


def _review(
    document: Any,
    input_hash: Optional[str],
    artifact_root_value: Optional[Union[str, Path]],
) -> Dict[str, Any]:
    result = _base_result(input_hash)
    issues: List[_Issue] = []
    artifact_root = _artifact_root(artifact_root_value, issues)
    root = _mapping(document, "$", issues)
    allowed = {"schema_version", "review_id", "fixture_scope", "regulator", "operating_point", "rail_budget_input", "limits", "sources", "model", "analyses"}
    _reject(root, allowed, "$", issues)
    _require(root, allowed, "$", issues)
    if root.get("schema_version") != "1.0.0":
        _add(issues, "BLOCKED", "SCHEMA_VERSION_UNSUPPORTED", "$.schema_version", "expected regulator-review schema version 1.0.0")
    review_id = _identifier(root.get("review_id"), "$.review_id", issues)
    fixture_scope = _enum(root.get("fixture_scope"), {"SYNTHETIC", "PROJECT"}, "$.fixture_scope", issues, "FIXTURE_SCOPE_INVALID")
    if review_id is not None:
        result["component_id"] = f"REP02.REGULATOR.REVIEW:{review_id}"

    regulator_raw = _mapping(root.get("regulator"), "$.regulator", issues)
    regulator_fields = {"regulator_id", "manufacturer", "part_number", "topology", "output_rail_id", "source_refs"}
    _reject(regulator_raw, regulator_fields, "$.regulator", issues)
    _require(regulator_raw, regulator_fields, "$.regulator", issues)
    regulator: Dict[str, Any] = {
        "regulator_id": _identifier(regulator_raw.get("regulator_id"), "$.regulator.regulator_id", issues),
        "manufacturer": _text(regulator_raw.get("manufacturer"), "$.regulator.manufacturer", issues),
        "part_number": _text(regulator_raw.get("part_number"), "$.regulator.part_number", issues),
        "topology": _enum(regulator_raw.get("topology"), {"BUCK", "LDO"}, "$.regulator.topology", issues, "TOPOLOGY_INVALID"),
        "output_rail_id": _identifier(regulator_raw.get("output_rail_id"), "$.regulator.output_rail_id", issues),
        "source_refs": _refs(regulator_raw.get("source_refs"), "$.regulator.source_refs", issues),
    }
    result["review_subject"] = {
        "review_id": review_id,
        "fixture_scope": fixture_scope,
        **{key: value for key, value in regulator.items() if key != "source_refs"},
        "source_refs": list(regulator["source_refs"]),
    }
    if fixture_scope == "SYNTHETIC":
        result["limitations"].append("SYNTHETIC_FIXTURE_ONLY")
        result["claim_limits"].append("SYNTHETIC_PASS_IS_NOT_PROJECT_OR_HARDWARE_PASS")
    elif fixture_scope == "PROJECT":
        result["limitations"].append("PROJECT_SCOPE_LIVE_INTEGRATION_NOT_VERIFIED")
        result["claim_limits"].append("PROJECT_SCOPE_CANNOT_PASS_WITHOUT_VERIFIED_LIVE_INTEGRATION")
        _add(
            issues,
            "BLOCKED",
            "PROJECT_SCOPE_LIVE_BINDING_UNAVAILABLE",
            "$.fixture_scope",
            "PROJECT scope cannot PASS because this version has no verified live-integration binding",
        )

    operating_raw = _mapping(root.get("operating_point"), "$.operating_point", issues)
    operating_fields = {"input_voltage", "output_voltage", "ambient_temperature", "junction_temperature"}
    _reject(operating_raw, operating_fields, "$.operating_point", issues)
    _require(operating_raw, operating_fields, "$.operating_point", issues)
    operating = {
        "input_voltage": _measure(operating_raw.get("input_voltage"), "V", "$.operating_point.input_voltage", issues),
        "output_voltage": _measure(operating_raw.get("output_voltage"), "V", "$.operating_point.output_voltage", issues),
        "ambient_temperature": _measure(operating_raw.get("ambient_temperature"), "degC", "$.operating_point.ambient_temperature", issues),
        "junction_temperature": _measure(operating_raw.get("junction_temperature"), "degC", "$.operating_point.junction_temperature", issues),
    }

    sources = _source_index(root.get("sources"), issues, artifact_root)

    limits_raw = _mapping(root.get("limits"), "$.limits", issues)
    _reject(limits_raw, {"absolute_maximum", "recommended_operating", "derating"}, "$.limits", issues)
    _require(limits_raw, {"absolute_maximum", "recommended_operating", "derating"}, "$.limits", issues)
    absolute_raw = _mapping(limits_raw.get("absolute_maximum"), "$.limits.absolute_maximum", issues)
    absolute_fields = {"input_voltage_max", "output_current_max", "junction_temperature_max", "source_refs"}
    _reject(absolute_raw, absolute_fields, "$.limits.absolute_maximum", issues)
    _require(absolute_raw, absolute_fields, "$.limits.absolute_maximum", issues)
    absolute = {
        "input_voltage_max": _measure(absolute_raw.get("input_voltage_max"), "V", "$.limits.absolute_maximum.input_voltage_max", issues),
        "output_current_max": _measure(absolute_raw.get("output_current_max"), "A", "$.limits.absolute_maximum.output_current_max", issues),
        "junction_temperature_max": _measure(absolute_raw.get("junction_temperature_max"), "degC", "$.limits.absolute_maximum.junction_temperature_max", issues),
        "source_refs": _refs(absolute_raw.get("source_refs"), "$.limits.absolute_maximum.source_refs", issues),
    }
    recommended_raw = _mapping(limits_raw.get("recommended_operating"), "$.limits.recommended_operating", issues)
    recommended_fields = {"input_voltage_min", "input_voltage_max", "output_current_max", "ambient_temperature_min", "ambient_temperature_max", "source_refs"}
    _reject(recommended_raw, recommended_fields, "$.limits.recommended_operating", issues)
    _require(recommended_raw, recommended_fields, "$.limits.recommended_operating", issues)
    recommended = {
        "input_voltage_min": _measure(recommended_raw.get("input_voltage_min"), "V", "$.limits.recommended_operating.input_voltage_min", issues),
        "input_voltage_max": _measure(recommended_raw.get("input_voltage_max"), "V", "$.limits.recommended_operating.input_voltage_max", issues),
        "output_current_max": _measure(recommended_raw.get("output_current_max"), "A", "$.limits.recommended_operating.output_current_max", issues),
        "ambient_temperature_min": _measure(recommended_raw.get("ambient_temperature_min"), "degC", "$.limits.recommended_operating.ambient_temperature_min", issues),
        "ambient_temperature_max": _measure(recommended_raw.get("ambient_temperature_max"), "degC", "$.limits.recommended_operating.ambient_temperature_max", issues),
        "source_refs": _refs(recommended_raw.get("source_refs"), "$.limits.recommended_operating.source_refs", issues),
    }
    derating_raw = _mapping(limits_raw.get("derating"), "$.limits.derating", issues)
    _reject(derating_raw, {"points", "source_refs", "interpolation"}, "$.limits.derating", issues)
    _require(derating_raw, {"points", "source_refs", "interpolation"}, "$.limits.derating", issues)
    derating_curve_valid = True
    if derating_raw.get("interpolation") != "PIECEWISE_LINEAR_CLOSED":
        _add(issues, "BLOCKED", "DERATING_INTERPOLATION_UNSUPPORTED", "$.limits.derating.interpolation", "expected PIECEWISE_LINEAR_CLOSED")
        derating_curve_valid = False
    derating_refs = _refs(derating_raw.get("source_refs"), "$.limits.derating.source_refs", issues)
    points: List[Tuple[Decimal, Decimal]] = []
    raw_points = derating_raw.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        _add(issues, "BLOCKED", "DERATING_POINTS_REQUIRED", "$.limits.derating.points", "at least two derating points are required")
        derating_curve_valid = False
    else:
        for index, raw_point in enumerate(raw_points):
            point_path = f"$.limits.derating.points[{index}]"
            point_issue_count = len(issues)
            point = _mapping(raw_point, point_path, issues)
            _reject(point, {"ambient_temperature", "maximum_output_current"}, point_path, issues)
            _require(point, {"ambient_temperature", "maximum_output_current"}, point_path, issues)
            temp = _measure(point.get("ambient_temperature"), "degC", f"{point_path}.ambient_temperature", issues)
            limit = _measure(point.get("maximum_output_current"), "A", f"{point_path}.maximum_output_current", issues)
            if temp.state == "KNOWN" and limit.state == "KNOWN" and temp.value is not None and limit.value is not None:
                if limit.value < 0:
                    _add(issues, "BLOCKED", "NEGATIVE_DERATING_LIMIT", f"{point_path}.maximum_output_current.value", "derating current must be non-negative")
                    derating_curve_valid = False
                points.append((temp.value, limit.value))
            else:
                _add(
                    issues,
                    "BLOCKED",
                    "DERATING_POINT_NOT_KNOWN",
                    point_path,
                    "every declared derating temperature and current must be KNOWN and valid",
                    DERATING_RULE,
                    derating_refs,
                )
                derating_curve_valid = False
            if len(issues) != point_issue_count:
                derating_curve_valid = False
        if len(points) >= 2 and any(points[index][0] >= points[index + 1][0] for index in range(len(points) - 1)):
            _add(issues, "BLOCKED", "DERATING_TEMPERATURE_ORDER_INVALID", "$.limits.derating.points", "derating temperatures must be strictly increasing")
            derating_curve_valid = False

    rail_input = root.get("rail_budget_input")
    rail_result = calculate_rail_budget(rail_input)
    result["rail_budget_result"] = rail_result
    rail_status = rail_result.get("status") if rail_result.get("status") in _STATUS_ORDER else "BLOCKED"
    checks: List[Dict[str, Any]] = [
        _check(
            RAIL_BUDGET_RULE,
            rail_status,
            "RAIL_BUDGET_" + rail_status,
            f"public rail-budget engine returned {rail_status}",
            (),
        )
    ]
    binding_ok = False
    if isinstance(rail_input, dict):
        capacity = rail_input.get("capacity") if isinstance(rail_input.get("capacity"), dict) else {}
        rail = rail_input.get("rail") if isinstance(rail_input.get("rail"), dict) else {}
        binding_ok = capacity.get("regulator_id") == regulator.get("regulator_id") and rail.get("rail_id") == regulator.get("output_rail_id") and capacity.get("rail_id") == regulator.get("output_rail_id")
    checks.append(
        _check(
            BINDING_RULE,
            "PASS" if binding_ok else "BLOCKED",
            "RAIL_BUDGET_BINDING_VERIFIED" if binding_ok else "RAIL_BUDGET_BINDING_MISMATCH",
            "rail-budget regulator and output rail match the review subject" if binding_ok else "rail-budget input is not bound to the exact regulator and output rail",
            (),
        )
    )
    checks.append(_output_voltage_binding_check(operating["output_voltage"], rail_input))
    checks.append(_rail_source_binding_check(rail_input, rail_result, sources, regulator))
    current = _Measure("UNKNOWN", None, "A")
    metrics = rail_result.get("metrics")
    if isinstance(metrics, dict):
        total = metrics.get("total_current")
        maximum = total.get("Imax") if isinstance(total, dict) else None
        if isinstance(maximum, dict) and maximum.get("state") == "KNOWN" and maximum.get("unit") == "A":
            current_value = _decimal(maximum.get("value"))
            if current_value is not None:
                current = _Measure("KNOWN", abs(current_value), "A")

    exact_gate = _datasheet_binding_gate(
        _gate_sources(regulator["source_refs"], sources, regulator, "DATASHEET", manufacturer_only=True),
        sources,
        regulator,
    )
    checks.append(
        _check(EXACT_SOURCE_RULE, exact_gate[0], "EXACT_PART_SOURCE_VERIFIED" if exact_gate[0] == "PASS" else exact_gate[1], exact_gate[2], exact_gate[3])
    )
    abs_gate = _datasheet_binding_gate(
        _gate_sources(absolute["source_refs"], sources, regulator, "DATASHEET", manufacturer_only=True),
        sources,
        regulator,
        absolute=absolute,
    )
    rec_gate = _datasheet_binding_gate(
        _gate_sources(recommended["source_refs"], sources, regulator, "DATASHEET", manufacturer_only=True),
        sources,
        regulator,
        recommended=recommended,
    )
    derating_gate = _gate_sources(derating_refs, sources, regulator, "DATASHEET", manufacturer_only=True)
    if derating_curve_valid:
        derating_gate = _datasheet_binding_gate(
            derating_gate,
            sources,
            regulator,
            derating_points=points,
        )
    checks.extend(
        [
            _closed_range_check(VIN_ABS_RULE, operating["input_voltage"], None, absolute["input_voltage_max"], abs_gate, "ABS_MAX_EXCEEDED"),
            _closed_range_check(VIN_REC_RULE, operating["input_voltage"], recommended["input_voltage_min"], recommended["input_voltage_max"], rec_gate, "RECOMMENDED_RANGE_EXCEEDED"),
            _closed_range_check(IOUT_ABS_RULE, current, None, absolute["output_current_max"], abs_gate, "ABS_MAX_EXCEEDED"),
            _closed_range_check(IOUT_REC_RULE, current, None, recommended["output_current_max"], rec_gate, "RATED_IOUT_EXCEEDED"),
            _closed_range_check(TJ_ABS_RULE, operating["junction_temperature"], None, absolute["junction_temperature_max"], abs_gate, "ABS_MAX_EXCEEDED"),
            _closed_range_check(TA_REC_RULE, operating["ambient_temperature"], recommended["ambient_temperature_min"], recommended["ambient_temperature_max"], rec_gate, "RECOMMENDED_RANGE_EXCEEDED"),
        ]
    )
    if derating_gate[0] != "PASS":
        derating_check = _source_blocked_check(DERATING_RULE, derating_gate)
    elif not derating_curve_valid:
        derating_check = _check(
            DERATING_RULE,
            "BLOCKED",
            "DERATING_CURVE_INVALID",
            "derating cannot be evaluated because one or more declared curve points are unknown or invalid",
            derating_gate[3],
        )
    elif operating["ambient_temperature"].state != "KNOWN" or current.state != "KNOWN":
        derating_check = _check(DERATING_RULE, "UNKNOWN", "OPERATING_VALUE_UNKNOWN", "temperature or output current is unknown", derating_gate[3])
    else:
        assert operating["ambient_temperature"].value is not None and current.value is not None
        allowed_current = _interpolated_limit(points, operating["ambient_temperature"].value) if len(points) >= 2 else None
        if allowed_current is None:
            derating_check = _check(DERATING_RULE, "BLOCKED", "DERATING_CURVE_NOT_APPLICABLE", "derating curve does not cover the operating temperature", derating_gate[3], observed=_quantity_output(current.value, "A"))
        elif current.value <= allowed_current:
            derating_check = _check(DERATING_RULE, "PASS", "LIMIT_SATISFIED", "output current is inside the interpolated derating limit", derating_gate[3], observed=_quantity_output(current.value, "A"), limits={"derated_maximum": _quantity_output(allowed_current, "A")})
        else:
            derating_check = _check(DERATING_RULE, "FAIL", "DERATED_IOUT_EXCEEDED", "output current exceeds the interpolated derating limit", derating_gate[3], observed=_quantity_output(current.value, "A"), limits={"derated_maximum": _quantity_output(allowed_current, "A")})
    checks.append(derating_check)

    model_raw = _mapping(root.get("model"), "$.model", issues)
    model_fields = {"access_status", "model_id", "model_type", "manufacturer", "part_number", "version", "origin", "license_status", "file_hash", "validity_bounds", "source_refs"}
    _reject(model_raw, model_fields, "$.model", issues)
    _require(model_raw, model_fields, "$.model", issues)
    model: Dict[str, Any] = {
        "access_status": _enum(model_raw.get("access_status"), {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"}, "$.model.access_status", issues, "ACCESS_STATUS_INVALID"),
        "model_id": _identifier(model_raw.get("model_id"), "$.model.model_id", issues),
        "model_type": _enum(model_raw.get("model_type"), {"SPICE"}, "$.model.model_type", issues, "MODEL_TYPE_INVALID"),
        "manufacturer": _text(model_raw.get("manufacturer"), "$.model.manufacturer", issues),
        "part_number": _text(model_raw.get("part_number"), "$.model.part_number", issues),
        "version": _version(model_raw.get("version"), "$.model.version", issues),
        "origin": _enum(model_raw.get("origin"), {"MANUFACTURER", "THIRD_PARTY", "SYNTHETIC", "UNKNOWN"}, "$.model.origin", issues, "MODEL_ORIGIN_INVALID"),
        "license_status": _enum(model_raw.get("license_status"), {"VERIFIED", "DECLARED", "UNKNOWN", "CONFLICT", "RESTRICTED"}, "$.model.license_status", issues, "LICENSE_STATUS_INVALID"),
        "file_hash": _hash_record(model_raw.get("file_hash"), "$.model.file_hash", issues),
        "source_refs": _refs(model_raw.get("source_refs"), "$.model.source_refs", issues),
    }
    bounds_raw = _mapping(model_raw.get("validity_bounds"), "$.model.validity_bounds", issues)
    bound_fields = {"input_voltage_min", "input_voltage_max", "output_current_max", "ambient_temperature_min", "ambient_temperature_max"}
    _reject(bounds_raw, bound_fields, "$.model.validity_bounds", issues)
    _require(bounds_raw, bound_fields, "$.model.validity_bounds", issues)
    bounds = {
        "input_voltage_min": _measure(bounds_raw.get("input_voltage_min"), "V", "$.model.validity_bounds.input_voltage_min", issues),
        "input_voltage_max": _measure(bounds_raw.get("input_voltage_max"), "V", "$.model.validity_bounds.input_voltage_max", issues),
        "output_current_max": _measure(bounds_raw.get("output_current_max"), "A", "$.model.validity_bounds.output_current_max", issues),
        "ambient_temperature_min": _measure(bounds_raw.get("ambient_temperature_min"), "degC", "$.model.validity_bounds.ambient_temperature_min", issues),
        "ambient_temperature_max": _measure(bounds_raw.get("ambient_temperature_max"), "degC", "$.model.validity_bounds.ambient_temperature_max", issues),
    }
    model["validity_bounds"] = {
        name: _quantity_output(measure.value if measure.state == "KNOWN" else None, measure.unit)
        for name, measure in bounds.items()
    }
    result["model_records"] = [
        {
            **{key: value for key, value in model.items() if key not in {"file_hash", "source_refs"}},
            "source_refs": list(model["source_refs"]),
            "file_hash": {"state": model["file_hash"].state, "algorithm": "SHA-256", "value": model["file_hash"].value},
        }
    ]
    model_gate = _gate_sources(model["source_refs"], sources, regulator, "MODEL", manufacturer_only=True)
    model_status = "PASS"
    model_reason = "MODEL_PROVENANCE_VERIFIED"
    model_message = "model provenance is exact-part, manufacturer-origin, licensed, pinned, and hash-bound"
    if model.get("access_status") != "AVAILABLE":
        model_status, model_reason, model_message = "BLOCKED", "MODEL_MISSING", "model is not AVAILABLE"
    elif model.get("origin") != "MANUFACTURER":
        model_status, model_reason, model_message = "BLOCKED", "MODEL_ORIGIN_UNVERIFIED", "model origin is not MANUFACTURER"
    elif model.get("manufacturer") != regulator.get("manufacturer") or model.get("part_number") != regulator.get("part_number"):
        model_status, model_reason, model_message = "BLOCKED", "MODEL_PART_MISMATCH", "model does not match the exact review subject"
    elif model.get("license_status") not in {"VERIFIED", "DECLARED"}:
        model_status, model_reason, model_message = "BLOCKED", "MODEL_LICENSE_UNVERIFIED", "model license status is not usable"
    elif not isinstance(model.get("file_hash"), _HashRecord) or model["file_hash"].state != "KNOWN":
        model_status, model_reason, model_message = "BLOCKED", "MODEL_HASH_UNKNOWN", "model file hash is unknown"
    elif model_gate[0] != "PASS":
        model_status, model_reason, model_message = model_gate[0], model_gate[1], model_gate[2]
    else:
        version_mismatch = any(
            model.get("version") != sources[ref].get("version")
            or model.get("version") != sources[ref].get("document_revision")
            for ref in model_gate[3]
        )
        if version_mismatch:
            model_status, model_reason, model_message = (
                "BLOCKED",
                "MODEL_VERSION_MISMATCH",
                "model version must exactly match source version and document revision",
            )
        else:
            source_hashes = {
                sources[ref]["_hash"].value
                for ref in model_gate[3]
                if isinstance(sources[ref].get("_hash"), _HashRecord)
            }
        if model_status == "PASS" and model["file_hash"].value not in source_hashes:
            model_status, model_reason, model_message = "BLOCKED", "MODEL_HASH_MISMATCH", "model file hash does not match the model source"
    if model_status == "PASS":
        artifact_gate = _model_artifact_binding_gate(model_gate, sources, model)
        if artifact_gate[0] != "PASS":
            model_status, model_reason, model_message = artifact_gate[0], artifact_gate[1], artifact_gate[2]
    bounds_gate: Tuple[str, str, str, Tuple[str, ...]] = (
        "PASS",
        "MODEL_BOUNDS_NOT_READY",
        "model bounds are not ready for source binding",
        (),
    )
    if all(measure.state == "KNOWN" and measure.value is not None for measure in bounds.values()):
        bounds_gate = _model_bounds_gate(bounds, rec_gate, sources)
        if model_status == "PASS" and bounds_gate[0] != "PASS":
            model_status, model_reason, model_message = bounds_gate[0], bounds_gate[1], bounds_gate[2]
    model_refs = tuple(sorted(set(model_gate[3] + bounds_gate[3])))
    checks.append(_check(MODEL_PROVENANCE_RULE, model_status, model_reason, model_message, model_refs))

    model_app_status = "PASS"
    model_app_reason = "MODEL_APPLICABLE"
    model_app_message = "operating point is inside all declared model validity bounds"
    if model_status != "PASS":
        model_app_status, model_app_reason, model_app_message = "BLOCKED", "MODEL_PROVENANCE_BLOCKED", "model applicability cannot be decided before provenance passes"
    elif any(measure.state != "KNOWN" for measure in bounds.values()):
        model_app_status, model_app_reason, model_app_message = "BLOCKED", "MODEL_BOUNDS_MISSING", "model validity bounds are incomplete"
    elif operating["input_voltage"].state != "KNOWN" or operating["ambient_temperature"].state != "KNOWN" or current.state != "KNOWN":
        model_app_status, model_app_reason, model_app_message = "UNKNOWN", "OPERATING_VALUE_UNKNOWN", "model applicability input is unknown"
    else:
        assert operating["input_voltage"].value is not None and operating["ambient_temperature"].value is not None and current.value is not None
        assert all(measure.value is not None for measure in bounds.values())
        inside = (
            bounds["input_voltage_min"].value <= operating["input_voltage"].value <= bounds["input_voltage_max"].value
            and current.value <= bounds["output_current_max"].value
            and bounds["ambient_temperature_min"].value <= operating["ambient_temperature"].value <= bounds["ambient_temperature_max"].value
        )
        if not inside:
            model_app_status, model_app_reason, model_app_message = "FAIL", "MODEL_OUT_OF_BOUNDS", "operating point is outside declared model validity bounds"
    checks.append(_check(MODEL_APPLICABILITY_RULE, model_app_status, model_app_reason, model_app_message, model_refs))

    analyses_raw = _mapping(root.get("analyses"), "$.analyses", issues)
    _reject(analyses_raw, {"transient", "loop_stability"}, "$.analyses", issues)
    _require(analyses_raw, {"transient", "loop_stability"}, "$.analyses", issues)

    def parse_analysis(name: str) -> Dict[str, Any]:
        path = f"$.analyses.{name}"
        raw = _mapping(analyses_raw.get(name), path, issues)
        fields = {"execution_status", "evidence_kind", "result_hash", "source_refs", "tool"}
        _reject(raw, fields, path, issues)
        _require(raw, fields, path, issues)
        tool_raw = _mapping(raw.get("tool"), f"{path}.tool", issues)
        _reject(tool_raw, {"state", "name", "version"}, f"{path}.tool", issues)
        _require(tool_raw, {"state", "name", "version"}, f"{path}.tool", issues)
        tool_state = _enum(tool_raw.get("state"), {"KNOWN", "UNKNOWN"}, f"{path}.tool.state", issues, "TOOL_STATE_INVALID")
        tool_name = tool_raw.get("name")
        tool_version = tool_raw.get("version")
        if tool_state == "KNOWN":
            tool_name = _text(tool_name, f"{path}.tool.name", issues)
            tool_version = _version(tool_version, f"{path}.tool.version", issues)
        elif tool_name is not None or tool_version is not None:
            _add(issues, "BLOCKED", "UNKNOWN_TOOL_MUST_BE_NULL", f"{path}.tool", "UNKNOWN tool requires null name and version")
        return {
            "execution_status": _enum(raw.get("execution_status"), set(_STATUS_ORDER), f"{path}.execution_status", issues, "ANALYSIS_STATUS_INVALID"),
            "evidence_kind": _text(raw.get("evidence_kind"), f"{path}.evidence_kind", issues),
            "result_hash": _hash_record(raw.get("result_hash"), f"{path}.result_hash", issues),
            "source_refs": _refs(raw.get("source_refs"), f"{path}.source_refs", issues),
            "tool": {"state": tool_state, "name": tool_name, "version": tool_version},
        }

    transient = parse_analysis("transient")
    stability = parse_analysis("loop_stability")
    transient_check = _analysis_check(TRANSIENT_RULE, transient, {"TIME_DOMAIN_TRANSIENT"}, "TRANSIENT_RESULT", sources, regulator, model_status == "PASS", model_app_status == "PASS")
    stability_check = _analysis_check(STABILITY_RULE, stability, {"LOOP_GAIN_AC", "PHASE_GAIN_MARGIN"}, "STABILITY_RESULT", sources, regulator, model_status == "PASS", model_app_status == "PASS")
    separation_check = _evidence_separation_check(transient, stability, sources)
    checks.append(transient_check)
    checks.append(separation_check if separation_check is not None else stability_check)

    requested_refs: List[str] = []
    for refs in (
        regulator["source_refs"], absolute["source_refs"], recommended["source_refs"], derating_refs,
        model["source_refs"], transient["source_refs"], stability["source_refs"],
    ):
        requested_refs.extend(refs)
    rail_source_refs = rail_result.get("source_refs")
    if isinstance(rail_source_refs, list):
        requested_refs.extend(
            item["source_id"]
            for item in rail_source_refs
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        )
    result["source_requirements"] = _source_requirements(sources, requested_refs)
    result["evidence_gaps"] = [
        {
            "required_source_id": record["source_id"],
            "status": "BLOCKED",
            "reason_code": (
                "SOURCE_UNAVAILABLE"
                if record["resolution"] == "UNRESOLVED" or record["access_status"] != "AVAILABLE"
                else "SOURCE_HASH_UNKNOWN"
                if record["hash_state"] != "KNOWN"
                else "SOURCE_BYTES_UNVERIFIED"
            ),
        }
        for record in result["source_requirements"]
        if (
            record["resolution"] != "RESOLVED"
            or record["access_status"] != "AVAILABLE"
            or record["hash_state"] != "KNOWN"
            or record["byte_verification"] != "VERIFIED"
        )
    ]
    result["sources"] = _available_source_output(sources)
    available_ids = {item["source_id"] for item in result["sources"]}
    result["source_refs"] = sorted(available_ids)

    for check in checks:
        if check["status"] != "PASS":
            issues.append(
                _Issue(
                    check["status"],
                    check["reason_code"],
                    "$.checks." + check["rule_id"],
                    check["message"],
                    check["rule_id"],
                    tuple(check.get("source_refs", ())),
                )
            )
    issues = sorted(
        issues,
        key=lambda issue: (
            issue.path,
            issue.rule_id,
            issue.reason_code,
            issue.status,
            issue.message,
            issue.source_refs,
        ),
    )
    issue_statuses = [issue.status for issue in issues]
    check_statuses = [check["status"] for check in checks]
    statuses = issue_statuses + check_statuses
    overall = max(statuses, key=lambda status: _STATUS_RANK[status]) if statuses else "PASS"
    if overall == "PASS":
        reason_codes = ["REGULATOR_REVIEW_COMPLETE"]
    else:
        reason_codes = []
        for issue in issues:
            if issue.status == overall and issue.reason_code not in reason_codes:
                reason_codes.append(issue.reason_code)
        if not reason_codes:
            reason_codes = ["INPUT_VALIDATION_BLOCKED"]
    result["status"] = overall
    result["reason_codes"] = reason_codes
    result["reason_code"] = reason_codes[0]
    result["checks"] = checks
    result["issues"] = [issue.as_dict(available_ids) for issue in issues]
    _project_claim_limits(result)
    return result


def _json_result_copy(value: Mapping[str, Any]) -> Dict[str, Any]:
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("regulator result must be an object")
    return decoded


def _container_graph_problem(value: Any) -> Optional[Tuple[str, str, str]]:
    seen: Dict[int, str] = {}
    active: Dict[int, str] = {}

    def visit(item: Any, path: str) -> Optional[Tuple[str, str, str]]:
        if not isinstance(item, (dict, list)):
            return None
        identity = id(item)
        if identity in active:
            return "CYCLE", path, active[identity]
        if identity in seen:
            return "ALIAS", path, seen[identity]
        seen[identity] = path
        active[identity] = path
        try:
            if isinstance(item, list):
                for index, child in enumerate(item):
                    problem = visit(child, f"{path}[{index}]")
                    if problem is not None:
                        return problem
            else:
                for key in sorted(item, key=repr):
                    problem = visit(item[key], f"{path}.{key!s}")
                    if problem is not None:
                        return problem
        finally:
            active.pop(identity, None)
        return None

    return visit(value, "$")


def review_regulator(
    document: Any,
    *,
    artifact_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Review one module-local regulator document without mutating it.

    ``artifact_root`` is optional at the call boundary for compatibility, but
    every outer source that claims ``AVAILABLE`` is accepted only when its
    locator is a safe relative POSIX path beneath that root and its real file
    bytes match the declared SHA-256.  Omitting or invalidating the root fails
    closed.  Nested rail-budget sources are exactly bound to those verified
    outer records.

    Invalid, cyclic, non-finite, aliased, or otherwise unrepresentable input
    fails closed.  The returned value is a detached, finite JSON object.  A
    synthetic fixture may demonstrate a rule-level PASS, but ``PROJECT`` scope
    cannot PASS in this engine version because no live integration binding is
    implemented; no result changes any G1-G6 hardware gate.
    """

    try:
        graph_problem = _container_graph_problem(document)
    except RecursionError:
        graph_problem = ("CYCLE", "$", "$")
    if graph_problem is not None and graph_problem[0] == "ALIAS":
        result = _base_result(None)
        result["status"] = "BLOCKED"
        result["reason_code"] = "INPUT_GRAPH_ALIAS_FORBIDDEN"
        result["reason_codes"] = ["INPUT_GRAPH_ALIAS_FORBIDDEN"]
        available = {item["source_id"] for item in result["sources"]}
        result["issues"] = [
            _Issue(
                "BLOCKED",
                "INPUT_GRAPH_ALIAS_FORBIDDEN",
                graph_problem[1],
                f"input container aliases {graph_problem[2]}; JSON review input must be a detached tree",
            ).as_dict(available)
        ]
        _project_claim_limits(result)
        return _json_result_copy(result)

    try:
        input_hash: Optional[str] = sha256_hexdigest(document)
    except (RecursionError, TypeError, ValueError, OverflowError):
        input_hash = None
    try:
        result = _review(document, input_hash, artifact_root)
    except (ArithmeticError, InvalidOperation, OSError, OverflowError, RecursionError, TypeError, ValueError, KeyError):
        result = _base_result(input_hash)
        issue = _Issue("BLOCKED", "INPUT_VALIDATION_BLOCKED", "$", "input could not be safely evaluated")
        available = {item["source_id"] for item in result["sources"]}
        result["issues"] = [issue.as_dict(available)]
    if input_hash is None:
        available = {item["source_id"] for item in result["sources"]}
        issue = _Issue("BLOCKED", "INPUT_HASH_UNCOMPUTABLE", "$", "canonical hash could not be computed for the original input")
        result["input_hash"] = None
        result["status"] = "BLOCKED"
        result["reason_code"] = "INPUT_HASH_UNCOMPUTABLE"
        result["reason_codes"] = ["INPUT_HASH_UNCOMPUTABLE"]
        result["issues"] = list(result.get("issues", [])) + [issue.as_dict(available)]
    _project_claim_limits(result)
    try:
        return _json_result_copy(result)
    except (RecursionError, TypeError, ValueError, OverflowError):
        fallback = _base_result(input_hash)
        fallback["status"] = "BLOCKED"
        fallback["reason_code"] = "RESULT_SANITIZATION_BLOCKED"
        fallback["reason_codes"] = ["RESULT_SANITIZATION_BLOCKED"]
        available = {item["source_id"] for item in fallback["sources"]}
        fallback["issues"] = [
            _Issue(
                "BLOCKED",
                "RESULT_SANITIZATION_BLOCKED",
                "$",
                "result could not be detached into a finite JSON object",
            ).as_dict(available)
        ]
        _project_claim_limits(fallback)
        return _json_result_copy(fallback)
