"""Finalize REP-02 D4 local release-candidate evidence.

The finalizer is deliberately fail-closed.  It runs the complete discovered
unit-test suite, calls the public build and release-verification gates, checks
their artifact hashes against the bytes in ``dist``, and only then publishes
the four D4 evidence files.  A failed gate never replaces existing evidence.

``generated_at`` is caller-supplied so the evidence is reproducible rather
than silently depending on wall-clock time.  The D3 closeout manifest remains
read-only historical evidence; this script records its byte hash and
provenance state but never regenerates it or claims to replay its tests.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
DIST_ROOT = REPO_ROOT / "dist"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "evidence" / "REP02-D4"
D3_MANIFEST = REPO_ROOT / "evidence" / "REP02-D3" / "manifest-closeout.json"

OUTPUT_NAMES = (
    "test-results.json",
    "build-results.json",
    "release-manifest.json",
    "report.md",
)

REQUIRED_D4_PATHS = (
    ".gitattributes",
    ".gitignore",
    ".github/workflows/ci.yml",
    "CHANGELOG.md",
    "LICENSE.txt",
    "MANIFEST.in",
    "README.md",
    "docs/QUICKSTART.md",
    "docs/README.md",
    "evidence/README.md",
    "pyproject.toml",
    "scripts/build_release.py",
    "scripts/finalize_d4_release.py",
    "scripts/verify_release.py",
)

OPEN_LIMITS = (
    "SYNTHETIC_FIXTURES_ONLY",
    "SYNTHETIC_PASS_IS_NOT_PROJECT_OR_HARDWARE_PASS",
    "NO_LIVE_REP01_OR_REP03_INTEGRATION_CLAIM",
    "NO_ANALOG_TRANSIENT_OR_WAVEFORM_EXECUTION",
    "NO_PHYSICAL_HARDWARE_OR_LAB_VALIDATION",
    "NO_EMC_ISO_AEC_OR_FIELD_COMPLIANCE_CLAIM",
    "PYTHON_3_9_NOT_EXECUTED_IN_THIS_LOCAL_RUN_UNLESS_RECORDED_AS_CURRENT_RUNTIME",
    "NO_COMMIT_TAG_PUSH_OR_PACKAGE_PUBLISH",
)


class D4FinalizationError(RuntimeError):
    """Raised when a D4 evidence precondition or verification gate fails."""


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
    # Serialize and parse before publication so unsupported/non-finite values
    # cannot leak from an imported build helper into the evidence set.
    if _strict_json_bytes(payload, "generated JSON") != value:
        raise D4FinalizationError("generated JSON did not round-trip exactly")
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
        raise D4FinalizationError("cannot hash {}: {}".format(path, exc)) from exc
    return digest.hexdigest()


def _strict_json_bytes(payload: bytes, label: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError("non-finite JSON token: " + token)

    def unique_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_pairs,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise D4FinalizationError("{} is not strict JSON: {}".format(label, exc)) from exc

    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise D4FinalizationError(label + " contains a non-finite number")
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            pending.extend(current.values())
    return value


def _strict_json_file(path: Path) -> Any:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise D4FinalizationError("cannot read {}: {}".format(path, exc)) from exc
    return _strict_json_bytes(payload, path.as_posix())


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _run(
    command: Sequence[str],
    *,
    environment: Optional[Mapping[str, str]] = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        list(command),
        cwd=str(REPO_ROOT),
        env=dict(environment) if environment is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="strict" if text else None,
        check=False,
    )


def _git_text(arguments: Sequence[str], *, check: bool = True) -> str:
    completed = _run(["git", "-c", "core.quotepath=false", *arguments])
    if check and completed.returncode != 0:
        raise D4FinalizationError(
            "git {} failed: {}".format(
                " ".join(arguments),
                (completed.stdout + completed.stderr).strip(),
            )
        )
    return completed.stdout.strip()


def _validate_generated_at(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("generated-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("generated-at must include a UTC offset")
    return parsed.isoformat(timespec="seconds")


def _validate_output_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    repository = REPO_ROOT.resolve()
    if resolved == repository or resolved.parent == resolved:
        raise D4FinalizationError("unsafe output root: " + str(resolved))
    try:
        relative = resolved.relative_to(repository)
    except ValueError:
        relative = None
    if relative is not None and relative.as_posix() != "evidence/REP02-D4":
        raise D4FinalizationError(
            "repository-local output root must be evidence/REP02-D4"
        )
    if resolved.exists() and (resolved.is_symlink() or not resolved.is_dir()):
        raise D4FinalizationError("output root must be a real directory: " + str(resolved))
    return resolved


def _source_inventory_paths() -> Tuple[Path, ...]:
    paths = {REPO_ROOT / relative for relative in REQUIRED_D4_PATHS}
    for root in (SRC_ROOT, REPO_ROOT / "tests"):
        if not root.is_dir():
            raise D4FinalizationError("required tree is missing: " + _relative(root))
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not any(part.endswith(".egg-info") for part in path.parts)
            and path.name.casefold() not in {".ds_store", "thumbs.db", "desktop.ini"}
            and path.suffix not in {".pyc", ".pyo"}
        )
    missing = sorted(_relative(path) for path in paths if not path.is_file())
    if missing:
        raise D4FinalizationError("D4 inventory paths are missing: " + ", ".join(missing))
    return tuple(sorted(paths, key=lambda item: _relative(item)))


def _hash_inventory(paths: Iterable[Path]) -> Dict[str, str]:
    return {_relative(path): _sha256(path) for path in paths}


def _python_39_ast_check() -> Dict[str, Any]:
    paths = sorted(
        {
            *(
                path
                for path in SRC_ROOT.rglob("*.py")
                if "__pycache__" not in path.parts
                and not any(part.endswith(".egg-info") for part in path.parts)
            ),
            REPO_ROOT / "scripts" / "build_release.py",
            REPO_ROOT / "scripts" / "verify_release.py",
            REPO_ROOT / "scripts" / "finalize_d4_release.py",
        },
        key=_relative,
    )
    if not paths or any(not path.is_file() for path in paths):
        raise D4FinalizationError("Python 3.9 AST gate has a missing or empty file set")
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(path), feature_version=(3, 9))
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            raise D4FinalizationError(
                "Python 3.9 AST compatibility failed for {}: {}".format(
                    _relative(path), exc
                )
            ) from exc
    return {
        "id": "PYTHON-3.9-AST-COMPATIBILITY",
        "status": "PASS",
        "files_checked": len(paths),
        "scope": "src/**/*.py plus D4 build, verify, and finalizer scripts",
        "interpretation": "GRAMMAR_COMPATIBILITY_ONLY_NOT_A_PYTHON_3_9_RUNTIME_EXECUTION",
    }


def _run_full_tests(expected_tests: Optional[int]) -> Dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(SRC_ROOT)
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test*.py",
        "-q",
    ]
    completed = _run(command, environment=environment)
    output = completed.stdout + completed.stderr
    count_match = re.search(r"Ran\s+(\d+)\s+tests?\b", output)
    if completed.returncode != 0:
        raise D4FinalizationError("full unittest suite failed:\n" + output.strip())
    if count_match is None:
        raise D4FinalizationError("full unittest suite did not report its test count")
    observed = int(count_match.group(1))
    if observed <= 0:
        raise D4FinalizationError("full unittest discovery found no tests")
    if expected_tests is not None and observed != expected_tests:
        raise D4FinalizationError(
            "full unittest count mismatch: expected {}, observed {}".format(
                expected_tests, observed
            )
        )
    skipped_match = re.search(r"skipped=(\d+)", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    if skipped:
        raise D4FinalizationError(
            "full unittest suite skipped {} test(s); closeout requires an unqualified PASS".format(
                skipped
            )
        )
    if re.search(r"(?m)^OK(?:\s|$)", output) is None:
        raise D4FinalizationError("full unittest suite did not emit an OK result")
    return {
        "id": "FULL-UNITTEST-DISCOVERY",
        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -p test*.py -q",
        "tests_run": observed,
        "passed": observed,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "status": "PASS",
    }


def _call_release_gate(
    module_name: str,
    path: Path,
    function_name: str,
) -> Mapping[str, Any]:
    # Execute every gate in a fresh interpreter.  Apart from making this a
    # genuine command boundary, isolation prevents setuptools' process-global
    # build state and previously imported tests from contaminating a build.
    wrapper = r'''
import contextlib
import importlib.util
import io
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
function_name = sys.argv[2]
module_name = sys.argv[3]
spec = importlib.util.spec_from_file_location(module_name, str(path))
if spec is None or spec.loader is None:
    sys.stderr.write("cannot load release helper: " + str(path) + "\n")
    raise SystemExit(1)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    function = getattr(module, function_name)
except BaseException as exc:
    sys.stderr.write("release helper import failed: {}: {}\n".format(type(exc).__name__, exc))
    raise SystemExit(1)
captured_out = io.StringIO()
captured_err = io.StringIO()
try:
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        result = function()
except BaseException as exc:
    diagnostics = (captured_out.getvalue() + captured_err.getvalue()).strip()
    sys.stderr.write("release gate failed: {}: {}\n".format(type(exc).__name__, exc))
    if diagnostics:
        sys.stderr.write(diagnostics + "\n")
    raise SystemExit(1)
if not isinstance(result, dict):
    sys.stderr.write("release gate result is not an object\n")
    raise SystemExit(1)
sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
'''
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    completed = _run(
        [sys.executable, "-c", wrapper, str(path), function_name, module_name],
        environment=environment,
    )
    if completed.returncode != 0:
        raise D4FinalizationError(
            "{} command failed:\n{}".format(
                function_name,
                (completed.stdout + completed.stderr).strip(),
            )
        )
    checked = _strict_json_bytes(
        completed.stdout.encode("utf-8"), function_name + " result"
    )
    if not isinstance(checked, dict) or checked.get("status") != "PASS":
        raise D4FinalizationError(function_name + " did not return status PASS")
    return checked


def _artifact_map_from_build(result: Mapping[str, Any]) -> Dict[str, str]:
    records = result.get("artifacts")
    if not isinstance(records, list):
        raise D4FinalizationError("build result has no artifact list")
    artifacts: Dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            raise D4FinalizationError("build artifact record is not an object")
        name, digest = record.get("file"), record.get("sha256")
        if not isinstance(name, str) or Path(name).name != name:
            raise D4FinalizationError("build artifact has an unsafe file name")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise D4FinalizationError("build artifact has an invalid SHA-256")
        if name in artifacts:
            raise D4FinalizationError("duplicate build artifact: " + name)
        artifacts[name] = digest
    return artifacts


def _artifact_map_from_verify(result: Mapping[str, Any]) -> Dict[str, str]:
    records = result.get("artifacts")
    if not isinstance(records, dict):
        raise D4FinalizationError("verify result has no artifact map")
    artifacts: Dict[str, str] = {}
    for kind in ("wheel", "sdist"):
        record = records.get(kind)
        if not isinstance(record, dict):
            raise D4FinalizationError("verify result has no {} record".format(kind))
        name, digest = record.get("file"), record.get("sha256")
        if not isinstance(name, str) or Path(name).name != name:
            raise D4FinalizationError("verify artifact has an unsafe file name")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise D4FinalizationError("verify artifact has an invalid SHA-256")
        if name in artifacts:
            raise D4FinalizationError("duplicate verified artifact: " + name)
        artifacts[name] = digest
    return artifacts


def _verified_artifacts(
    build_result: Mapping[str, Any],
    verify_result: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    build_map = _artifact_map_from_build(build_result)
    verify_map = _artifact_map_from_verify(verify_result)
    wheels = sorted(DIST_ROOT.glob("*.whl"))
    sdists = sorted(DIST_ROOT.glob("*.tar.gz"))
    files = wheels + sdists
    if len(wheels) != 1 or len(sdists) != 1:
        raise D4FinalizationError(
            "dist must contain exactly one wheel and one .tar.gz source distribution"
        )
    actual = {path.name: _sha256(path) for path in files}
    if build_map != actual:
        raise D4FinalizationError("build-result hashes do not match dist artifact bytes")
    if verify_map != actual:
        raise D4FinalizationError("verify-result hashes do not match dist artifact bytes")
    return [
        {
            "path": _relative(path),
            "sha256": actual[path.name],
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(files, key=lambda item: item.name)
    ]


def _d3_provenance() -> Dict[str, Any]:
    manifest = _strict_json_file(D3_MANIFEST)
    if not isinstance(manifest, dict):
        raise D4FinalizationError("D3 closeout manifest must be a JSON object")
    recorded_hash = manifest.get("manifest_hash")
    if not isinstance(recorded_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded_hash):
        raise D4FinalizationError("D3 closeout manifest has no valid internal hash")
    hash_input = dict(manifest)
    hash_input.pop("manifest_hash", None)
    # The D3 hash policy explicitly excludes generated_at as well as the hash
    # field.  This verification reads historical bytes; it never rewrites them.
    hash_input.pop("generated_at", None)
    observed_hash = _sha256_bytes(_canonical_bytes(hash_input))
    if observed_hash != recorded_hash:
        raise D4FinalizationError("D3 closeout manifest internal hash is invalid")

    relative = _relative(D3_MANIFEST)
    tracked = _run(
        ["git", "cat-file", "-e", "HEAD:" + relative]
    ).returncode == 0
    head_byte_match = False
    if tracked:
        historical = _run(
            ["git", "show", "HEAD:" + relative],
            text=False,
        )
        if historical.returncode == 0:
            head_byte_match = _sha256_bytes(historical.stdout) == _sha256(D3_MANIFEST)

    baseline = manifest.get("baseline")
    if not isinstance(baseline, dict):
        baseline = {}
    recorded_working_tree = str(baseline.get("working_tree", "UNKNOWN"))
    recorded_commit = str(baseline.get("commit", "UNKNOWN"))
    bound = recorded_working_tree == "CLEAN" and tracked and head_byte_match
    return {
        "status": "PASS" if bound else "BLOCKED",
        "provenance_status": "COMMIT_BOUND" if bound else "UNCOMMITTED_SOURCE",
        "reason": (
            "D3_MANIFEST_IS_COMMIT_BOUND"
            if bound
            else "D3_CLOSEOUT_RECORDED_OR_CURRENT_BYTES_ARE_NOT_BOUND_TO_A_CLEAN_COMMIT"
        ),
        "path": relative,
        "file_sha256": _sha256(D3_MANIFEST),
        "manifest_hash": recorded_hash,
        "internal_hash_verification": "PASS",
        "recorded_commit": recorded_commit,
        "recorded_working_tree": recorded_working_tree,
        "tracked_at_current_head": tracked,
        "current_head_byte_match": head_byte_match,
        "replay_claim": "NOT_MADE",
    }


def _git_state(output_root: Path) -> Dict[str, Any]:
    head = _git_text(["rev-parse", "HEAD"])
    branch = _git_text(["rev-parse", "--abbrev-ref", "HEAD"])
    status_text = _git_text(["status", "--porcelain=v1", "--untracked-files=all"])
    entries = [line for line in status_text.splitlines() if line]
    try:
        output_relative = output_root.relative_to(REPO_ROOT.resolve()).as_posix() + "/"
    except ValueError:
        output_relative = ""
    if output_relative:
        entries = [
            line
            for line in entries
            if not line[3:].replace("\\", "/").startswith(output_relative)
        ]
    diff_check = _run(["git", "diff", "--check"])
    if diff_check.returncode != 0:
        raise D4FinalizationError(
            "git diff --check failed:\n" + (diff_check.stdout + diff_check.stderr).strip()
        )
    return {
        "head": head,
        "branch": branch,
        "working_tree": "DIRTY" if entries else "CLEAN",
        "dirty": bool(entries),
        "status_entry_count": len(entries),
        "status_entries": entries,
        "git_diff_check": "PASS",
    }


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def _package_version() -> str:
    init_path = SRC_ROOT / "power_rail_validation" / "__init__.py"
    text = init_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$", text)
    if match is None:
        raise D4FinalizationError("cannot read package __version__")
    return match.group(1)


def _project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^version\s*=\s*['\"]([^'\"]+)['\"]\s*$", text)
    if match is None:
        raise D4FinalizationError("cannot read [project] version from pyproject.toml")
    return match.group(1)


def _tool_versions() -> Dict[str, Any]:
    git_version = _git_text(["--version"])
    runtime = ".".join(str(part) for part in sys.version_info[:3])
    return {
        "operating_system": platform.system(),
        "platform": platform.platform(),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_runtime": runtime,
        "python_implementation": platform.python_implementation(),
        "minimum_supported_python": "3.9",
        "python_3_9_executed_locally": sys.version_info[:2] == (3, 9),
        "package_version": _package_version(),
        "project_version": _project_version(),
        "git": git_version,
        "pip": _distribution_version("pip"),
        "setuptools": _distribution_version("setuptools"),
        "wheel": _distribution_version("wheel"),
        "build": _distribution_version("build"),
    }


def _release_readiness(d3: Mapping[str, Any], tools: Mapping[str, Any]) -> Dict[str, Any]:
    blockers: List[str] = []
    if d3.get("status") != "PASS":
        blockers.append("D3_BASELINE_UNCOMMITTED_SOURCE")
    if not tools.get("python_3_9_executed_locally"):
        blockers.append("PYTHON_3_9_LOCAL_RUNTIME_NOT_EXECUTED")
    blockers.extend(
        [
            "LIVE_REP01_REP03_INTEGRATION_NOT_RUN",
            "PHYSICAL_HARDWARE_VALIDATION_NOT_RUN",
        ]
    )
    return {
        "status": "BLOCKED" if blockers else "PASS",
        "candidate_status": "LOCAL_SYNTHETIC_RELEASE_CANDIDATE_VERIFIED",
        "publish_status": "NOT_RUN",
        "blockers": blockers,
    }


def _report_markdown(
    *,
    generated_at: str,
    test_result: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    git_state: Mapping[str, Any],
    d3: Mapping[str, Any],
    tools: Mapping[str, Any],
    readiness: Mapping[str, Any],
    test_sha256: str,
    build_sha256: str,
    ast_check: Mapping[str, Any],
) -> bytes:
    artifact_lines = "\n".join(
        "- `{}` — `{}` ({} bayt)".format(
            record["path"], record["sha256"], record["size_bytes"]
        )
        for record in artifacts
    )
    limit_lines = "\n".join("- `{}`".format(item) for item in OPEN_LIMITS)
    blocker_lines = "\n".join(
        "- `{}`".format(item) for item in readiness["blockers"]
    ) or "- Yok"
    text = """# REP-02 D4 yerel sürüm adayı doğrulama kaydı

