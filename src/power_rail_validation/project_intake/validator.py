"""Validate the bounded inputs for one real REP-01 power-rail slice.

This module validates identity, provenance, byte integrity, and execution
readiness only.  It deliberately does not run an electrical calculation or
turn a complete intake into an electrical, hardware, or G1-G6 PASS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest


DESCRIPTOR_SCHEMA_VERSION = "1.0.0"
ENGINE_VERSION = "0.1.0"
RULE_ID = "REP02.PROJECT_INTAKE.READINESS"
TOOL_NAME = "power-rail-project-intake"
SPEC_SOURCE_ID = "REP02-PROJECT-INTAKE-CONTRACT"
SPEC_VERSION = "1.0.0"
# Kept independent from the descriptor and verified against the packaged bytes.
SPEC_SHA256 = "91f89178113807e5da2901b3394495c73ccbcf3eace6aa8deae05566721a743c"

_SPEC_PATH = Path(__file__).with_name("project_intake_contract.json")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_MOVING_TOKENS = frozenset(
    {"current", "develop", "development", "head", "latest", "main", "master", "nightly", "stable", "tbd", "tip", "trunk", "unknown", "unspecified"}
)
_CAPABILITIES = frozenset(
    {"REP01_BINDING", "STATIC", "BUCK", "PIN", "TRANSIENT", "LOOP_STABILITY"}
)
_BASE_ROLES = frozenset(
    {"REP01_CONFIGURATION", "REP01_POWER_PROFILE", "REP01_RAIL_DEMANDS", "REGULATOR_SELECTION"}
)
_DATASHEET_CAPABILITIES = frozenset(
    {"STATIC", "BUCK", "PIN", "TRANSIENT", "LOOP_STABILITY"}
)
_MODEL_CAPABILITIES = frozenset({"TRANSIENT", "LOOP_STABILITY"})
_SUPPORTING_ROLES = frozenset(
    {"REP01_INTERFACE_CONTRACT", "REP01_REQUIREMENTS_TRACEABILITY", "REP01_OPEN_DECISIONS", "REP01_POWER_TREE_PLACEHOLDER"}
)
_READINESS_ROLES = _BASE_ROLES | frozenset({"REGULATOR_DATASHEET", "REGULATOR_MODEL"})
_ARTIFACT_ROLES = _READINESS_ROLES | _SUPPORTING_ROLES
_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)
_SEMANTIC_UNITS = {
    "vin_min_v": "V",
    "vin_nom_v": "V",
    "vin_max_v": "V",
    "vout_target_v": "V",
    "load_min_a": "A",
    "load_nom_a": "A",
    "load_max_a": "A",
    "ambient_min_c": "degC",
    "ambient_max_c": "degC",
}
_ROOT_FIELDS = frozenset(
    {"schema_version", "descriptor_id", "scope", "rep01", "topology", "requested_capabilities", "operating_envelope", "regulator", "artifacts", "artifact_gaps", "input_hash"}
)
_REP01_FIELDS = frozenset(
    {"repository_id", "git_revision", "configuration_id", "profile_id", "rail_id"}
)
_REGULATOR_FIELDS = frozenset({"manufacturer", "part_number"})
_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "role",
        "path",
        "media_type",
        "version",
        "document_revision",
        "origin",
        "license_status",
        "sha256",
        "bindings",
    }
)
_BINDING_FIELDS = frozenset(
    {"rep01_git_revision", "configuration_id", "profile_id", "rail_id", "manufacturer", "part_number"}
)
_GAP_FIELDS = frozenset({"gap_id", "role", "reason_code"})
_PLACEHOLDERS = frozenset({"na", "n/a", "none", "null", "tbd", "unknown", "unspecified"})
_PLACEHOLDER_SEGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:tbd|unknown|unspecified|none|null|n/?a)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MOVING_VERSION_SEGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:current|develop|development|head|latest|main|master|nightly|stable|tbd|tip|trunk|unknown|unspecified)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_ORIGINS = frozenset(
    {"REP01_PROJECT", "MANUFACTURER", "AUTHORIZED_MODEL_PROVIDER", "INTERNAL_TEST_FIXTURE"}
)
_LICENSE_STATUSES = frozenset({"VERIFIED", "DECLARED", "NOT_APPLICABLE"})
_STATUS_RANK = {"PASS": 0, "UNKNOWN": 1, "BLOCKED": 2}

_ROLE_BINDINGS = {
    "REP01_CONFIGURATION": ("rep01_git_revision", "configuration_id"),
    "REP01_POWER_PROFILE": ("rep01_git_revision", "configuration_id", "profile_id"),
    "REP01_RAIL_DEMANDS": ("rep01_git_revision", "configuration_id", "profile_id", "rail_id"),
    "REGULATOR_SELECTION": (
        "rep01_git_revision",
        "configuration_id",
        "profile_id",
        "rail_id",
        "manufacturer",
        "part_number",
    ),
    "REGULATOR_DATASHEET": ("manufacturer", "part_number"),
    "REGULATOR_MODEL": ("manufacturer", "part_number"),
}


@dataclass(frozen=True)
class _Issue:
    status: str
    reason_code: str
    path: str
    message: str

    def as_dict(self, index: int, source_refs: Sequence[str]) -> Dict[str, Any]:
        return {
            "finding_id": f"INTAKE-{index:04d}",
            "status": self.status,
            "reason_code": self.reason_code,
            "path": self.path,
            "message": self.message,
            "rule_id": RULE_ID,
            "source_refs": list(source_refs),
        }


def descriptor_input_hash(document: Mapping[str, Any]) -> str:
    """Return the canonical descriptor hash, excluding its self-hash field."""

    if not isinstance(document, dict):
        raise TypeError("project intake descriptor must be an object")
    return sha256_hexdigest({key: value for key, value in document.items() if key != "input_hash"})


def _add(
    issues: MutableSequence[_Issue], status: str, reason_code: str, path: str, message: str
) -> None:
    issues.append(_Issue(status, reason_code, path, message))


def _closed(
    value: Mapping[Any, Any], allowed: Iterable[str], path: str, issues: MutableSequence[_Issue]
) -> None:
    allowed_set = set(allowed)
    for key in sorted((key for key in value if key not in allowed_set), key=repr):
        _add(issues, "BLOCKED", "UNEXPECTED_FIELD", f"{path}.{key!s}", "field is outside the closed intake contract")


def _required(
    value: Mapping[Any, Any], fields: Iterable[str], path: str, issues: MutableSequence[_Issue]
) -> None:
    for field in sorted(field for field in fields if field not in value):
        _add(issues, "BLOCKED", "MISSING_FIELD", f"{path}.{field}", "required field is missing")


def _object(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, dict):
        _add(issues, "BLOCKED", "TYPE_OBJECT", path, "expected an object")
        return None
    return value


def _array(
    value: Any, path: str, issues: MutableSequence[_Issue], *, nonempty: bool = False
) -> Optional[Sequence[Any]]:
    if not isinstance(value, list):
        _add(issues, "BLOCKED", "TYPE_ARRAY", path, "expected an array")
        return None
    if nonempty and not value:
        _add(issues, "BLOCKED", "EMPTY_ARRAY", path, "array must not be empty")
    return value


def _identifier(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _add(issues, "BLOCKED", "IDENTIFIER_INVALID", path, "expected a stable identifier")
        return None
    return value


def _text(value: Any, path: str, issues: MutableSequence[_Issue], *, exact: bool = False) -> Optional[str]:
    if not isinstance(value, str) or not value or value != value.strip():
        _add(issues, "BLOCKED", "IDENTITY_INVALID", path, "expected a non-empty trimmed string")
        return None
    normalized = value.casefold()
    if exact and (
        normalized in _PLACEHOLDERS
        or _PLACEHOLDER_SEGMENT_RE.search(value) is not None
        or "*" in value
        or "?" in value
    ):
        _add(issues, "BLOCKED", "IDENTITY_NOT_EXACT", path, "placeholder or wildcard identity is forbidden")
        return None
    return value


def _string_state(
    value: Any,
    path: str,
    issues: MutableSequence[_Issue],
    *,
    exact: bool = False,
    revision: bool = False,
) -> Optional[str]:
    item = _object(value, path, issues)
    if item is None:
        return None
    state = item.get("state")
    if state == "KNOWN":
        _closed(item, {"state", "value"}, path, issues)
        _required(item, {"state", "value"}, path, issues)
        result = _text(item.get("value"), f"{path}.value", issues, exact=exact)
        if revision and result is not None and _GIT_REVISION_RE.fullmatch(result) is None:
            _add(issues, "BLOCKED", "REVISION_NOT_PINNED", f"{path}.value", "git revision must be a full lowercase 40- or 64-hex object id")
            return None
        return result
    if state == "UNKNOWN":
        _closed(item, {"state", "reason_code"}, path, issues)
        _required(item, {"state", "reason_code"}, path, issues)
        _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
        code = "REVISION_UNKNOWN" if revision else "REQUIRED_IDENTITY_UNKNOWN"
        _add(issues, "BLOCKED", code, path, "required identity is explicitly unknown")
        return None
    if state == "CONFLICT":
        _closed(item, {"state", "reason_code", "candidates"}, path, issues)
        _required(item, {"state", "reason_code", "candidates"}, path, issues)
        _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
        candidates = _array(item.get("candidates"), f"{path}.candidates", issues, nonempty=True)
        if candidates is not None:
            for index, candidate in enumerate(candidates):
                _text(candidate, f"{path}.candidates[{index}]", issues, exact=exact)
            if len(candidates) < 2:
                _add(issues, "BLOCKED", "CONFLICT_CANDIDATES_INSUFFICIENT", f"{path}.candidates", "a conflict needs at least two candidates")
        _add(issues, "BLOCKED", "REQUIRED_IDENTITY_CONFLICT", path, "required identity has conflicting candidates")
        return None
    _closed(item, {"state", "value", "reason_code", "candidates"}, path, issues)
    _add(issues, "BLOCKED", "KNOWLEDGE_STATE_INVALID", f"{path}.state", "state must be KNOWN, UNKNOWN, or CONFLICT")
    return None


def _number(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[Decimal]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _add(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", path, "expected a finite JSON number")
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, OverflowError, ValueError):
        _add(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", path, "expected a finite JSON number")
        return None
    if not converted.is_finite():
        _add(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", path, "expected a finite JSON number")
        return None
    return converted


def _numeric_state(
    value: Any, path: str, unit: str, issues: MutableSequence[_Issue]
) -> Optional[Decimal]:
    item = _object(value, path, issues)
    if item is None:
        return None
    state = item.get("state")
    if state == "KNOWN":
        _closed(item, {"state", "value", "unit"}, path, issues)
        _required(item, {"state", "value", "unit"}, path, issues)
        if item.get("unit") != unit:
            _add(issues, "BLOCKED", "UNIT_MISMATCH", f"{path}.unit", f"unit must be {unit!r}")
        return _number(item.get("value"), f"{path}.value", issues)
    if state == "UNKNOWN":
        _closed(item, {"state", "unit", "reason_code"}, path, issues)
        _required(item, {"state", "unit", "reason_code"}, path, issues)
        if item.get("unit") != unit:
            _add(issues, "BLOCKED", "UNIT_MISMATCH", f"{path}.unit", f"unit must be {unit!r}")
        _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
        _add(issues, "UNKNOWN", "MANDATORY_SEMANTIC_UNKNOWN", path, "mandatory operating-envelope value is unknown; no default was applied")
        return None
    if state == "CONFLICT":
        _closed(item, {"state", "unit", "reason_code", "candidates"}, path, issues)
        _required(item, {"state", "unit", "reason_code", "candidates"}, path, issues)
        if item.get("unit") != unit:
            _add(issues, "BLOCKED", "UNIT_MISMATCH", f"{path}.unit", f"unit must be {unit!r}")
        _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
        candidates = _array(item.get("candidates"), f"{path}.candidates", issues, nonempty=True)
        if candidates is not None:
            for index, candidate in enumerate(candidates):
                _number(candidate, f"{path}.candidates[{index}]", issues)
            if len(candidates) < 2:
                _add(issues, "BLOCKED", "CONFLICT_CANDIDATES_INSUFFICIENT", f"{path}.candidates", "a conflict needs at least two candidates")
        _add(issues, "BLOCKED", "MANDATORY_SEMANTIC_CONFLICT", path, "mandatory operating-envelope value is conflicting")
        return None
    _closed(item, {"state", "value", "unit", "reason_code", "candidates"}, path, issues)
    _add(issues, "BLOCKED", "KNOWLEDGE_STATE_INVALID", f"{path}.state", "state must be KNOWN, UNKNOWN, or CONFLICT")
    return None


def _safe_path(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or not value or value != value.strip():
        _add(issues, "BLOCKED", "ARTIFACT_PATH_INVALID", path, "path must be a non-empty relative POSIX path")
        return None
    if "\\" in value or value.startswith("/") or ":" in value or "//" in value or "\x00" in value:
        _add(issues, "BLOCKED", "ARTIFACT_PATH_UNSAFE", path, "path violates the relative POSIX path policy")
        return None
    segments = value.split("/")
    if any(segment in {"", ".", ".."} or segment != segment.strip() or any(ord(char) < 32 for char in segment) for segment in segments):
        _add(issues, "BLOCKED", "ARTIFACT_PATH_UNSAFE", path, "path contains an unsafe or non-canonical segment")
        return None
    return value


def _sha256(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _add(issues, "BLOCKED", "SHA256_INVALID", path, "expected a lowercase 64-character SHA-256 digest")
        return None
    return value


def _pinned_version(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[str]:
    result = _text(value, path, issues)
    if result is None:
        return None
    normalized = result.casefold()
    dotted = tuple(part for part in normalized.split(".") if part)
    if (
        normalized in _MOVING_TOKENS
        or _MOVING_VERSION_SEGMENT_RE.search(result) is not None
        or any(mark in normalized for mark in ("*", "^", "~", "<", ">", "=", "|"))
        or re.search(r"(?<![A-Za-z0-9])(?:branch|branches|heads)(?![A-Za-z0-9])", normalized)
        is not None
        or re.search(r"\s+-\s+", result) is not None
        or "–" in result
        or "—" in result
        or ".." in result
        or re.search(r"(?<![A-Za-z0-9])to(?![A-Za-z0-9])", normalized) is not None
        or (dotted and dotted[-1] == "x")
    ):
        _add(issues, "BLOCKED", "VERSION_NOT_PINNED", path, "version must be pinned rather than a moving alias or range")
        return None
    return result


def _resolve(root: Path, relative: str) -> Optional[Path]:
    try:
        root_lexical = Path(os.path.abspath(str(root)))
        root_metadata = root_lexical.lstat()
        if root_lexical.is_symlink() or getattr(
            root_metadata, "st_file_attributes", 0
        ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return None
        root_resolved = root_lexical.resolve(strict=True)
        if not root_resolved.is_dir():
            return None
        candidate_lexical = root_lexical
        for part in relative.split("/"):
            candidate_lexical = candidate_lexical / part
            try:
                metadata = candidate_lexical.lstat()
            except FileNotFoundError:
                continue
            if candidate_lexical.is_symlink() or getattr(
                metadata, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                return None
        candidate = candidate_lexical.resolve(strict=False)
        candidate.relative_to(root_resolved)
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_command(root: Path, *arguments: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _same_path(first: Path, second: Path) -> bool:
    try:
        first_value = os.path.normcase(str(first.resolve(strict=True)))
        second_value = os.path.normcase(str(second.resolve(strict=True)))
    except (OSError, RuntimeError):
        return False
    return first_value == second_value


def _block_duplicate_readiness_content(
    artifact_results: MutableSequence[Dict[str, Any]],
    issues: MutableSequence[_Issue],
) -> None:
    by_digest: Dict[str, List[Tuple[int, str]]] = {}
    for index, result in enumerate(artifact_results):
        digest = result.get("observed_sha256")
        role = result.get("role")
        if isinstance(digest, str) and role in _READINESS_ROLES:
            by_digest.setdefault(digest, []).append((index, role))
    for digest, entries in sorted(by_digest.items()):
        roles = sorted({role for _, role in entries})
        if len(roles) < 2:
            continue
        _add(
            issues,
            "BLOCKED",
            "DUPLICATE_ARTIFACT_CONTENT_ACROSS_ROLES",
            "$.artifacts",
            "byte-identical content is assigned to different readiness roles: "
            + ", ".join(roles),
        )
        for index, _ in entries:
            artifact_results[index]["status"] = "BLOCKED"
            artifact_results[index]["reason_code"] = (
                "DUPLICATE_ARTIFACT_CONTENT_ACROSS_ROLES"
            )


def _project_git_provenance(
    scope: Any,
    artifact_root: Optional[Path],
    revision: Optional[str],
    artifact_results: MutableSequence[Dict[str, Any]],
    issues: MutableSequence[_Issue],
) -> Dict[str, Any]:
    if scope != "PROJECT":
        return {
            "status": "NOT_RUN",
            "reason_code": "PROJECT_SCOPE_NOT_REQUESTED",
            "revision": None,
            "working_tree": "NOT_RUN",
            "committed_artifacts_verified": 0,
        }

    local: List[_Issue] = []
    working_tree = "NOT_CHECKED"
    verified_count = 0
    if artifact_root is None:
        _add(
            local,
            "BLOCKED",
            "PROJECT_ARTIFACT_ROOT_REQUIRED",
            "$.artifacts",
            "PROJECT scope requires an explicit Git top-level artifact_root",
        )
        issues.extend(local)
        return {
            "status": "BLOCKED",
            "reason_code": local[0].reason_code,
            "revision": revision,
            "working_tree": working_tree,
            "committed_artifacts_verified": verified_count,
        }

    try:
        root = artifact_root.resolve(strict=True)
    except (OSError, RuntimeError):
        root = artifact_root

    top_level = _git_command(root, "rev-parse", "--show-toplevel")
    if top_level is None:
        _add(local, "BLOCKED", "PROJECT_GIT_UNAVAILABLE", "$.artifacts", "Git could not be executed for PROJECT provenance")
    elif top_level.returncode != 0:
        _add(local, "BLOCKED", "PROJECT_GIT_REPOSITORY_UNAVAILABLE", "$.artifacts", "artifact_root is not an accessible Git worktree")
    else:
        try:
            top_path = Path(top_level.stdout.decode("utf-8", errors="strict").strip())
        except UnicodeError:
            top_path = Path("")
        if not _same_path(root, top_path):
            _add(local, "BLOCKED", "PROJECT_ARTIFACT_ROOT_NOT_GIT_TOPLEVEL", "$.artifacts", "artifact_root must be exactly the Git worktree top level")

    root_is_top = not any(
        item.reason_code
        in {
            "PROJECT_GIT_UNAVAILABLE",
            "PROJECT_GIT_REPOSITORY_UNAVAILABLE",
            "PROJECT_ARTIFACT_ROOT_NOT_GIT_TOPLEVEL",
        }
        for item in local
    )
    if revision is None:
        _add(local, "BLOCKED", "PROJECT_GIT_REVISION_UNAVAILABLE", "$.rep01.git_revision", "PROJECT scope requires a known full Git revision")

    if root_is_top:
        head = _git_command(root, "rev-parse", "--verify", "HEAD")
        if head is None:
            _add(local, "BLOCKED", "PROJECT_GIT_UNAVAILABLE", "$.rep01.git_revision", "Git could not read the PROJECT HEAD revision")
        elif head.returncode != 0:
            _add(local, "BLOCKED", "PROJECT_GIT_HEAD_UNAVAILABLE", "$.rep01.git_revision", "PROJECT Git HEAD is unavailable")
        else:
            try:
                observed_head = head.stdout.decode("ascii", errors="strict").strip()
            except UnicodeError:
                observed_head = ""
            if revision is not None and observed_head != revision:
                _add(local, "BLOCKED", "PROJECT_GIT_HEAD_MISMATCH", "$.rep01.git_revision", "declared REP-01 revision does not equal Git HEAD")

        status_result = _git_command(root, "status", "--porcelain=v1", "--untracked-files=all")
        if status_result is None:
            _add(local, "BLOCKED", "PROJECT_GIT_UNAVAILABLE", "$.artifacts", "Git could not inspect the PROJECT worktree")
        elif status_result.returncode != 0:
            _add(local, "BLOCKED", "PROJECT_GIT_STATUS_UNAVAILABLE", "$.artifacts", "PROJECT worktree status is unavailable")
        else:
            lines = [line for line in status_result.stdout.splitlines() if line]
            has_untracked = any(line.startswith(b"?? ") for line in lines)
            has_dirty = any(not line.startswith(b"?? ") for line in lines)
            if has_untracked:
                _add(local, "BLOCKED", "PROJECT_GIT_UNTRACKED_FILES", "$.artifacts", "PROJECT worktree contains untracked files")
            if has_dirty:
                _add(local, "BLOCKED", "PROJECT_GIT_WORKTREE_DIRTY", "$.artifacts", "PROJECT worktree contains staged or unstaged changes")
            if has_untracked and has_dirty:
                working_tree = "DIRTY_AND_UNTRACKED"
            elif has_untracked:
                working_tree = "UNTRACKED"
            elif has_dirty:
                working_tree = "DIRTY"
            else:
                working_tree = "CLEAN"

    if root_is_top and revision is not None:
        for index, result in enumerate(artifact_results):
            relative = result.get("path")
            if not isinstance(relative, str):
                continue
            result["committed_sha256"] = None
            result["committed_artifact_status"] = "BLOCKED"
            committed = _git_command(root, "show", f"{revision}:{relative}")
            if committed is None:
                _add(local, "BLOCKED", "PROJECT_GIT_UNAVAILABLE", f"$.artifacts[{index}]", "Git could not read committed artifact bytes")
                continue
            if committed.returncode != 0:
                _add(local, "BLOCKED", "PROJECT_ARTIFACT_NOT_COMMITTED", f"$.artifacts[{index}].path", "artifact is not present at the declared Git revision")
                result["reason_code"] = "PROJECT_ARTIFACT_NOT_COMMITTED"
                result["status"] = "BLOCKED"
                continue
            committed_hash = hashlib.sha256(committed.stdout).hexdigest()
            result["committed_sha256"] = committed_hash
            if committed_hash != result.get("expected_sha256") or committed_hash != result.get("observed_sha256"):
                _add(local, "BLOCKED", "PROJECT_COMMITTED_ARTIFACT_MISMATCH", f"$.artifacts[{index}].sha256", "working-tree, declared, and committed artifact bytes do not match")
                result["reason_code"] = "PROJECT_COMMITTED_ARTIFACT_MISMATCH"
                result["status"] = "BLOCKED"
                continue
            result["committed_artifact_status"] = "VERIFIED"
            verified_count += 1

    issues.extend(local)
    if local:
        return {
            "status": "BLOCKED",
            "reason_code": local[0].reason_code,
            "revision": revision,
            "working_tree": working_tree,
            "committed_artifacts_verified": verified_count,
        }
    return {
        "status": "PASS",
        "reason_code": "PROJECT_GIT_PROVENANCE_VERIFIED",
        "revision": revision,
        "working_tree": working_tree,
        "committed_artifacts_verified": verified_count,
    }


def _spec_source() -> Optional[Dict[str, Any]]:
    try:
        payload = _SPEC_PATH.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if digest != SPEC_SHA256 or not isinstance(document, dict):
        return None
    if document.get("contract_id") != SPEC_SOURCE_ID or document.get("schema_version") != SPEC_VERSION:
        return None
    return {"source_id": SPEC_SOURCE_ID, "version": SPEC_VERSION, "content_hash": digest}


def _required_roles(capabilities: Sequence[str]) -> List[str]:
    roles = set(_BASE_ROLES)
    if set(capabilities) & _DATASHEET_CAPABILITIES:
        roles.add("REGULATOR_DATASHEET")
    if set(capabilities) & _MODEL_CAPABILITIES:
        roles.add("REGULATOR_MODEL")
    return sorted(roles)


def _expected_bindings(
    revision: Optional[str], configuration: Optional[str], profile: Optional[str], rail: Optional[str], manufacturer: Optional[str], part_number: Optional[str]
) -> Dict[str, Optional[str]]:
    return {
        "rep01_git_revision": revision,
        "configuration_id": configuration,
        "profile_id": profile,
        "rail_id": rail,
        "manufacturer": manufacturer,
        "part_number": part_number,
    }


def _validate_artifacts(
    value: Any,
    artifact_root: Optional[Path],
    scope: Any,
    expected_bindings: Mapping[str, Optional[str]],
    issues: MutableSequence[_Issue],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int], Dict[str, bool]]:
    items = _array(value, "$.artifacts", issues, nonempty=True)
    if items is None:
        return [], [], {}, {}
    results: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    role_counts: Dict[str, int] = {}
    ready_by_role: Dict[str, bool] = {}
    seen_ids: Dict[str, str] = {}
    seen_paths: Dict[str, str] = {}
    for index, raw in enumerate(items):
        path = f"$.artifacts[{index}]"
        item = _object(raw, path, issues)
        if item is None:
            continue
        issue_start = len(issues)
        _closed(item, _ARTIFACT_FIELDS, path, issues)
        _required(item, _ARTIFACT_FIELDS, path, issues)
        artifact_id = _identifier(item.get("artifact_id"), f"{path}.artifact_id", issues)
        if artifact_id in {SPEC_SOURCE_ID, "REP02-PROJECT-INTAKE-DESCRIPTOR"}:
            _add(issues, "BLOCKED", "ARTIFACT_ID_RESERVED", f"{path}.artifact_id", "artifact id collides with an intake-owned source id")
        role = item.get("role")
        if not isinstance(role, str) or role not in _ARTIFACT_ROLES:
            _add(issues, "BLOCKED", "ARTIFACT_ROLE_INVALID", f"{path}.role", "artifact role is outside the closed role set")
            role = None
        relative = _safe_path(item.get("path"), f"{path}.path", issues)
        media_type = item.get("media_type")
        if not isinstance(media_type, str) or media_type not in _MEDIA_TYPES:
            _add(issues, "BLOCKED", "MEDIA_TYPE_INVALID", f"{path}.media_type", "media type is outside the closed media-type set")
            media_type = None
        version = _pinned_version(item.get("version"), f"{path}.version", issues)
        document_revision = _pinned_version(
            item.get("document_revision"), f"{path}.document_revision", issues
        )
        origin = item.get("origin")
        if not isinstance(origin, str) or origin not in _ORIGINS:
            _add(issues, "BLOCKED", "ARTIFACT_ORIGIN_INVALID", f"{path}.origin", "origin is outside the closed origin set")
            origin = None
        license_status = item.get("license_status")
        if not isinstance(license_status, str) or license_status not in _LICENSE_STATUSES:
            _add(issues, "BLOCKED", "LICENSE_STATUS_INVALID", f"{path}.license_status", "license_status is outside the closed license set")
            license_status = None
        if scope == "TEST":
            if origin != "INTERNAL_TEST_FIXTURE":
                _add(issues, "BLOCKED", "TEST_FIXTURE_ORIGIN_REQUIRED", f"{path}.origin", "TEST scope artifacts must be explicitly marked INTERNAL_TEST_FIXTURE")
        elif scope == "PROJECT" and origin == "INTERNAL_TEST_FIXTURE":
            _add(issues, "BLOCKED", "TEST_FIXTURE_FORBIDDEN_IN_PROJECT", f"{path}.origin", "PROJECT scope cannot promote an internal test fixture")
        if scope == "PROJECT" and role is not None:
            if role in (_BASE_ROLES | _SUPPORTING_ROLES) and origin != "REP01_PROJECT":
                _add(issues, "BLOCKED", "ARTIFACT_ORIGIN_ROLE_MISMATCH", f"{path}.origin", "REP-01 and supporting project roles must originate from REP01_PROJECT")
            elif role == "REGULATOR_DATASHEET" and origin != "MANUFACTURER":
                _add(issues, "BLOCKED", "ARTIFACT_ORIGIN_ROLE_MISMATCH", f"{path}.origin", "a project datasheet must originate from the manufacturer")
            elif role == "REGULATOR_MODEL" and origin not in {"MANUFACTURER", "AUTHORIZED_MODEL_PROVIDER"}:
                _add(issues, "BLOCKED", "ARTIFACT_ORIGIN_ROLE_MISMATCH", f"{path}.origin", "a project model must originate from the manufacturer or an authorized model provider")
        if role in {"REGULATOR_DATASHEET", "REGULATOR_MODEL"} and license_status not in {"VERIFIED", "DECLARED"}:
            _add(issues, "BLOCKED", "LICENSE_STATUS_REQUIRED", f"{path}.license_status", "datasheet and model licensing must be VERIFIED or DECLARED")
        expected_hash = _sha256(item.get("sha256"), f"{path}.sha256", issues)
        bindings = _object(item.get("bindings"), f"{path}.bindings", issues)
        if bindings is not None:
            _closed(bindings, _BINDING_FIELDS, f"{path}.bindings", issues)
        if artifact_id is not None:
            key = artifact_id.casefold()
            if key in seen_ids:
                _add(issues, "BLOCKED", "DUPLICATE_ARTIFACT_ID", f"{path}.artifact_id", f"artifact id duplicates {seen_ids[key]}")
            else:
                seen_ids[key] = path
        if relative is not None:
            key = relative.casefold()
            if key in seen_paths:
                _add(issues, "BLOCKED", "DUPLICATE_ARTIFACT_PATH", f"{path}.path", f"artifact path duplicates {seen_paths[key]}")
            else:
                seen_paths[key] = path
        if role is not None:
            role_counts[role] = role_counts.get(role, 0) + 1
            required_bindings = set(_ROLE_BINDINGS.get(role, ()))
            if bindings is not None:
                for name in sorted(required_bindings - set(bindings)):
                    _add(issues, "BLOCKED", "ARTIFACT_BINDING_MISSING", f"{path}.bindings.{name}", "role requires this cross-binding")
                for name, observed in bindings.items():
                    observed_value = _text(observed, f"{path}.bindings.{name}", issues, exact=name in {"manufacturer", "part_number"})
                    expected = expected_bindings.get(name)
                    if observed_value is not None and expected is not None and observed_value != expected:
                        _add(issues, "BLOCKED", "ARTIFACT_BINDING_MISMATCH", f"{path}.bindings.{name}", "artifact binding does not match the descriptor identity")

        observed_hash: Optional[str] = None
        resolved: Optional[Path] = None
        if artifact_root is not None and relative is not None:
            resolved = _resolve(artifact_root, relative)
            if resolved is None:
                _add(issues, "BLOCKED", "ARTIFACT_PATH_UNRESOLVED", f"{path}.path", "artifact path cannot be safely resolved beneath artifact_root")
            else:
                try:
                    if not resolved.is_file():
                        raise FileNotFoundError(str(resolved))
                    observed_hash = _hash_file(resolved)
                except OSError:
                    _add(issues, "BLOCKED", "ARTIFACT_FILE_MISSING", path, "declared artifact bytes are unavailable")
        elif artifact_root is None:
            _add(issues, "BLOCKED", "ARTIFACT_ROOT_REQUIRED", path, "explicit artifact_root is required for byte verification")
        if observed_hash is not None and expected_hash is not None and observed_hash != expected_hash:
            _add(issues, "BLOCKED", "ARTIFACT_HASH_MISMATCH", f"{path}.sha256", "declared SHA-256 does not match the exact artifact bytes")

        ready = len(issues) == issue_start and observed_hash is not None and observed_hash == expected_hash
        if role is not None:
            ready_by_role[role] = ready_by_role.get(role, False) or ready
        result = {
            "artifact_id": artifact_id,
            "role": role,
            "path": relative,
            "media_type": media_type,
            "version": version,
            "document_revision": document_revision,
            "origin": origin,
            "license_status": license_status,
            "expected_sha256": expected_hash,
            "observed_sha256": observed_hash,
            "status": "PASS" if ready else "BLOCKED",
            "reason_code": "ARTIFACT_BYTES_AND_BINDINGS_VERIFIED" if ready else "ARTIFACT_NOT_READY",
            "readiness_role": role in _READINESS_ROLES if role is not None else False,
        }
        results.append(result)
        if ready and artifact_id is not None and version is not None and observed_hash is not None:
            sources.append({"source_id": artifact_id, "version": version, "content_hash": observed_hash})
    return results, sources, role_counts, ready_by_role


def _validate_gaps(
    value: Any, artifact_roles: Mapping[str, int], issues: MutableSequence[_Issue]
) -> List[Dict[str, Any]]:
    items = _array(value, "$.artifact_gaps", issues)
    if items is None:
        return []
    output: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        path = f"$.artifact_gaps[{index}]"
        item = _object(raw, path, issues)
        if item is None:
            continue
        _closed(item, _GAP_FIELDS, path, issues)
        _required(item, _GAP_FIELDS, path, issues)
        gap_id = _identifier(item.get("gap_id"), f"{path}.gap_id", issues)
        role = item.get("role")
        if not isinstance(role, str) or role not in _ARTIFACT_ROLES:
            _add(issues, "BLOCKED", "ARTIFACT_ROLE_INVALID", f"{path}.role", "gap role is outside the closed role set")
            role = None
        reason = _identifier(item.get("reason_code"), f"{path}.reason_code", issues)
        if gap_id is not None:
            key = gap_id.casefold()
            if key in seen:
                _add(issues, "BLOCKED", "DUPLICATE_GAP_ID", f"{path}.gap_id", "gap id must be unique")
            seen.add(key)
        if role is not None and artifact_roles.get(role, 0):
            _add(issues, "BLOCKED", "ARTIFACT_AVAILABILITY_CONFLICT", path, "the same role is declared both available and as a gap")
        output.append({"gap_id": gap_id, "role": role, "reason_code": reason})
    return output


def validate_project_intake(
    document: Any, *, artifact_root: Optional[Union[str, Path]]
) -> Dict[str, Any]:
    """Validate one descriptor and its exact local artifact bytes.

    ``PASS`` means only that the requested downstream capabilities have a
    complete, internally consistent, byte-bound input set.  Every downstream
    electrical engine remains ``NOT_RUN``.
    """

    issues: List[_Issue] = []
    spec_source = _spec_source()
    sources: List[Dict[str, Any]] = []
    if spec_source is None:
        _add(issues, "BLOCKED", "CONTRACT_SOURCE_UNAVAILABLE", "$", "packaged intake contract bytes failed their pinned identity check")
    else:
        sources.append(spec_source)

    try:
        calculated_hash: Optional[str] = descriptor_input_hash(document)
    except (TypeError, ValueError, OverflowError, RecursionError):
        calculated_hash = None
        _add(issues, "BLOCKED", "INPUT_HASH_UNCOMPUTABLE", "$", "descriptor cannot be canonically hashed")
    if calculated_hash is not None:
        sources.append(
            {
                "source_id": "REP02-PROJECT-INTAKE-DESCRIPTOR",
                "version": DESCRIPTOR_SCHEMA_VERSION,
                "content_hash": calculated_hash,
            }
        )

    root = _object(document, "$", issues)
    capabilities: List[str] = []
    artifact_results: List[Dict[str, Any]] = []
    gap_results: List[Dict[str, Any]] = []
    required_roles: List[str] = []
    project_provenance: Dict[str, Any] = {
        "status": "NOT_RUN",
        "reason_code": "DESCRIPTOR_SCOPE_UNAVAILABLE",
        "revision": None,
        "working_tree": "NOT_RUN",
        "committed_artifacts_verified": 0,
    }
    if root is not None:
        _closed(root, _ROOT_FIELDS, "$", issues)
        _required(root, _ROOT_FIELDS, "$", issues)
        if root.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
            _add(issues, "BLOCKED", "SCHEMA_VERSION_INCOMPATIBLE", "$.schema_version", f"schema_version must be {DESCRIPTOR_SCHEMA_VERSION!r}")
        _identifier(root.get("descriptor_id"), "$.descriptor_id", issues)
        if root.get("scope") not in {"PROJECT", "TEST"}:
            _add(issues, "BLOCKED", "SCOPE_INVALID", "$.scope", "scope must be PROJECT or TEST")

        rep01 = _object(root.get("rep01"), "$.rep01", issues)
        revision = configuration = profile = rail = None
        if rep01 is not None:
            _closed(rep01, _REP01_FIELDS, "$.rep01", issues)
            _required(rep01, _REP01_FIELDS, "$.rep01", issues)
            if rep01.get("repository_id") != "REP-01":
                _add(issues, "BLOCKED", "REPOSITORY_IDENTITY_MISMATCH", "$.rep01.repository_id", "repository_id must be exactly 'REP-01'")
            revision = _string_state(rep01.get("git_revision"), "$.rep01.git_revision", issues, revision=True)
            configuration = _string_state(rep01.get("configuration_id"), "$.rep01.configuration_id", issues, exact=True)
            profile = _string_state(rep01.get("profile_id"), "$.rep01.profile_id", issues, exact=True)
            rail = _string_state(rep01.get("rail_id"), "$.rep01.rail_id", issues, exact=True)

        topology = _string_state(root.get("topology"), "$.topology", issues, exact=True)
        if topology is not None and topology != "BUCK":
            _add(issues, "BLOCKED", "TOPOLOGY_UNSUPPORTED", "$.topology.value", "this first intake contract supports exactly one BUCK slice")

        requested = _array(root.get("requested_capabilities"), "$.requested_capabilities", issues, nonempty=True)
        if requested is not None:
            seen_capabilities: set[str] = set()
            for index, value in enumerate(requested):
                path = f"$.requested_capabilities[{index}]"
                if not isinstance(value, str) or value not in _CAPABILITIES:
                    _add(issues, "BLOCKED", "CAPABILITY_INVALID", path, "capability is outside the closed capability set")
                    continue
                if value in seen_capabilities:
                    _add(issues, "BLOCKED", "DUPLICATE_CAPABILITY", path, "requested capabilities must be unique")
                    continue
                seen_capabilities.add(value)
                capabilities.append(value)
        required_roles = _required_roles(capabilities)

        envelope = _object(root.get("operating_envelope"), "$.operating_envelope", issues)
        semantic_values: Dict[str, Optional[Decimal]] = {}
        if envelope is not None:
            _closed(envelope, _SEMANTIC_UNITS, "$.operating_envelope", issues)
            _required(envelope, _SEMANTIC_UNITS, "$.operating_envelope", issues)
            for name, unit in _SEMANTIC_UNITS.items():
                semantic_values[name] = _numeric_state(envelope.get(name), f"$.operating_envelope.{name}", unit, issues)
            for name in ("vin_min_v", "vin_nom_v", "vin_max_v", "vout_target_v"):
                value = semantic_values.get(name)
                if value is not None and value <= 0:
                    _add(issues, "BLOCKED", "SEMANTIC_RANGE_INVALID", f"$.operating_envelope.{name}.value", "voltage must be greater than zero")
            for name in ("load_min_a", "load_nom_a", "load_max_a"):
                value = semantic_values.get(name)
                if value is not None and value < 0:
                    _add(issues, "BLOCKED", "SEMANTIC_RANGE_INVALID", f"$.operating_envelope.{name}.value", "load current must not be negative")
            order_sets = (
                ("vin_min_v", "vin_nom_v", "vin_max_v"),
                ("load_min_a", "load_nom_a", "load_max_a"),
                ("ambient_min_c", "ambient_max_c"),
            )
            for names in order_sets:
                values = [semantic_values.get(name) for name in names]
                if all(value is not None for value in values) and values != sorted(values):
                    _add(issues, "BLOCKED", "OPERATING_ENVELOPE_ORDER_CONFLICT", "$.operating_envelope", f"expected {' <= '.join(names)}")

        regulator = _object(root.get("regulator"), "$.regulator", issues)
        manufacturer = part_number = None
        if regulator is not None:
            _closed(regulator, _REGULATOR_FIELDS, "$.regulator", issues)
            _required(regulator, _REGULATOR_FIELDS, "$.regulator", issues)
            manufacturer = _string_state(regulator.get("manufacturer"), "$.regulator.manufacturer", issues, exact=True)
            part_number = _string_state(regulator.get("part_number"), "$.regulator.part_number", issues, exact=True)

        explicit_root: Optional[Path]
        if artifact_root is None:
            explicit_root = None
        else:
            try:
                explicit_root = Path(artifact_root)
            except TypeError:
                explicit_root = None
                _add(issues, "BLOCKED", "ARTIFACT_ROOT_INVALID", "$.artifacts", "artifact_root is not path-like")

        bindings = _expected_bindings(revision, configuration, profile, rail, manufacturer, part_number)
        artifact_results, artifact_sources, role_counts, ready_by_role = _validate_artifacts(
            root.get("artifacts"), explicit_root, root.get("scope"), bindings, issues
        )
        sources.extend(artifact_sources)
        _block_duplicate_readiness_content(artifact_results, issues)
        project_provenance = _project_git_provenance(
            root.get("scope"), explicit_root, revision, artifact_results, issues
        )
        ready_by_role = {}
        for artifact_result in artifact_results:
            role = artifact_result.get("role")
            if isinstance(role, str) and artifact_result.get("status") == "PASS":
                ready_by_role[role] = True
        gap_results = _validate_gaps(root.get("artifact_gaps"), role_counts, issues)
        for role in required_roles:
            count = role_counts.get(role, 0)
            if count == 0:
                _add(issues, "BLOCKED", "REQUIRED_ARTIFACT_ROLE_MISSING", "$.artifacts", f"requested capabilities require role {role}")
            elif count > 1:
                _add(issues, "BLOCKED", "REQUIRED_ARTIFACT_ROLE_AMBIGUOUS", "$.artifacts", f"readiness role {role} must resolve to exactly one artifact")
            elif not ready_by_role.get(role, False):
                _add(issues, "BLOCKED", "REQUIRED_ARTIFACT_NOT_READY", "$.artifacts", f"role {role} did not pass byte and binding verification")

        declared_hash = _sha256(root.get("input_hash"), "$.input_hash", issues)
        if declared_hash is not None and calculated_hash is not None and declared_hash != calculated_hash:
            _add(issues, "BLOCKED", "INPUT_HASH_MISMATCH", "$.input_hash", "declared input_hash does not match the canonical descriptor")

    issues = sorted(issues, key=lambda item: (item.path, item.reason_code, item.status, item.message))
    statuses = [item.status for item in issues]
    status = max(statuses, key=lambda item: _STATUS_RANK[item]) if statuses else "PASS"
    reason_code = "INTAKE_EXECUTION_READY" if status == "PASS" else next(
        item.reason_code for item in issues if item.status == status
    )
    sources = sorted(sources, key=lambda item: (item["source_id"], item["version"], item["content_hash"]))
    source_refs = [item["source_id"] for item in sources]
    limitations = [
        "EXECUTION_READINESS_ONLY",
        "NO_ELECTRICAL_COMPLIANCE_CLAIM",
        "NO_HARDWARE_VALIDATION_CLAIM",
        "NO_G1_G6_CLOSURE_CLAIM",
        "DOWNSTREAM_ENGINES_NOT_RUN",
        "NO_ARTIFACT_SEMANTIC_EXTRACTION",
        "DECLARED_ORIGIN_NOT_AUTHENTICATED",
        "DESCRIPTOR_HASH_IS_CANONICAL_JSON_NOT_SOURCE_FILE_BYTES",
    ]
    if isinstance(document, dict) and document.get("scope") == "TEST":
        limitations.append("SYNTHETIC_TEST_ONLY")
    downstream = [
        {"capability": capability, "status": "NOT_RUN", "reason_code": "DOWNSTREAM_ENGINE_NOT_RUN"}
        for capability in ("BUCK", "PIN", "TRANSIENT", "LOOP_STABILITY")
    ]
    capability_results = [
        {
            "capability": capability,
            "status": status,
            "reason_code": "INPUTS_READY" if status == "PASS" else ("MANDATORY_SEMANTIC_UNKNOWN" if status == "UNKNOWN" else "INTAKE_BLOCKED"),
        }
        for capability in sorted(capabilities)
    ]
    return {
        "rule_id": RULE_ID,
        "status": status,
        "reason_code": reason_code,
        "input_hash": calculated_hash,
        "tool_name": TOOL_NAME,
        "tool_version": ENGINE_VERSION,
        "package_version": PACKAGE_VERSION,
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "sources": sources,
        "source_refs": source_refs,
        "requested_capabilities": sorted(capabilities),
        "required_artifact_roles": required_roles,
        "capability_results": capability_results,
        "artifact_results": sorted(artifact_results, key=lambda item: ((item.get("artifact_id") or ""), (item.get("path") or ""))),
        "artifact_gaps": sorted(gap_results, key=lambda item: ((item.get("gap_id") or ""), (item.get("role") or ""))),
        "project_provenance": project_provenance,
        "downstream_evaluations": downstream,
        "issues": [
            item.as_dict(
                index,
                [SPEC_SOURCE_ID]
                if spec_source is not None
                else (
                    ["REP02-PROJECT-INTAKE-DESCRIPTOR"]
                    if calculated_hash is not None
                    else []
                ),
            )
            for index, item in enumerate(issues, start=1)
        ],
        "limitations": limitations,
    }


__all__ = [
    "DESCRIPTOR_SCHEMA_VERSION",
    "ENGINE_VERSION",
    "RULE_ID",
    "descriptor_input_hash",
    "validate_project_intake",
]
