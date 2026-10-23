"""Fail-closed, offline validation for the WF-02-06 file API.

The validator checks a descriptor plus, when an artifact root is supplied,
the exact bytes of locally supplied synthetic artifacts.  It deliberately has
no network/client code and no cross-repository import.  A descriptor-level
``PASS`` therefore means only that this package's pinned file contract and
integrity rules were satisfied; both live adapters remain ``NOT_RUN``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest


API_VERSION = "1.0.0"
DESCRIPTOR_SCHEMA_VERSION = "1.0.0"
ENGINE_VERSION = "0.1.0"
TOOL_NAME = "power-rail-file-api-validator"
RULE_ID = "REP02.INTEGRATION.FILE_API"
SPEC_SOURCE_ID = "REP02-FILE-API-CONTRACT"
SPEC_VERSION = "1.0.0"
SPEC_SHA256 = "e1a40ffd49fb126152a89e37e173ca8aeb87c76f9dc6477540a4b0708c2857da"
LOAD_ERROR_BINDING_VERSION = "REP02-FILE-LOAD-ERROR-1.0.0"

_SPEC_PATH = Path(__file__).with_name("file_api_contract.json")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_MOVING_VERSION_TOKENS = frozenset(
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
        "tip",
        "trunk",
        "unknown",
        "unspecified",
    }
)
_ROOT_FIELDS = frozenset(
    {
        "api_version",
        "schema_version",
        "descriptor_id",
        "producer",
        "consumer",
        "compatibility",
        "configurations",
        "claim_limits",
        "input_hash",
    }
)
_ENDPOINT_FIELDS = frozenset({"repository_id", "api_version", "schema_version"})
_CONFIG_FIELDS = frozenset({"configuration_id", "profile_id", "artifacts", "artifact_gaps"})
_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "role", "file_name", "media_type", "access_status", "sha256"}
)
_GAP_FIELDS = frozenset(
    {"artifact_id", "role", "file_name", "media_type", "access_status", "reason_code"}
)
_COMPATIBILITY_FIELDS = frozenset(
    {
        "api_version_match",
        "schema_version_match",
        "incompatible_version_status",
        "malformed_json_status",
        "hash_mismatch_status",
        "missing_artifact_status",
    }
)
_COMPATIBILITY = {
    "api_version_match": "EXACT",
    "schema_version_match": "EXACT",
    "incompatible_version_status": "BLOCKED",
    "malformed_json_status": "BLOCKED",
    "hash_mismatch_status": "BLOCKED",
    "missing_artifact_status": "NOT_RUN",
}
_CLAIM_LIMITS = (
    "DESCRIPTOR_STRUCTURE_AND_FILE_INTEGRITY_ONLY",
    "SYNTHETIC_FIXTURES_ONLY",
    "LIVE_ADAPTERS_NOT_RUN",
    "NO_REP01_OR_REP03_SEMANTIC_MAPPING_CLAIM",
    "NO_HARDWARE_COMPLIANCE_CLAIM",
)
_LIMITATIONS = (
    "OFFLINE_DESCRIPTOR_CONTRACT_ONLY",
    "LIVE_REP01_ADAPTER_NOT_EXECUTED",
    "LIVE_REP03_ADAPTER_NOT_EXECUTED",
    "NO_CROSS_REPOSITORY_IMPORT_OR_MUTATION",
    "ARRAY_ORDER_IS_INPUT_HASH_SIGNIFICANT",
    "NO_SEMANTIC_ENVELOPE_MAPPING_CLAIM",
    "ARTIFACT_MEDIA_TYPE_DECLARATION_NOT_CONTENT_SNIFFED",
    "NO_HARDWARE_OR_PHYSICAL_VALIDATION",
) + tuple(f"CLAIM_LIMIT:{value}" for value in _CLAIM_LIMITS)
_ARTIFACT_ROLES = frozenset(
    {
        "ASSESSMENT_INPUT",
        "CONFIGURATION_INPUT",
        "EVIDENCE_INPUT",
        "PROFILE_INPUT",
        "SOURCE_INPUT",
    }
)
_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
        "text/markdown",
    }
)
_STATUS_RANK = {"PASS": 0, "NOT_RUN": 1, "UNKNOWN": 2, "FAIL": 3, "BLOCKED": 4}


@dataclass(frozen=True)
class _Issue:
    status: str
    code: str
    path: str
    message: str

    def as_dict(self, index: int) -> Dict[str, Any]:
        return {
            "finding_id": f"INT-{index:04d}",
            "status": self.status,
            "reason_code": self.code,
            "path": self.path,
            "message": self.message,
            "rule_id": RULE_ID,
            "source_refs": [SPEC_SOURCE_ID],
        }


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonNumber(ValueError):
    pass


def descriptor_input_hash(document: Mapping[str, Any]) -> str:
    """Hash every descriptor field except the self-referential ``input_hash``.

    Object-key order is ignored by the shared canonical JSON encoder.  Array
    order remains significant by contract.  Invalid/non-finite/cyclic values
    raise instead of being silently normalized.
    """

    if not isinstance(document, dict):
        raise TypeError("file API descriptor must be an object")
    payload = {key: value for key, value in document.items() if key != "input_hash"}
    return sha256_hexdigest(payload)


def _spec_source() -> Optional[Dict[str, Any]]:
    """Return a byte-exact record for the package-owned contract source."""

    try:
        payload = _SPEC_PATH.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != SPEC_SHA256:
            return None
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if not _finite_tree(parsed):
            return None
    except (
        OSError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("contract_id") != SPEC_SOURCE_ID or parsed.get("api_version") != SPEC_VERSION:
        return None
    return {
        "source_id": SPEC_SOURCE_ID,
        "version": SPEC_VERSION,
        "content_hash": digest,
    }


def _add(
    issues: MutableSequence[_Issue],
    status: str,
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append(_Issue(status, code, path, message))


def _closed(value: Mapping[Any, Any], allowed: Iterable[str], path: str, issues: MutableSequence[_Issue]) -> None:
    allowed_set = set(allowed)
    for key in sorted((key for key in value if key not in allowed_set), key=repr):
        _add(issues, "BLOCKED", "UNEXPECTED_FIELD", f"{path}.{key!s}", "field is outside the closed file-API contract")


def _required(value: Mapping[Any, Any], fields: Iterable[str], path: str, issues: MutableSequence[_Issue]) -> None:
    for field in sorted(field for field in fields if field not in value):
        _add(issues, "BLOCKED", "MISSING_FIELD", f"{path}.{field}", "required field is missing")


def _object(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, dict):
        _add(issues, "BLOCKED", "TYPE_OBJECT", path, "expected an object")
        return None
    return value


def _array(value: Any, path: str, issues: MutableSequence[_Issue], *, nonempty: bool = False) -> Optional[Sequence[Any]]:
    if not isinstance(value, list):
        _add(issues, "BLOCKED", "TYPE_ARRAY", path, "expected an array")
        return None
    if nonempty and not value:
        _add(issues, "BLOCKED", "EMPTY_ARRAY", path, "array must not be empty")
    return value


def _identifier(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _add(issues, "BLOCKED", "IDENTIFIER_INVALID", path, "expected a non-empty stable identifier")
        return None
    return value


def _is_moving_version(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _MOVING_VERSION_TOKENS:
        return True
    if any(marker in normalized for marker in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    parts = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    if any(part in {"branch", "branches", "heads"} for part in parts):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _pinned_version(
    value: Any,
    path: str,
    expected: str,
    incompatible_code: str,
    issues: MutableSequence[_Issue],
) -> Optional[str]:
    if not isinstance(value, str) or not value or value != value.strip():
        _add(issues, "BLOCKED", "VERSION_INVALID", path, "version must be a non-empty trimmed string")
        return None
    if _is_moving_version(value) or _SEMVER_RE.fullmatch(value) is None:
        _add(issues, "BLOCKED", "VERSION_NOT_PINNED", path, "version must be a pinned semantic version, not an alias or range")
        return None
    if value != expected:
        _add(issues, "BLOCKED", incompatible_code, path, f"only {expected!r} is compatible with this validator")
    return value


def _sha256(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _add(issues, "BLOCKED", "SHA256_INVALID", path, "expected an exact lowercase 64-character SHA-256 digest")
        return None
    return value


def _file_name(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or not value:
        _add(issues, "BLOCKED", "FILE_NAME_INVALID", path, "file_name must be a non-empty relative POSIX path")
        return None
    if value != value.strip() or "\\" in value or value.startswith("/") or ":" in value or "//" in value:
        _add(issues, "BLOCKED", "PATH_TRAVERSAL_BLOCKED", path, "file_name violates the relative POSIX path policy")
        return None
    segments = value.split("/")
    if any(segment in {"", ".", ".."} or _PATH_SEGMENT_RE.fullmatch(segment) is None for segment in segments):
        _add(issues, "BLOCKED", "PATH_TRAVERSAL_BLOCKED", path, "file_name contains an unsafe or non-canonical segment")
        return None
    return value


def _endpoint(
    value: Any,
    path: str,
    role: str,
    issues: MutableSequence[_Issue],
) -> Optional[str]:
    endpoint = _object(value, path, issues)
    if endpoint is None:
        return None
    _closed(endpoint, _ENDPOINT_FIELDS, path, issues)
    _required(endpoint, _ENDPOINT_FIELDS, path, issues)
    repository_id = _identifier(endpoint.get("repository_id"), f"{path}.repository_id", issues)
    _pinned_version(endpoint.get("api_version"), f"{path}.api_version", API_VERSION, "ENDPOINT_API_VERSION_INCOMPATIBLE", issues)
    _pinned_version(
        endpoint.get("schema_version"),
        f"{path}.schema_version",
        DESCRIPTOR_SCHEMA_VERSION,
        "ENDPOINT_SCHEMA_VERSION_INCOMPATIBLE",
        issues,
    )
    if role == "producer" and repository_id not in {"REP-01", "REP-03"}:
        _add(issues, "BLOCKED", "PRODUCER_UNSUPPORTED", f"{path}.repository_id", "producer must be REP-01 or REP-03")
    if role == "consumer" and repository_id != "REP-02":
        _add(issues, "BLOCKED", "CONSUMER_UNSUPPORTED", f"{path}.repository_id", "consumer must be REP-02")
    return repository_id


def _compatibility(value: Any, path: str, issues: MutableSequence[_Issue]) -> None:
    item = _object(value, path, issues)
    if item is None:
        return
    _closed(item, _COMPATIBILITY_FIELDS, path, issues)
    _required(item, _COMPATIBILITY_FIELDS, path, issues)
    for field, expected in _COMPATIBILITY.items():
        if field in item and item[field] != expected:
            _add(
                issues,
                "BLOCKED",
                "COMPATIBILITY_POLICY_MISMATCH",
                f"{path}.{field}",
                f"policy must be pinned to {expected!r}",
            )


def _claim_limits(value: Any, path: str, issues: MutableSequence[_Issue]) -> None:
    items = _array(value, path, issues, nonempty=True)
    if items is None:
        return
    observed: List[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item:
            _add(issues, "BLOCKED", "CLAIM_LIMIT_INVALID", f"{path}[{index}]", "claim limit must be a non-empty string")
        else:
            observed.append(item)
    if len(set(observed)) != len(observed):
        _add(issues, "BLOCKED", "DUPLICATE_CLAIM_LIMIT", path, "claim limits must be unique")
    if set(observed) != set(_CLAIM_LIMITS):
        _add(issues, "BLOCKED", "CLAIM_LIMITS_MISMATCH", path, "descriptor must preserve the contract-only claim boundary")


def _artifact_common(
    value: Any,
    path: str,
    allowed_fields: Iterable[str],
    issues: MutableSequence[_Issue],
) -> Optional[Tuple[str, str, str, str]]:
    item = _object(value, path, issues)
    if item is None:
        return None
    _closed(item, allowed_fields, path, issues)
    _required(item, allowed_fields, path, issues)
    artifact_id = _identifier(item.get("artifact_id"), f"{path}.artifact_id", issues)
    role = item.get("role")
    if not isinstance(role, str) or role not in _ARTIFACT_ROLES:
        _add(issues, "BLOCKED", "ARTIFACT_ROLE_INVALID", f"{path}.role", "artifact role is outside the pinned role set")
        role = None
    file_name = _file_name(item.get("file_name"), f"{path}.file_name", issues)
    media_type = item.get("media_type")
    if not isinstance(media_type, str) or media_type not in _MEDIA_TYPES:
        _add(issues, "BLOCKED", "MEDIA_TYPE_INVALID", f"{path}.media_type", "media type is outside the pinned set")
        media_type = None
    if None in (artifact_id, role, file_name, media_type):
        return None
    return artifact_id, role, file_name, media_type


def _register_identity(
    artifact_id: str,
    file_name: str,
    path: str,
    seen_ids: Dict[str, str],
    seen_paths: Dict[str, str],
    issues: MutableSequence[_Issue],
) -> None:
    id_key = artifact_id.casefold()
    path_key = file_name.casefold()
    if id_key in seen_ids:
        _add(issues, "BLOCKED", "DUPLICATE_ARTIFACT_ID", f"{path}.artifact_id", f"artifact id duplicates {seen_ids[id_key]}")
    else:
        seen_ids[id_key] = path
    if path_key in seen_paths:
        _add(issues, "BLOCKED", "DUPLICATE_ARTIFACT_PATH", f"{path}.file_name", f"file name duplicates {seen_paths[path_key]}")
    else:
        seen_paths[path_key] = path


def _resolve_artifact(root: Path, file_name: str) -> Optional[Path]:
    try:
        root_resolved = root.resolve()
        candidate = root_resolved.joinpath(*file_name.split("/")).resolve()
        candidate.relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _available_artifact(
    value: Any,
    path: str,
    artifact_root: Optional[Path],
    seen_ids: Dict[str, str],
    seen_paths: Dict[str, str],
    artifact_results: MutableSequence[Dict[str, Any]],
    artifact_gaps: MutableSequence[Dict[str, Any]],
    issues: MutableSequence[_Issue],
) -> None:
    common = _artifact_common(value, path, _ARTIFACT_FIELDS, issues)
    if common is None:
        return
    item = value
    artifact_id, role, file_name, media_type = common
    _register_identity(artifact_id, file_name, path, seen_ids, seen_paths, issues)
    if item.get("access_status") != "AVAILABLE":
        _add(issues, "BLOCKED", "AVAILABLE_ARTIFACT_STATUS_INVALID", f"{path}.access_status", "artifacts entries must have AVAILABLE access status")
    expected_hash = _sha256(item.get("sha256"), f"{path}.sha256", issues)
    result = {
        "artifact_id": artifact_id,
        "role": role,
        "file_name": file_name,
        "media_type": media_type,
        "access_status": "AVAILABLE",
        "expected_sha256": expected_hash,
        "observed_sha256": None,
        "status": "NOT_RUN",
        "reason_code": "ARTIFACT_BYTES_NOT_VERIFIED",
    }
    if artifact_root is None:
        _add(issues, "NOT_RUN", "ARTIFACT_BYTES_NOT_VERIFIED", path, "artifact_root was not supplied; exact file bytes were not read")
        artifact_gaps.append(
            {
                "artifact_id": artifact_id,
                "role": role,
                "file_name": file_name,
                "media_type": media_type,
                "access_status": "UNKNOWN",
                "reason_code": "ARTIFACT_BYTES_NOT_VERIFIED",
            }
        )
        artifact_results.append(result)
        return
    candidate = _resolve_artifact(artifact_root, file_name)
    if candidate is None:
        _add(issues, "BLOCKED", "ARTIFACT_PATH_ESCAPE", f"{path}.file_name", "resolved artifact path escapes or cannot be resolved under artifact_root")
        result.update({"status": "BLOCKED", "reason_code": "ARTIFACT_PATH_ESCAPE"})
        artifact_results.append(result)
        return
    try:
        if not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        observed_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        _add(issues, "BLOCKED", "ARTIFACT_FILE_MISSING", path, "descriptor declares an available artifact whose file bytes are unavailable")
        result.update({"status": "BLOCKED", "reason_code": "ARTIFACT_FILE_MISSING"})
        artifact_gaps.append(
            {
                "artifact_id": artifact_id,
                "role": role,
                "file_name": file_name,
                "media_type": media_type,
                "access_status": "MISSING",
                "reason_code": "ARTIFACT_FILE_MISSING",
            }
        )
        artifact_results.append(result)
        return
    result["observed_sha256"] = observed_hash
    if expected_hash is None or observed_hash != expected_hash:
        _add(issues, "BLOCKED", "ARTIFACT_HASH_MISMATCH", f"{path}.sha256", "declared SHA-256 does not match the exact artifact bytes")
        result.update({"status": "BLOCKED", "reason_code": "ARTIFACT_HASH_MISMATCH"})
    else:
        result.update({"status": "PASS", "reason_code": "ARTIFACT_BYTES_VERIFIED"})
    artifact_results.append(result)


def _artifact_gap(
    value: Any,
    path: str,
    seen_ids: Dict[str, str],
    seen_paths: Dict[str, str],
    artifact_gaps: MutableSequence[Dict[str, Any]],
    issues: MutableSequence[_Issue],
) -> None:
    common = _artifact_common(value, path, _GAP_FIELDS, issues)
    if common is None:
        return
    item = value
    artifact_id, role, file_name, media_type = common
    _register_identity(artifact_id, file_name, path, seen_ids, seen_paths, issues)
    access_status = item.get("access_status")
    if not isinstance(access_status, str) or access_status not in {"MISSING", "BLOCKED", "UNKNOWN"}:
        _add(issues, "BLOCKED", "ARTIFACT_GAP_STATUS_INVALID", f"{path}.access_status", "artifact_gaps must use MISSING, BLOCKED, or UNKNOWN")
        access_status = "UNKNOWN"
    reason_code = _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
    artifact_gaps.append(
        {
            "artifact_id": artifact_id,
            "role": role,
            "file_name": file_name,
            "media_type": media_type,
            "access_status": access_status,
            "reason_code": reason_code or "ARTIFACT_GAP_INVALID",
        }
    )
    gap_status = "BLOCKED" if access_status == "BLOCKED" else "NOT_RUN"
    _add(issues, gap_status, reason_code or "ARTIFACT_GAP_INVALID", path, "external artifact is explicitly unavailable; no content hash was synthesized")


def _configurations(
    value: Any,
    artifact_root: Optional[Path],
    artifact_results: MutableSequence[Dict[str, Any]],
    artifact_gaps: MutableSequence[Dict[str, Any]],
    issues: MutableSequence[_Issue],
) -> None:
    configs = _array(value, "$.configurations", issues, nonempty=True)
    if configs is None:
        return
    seen_pairs: Dict[Tuple[str, str], str] = {}
    seen_ids: Dict[str, str] = {}
    seen_paths: Dict[str, str] = {}
    for config_index, config_value in enumerate(configs):
        path = f"$.configurations[{config_index}]"
        config = _object(config_value, path, issues)
        if config is None:
            continue
        _closed(config, _CONFIG_FIELDS, path, issues)
        _required(config, _CONFIG_FIELDS, path, issues)
        configuration_id = _identifier(config.get("configuration_id"), f"{path}.configuration_id", issues)
        profile_id = _identifier(config.get("profile_id"), f"{path}.profile_id", issues)
        if configuration_id is not None and profile_id is not None:
            pair = (configuration_id.casefold(), profile_id.casefold())
            if pair in seen_pairs:
                _add(issues, "BLOCKED", "DUPLICATE_CONFIGURATION_PROFILE", path, f"configuration/profile identity duplicates {seen_pairs[pair]}")
            else:
                seen_pairs[pair] = path
        artifacts = _array(config.get("artifacts"), f"{path}.artifacts", issues)
        gaps = _array(config.get("artifact_gaps"), f"{path}.artifact_gaps", issues)
        if artifacts is not None and gaps is not None and not artifacts and not gaps:
            _add(issues, "BLOCKED", "EMPTY_CONFIGURATION", path, "configuration/profile must declare an artifact or an explicit artifact gap")
        if artifacts is not None:
            for artifact_index, artifact in enumerate(artifacts):
                _available_artifact(
                    artifact,
                    f"{path}.artifacts[{artifact_index}]",
                    artifact_root,
                    seen_ids,
                    seen_paths,
                    artifact_results,
                    artifact_gaps,
                    issues,
                )
        if gaps is not None:
            for gap_index, gap in enumerate(gaps):
                _artifact_gap(
                    gap,
                    f"{path}.artifact_gaps[{gap_index}]",
                    seen_ids,
                    seen_paths,
                    artifact_gaps,
                    issues,
                )


def _base_result(input_hash: Optional[str], spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sources = [dict(spec)] if spec is not None else []
    source_refs = [dict(spec)] if spec is not None else []
    return {
        "rule_id": RULE_ID,
        "status": "BLOCKED",
        "reason_code": "INPUT_VALIDATION_BLOCKED",
        "input_hash": input_hash,
        "input_binding": {
            "kind": (
                "CANONICAL_DESCRIPTOR_FIELDS_EXCLUDING_INPUT_HASH"
                if input_hash is not None
                else "UNAVAILABLE_INVALID_IN_MEMORY_INPUT"
            ),
            "algorithm": "SHA-256" if input_hash is not None else None,
        },
        "tool_name": TOOL_NAME,
        "tool_version": ENGINE_VERSION,
        "package_version": PACKAGE_VERSION,
        "api_version": API_VERSION,
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "source_refs": source_refs,
        "sources": sources,
        "issues": [],
        "limitations": list(_LIMITATIONS),
        "claim_limits": list(_CLAIM_LIMITS),
        "compatibility_policy": dict(_COMPATIBILITY),
        "descriptor_identity": {
            "descriptor_id": None,
            "producer": None,
            "consumer": None,
            "configuration_profile_count": 0,
        },
        "artifact_results": [],
        "artifact_gaps": [],
        "adapter_results": [
            {"adapter_id": "REP-01", "status": "BLOCKED", "reason_code": "DESCRIPTOR_VALIDATION_BLOCKED"},
            {"adapter_id": "REP-03", "status": "BLOCKED", "reason_code": "DESCRIPTOR_VALIDATION_BLOCKED"},
        ],
    }


def _finish(result: Dict[str, Any], issues: Sequence[_Issue]) -> Dict[str, Any]:
    ordered = sorted(issues, key=lambda item: (-_STATUS_RANK[item.status], item.code, item.path, item.message))
    status = max((item.status for item in ordered), key=lambda item: _STATUS_RANK[item], default="PASS")
    if status == "PASS":
        reason_code = "FILE_API_DESCRIPTOR_VALID"
    else:
        reason_code = next(item.code for item in ordered if item.status == status)
    result["status"] = status
    result["reason_code"] = reason_code
    result["issues"] = [item.as_dict(index) for index, item in enumerate(ordered, 1)]
    result["artifact_results"] = sorted(
        result["artifact_results"], key=lambda item: (item["artifact_id"], item["file_name"])
    )
    result["artifact_gaps"] = sorted(
        result["artifact_gaps"], key=lambda item: (item["artifact_id"], item["file_name"])
    )
    adapter_status = "BLOCKED" if status == "BLOCKED" else "NOT_RUN"
    adapter_reason = "DESCRIPTOR_VALIDATION_BLOCKED" if status == "BLOCKED" else "LIVE_ADAPTER_NOT_EXECUTED"
    result["adapter_results"] = [
        {"adapter_id": "REP-01", "status": adapter_status, "reason_code": adapter_reason},
        {"adapter_id": "REP-03", "status": adapter_status, "reason_code": adapter_reason},
    ]
    return result


def _evaluate(document: Any, artifact_root: Optional[Path]) -> Dict[str, Any]:
    issues: List[_Issue] = []
    try:
        input_hash = descriptor_input_hash(document)
    except (RecursionError, TypeError, ValueError, OverflowError):
        input_hash = None
        _add(issues, "BLOCKED", "INPUT_HASH_UNCOMPUTABLE", "$", "descriptor cannot be encoded as finite, acyclic canonical JSON")
    spec = _spec_source()
    result = _base_result(input_hash, spec)
    if spec is None:
        _add(issues, "BLOCKED", "CONTRACT_SPEC_UNAVAILABLE", "$", "package-owned file-API contract bytes are unavailable or invalid")
    descriptor = _object(document, "$", issues)
    if descriptor is None:
        return _finish(result, issues)
    _closed(descriptor, _ROOT_FIELDS, "$", issues)
    _required(descriptor, _ROOT_FIELDS, "$", issues)
    _pinned_version(descriptor.get("api_version"), "$.api_version", API_VERSION, "API_VERSION_INCOMPATIBLE", issues)
    _pinned_version(
        descriptor.get("schema_version"),
        "$.schema_version",
        DESCRIPTOR_SCHEMA_VERSION,
        "SCHEMA_VERSION_INCOMPATIBLE",
        issues,
    )
    descriptor_id = _identifier(descriptor.get("descriptor_id"), "$.descriptor_id", issues)
    producer = _endpoint(descriptor.get("producer"), "$.producer", "producer", issues)
    consumer = _endpoint(descriptor.get("consumer"), "$.consumer", "consumer", issues)
    if producer is not None and consumer is not None and producer == consumer:
        _add(issues, "BLOCKED", "ENDPOINT_DIRECTION_INVALID", "$", "producer and consumer must be different repositories")
    _compatibility(descriptor.get("compatibility"), "$.compatibility", issues)
    _claim_limits(descriptor.get("claim_limits"), "$.claim_limits", issues)
    declared_hash = _sha256(descriptor.get("input_hash"), "$.input_hash", issues)
    if input_hash is not None and declared_hash is not None and declared_hash != input_hash:
        _add(issues, "BLOCKED", "DESCRIPTOR_HASH_MISMATCH", "$.input_hash", "declared input hash does not match canonical descriptor content")
    artifact_results: List[Dict[str, Any]] = []
    artifact_gaps: List[Dict[str, Any]] = []
    _configurations(descriptor.get("configurations"), artifact_root, artifact_results, artifact_gaps, issues)
    configs = descriptor.get("configurations")
    result["descriptor_identity"] = {
        "descriptor_id": descriptor_id,
        "producer": producer,
        "consumer": consumer,
        "configuration_profile_count": len(configs) if isinstance(configs, list) else 0,
    }
    result["artifact_results"] = artifact_results
    result["artifact_gaps"] = artifact_gaps
    return _finish(result, issues)


def validate_file_api_descriptor(
    document: Any,
    *,
    artifact_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Validate one already-decoded descriptor without mutating it.

    ``artifact_root`` enables byte-exact file verification.  Omitting it is
    explicit ``NOT_RUN`` for artifact bytes and cannot yield descriptor PASS.
    """

    root = Path(artifact_root) if artifact_root is not None else None
    try:
        return _evaluate(document, root)
    except (ArithmeticError, OSError, RecursionError, RuntimeError, TypeError, ValueError):
        spec = _spec_source()
        result = _base_result(None, spec)
        return _finish(
            result,
            [
                _Issue(
                    "BLOCKED",
                    "INPUT_VALIDATION_BLOCKED",
                    "$",
                    "descriptor could not be safely evaluated",
                )
            ],
        )


