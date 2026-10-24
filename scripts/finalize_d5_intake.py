"""Finalize the REP-02 D5 real-project intake checkpoint.

The checkpoint is deliberately split in two: tool verification must PASS,
while the current REP-01 execution-readiness decision must remain BLOCKED
until the exact rail artifacts, regulator identity, datasheet, and model are
available.  No electrical downstream engine is executed by this script.

The sibling REP-01 repository is addressed by a stable root identifier plus
POSIX repository-relative paths.  Absolute workstation paths are never
written to evidence.  D3/D4 evidence is read-only; the current D4 manifest
file hash, internal hash, and Git blob OID are revalidated at run time.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
DEFAULT_SCENARIO = REPO_ROOT / "scenarios" / "REP02-D5" / "project-intake.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "evidence" / "REP02-D5"
DEFAULT_REP01_ROOT = REPO_ROOT.parent / "esp32-s3-24v-interface-board"
D4_MANIFEST = REPO_ROOT / "evidence" / "REP02-D4" / "release-manifest.json"
REP02_ROOT_ID = "REP-02-REPOSITORY"
REP01_ROOT_ID = "REP-01-LOCAL-WORKTREE"
EXPECTED_PACKAGE_VERSION = "0.6.0.dev0"
EXPECTED_D4_FILE_SHA256 = "5fcbf70de457386f220ca22e86f1168350bfb37219bb1a799bb51ec6f55b4176"
EXPECTED_D4_MANIFEST_HASH = "a0c758fcc74089a3f474559d7564775c6228d122c4cf40dad48411b474be69c9"
EXPECTED_D4_GIT_BLOB_OID = "12ef5c3aff27fbb0d27616fd615952a89b54a153"
EXPECTED_REP01_REVISION = "b335eec28c8cfb514c79e2ddd41be1b82a4b699b"
EXPECTED_REP01_ARTIFACTS = {
    "REP01_INTERFACE_CONTRACT": (
        "REP01-INTERFACE-CONTRACT-V1.2",
        "docs/interface-contract-v1.2.md",
        "8fbb2a737699a5b92a13cf4756dcd4a5266306d398a316612e997388794ac3f0",
    ),
    "REP01_OPEN_DECISIONS": (
        "REP01-OPEN-DECISIONS-V1.2",
        "docs/open-decisions-v1.2.md",
        "1be7d880f69b82830d55483c2aed932c4c390384bb3d17304447958e542d563c",
    ),
    "REP01_POWER_TREE_PLACEHOLDER": (
        "REP01-POWER-TREE-PLACEHOLDER",
        "engineering/power-tree/README.md",
        "110e497c8596e2f54857bd15075ae19c9b6df1915713b8ab332bb24cb02b9772",
    ),
    "REP01_REQUIREMENTS_TRACEABILITY": (
        "REP01-REQUIREMENTS-TRACEABILITY-V1.2",
        "requirements/requirements-traceability-v1.2.csv",
        "237691f6f9fd2bd244a8881ce05550e1ecd0a423d94e3bfb504944c23fe2cfe2",
    ),
}

OUTPUT_NAMES = (
    "intake-result.json",
    "test-results.json",
    "report.md",
    "checkpoint-manifest.json",
)

EXPECTED_GAP_ROLES = frozenset(
    {
        "REP01_CONFIGURATION",
        "REP01_POWER_PROFILE",
        "REP01_RAIL_DEMANDS",
        "REGULATOR_SELECTION",
        "REGULATOR_DATASHEET",
        "REGULATOR_MODEL",
    }
)
EXPECTED_SUPPORTING_ROLES = frozenset(
    {
        "REP01_INTERFACE_CONTRACT",
        "REP01_REQUIREMENTS_TRACEABILITY",
        "REP01_OPEN_DECISIONS",
        "REP01_POWER_TREE_PLACEHOLDER",
    }
)
EXPECTED_CAPABILITIES = frozenset(
    {"REP01_BINDING", "STATIC", "BUCK", "PIN", "TRANSIENT", "LOOP_STABILITY"}
)
EXPECTED_DOWNSTREAM = frozenset({"BUCK", "PIN", "TRANSIENT", "LOOP_STABILITY"})

D5_REQUIRED_PATHS = (
    "CHANGELOG.md",
    "MANIFEST.in",
    "README.md",
    "docs/QUICKSTART.md",
    "docs/README.md",
    "docs/REP02-D5/REP02-D5-project-intake-readiness.md",
    "evidence/README.md",
    "examples/nightstand/README.md",
    "examples/nightstand/ncs01-nightstand-v1.json",
    "pyproject.toml",
    "scenarios/REP02-D5/project-intake.json",
    "scripts/build_release.py",
    "scripts/finalize_d5_intake.py",
    "scripts/verify_release.py",
    "src/power_rail_validation/__init__.py",
    "src/power_rail_validation/cli.py",
    "src/power_rail_validation/ncs01.py",
    "src/power_rail_validation/project_intake/__init__.py",
    "src/power_rail_validation/project_intake/project_intake_contract.json",
    "src/power_rail_validation/project_intake/validator.py",
    "tests/test_cli.py",
    "tests/test_d5_finalizer.py",
    "tests/test_ncs01.py",
    "tests/test_project_intake.py",
)


class D5FinalizationError(RuntimeError):
    """Raised when a D5 checkpoint invariant is not satisfied."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if _strict_json_bytes(payload, "generated JSON") != value:
        raise D5FinalizationError("generated JSON did not round-trip exactly")
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise D5FinalizationError("cannot hash {}: {}".format(path, exc)) from exc
    return digest.hexdigest()


def _strict_json_bytes(payload: bytes, label: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError("non-finite JSON token: " + token)

    def unique_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key: " + key)
            output[key] = value
        return output

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_pairs,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise D5FinalizationError("{} is not strict JSON: {}".format(label, exc)) from exc

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise D5FinalizationError(label + " contains a non-finite number")
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item.values())
    return value


def _strict_json_file(path: Path) -> Any:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise D5FinalizationError("cannot read {}: {}".format(path, exc)) from exc
    return _strict_json_bytes(payload, path.as_posix())


