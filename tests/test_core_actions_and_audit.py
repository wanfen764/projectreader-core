from __future__ import annotations

import unittest

from projectreader.actions import (
    ActionPayloadRejected,
    CommandCatalog,
    CommandSelectionRejected,
    CoreActionCatalog,
    FileReplacement,
)
from projectreader.audit import AuditLedger


class CoreActionContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog = CoreActionCatalog()

    def test_public_catalog_is_exact(self):
        self.assertEqual(
            [item.action_type for item in self.catalog.descriptors()],
            [
                "search_repository",
                "inspect_repository_target",
                "focus_target",
                "replace_files",
                "verify_patch",
                "accept_patch",
                "rollback_patch",
            ],
        )
    def test_replace_files_is_strict_and_normalizes_value_objects(self):
        action = self.catalog.validate(
            "replace_files",
            {
                "description": "replace and delete",
                "file_replacements": [
                    {"path": "app.py", "content": "VALUE = 2\n"},
                    {"path": "obsolete.py", "content": None},
                ],
            },
        )
        self.assertEqual(action.action_type, "replace_files")
        self.assertEqual(
            action.payload["file_replacements"],
            (
                FileReplacement("app.py", "VALUE = 2\n"),
                FileReplacement("obsolete.py", None),
            ),
        )

    def test_rejects_patch_shapes_duplicates_and_unknown_fields(self):
        cases = (
            ({"file_replacements": "diff --git"}, "invalid_field_type"),
            (
                {"file_replacements": [{"path": "app.py", "patch": "@@"}]},
                "missing_required_field",
            ),
            (
                {
                    "file_replacements": [
                        {"path": "app.py", "content": "one"},
                        {"path": "app.py", "content": "two"},
                    ]
                },
                "duplicate_replacement_path",
            ),
            (
                {"file_replacements": [{"path": "app.py", "content": 2}]},
                "invalid_field_type",
            ),
            (
                {"file_replacements": [{"path": "app.py", "content": "x", "old_text": "y"}]},
                "unknown_field",
            ),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ActionPayloadRejected) as raised:
                    self.catalog.validate("replace_files", payload)
                self.assertEqual(raised.exception.reason_code, reason)
                self.assertEqual(
                    raised.exception.as_error()["side_effects"], False
                )

    def test_all_actions_reject_unknown_top_level_fields(self):
        with self.assertRaises(ActionPayloadRejected) as raised:
            self.catalog.validate(
                "search_repository", {"query": "value", "filesystem_path": "../"}
            )
        self.assertEqual(raised.exception.reason_code, "unknown_field")
        self.assertEqual(raised.exception.as_error()["recoverable"], True)

    def test_command_catalog_allows_exact_argv_only(self):
        commands = CommandCatalog()
        registered = commands.register(
            "unit", ["python", "check.py"], description="trusted unit check"
        )
        self.assertEqual(commands.resolve("unit"), ("python", "check.py"))
        self.assertEqual(registered.purpose, "verification")
        with self.assertRaises(TypeError):
            commands.register("shell", "python check.py")
        with self.assertRaises(CommandSelectionRejected) as raised:
            commands.resolve("missing")
        self.assertEqual(raised.exception.reason_code, "command_id_unknown")


class AuditLedgerTests(unittest.TestCase):
    def test_ordered_copy_safe_audit(self):
        ledger = AuditLedger()
        first = ledger.record("repository_opened", target_ref="repo")
        second = ledger.record("patch_applied", data={"patch_id": "patch-000001"})
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(ledger.last_sequence, 2)
        returned = ledger.all()
        self.assertIsInstance(returned, tuple)
        self.assertEqual(ledger.summary()["by_type"]["patch_applied"], 1)
        self.assertEqual(ledger.tail(1), (second,))
        self.assertEqual(ledger.tail(0), ())


if __name__ == "__main__":
    unittest.main()
