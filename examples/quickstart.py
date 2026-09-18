"""Run a deterministic ProjectReader Core workflow against a temporary copy."""

from pathlib import Path
import shutil
import sys
import tempfile

from projectreader import ProjectReader


fixture = Path(__file__).parent / "demo_repository"
with tempfile.TemporaryDirectory(prefix="projectreader-demo-") as temporary:
    repository = Path(temporary) / "repository"
    shutil.copytree(fixture, repository)
    reader = ProjectReader.open(
        repository,
        verifier=(sys.executable, "verify_demo.py"),
    )
    print(reader.index())
    hits = reader.search("greeting")
    print(hits)
    if hits:
        print(reader.inspect(hits[0].inspection_ref))
    replacement = "def greeting(name: str) -> str:\n    return f\"Hello, {name}!\"\n"
    print(
        reader.patch(
            [{"path": "demo_app/greeting.py", "content": replacement}],
            description="Correct the greeting through a complete file replacement",
        )
    )
    verification = reader.verify()
    print(verification)
    passed = verification.get("passed") if isinstance(verification, dict) else verification.passed
    print(reader.accept() if passed else reader.rollback())
    print(reader.status())
