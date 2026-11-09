"""Attach a hard deadline to native workers in an already-running budget sweep."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--seconds", type=int, default=1380)
    args = parser.parse_args()
    root = args.run.resolve()
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    while True:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                words = [w.decode(errors="replace") for w in (proc / "cmdline").read_bytes().split(b"\0") if w]
                if "minisweagent.run.benchmarks.swebench_single" not in words or "-o" not in words:
                    continue
                trajectory = Path(words[words.index("-o") + 1])
                if not trajectory.is_relative_to(root / "conditions"):
                    continue
                ticks = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[19])
                elapsed = float(Path("/proc/uptime").read_text().split()[0]) - ticks / ticks_per_second
                if elapsed < args.seconds:
                    continue
                data = json.loads(trajectory.read_text()) if trajectory.exists() else {}
                if data.get("info", {}).get("exit_status"):
                    continue
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                archive = root / "timeouts" / f"{trajectory.parent.name}-{stamp}"
                archive.mkdir(parents=True, exist_ok=True)
                if trajectory.exists():
                    shutil.copy2(trajectory, archive / trajectory.name)
                log = trajectory.parent / "runner.log"
                text = log.read_text(errors="replace") if log.exists() else ""
                if log.exists():
                    shutil.copy2(log, archive / "runner.log")
                known_cost = sum(
                    (
                        Decimal(str(m["extra"]["cost"]))
                        for m in data.get("messages", [])
                        if "cost" in m.get("extra", {})
                    ),
                    Decimal(0),
                )
                event = {
                    "instance_id": trajectory.parent.name,
                    "trajectory": str(trajectory),
                    "pid": int(proc.name),
                    "elapsed_seconds": elapsed,
                    "hard_deadline_seconds": args.seconds,
                    "archive": str(archive),
                    "known_server_cost_usd": str(known_cost),
                    "pending_request_charge_unknown": True,
                    "stopped_at": datetime.now(timezone.utc).isoformat(),
                }
                (archive / "timeout.json").write_text(json.dumps(event, indent=2) + "\n")
                with (root / "external-timeouts.jsonl").open("a") as stream:
                    stream.write(json.dumps(event) + "\n")
                os.kill(int(proc.name), signal.SIGTERM)
                names = set(re.findall(r"(?:--name|Started container)\s+(minisweagent-[0-9a-f]+)", text))
                for name in names:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
                print("PROCESS_DEADLINE", trajectory.parent.name, round(elapsed), flush=True)
            except (FileNotFoundError, PermissionError, ProcessLookupError, json.JSONDecodeError):
                continue
        experiment = root / "experiment.json"
        if experiment.exists() and json.loads(experiment.read_text()).get("finished_at"):
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
