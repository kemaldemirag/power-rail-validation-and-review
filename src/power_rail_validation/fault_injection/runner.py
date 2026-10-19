"""Deterministic, evidence-bound fault-injection runner for WF-02-04.

The runner is an orchestration and rule-evaluation layer.  It deliberately
does not model analogue transients.  Static rail-budget and sequencing inputs
are evaluated by their public engines, while fault context remains in this
module-local contract and is never inserted into the sequencing event stream.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest
from power_rail_validation.rail_budget import (
    FORMULA_VERSION as RAIL_BUDGET_FORMULA_VERSION,
    calculate_rail_budget,
)
from power_rail_validation.sequencing import (
    FSM_VERSION as SEQUENCING_FSM_VERSION,
    RULE_SET_VERSION as SEQUENCING_RULE_SET_VERSION,
    evaluate_sequencing,
)


ENGINE_VERSION = "0.1.0"
RULE_SET_VERSION = "REP02-FI-RULESET-1.0.0"
TOOL_NAME = "power-rail-validation"

_FAULT_TYPES = frozenset({"BROWNOUT", "STUCK_LOW", "DELAY", "RAIL_LOSS", "RESTART"})
_STATUS = frozenset({"PASS", "FAIL", "UNKNOWN", "BLOCKED"})
_PROVENANCE = frozenset(
    {"SOURCE_EXPLICIT", "ENGINEERING_RULE", "ASSUMPTION", "UNKNOWN", "DERIVED", "CONFLICT"}
)
_TRUSTED_PROVENANCE = frozenset({"SOURCE_EXPLICIT", "ENGINEERING_RULE"})
_UNCERTAIN_PROVENANCE = frozenset({"ASSUMPTION", "UNKNOWN", "DERIVED"})
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DECIMAL_PRECISION = 10000
_FLOATING_VERSIONS = frozenset(
    {"current", "develop", "development", "head", "latest", "main", "master", "nightly", "stable", "tip", "trunk", "unknown", "unspecified", "tbd", "n/a", "na"}
)
_MISSING = object()

_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "scenario_id",
        "generation",
        "rail_budget_input",
        "sequencing_input",
        "targets",
        "faults",
        "sources",
        "observation",
        "overlap_policy",
    }
)
_GENERATION_FIELDS = frozenset(
    {"mode", "fixture_id", "seed", "runner_version", "provenance", "source_refs"}
)
_TARGET_FIELDS = frozenset(
    {"target_id", "target_type", "baseline_binding", "baseline_ref", "polarity", "provenance", "source_refs"}
)
_FAULT_FIELDS = frozenset(
    {
        "fault_id",
        "fault_type",
        "target",
        "start_time",
        "duration",
        "severity",
        "effect_rule",
        "recovery",
        "expected_status",
        "provenance",
        "source_refs",
    }
)
_FAULT_TARGET_FIELDS = frozenset({"target_id", "target_type"})
_MEASURE_FIELDS = frozenset({"kind", "state", "value", "unit"})
_EFFECT_FIELDS = frozenset({"rule_id", "operator", "threshold", "provenance", "source_refs"})
_RECOVERY_FIELDS = frozenset(
    {"policy_id", "mode", "required", "completion", "completion_time", "maximum_delay", "provenance", "source_refs"}
)
_BOOLEAN_STATE_FIELDS = frozenset({"state", "value"})
_QUANTITY_FIELDS = frozenset({"state", "value", "unit"})
_OBSERVATION_FIELDS = frozenset({"end_time", "provenance", "source_refs"})
_SOURCE_FIELDS = frozenset(
    {"schema_version", "entity_type", "source_id", "classification", "title", "version", "locator", "access_status", "content_hash"}
)
_HASH_FIELDS = frozenset({"state", "algorithm", "value"})

_EFFECT_PROFILE: Mapping[str, Tuple[str, str, str, str]] = {
    "BROWNOUT": ("VOLTAGE_RATIO", "1", "LESS_THAN", "FI.BROWNOUT.THRESHOLD"),
    "STUCK_LOW": ("LOGIC_LEVEL", "logic", "EQUALS", "FI.STUCK_LOW.LEVEL"),
    "DELAY": ("TIME_EXTENSION", "s", "GREATER_THAN", "FI.DELAY.THRESHOLD"),
    "RAIL_LOSS": ("AVAILABILITY", "state", "EQUALS", "FI.RAIL_LOSS.STATE"),
    "RESTART": ("RESTART_REQUEST", "state", "EQUALS", "FI.RESTART.REQUEST"),
}
_TARGET_TYPES: Mapping[str, frozenset[str]] = {
    "BROWNOUT": frozenset({"RAIL"}),
    "STUCK_LOW": frozenset({"SIGNAL"}),
    "DELAY": frozenset({"SIGNAL", "RAIL", "SYSTEM"}),
    "RAIL_LOSS": frozenset({"RAIL"}),
    "RESTART": frozenset({"SYSTEM"}),
}


@dataclass(frozen=True)
class _Finding:
    status: str
    code: str
    path: str
    message: str
    rule_id: str = "REP02.FI.CONTRACT"
    fault_id: Optional[str] = None
    source_refs: Tuple[str, ...] = ()

    def as_dict(self, index: int) -> Dict[str, Any]:
        return {
            "finding_id": f"FI-FINDING-{index:04d}",
            "rule_id": self.rule_id,
            "fault_id": self.fault_id,
            "status": self.status,
            "reason_code": self.code,
            "path": self.path,
            "message": self.message,
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True)
class _Target:
    target_id: str
    target_type: str
    baseline_binding: str
    baseline_ref: str
    polarity: str
    provenance: str
    source_refs: Tuple[str, ...]


@dataclass(frozen=True)
class _Measure:
    kind: str
    state: str
    value: Any
    unit: str


@dataclass(frozen=True)
class _Recovery:
    policy_id: str
    mode: str
    required: bool
    completion_state: str
    completion_value: Optional[bool]
    completion_time: Optional[Decimal]
    maximum_delay: Optional[Decimal]
    provenance: str
    source_refs: Tuple[str, ...]


@dataclass(frozen=True)
class _Fault:
    input_index: int
    fault_id: str
    fault_type: str
    target: _Target
    start: Optional[Decimal]
    duration: Optional[Decimal]
    severity: _Measure
    effect_rule_id: str
    operator: str
    threshold: _Measure
    effect_provenance: str
    effect_source_refs: Tuple[str, ...]
    recovery: _Recovery
    expected_status: str
    provenance: str
    source_refs: Tuple[str, ...]


def _add(
    findings: MutableSequence[_Finding],
    status: str,
    code: str,
    path: str,
    message: str,
    *,
    rule_id: str = "REP02.FI.CONTRACT",
    fault_id: Optional[str] = None,
    source_refs: Sequence[str] = (),
) -> None:
    candidate = _Finding(status, code, path, message, rule_id, fault_id, tuple(source_refs))
    if candidate not in findings:
        findings.append(candidate)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _decimal(value: Any) -> Optional[Decimal]:
    if not _is_number(value) or (isinstance(value, float) and not math.isfinite(value)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _json_number(value: Optional[Decimal]) -> Optional[Union[int, float]]:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    converted = float(value)
    if not math.isfinite(converted) or (converted == 0 and value != 0):
        raise OverflowError("Decimal is not representable as a finite nonzero JSON number")
    return converted


def _time_output(value: Optional[Decimal]) -> Dict[str, Any]:
    return {
        "state": "KNOWN" if value is not None else "UNKNOWN",
        "value": _json_number(value),
        "decimal_text": _decimal_text(value),
        "unit": "s",
    }


def _exact_precision(*values: Decimal) -> int:
    nonzero = tuple(value for value in values if value != 0)
    if not nonzero:
        return 2
    highest = max(value.adjusted() for value in nonzero)
    lowest_exponent = min(value.as_tuple().exponent for value in nonzero)
    precision = max(
        max(len(value.as_tuple().digits) for value in nonzero),
        highest - lowest_exponent + 3,
        32,
    )
    if precision > _MAX_DECIMAL_PRECISION:
        raise ArithmeticError("exact Decimal operation exceeds the precision cap")
    return precision


def _exact_add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _exact_precision(left, right)
        result = left + right
    if not result.is_finite():
        raise ArithmeticError("exact Decimal addition produced a non-finite value")
    return result


def _exact_subtract(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _exact_precision(left, right)
        result = left - right
    if not result.is_finite():
        raise ArithmeticError("exact Decimal subtraction produced a non-finite value")
    return result


def _reject(value: Mapping[str, Any], allowed: Iterable[str], path: str, findings: MutableSequence[_Finding]) -> None:
    permitted = set(allowed)
    for key in sorted((key for key in value if not isinstance(key, str) or key not in permitted), key=repr):
        _add(findings, "BLOCKED", "UNEXPECTED_FIELD", f"{path}.{key!r}", "field is outside the fault-injection contract")


def _require(value: Mapping[str, Any], fields: Iterable[str], path: str, findings: MutableSequence[_Finding]) -> None:
    for field in sorted(fields):
        if field not in value:
            _add(findings, "BLOCKED", "REQUIRED_INPUT_MISSING", f"{path}.{field}", "required field is absent")


def _identifier(value: Any, path: str, findings: MutableSequence[_Finding], code: str = "IDENTIFIER_INVALID") -> Optional[str]:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _add(findings, "BLOCKED", code, path, "expected a stable identifier")
        return None
    return value


def _is_floating_version(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _FLOATING_VERSIONS or any(token in normalized for token in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    pieces = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    if pieces and (pieces[-1] in _FLOATING_VERSIONS or any(part in {"branch", "branches", "heads"} for part in pieces)):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _refs(value: Any, path: str, findings: MutableSequence[_Finding]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _add(findings, "BLOCKED", "SOURCE_REFS_REQUIRED", path, "at least one source reference is required")
        return ()
    result: List[str] = []
    for index, item in enumerate(value):
        reference = _identifier(item, f"{path}[{index}]", findings, "SOURCE_REF_INVALID")
        if reference is None:
            continue
        if reference in result:
            _add(findings, "BLOCKED", "SOURCE_REF_DUPLICATE", f"{path}[{index}]", "source references must be unique")
        else:
            result.append(reference)
    return tuple(result)


def _provenance(value: Any, path: str, findings: MutableSequence[_Finding]) -> str:
    if not isinstance(value, str) or value not in _PROVENANCE:
        _add(findings, "BLOCKED", "PROVENANCE_INVALID", path, "unrecognized provenance classification")
        return "UNKNOWN"
    if value == "CONFLICT":
        _add(findings, "BLOCKED", "PROVENANCE_CONFLICT", path, "conflicted evidence cannot support an assessment")
    elif value in _UNCERTAIN_PROVENANCE:
        _add(findings, "UNKNOWN", "PROVENANCE_UNCERTAIN", path, "unverified provenance cannot support PASS")
    return value


def _time_quantity(
    value: Any,
    path: str,
    findings: MutableSequence[_Finding],
    *,
    unknown_code: Optional[str],
) -> Optional[Decimal]:
    if not isinstance(value, dict):
        _add(findings, "BLOCKED", "QUANTITY_OBJECT_REQUIRED", path, "expected {state, value, unit}")
        return None
    _reject(value, _QUANTITY_FIELDS, path, findings)
    _require(value, _QUANTITY_FIELDS, path, findings)
    if value.get("unit") != "s":
        _add(findings, "BLOCKED", "TIME_UNIT_INVALID", f"{path}.unit", "time quantity unit must be 's'")
    state = value.get("state")
    if state == "UNKNOWN":
        if value.get("value") is not None:
            _add(findings, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{path}.value", "UNKNOWN quantity requires null")
        if unknown_code is not None:
            _add(findings, "UNKNOWN", unknown_code, path, "time quantity is explicitly unknown")
        return None
    if state != "KNOWN":
        _add(findings, "BLOCKED", "QUANTITY_STATE_INVALID", f"{path}.state", "state must be KNOWN or UNKNOWN")
        return None
    result = _decimal(value.get("value"))
    if result is None:
        _add(findings, "BLOCKED", "FINITE_NUMBER_REQUIRED", f"{path}.value", "KNOWN time requires a finite JSON number")
        return None
    if result < 0:
        _add(findings, "BLOCKED", "NEGATIVE_TIME", f"{path}.value", "time cannot be negative")
        return None
    return result


def _measure(value: Any, path: str, findings: MutableSequence[_Finding]) -> _Measure:
    if not isinstance(value, dict):
        _add(findings, "BLOCKED", "MEASURE_OBJECT_REQUIRED", path, "expected a type-labelled measure")
        return _Measure("UNKNOWN", "UNKNOWN", None, "unknown")
    _reject(value, _MEASURE_FIELDS, path, findings)
    _require(value, _MEASURE_FIELDS, path, findings)
    kind = value.get("kind") if isinstance(value.get("kind"), str) else "UNKNOWN"
    unit = value.get("unit") if isinstance(value.get("unit"), str) else "unknown"
    if kind == "UNKNOWN":
        _add(findings, "BLOCKED", "MEASURE_KIND_INVALID", f"{path}.kind", "measure kind must be a non-empty string")
    if unit == "unknown":
        _add(findings, "BLOCKED", "MEASURE_UNIT_INVALID", f"{path}.unit", "measure unit must be a non-empty string")
    state = value.get("state")
    raw = value.get("value")
    if state == "UNKNOWN":
        if raw is not None:
            _add(findings, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{path}.value", "UNKNOWN measure requires null")
        return _Measure(kind, "UNKNOWN", None, unit)
    if state != "KNOWN":
        _add(findings, "BLOCKED", "MEASURE_STATE_INVALID", f"{path}.state", "measure state must be KNOWN or UNKNOWN")
        return _Measure(kind, "UNKNOWN", None, unit)
    if _is_number(raw):
        converted = _decimal(raw)
        if converted is None:
            _add(findings, "BLOCKED", "FINITE_NUMBER_REQUIRED", f"{path}.value", "numeric measure must be finite")
            raw = None
        else:
            raw = converted
    elif not isinstance(raw, str) or not raw:
        _add(findings, "BLOCKED", "MEASURE_VALUE_INVALID", f"{path}.value", "KNOWN measure requires a finite number or non-empty token")
        raw = None
    return _Measure(kind, "KNOWN", raw, unit)


def _boolean_state(value: Any, path: str, findings: MutableSequence[_Finding]) -> Tuple[str, Optional[bool]]:
    if not isinstance(value, dict):
        _add(findings, "BLOCKED", "STATE_OBJECT_REQUIRED", path, "expected {state, value}")
        return "UNKNOWN", None
    _reject(value, _BOOLEAN_STATE_FIELDS, path, findings)
    _require(value, _BOOLEAN_STATE_FIELDS, path, findings)
    state, raw = value.get("state"), value.get("value")
    if state == "UNKNOWN":
        if raw is not None:
            _add(findings, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{path}.value", "UNKNOWN completion requires null")
        return "UNKNOWN", None
    if state != "KNOWN" or not isinstance(raw, bool):
        _add(findings, "BLOCKED", "COMPLETION_STATE_INVALID", path, "completion must be KNOWN/boolean or UNKNOWN/null")
        return "UNKNOWN", None
    return "KNOWN", raw


def _sources(value: Any, findings: MutableSequence[_Finding]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        _add(findings, "BLOCKED", "TYPE_ARRAY", "$.sources", "sources must be an array")
        return {}
    result: Dict[str, Mapping[str, Any]] = {}
    for index, source in enumerate(value):
        path = f"$.sources[{index}]"
        if not isinstance(source, dict):
            _add(findings, "BLOCKED", "TYPE_OBJECT", path, "source must be an object")
            continue
        _reject(source, _SOURCE_FIELDS, path, findings)
        _require(source, _SOURCE_FIELDS, path, findings)
        if source.get("schema_version") != "1.0.0":
            _add(findings, "BLOCKED", "SOURCE_SCHEMA_VERSION_INVALID", f"{path}.schema_version", "source schema_version must be '1.0.0'")
        if source.get("entity_type") != "source":
            _add(findings, "BLOCKED", "SOURCE_ENTITY_TYPE_INVALID", f"{path}.entity_type", "source entity_type must be 'source'")
        source_id = _identifier(source.get("source_id"), f"{path}.source_id", findings, "SOURCE_ID_INVALID")
        for field in ("title", "locator"):
            if not isinstance(source.get(field), str) or not source.get(field).strip():
                _add(findings, "BLOCKED", "SOURCE_METADATA_INVALID", f"{path}.{field}", f"source {field} must be non-empty")
        classification = source.get("classification")
        if classification not in _PROVENANCE:
            _add(findings, "BLOCKED", "SOURCE_CLASSIFICATION_INVALID", f"{path}.classification", "unrecognized source classification")
        elif classification == "CONFLICT":
            _add(findings, "BLOCKED", "SOURCE_CONFLICT", f"{path}.classification", "conflicted source cannot support assessment")
        version = source.get("version")
        if not isinstance(version, str) or not version.strip():
            _add(findings, "BLOCKED", "SOURCE_VERSION_MISSING", f"{path}.version", "source version is required")
        elif _is_floating_version(version):
            _add(findings, "BLOCKED", "SOURCE_VERSION_FLOATING", f"{path}.version", "source version must be immutable")
        access = source.get("access_status")
        if access not in {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"}:
            _add(findings, "BLOCKED", "SOURCE_ACCESS_STATUS_INVALID", f"{path}.access_status", "unrecognized source access status")
        elif access != "AVAILABLE":
            _add(findings, "BLOCKED", "SOURCE_UNAVAILABLE", f"{path}.access_status", "referenced source is not available")
        digest = source.get("content_hash")
        if not isinstance(digest, dict):
            _add(findings, "BLOCKED", "SOURCE_HASH_INVALID", f"{path}.content_hash", "content_hash must be an object")
        else:
            _reject(digest, _HASH_FIELDS, f"{path}.content_hash", findings)
            _require(digest, _HASH_FIELDS, f"{path}.content_hash", findings)
            if digest.get("state") != "KNOWN":
                _add(findings, "BLOCKED", "SOURCE_HASH_REQUIRED", f"{path}.content_hash.state", "AVAILABLE source requires KNOWN hash")
            if digest.get("algorithm") != "SHA-256":
                _add(findings, "BLOCKED", "SOURCE_HASH_ALGORITHM_INVALID", f"{path}.content_hash.algorithm", "hash algorithm must be SHA-256")
            if not isinstance(digest.get("value"), str) or _SHA256_RE.fullmatch(digest.get("value", "")) is None:
                _add(findings, "BLOCKED", "SOURCE_HASH_VALUE_INVALID", f"{path}.content_hash.value", "hash must be 64 lowercase hexadecimal characters")
        if source_id is not None:
            if source_id in result:
                _add(findings, "BLOCKED", "SOURCE_ID_DUPLICATE", f"{path}.source_id", "source identifiers must be unique")
            else:
                result[source_id] = source
    return result


def _gate_refs(
    refs: Sequence[str],
    provenance: str,
    path: str,
    sources: Mapping[str, Mapping[str, Any]],
    findings: MutableSequence[_Finding],
) -> None:
    for index, source_id in enumerate(refs):
        source = sources.get(source_id)
        if source is None:
            _add(findings, "BLOCKED", "SOURCE_REF_UNRESOLVED", f"{path}[{index}]", "source reference does not resolve")
        elif provenance in _TRUSTED_PROVENANCE and source.get("classification") != provenance:
            _add(findings, "BLOCKED", "SOURCE_CLASSIFICATION_MISMATCH", f"{path}[{index}]", "trusted provenance must match source classification")


def _baseline_subjects(document: Any) -> Dict[str, frozenset[str]]:
    if not isinstance(document, dict):
        return {}
    subjects: Dict[str, set[str]] = {}

    def register(expected: Any) -> None:
        if not isinstance(expected, dict) or not isinstance(expected.get("subject_refs"), list):
            return
        event_kind = expected.get("event_kind")
        if event_kind == "POWER_OFF":
            semantic_type = "SYSTEM"
        elif isinstance(event_kind, str) and event_kind in {"VIN_VALID", "EN", "PGOOD", "RESET"}:
            semantic_type = "SIGNAL"
        else:
            semantic_type = "UNSUPPORTED"
        for reference in expected["subject_refs"]:
            if isinstance(reference, str):
                subjects.setdefault(reference, set()).add(semantic_type)

    for event in document.get("events", []) if isinstance(document.get("events"), list) else []:
        register(event)
    for rule in document.get("timing_rules", []) if isinstance(document.get("timing_rules"), list) else []:
        expected = rule.get("expected_event") if isinstance(rule, dict) else None
        register(expected)
    power_off = document.get("power_off_rule")
    expected = power_off.get("expected_event") if isinstance(power_off, dict) else None
    register(expected)
    return {reference: frozenset(types) for reference, types in subjects.items()}


def _validate_targets(
    value: Any,
    rail_input: Any,
    sequence_input: Any,
    sources: Mapping[str, Mapping[str, Any]],
    findings: MutableSequence[_Finding],
) -> Dict[Tuple[str, str], _Target]:
    if not isinstance(value, list) or not value:
        _add(findings, "BLOCKED", "TARGETS_REQUIRED", "$.targets", "at least one exact target binding is required")
        return {}
    rail_ids: set[Tuple[str, str]] = set()
    if isinstance(rail_input, dict):
        rail = rail_input.get("rail")
        if isinstance(rail, dict) and isinstance(rail.get("rail_id"), str):
            rail_ids.add(("RAIL_BUDGET_RAIL", rail["rail_id"]))
        for load in rail_input.get("loads", []) if isinstance(rail_input.get("loads"), list) else []:
            if isinstance(load, dict) and isinstance(load.get("load_id"), str):
                rail_ids.add(("RAIL_BUDGET_LOAD", load["load_id"]))
        capacity = rail_input.get("capacity")
        if isinstance(capacity, dict) and isinstance(capacity.get("regulator_id"), str):
            rail_ids.add(("RAIL_BUDGET_REGULATOR", capacity["regulator_id"]))
    subjects = _baseline_subjects(sequence_input)
    result: Dict[Tuple[str, str], _Target] = {}
    for index, item in enumerate(value):
        path = f"$.targets[{index}]"
        if not isinstance(item, dict):
            _add(findings, "BLOCKED", "TYPE_OBJECT", path, "target must be an object")
            continue
        _reject(item, _TARGET_FIELDS, path, findings)
        _require(item, _TARGET_FIELDS, path, findings)
        target_id = _identifier(item.get("target_id"), f"{path}.target_id", findings, "TARGET_ID_INVALID")
        target_type = item.get("target_type")
        if target_type not in {"RAIL", "SIGNAL", "LOAD", "REGULATOR", "SYSTEM"}:
            _add(findings, "BLOCKED", "TARGET_TYPE_INVALID", f"{path}.target_type", "unrecognized target type")
            target_type = "SYSTEM"
        binding = item.get("baseline_binding")
        if binding not in {"RAIL_BUDGET_RAIL", "RAIL_BUDGET_LOAD", "RAIL_BUDGET_REGULATOR", "SEQUENCING_SUBJECT"}:
            _add(findings, "BLOCKED", "TARGET_BINDING_INVALID", f"{path}.baseline_binding", "unrecognized baseline binding")
            binding = "SEQUENCING_SUBJECT"
        binding_types = {
            "RAIL_BUDGET_RAIL": frozenset({"RAIL"}),
            "RAIL_BUDGET_LOAD": frozenset({"LOAD"}),
            "RAIL_BUDGET_REGULATOR": frozenset({"REGULATOR"}),
            "SEQUENCING_SUBJECT": frozenset({"SIGNAL", "SYSTEM"}),
        }
        if target_type not in binding_types[binding]:
            _add(
                findings,
                "BLOCKED",
                "TARGET_TYPE_BINDING_MISMATCH",
                f"{path}.target_type",
                "target_type is incompatible with baseline_binding",
            )
        baseline_ref = _identifier(item.get("baseline_ref"), f"{path}.baseline_ref", findings, "TARGET_BASELINE_REF_INVALID") or "INVALID"
        if target_id is not None and target_id != baseline_ref:
            _add(findings, "BLOCKED", "TARGET_BINDING_NOT_EXACT", path, "target_id must exactly equal baseline_ref")
        resolved = (binding, baseline_ref) in rail_ids if binding.startswith("RAIL_BUDGET_") else baseline_ref in subjects
        if not resolved:
            _add(findings, "BLOCKED", "TARGET_UNRESOLVED", f"{path}.baseline_ref", "target does not resolve in the selected baseline input")
        elif binding == "SEQUENCING_SUBJECT":
            semantic_types = subjects[baseline_ref]
            if "UNSUPPORTED" in semantic_types:
                _add(
                    findings,
                    "BLOCKED",
                    "TARGET_SEQUENCE_SUBJECT_KIND_UNSUPPORTED",
                    f"{path}.baseline_ref",
                    "subject appears under an event_kind that cannot establish SIGNAL or SYSTEM identity",
                )
            supported_types = semantic_types & {"SIGNAL", "SYSTEM"}
            if len(supported_types) > 1:
                _add(
                    findings,
                    "BLOCKED",
                    "TARGET_SEQUENCE_SUBJECT_TYPE_CONFLICT",
                    f"{path}.baseline_ref",
                    "subject has conflicting SIGNAL and SYSTEM semantics in the sequencing baseline",
                )
            elif len(supported_types) == 1 and target_type not in supported_types:
                _add(
                    findings,
                    "BLOCKED",
                    "TARGET_SEQUENCE_SUBJECT_TYPE_MISMATCH",
                    f"{path}.target_type",
                    "target_type does not match the sequencing event_kind semantics",
                )
        polarity = item.get("polarity")
        if polarity not in {"ACTIVE_HIGH", "ACTIVE_LOW", "UNKNOWN", "NOT_APPLICABLE"}:
            _add(findings, "BLOCKED", "TARGET_POLARITY_INVALID", f"{path}.polarity", "unrecognized target polarity")
            polarity = "UNKNOWN"
        if target_type == "SIGNAL" and polarity == "NOT_APPLICABLE":
            _add(
                findings,
                "BLOCKED",
                "TARGET_SIGNAL_POLARITY_REQUIRED",
                f"{path}.polarity",
                "signal targets require ACTIVE_HIGH, ACTIVE_LOW, or UNKNOWN polarity",
            )
        elif target_type != "SIGNAL" and polarity != "NOT_APPLICABLE":
            _add(
                findings,
                "BLOCKED",
                "TARGET_NON_SIGNAL_POLARITY_INVALID",
                f"{path}.polarity",
                "RAIL, LOAD, REGULATOR, and SYSTEM targets require NOT_APPLICABLE polarity",
            )
        provenance = _provenance(item.get("provenance"), f"{path}.provenance", findings)
        refs = _refs(item.get("source_refs"), f"{path}.source_refs", findings)
        _gate_refs(refs, provenance, f"{path}.source_refs", sources, findings)
        if target_id is None:
            continue
        key = (target_type, target_id)
        target = _Target(target_id, target_type, binding, baseline_ref, polarity, provenance, refs)
        if key in result:
            _add(findings, "BLOCKED", "TARGET_DUPLICATE", path, "target bindings must be unique")
        else:
            result[key] = target
    return result


def _validate_generation(value: Any, sources: Mapping[str, Mapping[str, Any]], findings: MutableSequence[_Finding]) -> Dict[str, Any]:
    path = "$.generation"
    output = {"mode": None, "fixture_id": None, "seed": None, "runner_version": None, "provenance": None, "source_refs": []}
    if not isinstance(value, dict):
        _add(findings, "BLOCKED", "GENERATION_OBJECT_REQUIRED", path, "generation must be an object")
        return output
    _reject(value, _GENERATION_FIELDS, path, findings)
    _require(value, _GENERATION_FIELDS, path, findings)
    mode = value.get("mode")
    if mode not in {"FIXTURE", "SEEDED"}:
        _add(findings, "BLOCKED", "GENERATION_MODE_INVALID", f"{path}.mode", "generation mode must be FIXTURE or SEEDED")
    elif mode == "SEEDED":
        _add(findings, "BLOCKED", "SEEDED_GENERATION_UNSUPPORTED", f"{path}.mode", "this engine version supports deterministic FIXTURE mode only")
    fixture_id = _identifier(value.get("fixture_id"), f"{path}.fixture_id", findings, "FIXTURE_ID_INVALID")
    if mode == "FIXTURE" and value.get("seed") is not None:
        _add(findings, "BLOCKED", "FIXTURE_SEED_MUST_BE_NULL", f"{path}.seed", "FIXTURE mode does not use a seed")
    if value.get("runner_version") != ENGINE_VERSION:
        _add(findings, "BLOCKED", "RUNNER_VERSION_MISMATCH", f"{path}.runner_version", f"runner_version must be {ENGINE_VERSION}")
    provenance = _provenance(value.get("provenance"), f"{path}.provenance", findings)
    refs = _refs(value.get("source_refs"), f"{path}.source_refs", findings)
    _gate_refs(refs, provenance, f"{path}.source_refs", sources, findings)
    output.update({"mode": mode, "fixture_id": fixture_id, "seed": value.get("seed"), "runner_version": value.get("runner_version"), "provenance": provenance, "source_refs": list(refs)})
    return output


def _validate_recovery(
    value: Any,
    path: str,
    sources: Mapping[str, Mapping[str, Any]],
    findings: MutableSequence[_Finding],
    fault_id: Optional[str],
) -> _Recovery:
    if not isinstance(value, dict):
        _add(findings, "BLOCKED", "RECOVERY_OBJECT_REQUIRED", path, "recovery must be an object", fault_id=fault_id)
        return _Recovery("INVALID", "NONE", False, "UNKNOWN", None, None, None, "UNKNOWN", ())
    _reject(value, _RECOVERY_FIELDS, path, findings)
    _require(value, _RECOVERY_FIELDS, path, findings)
    policy_id = _identifier(value.get("policy_id"), f"{path}.policy_id", findings, "RECOVERY_POLICY_ID_INVALID") or "INVALID"
    mode = value.get("mode")
    if mode not in {"AUTO", "MANUAL", "NONE"}:
        _add(findings, "BLOCKED", "RECOVERY_MODE_INVALID", f"{path}.mode", "mode must be AUTO, MANUAL, or NONE", fault_id=fault_id)
        mode = "NONE"
    required = value.get("required")
    if not isinstance(required, bool):
        _add(findings, "BLOCKED", "RECOVERY_REQUIRED_INVALID", f"{path}.required", "required must be boolean", fault_id=fault_id)
        required = False
    if required and mode == "NONE":
        _add(findings, "BLOCKED", "RECOVERY_MODE_CONFLICT", path, "required recovery cannot use NONE mode", fault_id=fault_id)
    if not required and mode != "NONE":
        _add(findings, "BLOCKED", "RECOVERY_MODE_CONFLICT", path, "optional recovery must use NONE mode", fault_id=fault_id)
    completion_state, completion_value = _boolean_state(value.get("completion"), f"{path}.completion", findings)
    completion_time = _time_quantity(
        value.get("completion_time"),
        f"{path}.completion_time",
        findings,
        unknown_code="RECOVERY_TIME_UNKNOWN" if required else None,
    )
    maximum_delay = _time_quantity(
        value.get("maximum_delay"),
        f"{path}.maximum_delay",
        findings,
        unknown_code="RECOVERY_DEADLINE_UNKNOWN" if required else None,
    )
    if completion_value is not True and completion_time is not None:
        _add(
            findings,
            "BLOCKED",
            "RECOVERY_COMPLETION_TIME_CONFLICT",
            f"{path}.completion_time",
            "a known completion_time requires completion KNOWN/true",
            fault_id=fault_id,
        )
    provenance = _provenance(value.get("provenance"), f"{path}.provenance", findings)
    refs = _refs(value.get("source_refs"), f"{path}.source_refs", findings)
    _gate_refs(refs, provenance, f"{path}.source_refs", sources, findings)
    if not required and (completion_state != "UNKNOWN" or completion_time is not None or maximum_delay is not None):
        _add(findings, "BLOCKED", "RECOVERY_NOT_REQUIRED_FIELDS_CONFLICT", path, "NONE recovery requires UNKNOWN completion, time, and deadline")
    return _Recovery(policy_id, mode, required, completion_state, completion_value, completion_time, maximum_delay, provenance, refs)


def _validate_faults(
    value: Any,
    targets: Mapping[Tuple[str, str], _Target],
    sources: Mapping[str, Mapping[str, Any]],
    findings: MutableSequence[_Finding],
) -> List[_Fault]:
    if not isinstance(value, list) or not value:
        _add(findings, "BLOCKED", "FAULTS_REQUIRED", "$.faults", "at least one fault is required")
        return []
    result: List[_Fault] = []
    fault_ids: set[str] = set()
    semantic_keys: set[Tuple[Any, ...]] = set()
    for index, item in enumerate(value):
        path = f"$.faults[{index}]"
        if not isinstance(item, dict):
            _add(findings, "BLOCKED", "TYPE_OBJECT", path, "fault must be an object")
            continue
        _reject(item, _FAULT_FIELDS, path, findings)
        _require(item, _FAULT_FIELDS, path, findings)
        fault_id = _identifier(item.get("fault_id"), f"{path}.fault_id", findings, "FAULT_ID_INVALID") or f"INVALID-{index}"
        if fault_id in fault_ids:
            _add(findings, "BLOCKED", "FAULT_ID_DUPLICATE", f"{path}.fault_id", "fault identifiers must be unique", fault_id=fault_id)
        fault_ids.add(fault_id)
        fault_type = item.get("fault_type")
        if fault_type not in _FAULT_TYPES:
            _add(findings, "BLOCKED", "FAULT_TYPE_INVALID", f"{path}.fault_type", "unsupported fault type", fault_id=fault_id)
            fault_type = "RESTART"
        target_value = item.get("target")
        if not isinstance(target_value, dict):
            _add(findings, "BLOCKED", "FAULT_TARGET_OBJECT_REQUIRED", f"{path}.target", "fault target must be an object", fault_id=fault_id)
            target_type, target_id = "SYSTEM", "INVALID"
        else:
            _reject(target_value, _FAULT_TARGET_FIELDS, f"{path}.target", findings)
            _require(target_value, _FAULT_TARGET_FIELDS, f"{path}.target", findings)
            target_type = target_value.get("target_type")
            target_id = _identifier(target_value.get("target_id"), f"{path}.target.target_id", findings, "FAULT_TARGET_ID_INVALID") or "INVALID"
        target = targets.get((target_type, target_id))
        if target is None:
            _add(findings, "BLOCKED", "FAULT_TARGET_UNRESOLVED", f"{path}.target", "fault target does not exactly resolve", fault_id=fault_id)
            target = _Target(target_id, target_type if isinstance(target_type, str) else "SYSTEM", "SEQUENCING_SUBJECT", target_id, "UNKNOWN", "UNKNOWN", ())
        if target.target_type not in _TARGET_TYPES[fault_type]:
            _add(findings, "BLOCKED", "FAULT_TARGET_TYPE_MISMATCH", f"{path}.target.target_type", "target type is incompatible with fault type", fault_id=fault_id)
        start = _time_quantity(item.get("start_time"), f"{path}.start_time", findings, unknown_code="FAULT_START_TIME_UNKNOWN")
        duration = _time_quantity(item.get("duration"), f"{path}.duration", findings, unknown_code="FAULT_DURATION_UNKNOWN")
        if duration == 0 and fault_type != "RESTART":
            _add(
                findings,
                "BLOCKED",
                "ZERO_DURATION_NOT_ALLOWED",
                f"{path}.duration.value",
                "BROWNOUT, STUCK_LOW, DELAY, and RAIL_LOSS require positive duration; zero is reserved for instantaneous RESTART",
                fault_id=fault_id,
            )
        severity = _measure(item.get("severity"), f"{path}.severity", findings)
        effect_value = item.get("effect_rule")
        if not isinstance(effect_value, dict):
            _add(findings, "BLOCKED", "EFFECT_RULE_OBJECT_REQUIRED", f"{path}.effect_rule", "effect_rule must be an object", fault_id=fault_id)
            effect_value = {}
        _reject(effect_value, _EFFECT_FIELDS, f"{path}.effect_rule", findings)
        _require(effect_value, _EFFECT_FIELDS, f"{path}.effect_rule", findings)
        rule_id = _identifier(effect_value.get("rule_id"), f"{path}.effect_rule.rule_id", findings, "EFFECT_RULE_ID_INVALID") or "INVALID"
        operator = effect_value.get("operator")
        threshold = _measure(effect_value.get("threshold"), f"{path}.effect_rule.threshold", findings)
        effect_provenance = _provenance(effect_value.get("provenance"), f"{path}.effect_rule.provenance", findings)
        effect_refs = _refs(effect_value.get("source_refs"), f"{path}.effect_rule.source_refs", findings)
        _gate_refs(effect_refs, effect_provenance, f"{path}.effect_rule.source_refs", sources, findings)
        expected_kind, expected_unit, expected_operator, expected_rule = _EFFECT_PROFILE[fault_type]
        if severity.kind != expected_kind or threshold.kind != expected_kind:
            _add(findings, "BLOCKED", "SEVERITY_KIND_MISMATCH", f"{path}.severity.kind", f"{fault_type} requires {expected_kind}", rule_id=rule_id, fault_id=fault_id)
        if severity.unit != expected_unit or threshold.unit != expected_unit:
            _add(findings, "BLOCKED", "SEVERITY_UNIT_MISMATCH", f"{path}.severity.unit", f"{fault_type} requires unit {expected_unit}", rule_id=rule_id, fault_id=fault_id)
        if operator != expected_operator:
            _add(findings, "BLOCKED", "EFFECT_OPERATOR_MISMATCH", f"{path}.effect_rule.operator", f"{fault_type} requires {expected_operator}", rule_id=rule_id, fault_id=fault_id)
        if rule_id != expected_rule:
            _add(findings, "BLOCKED", "EFFECT_RULE_PROFILE_MISMATCH", f"{path}.effect_rule.rule_id", f"{fault_type} requires rule_id {expected_rule}", rule_id=rule_id, fault_id=fault_id)
        if expected_operator in {"LESS_THAN", "GREATER_THAN"}:
            if severity.state == "KNOWN" and not isinstance(severity.value, Decimal):
                _add(findings, "BLOCKED", "NUMERIC_SEVERITY_REQUIRED", f"{path}.severity.value", "numeric effect rule requires numeric severity", fault_id=fault_id)
            if threshold.state == "KNOWN" and not isinstance(threshold.value, Decimal):
                _add(findings, "BLOCKED", "NUMERIC_THRESHOLD_REQUIRED", f"{path}.effect_rule.threshold.value", "numeric effect rule requires numeric threshold", fault_id=fault_id)
        if fault_type == "BROWNOUT":
            if isinstance(severity.value, Decimal) and not (Decimal(0) <= severity.value <= Decimal(1)):
                _add(findings, "BLOCKED", "FAULT_SEVERITY_VALUE_INVALID", f"{path}.severity.value", "brownout voltage ratio must be within [0, 1]", fault_id=fault_id)
            if isinstance(threshold.value, Decimal) and not (Decimal(0) <= threshold.value <= Decimal(1)):
                _add(findings, "BLOCKED", "EFFECT_THRESHOLD_VALUE_INVALID", f"{path}.effect_rule.threshold.value", "brownout threshold must be within [0, 1]", fault_id=fault_id)
        elif fault_type == "DELAY":
            if isinstance(severity.value, Decimal) and severity.value < 0:
                _add(findings, "BLOCKED", "FAULT_SEVERITY_VALUE_INVALID", f"{path}.severity.value", "delay severity cannot be negative", fault_id=fault_id)
            if isinstance(threshold.value, Decimal) and threshold.value < 0:
                _add(findings, "BLOCKED", "EFFECT_THRESHOLD_VALUE_INVALID", f"{path}.effect_rule.threshold.value", "delay threshold cannot be negative", fault_id=fault_id)
        else:
            required_token = {
                "STUCK_LOW": "LOW",
                "RAIL_LOSS": "LOST",
                "RESTART": "REQUESTED",
            }.get(fault_type)
            if severity.state == "KNOWN" and severity.value != required_token:
                _add(findings, "BLOCKED", "FAULT_SEVERITY_VALUE_INVALID", f"{path}.severity.value", f"{fault_type} severity must be {required_token}", fault_id=fault_id)
            if threshold.state == "KNOWN" and threshold.value != required_token:
                _add(findings, "BLOCKED", "EFFECT_THRESHOLD_VALUE_INVALID", f"{path}.effect_rule.threshold.value", f"{fault_type} threshold must be {required_token}", fault_id=fault_id)
        recovery = _validate_recovery(item.get("recovery"), f"{path}.recovery", sources, findings, fault_id)
        expected_status = item.get("expected_status")
        if expected_status not in _STATUS:
            _add(findings, "BLOCKED", "EXPECTED_STATUS_INVALID", f"{path}.expected_status", "expected_status must be PASS, FAIL, UNKNOWN, or BLOCKED", fault_id=fault_id)
            expected_status = "BLOCKED"
        provenance = _provenance(item.get("provenance"), f"{path}.provenance", findings)
        refs = _refs(item.get("source_refs"), f"{path}.source_refs", findings)
        _gate_refs(refs, provenance, f"{path}.source_refs", sources, findings)
        semantic_key = (fault_type, target.target_type, target.target_id, _decimal_text(start), _decimal_text(duration))
        if start is not None and duration is not None and semantic_key in semantic_keys:
            _add(findings, "BLOCKED", "DUPLICATE_FAULT", path, "semantically duplicate fault is not allowed", fault_id=fault_id)
        semantic_keys.add(semantic_key)
        result.append(_Fault(index, fault_id, fault_type, target, start, duration, severity, rule_id, operator if isinstance(operator, str) else "INVALID", threshold, effect_provenance, effect_refs, recovery, expected_status, provenance, refs))
    return result


def _status(findings: Sequence[_Finding], fault_statuses: Sequence[str] = ()) -> str:
    statuses = {item.status for item in findings} | set(fault_statuses)
    for status in ("BLOCKED", "FAIL", "UNKNOWN"):
        if status in statuses:
            return status
    return "PASS"


def _compare(left: Any, operator: str, right: Any) -> Optional[bool]:
    if left is None or right is None or type(left) is not type(right):
        return None
    if operator == "EQUALS":
        return left == right
    if not isinstance(left, Decimal):
        return None
    if operator == "LESS_THAN":
        return left < right
    if operator == "GREATER_THAN":
        return left > right
    return None


def _measure_output(value: _Measure) -> Dict[str, Any]:
    raw = value.value
    return {
        "kind": value.kind,
        "state": value.state,
        "value": _json_number(raw) if isinstance(raw, Decimal) else raw,
        "decimal_text": _decimal_text(raw) if isinstance(raw, Decimal) else None,
        "unit": value.unit,
    }


def _target_output(value: _Target) -> Dict[str, Any]:
    return {
        "target_id": value.target_id,
        "target_type": value.target_type,
        "baseline_binding": value.baseline_binding,
        "baseline_ref": value.baseline_ref,
        "polarity": value.polarity,
    }


def _trace(
    fault: _Fault,
    phase: str,
    event: str,
    outcome: str,
    reason_code: str,
    *,
    rule_id: str,
    time: Optional[Decimal],
    expected: Any,
    observed: Any,
    violated_bound: Optional[str],
) -> Dict[str, Any]:
    return {
        "trace_index": None,
        "input_index": fault.input_index,
        "fault_id": fault.fault_id,
        "fault_type": fault.fault_type,
        "target": _target_output(fault.target),
        "phase": phase,
        "event": event,
        "rule_id": rule_id,
        "time": _time_output(time),
        "outcome": outcome,
        "reason_code": reason_code,
        "expected": expected,
        "observed": observed,
        "violated_bound": violated_bound,
    }


def _evaluate_recovery(
    fault: _Fault,
    observation_end: Optional[Decimal],
) -> Tuple[str, str, List[Dict[str, Any]], List[_Finding]]:
    recovery = fault.recovery
    trace: List[Dict[str, Any]] = []
    findings: List[_Finding] = []
    fault_end = _exact_add(fault.start, fault.duration) if fault.start is not None and fault.duration is not None else None
    if recovery.completion_value is True and recovery.completion_time is not None:
        if fault_end is not None and recovery.completion_time < fault_end:
            code = "RECOVERY_BEFORE_FAULT_END"
            findings.append(_Finding("BLOCKED", code, f"$.faults[{fault.input_index}].recovery.completion_time", "recovery completion precedes fault end", recovery.policy_id, fault.fault_id, recovery.source_refs))
            trace.append(_trace(fault, "RECOVERY", "RECOVERY_COMPLETED", "BLOCKED", code, rule_id=recovery.policy_id, time=recovery.completion_time, expected={"earliest": _time_output(fault_end)}, observed={"completion_time": _time_output(recovery.completion_time)}, violated_bound="CAUSAL_ORDER"))
            return "BLOCKED", code, trace, findings
        if observation_end is not None and recovery.completion_time > observation_end:
            code = "RECOVERY_AFTER_OBSERVATION_END"
            findings.append(_Finding("BLOCKED", code, f"$.faults[{fault.input_index}].recovery.completion_time", "recovery completion lies beyond the observation horizon", recovery.policy_id, fault.fault_id, recovery.source_refs))
            trace.append(_trace(fault, "RECOVERY", "RECOVERY_COMPLETED", "BLOCKED", code, rule_id=recovery.policy_id, time=recovery.completion_time, expected={"observation_end": _time_output(observation_end)}, observed={"completion_time": _time_output(recovery.completion_time)}, violated_bound="OBSERVATION_HORIZON"))
            return "BLOCKED", code, trace, findings
    if recovery.provenance not in _TRUSTED_PROVENANCE:
        code = "RECOVERY_PROVENANCE_UNCERTAIN"
        trace.append(_trace(fault, "RECOVERY", "RECOVERY_EVALUATED", "UNKNOWN", code, rule_id=recovery.policy_id, time=recovery.completion_time, expected={"maximum_delay": _time_output(recovery.maximum_delay)}, observed={"completion": recovery.completion_value}, violated_bound="PROVENANCE"))
        return "UNKNOWN", code, trace, findings
    if not recovery.required:
        trace.append(_trace(fault, "RECOVERY", "RECOVERY_NOT_REQUIRED", "PASS", "RECOVERY_NOT_REQUIRED", rule_id=recovery.policy_id, time=fault_end, expected={"required": False}, observed={"required": False}, violated_bound=None))
        return "PASS", "RECOVERY_NOT_REQUIRED", trace, findings
    if fault_end is None or recovery.maximum_delay is None or observation_end is None:
        code = "RECOVERY_TIMING_UNKNOWN"
        trace.append(_trace(fault, "RECOVERY", "RECOVERY_EVALUATED", "UNKNOWN", code, rule_id=recovery.policy_id, time=recovery.completion_time, expected={"maximum_delay": _time_output(recovery.maximum_delay)}, observed={"completion": recovery.completion_value}, violated_bound="UNKNOWN"))
        return "UNKNOWN", code, trace, findings
    deadline = _exact_add(fault_end, recovery.maximum_delay)
    if recovery.completion_state == "UNKNOWN":
        code = "RECOVERY_COMPLETION_UNKNOWN"
        trace.append(_trace(fault, "RECOVERY", "RECOVERY_EVALUATED", "UNKNOWN", code, rule_id=recovery.policy_id, time=None, expected={"deadline": _time_output(deadline)}, observed={"completion": None}, violated_bound="UNKNOWN"))
        return "UNKNOWN", code, trace, findings
    if recovery.completion_value is True:
        if recovery.completion_time is None:
            code = "RECOVERY_TIME_UNKNOWN"
            trace.append(_trace(fault, "RECOVERY", "RECOVERY_COMPLETED", "UNKNOWN", code, rule_id=recovery.policy_id, time=None, expected={"deadline": _time_output(deadline)}, observed={"completion": True}, violated_bound="UNKNOWN"))
            return "UNKNOWN", code, trace, findings
        elapsed = _exact_subtract(recovery.completion_time, fault_end)
        if recovery.completion_time <= deadline:
            code, status, bound = "RECOVERY_WITHIN_DEADLINE", "PASS", None
        else:
            code, status, bound = "RECOVERY_DEADLINE_EXCEEDED", "FAIL", "MAXIMUM"
        trace.append(_trace(fault, "RECOVERY", "RECOVERY_COMPLETED", status, code, rule_id=recovery.policy_id, time=recovery.completion_time, expected={"maximum_delay": _time_output(recovery.maximum_delay), "deadline": _time_output(deadline)}, observed={"completion": True, "measured_delay": _time_output(elapsed)}, violated_bound=bound))
        return status, code, trace, findings
    if observation_end > deadline:
        code, status, bound = "RECOVERY_MISSING_AFTER_DEADLINE", "FAIL", "MAXIMUM"
    else:
        code, status, bound = "RECOVERY_WINDOW_INCOMPLETE", "UNKNOWN", "NOT_YET_EXCEEDED"
    trace.append(_trace(fault, "RECOVERY", "RECOVERY_NOT_COMPLETED", status, code, rule_id=recovery.policy_id, time=observation_end, expected={"deadline": _time_output(deadline)}, observed={"completion": False}, violated_bound=bound))
    return status, code, trace, findings


def _evaluate_fault(
    fault: _Fault,
    observation_end: Optional[Decimal],
    causal_unknown_code: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[_Finding]]:
    traces = [
        _trace(
            fault,
            "INJECTION",
            "FAULT_INJECTED",
            "PASS" if fault.start is not None else "UNKNOWN",
            "FAULT_INJECTED" if fault.start is not None else "FAULT_START_TIME_UNKNOWN",
            rule_id=fault.effect_rule_id,
            time=fault.start,
            expected={"fault_type": fault.fault_type, "target": _target_output(fault.target)},
            observed={"severity": _measure_output(fault.severity), "duration": _time_output(fault.duration)},
            violated_bound=None,
        )
    ]
    findings: List[_Finding] = []
    if fault.start is not None and observation_end is not None and fault.start > observation_end:
        findings.append(_Finding("BLOCKED", "FAULT_AFTER_OBSERVATION_END", f"$.faults[{fault.input_index}].start_time", "fault starts after observation horizon", fault.effect_rule_id, fault.fault_id, fault.source_refs))
    fault_end = _exact_add(fault.start, fault.duration) if fault.start is not None and fault.duration is not None else None
    if fault_end is not None and observation_end is not None and fault_end > observation_end:
        findings.append(_Finding("UNKNOWN", "FAULT_EXTENDS_BEYOND_OBSERVATION", f"$.faults[{fault.input_index}].duration", "fault remains active beyond observation horizon", fault.effect_rule_id, fault.fault_id, fault.source_refs))

    if causal_unknown_code is not None:
        effect_reason = causal_unknown_code
        traces.append(_trace(fault, "EFFECT", "FAULT_EFFECT_EVALUATED", "UNKNOWN", effect_reason, rule_id=fault.effect_rule_id, time=fault.start, expected={"operator": fault.operator, "threshold": _measure_output(fault.threshold)}, observed={"severity": _measure_output(fault.severity), "target_polarity": fault.target.polarity}, violated_bound="BASELINE"))
        _add(findings, "UNKNOWN", effect_reason, f"$.faults[{fault.input_index}].effect_rule", "relevant baseline uncertainty prevents causal fault attribution", rule_id=fault.effect_rule_id, fault_id=fault.fault_id, source_refs=fault.effect_source_refs)
        evaluated_recovery_status, evaluated_recovery_reason, evaluated_recovery_trace, evaluated_recovery_findings = _evaluate_recovery(fault, observation_end)
        if evaluated_recovery_status == "BLOCKED":
            recovery_status = evaluated_recovery_status
            recovery_reason = evaluated_recovery_reason
            traces.extend(evaluated_recovery_trace)
            findings.extend(evaluated_recovery_findings)
        else:
            recovery_status = "UNKNOWN"
            recovery_reason = "RECOVERY_CAUSALITY_UNKNOWN"
            traces.append(_trace(fault, "RECOVERY", "RECOVERY_EVALUATED", "UNKNOWN", recovery_reason, rule_id=fault.recovery.policy_id, time=fault.recovery.completion_time, expected={"maximum_delay": _time_output(fault.recovery.maximum_delay)}, observed={"completion": fault.recovery.completion_value}, violated_bound="BASELINE"))
            _add(findings, "UNKNOWN", recovery_reason, f"$.faults[{fault.input_index}].recovery", "relevant baseline uncertainty prevents causal recovery attribution", rule_id=fault.recovery.policy_id, fault_id=fault.fault_id, source_refs=fault.recovery.source_refs)
        fault_status = "BLOCKED" if any(item.status == "BLOCKED" for item in findings) else "UNKNOWN"
        reason_codes = sorted(
            {
                item.code
                for item in findings
                if item.status == fault_status
            }
        ) or [effect_reason]
        return (
            {
                "fault_id": fault.fault_id,
                "fault_type": fault.fault_type,
                "target": _target_output(fault.target),
                "status": fault_status,
                "reason_code": reason_codes[0],
                "reason_codes": reason_codes,
                "effect": {"rule_id": fault.effect_rule_id, "status": "UNKNOWN", "reason_code": effect_reason, "severity": _measure_output(fault.severity), "threshold": _measure_output(fault.threshold)},
                "recovery": {"policy_id": fault.recovery.policy_id, "status": recovery_status, "reason_code": recovery_reason},
                "expectation": {"expected_status": fault.expected_status, "observed_status": fault_status, "matches": fault.expected_status == fault_status},
                "source_refs": sorted(set(fault.source_refs) | set(fault.effect_source_refs) | set(fault.recovery.source_refs) | set(fault.target.source_refs)),
            },
            traces,
            findings,
        )

    comparison: Optional[bool] = None
    effect_status, effect_reason, violated = "UNKNOWN", "FAULT_EFFECT_UNKNOWN", "UNKNOWN"
    if fault.provenance not in _TRUSTED_PROVENANCE:
        effect_reason = "FAULT_PROVENANCE_UNCERTAIN"
        violated = "PROVENANCE"
    elif fault.effect_provenance not in _TRUSTED_PROVENANCE:
        effect_reason = "EFFECT_RULE_PROVENANCE_UNCERTAIN"
        violated = "PROVENANCE"
    elif fault.target.provenance not in _TRUSTED_PROVENANCE:
        effect_reason = "TARGET_PROVENANCE_UNCERTAIN"
        violated = "PROVENANCE"
    elif fault.fault_type == "STUCK_LOW" and fault.target.polarity not in {"ACTIVE_HIGH", "ACTIVE_LOW"}:
        effect_reason = "STUCK_LOW_POLARITY_UNKNOWN"
    elif fault.severity.state == "KNOWN" and fault.threshold.state == "KNOWN":
        comparison = _compare(fault.severity.value, fault.operator, fault.threshold.value)
        if comparison is None:
            effect_reason = "FAULT_EFFECT_NOT_COMPARABLE"
        elif fault.fault_type == "RESTART":
            effect_status = "PASS" if comparison else "FAIL"
            effect_reason = "RESTART_REQUEST_ACCEPTED" if comparison else "RESTART_REQUEST_NOT_ASSERTED"
            violated = None if comparison else "EXPECTED_VALUE"
        elif comparison:
            effect_status, effect_reason, violated = "FAIL", f"{fault.fault_type}_EFFECT_TRIGGERED", "THRESHOLD"
        else:
            effect_status, effect_reason, violated = "PASS", "FAULT_THRESHOLD_NOT_VIOLATED", None
    traces.append(_trace(fault, "EFFECT", "FAULT_EFFECT_EVALUATED", effect_status, effect_reason, rule_id=fault.effect_rule_id, time=fault.start, expected={"operator": fault.operator, "threshold": _measure_output(fault.threshold)}, observed={"severity": _measure_output(fault.severity), "target_polarity": fault.target.polarity}, violated_bound=violated))
    _add(
        findings,
        effect_status,
        effect_reason,
        f"$.faults[{fault.input_index}].effect_rule",
        "fault effect rule was evaluated against the type-labelled severity",
        rule_id=fault.effect_rule_id,
        fault_id=fault.fault_id,
        source_refs=fault.effect_source_refs,
    )

    recovery_status, recovery_reason, recovery_trace, recovery_findings = _evaluate_recovery(fault, observation_end)
    traces.extend(recovery_trace)
    findings.extend(recovery_findings)
    _add(
        findings,
        recovery_status,
        recovery_reason,
        f"$.faults[{fault.input_index}].recovery",
        "fault recovery policy was evaluated against its closed deadline",
        rule_id=fault.recovery.policy_id,
        fault_id=fault.fault_id,
        source_refs=fault.recovery.source_refs,
    )
    fault_status = _status(findings, (effect_status, recovery_status))
    if fault.fault_type != "RESTART" and effect_status == "FAIL":
        fault_status = "BLOCKED" if any(item.status == "BLOCKED" for item in findings) else "FAIL"
    reason_candidates = [
        item.code for item in findings if item.status == fault_status
    ] + [
        reason for status, reason in ((effect_status, effect_reason), (recovery_status, recovery_reason)) if status == fault_status
    ]
    reason_codes = sorted(set(reason_candidates)) or ["FAULT_EVALUATION_COMPLETE"]
    return (
        {
            "fault_id": fault.fault_id,
            "fault_type": fault.fault_type,
            "target": _target_output(fault.target),
            "status": fault_status,
            "reason_code": reason_codes[0],
            "reason_codes": reason_codes,
            "effect": {"rule_id": fault.effect_rule_id, "status": effect_status, "reason_code": effect_reason, "severity": _measure_output(fault.severity), "threshold": _measure_output(fault.threshold)},
            "recovery": {"policy_id": fault.recovery.policy_id, "status": recovery_status, "reason_code": recovery_reason},
            "expectation": {"expected_status": fault.expected_status, "observed_status": fault_status, "matches": fault.expected_status == fault_status},
            "source_refs": sorted(set(fault.source_refs) | set(fault.effect_source_refs) | set(fault.recovery.source_refs) | set(fault.target.source_refs)),
        },
        traces,
        findings,
    )


def _overlaps(left: _Fault, right: _Fault) -> Optional[bool]:
    if left.start is None or left.duration is None or right.start is None or right.duration is None:
        return None
    left_end, right_end = _exact_add(left.start, left.duration), _exact_add(right.start, right.duration)
    if left.duration == 0 and right.duration == 0:
        return left.start == right.start
    if left.duration == 0:
        return right.start <= left.start < right_end
    if right.duration == 0:
        return left.start <= right.start < left_end
    return max(left.start, right.start) < min(left_end, right_end)


def _check_overlaps(faults: Sequence[_Fault], policy: str, findings: MutableSequence[_Finding]) -> None:
    for left_index, left in enumerate(faults):
        for right in faults[left_index + 1 :]:
            overlap = _overlaps(left, right)
            if overlap is None:
                _add(findings, "UNKNOWN", "FAULT_OVERLAP_INDETERMINATE", "$.faults", "unknown timing prevents overlap decision", rule_id="REP02.FI.OVERLAP")
            elif overlap and not (policy == "ALLOW_RESTART_RECOVERY" and "RESTART" in {left.fault_type, right.fault_type}):
                _add(findings, "BLOCKED", "FAULT_OVERLAP_NOT_ALLOWED", "$.faults", f"faults {left.fault_id} and {right.fault_id} overlap", rule_id="REP02.FI.OVERLAP")


def _source_output(sources: Mapping[str, Mapping[str, Any]], referenced: Iterable[str]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for source_id in sorted(set(referenced)):
        source = sources.get(source_id)
        if source is None:
            continue
        version = source.get("version")
        digest = source.get("content_hash")
        digest_value = digest.get("value") if isinstance(digest, dict) else None
        result.append(
            {
                "source_id": source_id,
                "version": version if isinstance(version, str) and version.strip() and not _is_floating_version(version) else None,
                "content_hash": digest_value if isinstance(digest_value, str) and _SHA256_RE.fullmatch(digest_value) else None,
            }
        )
    return result


def _base_result(input_hash: Optional[str]) -> Dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "tool_name": TOOL_NAME,
        "package_version": PACKAGE_VERSION,
        "versions": {
            "contract_schema": "1.0.0",
            "fault_engine": ENGINE_VERSION,
            "fault_rule_set": RULE_SET_VERSION,
            "package": PACKAGE_VERSION,
            "rail_budget_formula": RAIL_BUDGET_FORMULA_VERSION,
            "sequencing_fsm": SEQUENCING_FSM_VERSION,
            "sequencing_rule_set": SEQUENCING_RULE_SET_VERSION,
        },
        "status": "BLOCKED",
        "reason_code": "INPUT_VALIDATION_BLOCKED",
        "reason_codes": ["INPUT_VALIDATION_BLOCKED"],
        "input_hash": input_hash,
        "scenario": {"scenario_id": None, "generation": None, "observation_end": _time_output(None), "overlap_policy": None},
        "policy": {
            "generation": "FIXTURE_ONLY",
            "fault_interval": "HALF_OPEN_START_INCLUSIVE_END_EXCLUSIVE",
            "duration": "POSITIVE_EXCEPT_INSTANTANEOUS_RESTART_MAY_BE_ZERO",
            "target_polarity": "SIGNAL_ACTIVE_HIGH_ACTIVE_LOW_OR_UNKNOWN_NON_SIGNAL_NOT_APPLICABLE",
            "recovery_deadline": "CLOSED_MAXIMUM",
            "missing_recovery": "UNKNOWN_AT_OR_BEFORE_DEADLINE_FAIL_AFTER_DEADLINE",
        },
        "sources": [],
        "engine_runs": [
            {"engine": "rail_budget", "executed": False, "status": "NOT_RUN", "input_hash": None},
            {"engine": "sequencing", "executed": False, "status": "NOT_RUN", "input_hash": None},
        ],
        "baseline_results": {"rail_budget": None, "sequencing": None},
        "fault_results": [],
        "trace": [],
        "findings": [],
        "limitations": [
            "SYNTHETIC_FAULT_FIXTURES_ONLY",
            "NO_ANALOG_TRANSIENT_PHYSICS_MODEL",
            "NO_HARDWARE_RELIABILITY_CLAIM",
            "GAP_02_OPEN",
            "GAP_03_OPEN",
            "FAULT_CONTEXT_NOT_ADDED_TO_SEQUENCING_EVENTS",
            "ARRAY_ORDER_IS_INPUT_HASH_SIGNIFICANT",
        ],
        "claim_limits": [
            "BROWNOUT_AND_RAIL_LOSS_ARE_RULE_EVENTS_NOT_PHYSICAL_WAVEFORMS",
            "PASS_MEANS_ONLY_VERSIONED_TECHNICAL_FAULT_AND_RECOVERY_RULES_WERE_SATISFIED",
            "EXPECTED_STATUS_MATCH_IS_SEPARATE_AND_DOES_NOT_CHANGE_TECHNICAL_STATUS",
            "RESULT_IS_NOT_EMC_ISO_AEC_OR_FIELD_VALIDATION",
        ],
    }


def _run(document: Any, input_hash: Optional[str]) -> Dict[str, Any]:
    result = _base_result(input_hash)
    findings: List[_Finding] = []
    if not isinstance(document, dict):
        _add(findings, "BLOCKED", "TYPE_OBJECT", "$", "fault scenario must be an object")
        return _finish(result, findings)
    _reject(document, _ROOT_FIELDS, "$", findings)
    _require(document, _ROOT_FIELDS, "$", findings)
    if document.get("schema_version") != "1.0.0":
        _add(findings, "BLOCKED", "SCHEMA_VERSION_INVALID", "$.schema_version", "schema_version must be '1.0.0'")
    scenario_id = _identifier(document.get("scenario_id"), "$.scenario_id", findings, "SCENARIO_ID_INVALID")

    rail_input = document.get("rail_budget_input", _MISSING)
    sequence_input = document.get("sequencing_input", _MISSING)
    rail_result = calculate_rail_budget(rail_input)
    sequence_result = evaluate_sequencing(sequence_input)
    result["baseline_results"] = {"rail_budget": rail_result, "sequencing": sequence_result}
    result["engine_runs"] = [
        {"engine": "rail_budget", "executed": True, "status": rail_result.get("status"), "input_hash": rail_result.get("input_hash"), "tool_version": rail_result.get("tool_version")},
        {"engine": "sequencing", "executed": True, "status": sequence_result.get("status"), "input_hash": sequence_result.get("input_hash"), "tool_version": sequence_result.get("tool_version")},
    ]

    sources = _sources(document.get("sources"), findings)
    generation = _validate_generation(document.get("generation"), sources, findings)
    observation_value = document.get("observation")
    observation_end: Optional[Decimal] = None
    observation_provenance = "UNKNOWN"
    observation_refs: Tuple[str, ...] = ()
    if not isinstance(observation_value, dict):
        _add(findings, "BLOCKED", "OBSERVATION_OBJECT_REQUIRED", "$.observation", "observation must be an object")
    else:
        _reject(observation_value, _OBSERVATION_FIELDS, "$.observation", findings)
        _require(observation_value, _OBSERVATION_FIELDS, "$.observation", findings)
        observation_end = _time_quantity(observation_value.get("end_time"), "$.observation.end_time", findings, unknown_code="OBSERVATION_END_UNKNOWN")
        observation_provenance = _provenance(observation_value.get("provenance"), "$.observation.provenance", findings)
        observation_refs = _refs(observation_value.get("source_refs"), "$.observation.source_refs", findings)
        _gate_refs(observation_refs, observation_provenance, "$.observation.source_refs", sources, findings)
        if observation_provenance in _UNCERTAIN_PROVENANCE:
            _add(
                findings,
                "UNKNOWN",
                "OBSERVATION_PROVENANCE_UNCERTAIN",
                "$.observation.provenance",
                "untrusted observation provenance cannot support deadline or horizon decisions",
                rule_id="REP02.FI.OBSERVATION",
                source_refs=observation_refs,
            )
    observation_decision_end = (
        observation_end
        if observation_provenance in _TRUSTED_PROVENANCE
        else None
    )
    overlap_policy = document.get("overlap_policy")
    if overlap_policy not in {"REJECT", "ALLOW_RESTART_RECOVERY"}:
        _add(findings, "BLOCKED", "OVERLAP_POLICY_INVALID", "$.overlap_policy", "overlap_policy must be REJECT or ALLOW_RESTART_RECOVERY")
        overlap_policy = "REJECT"
    targets = _validate_targets(document.get("targets"), rail_input, sequence_input, sources, findings)
    faults = _validate_faults(document.get("faults"), targets, sources, findings)
    _check_overlaps(faults, overlap_policy, findings)

    relevant = set()
    for fault in faults:
        if fault.target.baseline_binding.startswith("RAIL_BUDGET_"):
            relevant.add("rail_budget")
        if fault.target.baseline_binding == "SEQUENCING_SUBJECT":
            relevant.add("sequencing")
    causally_unknown: set[str] = set()
    for name, baseline in (("rail_budget", rail_result), ("sequencing", sequence_result)):
        status = baseline.get("status")
        if status == "BLOCKED":
            _add(findings, "BLOCKED", f"BASELINE_{name.upper()}_BLOCKED", f"$.{name}_input", "baseline engine is blocked", rule_id="REP02.FI.BASELINE")
        elif name in relevant and status == "FAIL":
            _add(findings, "BLOCKED", f"BASELINE_{name.upper()}_FAIL_ATTRIBUTION_BLOCKED", f"$.{name}_input", "relevant baseline already fails, so fault attribution is blocked", rule_id="REP02.FI.BASELINE")
        elif name in relevant and status == "UNKNOWN":
            _add(findings, "UNKNOWN", f"BASELINE_{name.upper()}_UNKNOWN", f"$.{name}_input", "relevant baseline uncertainty makes causality unknown", rule_id="REP02.FI.BASELINE")
            causally_unknown.add(name)

    result["scenario"] = {"scenario_id": scenario_id, "generation": generation, "observation_end": _time_output(observation_end), "overlap_policy": overlap_policy}
    if not any(item.status == "BLOCKED" for item in findings):
        fault_results: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []
        for fault in faults:
            domain = (
                "rail_budget"
                if fault.target.baseline_binding.startswith("RAIL_BUDGET_")
                else "sequencing"
            )
            causal_code = (
                f"BASELINE_{domain.upper()}_UNKNOWN"
                if domain in causally_unknown
                else None
            )
            fault_result, fault_trace, fault_findings = _evaluate_fault(
                fault,
                observation_decision_end,
                causal_code,
            )
            fault_results.append(fault_result)
            trace.extend(fault_trace)
            findings.extend(fault_findings)
        trace.sort(
            key=lambda item: (
                item["time"]["state"] != "KNOWN",
                Decimal(item["time"]["decimal_text"])
                if item["time"]["decimal_text"] is not None
                else Decimal(0),
                item["input_index"],
                {"INJECTION": 0, "EFFECT": 1, "RECOVERY": 2}[item["phase"]],
            )
        )
        for index, entry in enumerate(trace):
            entry["trace_index"] = index
        result["fault_results"] = fault_results
        result["trace"] = trace

    referenced: set[str] = set(observation_refs) | set(generation.get("source_refs", []))
    for target in targets.values():
        referenced.update(target.source_refs)
    for fault in faults:
        referenced.update(fault.source_refs)
        referenced.update(fault.effect_source_refs)
        referenced.update(fault.recovery.source_refs)
    result["sources"] = _source_output(sources, referenced)
    return _finish(result, findings)


def _finish(result: Dict[str, Any], findings: Sequence[_Finding]) -> Dict[str, Any]:
    ordered = sorted(findings, key=lambda item: (item.status, item.code, item.path, item.fault_id or "", item.message))
    fault_statuses = [item.get("status", "BLOCKED") for item in result.get("fault_results", [])]
    status = _status(ordered, fault_statuses)
    if status == "PASS":
        reason_codes = ["FAULT_SCENARIO_COMPLETE"]
    else:
        codes = {item.code for item in ordered if item.status == status}
        for item in result.get("fault_results", []):
            if item.get("status") == status:
                codes.update(item.get("reason_codes", []))
        reason_codes = sorted(codes) or ["INPUT_VALIDATION_BLOCKED"]
    result["status"] = status
    result["reason_codes"] = reason_codes
    result["reason_code"] = reason_codes[0]
    result["findings"] = [item.as_dict(index) for index, item in enumerate(ordered, 1)]
    return result


def run_fault_scenario(document: Any) -> Dict[str, Any]:
    """Run one JSON-compatible fault scenario without mutation, I/O, or RNG.

    Invalid, non-finite, cyclic, and pathologically deep inputs fail closed as
    deterministic, JSON-safe ``BLOCKED`` results.
    """

    try:
        input_hash: Optional[str] = sha256_hexdigest(document)
    except (RecursionError, TypeError, ValueError):
        input_hash = None
    try:
        result = _run(document, input_hash)
    except (ArithmeticError, OverflowError, RecursionError, TypeError, ValueError):
        result = _base_result(input_hash)
        result = _finish(result, [_Finding("BLOCKED", "INPUT_VALIDATION_BLOCKED", "$", "input could not be safely evaluated")])
    if input_hash is None:
        findings = [
            _Finding(item["status"], item["reason_code"], item["path"], item["message"], item["rule_id"], item.get("fault_id"), tuple(item.get("source_refs", [])))
            for item in result.get("findings", [])
        ]
        findings.append(_Finding("BLOCKED", "INPUT_HASH_UNCOMPUTABLE", "$", "canonical hash could not be computed for the original input"))
        result["input_hash"] = None
        result = _finish(result, findings)
    return result
