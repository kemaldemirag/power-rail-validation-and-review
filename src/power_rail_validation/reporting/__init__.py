"""Deterministic, fail-closed reporting for WF-02-06.

The reporting package normalizes module-local technical results without
pretending that they are D1 ``finding`` entities.  JSON and Markdown renderers
consume the exact same immutable report model.
"""

from .reporter import (
    MANIFEST_SCHEMA_VERSION,
    REPORTER_VERSION,
    REPORT_SCHEMA_VERSION,
    ReportValidationError,
    build_evidence_manifest,
    build_report_model,
    render_json_report,
    render_markdown_report,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "REPORTER_VERSION",
    "REPORT_SCHEMA_VERSION",
    "ReportValidationError",
    "build_evidence_manifest",
    "build_report_model",
    "render_json_report",
    "render_markdown_report",
]
