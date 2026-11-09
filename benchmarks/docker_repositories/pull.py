"""Compatibility entry point; use scripts/pull_docker.py for new commands."""

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).resolve().parents[2] / "scripts" / "pull_docker.py"
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")