def _relative(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(
            REPO_ROOT.resolve(strict=True)
        ).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise D5FinalizationError(
            "path is not a real repository-local file: " + str(path)
        ) from exc


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _validate_repo_source_file(path: Path) -> Path:
    repository = REPO_ROOT.resolve(strict=True)
    lexical = Path(os.path.abspath(str(path)))
    try:
        relative = lexical.relative_to(Path(os.path.abspath(str(REPO_ROOT))))
    except ValueError as exc:
        raise D5FinalizationError(
            "source path is outside the repository: " + str(path)
        ) from exc
    component = Path(os.path.abspath(str(REPO_ROOT)))
    for part in relative.parts:
        component = component / part
        if _is_link_or_reparse(component):
            raise D5FinalizationError(
                "source path must not contain a link or reparse point: "
                + str(component)
            )
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(repository)
    except (OSError, RuntimeError, ValueError) as exc:
        raise D5FinalizationError(
            "source path is not a real repository-local file: " + str(path)
        ) from exc
    if not resolved.is_file():
        raise D5FinalizationError("source path is not a regular file: " + str(path))
    return resolved


def _run(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    environment: Optional[Mapping[str, str]] = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=dict(environment) if environment is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="strict" if text else None,
        check=False,
    )


def _git_text(
    root: Path, arguments: Sequence[str], *, check: bool = True
) -> str:
    completed = _run(
        ["git", "-c", "core.quotepath=false", *arguments], cwd=root
    )
    if check and completed.returncode != 0:
        raise D5FinalizationError(
            "git {} failed for {}: {}".format(
                " ".join(arguments),
                root.name,
                (completed.stdout + completed.stderr).strip(),
            )
        )
    return completed.stdout.strip()


def _git_bytes(root: Path, arguments: Sequence[str]) -> bytes:
    completed = _run(
        ["git", "-c", "core.quotepath=false", *arguments], cwd=root, text=False
    )
    if completed.returncode != 0:
        diagnostics = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        raise D5FinalizationError(
            "git {} failed for {}: {}".format(
                " ".join(arguments), root.name, diagnostics.strip()
            )
        )
    return completed.stdout


def _git_status_lines(root: Path) -> List[str]:
    """Return porcelain-v1 lines without stripping their status columns."""

    completed = _run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "status",
            "--short",
            "--untracked-files=all",
        ],
        cwd=root,
    )
    if completed.returncode != 0:
        raise D5FinalizationError(
            "git status failed for {}: {}".format(
                root.name, (completed.stdout + completed.stderr).strip()
            )
        )
    return [line for line in completed.stdout.splitlines() if line]


