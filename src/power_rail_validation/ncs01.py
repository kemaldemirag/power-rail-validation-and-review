"""Deterministic feasibility model for the NCS-01 Nightstand case study.

The model deliberately stays in the power domain.  It does not convert the
handoff-only 45 W label statement into voltage, current, USB-PD contracts, or a
rail-budget input.  Portfolio assumptions can expose arithmetic boundaries,
but they can never produce a hardware PASS.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.cli import EXIT_CODES, EXIT_INTERNAL, EXIT_USAGE_OR_INPUT
from power_rail_validation.contracts import load_json, sha256_hexdigest


MODEL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
RULE_ID = "REP02.NCS01.NIGHTSTAND"
TOOL_NAME = "power-rail-validation-ncs01"

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
_FLOATING_VERSION_SEGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:current|develop|development|head|latest|main|master|nightly|stable|tbd|tip|trunk|unknown|unspecified|branch|branches|heads)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_STATUSES = ("BLOCKED", "FAIL", "UNKNOWN", "NOT_RUN", "PASS")
_STATUS_RANK = {status: index for index, status in enumerate(reversed(_STATUSES))}
_CLAIM_LIMITS = (
    "PORTFOLIO_WHAT_IF_ONLY",
    "LABEL_POWER_IS_NOT_MEASURED_POWER",
    "LABEL_POWER_IS_NOT_RAIL_CAPACITY",
    "PD_CONTRACTS_UNKNOWN",
    "NO_HARDWARE_PASS",
    "NO_EXACT_PART_SELECTION",
)
_EVIDENCE_CLASSES = frozenset(
    {
        "SOURCE_EXPLICIT",
        "DERIVED",
        "ENGINEERING_RULE",
        "SECONDARY_SOURCE",
        "HANDOFF_ONLY",
        "INTERNAL_RULE",
        "ASSUMPTION",
        "UNKNOWN",
        "CONFLICT",
    }
)
_FACT_NAMES = frozenset(
    {
        "label_total_power",
        "measured_continuous_power",
        "pd_port_count",
        "pd_profiles",
        "simultaneous_power_policy",
    }
)
_VERIFICATION_CHECKS = (
    "SOURCE_BINDING",
    "LOAD_DEFINITION",
    "PORT_PROFILE",
    "SIMULTANEOUS_POWER_POLICY",
    "STATIC_CAPACITY",
    "DERATING",
    "EFFICIENCY_LOSS",
    "STARTUP_SHUTDOWN",
    "BACKFEED_POWER_OFF",
    "NEGOTIATION",
    "FAULT_RECOVERY",
    "THERMAL",
    "COMPLIANCE",
    "MEASUREMENT_INTEGRITY",
    "MODEL_REPRODUCIBILITY",
    "INTERFACE_MAP",
    "MODULE_SWAP_REPEATABILITY",
    "HYBRID_BOUNDARY_CONTRACT",
    "CONTROL_TELEMETRY_FAIL_SAFE",
    "INTERCONNECT_DROP_THERMAL",
    "EXACT_PART_PROVENANCE",
    "SCHEMATIC_LAYOUT_REVIEW",
    "LOOP_STABILITY",
    "BRING_UP",
)
_COMMON_ARCHITECTURE_EVIDENCE = (
    "SOURCE_BINDING",
    "LOAD_DEFINITION",
    "PORT_PROFILE",
    "SIMULTANEOUS_POWER_POLICY",
    "STATIC_CAPACITY",
    "DERATING",
    "EFFICIENCY_LOSS",
    "INTERFACE_MAP",
    "STARTUP_SHUTDOWN",
    "BACKFEED_POWER_OFF",
    "NEGOTIATION",
    "FAULT_RECOVERY",
    "THERMAL",
    "COMPLIANCE",
    "MEASUREMENT_INTEGRITY",
    "MODEL_REPRODUCIBILITY",
)
_REQUIRED_EVIDENCE_BY_ARCHITECTURE = {
    "READY_MODULE": _COMMON_ARCHITECTURE_EVIDENCE
    + ("MODULE_SWAP_REPEATABILITY",),
    "HYBRID": _COMMON_ARCHITECTURE_EVIDENCE
    + (
        "MODULE_SWAP_REPEATABILITY",
        "HYBRID_BOUNDARY_CONTRACT",
        "CONTROL_TELEMETRY_FAIL_SAFE",
        "INTERCONNECT_DROP_THERMAL",
    ),
    "CUSTOM_PCB": _COMMON_ARCHITECTURE_EVIDENCE
    + (
        "EXACT_PART_PROVENANCE",
        "SCHEMATIC_LAYOUT_REVIEW",
        "LOOP_STABILITY",
        "BRING_UP",
    ),
}
_TOP_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "title",
        "claim_limits",
        "sources",
        "facts",
        "scenarios",
        "architectures",
    }
)


class Ncs01InputError(ValueError):
    """Raised when an NCS-01 case document is structurally unsafe or invalid."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Ncs01InputError(f"{path}: expected an object")
    return value


def _sequence(value: Any, path: str, *, nonempty: bool = False) -> Sequence[Any]:
    if not isinstance(value, list):
        raise Ncs01InputError(f"{path}: expected an array")
    if nonempty and not value:
        raise Ncs01InputError(f"{path}: array must not be empty")
    return value


def _closed(value: Mapping[str, Any], allowed: Iterable[str], path: str) -> None:
    unexpected = sorted(key for key in value if key not in set(allowed))
    if unexpected:
        raise Ncs01InputError(f"{path}: unexpected field {unexpected[0]!r}")


