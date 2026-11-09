"""Queue repeated once budget sweeps, then interval sweeps, with continuous and final reports."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from pull_docker import PROJECT  # noqa: E402
from run_budget_sweep import write_json  # noqa: E402

from mini_swe_repomap.env import load_environment  # noqa: E402


def load(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True, help="Verified prepare_budget_sweep.py output")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collector-runtime", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--budgets", nargs="+", type=int, default=[0, 1024, 2048, 4096])
    parser.add_argument("--phases", nargs="+", choices=["once", "interval"], default=["once", "interval"])
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="Write validated plans only; no containers or model calls"
    )
    parser.add_argument("--pointer", type=Path, default=Path("/tmp/repomap-repeat-active.json"))
    args = parser.parse_args()
    if args.repeats < 1 or args.workers < 1 or min(args.budgets) < 0 or len(set(args.budgets)) != len(args.budgets):
        parser.error("Positive repeats/workers and unique nonnegative budgets required")
    load_environment(args.env_file, default_path=PROJECT / ".env")
    model = args.model or os.getenv("MSWEA_MODEL_NAME") or os.getenv("MODEL_NAME")
    if not model:
        parser.error("Model is missing")
    prepared, root = args.prepared.resolve(), args.output.resolve()
    coverage = load(prepared / "scan-coverage.json")
    items = load(prepared / "selection-pinned.json")
    if not coverage.get("verified") or any(not item["image"].startswith("sha256:") for item in items):
        parser.error("Verified, immutable images are required")
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "manager.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.resume and (root / "status.json").exists():
        previous_status = load(root / "status.json")
        child_pid = previous_status.get("child_pid")
        command_file = Path(f"/proc/{child_pid}/cmdline")
        if child_pid and command_file.exists() and str(root).encode() in command_file.read_bytes():
            parser.error("An earlier round driver is still running; do not duplicate paid inference")
    from minisweagent.config import get_config_from_spec
    from minisweagent.utils.serialize import recursive_merge

    cache, queue, entries = {}, [], []
    for phase in dict.fromkeys(args.phases):
        for repeat in range(1, args.repeats + 1):
            directory = root / phase / f"repeat-{repeat:02d}"
            directory.mkdir(parents=True, exist_ok=True)
            for name in ("selection-pinned.json", "scan-coverage.json", "scan-config.yaml"):
                target = directory / name
                if target.exists() and target.read_bytes() != (prepared / name).read_bytes():
                    parser.error(f"Prepared input changed: {target}")
                if not target.exists():
                    shutil.copyfile(prepared / name, target)
            if (directory / "summary.json").exists() and not args.resume:
                parser.error("Existing inference results require --resume")
            offset = (repeat - 1) % len(args.budgets)
            budgets = args.budgets[offset:] + args.budgets[:offset]
            command = [
                sys.executable,
                str(PROJECT / "scripts/run_budget_sweep.py"),
                "--output",
                str(directory),
                "--collector-runtime",
                str(args.collector_runtime.resolve()),
                "--budgets",
                *map(str, budgets),
                "--map-refresh",
                phase,
                "--model",
                model,
                "--workers",
                str(args.workers),
                "--process-timeout",
                "1380",
            ]
            dry = subprocess.run([*command, "--dry-run"], capture_output=True, text=True, check=True, timeout=90)
            plan = json.loads(dry.stdout)
            comparable = {}
            for entry in plan["entries"]:
                argv, config = entry["command"], {}
                for i, value in enumerate(argv):
                    if value == "-c":
                        spec = argv[i + 1]
                        if spec not in cache:
                            cache[spec] = get_config_from_spec(spec)
                        config = recursive_merge(config, cache[spec])
                settings = config["agent"]["repomap"]
                if (
                    settings.get("refresh", "interval") != phase
                    or settings["map_tokens"] != entry["budget"]
                    or settings.get("context_messages", 6) != (0 if phase == "once" else 6)
                    or "--network=none" not in config["environment"]["run_args"]
                    or "--pull=never" not in config["environment"]["run_args"]
                ):
                    raise ValueError("Effective config violates experiment protocol")
                control = copy.deepcopy(config)
                control["agent"]["repomap"].pop("map_tokens")
                iid = entry["item"]["instance_id"]
                if iid in comparable and comparable[iid] != control:
                    raise ValueError(f"Budget conditions differ in additional settings: {iid}")
                comparable[iid] = control
                entries.append({"phase": phase, "repeat": repeat, **entry})
            if len(plan["entries"]) != len(items) * len(args.budgets):
                raise ValueError("Incomplete plan")
            queue.append(
                {
                    "phase": phase,
                    "repeat": repeat,
                    "directory": str(directory),
                    "budgets_in_execution_order": budgets,
                    "command": command,
                }
            )
    paths = [
        *(PROJECT / "src/mini_swe_repomap").glob("*.py"),
        *(PROJECT / "src/mini_swe_repomap/config").glob("*.yaml"),
        *(PROJECT / "scripts").glob("*.py"),
    ]
    policy = {
        "model": model,
        "instances": len(items),
        "repeats": args.repeats,
        "phases": list(dict.fromkeys(args.phases)),
        "budgets": args.budgets,
        "planned_trials": len(entries),
        "network_mode": "none",
        "inference_workers": args.workers,
        "evaluation_workers": 1,
        "context_messages": {"once": 0, "interval": 6},
        "history": "mini accumulated history",
        "hard_process_timeout_seconds": 1380,
        "soft_wall_time_limit_seconds": 1200,
        "step_limit": 250,
        "cost_limit_per_trial_usd": 3,
        "primary_metric": "explicit_search_subcommands_per_task",
        "localization_reference": "post-hoc official baseline hunk context; never supplied to inference",
        "resolved_rate_is_constraint": True,
        "noninferiority_margin_specified": False,
        "source_hashes": {str(p.relative_to(PROJECT)): digest(p) for p in paths},
        "prepared_hashes": {
            name: digest(prepared / name)
            for name in ("selection-pinned.json", "scan-coverage.json", "scan-config.yaml")
        },
        "queue": queue,
    }
    previous = load(root / "protocol.json") if (root / "protocol.json").exists() else None
    if previous and previous != policy:
        parser.error("Protocol or code changed; do not resume paid runs with different settings")
    write_json(root / "protocol.json", policy)
    write_json(root / "launch-plan.json", entries)
    if args.dry_run:
        print(json.dumps({"output": str(root), "validated_trials": len(entries), "model_calls": 0}, indent=2))
        return
    from analyze_budget_sweep import summarize as summarize_round
    from analyze_repeated_budget import summarize as summarize_all

    environment = os.environ | {"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "MSWEA_SILENT_STARTUP": "1"}
    state = {"output": str(root), "state": "running", "planned": len(entries), "manager_pid": os.getpid(), "rounds": []}

    def progress():
        report = summarize_all(root)
        state.update(
            {
                "inference_completed": report["inference_completed"],
                "graded": report["graded"],
                "report": str(root / "report.md"),
            }
        )
        write_json(root / "status.json", state)
        write_json(args.pointer, state)

    for job in queue:
        directory = Path(job["directory"])
        finished = load(directory / "experiment.json") if (directory / "experiment.json").exists() else {}
        if finished.get("finished_at"):
            summarize_round(directory)
            state["rounds"].append(
                {
                    "phase": job["phase"],
                    "repeat": job["repeat"],
                    "state": "previously_finished",
                    "inference_errors": finished.get("inference_errors", 0),
                }
            )
            continue
        for relative, expected in policy["source_hashes"].items():
            if digest(PROJECT / relative) != expected:
                raise RuntimeError(f"Frozen source changed before next round: {relative}")
        state.update({"current_phase": job["phase"], "current_repeat": job["repeat"]})
        command = job["command"] + (["--resume"] if (directory / "plan.json").exists() else [])
        with (directory / "driver.log").open("a") as log:
            child = subprocess.Popen(
                command, cwd=PROJECT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
            )
            state["child_pid"] = child.pid
            progress()
            print("START", job["phase"], job["repeat"], "pid", child.pid, flush=True)
            while child.poll() is None:
                time.sleep(20)
                summarize_round(directory)
                progress()
        summarize_round(directory)
        if not (directory / "experiment.json").exists() or not load(directory / "experiment.json").get("finished_at"):
            state.update({"state": "failed", "exit_code": child.returncode})
            progress()
            raise SystemExit(
                "Round driver exited before recording completion; inspect driver.log and resume explicitly"
            )
        state["rounds"].append({"phase": job["phase"], "repeat": job["repeat"], "exit_code": child.returncode})
        progress()
    state["state"] = (
        "completed_with_errors"
        if any(r.get("exit_code") or r.get("inference_errors") for r in state["rounds"])
        else "completed"
    )
    state.pop("child_pid", None)
    progress()
    final = load(root / "repeat-report.json")
    audit_paths = [p for job in queue for p in Path(job["directory"]).rglob("network-audit.jsonl")]
    audits = [json.loads(line) for path in audit_paths for line in path.read_text().splitlines() if line.strip()]
    source_checks = {
        relative: digest(PROJECT / relative) == expected for relative, expected in policy["source_hashes"].items()
    }
    write_json(
        root / "final-audit.json",
        {
            "all_planned_inferences_recorded": final["inference_completed"] == policy["planned_trials"],
            "all_planned_evaluations_recorded": final["graded"] == policy["planned_trials"],
            "grading_containers_audited": len(audits),
            "all_audited_grading_networks_none": all(r["network_mode"] == "none" for r in audits),
            "source_hashes_unchanged": all(source_checks.values()),
            "source_checks": source_checks,
            "billing_issues": final["billing_issues"],
            "returned_requests_missing_map_metadata": sum(
                r.get("requests_missing_map_metadata", 0) for r in final["trials"]
            ),
            "map_hashes_valid": all(r.get("map_hashes_valid", True) for r in final["trials"]),
        },
    )
    for name in ("report.md", "repeat-report.json", "repeat-trials.csv"):
        shutil.copyfile(root / name, root / ("final-" + name))
    state["final_report"] = str(root / "final-report.md")
    write_json(root / "status.json", state)
    write_json(args.pointer, state)
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