def _strict_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise _NonFiniteJsonNumber(token)


def _file_error_result(
    descriptor_path: Path,
    code: str,
    message: str,
    payload: Optional[bytes],
) -> Dict[str, Any]:
    """Build a reporter-compatible binding for bytes that are not valid JSON.

    This is deliberately not labelled as a descriptor canonical hash.  It is
    the canonical hash of a closed diagnostic envelope which contains the
    exact raw-file SHA-256 whenever file bytes were readable.
    """

    raw_hash = hashlib.sha256(payload).hexdigest() if payload is not None else None
    access_status = "AVAILABLE" if payload is not None else "MISSING"
    binding = {
        "binding_version": LOAD_ERROR_BINDING_VERSION,
        "kind": "CANONICAL_LOAD_ERROR_ENVELOPE",
        "descriptor_file_name": descriptor_path.name,
        "raw_file_access_status": access_status,
        "raw_file_sha256": raw_hash,
        "reason_code": code,
    }
    result = _base_result(sha256_hexdigest(binding), _spec_source())
    result["input_binding"] = {
        **binding,
        "algorithm": "SHA-256",
    }
    return _finish(result, [_Issue("BLOCKED", code, "$", message)])


def validate_file_api_file(
    path: Union[str, Path],
    *,
    artifact_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Strictly load and validate a descriptor file.

    Duplicate JSON keys and ``NaN``/``Infinity`` constants are rejected.  The
    descriptor's parent directory is the default artifact root.
    """

    descriptor_path = Path(path)
    try:
        payload = descriptor_path.read_bytes()
    except OSError:
        return _file_error_result(
            descriptor_path,
            "DESCRIPTOR_FILE_UNAVAILABLE",
            "descriptor file is missing or unreadable",
            None,
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return _file_error_result(
            descriptor_path,
            "DESCRIPTOR_ENCODING_INVALID",
            "descriptor file is not valid UTF-8",
            payload,
        )
    try:
        document = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if not _finite_tree(document):
            raise _NonFiniteJsonNumber("overflow")
    except _DuplicateJsonKey as exc:
        return _file_error_result(
            descriptor_path,
            "DUPLICATE_JSON_KEY",
            f"duplicate JSON key {str(exc)!r}",
            payload,
        )
    except _NonFiniteJsonNumber:
        return _file_error_result(
            descriptor_path,
            "NON_FINITE_JSON_NUMBER",
            "non-finite JSON number is forbidden",
            payload,
        )
    except (OverflowError, RecursionError, json.JSONDecodeError):
        return _file_error_result(
            descriptor_path,
            "MALFORMED_JSON",
            "descriptor file is malformed JSON",
            payload,
        )
    root = Path(artifact_root) if artifact_root is not None else descriptor_path.parent
    return validate_file_api_descriptor(document, artifact_root=root)


def _finite_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    return True
