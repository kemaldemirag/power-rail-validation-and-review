"""Build the REP-02 wheel and source distribution without ``build``.

The project declares ``setuptools.build_meta`` as its PEP 517 backend.  This
script calls that installed backend directly from a disposable source staging
directory, so stale ``build`` or ``*.egg-info`` directories in the checkout
cannot leak into a release.  Only the repository's exact ``dist`` directory
and the disposable staging directory are ever removed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Dict, Iterable, Iterator, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
TEMP_ROOT = REPO_ROOT / "build"
LOCK_FILE = TEMP_ROOT / ".rep02-release.lock"

_ROOT_FILES = (
    "pyproject.toml",
    "MANIFEST.in",
    "README.md",
    "CHANGELOG.md",
    "LICENSE.txt",
)
_SOURCE_TREES = ("src", "tests", "docs", "examples", "scenarios")


class ReleaseBuildError(RuntimeError):
    """Raised when a release cannot be built safely or completely."""


@contextmanager
def release_lock(timeout_seconds: float = 60.0) -> Iterator[None]:
    """Serialize build and verification processes that share ``dist``."""

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    attributes = getattr(TEMP_ROOT.lstat(), "st_file_attributes", 0)
    if TEMP_ROOT.is_symlink() or attributes & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        raise ReleaseBuildError(f"refusing to use linked temporary root: {TEMP_ROOT}")

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
                        raise ReleaseBuildError(
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
                        raise ReleaseBuildError(
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


def _ensure_expected_dist(path: Path) -> None:
    expected = DIST_DIR.resolve()
    if path.resolve() != expected:
        raise ReleaseBuildError(f"refusing to clean unexpected output directory: {path}")
    if path.is_symlink():
        raise ReleaseBuildError(f"refusing to clean symlinked output directory: {path}")


def _clean_dist(path: Path) -> None:
    _ensure_expected_dist(path)
    path.mkdir(parents=True, exist_ok=True)

    def make_writable_and_retry(function: object, target: str, exc_info: object) -> None:
        del exc_info
        os.chmod(target, stat.S_IWRITE)
        function(target)  # type: ignore[operator]

    for child in path.iterdir():
        attributes = getattr(child.lstat(), "st_file_attributes", 0)
        if child.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ReleaseBuildError(f"refusing to clean linked dist member: {child}")
        if child.is_dir():
            shutil.rmtree(child, onerror=make_writable_and_retry)
        else:
            child.chmod(stat.S_IWRITE)
            child.unlink()


def _stage_source(destination: Path) -> None:
    missing = [name for name in _ROOT_FILES if not (REPO_ROOT / name).is_file()]
    missing.extend(name for name in _SOURCE_TREES if not (REPO_ROOT / name).is_dir())
    if missing:
        raise ReleaseBuildError("release inputs are missing: " + ", ".join(sorted(missing)))

    for name in _ROOT_FILES:
        shutil.copy2(REPO_ROOT / name, destination / name)
    for name in _SOURCE_TREES:
        shutil.copytree(
            REPO_ROOT / name,
            destination / name,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "*.pyo",
                "*.egg-info",
                ".DS_Store",
                "Thumbs.db",
                "desktop.ini",
            ),
        )


def _invoke_backend(stage: Path, temporary_root: Path, hook: str) -> Dict[str, str]:
    result_path = temporary_root / (hook + "-result.json")
    backend_temp = temporary_root / "backend-temp"
    backend_temp.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for variable in ("TEMP", "TMP", "TMPDIR"):
        environment[variable] = str(backend_temp)

    runner = r'''import json
import pathlib
import sys

import setuptools
from setuptools import build_meta

hook_name, destination = sys.argv[1], sys.argv[2]
result_path = pathlib.Path(sys.argv[3])
setuptools_version = setuptools.__version__
artifact = getattr(build_meta, hook_name)(destination)
result_path.write_text(
    json.dumps({"artifact": artifact, "setuptools_version": setuptools_version}),
    encoding="utf-8",
)
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            runner,
            hook,
            str(DIST_DIR),
            str(result_path),
        ],
        cwd=stage,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        diagnostics = (completed.stdout + completed.stderr).strip()
        raise ReleaseBuildError(
            f"setuptools.build_meta.{hook} failed: {diagnostics}"
        )
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        diagnostics = (completed.stdout + completed.stderr).strip()
        raise ReleaseBuildError(
            f"{hook} did not publish a valid result: {exc}; {diagnostics}"
        ) from exc
    if not isinstance(value, dict):
        raise ReleaseBuildError(f"{hook} result is not an object")
    artifact = value.get("artifact")
    version = value.get("setuptools_version")
    if not isinstance(artifact, str) or not isinstance(version, str):
        raise ReleaseBuildError(f"{hook} result is incomplete")
    return {"artifact": artifact, "setuptools_version": version}


def _artifact_record(path: Path) -> Dict[str, object]:
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def build_release() -> Dict[str, object]:
    """Build one wheel and one sdist into the repository's ``dist`` folder."""

    with release_lock():
        _clean_dist(DIST_DIR)
        with tempfile.TemporaryDirectory(
            prefix=".tmp-release-", dir=TEMP_ROOT
        ) as temporary:
            temporary_root = Path(temporary)
            stage = temporary_root / "source"
            stage.mkdir()
            _stage_source(stage)
            sdist = _invoke_backend(stage, temporary_root, "build_sdist")
            wheel = _invoke_backend(stage, temporary_root, "build_wheel")

        sdist_name = sdist["artifact"]
        wheel_name = wheel["artifact"]
        if sdist["setuptools_version"] != wheel["setuptools_version"]:
            raise ReleaseBuildError("build hooks used different setuptools versions")

        names = (sdist_name, wheel_name)
        if any(Path(name).name != name for name in names):
            raise ReleaseBuildError("the build backend returned an unsafe artifact name")

        artifacts = [DIST_DIR / name for name in names]
        missing_outputs = [path.name for path in artifacts if not path.is_file()]
        if missing_outputs:
            raise ReleaseBuildError(
                "the build backend did not create: " + ", ".join(missing_outputs)
            )
        if len(list(DIST_DIR.glob("*.whl"))) != 1 or len(list(DIST_DIR.glob("*.tar.gz"))) != 1:
            raise ReleaseBuildError("expected exactly one wheel and one source distribution")

        return {
            "status": "PASS",
            "backend": "setuptools.build_meta",
            "setuptools_version": sdist["setuptools_version"],
            "artifacts": [_artifact_record(path) for path in sorted(artifacts)],
        }


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Build the REP-02 wheel and sdist with setuptools.build_meta."
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    build_parser().parse_args(argv)
    try:
        result = build_release()
    except ReleaseBuildError as exc:
        sys.stderr.write(f"release build failed: {exc}\n")
        return 1
    except Exception as exc:  # pragma: no cover - backend diagnostics are environment-specific
        sys.stderr.write(f"release build failed: {type(exc).__name__}: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
