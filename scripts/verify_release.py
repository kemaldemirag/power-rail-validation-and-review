"""Verify REP-02 distribution contents and an isolated wheel installation."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Set, Tuple
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
TEMP_ROOT = REPO_ROOT / "build"
LOCK_FILE = TEMP_ROOT / ".rep02-release.lock"

EXIT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "UNKNOWN": 2,
    "BLOCKED": 3,
    "NOT_RUN": 4,
}


class ReleaseVerificationError(RuntimeError):
    """Raised when a release invariant is not satisfied."""


@contextmanager
def _release_lock(timeout_seconds: float = 60.0) -> Iterator[None]:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    attributes = getattr(TEMP_ROOT.lstat(), "st_file_attributes", 0)
    if TEMP_ROOT.is_symlink() or attributes & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        raise ReleaseVerificationError(
            f"refusing to use linked temporary root: {TEMP_ROOT}"
        )

    with LOCK_FILE.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds

        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ReleaseVerificationError(
                            "timed out waiting for the release build lock"
                        ) from exc
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ReleaseVerificationError(
                            "timed out waiting for the release build lock"
                        ) from exc
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_name(name: str) -> str:
    normalized = name.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ReleaseVerificationError(f"unsafe archive member: {name!r}")
    return normalized


def _wheel_members(path: Path) -> Set[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ReleaseVerificationError(
                    f"wheel CRC verification failed at {bad_member!r}"
                )
            return {_archive_name(info.filename) for info in archive.infolist() if not info.is_dir()}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(f"cannot read wheel {path.name}: {exc}") from exc


def _sdist_members(path: Path) -> Set[str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            return {
                _archive_name(member.name)
                for member in archive.getmembers()
                if member.isfile()
            }
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseVerificationError(f"cannot read sdist {path.name}: {exc}") from exc


def _source_package_files() -> Set[str]:
    package = REPO_ROOT / "src" / "power_rail_validation"
    return {
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in package.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }


def _test_distribution_files() -> Set[str]:
    tests = REPO_ROOT / "tests"
    selected = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in tests.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    fixtures = tests / "fixtures"
    selected.update(
        path.relative_to(REPO_ROOT).as_posix()
        for path in fixtures.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    return selected


def _documentation_distribution_files() -> Set[str]:
    docs = REPO_ROOT / "docs"
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for path in docs.rglob("*.md")
        if path.is_file()
    }


def _require_members(label: str, actual: Set[str], expected: Set[str]) -> None:
    missing = sorted(expected - actual)
    if missing:
        preview = ", ".join(missing[:8])
        suffix = " ..." if len(missing) > 8 else ""
        raise ReleaseVerificationError(
            f"{label} is missing {len(missing)} required member(s): {preview}{suffix}"
        )


def _verify_inventory(wheel: Path, sdist: Path) -> Dict[str, object]:
    wheel_members = _wheel_members(wheel)
    source_members = _source_package_files()
    _require_members("wheel", wheel_members, source_members)

    package_data = {name for name in source_members if not name.endswith(".py")}
    _require_members("wheel package data", wheel_members, package_data)

    dist_info_roots = {
        name.split("/", 1)[0]
        for name in wheel_members
        if ".dist-info/" in name
    }
    if len(dist_info_roots) != 1:
        raise ReleaseVerificationError("wheel must contain exactly one .dist-info directory")
    dist_info = next(iter(dist_info_roots))
    _require_members(
        "wheel metadata",
        wheel_members,
        {
            f"{dist_info}/METADATA",
            f"{dist_info}/RECORD",
            f"{dist_info}/WHEEL",
            f"{dist_info}/licenses/LICENSE.txt",
        },
    )

    sdist_members = _sdist_members(sdist)
    roots = {name.split("/", 1)[0] for name in sdist_members}
    if len(roots) != 1:
        raise ReleaseVerificationError("sdist must contain exactly one top-level directory")
    root = next(iter(roots))
    quickstart = REPO_ROOT / "docs" / "QUICKSTART.md"
    if not quickstart.is_file():
        raise ReleaseVerificationError("repository is missing docs/QUICKSTART.md")
    expected_sdist = {
        f"{root}/src/{name}" for name in source_members
    } | {
        f"{root}/{name}" for name in _test_distribution_files()
    } | {
        f"{root}/{name}" for name in _documentation_distribution_files()
    } | {
        f"{root}/{name}"
        for name in (
            "pyproject.toml",
            "MANIFEST.in",
            "README.md",
            "CHANGELOG.md",
            "LICENSE.txt",
        )
    }
    _require_members("sdist", sdist_members, expected_sdist)

    return {
        "wheel_file_count": len(wheel_members),
        "wheel_package_file_count": len(source_members),
        "wheel_package_data_count": len(package_data),
        "sdist_file_count": len(sdist_members),
        "sdist_test_and_fixture_count": len(_test_distribution_files()),
        "sdist_documentation_count": len(_documentation_distribution_files()),
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"cannot load fixture {path}: {exc}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _reject_constant(token: str) -> None:
    raise ReleaseVerificationError(f"non-finite JSON token in CLI output: {token}")


def _strict_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseVerificationError(f"duplicate JSON key in CLI output: {key!r}")
        result[key] = value
    return result


def _ensure_finite(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise ReleaseVerificationError("non-finite number in CLI output")
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            pending.extend(current.values())


def _strict_json(data: bytes, label: str) -> Mapping[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
        decoder = json.JSONDecoder(
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
        value, end = decoder.raw_decode(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"{label} did not emit strict UTF-8 JSON: {exc}") from exc
    if text[end:].strip():
        raise ReleaseVerificationError(f"{label} emitted trailing non-JSON output")
    if not isinstance(value, dict):
        raise ReleaseVerificationError(f"{label} output must be a JSON object")
    _ensure_finite(value)
    return value


def _run(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _verify_smoke(
    python: Path,
    arguments: Sequence[str],
    expected_status: str,
    cwd: Path,
    environment: Mapping[str, str],
) -> Mapping[str, Any]:
    command = [str(python), "-m", "power_rail_validation", *arguments]
    first = _run(command, cwd, environment)
    second = _run(command, cwd, environment)
    if (first.returncode, first.stdout, first.stderr) != (
        second.returncode,
        second.stdout,
        second.stderr,
    ):
        raise ReleaseVerificationError(
            f"{' '.join(arguments[:1])} is not byte-deterministic across repeated runs"
        )
    if first.stderr:
        raise ReleaseVerificationError(
            f"{' '.join(arguments[:1])} wrote to stderr: "
            + first.stderr.decode("utf-8", errors="replace").strip()
        )
    result = _strict_json(first.stdout, arguments[0])
    status = result.get("status")
    if status != expected_status:
        raise ReleaseVerificationError(
            f"{arguments[0]} returned status {status!r}, expected {expected_status!r}"
        )
    expected_exit = EXIT_CODES[expected_status]
    if first.returncode != expected_exit:
        raise ReleaseVerificationError(
            f"{arguments[0]} returned exit {first.returncode}, expected {expected_exit}"
        )
    return result


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_console_script(root: Path) -> Path:
    return root / (
        "Scripts/power-rail-validation.exe"
        if os.name == "nt"
        else "bin/power-rail-validation"
    )


def _clean_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def _hydrate_fault_fixture() -> Dict[str, Any]:
    path = FIXTURES / "fault_injection" / "brownout.json"
    descriptor = _load_json(path)
    if not isinstance(descriptor, dict):
        raise ReleaseVerificationError("fault fixture must be a JSON object")
    value = copy.deepcopy(descriptor.get("input"))
    baseline = descriptor.get("baseline_fixtures")
    if not isinstance(value, dict) or not isinstance(baseline, dict):
        raise ReleaseVerificationError("fault fixture lacks input or baseline_fixtures")
    rail_path = (path.parent / str(baseline.get("rail_budget"))).resolve()
    sequence_path = (path.parent / str(baseline.get("sequencing"))).resolve()
    rail = _load_json(rail_path)
    sequence = _load_json(sequence_path)
    if not isinstance(rail, dict) or not isinstance(sequence, dict):
        raise ReleaseVerificationError("fault baseline fixtures must be JSON objects")
    value["rail_budget_input"] = rail.get("input")
    value["sequencing_input"] = sequence.get("input")
    return value


def _verify_install_and_cli(wheel: Path) -> Dict[str, object]:
    with tempfile.TemporaryDirectory(
        prefix=".tmp-verify-", dir=DIST_DIR
    ) as temporary:
        root = Path(temporary)
        environment = _clean_environment()
        process_temp = root / "temp"
        process_temp.mkdir()
        for variable in ("TEMP", "TMP", "TMPDIR"):
            environment[variable] = str(process_temp)
        venv_root = root / "venv"
        create_venv = _run(
            [sys.executable, "-m", "venv", str(venv_root)],
            root,
            environment,
        )
        if create_venv.returncode != 0:
            message = create_venv.stderr.decode("utf-8", errors="replace").strip()
            raise ReleaseVerificationError(
                f"isolated environment creation failed: {message}"
            )
        python = _venv_python(venv_root)
        if not python.is_file():
            raise ReleaseVerificationError("isolated environment has no Python executable")

        install = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel.resolve()),
            ],
            root,
            environment,
        )
        if install.returncode != 0:
            message = install.stderr.decode("utf-8", errors="replace").strip()
            raise ReleaseVerificationError(f"isolated wheel install failed: {message}")

        version = _run(
            [str(python), "-m", "power_rail_validation", "--version"],
            root,
            environment,
        )
        if version.returncode != 0 or version.stderr:
            raise ReleaseVerificationError("installed package --version check failed")
        try:
            version_text = version.stdout.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise ReleaseVerificationError("--version output is not UTF-8") from exc
        if not version_text.startswith("power-rail-validation "):
            raise ReleaseVerificationError(f"unexpected --version output: {version_text!r}")

        console = _venv_console_script(venv_root)
        if not console.is_file():
            raise ReleaseVerificationError(
                "wheel installation did not create the power-rail-validation console script"
            )
        console_version = _run(
            [str(console), "--version"], root, environment
        )
        if (
            console_version.returncode != 0
            or console_version.stderr
            or console_version.stdout != version.stdout
        ):
            raise ReleaseVerificationError(
                "console-script --version does not match the module entry point"
            )

        console_arguments = [
            str(console),
            "validate",
            str(FIXTURES / "contracts" / "valid.json"),
        ]
        console_first = _run(console_arguments, root, environment)
        console_second = _run(console_arguments, root, environment)
        if (
            console_first.returncode,
            console_first.stdout,
            console_first.stderr,
        ) != (
            console_second.returncode,
            console_second.stdout,
            console_second.stderr,
        ):
            raise ReleaseVerificationError(
                "console-script validate is not byte-deterministic"
            )
        if console_first.returncode != 0 or console_first.stderr:
            raise ReleaseVerificationError("console-script validate smoke check failed")
        console_result = _strict_json(console_first.stdout, "console-script validate")
        if console_result.get("status") != "PASS":
            raise ReleaseVerificationError(
                "console-script validate did not return PASS"
            )

        rail_descriptor = _load_json(FIXTURES / "rail_budget" / "nominal.json")
        sequence_descriptor = _load_json(FIXTURES / "sequencing" / "normal.json")
        if not isinstance(rail_descriptor, dict) or not isinstance(sequence_descriptor, dict):
            raise ReleaseVerificationError("rail and sequencing fixtures must be JSON objects")
        rail_input = root / "rail-budget.json"
        sequence_input = root / "sequence.json"
        fault_input = root / "fault.json"
        _write_json(rail_input, rail_descriptor.get("input"))
        _write_json(sequence_input, sequence_descriptor.get("input"))
        _write_json(fault_input, _hydrate_fault_fixture())

        outcomes: Dict[str, str] = {}
        _verify_smoke(
            python,
            ["validate", str(FIXTURES / "contracts" / "valid.json")],
            "PASS",
            root,
            environment,
        )
        outcomes["validate"] = "PASS"
        rail_result = _verify_smoke(
            python,
            ["rail-budget", str(rail_input)],
            "PASS",
            root,
            environment,
        )
        outcomes["rail-budget"] = "PASS"
        _verify_smoke(
            python,
            ["sequence", str(sequence_input)],
            "PASS",
            root,
            environment,
        )
        outcomes["sequence"] = "PASS"
        _verify_smoke(
            python,
            ["inject-fault", str(fault_input)],
            "FAIL",
            root,
            environment,
        )
        outcomes["inject-fault"] = "FAIL"
        _verify_smoke(
            python,
            [
                "review",
                str(FIXTURES / "regulator_review" / "nominal-synthetic.json"),
                "--artifact-root",
                str(REPO_ROOT),
            ],
            "PASS",
            root,
            environment,
        )
        outcomes["review"] = "PASS"

        report_input = root / "report-input.json"
        _write_json(report_input, rail_result)
        _verify_smoke(
            python,
            ["report", str(report_input)],
            "PASS",
            root,
            environment,
        )
        outcomes["report"] = "PASS"

        return {
            "version": version_text,
            "install_mode": "local-wheel --no-index --no-deps",
            "console_script": {
                "version_matches_module": True,
                "validate_status": "PASS",
                "deterministic_repetitions": 2,
            },
            "deterministic_repetitions_per_command": 2,
            "commands": outcomes,
        }


def _verify_release_unlocked() -> Dict[str, object]:
    wheels = sorted(DIST_DIR.glob("*.whl"))
    sdists = sorted(DIST_DIR.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseVerificationError(
            "dist must contain exactly one wheel and one .tar.gz source distribution"
        )
    wheel, sdist = wheels[0], sdists[0]
    inventory = _verify_inventory(wheel, sdist)
    smoke = _verify_install_and_cli(wheel)
    return {
        "status": "PASS",
        "artifacts": {
            "wheel": {"file": wheel.name, "sha256": _sha256(wheel)},
            "sdist": {"file": sdist.name, "sha256": _sha256(sdist)},
        },
        "inventory": inventory,
        "isolated_install_and_cli": smoke,
    }


def verify_release() -> Dict[str, object]:
    """Verify the current release while excluding concurrent dist mutation."""

    with _release_lock():
        return _verify_release_unlocked()


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify REP-02 release inventory and isolated wheel behavior."
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    build_parser().parse_args(argv)
    try:
        result = verify_release()
    except ReleaseVerificationError as exc:
        sys.stderr.write(f"release verification failed: {exc}\n")
        return 1
    except Exception as exc:  # pragma: no cover - environment diagnostics are platform-specific
        sys.stderr.write(f"release verification failed: {type(exc).__name__}: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
