"""Load project environment settings without replacing exported variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment(env_file: Path | None, *, default_path: Path) -> Path | None:
    path = env_file.expanduser().resolve() if env_file is not None else default_path
    if not path.is_file():
        if env_file is not None:
            raise FileNotFoundError(f"Environment file not found: {path}")
    else:
        load_dotenv(path, override=False)
    if model := os.getenv("MODEL_NAME"):
        if os.getenv("MODEL_BASE_URL") and "/" not in model:
            model = "openai/" + model
        if not os.getenv("MSWEA_MODEL_NAME"):
            os.environ["MSWEA_MODEL_NAME"] = model
    return path if path.is_file() else None
