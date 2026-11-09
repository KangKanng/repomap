"""Retry a recorded infrastructure timeout once, preserving the original attempt and fees."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pull_docker import PROJECT
from run_budget_sweep import write_json
from run_swebench import run_entry

from mini_swe_repomap.env import load_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    root = args.run.resolve()
    recovery = json.loads((root / "recovery-plan.json").read_text())
    if recovery["state"] != "planned":
        raise SystemExit("Recovery is already started or complete; refusing duplicate paid retry")
    recovery["state"] = "waiting"
    write_json(root / "recovery-plan.json", recovery)
    load_environment(None, default_path=PROJECT / ".env")
    while True:
        experiment = json.loads((root / "experiment.json").read_text())
        if experiment.get("finished_at"):
            break
        time.sleep(10)
    recovery["state"] = "running"
    recovery["started_at"] = datetime.now(timezone.utc).isoformat()
    write_json(root / "recovery-plan.json", recovery)
    overrides = []
    plan = json.loads((root / "plan.json").read_text())
    for target in recovery["targets"]:
        original = next(
            e
            for e in plan
            if e["condition"] == target["condition"] and e["item"]["instance_id"] == target["instance_id"]
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        retry = root / "retries" / f"{target['instance_id']}-{target['condition']}-{stamp}"
        directory = retry / original["variant"] / original["item"]["dataset"].rsplit("/", 1)[-1] / target["instance_id"]
        command = list(original["command"])
        command[command.index("-o") + 1] = str(directory / f"{target['instance_id']}.traj.json")
        entry = original | {"output": str(directory), "command": command, "process_timeout_seconds": 1380}
        write_json(retry / "plan.json", [entry])
        started = time.monotonic()
        record = run_entry(entry) | {
            "condition": original["condition"],
            "budget": original["budget"],
            "elapsed_seconds": time.monotonic() - started,
            "retry_reason": "infrastructure_request_hang",
        }
        submission = record.pop("submission", "")
        write_json(retry / "summary.json", {"errors": int(not record["valid"]), "runs": [record]})
        write_json(
            Path(record["output_group"]) / "preds.json",
            {
                target["instance_id"]: {
                    "instance_id": target["instance_id"],
                    "model_name": experiment["model"],
                    "model_patch": submission,
                }
            },
        )
        evaluate = [
            "/root/.cache/repomap-eval-venv/bin/python",
            str(PROJECT / "scripts/evaluate_swebench_offline.py"),
            str(retry),
            "--variant",
            original["variant"],
            "--workers",
            "1",
            "--timeout",
            "900",
        ]
        with (retry / "evaluation-driver.log").open("w") as log:
            result = subprocess.run(
                evaluate,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ | {"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"},
            )
        overrides.append(
            {
                "condition": original["condition"],
                "instance_id": target["instance_id"],
                "record": record,
                "evaluation_root": str(retry / "evaluation" / original["variant"]),
                "previous_trajectory": str(Path(original["output"]) / f"{target['instance_id']}.traj.json"),
                "timeout_event": target["timeout_event"],
                "evaluation_exit_code": result.returncode,
            }
        )
        write_json(
            root / "trial-overrides.json",
            {"reason": "infrastructure timeout only; no score-based retries", "entries": overrides},
        )
    recovery["state"] = (
        "complete" if all(e["record"]["valid"] and e["evaluation_exit_code"] == 0 for e in overrides) else "failed"
    )
    recovery["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(root / "recovery-plan.json", recovery)
    print("RECOVERY_FINISHED", recovery["state"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if len(sys.argv) > 1:
            state_path = Path(sys.argv[1]).resolve() / "recovery-plan.json"
            if state_path.exists():
                state = json.loads(state_path.read_text())
                state.update({"state": "failed", "error_type": type(exc).__name__})
                write_json(state_path, state)
        raise
