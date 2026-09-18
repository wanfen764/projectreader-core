"""Portable repository scanning and text decoding."""

from __future__ import annotations

from pathlib import Path


IGNORED_DIRECTORY_NAMES = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__",
    "node_modules", ".venv", "venv", "build", "dist",
})


class ProjectScanner:
    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path).resolve()

    def scan(self) -> list[Path]:
        if not self.project_path.exists():
            raise FileNotFoundError(f"repository does not exist: {self.project_path}")
        if not self.project_path.is_dir():
            raise NotADirectoryError(f"repository is not a directory: {self.project_path}")
        files = []
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue
            # A repository registration is not authority to read through a
            # filesystem link.  Skipping links also keeps the indexed identity
            # portable when the tree is copied to another machine.
            if path.is_symlink():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(self.project_path)
            except ValueError:
                continue
            relative = path.relative_to(self.project_path)
            if any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts):
                continue
            files.append(path)
        return sorted(files, key=lambda item: item.relative_to(self.project_path).as_posix())


class TextFileReader:
    ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "cp1252")

    def read(self, path: Path) -> tuple[str, str] | None:
        with path.open("rb") as stream:
            if b"\x00" in stream.read(4096):
                return None
        for encoding in self.ENCODINGS:
            try:
                return path.read_text(encoding=encoding), encoding
            except UnicodeDecodeError:
                continue
            except OSError:
                raise
        return None