def _validate_generated_at(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "generated-at must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("generated-at must include a UTC offset")
    return parsed.isoformat(timespec="seconds")


def _validate_output_root(path: Path) -> Path:
    candidate = Path(os.path.abspath(str(path.expanduser())))
    expected = Path(os.path.abspath(str(REPO_ROOT / "evidence" / "REP02-D5")))
    if os.path.normcase(str(candidate)) != os.path.normcase(str(expected)):
        raise D5FinalizationError(
            "output root must be exactly evidence/REP02-D5"
        )
    for component in (REPO_ROOT, REPO_ROOT / "evidence", expected):
        if _is_link_or_reparse(component):
            raise D5FinalizationError(
                "output root path must not contain a link or reparse point: "
                + str(component)
            )
    resolved = candidate.resolve()
    canonical = REPO_ROOT.resolve(strict=True) / "evidence" / "REP02-D5"
    if os.path.normcase(str(resolved)) != os.path.normcase(str(canonical)):
        raise D5FinalizationError(
            "resolved output root escaped the canonical evidence directory"
        )
    if resolved.exists() and not resolved.is_dir():
        raise D5FinalizationError(
            "output root must be a real directory: " + str(resolved)
        )
    return resolved


def _d5_source_paths() -> Tuple[Path, ...]:
    paths = {REPO_ROOT / relative for relative in D5_REQUIRED_PATHS}
    fixture_root = REPO_ROOT / "tests" / "fixtures" / "project_intake"
    if _is_link_or_reparse(fixture_root) or not fixture_root.is_dir():
        raise D5FinalizationError("missing D5 fixture tree")
    paths.update(
        path
        for path in fixture_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    missing = sorted(
        path.relative_to(REPO_ROOT).as_posix() for path in paths if not path.is_file()
    )
    if missing:
        raise D5FinalizationError(
            "D5 source inventory paths are missing: " + ", ".join(missing)
        )
    validated = tuple(_validate_repo_source_file(path) for path in paths)
    return tuple(sorted(validated, key=_relative))


def _hash_inventory(paths: Iterable[Path]) -> Dict[str, str]:
    return {_relative(path): _sha256(path) for path in paths}


def _run_test_command(pattern: str, label: str) -> Dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(SRC_ROOT)
    test_temp = Path(
        tempfile.mkdtemp(prefix=".rep02-d5-tests-", dir=str(REPO_ROOT))
    )
    environment["TMP"] = str(test_temp)
    environment["TEMP"] = str(test_temp)
    environment["TMPDIR"] = str(test_temp)
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        pattern,
        "-q",
    ]
    try:
        completed = _run(command, environment=environment)
    finally:
        shutil.rmtree(test_temp, ignore_errors=True)
    output = completed.stdout + completed.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?\b", output)
    if completed.returncode != 0:
        raise D5FinalizationError(label + " failed:\n" + output.strip())
    if match is None or int(match.group(1)) <= 0:
        raise D5FinalizationError(label + " did not report a positive test count")
    if re.search(r"(?m)^OK(?:\s|$)", output) is None:
        raise D5FinalizationError(label + " did not emit OK")
    skipped_match = re.search(r"skipped=(\d+)", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    if skipped:
        raise D5FinalizationError(label + " skipped {} test(s)".format(skipped))
    count = int(match.group(1))
    return {
        "id": label,
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -p {} -q".format(
            pattern
        ),
        "tests_run": count,
        "passed": count,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "status": "PASS",
    }


def _workspace_test_modules(source_inventory: Mapping[str, str]) -> List[Dict[str, str]]:
    tracked = set(_git_text(
        REPO_ROOT, ["ls-tree", "-r", "--name-only", "HEAD", "--", "tests"]
    ).splitlines())
    worktree = {
        _relative(path)
        for path in (REPO_ROOT / "tests").glob("test*.py")
        if path.is_file()
    }
    relative_paths = sorted(
        {
            path
            for path in tracked
            if Path(path).parent.as_posix() == "tests"
            and Path(path).name.startswith("test")
            and Path(path).suffix == ".py"
        }
        | worktree
        | {"tests/test_project_intake.py"}
    )
    records: List[Dict[str, str]] = []
    for relative in relative_paths:
        path = REPO_ROOT / relative
        if not path.is_file():
            raise D5FinalizationError("baseline test module is missing: " + relative)
        observed = _sha256(path)
        if relative in source_inventory:
            if source_inventory[relative] != observed:
                raise D5FinalizationError("D5 test source hash drifted: " + relative)
            binding = "D5_SOURCE_HASH_INVENTORY"
        elif relative in tracked:
            head_bytes = _git_bytes(REPO_ROOT, ["show", "HEAD:" + relative])
            if _sha256_bytes(head_bytes) != observed:
                raise D5FinalizationError(
                    "baseline test differs from HEAD without D5 inventory binding: "
                    + relative
                )
            binding = "REP02_HEAD"
        else:
            binding = "WORKTREE_TEST_HASH_INVENTORY"
        records.append(
            {
                "root_id": REP02_ROOT_ID,
                "relative_path": relative,
                "sha256": observed,
                "binding": binding,
            }
        )
    return records


def _run_explicit_tests(
    modules: Sequence[Mapping[str, str]], label: str
) -> Dict[str, Any]:
    relative_paths = [item["relative_path"] for item in modules]
    if not relative_paths:
        raise D5FinalizationError(label + " has an empty module allowlist")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(SRC_ROOT)
    test_temp = Path(
        tempfile.mkdtemp(prefix=".rep02-d5-tests-", dir=str(REPO_ROOT))
    )
    environment["TMP"] = str(test_temp)
    environment["TEMP"] = str(test_temp)
    environment["TMPDIR"] = str(test_temp)
    command = [sys.executable, "-m", "unittest", "-q", *relative_paths]
    try:
        completed = _run(command, environment=environment)
    finally:
        shutil.rmtree(test_temp, ignore_errors=True)
    output = completed.stdout + completed.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?\b", output)
    if completed.returncode != 0:
        raise D5FinalizationError(label + " failed:\n" + output.strip())
    if match is None or int(match.group(1)) <= 0:
        raise D5FinalizationError(label + " did not report a positive test count")
    if re.search(r"(?m)^OK(?:\s|$)", output) is None:
        raise D5FinalizationError(label + " did not emit OK")
    skipped_match = re.search(r"skipped=(\d+)", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    if skipped:
        raise D5FinalizationError(label + " skipped {} test(s)".format(skipped))
    count = int(match.group(1))
    return {
        "id": label,
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest -q <BYTE_BOUND_MODULE_ALLOWLIST>",
        "coverage": "ALL_TOP_LEVEL_TEST_MODULES_PRESENT_IN_WORKTREE",
        "module_count": len(relative_paths),
        "modules": list(modules),
        "tests_run": count,
        "passed": count,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "status": "PASS",
    }


def _python_39_ast(paths: Iterable[Path]) -> Dict[str, Any]:
    python_paths = sorted((path for path in paths if path.suffix == ".py"), key=_relative)
    if not python_paths:
        raise D5FinalizationError("Python 3.9 AST gate has an empty file set")
    for path in python_paths:
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(path), feature_version=(3, 9))
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            raise D5FinalizationError(
                "Python 3.9 AST failed for {}: {}".format(_relative(path), exc)
            ) from exc
    return {
        "id": "PYTHON-3.9-AST-D5",
        "files_checked": len(python_paths),
        "interpretation": "GRAMMAR_COMPATIBILITY_ONLY_NOT_A_PYTHON_3_9_RUNTIME_EXECUTION",
        "status": "PASS",
    }


def _descriptor_revision(document: Mapping[str, Any]) -> str:
    try:
        revision = document["rep01"]["git_revision"]
        if revision["state"] != "KNOWN":
            raise KeyError("state")
        value = revision["value"]
    except (KeyError, TypeError) as exc:
        raise D5FinalizationError(
            "canonical scenario has no known REP-01 revision"
        ) from exc
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise D5FinalizationError("REP-01 revision is not a full lowercase SHA-1")
    return value


def _validate_actual_scenario(document: Mapping[str, Any]) -> None:
    try:
        rep01 = document["rep01"]
        envelope = document["operating_envelope"]
        topology = document["topology"]
        regulator = document["regulator"]
    except KeyError as exc:
        raise D5FinalizationError("canonical scenario is missing a semantic field") from exc
    if document.get("descriptor_id") != "REP02-D5-REP01-ACTUAL-GAP" or document.get("scope") != "PROJECT":
        raise D5FinalizationError("canonical scenario identity or scope changed")
    if _descriptor_revision(document) != EXPECTED_REP01_REVISION:
        raise D5FinalizationError("canonical scenario REP-01 revision changed")
    expected_known = {
        "rail_id": "5V",
    }
    for field, value in expected_known.items():
        if rep01.get(field) != {"state": "KNOWN", "value": value}:
            raise D5FinalizationError("canonical scenario did not bind REP-01 IC-02 5V")
    if topology != {"state": "KNOWN", "value": "BUCK"}:
        raise D5FinalizationError("canonical scenario topology must be the derived BUCK slice")
    for field, value in (("vin_nom_v", 24), ("vout_target_v", 5)):
        state = envelope.get(field)
        if not isinstance(state, dict) or state.get("state") != "KNOWN" or state.get("value") != value or state.get("unit") != "V":
            raise D5FinalizationError("canonical scenario has an invalid known {}".format(field))
    for field in (
        "vin_min_v",
        "vin_max_v",
        "load_min_a",
        "load_nom_a",
        "load_max_a",
        "ambient_min_c",
        "ambient_max_c",
    ):
        state = envelope.get(field)
        if not isinstance(state, dict) or state.get("state") != "UNKNOWN":
            raise D5FinalizationError("{} must remain explicitly UNKNOWN".format(field))
    for field in ("configuration_id", "profile_id"):
        state = rep01.get(field)
        if not isinstance(state, dict) or state.get("state") != "UNKNOWN":
            raise D5FinalizationError("REP-01 {} must remain explicitly UNKNOWN".format(field))
    for field in ("manufacturer", "part_number"):
        state = regulator.get(field)
        if not isinstance(state, dict) or state.get("state") != "UNKNOWN":
            raise D5FinalizationError("regulator {} must remain explicitly UNKNOWN".format(field))
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_REP01_ARTIFACTS):
        raise D5FinalizationError("canonical supporting-artifact inventory changed")
    observed_artifacts = {
        item.get("role"): (
            item.get("artifact_id"),
            item.get("path"),
            item.get("sha256"),
        )
        for item in artifacts
        if isinstance(item, dict)
    }
    if observed_artifacts != EXPECTED_REP01_ARTIFACTS:
        raise D5FinalizationError("canonical supporting-artifact identities changed")


