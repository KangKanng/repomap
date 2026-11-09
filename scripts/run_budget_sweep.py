"""Run a balanced offline budget sweep and incrementally grade its predictions."""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pull_docker import BASE, PROJECT
from run_swebench import run_entry

from mini_swe_repomap.env import load_environment


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory prepared by prepare_budget_sweep.py")
    parser.add_argument("--collector-runtime", type=Path, required=True)
    parser.add_argument("--budgets", nargs="+", type=int, default=[0, 1024, 2048, 4096])
    parser.add_argument("--map-refresh", choices=["interval", "once"], default="interval")
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cost-limit", type=float, default=3)
    parser.add_argument("--step-limit", type=int, default=250)
    parser.add_argument("--process-timeout", type=int, default=1380, help="Hard process deadline, including API waits")
    parser.add_argument("--eval-python", type=Path, default=Path("/root/.cache/repomap-eval-venv/bin/python"))
    parser.add_argument("--eval-timeout", type=int, default=900)
    parser.add_argument(
        "--no-evaluate", action="store_true", help="Skip official grading (e.g. deterministic smoke tests)"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if (
        min(args.budgets) < 0
        or args.workers < 1
        or args.step_limit < 1
        or args.cost_limit < 0
        or args.process_timeout < 1
    ):
        parser.error("Budgets and cost limit must be nonnegative; workers and steps must be positive")
    load_environment(args.env_file, default_path=PROJECT / ".env")
    model = args.model or os.getenv("MSWEA_MODEL_NAME") or os.getenv("MODEL_NAME")
    if not model:
        parser.error("Set --model, MODEL_NAME, or MSWEA_MODEL_NAME")
    root = args.output.resolve()
    coverage_path = root / "scan-coverage.json"
    if not coverage_path.exists() or not json.loads(coverage_path.read_text()).get("verified"):
        parser.error("Run prepare_budget_sweep.py successfully before starting inference")
    if not args.dry_run and not args.no_evaluate and not args.eval_python.is_file():
        parser.error("Official evaluation Python environment is missing")
    selection = root / "selection-pinned.json"
    items = json.loads(selection.read_text())
    by_id = {item["instance_id"]: item for item in items}
    inventory = json.loads((BASE / "inventory.json").read_text())
    lines = {item["instance_id"]: item["repository"]["source_lines"] for item in inventory["images"]}
    ordered = sorted(items, key=lambda item: (lines.get(item["instance_id"], 0), item["instance_id"]))
    budgets = list(dict.fromkeys(args.budgets))
    conditions = {}
    for budget in budgets:
        label = f"budget-{budget}"
        directory = root / "conditions" / f"{root.name}-b{budget}"
        variant = "plain" if budget == 0 else "repomap"
        command = [
            sys.executable,
            str(PROJECT / "scripts/run_swebench.py"),
            "--dry-run",
            "--offline",
            "--skip-preflight",
            "--selection",
            str(selection),
            "--variants",
            variant,
            "--map-tokens",
            str(budget or 1024),
            "--model",
            model,
            "--workers",
            "1",
            "--cost-limit",
            str(args.cost_limit),
            "--step-limit",
            str(args.step_limit),
            "--collector-runtime",
            str(args.collector_runtime),
            "--output",
            str(directory),
            "--config",
            str(PROJECT / "src/mini_swe_repomap/config/offline.yaml"),
            "--config",
            str(root / "scan-config.yaml"),
        ]
        if args.map_refresh == "once":
            command += ["--config", str(PROJECT / "src/mini_swe_repomap/config/once.yaml")]
        built = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        entries = []
        for line in built.stdout.splitlines():
            argv = shlex.split(line)
            if "--instance" not in argv:
                raise RuntimeError("Unexpected output from shared runner dry run")
            iid = argv[argv.index("--instance") + 1]
            trajectory = Path(argv[argv.index("-o") + 1])
            entries.append(
                {
                    "condition": label,
                    "budget": budget,
                    "variant": variant,
                    "item": by_id[iid],
                    "output": str(trajectory.parent),
                    "command": argv,
                }
            )
        if len(entries) != len(items):
            raise RuntimeError("Incomplete command plan")
        conditions[label] = {"budget": budget, "directory": str(directory), "variant": variant, "entries": entries}
    # Rotate all budgets through each execution position, balancing provider/cache order.
    plan = []
    for index, item in enumerate(ordered):
        for offset in range(len(budgets)):
            label = f"budget-{budgets[(index + offset) % len(budgets)]}"
            plan.append(
                next(e for e in conditions[label]["entries"] if e["item"]["instance_id"] == item["instance_id"])
            )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "tasks": len(plan),
                    "budgets": budgets,
                    "order": [{"instance_id": e["item"]["instance_id"], "budget": e["budget"]} for e in plan],
                    "entries": plan,
                },
                indent=2,
            )
        )
        return
    if (root / "plan.json").exists():
        if not args.resume:
            parser.error("Sweep already has a plan; use --resume to avoid duplicate paid runs")
        if json.loads((root / "plan.json").read_text()) != plan:
            parser.error("Resume settings differ from the saved plan")
    else:
        write_json(root / "plan.json", plan)
    records = (
        json.loads((root / "summary.json").read_text())["runs"]
        if args.resume and (root / "summary.json").exists()
        else []
    )
    completed_keys = {(r["condition"], r["instance_id"]) for r in records}
    lock = threading.Lock()
    inference_done = threading.Event()
    predictions = defaultdict(dict)
    for record in records:
        data = json.loads(Path(record["trajectory"]).read_text())
        predictions[record["output_group"]][record["instance_id"]] = {
            "instance_id": record["instance_id"],
            "model_name": model,
            "model_patch": data["info"].get("submission", ""),
        }
    for label, condition in conditions.items():
        directory = Path(condition["directory"])
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "plan.json", condition["entries"])
        write_json(directory / "summary.json", {"errors": 0, "runs": [r for r in records if r["condition"] == label]})
    experiment = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "budgets": budgets,
        "instances": len(items),
        "planned_tasks": len(plan),
        "network_mode": "none",
        "workers": args.workers,
        "evaluation_workers": 1,
        "evaluation_enabled": not args.no_evaluate,
        "cost_limit_per_instance": args.cost_limit,
        "step_limit": args.step_limit,
        "wall_time_limit_seconds": 1200,
        "process_timeout_seconds": args.process_timeout,
        "repomap_refresh": args.map_refresh,
        "context_messages": 0 if args.map_refresh == "once" else 6,
        "refresh_interval": 1 if args.map_refresh == "interval" else None,
        "scan_limits": json.loads(coverage_path.read_text())["scan_limits"],
        "history_policy": "mini accumulated history, no summary",
        "order": "source-lines ascending; rotating budget order",
        "conditions": {label: {k: v for k, v in c.items() if k != "entries"} for label, c in conditions.items()},
        "source_hashes": {
            str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (PROJECT / "src/mini_swe_repomap").glob("*.py")
        },
    }
    write_json(root / "experiment.json", experiment)
    evaluation_state = {}

    def grade():
        if args.no_evaluate:
            return
        graded_counts = defaultdict(int)
        failures = defaultdict(int)
        while True:
            with lock:
                counts = {label: sum(r["condition"] == label for r in records) for label in conditions}
            candidates = [
                label
                for label in conditions
                if counts[label] > graded_counts[label]
                and failures[label] < 3
                and (counts[label] >= 3 or inference_done.is_set())
            ]
            if not candidates:
                if inference_done.is_set():
                    return
                inference_done.wait(3)
                continue
            label = min(candidates, key=lambda name: graded_counts[name])
            condition = conditions[label]
            command = [
                str(args.eval_python),
                str(PROJECT / "scripts/evaluate_swebench_offline.py"),
                condition["directory"],
                "--variant",
                condition["variant"],
                "--completed-only",
                "--workers",
                "1",
                "--timeout",
                str(args.eval_timeout),
            ]
            started = time.monotonic()
            with (root / "evaluation-driver.log").open("a") as log:
                log.write(f"\nCONDITION {label} completed={counts[label]}\n")
                log.flush()
                result = subprocess.run(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=os.environ | {"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"},
                )
            if result.returncode == 0:
                graded_counts[label] = counts[label]
                failures[label] = 0
            else:
                failures[label] += 1
            evaluation_state[label] = {
                "completed_at_last_pass": graded_counts[label],
                "exit_code": result.returncode,
                "consecutive_errors": failures[label],
                "last_pass_seconds": time.monotonic() - started,
            }
            write_json(root / "evaluation-progress.json", evaluation_state)
            print("EVALUATION", label, "completed", graded_counts[label], "exit", result.returncode, flush=True)

    def grade_safely():
        try:
            grade()
        except Exception as exc:
            evaluation_state["_driver_error"] = {"exit_code": 1, "error": str(exc)}
            write_json(root / "evaluation-progress.json", evaluation_state)

    grader = threading.Thread(target=grade_safely, name="official-offline-evaluator")
    grader.start()

    def execute(entry):
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        try:
            record = run_entry(entry | {"process_timeout_seconds": args.process_timeout})
        except Exception as exc:
            record = {
                "variant": entry["variant"],
                "repo": entry["item"]["repo"],
                "instance_id": entry["item"]["instance_id"],
                "trajectory": str(Path(entry["output"]) / (entry["item"]["instance_id"] + ".traj.json")),
                "output_group": str(Path(entry["output"]).parent),
                "valid": False,
                "exit_status": type(exc).__name__,
                "error": str(exc),
            }
        return record | {
            "condition": entry["condition"],
            "budget": entry["budget"],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - started,
        }

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(execute, e)
                for e in plan
                if (e["condition"], e["item"]["instance_id"]) not in completed_keys
            ]
            for future in as_completed(futures):
                record = future.result()
                submission = record.pop("submission", "")
                group = record["output_group"]
                predictions[group][record["instance_id"]] = {
                    "instance_id": record["instance_id"],
                    "model_name": model,
                    "model_patch": submission or "",
                }
                write_json(Path(group) / "preds.json", predictions[group])
                with lock:
                    records.append(record)
                    write_json(
                        root / "summary.json",
                        {"planned": len(plan), "errors": sum(not r["valid"] for r in records), "runs": records},
                    )
                    subset = [r for r in records if r["condition"] == record["condition"]]
                    write_json(
                        Path(conditions[record["condition"]]["directory"]) / "summary.json",
                        {"errors": sum(not r["valid"] for r in subset), "runs": subset},
                    )
                print(
                    "INFERENCE",
                    len(records),
                    "/",
                    len(plan),
                    record["condition"],
                    record["repo"],
                    record["exit_status"],
                    flush=True,
                )
    finally:
        inference_done.set()
        grader.join()
    experiment["finished_at"] = datetime.now(timezone.utc).isoformat()
    experiment["inference_errors"] = sum(not r["valid"] for r in records)
    experiment["evaluation"] = evaluation_state
    write_json(root / "experiment.json", experiment)
    incomplete_evaluation = not args.no_evaluate and any(
        evaluation_state.get(label, {}).get("completed_at_last_pass") != len(items) for label in conditions
    )
    if (
        len(records) != len(plan)
        or experiment["inference_errors"]
        or incomplete_evaluation
        or any(s["exit_code"] for s in evaluation_state.values())
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
