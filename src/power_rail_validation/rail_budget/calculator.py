"""Static rail-budget calculator for WF-02-02.

The calculator consumes a small JSON-compatible, module-local input envelope.
It uses :class:`decimal.Decimal` internally, rounds every published derived
number to a declared precision, binds the result to the exact input hash, and
never substitutes a numeric default for missing evidence.

The calculation is intentionally algebraic.  A ``PASS`` means only that the
provided static values are internally within their declared voltage and
current limits.  It is not physical, transient, thermal, stability, EMC, or
field validation.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any, Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple, Union

from power_rail_validation import __version__ as PACKAGE_VERSION
from power_rail_validation.contracts.canonical import sha256_hexdigest


FORMULA_VERSION = "REP02-RB-1.0.0"
RULE_ID = "REP02.RAIL_BUDGET.STATIC"
TOOL_NAME = "power-rail-validation"
ROUNDING_MODE = "ROUND_HALF_EVEN"
ROUNDING_SIGNIFICANT_DIGITS = 12

_PROFILE_NAMES = ("Imin", "Ityp", "Imax")
_POWER_PROFILE_NAMES = {"Imin": "Pmin", "Ityp": "Ptyp", "Imax": "Pmax"}
_TRUSTED_PROVENANCE = frozenset({"SOURCE_EXPLICIT", "ENGINEERING_RULE"})
_UNCERTAIN_PROVENANCE = frozenset({"ASSUMPTION", "UNKNOWN", "DERIVED"})
_ALL_PROVENANCE = _TRUSTED_PROVENANCE | _UNCERTAIN_PROVENANCE | {"CONFLICT"}
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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_MISSING = object()


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


def _is_floating_version(value: str) -> bool:
    """Reject common mutable aliases; immutable identity remains evidence-owned."""

    normalized = value.strip().casefold()
    if normalized in _FLOATING_VERSION_TOKENS:
        return True
    if any(marker in normalized for marker in ("*", "^", "~", "<", ">", "=", "|")):
        return True
    if normalized.startswith("nightly/") or normalized.startswith("nightly-"):
        return True
    segments = tuple(part for part in normalized.replace("\\", "/").split("/") if part)
    if segments and segments[-1] in _FLOATING_VERSION_TOKENS:
        return True
    if any(segment in {"branch", "branches", "heads"} for segment in segments):
        return True
    dotted = tuple(part for part in normalized.split(".") if part)
    return bool(dotted and dotted[-1] == "x")


def _working_precision(value: Any) -> int:
    """Choose a context large enough to avoid decision-changing input rounding."""

    stack = [value]
    seen: set[int] = set()
    maximum_span = ROUNDING_SIGNIFICANT_DIGITS
    maximum_adjusted: Optional[int] = None
    minimum_exponent: Optional[int] = None
    number_count = 0
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current)
        elif _is_number(current):
            converted = _to_decimal(current)
            if converted is None:
                continue
            number_count += 1
            digits = len(converted.as_tuple().digits)
            integer_span = max(converted.adjusted() + 1, 1)
            maximum_span = max(maximum_span, digits, integer_span)
            adjusted = converted.adjusted()
            exponent = converted.as_tuple().exponent
            maximum_adjusted = adjusted if maximum_adjusted is None else max(maximum_adjusted, adjusted)
            minimum_exponent = exponent if minimum_exponent is None else min(minimum_exponent, exponent)
    if maximum_adjusted is not None and minimum_exponent is not None:
        maximum_span = max(maximum_span, maximum_adjusted - minimum_exponent + 1)
    return max(80, maximum_span * 4 + len(str(max(number_count, 1))) + 32)


def _rounded_number(value: Decimal) -> Union[int, float]:
    """Return a finite JSON number rounded by the published policy."""

    if value == 0:
        return 0.0
    with localcontext() as context:
        context.prec = max(
            80,
            len(value.as_tuple().digits) + ROUNDING_SIGNIFICANT_DIGITS + 8,
            value.adjusted() + ROUNDING_SIGNIFICANT_DIGITS + 8,
        )
        quantum = Decimal(1).scaleb(value.adjusted() - ROUNDING_SIGNIFICANT_DIGITS + 1)
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    candidate = float(rounded)
    if math.isfinite(candidate) and candidate != 0:
        return candidate
    if rounded == rounded.to_integral_value():
        return int(rounded)
    raise OverflowError("derived value is outside finite JSON floating-point range")


def _known(value: Decimal, unit: str) -> Dict[str, Any]:
    return {"state": "KNOWN", "value": _rounded_number(value), "unit": unit}


def _unknown(unit: str) -> Dict[str, Any]:
    return {"state": "UNKNOWN", "value": None, "unit": unit}


def _unknown_profiles(unit: str, *, power: bool = False) -> Dict[str, Dict[str, Any]]:
    names = tuple(_POWER_PROFILE_NAMES.values()) if power else _PROFILE_NAMES
    return {name: _unknown(unit) for name in names}


def _mapping(
    value: Any, path: str, issues: MutableSequence[_Issue], *, missing_code: str = "REQUIRED_INPUT_MISSING"
) -> Optional[Mapping[str, Any]]:
    if value is _MISSING:
        _add_issue(issues, "BLOCKED", missing_code, path, "required object is absent")
        return None
    if not isinstance(value, dict):
        _add_issue(issues, "BLOCKED", "TYPE_OBJECT", path, "expected an object")
        return None
    return value


def _reject_unexpected_fields(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    path: str,
    issues: MutableSequence[_Issue],
) -> bool:
    allowed_names = set(allowed)
    unexpected = [name for name in value if not isinstance(name, str) or name not in allowed_names]
    for name in sorted(unexpected, key=repr):
        _add_issue(
            issues,
            "BLOCKED",
            "UNEXPECTED_FIELD",
            f"{path}.{name!r}",
            "field is outside the rail-budget input contract",
        )
    return bool(unexpected)


def _quantity(
    value: Any,
    expected_unit: str,
    path: str,
    issues: MutableSequence[_Issue],
    *,
    unknown_code: str,
    optional: bool = False,
) -> Optional[Decimal]:
    if value is _MISSING:
        if optional:
            _add_issue(issues, "UNKNOWN", unknown_code, path, "optional value is absent; no default was applied")
        else:
            _add_issue(issues, "BLOCKED", "REQUIRED_INPUT_MISSING", path, "required quantity is absent")
        return None
    if not isinstance(value, dict):
        _add_issue(issues, "BLOCKED", "QUANTITY_OBJECT_REQUIRED", path, "expected {state, value, unit}")
        return None
    has_unexpected = _reject_unexpected_fields(
        value,
        {"state", "value", "unit"},
        path,
        issues,
    )
    for name in ("state", "value", "unit"):
        if name not in value:
            _add_issue(
                issues,
                "BLOCKED",
                "REQUIRED_INPUT_MISSING",
                f"{path}.{name}",
                "required quantity member is absent",
            )
    unit_matches = value.get("unit") == expected_unit
    if not unit_matches:
        _add_issue(
            issues,
            "FAIL",
            "UNIT_MISMATCH",
            f"{path}.unit",
            f"expected explicit unit {expected_unit!r}",
        )
    state = value.get("state")
    if state == "UNKNOWN":
        if value.get("value") is not None:
            _add_issue(
                issues,
                "BLOCKED",
                "UNKNOWN_MUST_BE_NULL",
                f"{path}.value",
                "UNKNOWN quantity requires null",
            )
        _add_issue(issues, "UNKNOWN", unknown_code, path, "required calculation value is explicitly unknown")
        return None
    if state != "KNOWN":
        _add_issue(
            issues,
            "BLOCKED",
            "QUANTITY_STATE_INVALID",
            f"{path}.state",
            "expected KNOWN or UNKNOWN",
        )
        return None
    converted = _to_decimal(value.get("value"))
    if converted is None:
        _add_issue(
            issues,
            "BLOCKED",
            "FINITE_NUMBER_REQUIRED",
            f"{path}.value",
            "KNOWN quantity requires a finite JSON number",
        )
        return None
    return converted if unit_matches and not has_unexpected else None


def _provenance_and_refs(
    value: Optional[Mapping[str, Any]], path: str, issues: MutableSequence[_Issue]
) -> Tuple[str, ...]:
    if value is None:
        return ()
    provenance = value.get("provenance", _MISSING)
    if provenance is _MISSING:
        _add_issue(
            issues,
            "BLOCKED",
            "REQUIRED_INPUT_MISSING",
            f"{path}.provenance",
            "provenance is required",
        )
    elif not isinstance(provenance, str) or provenance not in _ALL_PROVENANCE:
        _add_issue(
            issues,
            "BLOCKED",
            "PROVENANCE_INVALID",
            f"{path}.provenance",
            "unrecognized provenance classification",
        )
    elif provenance == "CONFLICT":
        _add_issue(
            issues,
            "BLOCKED",
            "PROVENANCE_CONFLICT",
            f"{path}.provenance",
            "conflicted calculation input cannot support an assessment",
        )
    elif provenance in _UNCERTAIN_PROVENANCE:
        _add_issue(
            issues,
            "UNKNOWN",
            "PROVENANCE_UNCERTAIN",
            f"{path}.provenance",
            "assumed, unverified derived, or unknown provenance cannot support PASS",
        )

    refs = value.get("source_refs", _MISSING)
    if refs is _MISSING:
        _add_issue(
            issues,
            "BLOCKED",
            "REQUIRED_INPUT_MISSING",
            f"{path}.source_refs",
            "source references are required",
        )
        return ()
    if not isinstance(refs, list) or not refs:
        _add_issue(
            issues,
            "BLOCKED",
            "SOURCE_REFS_REQUIRED",
            f"{path}.source_refs",
            "at least one source reference is required",
        )
        return ()
    result: List[str] = []
    for index, reference in enumerate(refs):
        if not isinstance(reference, str) or _ID_RE.fullmatch(reference) is None:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_REF_INVALID",
                f"{path}.source_refs[{index}]",
                "source reference must be a stable identifier",
            )
            continue
        if reference in result:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_REF_DUPLICATE",
                f"{path}.source_refs[{index}]",
                "source references must be unique",
            )
            continue
        result.append(reference)
    return tuple(result)


def _source_index(
    value: Any, issues: MutableSequence[_Issue]
) -> Tuple[Dict[str, Mapping[str, Any]], Tuple[str, ...]]:
    if value is _MISSING:
        _add_issue(issues, "BLOCKED", "REQUIRED_INPUT_MISSING", "$.sources", "sources array is absent")
        return {}, ()
    if not isinstance(value, list):
        _add_issue(issues, "BLOCKED", "TYPE_ARRAY", "$.sources", "expected an array")
        return {}, ()
    index: Dict[str, Mapping[str, Any]] = {}
    for item_index, source in enumerate(value):
        path = f"$.sources[{item_index}]"
        if not isinstance(source, dict):
            _add_issue(issues, "BLOCKED", "TYPE_OBJECT", path, "source must be an object")
            continue
        _reject_unexpected_fields(
            source,
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
            },
            path,
            issues,
        )
        if source.get("schema_version") != "1.0.0":
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_SCHEMA_VERSION_INVALID",
                f"{path}.schema_version",
                "source schema_version must be '1.0.0'",
            )
        if source.get("entity_type") != "source":
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_ENTITY_TYPE_INVALID",
                f"{path}.entity_type",
                "source entity_type must be 'source'",
            )
        for field_name in ("title", "locator"):
            field_value = source.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_METADATA_INVALID",
                    f"{path}.{field_name}",
                    f"source {field_name} must be a non-empty string",
                )
        classification = source.get("classification")
        if not isinstance(classification, str) or classification not in _ALL_PROVENANCE:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_CLASSIFICATION_INVALID",
                f"{path}.classification",
                "unrecognized source classification",
            )
        version = source.get("version")
        normalized_version = version.strip() if isinstance(version, str) else ""
        if not normalized_version:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_VERSION_MISSING",
                f"{path}.version",
                "a non-empty source version is required",
            )
        elif _is_floating_version(normalized_version):
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_VERSION_FLOATING",
                f"{path}.version",
                "source version uses a mutable alias instead of an immutable revision",
            )

        access_status = source.get("access_status")
        if not isinstance(access_status, str) or access_status not in {
            "AVAILABLE",
            "MISSING",
            "BLOCKED",
            "UNKNOWN",
        }:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_ACCESS_STATUS_INVALID",
                f"{path}.access_status",
                "unrecognized source access status",
            )

        content_hash = source.get("content_hash", _MISSING)
        if isinstance(content_hash, dict):
            _reject_unexpected_fields(
                content_hash,
                {"state", "algorithm", "value"},
                f"{path}.content_hash",
                issues,
            )
            for field_name in ("state", "algorithm", "value"):
                if field_name not in content_hash:
                    _add_issue(
                        issues,
                        "BLOCKED",
                        "REQUIRED_INPUT_MISSING",
                        f"{path}.content_hash.{field_name}",
                        "required content-hash member is absent",
                    )
            hash_state = content_hash.get("state")
            if not isinstance(hash_state, str) or hash_state not in {"KNOWN", "UNKNOWN"}:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_HASH_STATE_INVALID",
                    f"{path}.content_hash.state",
                    "content-hash state must be KNOWN or UNKNOWN",
                )
            if content_hash.get("algorithm") != "SHA-256":
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_HASH_ALGORITHM_INVALID",
                    f"{path}.content_hash.algorithm",
                    "content-hash algorithm must be SHA-256",
                )
            hash_value = content_hash.get("value")
            if hash_state == "KNOWN" and (
                not isinstance(hash_value, str) or _SHA256_RE.fullmatch(hash_value) is None
            ):
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_HASH_VALUE_INVALID",
                    f"{path}.content_hash.value",
                    "KNOWN content hash requires 64 lowercase hexadecimal characters",
                )
            elif hash_state == "UNKNOWN" and hash_value is not None:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_HASH_UNKNOWN_MUST_BE_NULL",
                    f"{path}.content_hash.value",
                    "UNKNOWN content hash requires null",
                )
            if access_status == "AVAILABLE" and hash_state != "KNOWN":
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_AVAILABLE_HASH_REQUIRED",
                    f"{path}.content_hash.state",
                    "AVAILABLE source requires a KNOWN content hash",
                )
            if (
                isinstance(access_status, str)
                and access_status in {"MISSING", "UNKNOWN"}
                and hash_state != "UNKNOWN"
            ):
                _add_issue(
                    issues,
                    "BLOCKED",
                    "SOURCE_UNAVAILABLE_HASH_MUST_BE_UNKNOWN",
                    f"{path}.content_hash.state",
                    "MISSING or UNKNOWN source requires an UNKNOWN content hash",
                )
        else:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_HASH_INVALID",
                f"{path}.content_hash",
                "content_hash must be an object",
            )
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or _ID_RE.fullmatch(source_id) is None:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_ID_INVALID",
                f"{path}.source_id",
                "source_id must be a stable identifier",
            )
            continue
        if source_id in index:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_ID_DUPLICATE",
                f"{path}.source_id",
                "source identifiers must be unique",
            )
            continue
        index[source_id] = source
    return index, tuple(sorted(index))


def _gate_sources(
    source_index: Mapping[str, Mapping[str, Any]],
    required_refs: Iterable[str],
    issues: MutableSequence[_Issue],
) -> List[Dict[str, Any]]:
    output_refs: List[Dict[str, Any]] = []
    for source_id in sorted(set(required_refs)):
        path = f"$.sources[{source_id!r}]"
        source = source_index.get(source_id)
        if source is None:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_REF_UNRESOLVED",
                path,
                "required source reference is absent",
            )
            output_refs.append({"source_id": source_id, "version": None})
            continue
        version = source.get("version")
        normalized_version = version.strip() if isinstance(version, str) else ""
        if not normalized_version:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_VERSION_MISSING",
                f"{path}.version",
                "a non-empty source version is required",
            )
            version = None
        elif _is_floating_version(normalized_version):
            version = normalized_version
        else:
            version = normalized_version
        output_refs.append({"source_id": source_id, "version": version})

        classification = source.get("classification")
        if classification == "CONFLICT":
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_CONFLICT",
                f"{path}.classification",
                "conflicted evidence source blocks calculation status",
            )
        elif isinstance(classification, str) and classification in _UNCERTAIN_PROVENANCE:
            _add_issue(
                issues,
                "UNKNOWN",
                "SOURCE_TRUST_UNKNOWN",
                f"{path}.classification",
                "assumed, unverified derived, or unknown evidence cannot support PASS",
            )
        elif not isinstance(classification, str) or classification not in _TRUSTED_PROVENANCE:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_CLASSIFICATION_INVALID",
                f"{path}.classification",
                "unrecognized source classification",
            )

        if source.get("access_status") != "AVAILABLE":
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_UNAVAILABLE",
                f"{path}.access_status",
                "required source must be AVAILABLE",
            )
        content_hash = source.get("content_hash")
        known_hash = (
            isinstance(content_hash, dict)
            and content_hash.get("state") == "KNOWN"
            and content_hash.get("algorithm") == "SHA-256"
            and isinstance(content_hash.get("value"), str)
            and _SHA256_RE.fullmatch(content_hash["value"]) is not None
        )
        if not known_hash:
            _add_issue(
                issues,
                "BLOCKED",
                "SOURCE_HASH_UNVERIFIED",
                f"{path}.content_hash",
                "required source must have a KNOWN SHA-256",
            )
    return output_refs


def _first_reason(issues: Sequence[_Issue], severity: str) -> str:
    return next(item.code for item in issues if item.severity == severity)


def _unique_codes(issues: Sequence[_Issue], extra: Sequence[str] = ()) -> List[str]:
    result: List[str] = []
    for code in tuple(extra) + tuple(item.code for item in issues):
        if code not in result:
            result.append(code)
    return result


def _base_result(document: Any, input_hash: Optional[str]) -> Dict[str, Any]:
    calculation_inputs: Any = None
    if input_hash is not None:
        try:
            calculation_inputs = copy.deepcopy(document)
        except RecursionError:
            calculation_inputs = None
    return {
        "formula_version": FORMULA_VERSION,
        "rule_id": RULE_ID,
        "tool_name": TOOL_NAME,
        "tool_version": PACKAGE_VERSION,
        "package_version": PACKAGE_VERSION,
        "status": "BLOCKED",
        "reason_code": "CALCULATION_NOT_COMPLETED",
        "reason_codes": [],
        "input_hash": input_hash,
        "source_refs": [],
        "calculation_inputs": calculation_inputs,
        "calculation_policy": {
            "numeric_engine": "decimal.Decimal",
            "rounding_mode": ROUNDING_MODE,
            "significant_digits": ROUNDING_SIGNIFICANT_DIGITS,
            "current_capacity_basis": "max(abs(Imin), abs(Imax))",
            "effective_capacity": "min(rated_capacity * derating_factor, current_limit)",
        },
        "formulas": {
            "total_current": "sum(load.current[profile])",
            "output_power": "P = V * I",
            "input_power": "Pin = Pout / efficiency",
            "loss_power": "Ploss = Pin - Pout",
            "derated_capacity": "Irated * derating_factor",
            "capacity_utilization": "100 * max(abs(Imin), abs(Imax)) / effective_capacity",
            "capacity_margin": "effective_capacity - max(abs(Imin), abs(Imax))",
            "power_margin": "nominal_voltage * capacity_margin",
        },
        "metrics": {
            "nominal_voltage": _unknown("V"),
            "total_current": _unknown_profiles("A"),
            "output_power": _unknown_profiles("W", power=True),
            "input_power": _unknown_profiles("W", power=True),
            "loss_power": _unknown_profiles("W", power=True),
            "rated_capacity": _unknown("A"),
            "current_limit": _unknown("A"),
            "derating_factor": _unknown("1"),
            "derated_capacity": _unknown("A"),
            "effective_capacity": _unknown("A"),
            "capacity_power": _unknown("W"),
            "capacity_utilization": _unknown("%"),
            "capacity_margin": _unknown("A"),
            "power_margin": _unknown("W"),
            "efficiency": _unknown("1"),
        },
        "issues": [],
        "limitations": [
            "STATIC_ALGEBRA_ONLY",
            "NO_TRANSIENT_THERMAL_STABILITY_OR_PHYSICAL_VALIDATION",
            "MODULE_LOCAL_RESULT_CONTRACT_NOT_D1_RESPONSE",
            "ARRAY_ORDER_IS_HASH_SIGNIFICANT_BY_D1_CANONICALIZATION",
        ],
    }


def _calculate_rail_budget(document: Any) -> Dict[str, Any]:
    """Calculate one static rail budget and return a JSON-compatible result.

    The input envelope is module-local and versioned with ``schema_version``.
    Required members are ``rail``, ``loads``, ``capacity``, ``derating``, and
    ``sources``; ``efficiency`` is optional but its absence makes input/loss
    power and the overall assessment ``UNKNOWN``.  No exception is used as a
    substitute for a safety status.
    """

    issues: List[_Issue] = []
    try:
        input_hash = sha256_hexdigest(document)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        input_hash = None
        _add_issue(
            issues,
            "BLOCKED",
            "INPUT_HASH_UNCOMPUTABLE",
            "$",
            f"input cannot be bound to deterministic JSON: {exc}",
        )
    result = _base_result(document, input_hash)
    if input_hash is not None and result["calculation_inputs"] is None:
        _add_issue(
            issues,
            "BLOCKED",
            "INPUT_COPY_UNREPRESENTABLE",
            "$",
            "input is too deeply nested to preserve as an auditable result copy",
        )
    if not isinstance(document, dict):
        _add_issue(issues, "BLOCKED", "TYPE_OBJECT", "$", "rail-budget input must be an object")
        result["reason_code"] = _first_reason(issues, "BLOCKED")
        result["reason_codes"] = _unique_codes(issues)
        result["issues"] = [item.as_dict() for item in issues]
        return result
    allowed_top_level = {"schema_version", "rail", "loads", "capacity", "derating", "efficiency", "sources"}
    _reject_unexpected_fields(document, allowed_top_level, "$", issues)
    if document.get("schema_version") != "1.0.0":
        _add_issue(
            issues,
            "BLOCKED",
            "SCHEMA_VERSION_UNSUPPORTED",
            "$.schema_version",
            "expected rail-budget input schema version '1.0.0'",
        )

    source_index, _ = _source_index(document.get("sources", _MISSING), issues)
    required_refs: List[str] = []

    rail = _mapping(document.get("rail", _MISSING), "$.rail", issues)
    voltage: Optional[Decimal] = None
    voltage_minimum: Optional[Decimal] = None
    voltage_maximum: Optional[Decimal] = None
    if rail is not None:
        _reject_unexpected_fields(
            rail,
            {
                "schema_version",
                "entity_type",
                "rail_id",
                "nominal_voltage",
                "voltage_limits",
                "provenance",
                "source_refs",
            },
            "$.rail",
            issues,
        )
        if rail.get("schema_version") != "1.0.0":
            _add_issue(
                issues,
                "BLOCKED",
                "RAIL_SCHEMA_VERSION_INVALID",
                "$.rail.schema_version",
                "rail schema_version must be '1.0.0'",
            )
        if rail.get("entity_type") != "rail":
            _add_issue(
                issues,
                "BLOCKED",
                "RAIL_ENTITY_TYPE_INVALID",
                "$.rail.entity_type",
                "rail entity_type must be 'rail'",
            )
        rail_id = rail.get("rail_id")
        if not isinstance(rail_id, str) or _ID_RE.fullmatch(rail_id) is None:
            _add_issue(
                issues,
                "BLOCKED",
                "RAIL_ID_INVALID",
                "$.rail.rail_id",
                "rail_id must be a stable identifier",
            )
        voltage = _quantity(
            rail.get("nominal_voltage", _MISSING),
            "V",
            "$.rail.nominal_voltage",
            issues,
            unknown_code="VOLTAGE_UNKNOWN",
        )
        limits = _mapping(rail.get("voltage_limits", _MISSING), "$.rail.voltage_limits", issues)
        if limits is not None:
            _reject_unexpected_fields(
                limits,
                {"minimum", "maximum"},
                "$.rail.voltage_limits",
                issues,
            )
            voltage_minimum = _quantity(
                limits.get("minimum", _MISSING),
                "V",
                "$.rail.voltage_limits.minimum",
                issues,
                unknown_code="VOLTAGE_LIMIT_UNKNOWN",
            )
            voltage_maximum = _quantity(
                limits.get("maximum", _MISSING),
                "V",
                "$.rail.voltage_limits.maximum",
                issues,
                unknown_code="VOLTAGE_LIMIT_UNKNOWN",
            )
        required_refs.extend(_provenance_and_refs(rail, "$.rail", issues))

    loads = document.get("loads", _MISSING)
    load_values: Dict[str, List[Decimal]] = {name: [] for name in _PROFILE_NAMES}
    reverse_current_present = False
    if loads is _MISSING:
        _add_issue(issues, "BLOCKED", "REQUIRED_INPUT_MISSING", "$.loads", "loads array is absent")
    elif not isinstance(loads, list):
        _add_issue(issues, "BLOCKED", "TYPE_ARRAY", "$.loads", "expected an array")
    elif not loads:
        _add_issue(
            issues,
            "BLOCKED",
            "EMPTY_LOAD_SET",
            "$.loads",
            "an explicit load record is required; silent zero-load inference is forbidden",
        )
    else:
        observed_load_ids: set[str] = set()
        for load_index, load_value in enumerate(loads):
            path = f"$.loads[{load_index}]"
            load = _mapping(load_value, path, issues)
            if load is None:
                continue
            _reject_unexpected_fields(
                load,
                {
                    "load_id",
                    "rail_id",
                    "currents",
                    "backfeed_permitted",
                    "provenance",
                    "source_refs",
                },
                path,
                issues,
            )
            load_id = load.get("load_id")
            if not isinstance(load_id, str) or _ID_RE.fullmatch(load_id) is None:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "LOAD_ID_INVALID",
                    f"{path}.load_id",
                    "load_id must be a stable identifier",
                )
            elif load_id in observed_load_ids:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "LOAD_ID_DUPLICATE",
                    f"{path}.load_id",
                    "load identifiers must be unique",
                )
            else:
                observed_load_ids.add(load_id)
            load_rail_id = load.get("rail_id")
            if not isinstance(load_rail_id, str) or _ID_RE.fullmatch(load_rail_id) is None:
                _add_issue(
                    issues,
                    "BLOCKED",
                    "LOAD_RAIL_ID_INVALID",
                    f"{path}.rail_id",
                    "load rail_id must be a stable identifier",
                )
            if load_rail_id != (rail.get("rail_id") if rail is not None else None):
                _add_issue(
                    issues,
                    "BLOCKED",
                    "LOAD_RAIL_MISMATCH",
                    f"{path}.rail_id",
                    "load must reference the assessed rail",
                )
            backfeed = load.get("backfeed_permitted", False)
            if not isinstance(backfeed, bool):
                _add_issue(
                    issues,
                    "BLOCKED",
                    "BACKFEED_PERMISSION_INVALID",
                    f"{path}.backfeed_permitted",
                    "backfeed_permitted must be boolean",
                )
                backfeed = False
            currents = _mapping(load.get("currents", _MISSING), f"{path}.currents", issues)
            parsed_currents: Dict[str, Optional[Decimal]] = {name: None for name in _PROFILE_NAMES}
            if currents is not None:
                _reject_unexpected_fields(currents, _PROFILE_NAMES, f"{path}.currents", issues)
                for profile in _PROFILE_NAMES:
                    parsed = _quantity(
                        currents.get(profile, _MISSING),
                        "A",
                        f"{path}.currents.{profile}",
                        issues,
                        unknown_code="LOAD_CURRENT_UNKNOWN",
                    )
                    parsed_currents[profile] = parsed
                    if parsed is not None:
                        load_values[profile].append(parsed)
                        if parsed < 0:
                            reverse_current_present = True
                            if not backfeed:
                                _add_issue(
                                    issues,
                                    "FAIL",
                                    "NEGATIVE_CURRENT_NOT_PERMITTED",
                                    f"{path}.currents.{profile}.value",
                                    "negative current requires explicit backfeed_permitted=true",
                                )
                if all(parsed_currents[name] is not None for name in _PROFILE_NAMES):
                    minimum = parsed_currents["Imin"]
                    typical = parsed_currents["Ityp"]
                    maximum = parsed_currents["Imax"]
                    assert minimum is not None and typical is not None and maximum is not None
                    if not minimum <= typical <= maximum:
                        _add_issue(
                            issues,
                            "FAIL",
                            "CURRENT_PROFILE_ORDER_INVALID",
                            f"{path}.currents",
                            "expected Imin <= Ityp <= Imax",
                        )
            required_refs.extend(_provenance_and_refs(load, path, issues))

    capacity = _mapping(document.get("capacity", _MISSING), "$.capacity", issues)
    rated_capacity: Optional[Decimal] = None
    current_limit: Optional[Decimal] = None
    if capacity is not None:
        _reject_unexpected_fields(
            capacity,
            {
                "regulator_id",
                "rail_id",
                "rated_current",
                "current_limit",
                "provenance",
                "source_refs",
            },
            "$.capacity",
            issues,
        )
        regulator_id = capacity.get("regulator_id")
        if not isinstance(regulator_id, str) or _ID_RE.fullmatch(regulator_id) is None:
            _add_issue(
                issues,
                "BLOCKED",
                "REGULATOR_ID_INVALID",
                "$.capacity.regulator_id",
                "the single selected capacity source requires a stable regulator_id",
            )
        capacity_rail_id = capacity.get("rail_id")
        if not isinstance(capacity_rail_id, str) or _ID_RE.fullmatch(capacity_rail_id) is None:
            _add_issue(
                issues,
                "BLOCKED",
                "CAPACITY_RAIL_ID_INVALID",
                "$.capacity.rail_id",
                "capacity rail_id must be a stable identifier",
            )
        if capacity_rail_id != (rail.get("rail_id") if rail is not None else None):
            _add_issue(
                issues,
                "BLOCKED",
                "CAPACITY_RAIL_MISMATCH",
                "$.capacity.rail_id",
                "selected regulator capacity must reference the assessed rail",
            )
        rated_capacity = _quantity(
            capacity.get("rated_current", _MISSING),
            "A",
            "$.capacity.rated_current",
            issues,
            unknown_code="CAPACITY_UNKNOWN",
        )
        current_limit = _quantity(
            capacity.get("current_limit", _MISSING),
            "A",
            "$.capacity.current_limit",
            issues,
            unknown_code="CURRENT_LIMIT_UNKNOWN",
        )
        required_refs.extend(_provenance_and_refs(capacity, "$.capacity", issues))

    derating_record = _mapping(document.get("derating", _MISSING), "$.derating", issues)
    derating: Optional[Decimal] = None
    if derating_record is not None:
        _reject_unexpected_fields(
            derating_record,
            {"factor", "provenance", "source_refs"},
            "$.derating",
            issues,
        )
        derating = _quantity(
            derating_record.get("factor", _MISSING),
            "1",
            "$.derating.factor",
            issues,
            unknown_code="DERATING_UNKNOWN",
        )
        required_refs.extend(_provenance_and_refs(derating_record, "$.derating", issues))

    efficiency_record: Optional[Mapping[str, Any]]
    if "efficiency" in document:
        efficiency_record = _mapping(document["efficiency"], "$.efficiency", issues)
    else:
        efficiency_record = None
        _add_issue(
            issues,
            "UNKNOWN",
            "EFFICIENCY_UNKNOWN",
            "$.efficiency",
            "efficiency is absent; input and loss power remain UNKNOWN",
        )
    efficiency: Optional[Decimal] = None
    if efficiency_record is not None:
        _reject_unexpected_fields(
            efficiency_record,
            {"factor", "provenance", "source_refs"},
            "$.efficiency",
            issues,
        )
        efficiency = _quantity(
            efficiency_record.get("factor", _MISSING),
            "1",
            "$.efficiency.factor",
            issues,
            unknown_code="EFFICIENCY_UNKNOWN",
            optional=True,
        )
        required_refs.extend(_provenance_and_refs(efficiency_record, "$.efficiency", issues))

    result["source_refs"] = _gate_sources(source_index, required_refs, issues)

    if reverse_current_present:
        _add_issue(
            issues,
            "UNKNOWN",
            "REVERSE_EFFICIENCY_MODEL_REQUIRED",
            "$.loads",
            "forward efficiency cannot determine reverse-flow input or loss power",
        )

    if voltage is not None:
        result["metrics"]["nominal_voltage"] = _known(voltage, "V")
        if voltage <= 0:
            _add_issue(
                issues,
                "FAIL",
                "NON_POSITIVE_VOLTAGE",
                "$.rail.nominal_voltage.value",
                "static energized-rail calculation requires voltage greater than zero",
            )
    if voltage_minimum is not None and voltage_maximum is not None and voltage_minimum > voltage_maximum:
        _add_issue(
            issues,
            "FAIL",
            "VOLTAGE_LIMIT_ORDER_INVALID",
            "$.rail.voltage_limits",
            "minimum voltage limit exceeds maximum",
        )

    for value, path, code in (
        (rated_capacity, "$.capacity.rated_current.value", "CAPACITY_RANGE_INVALID"),
        (current_limit, "$.capacity.current_limit.value", "CURRENT_LIMIT_RANGE_INVALID"),
        (derating, "$.derating.factor.value", "DERATING_RANGE_INVALID"),
        (efficiency, "$.efficiency.factor.value", "EFFICIENCY_RANGE_INVALID"),
    ):
        if value is None:
            continue
        if value <= 0 or (code in {"DERATING_RANGE_INVALID", "EFFICIENCY_RANGE_INVALID"} and value > 1):
            _add_issue(issues, "FAIL", code, path, "value must be greater than zero and no greater than one" if code in {"DERATING_RANGE_INVALID", "EFFICIENCY_RANGE_INVALID"} else "value must be greater than zero")

    totals: Dict[str, Decimal] = {}
    if isinstance(loads, list) and loads and all(len(load_values[name]) == len(loads) for name in _PROFILE_NAMES):
        for profile in _PROFILE_NAMES:
            totals[profile] = sum(load_values[profile], Decimal(0))
            result["metrics"]["total_current"][profile] = _known(totals[profile], "A")

    output_power: Dict[str, Decimal] = {}
    if voltage is not None:
        for current_name, current in totals.items():
            power_name = _POWER_PROFILE_NAMES[current_name]
            output_power[power_name] = voltage * current
            result["metrics"]["output_power"][power_name] = _known(output_power[power_name], "W")

    if rated_capacity is not None:
        result["metrics"]["rated_capacity"] = _known(rated_capacity, "A")
    if current_limit is not None:
        result["metrics"]["current_limit"] = _known(current_limit, "A")
    if derating is not None:
        result["metrics"]["derating_factor"] = _known(derating, "1")
    if efficiency is not None:
        result["metrics"]["efficiency"] = _known(efficiency, "1")

    derated_capacity: Optional[Decimal] = None
    effective_capacity: Optional[Decimal] = None
    if rated_capacity is not None and derating is not None and rated_capacity > 0 and 0 < derating <= 1:
        derated_capacity = rated_capacity * derating
        result["metrics"]["derated_capacity"] = _known(derated_capacity, "A")
    if derated_capacity is not None and current_limit is not None and current_limit > 0:
        effective_capacity = min(derated_capacity, current_limit)
        result["metrics"]["effective_capacity"] = _known(effective_capacity, "A")
        if voltage is not None and voltage > 0:
            result["metrics"]["capacity_power"] = _known(voltage * effective_capacity, "W")

    worst_current: Optional[Decimal] = None
    if "Imin" in totals and "Imax" in totals:
        worst_current = max(abs(totals["Imin"]), abs(totals["Imax"]))
    if effective_capacity is not None and effective_capacity > 0 and worst_current is not None:
        utilization = Decimal(100) * worst_current / effective_capacity
        margin = effective_capacity - worst_current
        result["metrics"]["capacity_utilization"] = _known(utilization, "%")
        result["metrics"]["capacity_margin"] = _known(margin, "A")
        if voltage is not None and voltage > 0:
            result["metrics"]["power_margin"] = _known(voltage * margin, "W")

    if efficiency is not None and 0 < efficiency <= 1 and not reverse_current_present:
        for power_name, power in output_power.items():
            input_power = power / efficiency
            loss_power = input_power - power
            result["metrics"]["input_power"][power_name] = _known(input_power, "W")
            result["metrics"]["loss_power"][power_name] = _known(loss_power, "W")

    fail_codes: List[str] = []
    if (
        voltage is not None
        and voltage_minimum is not None
        and voltage_maximum is not None
        and not voltage_minimum <= voltage <= voltage_maximum
    ):
        fail_codes.append("VOLTAGE_OUT_OF_TOLERANCE")
        _add_issue(
            issues,
            "FAIL",
            "VOLTAGE_OUT_OF_TOLERANCE",
            "$.rail.nominal_voltage",
            "nominal voltage is outside the declared minimum/maximum limits",
        )
    if effective_capacity is not None and worst_current is not None and worst_current > effective_capacity:
        fail_codes.append("OVER_DERATED_CAPACITY")
        _add_issue(
            issues,
            "FAIL",
            "OVER_DERATED_CAPACITY",
            "$.loads",
            "worst-case absolute load current exceeds effective current capacity",
        )

    for item in issues:
        if item.severity == "FAIL" and item.code not in fail_codes:
            fail_codes.append(item.code)

    if any(item.severity == "BLOCKED" for item in issues):
        result["status"] = "BLOCKED"
        result["reason_code"] = _first_reason(issues, "BLOCKED")
    elif fail_codes:
        result["status"] = "FAIL"
        result["reason_code"] = fail_codes[0] if len(fail_codes) == 1 else "MULTIPLE_LIMIT_VIOLATIONS"
    elif any(item.severity == "UNKNOWN" for item in issues):
        result["status"] = "UNKNOWN"
        result["reason_code"] = _first_reason(issues, "UNKNOWN")
    else:
        result["status"] = "PASS"
        result["reason_code"] = "WITHIN_DERATED_CAPACITY_AND_LIMITS"

    result["reason_codes"] = _unique_codes(issues, fail_codes)
    result["issues"] = [item.as_dict() for item in issues]
    if isinstance(loads, list) and any(
        isinstance(load, dict) and load.get("backfeed_permitted") is True for load in loads
    ):
        result["limitations"].append("SIGNED_BACKFEED_STATIC_ALGEBRA_ONLY")
    if reverse_current_present:
        result["limitations"].append("REVERSE_EFFICIENCY_MODEL_NOT_DEFINED")
    if efficiency_record is None:
        result["limitations"].append("EFFICIENCY_NOT_PROVIDED")
    elif efficiency_record.get("provenance") == "ASSUMPTION":
        result["limitations"].append("EFFICIENCY_IS_ASSUMPTION")
    return result


def calculate_rail_budget(document: Any) -> Dict[str, Any]:
    """Calculate one static rail budget with fail-closed numeric handling."""

    try:
        with localcontext() as context:
            context.prec = _working_precision(document)
            return _calculate_rail_budget(document)
    except (ArithmeticError, OverflowError, ValueError, RecursionError) as exc:
        try:
            input_hash = sha256_hexdigest(document)
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            input_hash = None
        result = _base_result(document, input_hash)
        issue = _Issue(
            severity="BLOCKED",
            code="NUMERIC_RESULT_UNREPRESENTABLE",
            path="$.metrics",
            message=f"derived result cannot be represented as finite JSON: {exc}",
        )
        result["reason_code"] = issue.code
        result["reason_codes"] = [issue.code]
        result["issues"] = [issue.as_dict()]
        result["limitations"].append("NUMERIC_RESULT_OUTSIDE_JSON_RANGE")
        return result