def _rep01_provenance(
    root: Path, document: Mapping[str, Any]
) -> Dict[str, Any]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir() or not (resolved / ".git").exists():
        raise D5FinalizationError("REP-01 root is not the expected Git worktree")
    revision = _descriptor_revision(document)
    if revision != EXPECTED_REP01_REVISION:
        raise D5FinalizationError("REP-01 revision is outside the reviewed checkpoint")
    observed_head = _git_text(resolved, ["rev-parse", "HEAD"])
    if observed_head != revision:
        raise D5FinalizationError(
            "REP-01 HEAD does not match the descriptor revision"
        )
    status_lines = _git_status_lines(resolved)
    if status_lines:
        raise D5FinalizationError(
            "REP-01 working tree must be clean for checkpoint binding"
        )
    branch = _git_text(resolved, ["branch", "--show-current"])

    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise D5FinalizationError("canonical scenario has no artifact array")
    artifacts: List[Dict[str, Any]] = []
    for record in raw_artifacts:
        if not isinstance(record, dict):
            raise D5FinalizationError("canonical scenario artifact is not an object")
        relative = record.get("path")
        expected = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise D5FinalizationError("canonical scenario artifact identity is incomplete")
        try:
            file_path = resolved.joinpath(*relative.split("/")).resolve(strict=True)
            file_path.relative_to(resolved)
        except (OSError, RuntimeError, ValueError) as exc:
            raise D5FinalizationError(
                "REP-01 artifact path is unsafe or unavailable: " + relative
            ) from exc
        current_hash = _sha256(file_path)
        committed_bytes = _git_bytes(resolved, ["show", revision + ":" + relative])
        committed_hash = _sha256_bytes(committed_bytes)
        if current_hash != expected or committed_hash != expected:
            raise D5FinalizationError(
                "REP-01 artifact is not byte-bound to the declared revision: " + relative
            )
        blob_oid = _git_text(resolved, ["rev-parse", revision + ":" + relative])
        artifacts.append(
            {
                "artifact_id": record.get("artifact_id"),
                "role": record.get("role"),
                "root_id": REP01_ROOT_ID,
                "relative_path": relative,
                "sha256": current_hash,
                "size_bytes": file_path.stat().st_size,
                "git_blob_oid": blob_oid,
                "head_byte_match": True,
            }
        )
    return {
        "root_id": REP01_ROOT_ID,
        "repository_id": "REP-01",
        "branch": branch,
        "revision": revision,
        "working_tree": "CLEAN",
        "artifact_binding_status": "PASS",
        "artifacts": sorted(artifacts, key=lambda item: str(item["relative_path"])),
    }


def _enforce_reviewed_d4_identity(observed: Mapping[str, Any]) -> None:
    expected = {
        "file_sha256": EXPECTED_D4_FILE_SHA256,
        "manifest_hash": EXPECTED_D4_MANIFEST_HASH,
        "git_blob_oid": EXPECTED_D4_GIT_BLOB_OID,
        "head_blob_oid": EXPECTED_D4_GIT_BLOB_OID,
        "tracked_at_current_head": True,
        "current_head_byte_match": True,
    }
    if dict(observed) != expected:
        raise D5FinalizationError("D4 reviewed checkpoint identity mismatch")


def _d4_identity_policy_self_check() -> Dict[str, Any]:
    valid: Dict[str, Any] = {
        "file_sha256": EXPECTED_D4_FILE_SHA256,
        "manifest_hash": EXPECTED_D4_MANIFEST_HASH,
        "git_blob_oid": EXPECTED_D4_GIT_BLOB_OID,
        "head_blob_oid": EXPECTED_D4_GIT_BLOB_OID,
        "tracked_at_current_head": True,
        "current_head_byte_match": True,
    }
    _enforce_reviewed_d4_identity(valid)
    mutations = 0
    for field in sorted(valid):
        forged = dict(valid)
        forged[field] = False if isinstance(valid[field], bool) else "0" * len(str(valid[field]))
        try:
            _enforce_reviewed_d4_identity(forged)
        except D5FinalizationError:
            mutations += 1
        else:
            raise D5FinalizationError(
                "D4 identity policy accepted a forged {}".format(field)
            )
    return {
        "id": "D4-REVIEWED-ANCHOR-FORGERY-REJECTION",
        "mutations_rejected": mutations,
        "status": "PASS",
    }


def _d4_anchor() -> Dict[str, Any]:
    manifest = _strict_json_file(D4_MANIFEST)
    if not isinstance(manifest, dict):
        raise D5FinalizationError("D4 manifest must be a JSON object")
    internal_hash = manifest.get("manifest_hash")
    if not isinstance(internal_hash, str) or re.fullmatch(r"[0-9a-f]{64}", internal_hash) is None:
        raise D5FinalizationError("D4 manifest has no valid internal hash")
    replay = dict(manifest)
    replay.pop("manifest_hash", None)
    observed_internal = _sha256_bytes(_canonical_bytes(replay))
    if observed_internal != internal_hash:
        raise D5FinalizationError("D4 manifest internal hash replay failed")
    relative = _relative(D4_MANIFEST)
    file_hash = _sha256(D4_MANIFEST)
    current_blob_oid = _git_text(REPO_ROOT, ["hash-object", relative])
    tracked = _run(
        ["git", "cat-file", "-e", "HEAD:" + relative], cwd=REPO_ROOT
    ).returncode == 0
    head_blob_oid: Optional[str] = None
    head_byte_match = False
    if tracked:
        head_blob_oid = _git_text(REPO_ROOT, ["rev-parse", "HEAD:" + relative])
        head_bytes = _git_bytes(REPO_ROOT, ["show", "HEAD:" + relative])
        head_byte_match = _sha256_bytes(head_bytes) == file_hash
    if manifest.get("verification_status") != "PASS":
        raise D5FinalizationError("D4 manifest verification status is not PASS")
    # Self-consistency is insufficient: a forged manifest could recompute its
    # own hash.  D5 therefore pins the reviewed D4 checkpoint at all three
    # identity layers and still recomputes each observation from current bytes.
    observed_identity = {
        "file_sha256": file_hash,
        "manifest_hash": internal_hash,
        "git_blob_oid": current_blob_oid,
        "head_blob_oid": head_blob_oid,
        "tracked_at_current_head": tracked,
        "current_head_byte_match": head_byte_match,
    }
    _enforce_reviewed_d4_identity(observed_identity)
    return {
        "root_id": REP02_ROOT_ID,
        "relative_path": relative,
        "file_sha256": file_hash,
        "manifest_hash": internal_hash,
        "internal_hash_verification": "PASS",
        "reviewed_anchor_identity_verification": "PASS",
        "git_blob_oid": current_blob_oid,
        "tracked_at_current_head": tracked,
        "head_blob_oid": head_blob_oid,
        "current_head_byte_match": head_byte_match,
        "historical_release_readiness": manifest.get("release_readiness"),
    }