def _required(value: Mapping[str, Any], fields: Iterable[str], path: str) -> None:
    missing = sorted(field for field in fields if field not in value)
    if missing:
        raise Ncs01InputError(f"{path}: missing required field {missing[0]!r}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Ncs01InputError(f"{path}: expected a non-empty string")
    return value


def _identifier(value: Any, path: str) -> str:
    text = _text(value, path)
    if _ID_RE.fullmatch(text) is None:
        raise Ncs01InputError(f"{path}: expected a stable identifier")
    return text


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise Ncs01InputError(f"{path}: expected a lowercase SHA-256 digest")
    return value


def _is_floating_version(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _FLOATING_VERSION_TOKENS:
        return True
    if _FLOATING_VERSION_SEGMENT_RE.search(normalized) is not None:
        return True
    if any(marker in normalized for marker in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    if (
        re.search(r"\s+-\s+", normalized) is not None
        or "–" in normalized
        or "—" in normalized
        or ".." in normalized
        or re.search(r"(?<![A-Za-z0-9])to(?![A-Za-z0-9])", normalized) is not None
    ):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _version(value: Any, path: str) -> str:
    text = _text(value, path)
    if text != text.strip():
        raise Ncs01InputError(f"{path}: version must not contain leading or trailing whitespace")
    if _is_floating_version(text):
        raise Ncs01InputError(f"{path}: version must be pinned rather than a moving alias or range")
    return text


def _write_utf8(stream: Any, value: str) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(value)
        return
    buffer.write(value.encode("utf-8", errors="strict"))
    buffer.flush()


def _number(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Ncs01InputError(f"{path}: expected a finite JSON number")
    if isinstance(value, float) and not math.isfinite(value):
        raise Ncs01InputError(f"{path}: expected a finite JSON number")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Ncs01InputError(f"{path}: expected a finite JSON number") from None
    if not converted.is_finite() or converted < 0:
        raise Ncs01InputError(f"{path}: expected a non-negative finite number")
    return converted


def _json_number(value: Decimal) -> Any:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)


def _refs(value: Any, path: str, source_ids: Optional[set[str]] = None) -> Tuple[str, ...]:
    result: List[str] = []
    for index, item in enumerate(_sequence(value, path)):
        identifier = _identifier(item, f"{path}[{index}]")
        if identifier in result:
            raise Ncs01InputError(f"{path}[{index}]: duplicate source reference")
        if source_ids is not None and identifier not in source_ids:
            raise Ncs01InputError(f"{path}[{index}]: source reference does not resolve")
        result.append(identifier)
    return tuple(result)


def _validate_quantity(value: Any, path: str, *, allow_unknown: bool = True) -> Dict[str, Any]:
    item = _mapping(value, path)
    _closed(item, {"state", "value", "unit", "provenance"}, path)
    _required(item, {"state", "value", "unit", "provenance"}, path)
    state = item["state"]
    if state not in ({"KNOWN", "UNKNOWN"} if allow_unknown else {"KNOWN"}):
        raise Ncs01InputError(f"{path}.state: expected KNOWN" + (" or UNKNOWN" if allow_unknown else ""))
    unit = _text(item["unit"], f"{path}.unit")
    provenance = item["provenance"]
    if provenance not in _EVIDENCE_CLASSES:
        raise Ncs01InputError(f"{path}.provenance: unsupported evidence class")
    if state == "UNKNOWN":
        if item["value"] is not None:
            raise Ncs01InputError(f"{path}.value: UNKNOWN quantity must be null")
        if provenance not in {"UNKNOWN", "CONFLICT"}:
            raise Ncs01InputError(f"{path}.provenance: UNKNOWN quantity requires UNKNOWN or CONFLICT")
        return {"state": state, "value": None, "unit": unit, "provenance": provenance}
    number = _number(item["value"], f"{path}.value")
    if provenance in {"UNKNOWN", "CONFLICT"}:
        raise Ncs01InputError(f"{path}.provenance: KNOWN quantity cannot use {provenance}")
    return {
        "state": state,
        "value": _json_number(number),
        "unit": unit,
        "provenance": provenance,
    }


def _validate_fact_value(name: str, value: Any, path: str) -> Dict[str, Any]:
    """Validate typed facts without pretending every fact is a scalar quantity."""

    item = _mapping(value, path)
    fields = {"state", "value", "unit", "provenance"}
    _closed(item, fields, path)
    _required(item, fields, path)
    state = item["state"]
    if state not in {"KNOWN", "UNKNOWN"}:
        raise Ncs01InputError(f"{path}.state: expected KNOWN or UNKNOWN")
    provenance = item["provenance"]
    if provenance not in _EVIDENCE_CLASSES:
        raise Ncs01InputError(f"{path}.provenance: unsupported evidence class")
    expected_units = {
        "label_total_power": "W",
        "measured_continuous_power": "W",
        "pd_port_count": "COUNT",
        "pd_profiles": "PD_PROFILE_REF_SET",
        "simultaneous_power_policy": "POLICY_REF",
    }
    unit = _text(item["unit"], f"{path}.unit")
    if unit != expected_units[name]:
        raise Ncs01InputError(f"{path}.unit: expected {expected_units[name]}")
    if state == "UNKNOWN":
        if item["value"] is not None:
            raise Ncs01InputError(f"{path}.value: UNKNOWN fact must be null")
        if provenance not in {"UNKNOWN", "CONFLICT"}:
            raise Ncs01InputError(f"{path}.provenance: UNKNOWN fact requires UNKNOWN or CONFLICT")
        return {"state": state, "value": None, "unit": unit, "provenance": provenance}
    if provenance in {"UNKNOWN", "CONFLICT"}:
        raise Ncs01InputError(f"{path}.provenance: KNOWN fact cannot use {provenance}")

    if name in {"label_total_power", "measured_continuous_power"}:
        normalized_value: Any = _json_number(_number(item["value"], f"{path}.value"))
    elif name == "pd_port_count":
        raw_count = item["value"]
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count <= 0:
            raise Ncs01InputError(f"{path}.value: expected a positive integer port count")
        normalized_value = raw_count
    elif name == "pd_profiles":
        profiles: List[str] = []
        for index, profile in enumerate(_sequence(item["value"], f"{path}.value", nonempty=True)):
            profile_id = _identifier(profile, f"{path}.value[{index}]")
            if profile_id in profiles:
                raise Ncs01InputError(f"{path}.value[{index}]: duplicate profile identifier")
            profiles.append(profile_id)
        normalized_value = profiles
    else:
        normalized_value = _identifier(item["value"], f"{path}.value")
    return {
        "state": state,
        "value": normalized_value,
        "unit": unit,
        "provenance": provenance,
    }


def _validate_case(document: Any) -> Dict[str, Any]:
    case = _mapping(document, "$")
    _closed(case, _TOP_FIELDS, "$")
    _required(case, _TOP_FIELDS, "$")
    if case["schema_version"] != SCHEMA_VERSION:
        raise Ncs01InputError("$.schema_version: unsupported schema version")
    if case["case_id"] != "NCS-01":
        raise Ncs01InputError("$.case_id: expected NCS-01")
    _text(case["title"], "$.title")

    claim_limits: List[str] = []
    for index, item in enumerate(_sequence(case["claim_limits"], "$.claim_limits", nonempty=True)):
        limit = _identifier(item, f"$.claim_limits[{index}]")
        if limit in claim_limits:
            raise Ncs01InputError(f"$.claim_limits[{index}]: duplicate claim limit")
        claim_limits.append(limit)
    if tuple(claim_limits) != _CLAIM_LIMITS:
        raise Ncs01InputError(
            "$.claim_limits: expected the exact NCS-01 safety claim-limit set and order"
        )

    sources: List[Dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, value in enumerate(_sequence(case["sources"], "$.sources", nonempty=True)):
        path = f"$.sources[{index}]"
        item = _mapping(value, path)
        fields = {
            "source_id",
            "version",
            "locator",
            "content_hash",
            "access_status",
            "classification",
            "supports",
        }
        _closed(item, fields, path)
        _required(item, fields, path)
        source_id = _identifier(item["source_id"], f"{path}.source_id")
        if source_id in source_ids:
            raise Ncs01InputError(f"{path}.source_id: duplicate source identifier")
        source_ids.add(source_id)
        version = _version(item["version"], f"{path}.version")
        locator = _text(item["locator"], f"{path}.locator")
        pure = PurePosixPath(locator)
        if pure.is_absolute() or "\\" in locator or ":" in locator or any(part in {"", ".", ".."} for part in pure.parts):
            raise Ncs01InputError(f"{path}.locator: expected a safe relative POSIX path")
        hash_value = _mapping(item["content_hash"], f"{path}.content_hash")
        _closed(hash_value, {"state", "algorithm", "value"}, f"{path}.content_hash")
        _required(hash_value, {"state", "algorithm", "value"}, f"{path}.content_hash")
        if hash_value["state"] != "KNOWN" or hash_value["algorithm"] != "SHA-256":
            raise Ncs01InputError(f"{path}.content_hash: expected KNOWN SHA-256")
        digest = _sha256(hash_value["value"], f"{path}.content_hash.value")
        access_status = item["access_status"]
        if access_status not in {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"}:
            raise Ncs01InputError(f"{path}.access_status: unsupported access status")
        classification = item["classification"]
        if classification not in _EVIDENCE_CLASSES:
            raise Ncs01InputError(f"{path}.classification: unsupported evidence class")
        supports = []
        for support_index, support in enumerate(_sequence(item["supports"], f"{path}.supports", nonempty=True)):
            support_id = _identifier(support, f"{path}.supports[{support_index}]")
            if support_id in supports:
                raise Ncs01InputError(f"{path}.supports[{support_index}]: duplicate supported claim")
            supports.append(support_id)
        sources.append(
            {
                "source_id": source_id,
                "version": version,
                "locator": locator,
                "content_hash": {"state": "KNOWN", "algorithm": "SHA-256", "value": digest},
                "access_status": access_status,
                "classification": classification,
                "supports": supports,
            }
        )

    facts_value = _mapping(case["facts"], "$.facts")
    _closed(facts_value, _FACT_NAMES, "$.facts")
    _required(facts_value, _FACT_NAMES, "$.facts")
    facts: Dict[str, Any] = {}
    for name in sorted(_FACT_NAMES):
        path = f"$.facts.{name}"
        item = _mapping(facts_value[name], path)
        fields = {"state", "value", "unit", "basis", "provenance", "source_refs"}
        _closed(item, fields, path)
        _required(item, fields, path)
        fact_value = _validate_fact_value(
            name,
            {key: item[key] for key in ("state", "value", "unit", "provenance")},
            path,
        )
        basis = _identifier(item["basis"], f"{path}.basis")
        refs = _refs(item["source_refs"], f"{path}.source_refs", source_ids)
        if not refs:
            raise Ncs01InputError(f"{path}.source_refs: at least one source is required")
        facts[name] = {**fact_value, "basis": basis, "source_refs": list(refs)}

    source_classes = {item["source_id"]: item["classification"] for item in sources}
    source_supports = {item["source_id"]: set(item["supports"]) for item in sources}
    for name, fact in facts.items():
        provenance = fact["provenance"]
        if provenance in {"SOURCE_EXPLICIT", "SECONDARY_SOURCE", "HANDOFF_ONLY"} and any(
            source_classes[source_id] != provenance for source_id in fact["source_refs"]
        ):
            raise Ncs01InputError(
                f"$.facts.{name}.provenance: every referenced source must have matching classification"
            )
        if fact["state"] == "KNOWN" and provenance in {
            "SOURCE_EXPLICIT",
            "SECONDARY_SOURCE",
            "HANDOFF_ONLY",
        } and any(fact["basis"] not in source_supports[source_id] for source_id in fact["source_refs"]):
            raise Ncs01InputError(
                f"$.facts.{name}.basis: every referenced source must declare the supported claim"
            )

    label = facts["label_total_power"]
    if label["state"] != "KNOWN" or label["unit"] != "W":
        raise Ncs01InputError("$.facts.label_total_power: expected a KNOWN watt quantity")
    if Decimal(str(label["value"])) != Decimal("45"):
        raise Ncs01InputError(
            "$.facts.label_total_power.value: NCS-01 requires the case-defining 45 W label"
        )
    measured = facts["measured_continuous_power"]
    if measured["unit"] != "W":
        raise Ncs01InputError("$.facts.measured_continuous_power.unit: expected W")

    scenarios: List[Dict[str, Any]] = []
    scenario_ids: set[str] = set()
    for index, value in enumerate(_sequence(case["scenarios"], "$.scenarios", nonempty=True)):
        path = f"$.scenarios[{index}]"
        item = _mapping(value, path)
        fields = {"scenario_id", "description", "scenario_kind", "capacity"}
        _closed(item, fields, path)
        _required(item, fields, path)
        scenario_id = _identifier(item["scenario_id"], f"{path}.scenario_id")
        if scenario_id in scenario_ids:
            raise Ncs01InputError(f"{path}.scenario_id: duplicate scenario identifier")
        scenario_ids.add(scenario_id)
        description = _text(item["description"], f"{path}.description")
        scenario_kind = item["scenario_kind"]
        if scenario_kind not in {"KNOWN_GAP", "PORTFOLIO_ASSUMPTION"}:
            raise Ncs01InputError(f"{path}.scenario_kind: unsupported scenario kind")
        capacity = _validate_quantity(item["capacity"], f"{path}.capacity")
        if capacity["unit"] != "W":
            raise Ncs01InputError(f"{path}.capacity.unit: expected W")
        if scenario_kind == "PORTFOLIO_ASSUMPTION" and capacity["provenance"] != "ASSUMPTION":
            raise Ncs01InputError(f"{path}.capacity.provenance: portfolio scenario must be ASSUMPTION")
        if scenario_kind == "KNOWN_GAP" and capacity["state"] != "UNKNOWN":
            raise Ncs01InputError(f"{path}.capacity: known-gap scenario must remain UNKNOWN")
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "description": description,
                "scenario_kind": scenario_kind,
                "capacity": capacity,
            }
        )

    architectures: List[Dict[str, Any]] = []
    architecture_ids: set[str] = set()
    for index, value in enumerate(_sequence(case["architectures"], "$.architectures", nonempty=True)):
        path = f"$.architectures[{index}]"
        item = _mapping(value, path)
        fields = {
            "architecture_id",
            "description",
            "recommendation",
            "recommendation_basis",
            "fallback",
            "future_option",
            "partition",
            "required_evidence",
        }
        _closed(item, fields, path)
        _required(item, fields, path)
        architecture_id = _identifier(item["architecture_id"], f"{path}.architecture_id")
        if architecture_id in architecture_ids:
            raise Ncs01InputError(f"{path}.architecture_id: duplicate architecture identifier")
        architecture_ids.add(architecture_id)
        description = _text(item["description"], f"{path}.description")
        recommendation = _identifier(item["recommendation"], f"{path}.recommendation")
        recommendation_basis = item["recommendation_basis"]
        if recommendation_basis not in _EVIDENCE_CLASSES:
            raise Ncs01InputError(f"{path}.recommendation_basis: unsupported evidence class")
        if recommendation_basis not in {"ENGINEERING_RULE", "ASSUMPTION", "INTERNAL_RULE"}:
            raise Ncs01InputError(f"{path}.recommendation_basis: expected a non-source decision basis")
        if not isinstance(item["fallback"], bool) or not isinstance(item["future_option"], bool):
            raise Ncs01InputError(f"{path}: fallback and future_option must be booleans")
        partition = [
            _text(part, f"{path}.partition[{part_index}]")
            for part_index, part in enumerate(_sequence(item["partition"], f"{path}.partition", nonempty=True))
        ]
        required_evidence: List[str] = []
        for check_index, check in enumerate(
            _sequence(item["required_evidence"], f"{path}.required_evidence", nonempty=True)
        ):
            check_id = _identifier(check, f"{path}.required_evidence[{check_index}]")
            if check_id not in _VERIFICATION_CHECKS:
                raise Ncs01InputError(f"{path}.required_evidence[{check_index}]: unknown verification check")
            if check_id in required_evidence:
                raise Ncs01InputError(f"{path}.required_evidence[{check_index}]: duplicate verification check")
            required_evidence.append(check_id)
        expected_evidence = _REQUIRED_EVIDENCE_BY_ARCHITECTURE.get(architecture_id)
        if expected_evidence is not None and set(required_evidence) != set(expected_evidence):
            missing = sorted(set(expected_evidence) - set(required_evidence))
            extra = sorted(set(required_evidence) - set(expected_evidence))
            detail = f"missing={missing}, extra={extra}"
            raise Ncs01InputError(
                f"{path}.required_evidence: architecture-specific mandatory set mismatch ({detail})"
            )
        architectures.append(
            {
                "architecture_id": architecture_id,
                "description": description,
                "recommendation": recommendation,
                "recommendation_basis": recommendation_basis,
                "fallback": item["fallback"],
                "future_option": item["future_option"],
                "partition": partition,
                "required_evidence": required_evidence,
            }
        )

    if {item["architecture_id"] for item in architectures} != {"READY_MODULE", "HYBRID", "CUSTOM_PCB"}:
        raise Ncs01InputError("$.architectures: expected READY_MODULE, HYBRID, and CUSTOM_PCB exactly once")
    preferred = [item for item in architectures if item["recommendation"] == "PREFERRED_PROVISIONAL"]
    if len(preferred) != 1:
        raise Ncs01InputError("$.architectures: exactly one PREFERRED_PROVISIONAL architecture is required")
    fallbacks = [item for item in architectures if item["fallback"]]
    future_options = [item for item in architectures if item["future_option"]]
    if len(fallbacks) != 1:
        raise Ncs01InputError("$.architectures: exactly one fallback architecture is required")
    if len(future_options) != 1:
        raise Ncs01InputError("$.architectures: exactly one future option is required")
    role_ids = {
        preferred[0]["architecture_id"],
        fallbacks[0]["architecture_id"],
        future_options[0]["architecture_id"],
    }
    if len(role_ids) != 3:
        raise Ncs01InputError(
            "$.architectures: preferred, fallback, and future option must be distinct"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": "NCS-01",
        "title": case["title"],
        "claim_limits": claim_limits,
        "sources": sources,
        "facts": facts,
        "scenarios": scenarios,
        "architectures": architectures,
    }


def _source_result(source: Mapping[str, Any], source_root: Path) -> Dict[str, Any]:
    declared_hash = source["content_hash"]["value"]
    root = source_root.resolve(strict=True)
    relative = PurePosixPath(source["locator"])
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise Ncs01InputError(
            f"$.sources[{source['source_id']}].locator: resolved path escapes source root"
        ) from None

    status = "PASS"
    reason_code = "SOURCE_HASH_VERIFIED"
    observed_hash: Optional[str] = None
    if source["access_status"] != "AVAILABLE":
        status = "BLOCKED"
        reason_code = "SOURCE_DECLARED_UNAVAILABLE"
    elif not candidate.exists():
        status = "BLOCKED"
        reason_code = "SOURCE_UNAVAILABLE"
    elif candidate.is_symlink():
        status = "BLOCKED"
        reason_code = "SOURCE_SYMLINK_FORBIDDEN"
    elif not candidate.is_file():
        status = "BLOCKED"
        reason_code = "SOURCE_NOT_REGULAR_FILE"
    else:
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        observed_hash = digest.hexdigest()
        if observed_hash != declared_hash:
            status = "BLOCKED"
            reason_code = "SOURCE_HASH_MISMATCH"
    return {
        **copy.deepcopy(dict(source)),
        "observed_sha256": observed_hash,
        "verification_status": status,
        "reason_code": reason_code,
    }


def _fact_is_unknown(fact: Mapping[str, Any]) -> bool:
    return fact["state"] == "UNKNOWN" or fact["provenance"] in {"UNKNOWN", "CONFLICT"}


def _fact_gate(
    fact: Mapping[str, Any],
    *,
    trusted_provenance: Iterable[str],
    unknown_reason: str,
    success_reason: str,
) -> Tuple[str, str]:
    if fact["provenance"] == "CONFLICT":
        return "BLOCKED", "FACT_PROVENANCE_CONFLICT"
    if fact["state"] == "UNKNOWN" or fact["provenance"] == "UNKNOWN":
        return "UNKNOWN", unknown_reason
    if fact["provenance"] not in set(trusted_provenance):
        return "UNKNOWN", "PROVENANCE_NOT_SUFFICIENT_FOR_PASS"
    return "PASS", success_reason


def _combined_gate(
    gates: Sequence[Tuple[str, str]], *, unknown_reason: str, success_reason: str
) -> Tuple[str, str]:
    blocked = next((reason for status, reason in gates if status == "BLOCKED"), None)
    if blocked is not None:
        return "BLOCKED", blocked
    if all(status == "PASS" for status, _ in gates):
        return "PASS", success_reason
    uncertain = next((reason for status, reason in gates if status == "UNKNOWN"), None)
    return "UNKNOWN", uncertain or unknown_reason


def _scenario_result(scenario: Mapping[str, Any], label_power: Decimal) -> Dict[str, Any]:
    capacity = scenario["capacity"]
    if capacity["state"] == "UNKNOWN":
        return {
            **copy.deepcopy(dict(scenario)),
            "status": "UNKNOWN",
            "reason_code": "CAPACITY_UNKNOWN",
            "gross_margin": {"state": "UNKNOWN", "value": None, "unit": "W"},
            "hardware_pass": False,
        }
    capacity_value = Decimal(str(capacity["value"]))
    margin = capacity_value - label_power
    if margin < 0:
        status = "FAIL"
        reason_code = "CAPACITY_BELOW_LABEL_LOAD"
    elif margin == 0:
        status = "UNKNOWN"
        reason_code = "ZERO_GROSS_MARGIN_UNVERIFIED"
    else:
        status = "UNKNOWN"
        reason_code = "ASSUMPTION_NOT_HARDWARE_PASS"
    return {
        **copy.deepcopy(dict(scenario)),
        "status": status,
        "reason_code": reason_code,
        "gross_margin": {"state": "KNOWN", "value": _json_number(margin), "unit": "W"},
        "hardware_pass": False,
    }


def _check(check_id: str, status: str, reason_code: str, scope: str) -> Dict[str, Any]:
    return {
        "check_id": check_id,
        "mandatory": True,
        "status": status,
        "reason_code": reason_code,
        "scope": scope,
    }


def _verification_vector(
    facts: Mapping[str, Mapping[str, Any]], source_results: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    sources_ok = all(item["verification_status"] == "PASS" for item in source_results)
    source_explicit = facts["label_total_power"]["provenance"] == "SOURCE_EXPLICIT"
    if not sources_ok:
        source_status = "BLOCKED"
        source_reason = "SOURCE_VERIFICATION_BLOCKED"
    elif not source_explicit:
        source_status = "BLOCKED"
        source_reason = "PRIMARY_LABEL_ARTIFACT_MISSING"
    else:
        # The evaluator verifies bytes, declared classification and claim metadata.
        # It deliberately does not parse source contents, so it cannot claim that
        # the primary artifact itself substantiates the declared fact.
        source_status = "UNKNOWN"
        source_reason = "PRIMARY_SOURCE_BYTES_BOUND_CONTENT_NOT_PARSED"
    load_status, load_reason = _fact_gate(
        facts["measured_continuous_power"],
        trusted_provenance={"SOURCE_EXPLICIT"},
        unknown_reason="MEASUREMENT_NOT_RUN",
        success_reason="MEASURED_LOAD_BOUND",
    )
    count_gate = _fact_gate(
        facts["pd_port_count"],
        trusted_provenance={"SOURCE_EXPLICIT", "ENGINEERING_RULE"},
        unknown_reason="PD_PORT_COUNT_UNKNOWN",
        success_reason="PD_PORT_COUNT_BOUND",
    )
    profile_gate = _fact_gate(
        facts["pd_profiles"],
        trusted_provenance={"SOURCE_EXPLICIT", "ENGINEERING_RULE"},
        unknown_reason="PD_PROFILE_UNKNOWN",
        success_reason="PD_PROFILE_BOUND",
    )
    if profile_gate[0] == "PASS":
        profile_gate = "UNKNOWN", "PD_PROFILE_REFERENCES_BOUND_VALUES_NOT_MODELED"
    port_status, port_reason = _combined_gate(
        [count_gate, profile_gate],
        unknown_reason="PD_PORT_COUNT_OR_PROFILE_UNKNOWN",
        success_reason="PD_PORT_COUNT_AND_PROFILE_BOUND",
    )
    policy_status, policy_reason = _fact_gate(
        facts["simultaneous_power_policy"],
        trusted_provenance={"SOURCE_EXPLICIT", "ENGINEERING_RULE"},
        unknown_reason="SIMULTANEOUS_POLICY_UNKNOWN",
        success_reason="SIMULTANEOUS_POLICY_BOUND",
    )
    if policy_status == "PASS":
        policy_status = "UNKNOWN"
        policy_reason = "SIMULTANEOUS_POLICY_REFERENCE_BOUND_VALUES_NOT_MODELED"
    return [
        _check("SOURCE_BINDING", source_status, source_reason, "PRODUCT_EVIDENCE"),
        _check("LOAD_DEFINITION", load_status, load_reason, "PRODUCT_EVIDENCE"),
        _check("PORT_PROFILE", port_status, port_reason, "PRODUCT_EVIDENCE"),
        _check("SIMULTANEOUS_POWER_POLICY", policy_status, policy_reason, "PRODUCT_EVIDENCE"),
        _check("STATIC_CAPACITY", "UNKNOWN", "LOAD_SIDE_CAPACITY_UNKNOWN", "PRODUCT_EVIDENCE"),
        _check("DERATING", "UNKNOWN", "DERATING_RULE_UNKNOWN", "PRODUCT_EVIDENCE"),
        _check("EFFICIENCY_LOSS", "UNKNOWN", "EFFICIENCY_PATH_UNKNOWN", "PRODUCT_EVIDENCE"),
        _check("STARTUP_SHUTDOWN", "NOT_RUN", "BENCH_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("BACKFEED_POWER_OFF", "NOT_RUN", "BENCH_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("NEGOTIATION", "NOT_RUN", "BENCH_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("FAULT_RECOVERY", "NOT_RUN", "BENCH_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("THERMAL", "NOT_RUN", "BENCH_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("COMPLIANCE", "UNKNOWN", "COMPLIANCE_SCOPE_UNKNOWN", "PRODUCT_EVIDENCE"),
        _check("MEASUREMENT_INTEGRITY", "BLOCKED", "INSTRUMENT_AND_CALIBRATION_NOT_BOUND", "TEST_EVIDENCE"),
        _check("MODEL_REPRODUCIBILITY", "NOT_RUN", "RUN_EVIDENCE_NOT_BOUND", "SOFTWARE_MODEL_ONLY"),
        _check("INTERFACE_MAP", "UNKNOWN", "POWER_CONTROL_FAULT_MAP_UNKNOWN", "PRODUCT_EVIDENCE"),
        _check("MODULE_SWAP_REPEATABILITY", "NOT_RUN", "MODULE_SWAP_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("HYBRID_BOUNDARY_CONTRACT", "BLOCKED", "UPSTREAM_MODULE_NOT_SELECTED", "PRODUCT_EVIDENCE"),
        _check("CONTROL_TELEMETRY_FAIL_SAFE", "NOT_RUN", "FAIL_SAFE_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("INTERCONNECT_DROP_THERMAL", "NOT_RUN", "INTERCONNECT_TEST_NOT_RUN", "HARDWARE_TEST"),
        _check("EXACT_PART_PROVENANCE", "BLOCKED", "EXACT_PART_SOURCES_MISSING", "PRODUCT_EVIDENCE"),
        _check("SCHEMATIC_LAYOUT_REVIEW", "NOT_RUN", "DESIGN_NOT_AVAILABLE", "DESIGN_REVIEW"),
        _check("LOOP_STABILITY", "NOT_RUN", "EXACT_POWER_STAGE_NOT_AVAILABLE", "HARDWARE_TEST"),
        _check("BRING_UP", "NOT_RUN", "CUSTOM_PCB_NOT_AVAILABLE", "HARDWARE_TEST"),
    ]


def _architecture_results(
    architectures: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    catalog = {item["check_id"]: item for item in checks}
    results: List[Dict[str, Any]] = []
    for architecture in architectures:
        selected = [copy.deepcopy(catalog[check_id]) for check_id in architecture["required_evidence"]]
        eligible = bool(selected) and all(item["status"] == "PASS" for item in selected)
        verification_status = "PASS" if eligible else max(
            (item["status"] for item in selected), key=lambda status: _STATUS_RANK[status]
        )
        results.append(
            {
                **copy.deepcopy(dict(architecture)),
                "eligible": eligible,
                "verification_status": verification_status,
                "test_status": "NOT_RUN",
                "checks": selected,
            }
        )
    return results


def _issue(
    status: str,
    reason_code: str,
    path: str,
    message: str,
    source_refs: Sequence[str],
) -> Dict[str, Any]:
    return {
        "status": status,
        "reason_code": reason_code,
        "path": path,
        "message": message,
        "rule_id": RULE_ID,
        "source_refs": list(source_refs),
    }


def evaluate_ncs01(document: Any, source_root: Path) -> Dict[str, Any]:
    """Evaluate one NCS-01 case without mutating the caller's document."""

    normalized = _validate_case(copy.deepcopy(document))
    try:
        root = Path(source_root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise Ncs01InputError("source root does not exist or cannot be resolved") from None
    if not root.is_dir():
        raise Ncs01InputError("source root must be a directory")

    source_results = [_source_result(source, root) for source in normalized["sources"]]
    source_refs = [source["source_id"] for source in normalized["sources"]]
    label_power = Decimal(str(normalized["facts"]["label_total_power"]["value"]))
    scenario_results = [
        _scenario_result(scenario, label_power) for scenario in normalized["scenarios"]
    ]
    checks = _verification_vector(normalized["facts"], source_results)
    architecture_results = _architecture_results(normalized["architectures"], checks)
    preferred = next(
        item for item in architecture_results if item["recommendation"] == "PREFERRED_PROVISIONAL"
    )
    fallback = next(item for item in architecture_results if item["fallback"])
    future = next(item for item in architecture_results if item["future_option"])
    preferred_check_ids = set(preferred["required_evidence"])

    issues: List[Dict[str, Any]] = []
    for index, source in enumerate(source_results):
        if source["verification_status"] != "PASS":
            issues.append(
                _issue(
                    "BLOCKED",
                    source["reason_code"],
                    f"$.sources[{index}]",
                    "The declared local source could not be bound to the expected SHA-256.",
                    [source["source_id"]],
                )
            )
    if normalized["facts"]["label_total_power"]["provenance"] != "SOURCE_EXPLICIT":
        issues.append(
            _issue(
                "BLOCKED",
                "PRIMARY_LABEL_ARTIFACT_MISSING",
                "$.facts.label_total_power",
                "The 45 W label statement is handoff-only evidence; the primary label artifact is absent.",
                normalized["facts"]["label_total_power"]["source_refs"],
            )
        )
    unknown_messages = {
        "measured_continuous_power": ("MEASUREMENT_NOT_RUN", "Measured continuous load is not available."),
        "pd_port_count": ("PD_PORT_COUNT_UNKNOWN", "The product PD port count is not bound."),
        "pd_profiles": ("PD_PROFILE_UNKNOWN", "Per-port PD voltage/current profiles are not bound."),
        "simultaneous_power_policy": (
            "SIMULTANEOUS_POLICY_UNKNOWN",
            "The simultaneous port power policy is not bound.",
        ),
    }
    for fact_name, (reason_code, message) in unknown_messages.items():
        if _fact_is_unknown(normalized["facts"][fact_name]):
            issues.append(
                _issue(
                    "UNKNOWN",
                    reason_code,
                    f"$.facts.{fact_name}",
                    message,
                    normalized["facts"][fact_name]["source_refs"],
                )
            )
    for index, check in enumerate(checks):
        if check["check_id"] in preferred_check_ids and check["status"] != "PASS":
            issues.append(
                _issue(
                    check["status"],
                    check["check_id"] + "_" + check["reason_code"],
                    f"$.verification_status_vector[{index}]",
                    f"{check['check_id']} is {check['status']}: {check['reason_code']}.",
                    source_refs,
                )
            )

    status = preferred["verification_status"]
    reason_code = (
        "PREFERRED_ARCHITECTURE_ALL_MANDATORY_CHECKS_PASS"
        if status == "PASS"
        else next(item["reason_code"] for item in issues if item["status"] == status)
    )
    input_hash = sha256_hexdigest(normalized)
    label_is_source_explicit = (
        normalized["facts"]["label_total_power"]["provenance"] == "SOURCE_EXPLICIT"
    )
    label_limitations = (
        ["PRIMARY_LABEL_SOURCE_BYTES_BOUND_CONTENT_NOT_EXTRACTED"]
        if label_is_source_explicit
        else [
            "PRIMARY_LABEL_ARTIFACT_NOT_LOCAL",
            "45_W_IS_HANDOFF_ONLY_LABEL_SUM_NOT_A_MEASUREMENT",
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "case_id": normalized["case_id"],
        "component_id": f"{RULE_ID}:{input_hash[:16]}",
        "rule_id": RULE_ID,
        "status": status,
        "reason_code": reason_code,
        "input_hash": input_hash,
        "tool_name": TOOL_NAME,
        "tool_version": MODEL_VERSION,
        "package_version": PACKAGE_VERSION,
        "source_refs": source_refs,
        "sources": source_results,
        "claim_limits": normalized["claim_limits"],
        "facts": normalized["facts"],
        "calculation_policy": {
            "domain": "AGGREGATE_LOAD_SIDE_POWER_ONLY",
            "label_power_conversion": "FORBIDDEN_WITHOUT_SOURCE_BOUND_VOLTAGE_AND_CURRENT_PROFILE",
            "assumption_can_produce_hardware_pass": False,
            "overall_status_scope": "PREFERRED_PROVISIONAL_MANDATORY_CHECKS",
            "portfolio_scenario_status_affects_overall": False,
            "status_precedence": list(_STATUSES),
        },
        "scenario_results": scenario_results,
        "verification_status_vector": checks,
        "architecture_assessment": architecture_results,
        "recommendation": {
            "preferred_provisional": preferred["architecture_id"],
            "basis": preferred["recommendation_basis"],
            "fallback": fallback["architecture_id"],
            "future_option": future["architecture_id"],
            "verification_status": preferred["verification_status"],
            "test_status": preferred["test_status"],
        },
        "issues": issues,
        "limitations": label_limitations + [
            "SOURCE_SUPPORTS_METADATA_NOT_CONTENT_EXTRACTION",
            "PD_VOLTAGE_CURRENT_AND_PORT_PROFILES_UNKNOWN",
            "NO_WATT_TO_AMPERE_CONVERSION_PERFORMED",
            "PORTFOLIO_ASSUMPTIONS_CANNOT_PRODUCE_HARDWARE_PASS",
            "NO_EXACT_PART_OR_PCB_RELEASE_CLAIM",
        ],
        "g1_impact": {
            "status": "BLOCKED",
            "change": "NONE",
            "reason_code": "NCS01_DOES_NOT_CLOSE_G1_EVIDENCE_GAPS",
        },
        "next_action": {
            "action_id": "NCS01-BENCH-01",
            "description": (
                "Validate the bound primary label content and characterize one replaceable "
                "upstream module at the load-side output."
                if label_is_source_explicit
                else "Bind the primary label artifact and characterize one replaceable "
                "upstream module at the load-side output."
            ),
            "required_observations": [
                "PD source and sink profiles",
                "single-port and simultaneous delivered power",
                "startup and shutdown behavior",
                "power-off backfeed",
                "fault recovery",
                "steady-state thermal behavior",
            ],
        },
    }


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Ncs01InputError(message)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the NCS-01 evaluator and emit deterministic JSON to stdout."""

    parser = _ArgumentParser(prog="python -m power_rail_validation.ncs01")
    parser.add_argument("input", type=Path, help="NCS-01 case JSON")
    parser.add_argument(
        "--source-root",
        required=True,
        type=Path,
        help="explicit root used to resolve source locators",
    )
    try:
        args = parser.parse_args(argv)
        document = load_json(args.input)
        result = evaluate_ncs01(document, args.source_root)
        _write_utf8(
            sys.stdout,
            json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            + "\n",
        )
        return EXIT_CODES[result["status"]]
    except (Ncs01InputError, json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
        _write_utf8(sys.stderr, f"input error: {exc}\n")
        return EXIT_USAGE_OR_INPUT
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        _write_utf8(sys.stderr, f"internal error: {type(exc).__name__}: {exc}\n")
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover - exercised through the module entry point
    raise SystemExit(main())


__all__ = [
    "MODEL_VERSION",
    "Ncs01InputError",
    "RULE_ID",
    "SCHEMA_VERSION",
    "TOOL_NAME",
    "evaluate_ncs01",
    "main",
]
