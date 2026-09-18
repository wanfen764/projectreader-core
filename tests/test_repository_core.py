from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from projectreader.repository import (
    InspectionRegistry,
    RepositoryIndexer,
    RepositoryInspectionRejected,
    RepositoryInspector,
    RepositorySearch,
    RepositorySearchRejected,
)


class RepositoryCoreTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        (root / "app.py").write_text(
            "from services import Calculator\n\n"
            "class Application:\n"
            "    def run(self, value):\n"
            "        return Calculator().double(value)\n",
            encoding="utf-8",
        )
        (root / "services.py").write_text(
            "class Calculator:\n"
            "    def double(self, value):\n"
            "        return value * 2\n",
            encoding="utf-8",
        )
        (root / "notes.txt").write_text("calculator design notes\n", encoding="utf-8")

    def test_index_search_and_exact_symbol_inspection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            index = RepositoryIndexer(root).build()
            registry = InspectionRegistry(index, namespace="1" * 32)
            search = RepositorySearch(index, registry)
            inspector = RepositoryInspector(index, registry)

            hits = search.search("Calculator double")
            symbol = next(item for item in hits if item.target_ref == "Calculator.double")
            selection = inspector.inspect(symbol.inspection_ref)

            self.assertEqual(index.root, root.resolve())
            self.assertEqual(selection.mode, "symbol_source")
            self.assertEqual(selection.source_id, "services.py")
            self.assertIn("def double", selection.content)
            self.assertNotIn("class Application", selection.content)
            self.assertEqual(registry.resolve(symbol.inspection_ref).target_ref, "Calculator.double")

    def test_search_refs_are_stable_and_session_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            index = RepositoryIndexer(root).build()
            first = InspectionRegistry(index, namespace="2" * 32)
            second = InspectionRegistry(index, namespace="3" * 32)
            search = RepositorySearch(index, first)
            first_ref = search.search("double")[0].inspection_ref
            repeated_ref = search.search("double")[0].inspection_ref

            self.assertEqual(first_ref, repeated_ref)
            with self.assertRaises(RepositoryInspectionRejected) as context:
                second.resolve(first_ref)
            self.assertEqual(context.exception.reason_code, "inspection_ref_wrong_session")
            self.assertEqual(context.exception.structured()["side_effects"], False)

    def test_stale_ref_is_typed_and_side_effect_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            index = RepositoryIndexer(root).build()
            registry = InspectionRegistry(index, namespace="4" * 32)
            inspector = RepositoryInspector(index, registry)
            inspection_ref = registry.issue_file("notes.txt")
            (root / "notes.txt").write_text("changed\n", encoding="utf-8")

            with self.assertRaises(RepositoryInspectionRejected) as context:
                inspector.inspect(inspection_ref)
            self.assertEqual(context.exception.reason_code, "inspection_ref_stale")

    def test_empty_query_and_unbounded_limit_are_typed_rejections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            search = RepositorySearch(RepositoryIndexer(root).build())
            for query, limit, reason in (("", 8, "query_empty"), ("run", 11, "limit_out_of_range")):
                with self.subTest(reason=reason):
                    with self.assertRaises(RepositorySearchRejected) as context:
                        search.search(query, limit=limit)
                    self.assertEqual(context.exception.reason_code, reason)

    def test_python_outline_and_text_source_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            index = RepositoryIndexer(root).build()
            registry = InspectionRegistry(index, namespace="5" * 32)
            inspector = RepositoryInspector(index, registry)

            outline = inspector.inspect(registry.issue_file("app.py"))
            text = inspector.inspect(registry.issue_file("notes.txt"))

            self.assertEqual(outline.mode, "python_outline")
            self.assertEqual(outline.content, "")
            self.assertIn("Application.run", [item["target_ref"] for item in outline.segments])
            self.assertEqual(text.mode, "text_full")
            self.assertEqual(text.content, "calculator design notes\n")

    def test_large_source_uses_bounded_segments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            body = "".join(f"    value += {number}\n" for number in range(100))
            (root / "large.py").write_text(
                "def calculate(value):\n" + body + "    return value\n",
                encoding="utf-8",
            )
            (root / "large.txt").write_text("line value\n" * 80, encoding="utf-8")
            index = RepositoryIndexer(root, max_chars=120).build()
            registry = InspectionRegistry(index, namespace="6" * 32)
            inspector = RepositoryInspector(index, registry, max_inline_chars=120)

            symbol_ref = registry.issue_symbol("large.py", "calculate")
            symbol_index = inspector.inspect(symbol_ref)
            symbol_part = inspector.inspect(symbol_ref, 1)
            text_ref = registry.issue_file("large.txt")
            text_index = inspector.inspect(text_ref)
            text_part = inspector.inspect(text_ref, 1)

            self.assertEqual(symbol_index.mode, "split_callable_index")
            self.assertGreater(len(symbol_index.segments), 1)
            self.assertEqual(symbol_part.mode, "callable_part")
            self.assertLessEqual(len(symbol_part.content), 120)
            self.assertEqual(text_index.mode, "text_chunk_index")
            self.assertEqual(text_part.mode, "text_chunk")
            self.assertLessEqual(len(text_part.content), 120)

    def test_import_relations_match_frozen_core_semantics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_repository(root)
            index = RepositoryIndexer(root).build()

            self.assertTrue(any(
                item.target_id == "services.py" and item.kind == "imports"
                for item in index.relations.related("app.py")
            ))
            self.assertTrue(any(
                item.target_id == "app.py" and item.kind == "imported_by"
                for item in index.relations.related("services.py")
            ))

    def test_scanner_is_stable_and_ignores_tool_directories_and_binary_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "z.txt").write_text("z\n", encoding="utf-8")
            (root / "a.txt").write_text("a\n", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"x\x00y")
            ignored = root / ".git"
            ignored.mkdir()
            (ignored / "private.txt").write_text("not indexed", encoding="utf-8")

            index = RepositoryIndexer(root).build()

            self.assertEqual([path.name for path in index.files], ["a.txt", "binary.bin", "z.txt"])
            self.assertNotIn(".git/private.txt", index.file_status)
            self.assertEqual(index.file_status["binary.bin"]["status"], "unsupported")

    def test_scanner_does_not_follow_file_symlinks_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "repository"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("outside content must not be indexed\n", encoding="utf-8")
            link = root / "linked.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                # Windows commonly requires an opt-in privilege to create a
                # symlink. Exercise the scanner branch deterministically on
                # those hosts without weakening the production check.
                link.write_text("stand-in linked content\n", encoding="utf-8")
                original_is_symlink = Path.is_symlink

                def simulated_is_symlink(path: Path) -> bool:
                    return path == link or original_is_symlink(path)

                with patch.object(Path, "is_symlink", new=simulated_is_symlink):
                    index = RepositoryIndexer(root).build()
            else:
                index = RepositoryIndexer(root).build()

            self.assertEqual(index.files, ())
            self.assertEqual(index.source_store.records, {})


if __name__ == "__main__":
    unittest.main()