def _validate_result(result: Mapping[str, Any], document: Mapping[str, Any]) -> None:
    if result.get("status") != "BLOCKED":
        raise D5FinalizationError("actual REP-01 intake must currently be BLOCKED")
    if result.get("package_version") != EXPECTED_PACKAGE_VERSION:
        raise D5FinalizationError(
            "D5 package version must be " + EXPECTED_PACKAGE_VERSION
        )
    if result.get("input_hash") != document.get("input_hash"):
        raise D5FinalizationError("validator input hash does not match the descriptor")
    if frozenset(result.get("requested_capabilities", [])) != EXPECTED_CAPABILITIES:
        raise D5FinalizationError("validator did not preserve the requested capability set")
    if frozenset(result.get("required_artifact_roles", [])) != EXPECTED_GAP_ROLES:
        raise D5FinalizationError("validator required-role set is incomplete")

    artifacts = result.get("artifact_results")
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        raise D5FinalizationError("validator did not return four supporting artifacts")
    roles = frozenset(item.get("role") for item in artifacts if isinstance(item, dict))
    if roles != EXPECTED_SUPPORTING_ROLES:
        raise D5FinalizationError("validator supporting-role set changed")
    if any(
        item.get("status") != "PASS"
        or item.get("expected_sha256") != item.get("observed_sha256")
        or item.get("expected_sha256") != item.get("committed_sha256")
        or item.get("committed_artifact_status") != "VERIFIED"
        or item.get("readiness_role") is not False
        for item in artifacts
        if isinstance(item, dict)
    ):
        raise D5FinalizationError("a supporting REP-01 artifact did not byte-verify")

    project_provenance = result.get("project_provenance")
    expected_project_provenance = {
        "status": "PASS",
        "reason_code": "PROJECT_GIT_PROVENANCE_VERIFIED",
        "revision": EXPECTED_REP01_REVISION,
        "working_tree": "CLEAN",
        "committed_artifacts_verified": 4,
    }
    if project_provenance != expected_project_provenance:
        raise D5FinalizationError("public engine did not verify PROJECT Git provenance")

    gaps = result.get("artifact_gaps")
    if not isinstance(gaps, list) or frozenset(
        item.get("role") for item in gaps if isinstance(item, dict)
    ) != EXPECTED_GAP_ROLES:
        raise D5FinalizationError("actual gap ledger is incomplete")

    downstream = result.get("downstream_evaluations")
    if not isinstance(downstream, list) or frozenset(
        item.get("capability") for item in downstream if isinstance(item, dict)
    ) != EXPECTED_DOWNSTREAM:
        raise D5FinalizationError("downstream evaluation ledger changed")
    if any(
        item.get("status") != "NOT_RUN"
        or item.get("reason_code") != "DOWNSTREAM_ENGINE_NOT_RUN"
        for item in downstream
        if isinstance(item, dict)
    ):
        raise D5FinalizationError("a downstream engine was reported as executed")

    issues = result.get("issues")
    if not isinstance(issues, list):
        raise D5FinalizationError("validator returned no issue ledger")
    counts: Dict[str, int] = {}
    for item in issues:
        if not isinstance(item, dict):
            raise D5FinalizationError("validator issue is not an object")
        reason = item.get("reason_code")
        if isinstance(reason, str):
            counts[reason] = counts.get(reason, 0) + 1
    expected_counts = {
        "REQUIRED_ARTIFACT_ROLE_MISSING": 6,
        "MANDATORY_SEMANTIC_UNKNOWN": 7,
        "REQUIRED_IDENTITY_UNKNOWN": 4,
    }
    if counts != expected_counts:
        raise D5FinalizationError(
            "actual-gap issue ledger changed: {}".format(counts)
        )
    required_limits = {
        "EXECUTION_READINESS_ONLY",
        "DOWNSTREAM_ENGINES_NOT_RUN",
        "NO_ARTIFACT_SEMANTIC_EXTRACTION",
        "DECLARED_ORIGIN_NOT_AUTHENTICATED",
        "DESCRIPTOR_HASH_IS_CANONICAL_JSON_NOT_SOURCE_FILE_BYTES",
    }
    if not required_limits.issubset(set(result.get("limitations", []))):
        raise D5FinalizationError("project-intake claim limitations are incomplete")


