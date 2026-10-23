"""Offline, versioned REP-01/REP-03 file-API contract checks.

The public API validates descriptors and byte-exact local artifact bindings.
It never imports, calls, or mutates another repository, and it never reports a
live adapter as having run.
"""

from .file_api import (
    API_VERSION,
    DESCRIPTOR_SCHEMA_VERSION,
    ENGINE_VERSION,
    RULE_ID,
    SPEC_SHA256,
    SPEC_SOURCE_ID,
    TOOL_NAME,
    descriptor_input_hash,
    validate_file_api_descriptor,
    validate_file_api_file,
)

__all__ = [
    "API_VERSION",
    "DESCRIPTOR_SCHEMA_VERSION",
    "ENGINE_VERSION",
    "RULE_ID",
    "SPEC_SHA256",
    "SPEC_SOURCE_ID",
    "TOOL_NAME",
    "descriptor_input_hash",
    "validate_file_api_descriptor",
    "validate_file_api_file",
]
