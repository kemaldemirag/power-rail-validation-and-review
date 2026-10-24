"""Command-line interface for the REP-02 validation engines.

The CLI is intentionally a thin adapter.  It performs strict JSON loading,
delegates all domain decisions to the package's public APIs, writes one
deterministic JSON document to stdout, and reserves stderr for CLI failures.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Optional, Sequence

from . import __version__
from .contracts import sha256_hexdigest, validate_document
from .fault_injection import run_fault_scenario
from .integration import validate_file_api_descriptor
from .project_intake import validate_project_intake
from .rail_budget import calculate_rail_budget
from .regulator_review import review_regulator
from .reporting import ReportValidationError, build_report_model
from .sequencing import evaluate_sequencing


EXIT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "UNKNOWN": 2,
    "BLOCKED": 3,
    "NOT_RUN": 4,
}
EXIT_USAGE_OR_INPUT = 64
EXIT_INTERNAL = 70

_CONTRACT_RULE_ID = "REP02.CONTRACT.VALIDATION"
_CONTRACT_TOOL_NAME = "power-rail-validation-contracts"


class _CliInputError(ValueError):
    """A deterministic usage or input failure that belongs on stderr."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliInputError(message)


def _strict_object(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _CliInputError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_constant(token: str) -> None:
    raise _CliInputError(f"non-finite JSON number {token!r} is forbidden")


def _ensure_finite_numbers(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise _CliInputError("JSON number is outside the finite numeric range")
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise _CliInputError(
                    "JSON strings must contain only valid Unicode scalar values"
                ) from None
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())


def _read_stdin_utf8() -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    payload = buffer.read()
    if not isinstance(payload, bytes):
        raise _CliInputError("standard input did not provide bytes")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _CliInputError("standard input is not valid UTF-8") from None


def _write_utf8(stream: Any, value: str) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(value)
        return
    buffer.write(value.encode("utf-8", errors="strict"))
    buffer.flush()


def _load_json_input(source: str) -> Any:
    try:
        if source == "-":
            text = _read_stdin_utf8()
        else:
            text = Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _CliInputError(f"cannot read UTF-8 JSON input {source!r}") from None

    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite_constant,
        )
        _ensure_finite_numbers(value)
        return value
    except _CliInputError:
        raise
    except json.JSONDecodeError as exc:
        raise _CliInputError(
            f"malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None
    except (OverflowError, RecursionError, ValueError):
        raise _CliInputError("JSON input exceeds supported numeric or nesting limits") from None


def _contract_result(document: Any) -> Dict[str, Any]:
    input_hash = sha256_hexdigest(document)
    input_source = {
        "source_id": "SRC-CLI-CONTRACT-INPUT",
        "version": f"sha256-{input_hash}",
        "content_hash": {
            "algorithm": "SHA-256",
            "state": "KNOWN",
            "value": input_hash,
        },
    }
    issues = sorted(
        validate_document(document),
        key=lambda item: (item.path, item.code, item.message),
    )
    status = "BLOCKED" if issues else "PASS"
    reason_code = issues[0].code if issues else "CONTRACT_VALID"
    return {
        "rule_id": _CONTRACT_RULE_ID,
        "status": status,
        "reason_code": reason_code,
        "input_hash": input_hash,
        "tool_name": _CONTRACT_TOOL_NAME,
        "tool_version": __version__,
        "package_version": __version__,
        "source_refs": [input_source],
        "issues": [
            {
                "status": "BLOCKED",
                "reason_code": issue.code,
                "path": issue.path,
                "message": issue.message,
                "rule_id": _CONTRACT_RULE_ID,
                "source_refs": [input_source],
            }
            for issue in issues
        ],
        "limitations": ["SCHEMA_AND_CROSS_FIELD_VALIDATION_ONLY"],
    }


def _default_artifact_root(input_source: str) -> Optional[Path]:
    if input_source == "-":
        return None
    return Path(input_source).parent


def _execute(args: argparse.Namespace, document: Any) -> Mapping[str, Any]:
    if args.command == "validate":
        if args.kind == "contract":
            if args.artifact_root is not None:
                raise _CliInputError(
                    "--artifact-root is only valid with validate --kind file-api"
                )
            return _contract_result(document)
        artifact_root = args.artifact_root
        if artifact_root is None:
            artifact_root = _default_artifact_root(args.input)
        return validate_file_api_descriptor(document, artifact_root=artifact_root)

    if args.command == "rail-budget":
        return calculate_rail_budget(document)
    if args.command == "intake":
        return validate_project_intake(document, artifact_root=args.artifact_root)
    if args.command == "sequence":
        return evaluate_sequencing(document)
    if args.command == "inject-fault":
        return run_fault_scenario(document)
    if args.command == "review":
        return review_regulator(document, artifact_root=args.artifact_root)
    if args.command == "report":
        try:
            return build_report_model(document, generated_at=args.generated_at)
        except ReportValidationError as exc:
            raise _CliInputError(f"report input rejected: {exc}") from None
    raise RuntimeError("unreachable command dispatch")


def _write_json(value: Mapping[str, Any]) -> None:
    _write_utf8(
        sys.stdout,
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
    )


def _exit_code(value: Mapping[str, Any]) -> int:
    status = value.get("status")
    if not isinstance(status, str) or status not in EXIT_CODES:
        raise ValueError("command result has no supported status")
    return EXIT_CODES[status]


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="power-rail-validation",
        description="Run deterministic REP-02 power-rail validation engines.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate a D1 contract or a versioned file-API descriptor",
    )
    validate.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")
    validate.add_argument(
        "--kind",
        choices=("contract", "file-api"),
        default="contract",
        help="validation contract (default: contract)",
    )
    validate.add_argument(
        "--artifact-root",
        type=Path,
        help="artifact root for file-API byte verification (defaults to INPUT parent)",
    )

    rail_budget = subparsers.add_parser("rail-budget", help="calculate one static rail budget")
    rail_budget.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")

    intake = subparsers.add_parser(
        "intake", help="validate one REP-01 regulator/rail execution-readiness intake"
    )
    intake.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")
    intake.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="explicit root used for byte-exact artifact verification",
    )

    sequence = subparsers.add_parser("sequence", help="evaluate one sequencing scenario")
    sequence.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")

    inject_fault = subparsers.add_parser(
        "inject-fault", help="run one deterministic fault-injection scenario"
    )
    inject_fault.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")

    review = subparsers.add_parser("review", help="review one regulator input")
    review.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")
    review.add_argument(
        "--artifact-root",
        type=Path,
        help="root used to verify declared source artifact bytes",
    )

    report = subparsers.add_parser(
        "report", help="normalize component result JSON into the common report model"
    )
    report.add_argument("input", metavar="INPUT", help="UTF-8 JSON file, or '-' for stdin")
    report.add_argument(
        "--generated-at",
        help="explicit RFC 3339 report timestamp; omitted by default for deterministic output",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI and return its sysexits-compatible process status."""

    try:
        args = build_parser().parse_args(argv)
        document = _load_json_input(args.input)
        result = _execute(args, document)
        exit_code = _exit_code(result)
        _write_json(result)
        return exit_code
    except _CliInputError as exc:
        _write_utf8(sys.stderr, f"error: {exc}\n")
        return EXIT_USAGE_OR_INPUT
    except (BrokenPipeError, KeyboardInterrupt):
        raise
    except Exception as exc:  # pragma: no cover - exercised through a patched engine
        _write_utf8(sys.stderr, f"internal error: {type(exc).__name__}\n")
        return EXIT_INTERNAL


__all__ = [
    "EXIT_CODES",
    "EXIT_INTERNAL",
    "EXIT_USAGE_OR_INPUT",
    "build_parser",
    "main",
]
