"""Enforce hard checkpoint deadlines on already-running SCBench workers."""

import argparse
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

import tiktoken
from scb_infer import snapshot, write_json


def worker_pid(output, problem, variant):
    listing = subprocess.check_output(["ps", "-eo", "pid,args"], text=True)
    for line in listing.splitlines()[1:]:
        try:
            pid, command = line.strip().split(None, 1)
            args = shlex.split(command)
            if not Path(args[0]).name.startswith("python") or len(args) < 2 or not args[1].endswith("scb_infer.py"):
                continue
            if (
                Path(args[args.index("--output") + 1]).resolve() == output
                and args[args.index("--problem") + 1] == problem
                and args[args.index("--variant") + 1] == variant
            ):
                return int(pid)
        except (ValueError, IndexError):
            continue
    return None


def stop_checkpoint(output, started_path, record):
    directory = started_path.parent
    root = directory.parent
    pid = worker_pid(output, record["problem"], record["variant"])
    if not pid:
        return
    trajectory = directory / "trajectory.json"
    if trajectory.exists():
        try:
            write_json(directory / "trajectory-before-hard-guard.json", json.loads(trajectory.read_text()))
        except ValueError:
            pass
    guard = {
        "pid": pid,
        "reason": "CheckpointHardTimeout",
        "guard_started_unix": time.time(),
        "elapsed_before_guard_seconds": time.time() - record["started_unix"],
        "retry": False,
    }
    write_json(directory / "guard.json", guard)
    write_json(
        root / "chain-finished.json",
        {
            "error": "CheckpointHardTimeout",
            "complete": False,
            "completed_checkpoints": [p.parent.name for p in root.glob("checkpoint_*/inference.json")],
        },
    )
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finalize_root(root)


def capture_interrupted(started_path):
    directory = started_path.parent
    root = directory.parent
    record = json.loads(started_path.read_text())
    trajectory = directory / "trajectory.json"
    try:
        data = json.loads(trajectory.read_text()) if trajectory.exists() else {"messages": [], "info": {}}
    except ValueError:
        backup = directory / "trajectory-before-hard-guard.json"
        data = json.loads(backup.read_text()) if backup.exists() else {"messages": [], "info": {}}
        trajectory.rename(directory / "trajectory-incomplete.json")
    maps = [
        m["content"]
        for m in data["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith("<repository_map>")
    ]
    if maps:
        (directory / "repository-map.txt").write_text(maps[0])
    if not (directory / "snapshot").exists():
        snapshot(root / "workspace", directory / "snapshot")
    if not trajectory.exists():
        write_json(trajectory, data)
    stats = data["info"].get("model_stats", {})
    write_json(
        directory / "inference.json",
        {k: v for k, v in record.items() if k != "started_unix"}
        | {
            "exit_status": "CheckpointHardTimeout",
            "error": "CheckpointHardTimeout",
            "elapsed_seconds": time.time() - record["started_unix"],
            "model_calls": stats.get("api_calls", 0),
            "cost": stats.get("instance_cost", 0),
            "map_count": len(maps),
            "map_tokens": len(tiktoken.get_encoding("cl100k_base").encode(maps[0])) if maps else 0,
            "map_sha256": hashlib.sha256(maps[0].encode()).hexdigest() if maps else None,
            "snapshot": str(directory / "snapshot"),
            "trajectory": str(directory / "trajectory.json"),
            "guarded": True,
            "pending_cost_unknown": True,
        },
    )


def finalize_root(root):
    container = json.loads((root / "container.json").read_text())["id"]
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
    for path in root.glob("checkpoint_*/started.json"):
        if not (path.parent / "inference.json").exists():
            capture_interrupted(path)
    write_json(
        root / "chain-finished.json",
        {
            "error": "CheckpointHardTimeout",
            "complete": False,
            "completed_checkpoints": [p.parent.name for p in root.glob("checkpoint_*/inference.json")],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    while json.loads((output / "status.json").read_text())["state"] == "running":
        protocol = json.loads((output / "protocol.json").read_text())
        for variant in protocol["variants"]:
            for path in (output / variant).glob("*/checkpoint_*/started.json"):
                if (path.parent / "inference.json").exists() or (path.parent / "guard.json").exists():
                    continue
                record = json.loads(path.read_text())
                if time.time() - record["started_unix"] > protocol.get("checkpoint_hard_timeout_seconds", 1380) + 10:
                    print(json.dumps({"guarding": str(path.parent)}), flush=True)
                    stop_checkpoint(output, path, record)
        time.sleep(10)


if __name__ == "__main__":
    main()
