"""Deterministic, fail-closed static rail-budget calculations.

This package intentionally owns a module-local result contract.  D1 response
metrics currently do not admit watts, percentages, or dimensionless factors,
so rail-budget results must not be inserted into a D1 response without a
separately reviewed contract revision.
"""

from .calculator import (
    FORMULA_VERSION,
    PACKAGE_VERSION,
    ROUNDING_MODE,
    ROUNDING_SIGNIFICANT_DIGITS,
    RULE_ID,
    TOOL_NAME,
    calculate_rail_budget,
)

__all__ = [
    "FORMULA_VERSION",
    "PACKAGE_VERSION",
    "ROUNDING_MODE",
    "ROUNDING_SIGNIFICANT_DIGITS",
    "RULE_ID",
    "TOOL_NAME",
    "calculate_rail_budget",
]