Oluşturulma: `{generated_at}`

Yerel D4 doğrulama sonucu: **PASS**

Yayına hazırlık: **{readiness_status}**

Aday durumu: `{candidate_status}`

## Gerçekleştirilen kapılar

- Tam `unittest` keşfi: **PASS** — {tests_run}/{tests_run} test geçti; hata, başarısızlık ve atlama yok.
- Python 3.9 AST dilbilgisi uyumluluğu: **PASS** — {ast_files} dosya. Bu statik kontrol gerçek bir Python 3.9 runtime koşusu değildir.
- Wheel ve kaynak dağıtımı oluşturma: **PASS**.
- Dağıtım içeriği, yalıtılmış kurulum, console-script ve altı CLI komutunun duman testi: **PASS**.
- `git diff --check`: **PASS**.

## Dağıtım artefaktları

{artifact_lines}

## Kaynak ve ortam izi

- Git HEAD: `{git_head}`
- Git dalı: `{git_branch}`
- Çalışma ağacı: `{working_tree}` ({status_count} kayıt; D4 kanıt çıktıları sayıma dahil değil)
- Python: `{python_runtime}` ({python_implementation})
- Paket sürümü: `{package_version}`
- D3 manifest dosya SHA-256: `{d3_file_sha256}`
- D3 iç manifest hash doğrulaması: `{d3_internal}`
- D3 kaynak bağı: **{d3_status} / {d3_provenance}**

