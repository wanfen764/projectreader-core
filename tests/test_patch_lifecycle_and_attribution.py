from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from projectreader.actions import PatchLifecycleRejected
from projectreader.audit import AuditLedger
from projectreader.patching import (
    PatchAttributionAnalyzer,
    PatchManager,
    PatchProposalContext,
    WorkspaceTransaction,
    WorkspaceTransactionState,
)


class PatchLifecycleCoreTests(unittest.TestCase):
    def test_passing_patch_can_be_verified_and_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "check.py").write_text(
                "import app\nassert app.VALUE == 2\n", encoding="utf-8"
            )
            audit = AuditLedger()
            manager = PatchManager(root, audit=audit)
            record = manager.apply(
                "fix value",
                {"app.py": "VALUE = 2\n"},
                proposal_context=PatchProposalContext(
                    focus_target_ref="app.py", source_id="app.py", context_mode="file"
                ),
            )
            verification = manager.verify(
                record.patch_id,
                [sys.executable, "check.py"],
                command_id="trusted-check",
            )
            self.assertTrue(verification.passed)
            manager.accept(record.patch_id)
            self.assertEqual(record.status, "accepted")
            self.assertIsNone(manager.active_patch_id)
            self.assertEqual(
                [item.event_type for item in audit.all()],
                ["patch_applied", "patch_verified", "patch_accepted"],
            )

    def test_failed_verification_requires_rollback_and_restores_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app.py"
            target.write_text("VALUE = 1\r\n", encoding="utf-8", newline="")
            (root / "check.py").write_text(
                "import app\nassert app.VALUE == 1\n", encoding="utf-8"
            )
            original = target.read_bytes()
            manager = PatchManager(root)
            record = manager.apply("bad value", {"app.py": "VALUE = 9\n"})
            self.assertFalse(
                manager.verify(record.patch_id, [sys.executable, "check.py"]).passed
            )
            with self.assertRaises(PatchLifecycleRejected) as raised:
                manager.accept(record.patch_id)
            self.assertEqual(
                raised.exception.reason_code, "patch_verification_not_passed"
            )
            manager.rollback(record.patch_id)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(record.status, "rolled_back")
            self.assertIs(
                manager.store.transaction(record.patch_id).state,
                WorkspaceTransactionState.RESTORED,
            )

    def test_lifecycle_rejections_are_pre_audit_and_side_effect_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            manager = PatchManager(root)
            record = manager.apply("first", {"app.py": "VALUE = 2\n"})
            sequence = manager.audit.last_sequence
            with self.assertRaises(PatchLifecycleRejected) as raised:
                manager.apply("second", {"app.py": "VALUE = 3\n"})
            self.assertEqual(raised.exception.reason_code, "active_patch_in_progress")
            self.assertEqual(manager.audit.last_sequence, sequence)
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")
            with self.assertRaises(PatchLifecycleRejected):
                manager.accept("missing")
            self.assertEqual(manager.audit.last_sequence, sequence)
            manager.rollback(record.patch_id)

    def test_verifier_is_argv_only_and_never_shell_string(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            manager = PatchManager(root)
            record = manager.apply("change", {"app.py": "VALUE = 2\n"})
            with self.assertRaises(TypeError):
                manager.verify(record.patch_id, "python check.py")
            self.assertEqual(record.verifications, [])
            manager.rollback(record.patch_id)


class PatchAttributionCoreTests(unittest.TestCase):
    def analyze(self, path, original, desired):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / path
            if original is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    original if isinstance(original, bytes) else original.encode("utf-8")
                )
            transaction = WorkspaceTransaction(root)
            transaction.prepare({path: desired})
            return PatchAttributionAnalyzer().analyze(transaction.frozen_changes())

    def test_function_method_class_and_module_attribution(self):
        cases = (
            (
                "def total(value):\n    return value + 1\n",
                "def total(value):\n    return value + 2\n",
                "total",
                "function",
            ),
            (
                "class Calc:\n    def total(self):\n        return 1\n",
                "class Calc:\n    def total(self):\n        return 2\n",
                "Calc.total",
                "method",
            ),
            (
                "class Config:\n    RATE = 1\n\n    def read(self):\n        return self.RATE\n",
                "class Config:\n    RATE = 2\n\n    def read(self):\n        return self.RATE\n",
                "Config",
                "class",
            ),
            (
                "RATE = 1\n\ndef read():\n    return RATE\n",
                "RATE = 2\n\ndef read():\n    return RATE\n",
                "__module__",
                "module_context",
            ),
        )
        for original, desired, symbol, unit_type in cases:
            with self.subTest(symbol=symbol):
                scopes = self.analyze("app.py", original, desired)
                self.assertEqual(len(scopes), 1)
                self.assertEqual(scopes[0].qualified_symbol, symbol)
                self.assertEqual(scopes[0].unit_type, unit_type)
                self.assertEqual(scopes[0].change_kind, "modified")

    def test_multi_file_add_delete_and_parse_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "modify.py").write_text(
                "def value():\n    return 1\n", encoding="utf-8"
            )
            (root / "remove.py").write_text(
                "class Gone:\n    pass\n", encoding="utf-8"
            )
            manager = PatchManager(root)
            record = manager.apply(
                "multi-file",
                {
                    "modify.py": "def value():\n    return 2\n",
                    "new.py": "class Added:\n    def run(self):\n        return 4\n",
                    "remove.py": None,
                    "broken.py": "def broken(:\n",
                },
            )
            observed = {
                (item.path, item.qualified_symbol, item.change_kind)
                for item in record.changed_scopes
            }
            self.assertIn(("modify.py", "value", "modified"), observed)
            self.assertIn(("new.py", "Added", "added"), observed)
            self.assertIn(("new.py", "Added.run", "added"), observed)
            self.assertIn(("remove.py", "Gone", "deleted"), observed)
            broken = [item for item in record.changed_scopes if item.path == "broken.py"]
            self.assertEqual(broken[0].scope_kind, "file")
            self.assertIsNone(record.causal_attribution)
            manager.rollback(record.patch_id)

    def test_proposal_context_is_distinct_from_changed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (
                "class Projector:\n"
                "    def __init__(self):\n        self.value = 0\n\n"
                "    def restore(self):\n        return 1\n"
            )
            # Preserve LF bytes so this test isolates semantic attribution from
            # a platform newline-conversion change across the whole file.
            (root / "projector.py").write_bytes(source.encode("utf-8"))
            changed = source.replace("        return 1", "        return 2")
            manager = PatchManager(root)
            record = manager.apply(
                "boundary fix",
                {"projector.py": changed},
                proposal_context=PatchProposalContext(
                    focus_target_ref="Projector.__init__",
                    source_id="projector.py",
                    context_mode="symbol",
                ),
            )
            self.assertEqual(
                record.proposal_context.focus_target_ref, "Projector.__init__"
            )
            self.assertEqual(
                [item.qualified_symbol for item in record.changed_scopes],
                ["Projector.restore"],
            )
            self.assertIsNone(record.causal_attribution)
            manager.rollback(record.patch_id)


if __name__ == "__main__":
    unittest.main()
