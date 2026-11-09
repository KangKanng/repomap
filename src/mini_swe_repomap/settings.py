from typing import Literal

from pydantic import BaseModel, Field


class RepoMapConfig(BaseModel):
    mode: Literal["budget", "full"] = "budget"
    map_tokens: int = Field(default=1024, ge=0)
    root: str = "."
    refresh: Literal["interval", "once"] = "interval"
    refresh_interval: int = Field(default=1, ge=1)
    python_command: str = "python3"
    tokenizer: str = "cl100k_base"
    max_files: int | None = Field(default=5000, ge=1)
    max_file_bytes: int | None = Field(default=256_000, ge=1)
    max_total_bytes: int | None = Field(default=16_000_000, ge=1)
    context_messages: int = Field(default=6, ge=0)
    strict: bool = False
