from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
FINALIZER_PATH = REPO_ROOT / "scripts" / "finalize_d5_intake.py"


def load_finalizer():
    spec = importlib.util.spec_from_file_location("rep02_d5_finalizer_test", FINALIZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load D5 finalizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class D5FinalizerSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.finalizer = load_finalizer()

    def test_canonical_provenance_excludes_live_commit_identity(self) -> None:
        provenance = self.finalizer._repo_provenance()

        self.assertNotIn("head", provenance)
        self.assertNotIn("branch", provenance)
        self.assertEqual("D5_SOURCE_HASH_INVENTORY", provenance["canonical_binding"])
        self.assertTrue(provenance["live_commit_and_branch_excluded_from_canonical_payload"])

    def test_output_root_is_exact_canonical_evidence_directory(self) -> None:
        expected = REPO_ROOT / "evidence" / "REP02-D5"
        self.assertEqual(expected.resolve(), self.finalizer._validate_output_root(expected))

        with tempfile.TemporaryDirectory(
            prefix=".tmp-d5-output-reject-", dir=REPO_ROOT.parent
        ) as directory:
            with self.assertRaisesRegex(
                self.finalizer.D5FinalizationError, "must be exactly"
            ):
                self.finalizer._validate_output_root(Path(directory))

        with mock.patch.object(
            self.finalizer,
            "_is_link_or_reparse",
            side_effect=lambda path: path.name == "REP02-D5",
        ):
            with self.assertRaisesRegex(
                self.finalizer.D5FinalizationError, "link or reparse point"
            ):
                self.finalizer._validate_output_root(expected)

    def test_source_inventory_rejects_linked_or_reparse_paths(self) -> None:
        with mock.patch.object(
            self.finalizer,
            "_is_link_or_reparse",
            side_effect=lambda path: path.name == "validator.py",
        ):
            with self.assertRaisesRegex(
                self.finalizer.D5FinalizationError, "link or reparse point"
            ):
                self.finalizer._d5_source_paths()

    def test_diff_check_rejects_untracked_trailing_whitespace(self) -> None:
        path = REPO_ROOT / ".tmp-d5-untracked-whitespace.txt"
        try:
            path.write_text("not clean \n", encoding="utf-8")
            with self.assertRaisesRegex(
                self.finalizer.D5FinalizationError,
                "untracked git diff --check failed",
            ):
                self.finalizer._git_diff_check()
        finally:
            path.unlink(missing_ok=True)

    def test_head_binding_rejects_status_invisible_ignored_source(self) -> None:
        ignored_parent = REPO_ROOT / "out"
        ignored_parent.mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="d5-ignored-", dir=ignored_parent) as directory:
                path = Path(directory) / "source.py"
                path.write_text("value = 1\n", encoding="utf-8")
                relative = path.relative_to(REPO_ROOT).as_posix()
                self.assertFalse(
                    any(relative in line for line in self.finalizer._git_status_lines(REPO_ROOT))
                )
                binding = self.finalizer._head_source_binding(
                    {relative: self.finalizer._sha256(path)}
                )

            self.assertEqual("BLOCKED", binding["status"])
            self.assertEqual(
                "D5_SOURCE_INVENTORY_NOT_BYTE_BOUND_TO_HEAD",
                binding["reason_code"],
            )
            self.assertEqual([relative], binding["head_mismatch_paths"])
        finally:
            try:
                ignored_parent.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
