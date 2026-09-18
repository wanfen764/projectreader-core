from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projectreader.actions import WorkspaceChangeRejected
from projectreader.patching import (
    WorkspaceTransaction,
    WorkspaceTransactionApplyError,
    WorkspaceTransactionRestoreError,
    WorkspaceTransactionState,
)


class WorkspaceTransactionCoreTests(unittest.TestCase):
    def test_apply_and_restore_existing_created_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old.txt").write_text("old", encoding="utf-8")
            (root / "delete.txt").write_text("delete", encoding="utf-8")
            transaction = WorkspaceTransaction(root)
            transaction.apply(
                {
                    "old.txt": "new",
                    "nested/new.txt": "created",
                    "delete.txt": None,
                }
            )
            self.assertEqual((root / "old.txt").read_text(encoding="utf-8"), "new")
            self.assertTrue((root / "nested/new.txt").is_file())
            self.assertFalse((root / "delete.txt").exists())
            transaction.restore()
            self.assertEqual((root / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((root / "nested").exists())
            self.assertEqual(
                (root / "delete.txt").read_text(encoding="utf-8"), "delete"
            )
            self.assertIs(transaction.state, WorkspaceTransactionState.RESTORED)

    def test_preflight_is_pure_typed_and_path_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "folder").mkdir()
            transaction = WorkspaceTransaction(root)
            before = tuple(root.rglob("*"))
            plan = transaction.preflight_changes({"new/child.txt": "x"})
            self.assertFalse((root / "new").exists())
            self.assertEqual(tuple(root.rglob("*")), before)
            with self.assertRaises(FrozenInstanceError):
                plan.paths[0].relative_path = "changed"

            cases = (
                ({"../escape.txt": "x"}, "path_outside_repository"),
                ({str(root / "absolute.txt"): "x"}, "path_not_repository_relative"),
                ({"folder": "x"}, "target_is_directory"),
                ({}, "replacement_set_empty"),
            )
            for changes, reason in cases:
                with self.subTest(reason=reason):
                    with self.assertRaises(WorkspaceChangeRejected) as raised:
                        transaction.preflight_changes(changes)
                    self.assertEqual(raised.exception.reason_code, reason)
                    self.assertFalse(raised.exception.as_error()["side_effects"])

    def test_prepare_freezes_bytes_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app.txt"
            target.write_text("old", encoding="utf-8")
            changes = {"app.txt": "new"}
            transaction = WorkspaceTransaction(root)
            transaction.prepare(changes)
            changes["app.txt"] = "caller mutation"
            frozen = transaction.frozen_changes()
            self.assertEqual(frozen[0].original_bytes, b"old")
            self.assertEqual(frozen[0].desired_bytes, b"new")
            transaction.apply_prepared()
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_apply_failure_compensates_attempted_files_and_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            # Match the transaction's canonical paths even for aliased temp roots.
            root = Path(directory).resolve()
            blocker = root / "z.txt"
            blocker.write_text("old-z", encoding="utf-8")
            original_write = Path.write_bytes
            transaction = WorkspaceTransaction(root)

            def fail_later(path, content):
                if path == blocker and content == b"new-z":
                    raise OSError("injected apply failure")
                return original_write(path, content)

            with patch.object(Path, "write_bytes", new=fail_later):
                with self.assertRaises(WorkspaceTransactionApplyError) as raised:
                    transaction.apply(
                        {"a/b/new.txt": "created", "z.txt": "new-z"}
                    )
            self.assertIs(
                transaction.state,
                WorkspaceTransactionState.APPLY_FAILED_RESTORED,
            )
            self.assertFalse((root / "a").exists())
            self.assertEqual(blocker.read_text(encoding="utf-8"), "old-z")
            self.assertTrue(raised.exception.report.repository_restored)

    def test_restore_failure_reports_partial_state(self):
        with tempfile.TemporaryDirectory() as directory:
            # Match the transaction's canonical paths even for aliased temp roots.
            root = Path(directory).resolve()
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("old-a", encoding="utf-8")
            second.write_text("old-b", encoding="utf-8")
            transaction = WorkspaceTransaction(root)
            transaction.apply({"a.txt": "new-a", "b.txt": "new-b"})
            original_write = Path.write_bytes

            def fail_restore(path, content):
                if path == second and content == b"old-b":
                    raise OSError("injected restore failure")
                return original_write(path, content)

            with patch.object(Path, "write_bytes", new=fail_restore):
                with self.assertRaises(WorkspaceTransactionRestoreError) as raised:
                    transaction.restore()
            self.assertIs(
                transaction.state,
                WorkspaceTransactionState.RESTORE_FAILED_PARTIAL,
            )
            self.assertFalse(raised.exception.report.repository_restored)
            self.assertEqual(raised.exception.report.recovery_failed_paths, ("b.txt",))


if __name__ == "__main__":
    unittest.main()
