import os
import subprocess

import pytest

os.environ["MSWEA_SILENT_STARTUP"] = "1"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo with 'quotes' and $(shell)"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "api.py").write_text(
        "class Service:\n    def run(self, value: str) -> str:\n        return normalize(value)\n",
        encoding="utf-8",
    )
    (root / "helpers.py").write_text("def normalize(value: str) -> str:\n    return value.strip()\n", encoding="utf-8")
    (root / "README.md").write_text("# Example\n", encoding="utf-8")
    return root
