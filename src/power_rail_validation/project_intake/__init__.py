"""Fail-closed intake validation for one REP-01 regulator/rail slice."""

from .validator import (
    DESCRIPTOR_SCHEMA_VERSION,
    ENGINE_VERSION,
    RULE_ID,
    descriptor_input_hash,
    validate_project_intake,
)

__all__ = [
    "DESCRIPTOR_SCHEMA_VERSION",
    "ENGINE_VERSION",
    "RULE_ID",
    "descriptor_input_hash",
    "validate_project_intake",
]
