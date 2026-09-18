from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from projectreader.navigation import ContextCapacityExceeded, RepositoryNavigator
from projectreader.repository import InspectionRegistry, RepositoryIndexer, RepositoryInspector, RepositorySearch


class NavigationCoreTests(unittest.TestCase):
    def test_focus_retains_multiple_contexts_without_candidate_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "service.py").write_text(
                "def first():\n    return 1\n\ndef second():\n    return 2\n",
                encoding="utf-8",
            )
            index = RepositoryIndexer(root).build()
            registry = InspectionRegistry(index, namespace="7" * 32)
            search = RepositorySearch(index, registry)
            inspector = RepositoryInspector(index, registry)
            navigator = RepositoryNavigator(inspector)
            first = next(item for item in search.search("first") if item.target_type == "symbol")
            second = next(item for item in search.search("second") if item.target_type == "symbol")

            navigator.focus(first.inspection_ref)
            navigator.focus(second.inspection_ref)

            self.assertEqual(navigator.active.target_ref, "second")
            self.assertEqual(navigator.context.target_ref, "second")
            self.assertEqual(len(navigator.workspace.selections()), 2)
            self.assertEqual(navigator.workspace.summary()["targets"], ["first", "second"])

    def test_capacity_failure_does_not_replace_active_focus(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "small.txt").write_text("small\n", encoding="utf-8")
            (root / "large.txt").write_text("large text\n" * 100, encoding="utf-8")
            index = RepositoryIndexer(root, max_chars=50).build()
            registry = InspectionRegistry(index, namespace="8" * 32)
            inspector = RepositoryInspector(index, registry, max_inline_chars=2000)
            navigator = RepositoryNavigator(inspector, max_chars=100)
            navigator.focus(registry.issue_file("small.txt"))
            before = navigator.active

            with self.assertRaises(ContextCapacityExceeded):
                navigator.focus(registry.issue_file("large.txt"))

            self.assertEqual(navigator.active, before)
            self.assertEqual(navigator.workspace.summary()["entries"], 1)


if __name__ == "__main__":
    unittest.main()
