"""Public API for REP-02 D1 data contracts."""

from .canonical import (
    canonical_json_bytes,
    canonical_json_text,
    configuration_hash_payload,
    configuration_sha256,
    sha256_hexdigest,
)
from .validation import (
    ContractValidationError,
    ValidationIssue,
    assert_valid,
    load_json,
    validate_document,
    validate_file,
)

__all__ = [
    "ContractValidationError",
    "ValidationIssue",
    "assert_valid",
    "canonical_json_bytes",
    "canonical_json_text",
    "configuration_hash_payload",
    "configuration_sha256",
    "load_json",
    "sha256_hexdigest",
    "validate_document",
    "validate_file",
]

