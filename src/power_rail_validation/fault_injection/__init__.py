"""Deterministic WF-02-04 fault-injection orchestration."""

from .runner import (
    ENGINE_VERSION,
    RULE_SET_VERSION,
    TOOL_NAME,
    run_fault_scenario,
)

__all__ = [
    "ENGINE_VERSION",
    "RULE_SET_VERSION",
    "TOOL_NAME",
    "run_fault_scenario",
]
