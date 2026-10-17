"""Deterministic JSON encoding used by REP-02 input hashes.

This module deliberately does *not* claim RFC 8785 / JCS conformance.  Its
contract is narrower and explicit: accepted JSON values are encoded as UTF-8,
object keys are sorted, insignificant whitespace is removed, non-ASCII text is
preserved, and non-finite floating point values are rejected.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Mapping


def _check_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite numbers are not valid contract JSON")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: JSON object keys must be strings")
            _check_json_value(item, f"{path}.{key}")
        return
    raise TypeError(f"{path}: unsupported JSON value type {type(value).__name__}")


def canonical_json_text(value: Any) -> str:
    """Return the REP-02 deterministic, minified, key-sorted JSON text."""

    _check_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return :func:`canonical_json_text` encoded as UTF-8."""

    return canonical_json_text(value).encode("utf-8")


def sha256_hexdigest(value: Any) -> str:
    """Return a lowercase SHA-256 hex digest of the canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def configuration_hash_payload(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Select the versioned, technical request material bound by its hash.

    ``handoff_id`` is intentionally excluded because it is a transport identity.
    Contract/rule/product versions and claim scope are included because changing
    any of them can change the meaning or permissible interpretation of results.
    """

    producer = request["producer"]
    return {
        "contract_version": request["contract_version"],
        "configuration_id": request["configuration_id"],
        "producer": {
            "repo": producer["repo"],
            "product_contract_version": producer["product_contract_version"],
            "rule_registry_version": producer["rule_registry_version"],
        },
        "collections": request["collections"],
        "claim_limits": request["claim_limits"],
    }


def configuration_sha256(request: Mapping[str, Any]) -> str:
    """Hash :func:`configuration_hash_payload` with the REP-02 JSON encoding."""

    return sha256_hexdigest(configuration_hash_payload(request))

