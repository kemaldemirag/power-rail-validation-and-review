"""Deterministic power-sequencing evaluator for REP-02 WF-02-03.

The evaluator owns a deliberately small, closed input envelope.  It reuses
the D1 ``event`` and ``source`` record shapes, but keeps the sequencing rules
and result contract local to this module.  All decision-time arithmetic uses
``Decimal``; ``occurred_at`` is an independent audit coordinate and never
silently replaces ``time_offset``.

``PASS`` is limited to the supplied, hash-bound rules.  In particular, the
synthetic fixture windows are engineering rules, not manufacturer limits and
not evidence of physical hardware behaviour.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest


ENGINE_VERSION = "0.3.0"
FSM_VERSION = "REP02-SEQ-FSM-1.0.0"
RULE_SET_VERSION = "REP02-SEQ-RULESET-1.0.0"
TOOL_NAME = "power-rail-validation"

SORTING_POLICY = "TIME_OFFSET_ASCENDING"
SIMULTANEOUS_POLICIES = frozenset({"REJECT", "ALLOW_ZERO_MIN_EXPECTED_ORDER"})

_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "scenario_id",
        "scenario_kind",
        "timing_rules",
        "power_off_rule",
        "events",
        "sources",
        "observation_end_offset",
        "observation_end_at",
        "observation_provenance",
        "observation_source_refs",
        "sorting_policy",
        "simultaneous_policy",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "entity_type",
        "event_id",
        "event_type",
        "event_kind",
        "state",
        "occurred_at",
        "time_offset",
        "subject_refs",
        "measurements",
        "provenance",
        "source_refs",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "schema_version",
        "entity_type",
        "source_id",
        "classification",
        "title",
        "version",
        "locator",
        "access_status",
        "content_hash",
    }
)
_RULE_FIELDS = frozenset(
    {
        "rule_id",
        "expected_event",
        "minimum_delay",
        "maximum_delay",
        "limit_origin",
        "provenance",
        "source_refs",
    }
)
_POWER_OFF_RULE_FIELDS = frozenset(
    {"rule_id", "expected_event", "provenance", "source_refs"}
)
_EXPECTED_EVENT_FIELDS = frozenset({"event_kind", "state", "subject_refs"})
_QUANTITY_FIELDS = frozenset({"state", "value", "unit"})
_HASH_FIELDS = frozenset({"state", "algorithm", "value"})
_MEASUREMENT_FIELDS = frozenset({"name", "quantity"})

_EVENT_TYPES = frozenset(
    {
        "MEASUREMENT",
        "SIMULATION",
        "CALCULATION",
        "REVIEW",
        "SIGNAL_TRANSITION",
        "POWER_STATE",
        "FAULT",
    }
)
_EVENT_KINDS = frozenset(
    {"VIN_VALID", "EN", "PGOOD", "RESET", "POWER_OFF", "BROWNOUT", "RAIL_LOSS", "RESTART", "OTHER"}
)
_EVENT_STATES = frozenset(
    {"ASSERTED", "DEASSERTED", "ENTERED", "EXITED", "ACTIVE", "INACTIVE", "UNKNOWN"}
)
_PROVENANCE = frozenset(
    {"SOURCE_EXPLICIT", "DERIVED", "ENGINEERING_RULE", "ASSUMPTION", "UNKNOWN", "CONFLICT"}
)
_TRUSTED_PROVENANCE = frozenset({"SOURCE_EXPLICIT", "ENGINEERING_RULE"})
_UNCERTAIN_PROVENANCE = frozenset({"DERIVED", "ASSUMPTION", "UNKNOWN"})
_SOURCE_ACCESS = frozenset({"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"})
_QUANTITY_UNITS = frozenset({"V", "A", "Hz", "s"})
_LIMIT_ORIGINS = frozenset({"SYNTHETIC", "SOURCED"})

_EXPECTED_TRANSITIONS: Tuple[Tuple[str, str, str, str], ...] = (
    ("VIN_VALID", "ASSERTED", "WAIT_VIN_VALID", "WAIT_EN"),
    ("EN", "ASSERTED", "WAIT_EN", "WAIT_PGOOD"),
    ("PGOOD", "ASSERTED", "WAIT_PGOOD", "WAIT_RESET_RELEASE"),
    ("RESET", "DEASSERTED", "WAIT_RESET_RELEASE", "RUNNING"),
)
_TRANSITION_RANK = {(kind, state): index for index, (kind, state, _, _) in enumerate(_EXPECTED_TRANSITIONS)}

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?P<fraction>\.[0-9]+)?(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})$"
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
        "n/a",
        "na",
        "nightly",
        "stable",
        "tbd",
        "tip",
        "trunk",
        "unknown",
        "unspecified",
    }
)
_MISSING = object()
_MAX_DECIMAL_PRECISION = 10000


class _DecimalPrecisionError(ArithmeticError):
    """Raised when an exact operation would exceed the bounded work profile."""


@dataclass(frozen=True)
class _Issue:
    severity: str
    code: str
    path: str
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class _Rule:
    input_index: int
    rule_id: str
    event_kind: str
    event_state: str
    event_subject_refs: Tuple[str, ...]
    minimum: Optional[Decimal]
    maximum: Optional[Decimal]
    minimum_state: str
    maximum_state: str
    limit_origin: str
    provenance: str
    source_refs: Tuple[str, ...]


@dataclass(frozen=True)
class _Event:
    input_index: int
    event_id: str
    event_type: str
    event_kind: str
    event_state: str
    subject_refs: Tuple[str, ...]
    offset: Optional[Decimal]
    offset_state: str
    occurred_seconds: Optional[Decimal]
    provenance: str
    source_refs: Tuple[str, ...]


def _add_issue(
    issues: MutableSequence[_Issue], severity: str, code: str, path: str, message: str
) -> None:
    candidate = _Issue(severity=severity, code=code, path=path, message=message)
    if candidate not in issues:
        issues.append(candidate)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_decimal(value: Any) -> Optional[Decimal]:
    if not _is_number(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return converted if converted.is_finite() else None


def _exact_precision(left: Decimal, right: Decimal) -> int:
    """Return a bounded context precision sufficient for exact add/subtract."""

    nonzero = [value for value in (left, right) if value != 0]
    if not nonzero:
        return 2
    highest = max(value.adjusted() for value in nonzero)
    lowest_exponent = min(value.as_tuple().exponent for value in nonzero)
    precision = max(
        len(value.as_tuple().digits) for value in nonzero
    )
    precision = max(precision, highest - lowest_exponent + 3, 32)
    if precision > _MAX_DECIMAL_PRECISION:
        raise _DecimalPrecisionError("exact Decimal operation exceeds the precision cap")
    return precision


def _exact_add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _exact_precision(left, right)
        result = left + right
    if not result.is_finite():
        raise _DecimalPrecisionError("exact Decimal addition produced a non-finite value")
    return result

def _exact_subtract(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _exact_precision(left, right)
        result = left - right
    if not result.is_finite():
        raise _DecimalPrecisionError("exact Decimal subtraction produced a non-finite value")
    return result


def _json_number(value: Decimal) -> Union[int, float]:
    if value == value.to_integral_value():
        return int(value)
    candidate = float(value)
    if not math.isfinite(candidate) or (candidate == 0 and value != 0):
        raise OverflowError("Decimal is not representable as a finite nonzero JSON number")
    return candidate


def _quantity_output(value: Optional[Decimal]) -> Dict[str, Any]:
    if value is None:
        return {"state": "UNKNOWN", "value": None, "decimal_text": None, "unit": "s"}
    return {
        "state": "KNOWN",
        "value": _json_number(value),
        "decimal_text": str(value),
        "unit": "s",
    }


def _is_floating_version(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _FLOATING_VERSION_TOKENS:
        return True
    if normalized.startswith("nightly/") or normalized.startswith("nightly-"):
        return True
    if any(token in normalized for token in ("*", "^", "~", ">", "<", "=")):
        return True
    if re.search(r"(?:^|[./-])x(?:$|[./-])", normalized):
        return True
    if normalized.startswith(("branch/", "refs/heads/", "refs/remotes/", "origin/")):
        return True
    segments = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    return bool(segments and segments[-1] in _FLOATING_VERSION_TOKENS)


def _reject_unexpected_fields(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    path: str,
    issues: MutableSequence[_Issue],
) -> None:
    allowed_set = set(allowed)
    unexpected = [key for key in value if not isinstance(key, str) or key not in allowed_set]
    for key in sorted(unexpected, key=repr):
        _add_issue(
            issues,
            "BLOCKED",
            "UNEXPECTED_FIELD",
            f"{path}.{key!r}",
            "field is outside the closed sequencing contract",
        )


def _required_members(
    value: Mapping[str, Any], names: Iterable[str], path: str, issues: MutableSequence[_Issue]
) -> None:
    for name in names:
        if name not in value:
            _add_issue(
                issues,
                "BLOCKED",
                "REQUIRED_INPUT_MISSING",
                f"{path}.{name}",
                "required member is absent",
            )


def _identifier(value: Any, path: str, issues: MutableSequence[_Issue], code: str) -> Optional[str]:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _add_issue(issues, "BLOCKED", code, path, "expected a stable identifier")
        return None
    return value


def _id_refs(value: Any, path: str, issues: MutableSequence[_Issue]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        _add_issue(
            issues,
            "BLOCKED",
            "SOURCE_REFS_REQUIRED" if path.endswith("source_refs") else "ID_REFS_REQUIRED",
            path,
            "expected a non-empty array of unique stable identifiers",
        )
        return ()
    result: List[str] = []
    for index, item in enumerate(value):
        identifier = _identifier(item, f"{path}[{index}]", issues, "SOURCE_REF_INVALID" if path.endswith("source_refs") else "ID_REF_INVALID")
        if identifier is None:
            continue
        if identifier in result:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_REF_DUPLICATE" if path.endswith("source_refs") else "ID_REF_DUPLICATE",
                f"{path}[{index}]",
                "identifier references must be unique",
            )
            continue
        result.append(identifier)
    return tuple(result)


def _provenance(value: Any, path: str, issues: MutableSequence[_Issue]) -> str:
    if not isinstance(value, str) or value not in _PROVENANCE:
        _add_issue(issues, "BLOCKED", "PROVENANCE_INVALID", path, "unrecognized provenance classification")
        return "UNKNOWN"
    if value == "CONFLICT":
        _add_issue(issues, "BLOCKED", "PROVENANCE_CONFLICT", path, "conflicted evidence cannot support an assessment")
    elif value in _UNCERTAIN_PROVENANCE:
        _add_issue(
            issues,
            "UNKNOWN",
            "PROVENANCE_UNCERTAIN",
            path,
            "assumed, unverified derived, or unknown evidence cannot support PASS",
        )
    return value


def _time_quantity(
    value: Any,
    path: str,
    issues: MutableSequence[_Issue],
    *,
    unknown_code: str,
) -> Tuple[Optional[Decimal], str]:
    if not isinstance(value, dict):
        _add_issue(issues, "BLOCKED", "TIME_QUANTITY_REQUIRED", path, "expected {state, value, unit}")
        return None, "INVALID"
    _reject_unexpected_fields(value, _QUANTITY_FIELDS, path, issues)
    _required_members(value, _QUANTITY_FIELDS, path, issues)
    if value.get("unit") != "s":
        _add_issue(issues, "BLOCKED", "TIME_UNIT_INVALID", f"{path}.unit", "time quantity unit must be 's'")
    state = value.get("state")
    if state == "UNKNOWN":
        if value.get("value") is not None:
            _add_issue(issues, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{path}.value", "UNKNOWN quantity requires null")
        _add_issue(issues, "UNKNOWN", unknown_code, path, "time quantity is explicitly unknown")
        return None, "UNKNOWN"
    if state != "KNOWN":
        _add_issue(issues, "BLOCKED", "QUANTITY_STATE_INVALID", f"{path}.state", "expected KNOWN or UNKNOWN")
        return None, "INVALID"
    converted = _to_decimal(value.get("value"))
    if converted is None:
        _add_issue(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", f"{path}.value", "KNOWN time requires a finite JSON number")
        return None, "INVALID"
    if converted < 0:
        _add_issue(issues, "BLOCKED", "NEGATIVE_TIME_INVALID", f"{path}.value", "time value must be nonnegative")
        return None, "INVALID"
    if value.get("unit") != "s":
        return None, "INVALID"
    return converted, "KNOWN"


def _rfc3339_seconds(value: Any, path: str, issues: MutableSequence[_Issue]) -> Optional[Decimal]:
    if not isinstance(value, str):
        _add_issue(issues, "BLOCKED", "TIMESTAMP_INVALID", path, "occurred_at must be an RFC 3339 string")
        return None
    match = _RFC3339_RE.fullmatch(value)
    if match is None:
        _add_issue(issues, "BLOCKED", "TIMESTAMP_INVALID", path, "occurred_at must include date, time, and UTC offset")
        return None
    try:
        second = int(match.group("second"))
        if second > 59:
            raise ValueError("leap seconds are outside this deterministic profile")
        zone_text = match.group("zone")
        if zone_text == "Z":
            zone = timezone.utc
        else:
            sign = 1 if zone_text[0] == "+" else -1
            hours = int(zone_text[1:3])
            minutes = int(zone_text[4:6])
            if hours > 23 or minutes > 59:
                raise ValueError("invalid UTC offset")
            zone = timezone(sign * timedelta(hours=hours, minutes=minutes))
        base = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            second,
            tzinfo=zone,
        ).astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = base - epoch
        whole = Decimal(delta.days * 86400 + delta.seconds)
        fraction_text = match.group("fraction")
        fraction = Decimal(f"0{fraction_text}") if fraction_text else Decimal(0)
        return _exact_add(whole, fraction)
    except (InvalidOperation, OverflowError, ValueError, _DecimalPrecisionError) as exc:
        _add_issue(issues, "BLOCKED", "TIMESTAMP_INVALID", path, f"invalid RFC 3339 timestamp: {exc}")
        return None


def _validate_measurements(value: Any, path: str, issues: MutableSequence[_Issue]) -> None:
    if not isinstance(value, list):
        _add_issue(issues, "BLOCKED", "MEASUREMENTS_ARRAY_REQUIRED", path, "measurements must be an array")
        return
    for index, measurement in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(measurement, dict):
            _add_issue(issues, "BLOCKED", "TYPE_OBJECT", item_path, "measurement must be an object")
            continue
        _reject_unexpected_fields(measurement, _MEASUREMENT_FIELDS, item_path, issues)
        _required_members(measurement, _MEASUREMENT_FIELDS, item_path, issues)
        if not isinstance(measurement.get("name"), str) or not measurement.get("name", "").strip():
            _add_issue(issues, "BLOCKED", "MEASUREMENT_NAME_INVALID", f"{item_path}.name", "name must be non-empty")
        quantity = measurement.get("quantity")
        if not isinstance(quantity, dict):
            _add_issue(issues, "BLOCKED", "QUANTITY_OBJECT_REQUIRED", f"{item_path}.quantity", "quantity must be an object")
            continue
        _reject_unexpected_fields(quantity, _QUANTITY_FIELDS, f"{item_path}.quantity", issues)
        _required_members(quantity, _QUANTITY_FIELDS, f"{item_path}.quantity", issues)
        if quantity.get("unit") not in _QUANTITY_UNITS:
            _add_issue(issues, "BLOCKED", "QUANTITY_UNIT_INVALID", f"{item_path}.quantity.unit", "unsupported D1 unit")
        state = quantity.get("state")
        if state == "KNOWN":
            if _to_decimal(quantity.get("value")) is None:
                _add_issue(issues, "BLOCKED", "FINITE_NUMBER_REQUIRED", f"{item_path}.quantity.value", "KNOWN quantity requires a finite number")
        elif state == "UNKNOWN":
            if quantity.get("value") is not None:
                _add_issue(issues, "BLOCKED", "UNKNOWN_MUST_BE_NULL", f"{item_path}.quantity.value", "UNKNOWN quantity requires null")
        else:
            _add_issue(issues, "BLOCKED", "QUANTITY_STATE_INVALID", f"{item_path}.quantity.state", "expected KNOWN or UNKNOWN")


def _validate_sources(value: Any, issues: MutableSequence[_Issue]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        _add_issue(issues, "BLOCKED", "SOURCES_ARRAY_REQUIRED", "$.sources", "sources must be an array")
        return {}
    result: Dict[str, Mapping[str, Any]] = {}
    for index, source in enumerate(value):
        path = f"$.sources[{index}]"
        if not isinstance(source, dict):
            _add_issue(issues, "BLOCKED", "TYPE_OBJECT", path, "source must be an object")
            continue
        _reject_unexpected_fields(source, _SOURCE_FIELDS, path, issues)
        _required_members(source, _SOURCE_FIELDS, path, issues)
        if source.get("schema_version") != "1.0.0":
            _add_issue(issues, "BLOCKED", "SOURCE_SCHEMA_VERSION_INVALID", f"{path}.schema_version", "source schema_version must be '1.0.0'")
        if source.get("entity_type") != "source":
            _add_issue(issues, "BLOCKED", "SOURCE_ENTITY_TYPE_INVALID", f"{path}.entity_type", "source entity_type must be 'source'")
        source_id = _identifier(source.get("source_id"), f"{path}.source_id", issues, "SOURCE_ID_INVALID")
        if source_id is not None:
            if source_id in result:
                _add_issue(issues, "BLOCKED", "SOURCE_ID_DUPLICATE", f"{path}.source_id", "source identifiers must be unique")
            else:
                result[source_id] = source
        classification = source.get("classification")
        if not isinstance(classification, str) or classification not in _PROVENANCE:
            _add_issue(issues, "BLOCKED", "SOURCE_CLASSIFICATION_INVALID", f"{path}.classification", "unrecognized source classification")
        for field in ("title", "locator"):
            if not isinstance(source.get(field), str) or not source.get(field, "").strip():
                _add_issue(issues, "BLOCKED", "SOURCE_METADATA_INVALID", f"{path}.{field}", f"source {field} must be non-empty")
        version = source.get("version")
        if not isinstance(version, str) or not version.strip():
            _add_issue(issues, "BLOCKED", "SOURCE_VERSION_MISSING", f"{path}.version", "source version must be pinned and non-empty")
        elif _is_floating_version(version):
            _add_issue(issues, "BLOCKED", "SOURCE_VERSION_FLOATING", f"{path}.version", "mutable source version aliases are forbidden")
        access = source.get("access_status")
        if access not in _SOURCE_ACCESS:
            _add_issue(issues, "BLOCKED", "SOURCE_ACCESS_STATUS_INVALID", f"{path}.access_status", "unrecognized source access status")
        content_hash = source.get("content_hash")
        if not isinstance(content_hash, dict):
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_INVALID", f"{path}.content_hash", "content_hash must be an object")
            continue
        _reject_unexpected_fields(content_hash, _HASH_FIELDS, f"{path}.content_hash", issues)
        _required_members(content_hash, _HASH_FIELDS, f"{path}.content_hash", issues)
        state = content_hash.get("state")
        if state not in {"KNOWN", "UNKNOWN"}:
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_STATE_INVALID", f"{path}.content_hash.state", "hash state must be KNOWN or UNKNOWN")
        if content_hash.get("algorithm") != "SHA-256":
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_ALGORITHM_INVALID", f"{path}.content_hash.algorithm", "hash algorithm must be SHA-256")
        hash_value = content_hash.get("value")
        if state == "KNOWN" and (not isinstance(hash_value, str) or _SHA256_RE.fullmatch(hash_value) is None):
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_VALUE_INVALID", f"{path}.content_hash.value", "KNOWN hash requires 64 lowercase hexadecimal characters")
        if state == "UNKNOWN" and hash_value is not None:
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_UNKNOWN_MUST_BE_NULL", f"{path}.content_hash.value", "UNKNOWN hash requires null")
        if access == "AVAILABLE" and state != "KNOWN":
            _add_issue(issues, "BLOCKED", "SOURCE_AVAILABLE_HASH_REQUIRED", f"{path}.content_hash.state", "AVAILABLE source requires a KNOWN hash")
        if access in {"MISSING", "UNKNOWN"} and state != "UNKNOWN":
            _add_issue(issues, "BLOCKED", "SOURCE_UNAVAILABLE_HASH_MUST_BE_UNKNOWN", f"{path}.content_hash.state", "MISSING or UNKNOWN source requires UNKNOWN hash")
    return result


def _gate_refs(
    refs: Sequence[str],
    path: str,
    sources: Mapping[str, Mapping[str, Any]],
    issues: MutableSequence[_Issue],
) -> None:
    for index, source_id in enumerate(refs):
        ref_path = f"{path}[{index}]"
        source = sources.get(source_id)
        if source is None:
            _add_issue(issues, "BLOCKED", "SOURCE_REF_UNRESOLVED", ref_path, "referenced source is absent")
            continue
        if source.get("access_status") != "AVAILABLE":
            _add_issue(issues, "BLOCKED", "SOURCE_UNAVAILABLE", ref_path, "required source is not AVAILABLE")
        content_hash = source.get("content_hash")
        if not (
            isinstance(content_hash, dict)
            and content_hash.get("state") == "KNOWN"
            and content_hash.get("algorithm") == "SHA-256"
            and isinstance(content_hash.get("value"), str)
            and _SHA256_RE.fullmatch(content_hash["value"]) is not None
        ):
            _add_issue(issues, "BLOCKED", "SOURCE_HASH_UNKNOWN", ref_path, "required source is not bound to a known SHA-256")
        classification = source.get("classification")
        if classification == "CONFLICT":
            _add_issue(issues, "BLOCKED", "SOURCE_CONFLICT", ref_path, "conflicted source cannot support an assessment")
        elif classification in _UNCERTAIN_PROVENANCE:
            _add_issue(issues, "UNKNOWN", "SOURCE_PROVENANCE_UNCERTAIN", ref_path, "unverified source classification cannot support PASS")


def _validate_rules(
    value: Any,
    sources: Mapping[str, Mapping[str, Any]],
    issues: MutableSequence[_Issue],
) -> List[_Rule]:
    if not isinstance(value, list):
        _add_issue(issues, "BLOCKED", "TIMING_RULES_ARRAY_REQUIRED", "$.timing_rules", "timing_rules must be an array")
        return []
    if len(value) != len(_EXPECTED_TRANSITIONS):
        _add_issue(issues, "BLOCKED", "TIMING_RULE_COUNT_INVALID", "$.timing_rules", "exactly four ordered FSM timing rules are required")
    rules: List[_Rule] = []
    rule_ids: set[str] = set()
    for index, rule in enumerate(value):
        path = f"$.timing_rules[{index}]"
        if not isinstance(rule, dict):
            _add_issue(issues, "BLOCKED", "TYPE_OBJECT", path, "timing rule must be an object")
            continue
        _reject_unexpected_fields(rule, _RULE_FIELDS, path, issues)
        _required_members(rule, _RULE_FIELDS, path, issues)
        rule_id = _identifier(rule.get("rule_id"), f"{path}.rule_id", issues, "RULE_ID_INVALID")
        if rule_id is None:
            rule_id = f"INVALID-RULE-{index}"
        elif rule_id in rule_ids:
            _add_issue(issues, "BLOCKED", "RULE_ID_DUPLICATE", f"{path}.rule_id", "rule identifiers must be unique")
        rule_ids.add(rule_id)
        expected = rule.get("expected_event")
        event_kind = "OTHER"
        event_state = "UNKNOWN"
        event_subject_refs: Tuple[str, ...] = ()
        if not isinstance(expected, dict):
            _add_issue(issues, "BLOCKED", "EXPECTED_EVENT_REQUIRED", f"{path}.expected_event", "expected_event must be an object")
        else:
            _reject_unexpected_fields(expected, _EXPECTED_EVENT_FIELDS, f"{path}.expected_event", issues)
            _required_members(expected, _EXPECTED_EVENT_FIELDS, f"{path}.expected_event", issues)
            if expected.get("event_kind") not in _EVENT_KINDS:
                _add_issue(issues, "BLOCKED", "EVENT_KIND_INVALID", f"{path}.expected_event.event_kind", "unrecognized event kind")
            else:
                event_kind = expected["event_kind"]
            if expected.get("state") not in _EVENT_STATES:
                _add_issue(issues, "BLOCKED", "EVENT_STATE_INVALID", f"{path}.expected_event.state", "unrecognized event state")
            else:
                event_state = expected["state"]
            event_subject_refs = _id_refs(
                expected.get("subject_refs"),
                f"{path}.expected_event.subject_refs",
                issues,
            )
        if index < len(_EXPECTED_TRANSITIONS):
            required_kind, required_state, _, _ = _EXPECTED_TRANSITIONS[index]
            if (event_kind, event_state) != (required_kind, required_state):
                _add_issue(
                    issues,
                    "BLOCKED",
                    "FSM_RULE_ORDER_INVALID",
                    f"{path}.expected_event",
                    f"rule {index} must expect {required_kind}/{required_state}",
                )
        minimum, minimum_state = _time_quantity(
            rule.get("minimum_delay"),
            f"{path}.minimum_delay",
            issues,
            unknown_code="TIMING_WINDOW_UNKNOWN",
        )
        maximum, maximum_state = _time_quantity(
            rule.get("maximum_delay"),
            f"{path}.maximum_delay",
            issues,
            unknown_code="TIMING_WINDOW_UNKNOWN",
        )
        if minimum is not None and maximum is not None and maximum < minimum:
            _add_issue(issues, "BLOCKED", "TIMING_WINDOW_INVALID", path, "maximum_delay must be greater than or equal to minimum_delay")
        limit_origin = rule.get("limit_origin")
        if limit_origin not in _LIMIT_ORIGINS:
            _add_issue(issues, "BLOCKED", "LIMIT_ORIGIN_INVALID", f"{path}.limit_origin", "expected SYNTHETIC or SOURCED")
            limit_origin = "SYNTHETIC"
        provenance = _provenance(rule.get("provenance"), f"{path}.provenance", issues)
        refs = _id_refs(rule.get("source_refs"), f"{path}.source_refs", issues)
        _gate_refs(refs, f"{path}.source_refs", sources, issues)
        if limit_origin == "SYNTHETIC" and provenance != "ENGINEERING_RULE":
            severity = "UNKNOWN" if provenance in _UNCERTAIN_PROVENANCE else "BLOCKED"
            _add_issue(issues, severity, "SYNTHETIC_LIMIT_PROVENANCE_INVALID", f"{path}.provenance", "synthetic limits require ENGINEERING_RULE provenance")
        if limit_origin == "SOURCED" and provenance != "SOURCE_EXPLICIT":
            severity = "UNKNOWN" if provenance in _UNCERTAIN_PROVENANCE else "BLOCKED"
            _add_issue(issues, severity, "SOURCED_LIMIT_PROVENANCE_INVALID", f"{path}.provenance", "sourced limits require SOURCE_EXPLICIT provenance")
        expected_source_classification = (
            "ENGINEERING_RULE" if limit_origin == "SYNTHETIC" else "SOURCE_EXPLICIT"
        )
        for ref_index, source_id in enumerate(refs):
            source = sources.get(source_id)
            if source is None:
                continue
            classification = source.get("classification")
            if classification != expected_source_classification:
                severity = "UNKNOWN" if classification in _UNCERTAIN_PROVENANCE else "BLOCKED"
                _add_issue(
                    issues,
                    severity,
                    "LIMIT_SOURCE_CLASSIFICATION_MISMATCH",
                    f"{path}.source_refs[{ref_index}]",
                    f"{limit_origin} limit requires a {expected_source_classification} source",
                )
        rules.append(
            _Rule(
                input_index=index,
                rule_id=rule_id,
                event_kind=event_kind,
                event_state=event_state,
                event_subject_refs=event_subject_refs,
                minimum=minimum,
                maximum=maximum,
                minimum_state=minimum_state,
                maximum_state=maximum_state,
                limit_origin=limit_origin,
                provenance=provenance,
                source_refs=refs,
            )
        )
    return rules


def _validate_power_off_rule(
    value: Any,
    timing_rules: Sequence[_Rule],
    sources: Mapping[str, Mapping[str, Any]],
    issues: MutableSequence[_Issue],
) -> _Rule:
    path = "$.power_off_rule"
    fallback = _Rule(
        input_index=-1,
        rule_id="REP02.SEQ.POWER_OFF.INVALID",
        event_kind="POWER_OFF",
        event_state="ENTERED",
        event_subject_refs=(),
        minimum=None,
        maximum=None,
        minimum_state="NOT_APPLICABLE",
        maximum_state="NOT_APPLICABLE",
        limit_origin="EVENT_POLICY",
        provenance="UNKNOWN",
        source_refs=(),
    )
    if not isinstance(value, dict):
        _add_issue(
            issues,
            "BLOCKED",
            "POWER_OFF_RULE_REQUIRED",
            path,
            "power_off_rule must be an object",
        )
        return fallback
    _reject_unexpected_fields(value, _POWER_OFF_RULE_FIELDS, path, issues)
    _required_members(value, _POWER_OFF_RULE_FIELDS, path, issues)
    rule_id = _identifier(
        value.get("rule_id"),
        f"{path}.rule_id",
        issues,
        "RULE_ID_INVALID",
    )
    if rule_id is None:
        rule_id = fallback.rule_id
    if any(rule.rule_id == rule_id for rule in timing_rules):
        _add_issue(
            issues,
            "BLOCKED",
            "RULE_ID_DUPLICATE",
            f"{path}.rule_id",
            "power-off and timing rule identifiers must be unique",
        )

    expected = value.get("expected_event")
    event_kind = "POWER_OFF"
    event_state = "ENTERED"
    subject_refs: Tuple[str, ...] = ()
    if not isinstance(expected, dict):
        _add_issue(
            issues,
            "BLOCKED",
            "EXPECTED_EVENT_REQUIRED",
            f"{path}.expected_event",
            "expected_event must be an object",
        )
    else:
        _reject_unexpected_fields(
            expected,
            _EXPECTED_EVENT_FIELDS,
            f"{path}.expected_event",
            issues,
        )
        _required_members(
            expected,
            _EXPECTED_EVENT_FIELDS,
            f"{path}.expected_event",
            issues,
        )
        if expected.get("event_kind") != "POWER_OFF" or expected.get("state") != "ENTERED":
            _add_issue(
                issues,
                "BLOCKED",
                "POWER_OFF_RULE_EVENT_INVALID",
                f"{path}.expected_event",
                "power_off_rule must expect exactly POWER_OFF/ENTERED",
            )
        subject_refs = _id_refs(
            expected.get("subject_refs"),
            f"{path}.expected_event.subject_refs",
            issues,
        )

    provenance = _provenance(
        value.get("provenance"),
        f"{path}.provenance",
        issues,
    )
    refs = _id_refs(value.get("source_refs"), f"{path}.source_refs", issues)
    _gate_refs(refs, f"{path}.source_refs", sources, issues)
    if provenance in _TRUSTED_PROVENANCE:
        for ref_index, source_id in enumerate(refs):
            source = sources.get(source_id)
            if source is not None and source.get("classification") != provenance:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "POWER_OFF_RULE_SOURCE_CLASSIFICATION_MISMATCH",
                    f"{path}.source_refs[{ref_index}]",
                    "trusted power-off rule provenance must match every referenced source classification",
                )

    return _Rule(
        input_index=-1,
        rule_id=rule_id,
        event_kind=event_kind,
        event_state=event_state,
        event_subject_refs=subject_refs,
        minimum=None,
        maximum=None,
        minimum_state="NOT_APPLICABLE",
        maximum_state="NOT_APPLICABLE",
        limit_origin="EVENT_POLICY",
        provenance=provenance,
        source_refs=refs,
    )


def _validate_events(
    value: Any,
    sources: Mapping[str, Mapping[str, Any]],
    issues: MutableSequence[_Issue],
) -> List[_Event]:
    if not isinstance(value, list):
        _add_issue(issues, "BLOCKED", "EVENTS_ARRAY_REQUIRED", "$.events", "events must be an array")
        return []
    events: List[_Event] = []
    ids: set[str] = set()
    for index, event in enumerate(value):
        path = f"$.events[{index}]"
        if not isinstance(event, dict):
            _add_issue(issues, "BLOCKED", "TYPE_OBJECT", path, "event must be an object")
            continue
        _reject_unexpected_fields(event, _EVENT_FIELDS, path, issues)
        _required_members(event, _EVENT_FIELDS, path, issues)
        if event.get("schema_version") != "1.0.0":
            _add_issue(issues, "BLOCKED", "EVENT_SCHEMA_VERSION_INVALID", f"{path}.schema_version", "event schema_version must be '1.0.0'")
        if event.get("entity_type") != "event":
            _add_issue(issues, "BLOCKED", "EVENT_ENTITY_TYPE_INVALID", f"{path}.entity_type", "event entity_type must be 'event'")
        event_id = _identifier(event.get("event_id"), f"{path}.event_id", issues, "EVENT_ID_INVALID")
        if event_id is None:
            event_id = f"INVALID-EVENT-{index}"
        elif event_id in ids:
            _add_issue(issues, "BLOCKED", "EVENT_ID_DUPLICATE", f"{path}.event_id", "event identifiers must be unique")
        ids.add(event_id)
        event_type = event.get("event_type")
        if event_type not in _EVENT_TYPES:
            _add_issue(issues, "BLOCKED", "EVENT_TYPE_INVALID", f"{path}.event_type", "unrecognized D1 event type")
        kind = event.get("event_kind")
        if kind not in _EVENT_KINDS:
            _add_issue(issues, "BLOCKED", "EVENT_KIND_INVALID", f"{path}.event_kind", "unrecognized D1 event kind")
            kind = "OTHER"
        state = event.get("state")
        if state not in _EVENT_STATES:
            _add_issue(issues, "BLOCKED", "EVENT_STATE_INVALID", f"{path}.state", "unrecognized D1 event state")
            state = "UNKNOWN"
        elif state == "UNKNOWN":
            _add_issue(issues, "UNKNOWN", "EVENT_STATE_UNKNOWN", f"{path}.state", "unknown event state cannot prove a transition")
        required_event_type = _required_event_type(kind)
        if required_event_type is not None and event_type != required_event_type:
            _add_issue(
                issues,
                "FAIL",
                "EVENT_TYPE_INCOMPATIBLE",
                f"{path}.event_type",
                f"{kind} requires event_type {required_event_type}",
            )
        occurred = _rfc3339_seconds(event.get("occurred_at"), f"{path}.occurred_at", issues)
        offset, offset_state = _time_quantity(
            event.get("time_offset"),
            f"{path}.time_offset",
            issues,
            unknown_code="EVENT_TIME_UNKNOWN",
        )
        subject_refs = _id_refs(event.get("subject_refs"), f"{path}.subject_refs", issues)
        _validate_measurements(event.get("measurements"), f"{path}.measurements", issues)
        provenance = _provenance(event.get("provenance"), f"{path}.provenance", issues)
        refs = _id_refs(event.get("source_refs"), f"{path}.source_refs", issues)
        _gate_refs(refs, f"{path}.source_refs", sources, issues)
        if provenance in _TRUSTED_PROVENANCE:
            for ref_index, source_id in enumerate(refs):
                source = sources.get(source_id)
                if source is not None and source.get("classification") != provenance:
                    _add_issue(
                        issues,
                        "BLOCKED",
                        "EVENT_SOURCE_CLASSIFICATION_MISMATCH",
                        f"{path}.source_refs[{ref_index}]",
                        "trusted event provenance must match every referenced source classification",
                    )
        events.append(
            _Event(
                input_index=index,
                event_id=event_id,
                event_type=event_type if isinstance(event_type, str) else "INVALID",
                event_kind=kind,
                event_state=state,
                subject_refs=subject_refs,
                offset=offset,
                offset_state=offset_state,
                occurred_seconds=occurred,
                provenance=provenance,
                source_refs=refs,
            )
        )
    return events


def _has_severity(issues: Sequence[_Issue], severity: str) -> bool:
    return any(issue.severity == severity for issue in issues)


def _expected_output(rule: Optional[_Rule]) -> Optional[Dict[str, Any]]:
    if rule is None:
        return None
    return {
        "event_kind": rule.event_kind,
        "state": rule.event_state,
        "subject_refs": list(rule.event_subject_refs),
    }


def _required_event_type(event_kind: str) -> Optional[str]:
    if event_kind in {"VIN_VALID", "EN", "PGOOD", "RESET"}:
        return "SIGNAL_TRANSITION"
    if event_kind == "POWER_OFF":
        return "POWER_STATE"
    return None


def _event_type_matches_profile(event: _Event) -> bool:
    required = _required_event_type(event.event_kind)
    return required is None or event.event_type == required


def _timing_output(
    rule: Optional[_Rule],
    anchor_event_id: Optional[str],
    measured: Optional[Decimal],
    violated_bound: Optional[str],
) -> Dict[str, Any]:
    return {
        "anchor_event_id": anchor_event_id,
        "expected": {
            "minimum": _quantity_output(rule.minimum if rule else None),
            "maximum": _quantity_output(rule.maximum if rule else None),
        },
        "measured": _quantity_output(measured),
        "violated_bound": violated_bound,
    }


def _trace_record(
    *,
    processing_index: int,
    event: Optional[_Event],
    from_state: str,
    to_state: str,
    rule: Optional[_Rule],
    outcome: str,
    reason_code: str,
    anchor_event_id: Optional[str],
    measured: Optional[Decimal],
    violated_bound: Optional[str],
    rule_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "processing_index": processing_index,
        "input_index": event.input_index if event else None,
        "event_id": event.event_id if event else None,
        "event_type": event.event_type if event else None,
        "event_kind": event.event_kind if event else None,
        "event_state": event.event_state if event else None,
        "subject_refs": list(event.subject_refs) if event else None,
        "time_offset": _quantity_output(event.offset if event else None),
        "from_state": from_state,
        "to_state": to_state,
        "expected_event": _expected_output(rule),
        "rule_id": rule.rule_id if rule else (rule_id_override or "REP02.SEQ.ORDER.TERMINAL"),
        "outcome": outcome,
        "reason_code": reason_code,
        "timing": _timing_output(rule, anchor_event_id, measured, violated_bound),
    }


def _canonical_event_order(events: Sequence[_Event], simultaneous_policy: str) -> List[_Event]:
    def key(event: _Event) -> Tuple[int, Decimal, int, str, str, str]:
        known_rank = 0 if event.offset is not None else 1
        offset = event.offset if event.offset is not None else Decimal(0)
        transition_rank = _TRANSITION_RANK.get((event.event_kind, event.event_state), 100)
        if simultaneous_policy != "ALLOW_ZERO_MIN_EXPECTED_ORDER":
            transition_rank = 0
        return (known_rank, offset, transition_rank, event.event_kind, event.event_state, event.event_id)

    return sorted(events, key=key)


def _simultaneous_groups(events: Sequence[_Event]) -> Dict[Decimal, List[_Event]]:
    groups: Dict[Decimal, List[_Event]] = {}
    for event in events:
        if event.offset is not None:
            groups.setdefault(event.offset, []).append(event)
    return groups


def _binding_matches(event: _Event, rule: _Rule) -> bool:
    return frozenset(event.subject_refs) == frozenset(rule.event_subject_refs)


def _event_matches_rule(event: _Event, rule: _Rule) -> bool:
    return (
        event.event_kind == rule.event_kind
        and event.event_state == rule.event_state
        and _binding_matches(event, rule)
    )


def _could_supply_rule(
    event: _Event,
    rule: _Rule,
    anchor_offset: Decimal,
    *,
    before_event_offset: Optional[Decimal] = None,
) -> bool:
    """Return whether an indeterminate event could be the current transition."""

    if not (
        event.event_kind == rule.event_kind
        and event.event_state in {rule.event_state, "UNKNOWN"}
        and _binding_matches(event, rule)
        and (event.offset is None or event.event_state == "UNKNOWN")
    ):
        return False
    if event.offset is None:
        return True
    if event.offset < anchor_offset:
        return False
    if before_event_offset is not None and event.offset > before_event_offset:
        return False
    measured = _exact_subtract(event.offset, anchor_offset)
    if rule.minimum is not None and measured < rule.minimum:
        return False
    if rule.maximum is not None and measured > rule.maximum:
        return False
    return True


def _matching_rule_index(event: _Event, rules: Sequence[_Rule]) -> Optional[int]:
    for index, rule in enumerate(rules):
        if _event_matches_rule(event, rule):
            return index
    return None


def _allowed_simultaneous_chain(group: Sequence[_Event], rules: Sequence[_Rule]) -> bool:
    if not group:
        return True
    expected_index = _TRANSITION_RANK.get((group[0].event_kind, group[0].event_state), -1)
    if expected_index < 0:
        return False
    for position, event in enumerate(group):
        if event.event_kind == "POWER_OFF" and event.event_state == "ENTERED":
            return len(group) == 1
        if expected_index >= len(rules):
            return False
        rule = rules[expected_index]
        if not _event_matches_rule(event, rule):
            return False
        if position > 0 and rule.minimum != Decimal(0):
            return False
        expected_index += 1
    return True


def _evaluate_fsm(
    events: Sequence[_Event],
    rules: Sequence[_Rule],
    power_off_rule: _Rule,
    observation_end: Optional[Decimal],
    simultaneous_policy: str,
    scenario_kind: str,
    issues: MutableSequence[_Issue],
) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    ordered = _canonical_event_order(events, simultaneous_policy)
    groups = _simultaneous_groups(ordered)
    rejected_offsets: set[Decimal] = set()
    state = "WAIT_VIN_VALID"
    rule_index = 0
    anchor_offset = Decimal(0)
    anchor_event_id: Optional[str] = None
    trace: List[Dict[str, Any]] = []
    processing_index = 0
    terminal_pass_reason: Optional[str] = None

    for offset in sorted(groups):
        group = groups[offset]
        if len(group) <= 1:
            continue
        if simultaneous_policy == "REJECT" or not _allowed_simultaneous_chain(group, rules):
            rejected_offsets.add(offset)
            _add_issue(
                issues,
                "FAIL",
                "SIMULTANEOUS_EVENT_CONFLICT",
                "$.events",
                "same-time transitions cannot establish causal order under the selected policy",
            )

    for event in ordered:
        # A dedicated POWER_OFF scenario has its own one-event profile.  Kind
        # and known polarity are evaluated before time uncertainty so an
        # unrelated or definitely wrong event cannot become UNKNOWN merely
        # because its timestamp is unknown.
        if scenario_kind == "POWER_OFF":
            if event.event_kind != "POWER_OFF":
                _add_issue(
                    issues,
                    "FAIL",
                    "UNEXPECTED_EVENT_IN_POWER_OFF_SCENARIO",
                    f"$.events[{event.input_index}]",
                    "a POWER_OFF scenario accepts only one POWER_OFF/ENTERED event",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="UNEXPECTED_EVENT_IN_POWER_OFF_SCENARIO",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="ORDER",
                    )
                )
                processing_index += 1
                continue
            if not _binding_matches(event, power_off_rule):
                _add_issue(
                    issues,
                    "FAIL",
                    "POWER_OFF_SUBJECT_BINDING_MISMATCH",
                    f"$.events[{event.input_index}].subject_refs",
                    "POWER_OFF subject_refs must exactly match power_off_rule",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="POWER_OFF_SUBJECT_BINDING_MISMATCH",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="SUBJECT",
                    )
                )
                processing_index += 1
                continue
            if event.event_state not in {"ENTERED", "UNKNOWN"}:
                _add_issue(
                    issues,
                    "FAIL",
                    "POWER_OFF_POLARITY_INVALID",
                    f"$.events[{event.input_index}].state",
                    "POWER_OFF must use ENTERED",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="POWER_OFF_POLARITY_INVALID",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="STATE",
                    )
                )
                processing_index += 1
                continue
            if event.event_state == "UNKNOWN":
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="UNKNOWN",
                        reason_code="EVENT_STATE_UNKNOWN",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="STATE",
                    )
                )
                processing_index += 1
                continue
            # Once POWERED_OFF is established, another semantically identical
            # POWER_OFF/ENTERED event is a definite duplicate even when its
            # timestamp is unknown.  Time uncertainty cannot make the already
            # known kind, polarity, and exact subject binding indeterminate.
            if state == "POWERED_OFF":
                _add_issue(
                    issues,
                    "FAIL",
                    "EVENT_AFTER_POWER_OFF",
                    f"$.events[{event.input_index}]",
                    "events after terminal POWER_OFF are not accepted",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="EVENT_AFTER_POWER_OFF",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="DUPLICATE",
                    )
                )
                processing_index += 1
                continue
            if event.offset is None:
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="UNKNOWN",
                        reason_code="EVENT_TIME_UNKNOWN",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="TIME",
                    )
                )
                processing_index += 1
                continue
            if event.offset in rejected_offsets:
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="SIMULTANEOUS_EVENT_CONFLICT",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="SIMULTANEOUS",
                    )
                )
                processing_index += 1
                continue
            previous = state
            state = "POWERED_OFF"
            type_matches = _event_type_matches_profile(event)
            if type_matches:
                terminal_pass_reason = "POWER_OFF_TERMINATED"
                outcome = "PASS"
                reason_code = "POWER_OFF_TERMINATED"
                violated_bound = None
            else:
                outcome = "FAIL"
                reason_code = "EVENT_TYPE_INCOMPATIBLE"
                violated_bound = "EVENT_TYPE"
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=previous,
                    to_state=state,
                    rule=power_off_rule,
                    outcome=outcome,
                    reason_code=reason_code,
                    anchor_event_id=anchor_event_id,
                    measured=None,
                    violated_bound=violated_bound,
                )
            )
            processing_index += 1
            continue

        if event.event_kind == "POWER_OFF":
            if not _binding_matches(event, power_off_rule):
                _add_issue(
                    issues,
                    "FAIL",
                    "POWER_OFF_SUBJECT_BINDING_MISMATCH",
                    f"$.events[{event.input_index}].subject_refs",
                    "POWER_OFF subject_refs must exactly match power_off_rule",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="POWER_OFF_SUBJECT_BINDING_MISMATCH",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="SUBJECT",
                    )
                )
                processing_index += 1
                continue
            if event.event_state == "UNKNOWN":
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="UNKNOWN",
                        reason_code="EVENT_STATE_UNKNOWN",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="STATE",
                    )
                )
                processing_index += 1
                continue
            if event.event_state != "ENTERED":
                _add_issue(issues, "FAIL", "POWER_OFF_POLARITY_INVALID", f"$.events[{event.input_index}].state", "POWER_OFF must use ENTERED")
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="POWER_OFF_POLARITY_INVALID",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound="STATE",
                    )
                )
            else:
                previous = state
                state = "POWERED_OFF"
                _add_issue(
                    issues,
                    "FAIL",
                    "UNEXPECTED_POWER_OFF",
                    f"$.events[{event.input_index}]",
                    "POWER_OFF is not a successful terminal event in a STARTUP scenario",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=previous,
                        to_state=state,
                        rule=power_off_rule,
                        outcome="FAIL",
                        reason_code="UNEXPECTED_POWER_OFF",
                        anchor_event_id=anchor_event_id,
                        measured=None,
                        violated_bound=None,
                    )
                )
            processing_index += 1
            continue

        if event.offset is None or event.event_state == "UNKNOWN":
            rule = rules[rule_index] if rule_index < len(rules) else None
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=state,
                    to_state=state,
                    rule=rule,
                    outcome="UNKNOWN",
                    reason_code="EVENT_TIME_UNKNOWN" if event.offset is None else "EVENT_STATE_UNKNOWN",
                    anchor_event_id=anchor_event_id,
                    measured=None,
                    violated_bound="TIME" if event.offset is None else "STATE",
                )
            )
            processing_index += 1
            continue

        if event.offset in rejected_offsets:
            rule = rules[rule_index] if rule_index < len(rules) else None
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=state,
                    to_state=state,
                    rule=rule,
                    outcome="FAIL",
                    reason_code="SIMULTANEOUS_EVENT_CONFLICT",
                    anchor_event_id=anchor_event_id,
                    measured=_exact_subtract(event.offset, anchor_offset),
                    violated_bound="SIMULTANEOUS",
                )
            )
            processing_index += 1
            continue

        if state == "POWERED_OFF":
            _add_issue(issues, "FAIL", "EVENT_AFTER_POWER_OFF", f"$.events[{event.input_index}]", "events after terminal POWER_OFF are not accepted")
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=state,
                    to_state=state,
                    rule=power_off_rule,
                    outcome="FAIL",
                    reason_code="EVENT_AFTER_POWER_OFF",
                    anchor_event_id=anchor_event_id,
                    measured=None,
                    violated_bound="ORDER",
                )
            )
            processing_index += 1
            continue

        rule = rules[rule_index] if rule_index < len(rules) else None
        if rule is None:
            prior_rule_index = _matching_rule_index(event, rules)
            prior_rule = rules[prior_rule_index] if prior_rule_index is not None else None
            code = "DUPLICATE_TRANSITION" if prior_rule is not None else "EVENT_SEQUENCE_VIOLATION"
            _add_issue(issues, "FAIL", code, f"$.events[{event.input_index}]", "event is not valid after the terminal RUNNING state")
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=state,
                    to_state=state,
                    rule=prior_rule,
                    outcome="FAIL",
                    reason_code=code,
                    anchor_event_id=anchor_event_id,
                    measured=None,
                    violated_bound="DUPLICATE" if prior_rule is not None else "ORDER",
                    rule_id_override="REP02.SEQ.ORDER.TERMINAL",
                )
            )
            processing_index += 1
            continue

        if not _event_matches_rule(event, rule):
            prior_rank = _matching_rule_index(event, rules)
            indeterminate_can_supply_current = any(
                _could_supply_rule(
                    candidate,
                    rule,
                    anchor_offset,
                    before_event_offset=event.offset,
                )
                for candidate in events
            )
            transition_rank = _TRANSITION_RANK.get((event.event_kind, event.event_state))
            if (
                indeterminate_can_supply_current
                and transition_rank is not None
                and transition_rank > rule_index
            ):
                code = "EVENT_ORDER_INDETERMINATE"
                bound = "UNKNOWN"
                _add_issue(
                    issues,
                    "UNKNOWN",
                    code,
                    f"$.events[{event.input_index}]",
                    "an indeterminate event may supply the preceding transition",
                )
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=event,
                        from_state=state,
                        to_state=state,
                        rule=rule,
                        outcome="UNKNOWN",
                        reason_code=code,
                        anchor_event_id=anchor_event_id,
                        measured=_exact_subtract(event.offset, anchor_offset),
                        violated_bound=bound,
                    )
                )
                processing_index += 1
                continue
            if prior_rank is not None and prior_rank < rule_index:
                code = "DUPLICATE_TRANSITION"
                bound = "DUPLICATE"
            elif (
                event.event_kind == rule.event_kind
                and event.event_state == rule.event_state
                and not _binding_matches(event, rule)
            ):
                code = "SUBJECT_BINDING_MISMATCH"
                bound = "SUBJECT"
            elif event.event_kind == rule.event_kind:
                code = "WRONG_EVENT_POLARITY"
                bound = "STATE"
            else:
                code = "EVENT_SEQUENCE_VIOLATION"
                bound = "ORDER"
            _add_issue(issues, "FAIL", code, f"$.events[{event.input_index}]", "event does not match the transition expected in the current FSM state")
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=event,
                    from_state=state,
                    to_state=state,
                    rule=rule,
                    outcome="FAIL",
                    reason_code=code,
                    anchor_event_id=anchor_event_id,
                    measured=_exact_subtract(event.offset, anchor_offset),
                    violated_bound=bound,
                )
            )
            processing_index += 1
            continue

        measured = _exact_subtract(event.offset, anchor_offset)
        reason_code = "TRANSITION_WITHIN_WINDOW"
        outcome = "PASS"
        violated_bound: Optional[str] = None
        if rule.minimum is not None and measured < rule.minimum:
            reason_code = "TIMING_BELOW_MINIMUM"
            outcome = "FAIL"
            violated_bound = "MINIMUM"
            _add_issue(issues, "FAIL", reason_code, f"$.events[{event.input_index}].time_offset", "measured transition delay is below the closed minimum")
        elif rule.maximum is not None and measured > rule.maximum:
            reason_code = "TIMING_ABOVE_MAXIMUM"
            outcome = "FAIL"
            violated_bound = "MAXIMUM"
            _add_issue(issues, "FAIL", reason_code, f"$.events[{event.input_index}].time_offset", "measured transition delay is above the closed maximum")
        elif rule.minimum is None or rule.maximum is None:
            reason_code = "TIMING_WINDOW_UNKNOWN"
            outcome = "UNKNOWN"
            violated_bound = "UNKNOWN"
        if outcome != "FAIL" and not _event_type_matches_profile(event):
            reason_code = "EVENT_TYPE_INCOMPATIBLE"
            outcome = "FAIL"
            violated_bound = "EVENT_TYPE"
        previous_state = state
        state = _EXPECTED_TRANSITIONS[rule_index][3]
        trace.append(
            _trace_record(
                processing_index=processing_index,
                event=event,
                from_state=previous_state,
                to_state=state,
                rule=rule,
                outcome=outcome,
                reason_code=reason_code,
                anchor_event_id=anchor_event_id,
                measured=measured,
                violated_bound=violated_bound,
            )
        )
        anchor_offset = event.offset
        anchor_event_id = event.event_id
        rule_index += 1
        if rule_index == len(rules):
            terminal_pass_reason = "SEQUENCE_COMPLETE"
        processing_index += 1

    if scenario_kind == "POWER_OFF" and state != "POWERED_OFF":
        _add_issue(
            issues,
            "UNKNOWN",
            "POWER_OFF_EVENT_MISSING",
            "$.events",
            "the dedicated POWER_OFF scenario has no POWER_OFF/ENTERED event",
        )
        trace.append(
            _trace_record(
                processing_index=processing_index,
                event=None,
                from_state=state,
                to_state=state,
                rule=power_off_rule,
                outcome="UNKNOWN",
                reason_code="POWER_OFF_EVENT_MISSING",
                anchor_event_id=anchor_event_id,
                measured=None,
                violated_bound="MISSING",
            )
        )
    elif state not in {"RUNNING", "POWERED_OFF"} and rule_index < len(rules):
        rule = rules[rule_index]
        relevant_indeterminate = [
            event
            for event in events
            if _could_supply_rule(event, rule, anchor_offset)
        ]
        if relevant_indeterminate:
            code = (
                "EVENT_TIME_UNKNOWN"
                if any(event.offset is None for event in relevant_indeterminate)
                else "EVENT_STATE_UNKNOWN"
            )
            _add_issue(issues, "UNKNOWN", code, "$.events", "an indeterminate event may affect the missing-transition decision")
        elif rule.maximum is None or observation_end is None:
            _add_issue(issues, "UNKNOWN", "TIMING_WINDOW_UNKNOWN" if rule.maximum is None else "OBSERVATION_END_UNKNOWN", "$.observation_end_offset", "a missing event cannot be resolved without a known deadline and observation horizon")
            trace.append(
                _trace_record(
                    processing_index=processing_index,
                    event=None,
                    from_state=state,
                    to_state=state,
                    rule=rule,
                    outcome="UNKNOWN",
                    reason_code="TIMING_WINDOW_UNKNOWN" if rule.maximum is None else "OBSERVATION_END_UNKNOWN",
                    anchor_event_id=anchor_event_id,
                    measured=None,
                    violated_bound="UNKNOWN",
                )
            )
        else:
            deadline = _exact_add(anchor_offset, rule.maximum)
            elapsed = _exact_subtract(observation_end, anchor_offset)
            if observation_end > deadline:
                _add_issue(issues, "FAIL", "REQUIRED_EVENT_TIMEOUT", "$.observation_end_offset", "required event is absent after the inclusive maximum deadline")
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=None,
                        from_state=state,
                        to_state=state,
                        rule=rule,
                        outcome="FAIL",
                        reason_code="REQUIRED_EVENT_TIMEOUT",
                        anchor_event_id=anchor_event_id,
                        measured=elapsed,
                        violated_bound="MAXIMUM",
                    )
                )
            else:
                _add_issue(issues, "UNKNOWN", "OBSERVATION_WINDOW_INCOMPLETE", "$.observation_end_offset", "observation horizon has not passed the inclusive maximum deadline")
                trace.append(
                    _trace_record(
                        processing_index=processing_index,
                        event=None,
                        from_state=state,
                        to_state=state,
                        rule=rule,
                        outcome="UNKNOWN",
                        reason_code="OBSERVATION_WINDOW_INCOMPLETE",
                        anchor_event_id=anchor_event_id,
                        measured=elapsed,
                        violated_bound="NOT_YET_EXCEEDED",
                    )
                )
    return state, trace, terminal_pass_reason


def _source_refs_output(
    rules: Sequence[_Rule],
    power_off_rule: _Rule,
    events: Sequence[_Event],
    observation_refs: Sequence[str],
    sources: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    referenced = sorted(
        {ref for rule in rules for ref in rule.source_refs}
        | set(power_off_rule.source_refs)
        | {ref for event in events for ref in event.source_refs}
        | set(observation_refs)
    )
    result: List[Dict[str, Any]] = []
    for source_id in referenced:
        source = sources.get(source_id)
        if source is None:
            continue
        content_hash = source.get("content_hash")
        version = source.get("version")
        safe_version = (
            version
            if isinstance(version, str)
            and bool(version.strip())
            and not _is_floating_version(version)
            else None
        )
        hash_value = content_hash.get("value") if isinstance(content_hash, dict) else None
        safe_hash = (
            hash_value
            if isinstance(hash_value, str) and _SHA256_RE.fullmatch(hash_value) is not None
            else None
        )
        result.append(
            {
                "source_id": source_id,
                "version": safe_version,
                "content_hash": safe_hash,
            }
        )
    return result


def _status(issues: Sequence[_Issue]) -> str:
    for severity in ("BLOCKED", "FAIL", "UNKNOWN"):
        if _has_severity(issues, severity):
            return severity
    return "PASS"


def _reason_codes(issues: Sequence[_Issue], pass_reason: Optional[str]) -> List[str]:
    status = _status(issues)
    if status == "PASS":
        return [pass_reason or "SEQUENCE_COMPLETE"]
    return sorted({issue.code for issue in issues if issue.severity == status})


def _base_result(input_hash: Optional[str]) -> Dict[str, Any]:
    return {
        "fsm_version": FSM_VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "tool_name": TOOL_NAME,
        "tool_version": ENGINE_VERSION,
        "package_version": PACKAGE_VERSION,
        "status": "BLOCKED",
        "reason_code": "INPUT_VALIDATION_BLOCKED",
        "reason_codes": ["INPUT_VALIDATION_BLOCKED"],
        "input_hash": input_hash,
        "scenario_id": None,
        "scenario_kind": None,
        "observation": {
            "end_offset": _quantity_output(None),
            "end_at": None,
            "provenance": None,
            "source_refs": [],
        },
        "source_refs": [],
        "policy": {
            "sorting_policy": SORTING_POLICY,
            "simultaneous_policy": None,
            "timing_interval": "CLOSED_MINIMUM_AND_MAXIMUM",
            "decision_clock": "TIME_OFFSET_DECIMAL_SECONDS",
            "timestamp_role": "AUDIT_COORDINATE_ONLY",
            "missing_event_deadline": "UNKNOWN_AT_OR_BEFORE_MAXIMUM_FAIL_AFTER_MAXIMUM",
            "power_off_policy": "POWER_OFF_ENTERED_TERMINATES_WITHOUT_ASSUMED_SHUTDOWN_ORDER",
        },
        "final_state": "WAIT_VIN_VALID",
        "trace": [],
        "issues": [],
        "limitations": [
            "SYNTHETIC_TIMING_ONLY",
            "NO_HARDWARE_VALIDATION_CLAIM",
            "GAP_03_OPEN",
            "POWER_OFF_SIGNAL_ORDER_NOT_INFERRED",
            "ARRAY_ORDER_IS_INPUT_HASH_SIGNIFICANT",
        ],
    }


def _evaluate(document: Any, input_hash: Optional[str]) -> Dict[str, Any]:
    issues: List[_Issue] = []
    result = _base_result(input_hash)
    if not isinstance(document, dict):
        _add_issue(issues, "BLOCKED", "TYPE_OBJECT", "$", "sequencing input must be an object")
        result["issues"] = [issue.as_dict() for issue in issues]
        result["reason_codes"] = ["TYPE_OBJECT"]
        result["reason_code"] = "TYPE_OBJECT"
        return result

    _reject_unexpected_fields(document, _ROOT_FIELDS, "$", issues)
    _required_members(document, _ROOT_FIELDS, "$", issues)
    if document.get("schema_version") != "1.0.0":
        _add_issue(issues, "BLOCKED", "SCHEMA_VERSION_INVALID", "$.schema_version", "schema_version must be '1.0.0'")
    _identifier(document.get("scenario_id"), "$.scenario_id", issues, "SCENARIO_ID_INVALID")
    scenario_kind = document.get("scenario_kind")
    if scenario_kind not in {"STARTUP", "POWER_OFF"}:
        _add_issue(
            issues,
            "BLOCKED",
            "SCENARIO_KIND_INVALID",
            "$.scenario_kind",
            "scenario_kind must be STARTUP or POWER_OFF",
        )
        scenario_kind = "STARTUP"
    result["scenario_id"] = document.get("scenario_id") if isinstance(document.get("scenario_id"), str) else None
    result["scenario_kind"] = scenario_kind
    if document.get("sorting_policy") != SORTING_POLICY:
        _add_issue(issues, "BLOCKED", "SORTING_POLICY_INVALID", "$.sorting_policy", f"sorting_policy must be {SORTING_POLICY}")
    simultaneous_policy = document.get("simultaneous_policy")
    if simultaneous_policy not in SIMULTANEOUS_POLICIES:
        _add_issue(issues, "BLOCKED", "SIMULTANEOUS_POLICY_INVALID", "$.simultaneous_policy", "unrecognized simultaneous-event policy")
        simultaneous_policy = "REJECT"
    result["policy"]["simultaneous_policy"] = simultaneous_policy

    sources = _validate_sources(document.get("sources"), issues)
    rules = _validate_rules(document.get("timing_rules"), sources, issues)
    power_off_rule = _validate_power_off_rule(
        document.get("power_off_rule"),
        rules,
        sources,
        issues,
    )
    events = _validate_events(document.get("events"), sources, issues)
    observation_end, _ = _time_quantity(
        document.get("observation_end_offset"),
        "$.observation_end_offset",
        issues,
        unknown_code="OBSERVATION_END_UNKNOWN",
    )
    observation_end_at = _rfc3339_seconds(
        document.get("observation_end_at"),
        "$.observation_end_at",
        issues,
    )
    observation_provenance = _provenance(
        document.get("observation_provenance"),
        "$.observation_provenance",
        issues,
    )
    observation_refs = _id_refs(
        document.get("observation_source_refs"),
        "$.observation_source_refs",
        issues,
    )
    _gate_refs(observation_refs, "$.observation_source_refs", sources, issues)
    if observation_provenance in _TRUSTED_PROVENANCE:
        for ref_index, source_id in enumerate(observation_refs):
            source = sources.get(source_id)
            if source is not None and source.get("classification") != observation_provenance:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "OBSERVATION_SOURCE_CLASSIFICATION_MISMATCH",
                    f"$.observation_source_refs[{ref_index}]",
                    "trusted observation provenance must match every referenced source classification",
                )
    result["observation"] = {
        "end_offset": _quantity_output(observation_end),
        "end_at": (
            document.get("observation_end_at")
            if observation_end_at is not None and isinstance(document.get("observation_end_at"), str)
            else None
        ),
        "provenance": (
            observation_provenance
            if observation_provenance in _PROVENANCE
            else None
        ),
        "source_refs": list(observation_refs),
    }

    known_origins: List[Tuple[int, Decimal]] = []
    for event in events:
        if event.offset is not None and event.occurred_seconds is not None:
            known_origins.append(
                (event.input_index, _exact_subtract(event.occurred_seconds, event.offset))
            )
        if observation_end is not None and event.offset is not None and event.offset > observation_end:
            _add_issue(issues, "BLOCKED", "EVENT_AFTER_OBSERVATION_END", f"$.events[{event.input_index}].time_offset", "event lies beyond observation_end_offset")
    if known_origins:
        reference_origin = known_origins[0][1]
        for event_index, origin in known_origins[1:]:
            if origin != reference_origin:
                _add_issue(issues, "BLOCKED", "TIME_COORDINATE_CONFLICT", f"$.events[{event_index}].occurred_at", "occurred_at and time_offset do not share a common scenario origin")
        if observation_end is not None and observation_end_at is not None:
            expected_observation_at = _exact_add(reference_origin, observation_end)
            if observation_end_at != expected_observation_at:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "OBSERVATION_TIME_COORDINATE_CONFLICT",
                    "$.observation_end_at",
                    "observation_end_at must equal common_origin + observation_end_offset",
                )

    final_state = "WAIT_VIN_VALID"
    trace: List[Dict[str, Any]] = []
    pass_reason: Optional[str] = None
    if not _has_severity(issues, "BLOCKED") and len(rules) == len(_EXPECTED_TRANSITIONS):
        final_state, trace, pass_reason = _evaluate_fsm(
            events,
            rules,
            power_off_rule,
            observation_end,
            simultaneous_policy,
            scenario_kind,
            issues,
        )

    status = _status(issues)
    codes = _reason_codes(issues, pass_reason)
    result.update(
        {
            "status": status,
            "reason_code": codes[0],
            "reason_codes": codes,
            "source_refs": _source_refs_output(
                rules,
                power_off_rule,
                events,
                observation_refs,
                sources,
            ),
            "final_state": final_state,
            "trace": trace,
            "issues": [
                issue.as_dict()
                for issue in sorted(issues, key=lambda item: (item.severity, item.code, item.path, item.message))
            ],
        }
    )
    return result


def evaluate_sequencing(document: Any) -> Dict[str, Any]:
    """Evaluate a JSON-compatible sequencing input without mutating it.

    Malformed, non-finite, non-JSON, or pathologically deep values are returned
    as deterministic ``BLOCKED`` results rather than escaping as exceptions.
    """

    try:
        input_hash: Optional[str] = sha256_hexdigest(document)
    except (RecursionError, TypeError, ValueError):
        input_hash = None
    try:
        result = _evaluate(document, input_hash)
    except (ArithmeticError, OverflowError, RecursionError, TypeError, ValueError):
        result = _base_result(input_hash)
        issue = _Issue(
            severity="BLOCKED",
            code="INPUT_VALIDATION_BLOCKED",
            path="$",
            message="input could not be safely evaluated under the sequencing contract",
        )
        result["issues"] = [issue.as_dict()]
    if input_hash is None:
        existing = {item["code"] for item in result["issues"]}
        if "INPUT_HASH_UNCOMPUTABLE" not in existing:
            result["issues"].append(
                _Issue(
                    severity="BLOCKED",
                    code="INPUT_HASH_UNCOMPUTABLE",
                    path="$",
                    message="canonical hash could not be computed for the original input",
                ).as_dict()
            )
        result["status"] = "BLOCKED"
        result["reason_codes"] = sorted(
            {item["code"] for item in result["issues"] if item["severity"] == "BLOCKED"}
        )
        result["reason_code"] = result["reason_codes"][0]
        result["input_hash"] = None
    return result