def _public_engine_result(
    document: Mapping[str, Any], scenario: Path, rep01_root: Path
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    try:
        from power_rail_validation.project_intake import (  # type: ignore
            descriptor_input_hash,
            validate_project_intake,
        )
    except Exception as exc:
        raise D5FinalizationError(
            "cannot import the public project-intake API: {}".format(exc)
        ) from exc
    if descriptor_input_hash(document) != document.get("input_hash"):
        raise D5FinalizationError("canonical descriptor self-hash is invalid")
    result = validate_project_intake(document, artifact_root=rep01_root)
    if not isinstance(result, dict):
        raise D5FinalizationError("public project-intake API did not return an object")
    _validate_result(result, document)

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(SRC_ROOT)
    command = [
        sys.executable,
        "-m",
        "power_rail_validation",
        "intake",
        str(scenario),
        "--artifact-root",
        str(rep01_root),
    ]
    completed = _run(command, environment=environment)
    if completed.returncode != 3:
        raise D5FinalizationError(
            "CLI intake did not return BLOCKED exit code 3: {}".format(
                (completed.stdout + completed.stderr).strip()
            )
        )
    if completed.stderr:
        raise D5FinalizationError("CLI intake wrote unexpected stderr")
    cli_result = _strict_json_bytes(completed.stdout.encode("utf-8"), "CLI intake output")
    if cli_result != result:
        raise D5FinalizationError("CLI and public API intake results differ")
    return result, {
        "id": "PUBLIC-API-CLI-PARITY",
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m power_rail_validation intake scenarios/REP02-D5/project-intake.json --artifact-root <REP-01-LOCAL-WORKTREE>",
        "expected_exit_code": 3,
        "observed_exit_code": completed.returncode,
        "result_status": result["status"],
        "byte_semantic_parity": "PASS",
        "status": "PASS",
    }


def _git_diff_check() -> Dict[str, Any]:
    tracked = _run(["git", "diff", "--check", "HEAD", "--", "."])
    if tracked.returncode != 0 or tracked.stdout or tracked.stderr:
        raise D5FinalizationError(
            "git diff --check HEAD -- . failed: "
            + (tracked.stdout + tracked.stderr).strip()
        )
    untracked = _git_text(
        REPO_ROOT, ["ls-files", "--others", "--exclude-standard"]
    ).splitlines()
    for relative in untracked:
        completed = _run(
            ["git", "diff", "--no-index", "--check", "--", "/dev/null", relative]
        )
        diagnostics = completed.stdout + completed.stderr
        if completed.returncode not in {0, 1} or diagnostics:
            raise D5FinalizationError(
                "untracked git diff --check failed for {}: {}".format(
                    relative, diagnostics.strip()
                )
            )
    return {
        "id": "GIT-DIFF-CHECK",
        "scope": "HEAD_TRACKED_DIFF_PLUS_EACH_UNTRACKED_FILE",
        "status": "PASS",
    }


def _head_source_binding(source_hashes: Mapping[str, str]) -> Dict[str, Any]:
    mismatches: List[str] = []
    for relative, expected_hash in sorted(source_hashes.items()):
        try:
            committed = _git_bytes(REPO_ROOT, ["show", "HEAD:" + relative])
        except D5FinalizationError:
            mismatches.append(relative)
            continue
        if _sha256_bytes(committed) != expected_hash:
            mismatches.append(relative)
    matched_count = len(source_hashes) - len(mismatches)
    return {
        "status": "PASS" if not mismatches else "BLOCKED",
        "reason_code": (
            "D5_SOURCE_INVENTORY_BYTE_BOUND_TO_HEAD"
            if not mismatches
            else "D5_SOURCE_INVENTORY_NOT_BYTE_BOUND_TO_HEAD"
        ),
        "inventory_count": len(source_hashes),
        "head_byte_match_count": matched_count,
        "head_mismatch_paths": mismatches,
    }


def _repo_provenance(
    source_hashes: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    if source_hashes is None:
        source_hashes = _hash_inventory(_d5_source_paths())
    source_binding = _head_source_binding(source_hashes)
    raw_status_lines = _git_status_lines(REPO_ROOT)
    # Canonical evidence is an output of this finalizer, not source
    # provenance.  Excluding it keeps first-run and same-input replay equal.
    status_lines = [
        line
        for line in raw_status_lines
        if not (line[3:] if len(line) >= 4 else line).startswith("evidence/REP02-D5/")
    ]
    d5_prefixes = (
        "docs/REP02-D5/",
        "docs/README.md",
        "docs/QUICKSTART.md",
        "evidence/README.md",
        "examples/nightstand/",
        "MANIFEST.in",
        "CHANGELOG.md",
        "README.md",
        "scenarios/REP02-D5/",
        "scripts/build_release.py",
        "scripts/finalize_d5_intake.py",
        "scripts/verify_release.py",
        "src/power_rail_validation/project_intake/",
        "src/power_rail_validation/ncs01.py",
        "tests/fixtures/project_intake/",
        "tests/test_project_intake.py",
        "tests/test_d5_finalizer.py",
        "tests/test_ncs01.py",
        "tests/test_cli.py",
        "pyproject.toml",
        "src/power_rail_validation/__init__.py",
        "src/power_rail_validation/cli.py",
    )
    d5_entries: List[str] = []
    outside_entries: List[str] = []
    for line in status_lines:
        path = line[3:] if len(line) >= 4 else line
        target = d5_entries if path.startswith(d5_prefixes) else outside_entries
        target.append(line)
    return {
        "root_id": REP02_ROOT_ID,
        "canonical_binding": "D5_SOURCE_HASH_INVENTORY",
        "live_commit_and_branch_excluded_from_canonical_payload": True,
        "working_tree": "DIRTY" if status_lines else "CLEAN",
        "status_entry_count": len(status_lines),
        "generated_d5_evidence_excluded_from_source_status": True,
        "d5_scope_status_entries": sorted(d5_entries),
        "outside_d5_scope_status_entries": sorted(outside_entries),
        "source_provenance": source_binding,
    }


def _report_text(
    *,
    generated_at: str,
    result: Mapping[str, Any],
    test_results: Mapping[str, Any],
    rep01: Mapping[str, Any],
    d4: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> str:
    artifact_lines = []
    for artifact in rep01["artifacts"]:
        artifact_lines.append(
            "| `{root_id}` | `{relative_path}` | `{role}` | `{sha256}` | PASS |".format(
                **artifact
            )
        )
    gap_lines = []
    for gap in result["artifact_gaps"]:
        gap_lines.append(
            "| `{}` | `{}` | `{}` |".format(
                gap["role"], gap["reason_code"], gap["gap_id"]
            )
        )
    unknown_paths = sorted(
        item["path"]
        for item in result["issues"]
        if item["reason_code"] in {"MANDATORY_SEMANTIC_UNKNOWN", "REQUIRED_IDENTITY_UNKNOWN"}
    )
    downstream_lines = [
        "| `{}` | `{}` | `{}` |".format(
            item["capability"], item["status"], item["reason_code"]
        )
        for item in result["downstream_evaluations"]
    ]
    focused_run = test_results["test_runs"][0]
    full_run = test_results["test_runs"][1]
    d5_source = provenance["source_provenance"]
    return "\n".join(
        [
            "# REP-02 D5 — Gerçek REP-01 Girdi Hazırlık Kontrol Noktası",
            "",
            "- Üretim zamanı: `{}`".format(generated_at),
            "- Araç doğrulaması: `PASS`",
            "- Gerçek proje yürütme hazırlığı: `{}`".format(result["status"]),
            "- REP-02 kaynak provenansı: `{}` / `{}`".format(
                d5_source["status"], d5_source["reason_code"]
            ),
            "- Elektriksel downstream motorları: `NOT_RUN`",
            "",
            "## Sonuç",
            "",
            "D5 girdi kapısı başarıyla çalıştı ve gerçek REP-01 snapshot'ındaki mevcut dört destek dosyasını tam commit, göreli yol ve SHA-256 ile doğruladı. Buna rağmen tek `5V` dilimi için proje konfigürasyonu, güç profili, ray-yük talepleri, exact regülatör seçimi, exact-part datasheet'i ve model bulunmadığından yürütme hazırlığı kasıtlı olarak `BLOCKED` kaldı. Bu `BLOCKED`, test başarısızlığı değildir ve hiçbir değer varsayımla doldurulmamıştır.",
            "",
            "`24 V` nominal giriş, `5 V` hedef ray ve `BUCK` topolojisi `docs/interface-contract-v1.2.md` içindeki IC-01/IC-02 kayıtlarından alınmıştır. `BUCK`, kaynakta yazan ‘24→5 V switching’ ifadesinden türetilmiş topoloji sınıflandırmasıdır; exact parça veya elektriksel uygunluk kararı değildir.",
            "",
            "## Doğrulanan REP-01 ankrajı",
            "",
            "- Root ID: `{}`".format(rep01["root_id"]),
            "- Revision: `{}`".format(rep01["revision"]),
            "- Branch: `{}`".format(rep01["branch"]),
            "- Working tree: `{}`".format(rep01["working_tree"]),
            "",
            "| Root ID | Repository-relative path | Role | SHA-256 | Byte binding |",
            "|---|---|---|---|---|",
            *artifact_lines,
            "",
            "## Açık zorunlu artifact kapıları",
            "",
            "| Role | Reason | Gap ID |",
            "|---|---|---|",
            *gap_lines,
            "",
            "Bilinmeyen zorunlu kimlik/semantik yollar: "
            + ", ".join("`{}`".format(path) for path in unknown_paths)
            + ".",
            "",
            "## Downstream iddia sınırı",
            "",
            "| Capability | Status | Reason |",
            "|---|---|---|",
            *downstream_lines,
            "",
            "Bu kontrol noktası buck güç katı hesabı, pin/gerilim uygunluğu, transient, loop stability, şematik/ERC, fiziksel test veya G1–G6 kapanışı iddia etmez.",
            "",
            "## Test ve tekrar üretilebilirlik",
            "",
            "- Odaklı D5 testleri: `{}/{} PASS`".format(
                focused_run["passed"], focused_run["tests_run"]
            ),
            "- Byte-bağlı D5 taban regresyonu: `{}/{} PASS`".format(
                full_run["passed"], full_run["tests_run"]
            ),
            "- Public API / CLI parity ve CLI `BLOCKED=3` exit code: `PASS`",
            "- Python 3.9 gramer kontrolü: `PASS` (runtime icrası değildir)",
            "- `git diff --check`: `PASS`",
            "- D4 iç manifest hash replay: `PASS`",
            "- D4 manifest SHA-256: `{}`".format(d4["file_sha256"]),
            "- D4 iç manifest hash: `{}`".format(d4["manifest_hash"]),
            "- D4 current Git blob OID: `{}`".format(d4["git_blob_oid"]),
            "",
            "## En kısa sonraki adım",
            "",
            "REP-01 içinde yalnız IC-02 `5V` rayı için sürümlü proje konfigürasyonu, sayısal giriş profili, tüketici bazlı yük talepleri ve exact buck seçimi oluşturulmalıdır. Ardından seçilen parçanın üretici datasheet'i ve yetkili modeli hash/provenans ile bağlanmalıdır. Girdi kapısı `PASS` vermeden elektriksel downstream motorları çalıştırılmamalıdır.",
            "",
            "Bu finalizer commit, tag, push, paket yayını veya fiziksel test gerçekleştirmedi.",
            "",
        ]
    )


def _publish(output_root: Path, payloads: Mapping[str, bytes]) -> None:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".rep02-d5-stage-", dir=str(output_root.parent))
    )
    try:
        for name, payload in payloads.items():
            if name not in OUTPUT_NAMES:
                raise D5FinalizationError("unexpected D5 output name: " + name)
            (staging / name).write_bytes(payload)
            if _sha256(staging / name) != _sha256_bytes(payload):
                raise D5FinalizationError("staged evidence hash mismatch: " + name)
        output_root.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_NAMES:
            os.replace(str(staging / name), str(output_root / name))
    except OSError as exc:
        raise D5FinalizationError("cannot publish D5 evidence: {}".format(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def finalize(
    *,
    generated_at: str,
    scenario: Path = DEFAULT_SCENARIO,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    rep01_root: Path = DEFAULT_REP01_ROOT,
) -> Dict[str, Any]:
    scenario_resolved = scenario.expanduser().resolve()
    if scenario_resolved != DEFAULT_SCENARIO.resolve():
        raise D5FinalizationError(
            "canonical finalization requires scenarios/REP02-D5/project-intake.json"
        )
    output_resolved = _validate_output_root(output_root)
    document = _strict_json_file(scenario_resolved)
    if not isinstance(document, dict):
        raise D5FinalizationError("canonical scenario must be a JSON object")
    _validate_actual_scenario(document)

    source_paths = _d5_source_paths()
    source_hashes = _hash_inventory(source_paths)
    ast_result = _python_39_ast(source_paths)
    rep01 = _rep01_provenance(rep01_root, document)
    d4 = _d4_anchor()
    d4_policy_check = _d4_identity_policy_self_check()
    result, parity = _public_engine_result(document, scenario_resolved, rep01_root)
    focused_tests = _run_test_command("test_project_intake.py", "D5-PROJECT-INTAKE-TESTS")
    baseline_modules = _workspace_test_modules(source_hashes)
    full_tests = _run_explicit_tests(
        baseline_modules, "D5-WORKSPACE-FULL-REGRESSION"
    )
    diff_check = _git_diff_check()
    provenance = _repo_provenance(source_hashes)

    issue_counts: Dict[str, int] = {}
    for issue in result["issues"]:
        reason = issue["reason_code"]
        issue_counts[reason] = issue_counts.get(reason, 0) + 1
    test_results = {
        "schema_version": "1.0.0",
        "deliverable": "REP02-D5-TEST-RESULTS",
        "generated_at": generated_at,
        "verification_status": "PASS",
        "execution_readiness": result["status"],
        "interpretation": "TEST_PASS_AND_PROJECT_READINESS_BLOCKED_ARE_INTENTIONALLY_DISTINCT",
        "package_version": result["package_version"],
        "python_runtime": platform.python_version(),
        "test_runs": [focused_tests, full_tests],
        "checks": [parity, ast_result, diff_check, d4_policy_check],
        "scenario_assertions": {
            "descriptor_input_hash": "PASS",
            "rep01_revision_binding": "PASS",
            "project_git_provenance": "PASS",
            "supporting_artifact_byte_binding": "PASS",
            "required_gap_ledger": "PASS",
            "no_default_substitution": "PASS",
            "downstream_not_run": "PASS",
            "d4_read_only_anchor_replay": "PASS",
        },
        "actual_gap_issue_counts": issue_counts,
        "claim_limits": result["limitations"],
    }
    intake_payload = _json_bytes(result)
    test_payload = _json_bytes(test_results)
    report = _report_text(
        generated_at=generated_at,
        result=result,
        test_results=test_results,
        rep01=rep01,
        d4=d4,
        provenance=provenance,
    )
    report_payload = report.encode("utf-8")

    output_hashes = {
        "intake-result.json": _sha256_bytes(intake_payload),
        "test-results.json": _sha256_bytes(test_payload),
        "report.md": _sha256_bytes(report_payload),
    }
    manifest: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "deliverable": "REP02-D5-CHECKPOINT-MANIFEST",
        "generated_at": generated_at,
        "verification_status": "PASS",
        "execution_readiness": {
            "status": result["status"],
            "reason_code": result["reason_code"],
            "candidate_scope": "REP01_IC02_5V_SINGLE_BUCK_SLICE",
            "known_operating_points": {
                "vin_nom_v": 24,
                "vout_target_v": 5,
            },
            "topology": {
                "value": "BUCK",
                "provenance": "DERIVED_FROM_REP01_INTERFACE_CONTRACT_IC02_24_TO_5V_SWITCHING",
            },
            "open_artifact_roles": sorted(EXPECTED_GAP_ROLES),
        },
        "test_summary": {
            "status": "PASS",
            "focused_tests_run": focused_tests["tests_run"],
            "focused_passed": focused_tests["passed"],
            "full_tests_run": full_tests["tests_run"],
            "full_passed": full_tests["passed"],
        },
        "scenario": {
            "root_id": REP02_ROOT_ID,
            "relative_path": _relative(scenario_resolved),
            "file_sha256": _sha256(scenario_resolved),
            "descriptor_input_hash": result["input_hash"],
        },
        "rep01_snapshot": rep01,
        "d4_read_only_anchor": d4,
        "rep02_git": provenance,
        "d5_source_hash_inventory": source_hashes,
        "d5_source_hash_inventory_count": len(source_hashes),
        "evidence_outputs": output_hashes,
        "hash_policy": {
            "algorithm": "SHA-256",
            "canonicalization": "SORTED_MINIFIED_UTF8_JSON",
            "file_hash_scope": "EXACT_FILE_BYTES",
            "manifest_hash_scope": "ALL_MANIFEST_FIELDS_EXCEPT_MANIFEST_HASH",
            "manifest_file_excluded_from_source_and_output_inventories": True,
        },
        "claim_limits": [
            "EXECUTION_READINESS_ONLY",
            "NO_ELECTRICAL_COMPLIANCE_CLAIM",
            "NO_HARDWARE_VALIDATION_CLAIM",
            "NO_G1_G6_CLOSURE_CLAIM",
            "DOWNSTREAM_ENGINES_NOT_RUN",
            "FINALIZER_PERFORMS_NO_COMMIT_TAG_PUSH_OR_PACKAGE_PUBLISH",
        ],
        "actions": {
            "scope": "FINALIZER_PROCESS_ONLY",
            "commit": False,
            "tag": False,
            "push": False,
            "package_publish": False,
            "downstream_electrical_execution": False,
            "physical_test": False,
        },
    }
    manifest["manifest_hash"] = _sha256_bytes(_canonical_bytes(manifest))
    manifest_payload = _json_bytes(manifest)
    replay = _strict_json_bytes(manifest_payload, "checkpoint-manifest.json")
    replay_hash = replay.pop("manifest_hash", None)
    if replay_hash != _sha256_bytes(_canonical_bytes(replay)):
        raise D5FinalizationError("D5 manifest self-hash replay failed")

    if _hash_inventory(source_paths) != source_hashes:
        raise D5FinalizationError("D5 source inventory drifted before publication")
    if _sha256(D4_MANIFEST) != d4["file_sha256"]:
        raise D5FinalizationError("D4 manifest bytes drifted before publication")
    if _rep01_provenance(rep01_root, document) != rep01:
        raise D5FinalizationError("REP-01 snapshot drifted before publication")

    payloads = {
        "intake-result.json": intake_payload,
        "test-results.json": test_payload,
        "report.md": report_payload,
        "checkpoint-manifest.json": manifest_payload,
    }
    _publish(output_resolved, payloads)
    for name, payload in payloads.items():
        if _sha256(output_resolved / name) != _sha256_bytes(payload):
            raise D5FinalizationError("published evidence hash mismatch: " + name)
    return {
        "status": "PASS",
        "execution_readiness": result["status"],
        "focused_tests_run": focused_tests["tests_run"],
        "full_tests_run": full_tests["tests_run"],
        "manifest_hash": manifest["manifest_hash"],
        "outputs": {
            _relative(output_resolved / name): _sha256(output_resolved / name)
            for name in OUTPUT_NAMES
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize fail-closed REP-02 D5 project-intake evidence."
    )
    parser.add_argument(
        "--generated-at",
        required=True,
        type=_validate_generated_at,
        help="explicit timezone-aware ISO-8601 timestamp",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=DEFAULT_SCENARIO,
        help="canonical actual-gap scenario",
    )
    parser.add_argument(
        "--rep01-root",
        type=Path,
        default=DEFAULT_REP01_ROOT,
        help="local REP-01 Git worktree used for byte verification",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="evidence directory (default: evidence/REP02-D5)",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = finalize(
            generated_at=args.generated_at,
            scenario=args.scenario,
            rep01_root=args.rep01_root,
            output_root=args.output_root,
        )
    except D5FinalizationError as exc:
        sys.stderr.write("D5 finalization failed: {}\n".format(exc))
        return 1
    except Exception as exc:  # pragma: no cover - final defensive boundary
        sys.stderr.write(
            "D5 finalization failed: {}: {}\n".format(type(exc).__name__, exc)
        )
        return 1
    sys.stdout.buffer.write(_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
