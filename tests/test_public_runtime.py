from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import projectreader
from projectreader import ProjectReader
from projectreader.actions import ActionPayloadRejected, SessionStateRejected
from projectreader.actions.rejections import WorkspaceChangeRejected


class ProjectReaderRuntimeTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        (root / "app.py").write_bytes(
            b"def calculate_value():\n    return 1\n"
        )
        (root / "verify_pass.py").write_bytes(
            b"from app import calculate_value\nassert calculate_value() == 2\n"
        )
        (root / "verify_fail.py").write_bytes(
            b"from app import calculate_value\nassert calculate_value() == 999\n"
        )

    def test_public_namespace_is_exact(self):
        self.assertEqual(
            projectreader.__all__,
            [
                "ContextSelection",
                "FocusTarget",
                "InspectionTarget",
                "PatchRecord",
                "PatchVerification",
                "ProjectReader",
                "ProjectReaderRejected",
                "ProjectReaderStatus",
                "SearchResult",
                "__version__",
            ],
        )
    def test_open_index_search_inspect_and_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            reader = ProjectReader.open(root)
            summary = reader.index()
            self.assertEqual(summary["source_count"], 3)
            hits = reader.search("calculate value")
            symbol = next(item for item in hits if item.target_type == "symbol")
            selection = reader.inspect(symbol.inspection_ref)
            self.assertEqual(selection.mode, "symbol_source")
            self.assertIn("return 1", selection.content)
            focused = reader.focus(symbol.inspection_ref)
            self.assertEqual(focused, selection)
            self.assertEqual(reader.navigator.active.target_ref, "calculate_value")
            self.assertEqual(reader.status().context["content_chars"], len(selection.content))

    def test_not_indexed_is_typed_and_does_not_write_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = ProjectReader.open(directory)
            before = reader.audit.last_sequence
            with self.assertRaises(SessionStateRejected) as raised:
                reader.search("anything")
            self.assertEqual(raised.exception.reason_code, "repository_not_indexed")
            self.assertEqual(raised.exception.as_error()["side_effects"], False)
            self.assertEqual(reader.audit.last_sequence, before)

    def test_full_patch_verify_accept_path_and_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            reader = ProjectReader.open(
                root,
                verifier=(sys.executable, "verify_pass.py"),
            )
            reader.index()
            hit = next(
                item
                for item in reader.search("calculate_value")
                if item.target_type == "symbol"
            )
            reader.focus(hit.inspection_ref)
            patch = reader.patch(
                [{"path": "app.py", "content": "def calculate_value():\n    return 2\n"}],
                description="change the complete file",
            )
            self.assertEqual(patch.status, "applied")
            self.assertEqual(
                patch.proposal_context.focus_target_ref,
                "calculate_value",
            )
            self.assertEqual(
                [item.qualified_symbol for item in patch.changed_scopes],
                ["calculate_value"],
            )
            self.assertIsNone(patch.causal_attribution)
            verification = reader.verify()
            self.assertTrue(verification.passed)
            accepted = reader.accept()
            self.assertEqual(accepted.status, "accepted")
            self.assertEqual(
                [item.event_type for item in reader.audit_events][-3:],
                ["patch_applied", "patch_verified", "patch_accepted"],
            )

    def test_failed_verification_then_rollback_restores_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            original = (root / "app.py").read_bytes()
            reader = ProjectReader.open(
                root,
                verifier=(sys.executable, "verify_fail.py"),
            )
            patch = reader.patch(
                {"app.py": "def calculate_value():\n    return 2\n"},
                description="intentionally fails the trusted verifier",
            )
            self.assertFalse(reader.verify().passed)
            rolled_back = reader.rollback()
            self.assertEqual(rolled_back.patch_id, patch.patch_id)
            self.assertEqual(rolled_back.status, "rolled_back")
            self.assertEqual((root / "app.py").read_bytes(), original)

    def test_escape_rejection_is_pre_mutation_and_pre_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repo"
            root.mkdir()
            self.make_repository(root)
            outside = base / "outside.py"
            outside.write_text("safe\n", encoding="utf-8")
            reader = ProjectReader.open(root)
            before = reader.audit.last_sequence
            with self.assertRaises(WorkspaceChangeRejected) as raised:
                reader.patch({"../outside.py": "changed\n"})
            self.assertEqual(raised.exception.reason_code, "path_outside_repository")
            self.assertEqual(outside.read_text(encoding="utf-8"), "safe\n")
            self.assertEqual(reader.audit.last_sequence, before)

    def test_non_iterable_patch_payload_uses_typed_catalog_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = ProjectReader.open(directory)
            before = reader.audit.last_sequence
            with self.assertRaises(ActionPayloadRejected) as raised:
                reader.patch(None)
            self.assertEqual(raised.exception.reason_code, "invalid_field_type")
            self.assertEqual(raised.exception.as_error()["side_effects"], False)
            self.assertEqual(reader.audit.last_sequence, before)

    def test_structured_action_executor_is_one_shared_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            reader = ProjectReader.open(
                root,
                verifier=(sys.executable, "verify_pass.py"),
            )
            reader.index()
            search = reader.execute(
                "search_repository",
                {"query": "calculate_value", "limit": 8},
            )
            self.assertTrue(search.succeeded)
            ref = next(
                item.inspection_ref
                for item in search.data["result"]
                if item.target_type == "symbol"
            )
            self.assertTrue(
                reader.execute("focus_target", {"inspection_ref": ref}).succeeded
            )
            applied = reader.execute(
                "replace_files",
                {
                    "file_replacements": [
                        {
                            "path": "app.py",
                            "content": "def calculate_value():\n    return 2\n",
                        }
                    ],
                    "description": "structured action",
                },
            )
            patch_id = applied.data["result"].patch_id
            verified = reader.execute(
                "verify_patch",
                {"patch_id": patch_id, "command_id": "default"},
            )
            self.assertTrue(verified.data["result"].passed)
            self.assertTrue(
                reader.execute("accept_patch", {"patch_id": patch_id}).succeeded
            )

    def test_session_refs_ledgers_and_patch_ids_are_isolated(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            roots = (Path(first), Path(second))
            for root in roots:
                self.make_repository(root)
            readers = [ProjectReader.open(root) for root in roots]
            for reader in readers:
                reader.index()
            refs = [reader.search("calculate_value")[0].inspection_ref for reader in readers]
            self.assertNotEqual(refs[0], refs[1])
            before = readers[1].audit.last_sequence
            with self.assertRaises(projectreader.ProjectReaderRejected):
                readers[1].inspect(refs[0])
            self.assertEqual(readers[1].audit.last_sequence, before)
            patches = [
                reader.patch({"app.py": "def calculate_value():\n    return 2\n"})
                for reader in readers
            ]
            self.assertEqual([item.patch_id for item in patches], ["patch-000001"] * 2)
            for reader in readers:
                reader.rollback()


if __name__ == "__main__":
    unittest.main()
