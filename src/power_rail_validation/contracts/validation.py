"""Dependency-free structural and D1 domain validation.

JSON Schema files are the exchange-format authority.  This module implements
the safety-relevant D1 checks with the Python standard library so callers do
not need a schema-validator package merely to reject unsafe handoffs.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Sequence

from .canonical import configuration_sha256 as compute_configuration_sha256


PROVENANCE_VALUES = frozenset(
    {
        "SOURCE_EXPLICIT",
        "DERIVED",
        "ENGINEERING_RULE",
        "ASSUMPTION",
        "UNKNOWN",
        "CONFLICT",
    }
)
STATUS_VALUES = frozenset({"PASS", "FAIL", "UNKNOWN", "BLOCKED", "NOT_RUN"})
ENTITY_TYPES = frozenset({"rail", "load", "regulator", "source", "event", "finding"})
LIMIT_STATES = frozenset({"KNOWN", "MISSING", "UNKNOWN", "CONFLICT", "NOT_APPLICABLE"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ValidationIssue:
    """A stable, machine-readable validation failure."""

    path: str
    code: str
    message: str


class ContractValidationError(ValueError):
    """Raised by :func:`assert_valid` when one or more issues exist."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = tuple(issues)
        summary = "; ".join(f"{item.path} [{item.code}] {item.message}" for item in issues)
        super().__init__(summary)


def _issue(issues: MutableSequence[ValidationIssue], path: str, code: str, message: str) -> None:
    issues.append(ValidationIssue(path=path, code=code, message=message))


def _reject_non_finite_json_number(token: str) -> None:
    """Reject Python's non-standard JSON constants (NaN and infinities)."""

    raise ValueError(f"non-finite JSON numeric literal {token!r} is not permitted")


class _DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


