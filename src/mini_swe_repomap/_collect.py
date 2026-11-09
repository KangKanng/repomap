"""Standalone stdlib-only collector, executed inside the agent's environment."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

MARKER = "__MINISWE_REPOMAP_V1__"
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "vendor",
}
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".xz",
    ".so",
    ".dll",
    ".exe",
    ".pyc",
    ".woff",
    ".woff2",
    ".mp4",
    ".mp3",
    ".lock",
}


def collect(options: dict) -> dict:
    root = Path(options["root"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        paths = sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p))
    else:
        paths = []
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.endswith(".egg-info"))
            paths.extend((Path(directory) / name).relative_to(root).as_posix() for name in sorted(names))
        paths.sort()

    entries, warnings, total_bytes = [], set(), 0
    for name in paths:
        path = root / name
        if any(p in EXCLUDED_DIRS or p.endswith(".egg-info") for p in Path(name).parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            stat = path.stat()
            if options["max_file_bytes"] is not None and stat.st_size > options["max_file_bytes"]:
                warnings.add("Some files exceed max_file_bytes and were skipped.")
                continue
            if options["max_files"] is not None and len(entries) >= options["max_files"]:
                warnings.add("File count reached max_files; the map is partial.")
                break
            entry = {"path": name, "stamp": [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]}
            if path.suffix.lower() in options["suffixes"]:
                if options["max_total_bytes"] is not None and total_bytes + stat.st_size > options["max_total_bytes"]:
                    warnings.add("Source size reached max_total_bytes; the map is partial.")
                    continue
                total_bytes += stat.st_size
            entries.append(entry)
        except (OSError, UnicodeError):
            warnings.add("Some unreadable, deleted, or non-UTF-8 files were skipped.")
    fingerprint = hashlib.sha256(json.dumps([str(root), entries]).encode()).hexdigest()
    if fingerprint == options.get("fingerprint"):
        return {"root": str(root), "unchanged": True, "fingerprint": fingerprint, "warnings": sorted(warnings)}
    files = []
    for entry in entries:
        try:
            path = root / entry["path"]
            data = path.read_bytes() if path.suffix.lower() in options["suffixes"] else b""
            if b"\0" in data:
                continue
            entry["source"] = data.decode("utf-8-sig")
            files.append(entry)
        except (OSError, UnicodeError):
            warnings.add("Some unreadable, deleted, or non-UTF-8 files were skipped.")
    return {"root": str(root), "files": files, "fingerprint": fingerprint, "warnings": sorted(warnings)}


if __name__ == "__main__":
    print(MARKER + json.dumps(collect(json.loads(sys.argv[1])), ensure_ascii=True))