D3 kanıtı yeniden çalıştırılmamış ve yeniden yazılmamıştır. Bu kayıt yalnızca mevcut D3 manifestinin kesin bayt hashini ve manifest iç hashini doğrular. D3 tabanı temiz bir commit'e bağlı değilse bu durum `UNCOMMITTED_SOURCE` olarak korunur.

## Yayına hazırlık engelleri

{blocker_lines}

## İddia sınırları

{limit_lines}

Bu PASS sonucu sentetik/çevrimdışı yazılım kapsamına aittir; canlı REP-01/REP-03, laboratuvar, donanım veya uygunluk sonucu değildir. Commit, etiket, push ve paket yayını yapılmamıştır.

## Kanıt bütünlüğü

- `test-results.json`: `{test_sha256}`
- `build-results.json`: `{build_sha256}`
- `release-manifest.json`, kendi dosya hashini envantere almaz. `manifest_hash`, belgenin `manifest_hash` alanı hariç tüm alanlarının sıralı, boşluksuz UTF-8 JSON gösterimi üzerinden hesaplanır.
""".format(
        generated_at=generated_at,
        readiness_status=readiness["status"],
        candidate_status=readiness["candidate_status"],
        tests_run=test_result["tests_run"],
        ast_files=ast_check["files_checked"],
        artifact_lines=artifact_lines,
        git_head=git_state["head"],
        git_branch=git_state["branch"],
        working_tree=git_state["working_tree"],
        status_count=git_state["status_entry_count"],
        python_runtime=tools["python_runtime"],
        python_implementation=tools["python_implementation"],
        package_version=tools["package_version"],
        d3_file_sha256=d3["file_sha256"],
        d3_internal=d3["internal_hash_verification"],
        d3_status=d3["status"],
        d3_provenance=d3["provenance_status"],
        blocker_lines=blocker_lines,
        limit_lines=limit_lines,
        test_sha256=test_sha256,
        build_sha256=build_sha256,
    )
    return text.encode("utf-8")


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(path))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _publish(output_root: Path, payloads: Mapping[str, bytes]) -> None:
    if set(payloads) != set(OUTPUT_NAMES):
        raise D4FinalizationError("internal evidence output inventory mismatch")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=False, exist_ok=True)
    if output_root.is_symlink():
        raise D4FinalizationError("refusing to publish through a symlinked output root")

    previous: Dict[str, Optional[bytes]] = {}
    for name in OUTPUT_NAMES:
        path = output_root / name
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise D4FinalizationError("unsafe existing evidence target: " + str(path))
        previous[name] = path.read_bytes() if path.is_file() else None

    try:
        with tempfile.TemporaryDirectory(prefix=".d4-stage-", dir=str(output_root.parent)) as temporary:
            stage = Path(temporary)
            for name in OUTPUT_NAMES:
                staged = stage / name
                staged.write_bytes(payloads[name])
                if _sha256(staged) != _sha256_bytes(payloads[name]):
                    raise D4FinalizationError("staged evidence hash mismatch: " + name)
            for name in OUTPUT_NAMES:
                os.replace(str(stage / name), str(output_root / name))
            for name in OUTPUT_NAMES:
                if _sha256(output_root / name) != _sha256_bytes(payloads[name]):
                    raise D4FinalizationError(
                        "published evidence hash mismatch: " + name
                    )
    except Exception:
        for name, payload in previous.items():
            path = output_root / name
            if payload is None:
                if path.exists() and path.is_file() and not path.is_symlink():
                    path.unlink()
            else:
                _write_bytes_atomic(path, payload)
        raise


def finalize(
    *,
    generated_at: str,
    output_root: Path,
    expected_tests: Optional[int],
) -> Dict[str, Any]:
    output_root = _validate_output_root(output_root)
    inventory_paths = _source_inventory_paths()
    source_hashes_before = _hash_inventory(inventory_paths)

    ast_check = _python_39_ast_check()
    test_run = _run_full_tests(expected_tests)
    build_result = _call_release_gate(
        "_rep02_d4_build_release",
        REPO_ROOT / "scripts" / "build_release.py",
        "build_release",
    )
    verify_result = _call_release_gate(
        "_rep02_d4_verify_release",
        REPO_ROOT / "scripts" / "verify_release.py",
        "verify_release",
    )
    artifacts = _verified_artifacts(build_result, verify_result)

    source_hashes_after = _hash_inventory(inventory_paths)
    if source_hashes_after != source_hashes_before:
        raise D4FinalizationError("D4 source inventory changed while gates were running")

    d3 = _d3_provenance()
    git_state = _git_state(output_root)
    tools = _tool_versions()
    if tools["package_version"] != tools["project_version"]:
        raise D4FinalizationError(
            "package __version__ and pyproject.toml project version do not match"
        )
    installed = verify_result.get("isolated_install_and_cli")
    installed_version = installed.get("version") if isinstance(installed, dict) else None
    expected_version = "power-rail-validation " + str(tools["package_version"])
    if installed_version != expected_version:
        raise D4FinalizationError(
            "isolated installed CLI version mismatch: expected {!r}, observed {!r}".format(
                expected_version, installed_version
            )
        )
    readiness = _release_readiness(d3, tools)

    test_document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "deliverable": "REP02-D4-LOCAL-RELEASE-VERIFICATION",
        "generated_at": generated_at,
        "verification_status": "PASS",
        "test_runs": [test_run],
        "static_checks": [ast_check],
        "source_inventory_file_count": len(source_hashes_after),
        "source_inventory_unchanged_during_gates": True,
        "environment": tools,
        "git": git_state,
        "claim_limits": list(OPEN_LIMITS),
    }
    test_payload = _json_bytes(test_document)

    build_document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "deliverable": "REP02-D4-BUILD-AND-INSTALL-VERIFICATION",
        "generated_at": generated_at,
        "verification_status": "PASS",
        "build": build_result,
        "verify": verify_result,
        "artifact_hash_consistency": "PASS",
        "artifacts": artifacts,
    }
    build_payload = _json_bytes(build_document)

    report_payload = _report_markdown(
        generated_at=generated_at,
        test_result=test_run,
        artifacts=artifacts,
        git_state=git_state,
        d3=d3,
        tools=tools,
        readiness=readiness,
        test_sha256=_sha256_bytes(test_payload),
        build_sha256=_sha256_bytes(build_payload),
        ast_check=ast_check,
    )

    manifest: MutableMapping[str, Any] = {
        "schema_version": "1.0.0",
        "deliverable": "REP02-D4-RELEASE-MANIFEST",
        "generated_at": generated_at,
        "verification_status": "PASS",
        "release_readiness": readiness,
        "git": git_state,
        "environment": tools,
        "d3_baseline": d3,
        "test_summary": {
            "status": test_run["status"],
            "tests_run": test_run["tests_run"],
            "passed": test_run["passed"],
        },
        "static_checks": [ast_check],
        "release_gates": {
            "python_3_9_ast_compatibility": ast_check["status"],
            "build": build_result["status"],
            "distribution_inventory": verify_result["status"],
            "isolated_install_and_cli": verify_result["status"],
            "artifact_hash_consistency": "PASS",
        },
        "artifacts": artifacts,
        "d4_file_hash_inventory": source_hashes_after,
        "d4_file_hash_inventory_count": len(source_hashes_after),
        "evidence_outputs": {
            "test-results.json": _sha256_bytes(test_payload),
            "build-results.json": _sha256_bytes(build_payload),
            "report.md": _sha256_bytes(report_payload),
        },
        "claim_limits": list(OPEN_LIMITS),
        "hash_policy": {
            "algorithm": "SHA-256",
            "canonicalization": "SORTED_MINIFIED_UTF8_JSON",
            "file_hash_scope": "EXACT_FILE_BYTES",
            "manifest_hash_scope": "ALL_MANIFEST_FIELDS_EXCEPT_MANIFEST_HASH",
            "manifest_file_excluded_from_d4_inventory": True,
        },
        "actions": {
            "commit": False,
            "tag": False,
            "push": False,
            "package_publish": False,
            "live_rep01_rep03_integration": False,
            "physical_test": False,
        },
    }
    manifest["manifest_hash"] = _sha256_bytes(_canonical_bytes(manifest))
    manifest_payload = _json_bytes(manifest)

    replay = _strict_json_bytes(manifest_payload, "release-manifest.json")
    replay_hash = replay.pop("manifest_hash", None)
    if replay_hash != _sha256_bytes(_canonical_bytes(replay)):
        raise D4FinalizationError("release manifest self-hash replay failed")

    # Revalidate every bound byte immediately before publication.  The
    # manifest itself is intentionally absent from this inventory, avoiding a
    # self-reference cycle.
    if _hash_inventory(inventory_paths) != source_hashes_after:
        raise D4FinalizationError("D4 source inventory drifted before evidence publication")
    current_artifact_hashes = {
        record["path"]: _sha256(REPO_ROOT / str(record["path"]))
        for record in artifacts
    }
    if any(
        current_artifact_hashes[record["path"]] != record["sha256"]
        for record in artifacts
    ):
        raise D4FinalizationError("release artifact bytes drifted before evidence publication")
    if _sha256(D3_MANIFEST) != d3["file_sha256"]:
        raise D4FinalizationError("D3 manifest bytes drifted before evidence publication")

    payloads = {
        "test-results.json": test_payload,
        "build-results.json": build_payload,
        "release-manifest.json": manifest_payload,
        "report.md": report_payload,
    }
    _publish(output_root, payloads)

    for name, payload in payloads.items():
        if _sha256(output_root / name) != _sha256_bytes(payload):
            raise D4FinalizationError("published evidence hash mismatch: " + name)

    return {
        "status": "PASS",
        "release_readiness": readiness["status"],
        "tests_run": test_run["tests_run"],
        "manifest_hash": manifest["manifest_hash"],
        "outputs": {
            _relative(output_root / name): _sha256(output_root / name)
            for name in OUTPUT_NAMES
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize fail-closed REP-02 D4 release-candidate evidence."
    )
    parser.add_argument(
        "--generated-at",
        required=True,
        type=_validate_generated_at,
        help="explicit timezone-aware ISO-8601 timestamp for deterministic evidence",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="evidence directory (default: evidence/REP02-D4)",
    )
    parser.add_argument(
        "--expected-tests",
        type=int,
        help="optional exact full-suite count; mismatch is a hard failure",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.expected_tests is not None and args.expected_tests <= 0:
        parser.error("--expected-tests must be positive")
    try:
        result = finalize(
            generated_at=args.generated_at,
            output_root=args.output_root,
            expected_tests=args.expected_tests,
        )
    except D4FinalizationError as exc:
        sys.stderr.write("D4 finalization failed: {}\n".format(exc))
        return 1
    except Exception as exc:  # pragma: no cover - defensive final boundary
        sys.stderr.write(
            "D4 finalization failed: {}: {}\n".format(type(exc).__name__, exc)
        )
        return 1
    sys.stdout.buffer.write(_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