def _strict_json_object(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _DuplicateJsonKeyError(f"duplicate JSON object key {name!r} is not permitted")
        result[name] = value
    return result


def _require_fields(
    value: Mapping[str, Any], required: Iterable[str], path: str, issues: MutableSequence[ValidationIssue]
) -> None:
    for name in required:
        if name not in value:
            _issue(issues, f"{path}.{name}", "MISSING_FIELD", "required field is absent")


def _reject_unexpected_fields(
    value: Mapping[str, Any], allowed: Iterable[str], path: str, issues: MutableSequence[ValidationIssue]
) -> None:
    allowed_names = frozenset(allowed)
    for name in value:
        if name not in allowed_names:
            _issue(issues, f"{path}.{name}", "UNEXPECTED_FIELD", "field is outside the D1 contract")


def _reject_non_finite_values(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _issue(issues, path, "NON_FINITE_NUMBER", "contract numbers must be finite")
    elif isinstance(value, dict):
        for name, child in value.items():
            _reject_non_finite_values(child, f"{path}.{name}", issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_non_finite_values(child, f"{path}[{index}]", issues)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _expect_string(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> bool:
    if not isinstance(value, str) or not value:
        _issue(issues, path, "TYPE_STRING", "expected a non-empty string")
        return False
    return True


def _expect_list(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> bool:
    if not isinstance(value, list):
        _issue(issues, path, "TYPE_ARRAY", "expected an array")
        return False
    return True


def _expect_mapping(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> bool:
    if not isinstance(value, dict):
        _issue(issues, path, "TYPE_OBJECT", "expected an object")
        return False
    return True


def _expect_enum(
    value: Any,
    allowed: Iterable[str],
    path: str,
    issues: MutableSequence[ValidationIssue],
    code: str = "ENUM",
) -> bool:
    allowed_values = frozenset(allowed)
    try:
        accepted = value in allowed_values
    except TypeError:
        accepted = False
    if not accepted:
        _issue(issues, path, code, f"expected one of {sorted(allowed_values)}")
        return False
    return True


def _expect_sha256(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> bool:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _issue(issues, path, "SHA256_FORMAT", "expected 64 lowercase hexadecimal characters")
        return False
    return True


def _expect_timestamp(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> bool:
    if not _expect_string(value, path, issues):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _issue(issues, path, "DATETIME_FORMAT", "expected an RFC 3339 timestamp")
        return False
    if parsed.tzinfo is None:
        _issue(issues, path, "DATETIME_TIMEZONE", "timestamp must include a timezone")
        return False
    return True


def _validate_quantity(
    value: Any,
    path: str,
    issues: MutableSequence[ValidationIssue],
    allowed_units: Iterable[str] = ("V", "A", "Hz", "s"),
) -> None:
    if not isinstance(value, dict):
        _issue(
            issues,
            path,
            "QUANTITY_OBJECT_REQUIRED",
            "physical values must be {state, value, unit}; unitless bare numbers are forbidden",
        )
        return
    _require_fields(value, ("state", "value", "unit"), path, issues)
    _reject_unexpected_fields(value, ("state", "value", "unit"), path, issues)
    state = value.get("state")
    _expect_enum(
        state,
        {"KNOWN", "UNKNOWN"},
        f"{path}.state",
        issues,
        "QUANTITY_STATE",
    )
    unit = value.get("unit")
    if _expect_string(unit, f"{path}.unit", issues) and unit not in frozenset(allowed_units):
        _issue(
            issues,
            f"{path}.unit",
            "UNIT_ENUM",
            f"unit {unit!r} is not allowed here; expected one of {sorted(allowed_units)}",
        )
    measured = value.get("value")
    if state == "KNOWN":
        if not _is_number(measured):
            _issue(issues, f"{path}.value", "KNOWN_VALUE_REQUIRED", "KNOWN quantity requires a number")
        elif isinstance(measured, float) and not math.isfinite(measured):
            _issue(issues, f"{path}.value", "FINITE_NUMBER_REQUIRED", "quantity must be finite")
    elif state == "UNKNOWN" and measured is not None:
        _issue(issues, f"{path}.value", "UNKNOWN_MUST_BE_NULL", "UNKNOWN quantity requires null")


def _validate_string_refs(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> None:
    if not _expect_list(value, path, issues):
        return
    if not value:
        _issue(issues, path, "EMPTY_SOURCE_REFS", "at least one source reference is required")
    for index, item in enumerate(value):
        _expect_string(item, f"{path}[{index}]", issues)


def _validate_versioned_refs(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> None:
    if not _expect_list(value, path, issues):
        return
    if not value:
        _issue(issues, path, "EMPTY_SOURCE_REFS", "at least one versioned source reference is required")
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _expect_mapping(item, item_path, issues):
            continue
        _require_fields(item, ("source_id", "version"), item_path, issues)
        _reject_unexpected_fields(item, ("source_id", "version"), item_path, issues)
        _expect_string(item.get("source_id"), f"{item_path}.source_id", issues)
        _expect_string(item.get("version"), f"{item_path}.version", issues)


def _validate_common_entity(
    value: Mapping[str, Any], expected_type: str, path: str, issues: MutableSequence[ValidationIssue]
) -> None:
    _require_fields(value, ("schema_version", "entity_type"), path, issues)
    _expect_string(value.get("schema_version"), f"{path}.schema_version", issues)
    if isinstance(value.get("schema_version"), str) and value.get("schema_version") != "1.0.0":
        _issue(issues, f"{path}.schema_version", "SCHEMA_VERSION_UNSUPPORTED", "expected schema version '1.0.0'")
    if value.get("entity_type") != expected_type:
        _issue(issues, f"{path}.entity_type", "ENTITY_TYPE", f"expected {expected_type!r}")


def _validate_provenance(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _expect_enum(value, PROVENANCE_VALUES, path, issues, "PROVENANCE_ENUM")


def _validate_rail(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "rail", path, issues)
    allowed = ("schema_version", "entity_type", "rail_id", "nominal_voltage", "voltage_limits", "provenance", "source_refs")
    _reject_unexpected_fields(value, allowed, path, issues)
    _require_fields(
        value,
        ("rail_id", "nominal_voltage", "voltage_limits", "provenance", "source_refs"),
        path,
        issues,
    )
    _expect_string(value.get("rail_id"), f"{path}.rail_id", issues)
    _validate_quantity(value.get("nominal_voltage"), f"{path}.nominal_voltage", issues, ("V",))
    limits = value.get("voltage_limits")
    if _expect_mapping(limits, f"{path}.voltage_limits", issues):
        _require_fields(limits, ("minimum", "maximum"), f"{path}.voltage_limits", issues)
        _reject_unexpected_fields(limits, ("minimum", "maximum"), f"{path}.voltage_limits", issues)
        _validate_quantity(limits.get("minimum"), f"{path}.voltage_limits.minimum", issues, ("V",))
        _validate_quantity(limits.get("maximum"), f"{path}.voltage_limits.maximum", issues, ("V",))
        minimum = limits.get("minimum")
        maximum = limits.get("maximum")
        if isinstance(minimum, dict) and isinstance(maximum, dict):
            if minimum.get("state") == maximum.get("state") == "KNOWN":
                lo, hi = minimum.get("value"), maximum.get("value")
                if _is_number(lo) and _is_number(hi) and lo > hi:
                    _issue(issues, f"{path}.voltage_limits", "LIMIT_ORDER", "minimum exceeds maximum")
    _validate_provenance(value.get("provenance"), f"{path}.provenance", issues)
    _validate_string_refs(value.get("source_refs"), f"{path}.source_refs", issues)


def _validate_load(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "load", path, issues)
    allowed = ("schema_version", "entity_type", "load_id", "rail_id", "operating_current", "peak_current", "provenance", "source_refs")
    _reject_unexpected_fields(value, allowed, path, issues)
    _require_fields(
        value,
        ("load_id", "rail_id", "operating_current", "peak_current", "provenance", "source_refs"),
        path,
        issues,
    )
    _expect_string(value.get("load_id"), f"{path}.load_id", issues)
    _expect_string(value.get("rail_id"), f"{path}.rail_id", issues)
    _validate_quantity(value.get("operating_current"), f"{path}.operating_current", issues, ("A",))
    _validate_quantity(value.get("peak_current"), f"{path}.peak_current", issues, ("A",))
    _validate_provenance(value.get("provenance"), f"{path}.provenance", issues)
    _validate_string_refs(value.get("source_refs"), f"{path}.source_refs", issues)


def _validate_regulator(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "regulator", path, issues)
    allowed = (
        "schema_version", "entity_type", "regulator_id", "input_ref", "output_rail_id",
        "output_voltage", "rated_output_current", "switching_frequency", "provenance", "source_refs",
    )
    _reject_unexpected_fields(value, allowed, path, issues)
    _require_fields(
        value,
        (
            "regulator_id",
            "input_ref",
            "output_rail_id",
            "output_voltage",
            "rated_output_current",
            "switching_frequency",
            "provenance",
            "source_refs",
        ),
        path,
        issues,
    )
    for name in ("regulator_id", "input_ref", "output_rail_id"):
        _expect_string(value.get(name), f"{path}.{name}", issues)
    _validate_quantity(value.get("output_voltage"), f"{path}.output_voltage", issues, ("V",))
    _validate_quantity(value.get("rated_output_current"), f"{path}.rated_output_current", issues, ("A",))
    _validate_quantity(value.get("switching_frequency"), f"{path}.switching_frequency", issues, ("Hz",))
    _validate_provenance(value.get("provenance"), f"{path}.provenance", issues)
    _validate_string_refs(value.get("source_refs"), f"{path}.source_refs", issues)


def _validate_source(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "source", path, issues)
    allowed = (
        "schema_version", "entity_type", "source_id", "classification", "title", "version",
        "locator", "access_status", "content_hash",
    )
    _reject_unexpected_fields(value, allowed, path, issues)
    _require_fields(
        value,
        ("source_id", "classification", "title", "version", "locator", "access_status", "content_hash"),
        path,
        issues,
    )
    for name in ("source_id", "title", "version", "locator"):
        _expect_string(value.get(name), f"{path}.{name}", issues)
    _validate_provenance(value.get("classification"), f"{path}.classification", issues)
    access_status = value.get("access_status")
    _expect_enum(
        access_status,
        {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"},
        f"{path}.access_status",
        issues,
        "ACCESS_STATUS_ENUM",
    )
    content_hash = value.get("content_hash")
    if _expect_mapping(content_hash, f"{path}.content_hash", issues):
        _require_fields(content_hash, ("state", "algorithm", "value"), f"{path}.content_hash", issues)
        _reject_unexpected_fields(content_hash, ("state", "algorithm", "value"), f"{path}.content_hash", issues)
        hash_state = content_hash.get("state")
        _expect_enum(hash_state, {"KNOWN", "UNKNOWN"}, f"{path}.content_hash.state", issues, "HASH_STATE_ENUM")
        if content_hash.get("algorithm") != "SHA-256":
            _issue(issues, f"{path}.content_hash.algorithm", "HASH_ALGORITHM", "expected SHA-256")
        if hash_state == "KNOWN":
            _expect_sha256(content_hash.get("value"), f"{path}.content_hash.value", issues)
        elif hash_state == "UNKNOWN" and content_hash.get("value") is not None:
            _issue(
                issues,
                f"{path}.content_hash.value",
                "UNKNOWN_HASH_MUST_BE_NULL",
                "UNKNOWN hash requires null",
            )
        if access_status == "AVAILABLE" and hash_state != "KNOWN":
            _issue(
                issues,
                f"{path}.content_hash.state",
                "AVAILABLE_HASH_REQUIRED",
                "AVAILABLE source requires a KNOWN content hash",
            )
        if isinstance(access_status, str) and access_status in {"MISSING", "UNKNOWN"} and hash_state != "UNKNOWN":
            _issue(
                issues,
                f"{path}.content_hash.state",
                "UNAVAILABLE_HASH_MUST_BE_UNKNOWN",
                "MISSING or UNKNOWN source must use an UNKNOWN/null hash",
            )


def _validate_event(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "event", path, issues)
    allowed = (
        "schema_version", "entity_type", "event_id", "event_type", "event_kind", "state",
        "occurred_at", "time_offset", "subject_refs", "measurements", "provenance", "source_refs",
    )
    _reject_unexpected_fields(value, allowed, path, issues)
    _require_fields(
        value,
        (
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
        ),
        path,
        issues,
    )
    _expect_string(value.get("event_id"), f"{path}.event_id", issues)
    _expect_enum(
        value.get("event_type"),
        {
            "MEASUREMENT",
            "SIMULATION",
            "CALCULATION",
            "REVIEW",
            "SIGNAL_TRANSITION",
            "POWER_STATE",
            "FAULT",
        },
        f"{path}.event_type",
        issues,
        "EVENT_TYPE_ENUM",
    )
    _expect_enum(
        value.get("event_kind"),
        {"VIN_VALID", "EN", "PGOOD", "RESET", "POWER_OFF", "BROWNOUT", "RAIL_LOSS", "RESTART", "OTHER"},
        f"{path}.event_kind",
        issues,
        "EVENT_KIND_ENUM",
    )
    _expect_enum(
        value.get("state"),
        {"ASSERTED", "DEASSERTED", "ENTERED", "EXITED", "ACTIVE", "INACTIVE", "UNKNOWN"},
        f"{path}.state",
        issues,
        "EVENT_STATE_ENUM",
    )
    _expect_timestamp(value.get("occurred_at"), f"{path}.occurred_at", issues)
    _validate_quantity(value.get("time_offset"), f"{path}.time_offset", issues, ("s",))
    _validate_string_refs(value.get("subject_refs"), f"{path}.subject_refs", issues)
    measurements = value.get("measurements")
    if _expect_list(measurements, f"{path}.measurements", issues):
        for index, measurement in enumerate(measurements):
            item_path = f"{path}.measurements[{index}]"
            if not _expect_mapping(measurement, item_path, issues):
                continue
            _require_fields(measurement, ("name", "quantity"), item_path, issues)
            _reject_unexpected_fields(measurement, ("name", "quantity"), item_path, issues)
            _expect_string(measurement.get("name"), f"{item_path}.name", issues)
            _validate_quantity(measurement.get("quantity"), f"{item_path}.quantity", issues)
    _validate_provenance(value.get("provenance"), f"{path}.provenance", issues)
    _validate_string_refs(value.get("source_refs"), f"{path}.source_refs", issues)


def _validate_limit_context(
    value: Any, status: Any, path: str, issues: MutableSequence[ValidationIssue]
) -> None:
    if not _expect_mapping(value, path, issues):
        return
    _require_fields(value, ("required", "state", "refs"), path, issues)
    _reject_unexpected_fields(value, ("required", "state", "refs"), path, issues)
    required = value.get("required")
    if not isinstance(required, bool):
        _issue(issues, f"{path}.required", "TYPE_BOOLEAN", "expected a boolean")
    state = value.get("state")
    _expect_enum(state, LIMIT_STATES, f"{path}.state", issues, "LIMIT_STATE_ENUM")
    refs = value.get("refs")
    if _expect_list(refs, f"{path}.refs", issues):
        for index, ref in enumerate(refs):
            _expect_string(ref, f"{path}.refs[{index}]", issues)
        if required is True and not refs:
            _issue(issues, f"{path}.refs", "LIMIT_REF_REQUIRED", "required limit needs at least one reference")
    if required is True and state == "NOT_APPLICABLE":
        _issue(issues, f"{path}.state", "LIMIT_REQUIRED", "required limit cannot be NOT_APPLICABLE")
    if status == "PASS" and (
        not isinstance(state, str) or state not in {"KNOWN", "NOT_APPLICABLE"}
    ):
        _issue(
            issues,
            f"{path}.state",
            "PASS_WITHOUT_KNOWN_LIMIT",
            "PASS is forbidden when a limit is missing, unknown, or conflicting",
        )
    if status == "PASS" and required is True and state != "KNOWN":
        _issue(issues, f"{path}.state", "PASS_REQUIRED_LIMIT_NOT_KNOWN", "required limits must be KNOWN for PASS")


def _validate_finding(value: Mapping[str, Any], path: str, issues: MutableSequence[ValidationIssue]) -> None:
    _validate_common_entity(value, "finding", path, issues)
    required = (
        "finding_id",
        "subject_ref",
        "rule_id",
        "status",
        "reason_code",
        "message",
        "source_refs",
        "input_hash",
        "tool_version",
        "package_version",
        "limit_context",
    )
    _require_fields(value, required, path, issues)
    _reject_unexpected_fields(value, ("schema_version", "entity_type", *required), path, issues)
    for name in ("finding_id", "rule_id", "reason_code", "message", "tool_version", "package_version"):
        _expect_string(value.get(name), f"{path}.{name}", issues)
    status = value.get("status")
    _expect_enum(status, STATUS_VALUES, f"{path}.status", issues, "STATUS_ENUM")
    subject = value.get("subject_ref")
    if _expect_mapping(subject, f"{path}.subject_ref", issues):
        _require_fields(subject, ("entity_type", "entity_id"), f"{path}.subject_ref", issues)
        _reject_unexpected_fields(subject, ("entity_type", "entity_id"), f"{path}.subject_ref", issues)
        subject_type = subject.get("entity_type")
        if not isinstance(subject_type, str) or subject_type not in ENTITY_TYPES - {"finding"}:
            _issue(issues, f"{path}.subject_ref.entity_type", "SUBJECT_ENTITY_TYPE", "invalid subject type")
        _expect_string(subject.get("entity_id"), f"{path}.subject_ref.entity_id", issues)
    _validate_versioned_refs(value.get("source_refs"), f"{path}.source_refs", issues)
    _expect_sha256(value.get("input_hash"), f"{path}.input_hash", issues)
    _validate_limit_context(value.get("limit_context"), status, f"{path}.limit_context", issues)


ENTITY_VALIDATORS = {
    "rail": _validate_rail,
    "load": _validate_load,
    "regulator": _validate_regulator,
    "source": _validate_source,
    "event": _validate_event,
    "finding": _validate_finding,
}


def _validate_entity(value: Any, expected_type: str, path: str, issues: MutableSequence[ValidationIssue]) -> None:
    if not _expect_mapping(value, path, issues):
        return
    ENTITY_VALIDATORS[expected_type](value, path, issues)


def _validate_claim_limits(value: Any, path: str, issues: MutableSequence[ValidationIssue]) -> List[str]:
    result: List[str] = []
    if not _expect_list(value, path, issues):
        return result
    if not value:
        _issue(issues, path, "EMPTY_CLAIM_SCOPE", "claim scope must not be empty")
    for index, item in enumerate(value):
        if _expect_string(item, f"{path}[{index}]", issues):
            result.append(item)
    if len(result) != len(set(result)):
        _issue(issues, path, "DUPLICATE_CLAIM_LIMIT", "claim limits must be unique")
    return result


def _validate_status_records(
    value: Any, path: str, issues: MutableSequence[ValidationIssue]
) -> List[str]:
    statuses: List[str] = []
    if not _expect_list(value, path, issues):
        return statuses
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _expect_mapping(item, item_path, issues):
            continue
        _require_fields(
            item,
            (
                "id",
                "status",
                "reason_code",
                "reason",
                "inputs_used",
                "metrics",
                "evidence_refs",
                "model_refs",
                "limitations",
                "next_action",
            ),
            item_path,
            issues,
        )
        _reject_unexpected_fields(
            item,
            ("id", "status", "reason_code", "reason", "inputs_used", "metrics", "evidence_refs", "model_refs", "limitations", "next_action"),
            item_path,
            issues,
        )
        _expect_string(item.get("id"), f"{item_path}.id", issues)
        _expect_string(item.get("reason_code"), f"{item_path}.reason_code", issues)
        _expect_string(item.get("reason"), f"{item_path}.reason", issues)
        _expect_string(item.get("next_action"), f"{item_path}.next_action", issues)
        for field_name in ("inputs_used", "evidence_refs", "model_refs", "limitations"):
            field_value = item.get(field_name)
            field_path = f"{item_path}.{field_name}"
            if _expect_list(field_value, field_path, issues):
                for field_index, entry in enumerate(field_value):
                    _expect_string(entry, f"{field_path}[{field_index}]", issues)
        metrics = item.get("metrics")
        if _expect_mapping(metrics, f"{item_path}.metrics", issues):
            for metric_name, metric_value in metrics.items():
                if not isinstance(metric_name, str) or not metric_name:
                    _issue(issues, f"{item_path}.metrics", "METRIC_NAME", "metric names must be non-empty strings")
                    continue
                _validate_quantity(metric_value, f"{item_path}.metrics.{metric_name}", issues)
        status = item.get("status")
        if _expect_enum(status, STATUS_VALUES, f"{item_path}.status", issues, "STATUS_ENUM"):
            statuses.append(status)
            if status == "PASS":
                if not item.get("inputs_used"):
                    _issue(issues, f"{item_path}.inputs_used", "PASS_INPUTS_REQUIRED", "PASS must name its inputs")
                if not item.get("evidence_refs"):
                    _issue(
                        issues,
                        f"{item_path}.evidence_refs",
                        "PASS_EVIDENCE_REQUIRED",
                        "PASS must cite evidence",
                    )
    return statuses


def _lookup_limit_state(ref: str, collections: Mapping[str, Any]) -> str:
    """Resolve the D1 rail limit reference syntax.

    Supported form: ``rail:<rail_id>.voltage_limits.minimum`` (or maximum).
    Unsupported or missing references are conservatively MISSING.
    """

    match = re.fullmatch(r"rail:([^.]+)\.voltage_limits\.(minimum|maximum)", ref)
    if match is None:
        return "MISSING"
    rail_id, side = match.groups()
    rails = collections.get("rails")
    if not isinstance(rails, list):
        return "MISSING"
    for rail in rails:
        if not isinstance(rail, dict) or rail.get("rail_id") != rail_id:
            continue
        if rail.get("provenance") == "CONFLICT":
            return "CONFLICT"
        limits = rail.get("voltage_limits")
        quantity = limits.get(side) if isinstance(limits, dict) else None
        if not isinstance(quantity, dict):
            return "MISSING"
        if quantity.get("state") == "UNKNOWN" or quantity.get("value") is None:
            return "UNKNOWN"
        if quantity.get("state") == "KNOWN" and _is_number(quantity.get("value")):
            return "KNOWN"
        return "MISSING"
    return "MISSING"


def _combined_limit_state(states: Sequence[str]) -> str:
    for state in ("CONFLICT", "MISSING", "UNKNOWN"):
        if state in states:
            return state
    return "KNOWN" if states else "MISSING"


def _validate_handoff(value: Mapping[str, Any], issues: MutableSequence[ValidationIssue]) -> None:
    _require_fields(value, ("request", "response"), "$", issues)
    _reject_unexpected_fields(value, ("request", "response"), "$", issues)
    request = value.get("request")
    response = value.get("response")
    if not _expect_mapping(request, "$.request", issues) or not _expect_mapping(response, "$.response", issues):
        return

    request_required = (
        "contract_version",
        "handoff_id",
        "producer",
        "configuration_id",
        "configuration_sha256",
        "collections",
        "claim_limits",
    )
    _require_fields(request, request_required, "$.request", issues)
    _reject_unexpected_fields(request, request_required, "$.request", issues)
    for name in ("contract_version", "handoff_id", "configuration_id"):
        _expect_string(request.get(name), f"$.request.{name}", issues)
    if isinstance(request.get("contract_version"), str) and request.get("contract_version") != "1.0.0":
        _issue(
            issues,
            "$.request.contract_version",
            "CONTRACT_VERSION_UNSUPPORTED",
            "expected contract version '1.0.0'",
        )
    configuration_hash = request.get("configuration_sha256")
    _expect_sha256(configuration_hash, "$.request.configuration_sha256", issues)

    producer = request.get("producer")
    if _expect_mapping(producer, "$.request.producer", issues):
        _require_fields(
            producer,
            ("repo", "product_contract_version", "rule_registry_version"),
            "$.request.producer",
            issues,
        )
        _reject_unexpected_fields(
            producer,
            ("repo", "product_contract_version", "rule_registry_version"),
            "$.request.producer",
            issues,
        )
        for name in ("repo", "product_contract_version", "rule_registry_version"):
            _expect_string(producer.get(name), f"$.request.producer.{name}", issues)

    request_claims = _validate_claim_limits(request.get("claim_limits"), "$.request.claim_limits", issues)
    collections = request.get("collections")
    collection_types = {
        "sources": "source",
        "rails": "rail",
        "loads": "load",
        "regulators": "regulator",
        "events": "event",
    }
    source_index: Dict[str, Mapping[str, Any]] = {}
    entity_indexes: Dict[str, Dict[str, Mapping[str, Any]]] = {
        collection_name: {} for collection_name in collection_types
    }
    if _expect_mapping(collections, "$.request.collections", issues):
        _require_fields(collections, collection_types, "$.request.collections", issues)
        _reject_unexpected_fields(collections, collection_types, "$.request.collections", issues)
        id_fields = {
            "sources": "source_id",
            "rails": "rail_id",
            "loads": "load_id",
            "regulators": "regulator_id",
            "events": "event_id",
        }
        for collection_name, entity_type in collection_types.items():
            items = collections.get(collection_name)
            collection_path = f"$.request.collections.{collection_name}"
            if not _expect_list(items, collection_path, issues):
                continue
            seen_ids = set()
            for index, item in enumerate(items):
                item_path = f"{collection_path}[{index}]"
                _validate_entity(item, entity_type, item_path, issues)
                if not isinstance(item, dict):
                    continue
                entity_id = item.get(id_fields[collection_name])
                if not isinstance(entity_id, str):
                    continue
                if entity_id in seen_ids:
                    _issue(
                        issues,
                        f"{item_path}.{id_fields[collection_name]}",
                        "DUPLICATE_ENTITY_ID",
                        f"duplicate identifier {entity_id!r} in {collection_name}",
                    )
                seen_ids.add(entity_id)
                entity_indexes[collection_name].setdefault(entity_id, item)
                if collection_name == "sources":
                    source_index.setdefault(entity_id, item)

        for collection_name in ("rails", "loads", "regulators", "events"):
            items = collections.get(collection_name)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                refs = item.get("source_refs")
                if not isinstance(refs, list):
                    continue
                for ref_index, source_ref in enumerate(refs):
                    if isinstance(source_ref, str) and source_ref not in source_index:
                        _issue(
                            issues,
                            f"$.request.collections.{collection_name}[{index}].source_refs[{ref_index}]",
                            "SOURCE_REF_UNRESOLVED",
                            "source is absent from request collections.sources",
                        )

        loads = collections.get("loads")
        if isinstance(loads, list):
            for index, item in enumerate(loads):
                if not isinstance(item, dict):
                    continue
                rail_id = item.get("rail_id")
                if isinstance(rail_id, str) and rail_id not in entity_indexes["rails"]:
                    _issue(
                        issues,
                        f"$.request.collections.loads[{index}].rail_id",
                        "RAIL_REF_UNRESOLVED",
                        "load rail_id is absent from request collections.rails",
                    )

        regulators = collections.get("regulators")
        if isinstance(regulators, list):
            for index, item in enumerate(regulators):
                if not isinstance(item, dict):
                    continue
                for field_name in ("input_ref", "output_rail_id"):
                    rail_id = item.get(field_name)
                    if isinstance(rail_id, str) and rail_id not in entity_indexes["rails"]:
                        _issue(
                            issues,
                            f"$.request.collections.regulators[{index}].{field_name}",
                            "RAIL_REF_UNRESOLVED",
                            f"regulator {field_name} is absent from request collections.rails",
                        )

        events = collections.get("events")
        subject_collection = {
            "source": "sources",
            "rail": "rails",
            "load": "loads",
            "regulator": "regulators",
            "event": "events",
        }
        if isinstance(events, list):
            for index, item in enumerate(events):
                if not isinstance(item, dict) or not isinstance(item.get("subject_refs"), list):
                    continue
                for ref_index, subject_ref in enumerate(item["subject_refs"]):
                    if not isinstance(subject_ref, str):
                        continue
                    subject_type, separator, subject_id = subject_ref.partition(":")
                    collection_name = subject_collection.get(subject_type)
                    if not separator or not subject_id or collection_name is None:
                        _issue(
                            issues,
                            f"$.request.collections.events[{index}].subject_refs[{ref_index}]",
                            "SUBJECT_REF_FORMAT",
                            "expected a typed subject reference such as rail:VDD_3V3",
                        )
                    elif subject_id not in entity_indexes[collection_name]:
                        _issue(
                            issues,
                            f"$.request.collections.events[{index}].subject_refs[{ref_index}]",
                            "SUBJECT_REF_UNRESOLVED",
                            "event subject reference is absent from request collections",
                        )

    hash_material_fields = ("contract_version", "configuration_id", "producer", "collections", "claim_limits")
    if all(field in request for field in hash_material_fields) and isinstance(producer, dict):
        producer_hash_fields = ("repo", "product_contract_version", "rule_registry_version")
        if all(field in producer for field in producer_hash_fields):
            try:
                calculated_hash = compute_configuration_sha256(request)
            except (TypeError, ValueError, KeyError) as exc:
                _issue(
                    issues,
                    "$.request",
                    "REQUEST_HASH_UNCOMPUTABLE",
                    f"configuration hash material cannot be encoded safely: {exc}",
                )
            else:
                if configuration_hash != calculated_hash:
                    _issue(
                        issues,
                        "$.request.configuration_sha256",
                        "REQUEST_CONFIGURATION_HASH_MISMATCH",
                        "configuration_sha256 does not match the defined technical hash material",
                    )

    response_required = (
        "contract_version",
        "assessment_id",
        "input_configuration_sha256",
        "tool_versions",
        "model_records",
        "checks",
        "scenario_results",
        "findings",
        "summary_status",
        "claim_limits",
        "generated_at",
    )
    _require_fields(response, response_required, "$.response", issues)
    _reject_unexpected_fields(response, response_required, "$.response", issues)
    _expect_string(response.get("contract_version"), "$.response.contract_version", issues)
    if (
        isinstance(request.get("contract_version"), str)
        and isinstance(response.get("contract_version"), str)
        and request["contract_version"] != response["contract_version"]
    ):
        _issue(
            issues,
            "$.response.contract_version",
            "CONTRACT_VERSION_MISMATCH",
            "response contract version must match the request contract version",
        )
    _expect_string(response.get("assessment_id"), "$.response.assessment_id", issues)
    echoed_hash = response.get("input_configuration_sha256")
    _expect_sha256(echoed_hash, "$.response.input_configuration_sha256", issues)
    if isinstance(configuration_hash, str) and isinstance(echoed_hash, str) and configuration_hash != echoed_hash:
        _issue(
            issues,
            "$.response.input_configuration_sha256",
            "HASH_MISMATCH",
            "response does not assess the exact request configuration",
        )

    tool_versions = response.get("tool_versions")
    if _expect_list(tool_versions, "$.response.tool_versions", issues):
        if not tool_versions:
            _issue(issues, "$.response.tool_versions", "EMPTY_TOOL_VERSIONS", "at least one tool version is required")
        for index, tool_record in enumerate(tool_versions):
            item_path = f"$.response.tool_versions[{index}]"
            if not _expect_mapping(tool_record, item_path, issues):
                continue
            _require_fields(tool_record, ("name", "version", "package_version"), item_path, issues)
            _reject_unexpected_fields(tool_record, ("name", "version", "package_version"), item_path, issues)
            for name in ("name", "version", "package_version"):
                _expect_string(tool_record.get(name), f"{item_path}.{name}", issues)

    model_records = response.get("model_records")
    model_index: Dict[str, Mapping[str, Any]] = {}
    if _expect_list(model_records, "$.response.model_records", issues):
        for index, record in enumerate(model_records):
            item_path = f"$.response.model_records[{index}]"
            if not _expect_mapping(record, item_path, issues):
                continue
            _require_fields(
                record,
                (
                    "model_id",
                    "source",
                    "sha256",
                    "license_status",
                    "model_type",
                    "validity_bounds",
                    "access_status",
                ),
                item_path,
                issues,
            )
            _reject_unexpected_fields(
                record,
                ("model_id", "source", "sha256", "license_status", "model_type", "validity_bounds", "access_status"),
                item_path,
                issues,
            )
            for name in ("model_id", "source", "model_type"):
                _expect_string(record.get(name), f"{item_path}.{name}", issues)
            _expect_enum(
                record.get("license_status"),
                {"VERIFIED", "DECLARED", "UNKNOWN", "CONFLICT", "RESTRICTED"},
                f"{item_path}.license_status",
                issues,
                "LICENSE_STATUS_ENUM",
            )
            _expect_sha256(record.get("sha256"), f"{item_path}.sha256", issues)
            _expect_mapping(record.get("validity_bounds"), f"{item_path}.validity_bounds", issues)
            _expect_enum(
                record.get("access_status"),
                {"AVAILABLE", "MISSING", "BLOCKED", "UNKNOWN"},
                f"{item_path}.access_status",
                issues,
                "ACCESS_STATUS_ENUM",
            )
            model_id = record.get("model_id")
            if isinstance(model_id, str):
                if model_id in model_index:
                    _issue(
                        issues,
                        f"{item_path}.model_id",
                        "DUPLICATE_MODEL_ID",
                        f"duplicate model identifier {model_id!r}",
                    )
                else:
                    model_index[model_id] = record

    evaluation_statuses = _validate_status_records(response.get("checks"), "$.response.checks", issues)
    evaluation_statuses.extend(
        _validate_status_records(response.get("scenario_results"), "$.response.scenario_results", issues)
    )

    for record_group in ("checks", "scenario_results"):
        records = response.get(record_group)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            record_path = f"$.response.{record_group}[{index}]"
            record_status = record.get("status")
            inputs_used = record.get("inputs_used")
            if isinstance(inputs_used, list):
                known_input_ids = {
                    entity_id
                    for index_by_collection in entity_indexes.values()
                    for entity_id in index_by_collection
                }
                for ref_index, input_ref in enumerate(inputs_used):
                    if isinstance(input_ref, str) and input_ref not in known_input_ids:
                        _issue(
                            issues,
                            f"{record_path}.inputs_used[{ref_index}]",
                            "EVALUATION_INPUT_UNRESOLVED",
                            "evaluation input is absent from request collections",
                        )
            evidence_refs = record.get("evidence_refs")
            if isinstance(evidence_refs, list):
                for ref_index, source_ref in enumerate(evidence_refs):
                    if not isinstance(source_ref, str):
                        continue
                    source = source_index.get(source_ref)
                    ref_path = f"{record_path}.evidence_refs[{ref_index}]"
                    if source is None:
                        _issue(
                            issues,
                            ref_path,
                            "EVALUATION_EVIDENCE_UNRESOLVED",
                            "evidence source is absent from request",
                        )
                        continue
                    if record_status == "PASS":
                        content_hash = source.get("content_hash")
                        hash_known = (
                            isinstance(content_hash, dict)
                            and content_hash.get("state") == "KNOWN"
                            and isinstance(content_hash.get("value"), str)
                            and SHA256_RE.fullmatch(content_hash["value"]) is not None
                        )
                        if source.get("access_status") != "AVAILABLE" or not hash_known:
                            _issue(
                                issues,
                                ref_path,
                                "PASS_FROM_UNAVAILABLE_SOURCE",
                                "PASS requires AVAILABLE evidence with a KNOWN SHA-256",
                            )
                        classification = source.get("classification")
                        if isinstance(classification, str) and classification in {"ASSUMPTION", "UNKNOWN", "CONFLICT"}:
                            _issue(
                                issues,
                                ref_path,
                                "PASS_FROM_UNSAFE_SOURCE",
                                "PASS cannot rely only on ASSUMPTION, UNKNOWN, or CONFLICT source evidence",
                            )
            model_refs = record.get("model_refs")
            if isinstance(model_refs, list):
                for ref_index, model_ref in enumerate(model_refs):
                    if not isinstance(model_ref, str):
                        continue
                    model = model_index.get(model_ref)
                    ref_path = f"{record_path}.model_refs[{ref_index}]"
                    if model is None:
                        _issue(
                            issues,
                            ref_path,
                            "EVALUATION_MODEL_UNRESOLVED",
                            "model is absent from response.model_records",
                        )
                        continue
                    license_status = model.get("license_status")
                    if record_status == "PASS" and (
                        model.get("access_status") != "AVAILABLE"
                        or not isinstance(license_status, str)
                        or license_status not in {"VERIFIED", "DECLARED"}
                        or not isinstance(model.get("validity_bounds"), dict)
                        or not model.get("validity_bounds")
                    ):
                        _issue(
                            issues,
                            ref_path,
                            "PASS_FROM_UNUSABLE_MODEL",
                            "PASS requires an AVAILABLE, licensed model with explicit validity bounds",
                        )

    findings = response.get("findings")
    valid_findings: List[Mapping[str, Any]] = []
    if _expect_list(findings, "$.response.findings", issues):
        for index, finding in enumerate(findings):
            finding_path = f"$.response.findings[{index}]"
            _validate_entity(finding, "finding", finding_path, issues)
            if isinstance(finding, dict):
                valid_findings.append(finding)
                if isinstance(finding.get("status"), str) and finding["status"] in STATUS_VALUES:
                    evaluation_statuses.append(finding["status"])
                if isinstance(configuration_hash, str) and finding.get("input_hash") != configuration_hash:
                    _issue(
                        issues,
                        f"{finding_path}.input_hash",
                        "FINDING_INPUT_HASH_MISMATCH",
                        "finding must bind to the request configuration hash",
                    )
                subject = finding.get("subject_ref")
                if isinstance(subject, dict):
                    subject_type = subject.get("entity_type")
                    subject_id = subject.get("entity_id")
                    collection_name = {
                        "source": "sources",
                        "rail": "rails",
                        "load": "loads",
                        "regulator": "regulators",
                        "event": "events",
                    }.get(subject_type) if isinstance(subject_type, str) else None
                    if (
                        collection_name is not None
                        and isinstance(subject_id, str)
                        and subject_id not in entity_indexes[collection_name]
                    ):
                        _issue(
                            issues,
                            f"{finding_path}.subject_ref.entity_id",
                            "SUBJECT_REF_UNRESOLVED",
                            "finding subject is absent from request collections",
                        )

    summary_status = response.get("summary_status")
    _expect_enum(summary_status, STATUS_VALUES, "$.response.summary_status", issues, "STATUS_ENUM")
    response_claims = _validate_claim_limits(response.get("claim_limits"), "$.response.claim_limits", issues)
    expanded_claims = sorted(set(response_claims) - set(request_claims))
    reduced_claims = sorted(set(request_claims) - set(response_claims))
    if expanded_claims:
        _issue(
            issues,
            "$.response.claim_limits",
            "CLAIM_SCOPE_EXPANDED",
            f"response adds claims not authorized by request: {expanded_claims}",
        )
    if reduced_claims:
        _issue(
            issues,
            "$.response.claim_limits",
            "CLAIM_SCOPE_REDUCED",
            f"response omits request constraints or claims: {reduced_claims}",
        )
    _expect_timestamp(response.get("generated_at"), "$.response.generated_at", issues)

    if summary_status == "PASS":
        if not evaluation_statuses:
            _issue(issues, "$.response.summary_status", "EMPTY_PASS", "PASS requires at least one evaluation")
        non_pass = [status for status in evaluation_statuses if status != "PASS"]
        if non_pass:
            _issue(
                issues,
                "$.response.summary_status",
                "PASS_SCOPE_VIOLATION",
                f"summary PASS is forbidden while evaluations contain {sorted(set(non_pass))}",
            )

    for index, finding in enumerate(valid_findings):
        finding_path = f"$.response.findings[{index}]"
        finding_source_refs = finding.get("source_refs")
        if not isinstance(finding_source_refs, list):
            finding_source_refs = []
        for ref_index, ref in enumerate(finding_source_refs):
            if not isinstance(ref, dict):
                continue
            source_id = ref.get("source_id")
            if not isinstance(source_id, str):
                continue
            source = source_index.get(source_id)
            ref_path = f"{finding_path}.source_refs[{ref_index}]"
            if source is None:
                _issue(issues, f"{ref_path}.source_id", "SOURCE_REF_UNRESOLVED", "source is absent from request")
                continue
            if ref.get("version") != source.get("version"):
                _issue(issues, f"{ref_path}.version", "SOURCE_VERSION_MISMATCH", "source version is not exact")
            source_classification = source.get("classification")
            if (
                finding.get("status") == "PASS"
                and isinstance(source_classification, str)
                and source_classification in {"ASSUMPTION", "UNKNOWN", "CONFLICT"}
            ):
                _issue(
                    issues,
                    ref_path,
                    "PASS_FROM_UNSAFE_SOURCE",
                    "PASS cannot rely only on ASSUMPTION, UNKNOWN, or CONFLICT source evidence",
                )
            if finding.get("status") == "PASS":
                content_hash = source.get("content_hash")
                hash_known = (
                    isinstance(content_hash, dict)
                    and content_hash.get("state") == "KNOWN"
                    and isinstance(content_hash.get("value"), str)
                    and SHA256_RE.fullmatch(content_hash["value"]) is not None
                )
                if source.get("access_status") != "AVAILABLE" or not hash_known:
                    _issue(
                        issues,
                        ref_path,
                        "PASS_FROM_UNAVAILABLE_SOURCE",
                        "PASS requires AVAILABLE evidence with a KNOWN SHA-256",
                    )

        limit_context = finding.get("limit_context")
        if isinstance(limit_context, dict) and limit_context.get("required") is True:
            refs = limit_context.get("refs")
            if isinstance(refs, list) and isinstance(collections, dict):
                observed = _combined_limit_state(
                    [_lookup_limit_state(ref, collections) for ref in refs if isinstance(ref, str)]
                )
                declared = limit_context.get("state")
                if declared != observed:
                    _issue(
                        issues,
                        f"{finding_path}.limit_context.state",
                        "LIMIT_STATE_MISMATCH",
                        f"declared {declared!r}, resolved {observed!r}",
                    )
                if finding.get("status") == "PASS" and observed != "KNOWN":
                    _issue(
                        issues,
                        f"{finding_path}.status",
                        "PASS_WITHOUT_RESOLVED_LIMIT",
                        f"required limit resolves to {observed}, not KNOWN",
                    )


def validate_document(value: Any) -> List[ValidationIssue]:
    """Return all D1 validation issues for an entity or request/response handoff."""

    issues: List[ValidationIssue] = []
    if not isinstance(value, dict):
        _issue(issues, "$", "TYPE_OBJECT", "contract document must be an object")
        return issues
    _reject_non_finite_values(value, "$", issues)
    if "request" in value or "response" in value:
        _validate_handoff(value, issues)
        return issues
    entity_type = value.get("entity_type")
    if not isinstance(entity_type, str) or entity_type not in ENTITY_VALIDATORS:
        _issue(issues, "$.entity_type", "ENTITY_TYPE", f"expected one of {sorted(ENTITY_TYPES)}")
        return issues
    _validate_entity(value, entity_type, "$", issues)
    return issues


def load_json(path: Any) -> Any:
    """Load strict UTF-8 JSON, preserving JSON null as Python ``None``."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            parse_constant=_reject_non_finite_json_number,
            object_pairs_hook=_strict_json_object,
        )


def validate_file(path: Any) -> List[ValidationIssue]:
    """Load and validate a file, returning parse failures as normal issues."""

    try:
        value = load_json(path)
    except json.JSONDecodeError as exc:
        return [
            ValidationIssue(
                path="$",
                code="MALFORMED_JSON",
                message=f"line {exc.lineno}, column {exc.colno}: {exc.msg}",
            )
        ]
    except ValueError as exc:
        code = "DUPLICATE_JSON_KEY" if isinstance(exc, _DuplicateJsonKeyError) else "NON_FINITE_JSON_NUMBER"
        return [ValidationIssue(path="$", code=code, message=str(exc))]
    except (OSError, UnicodeError) as exc:
        return [ValidationIssue(path="$", code="FILE_READ_ERROR", message=str(exc))]
    return validate_document(value)


def assert_valid(value: Any) -> None:
    """Raise :class:`ContractValidationError` unless *value* is valid."""

    issues = validate_document(value)
    if issues:
        raise ContractValidationError(issues)

