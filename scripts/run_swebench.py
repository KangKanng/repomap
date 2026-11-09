"""Run baseline/Repo Map SWE-bench inference with an explicitly supplied model."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pull_docker import BASE, PROJECT, container_command

from mini_swe_repomap.env import load_environment


def run_entry(entry: dict) -> dict:
    directory = Path(entry["output"])
    directory.mkdir(parents=True)
    trajectory = directory / f"{entry['item']['instance_id']}.traj.json"
    record = {
        "variant": entry["variant"],
        "repo": entry["item"]["repo"],
        "instance_id": entry["item"]["instance_id"],
        "trajectory": str(trajectory),
        "output_group": str(directory.parent),
        "valid": False,
    }
    timed_out = False
    with (directory / "runner.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.run(
                entry["command"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ | {"MSWEA_SILENT_STARTUP": "1"},
                timeout=entry.get("process_timeout_seconds"),
            )
        except subprocess.TimeoutExpired:
            timed_out = True
    if timed_out:
        # A terminated Python worker cannot run DockerEnvironment.__del__.
        text = (directory / "runner.log").read_text(errors="replace")
        names = set(re.findall(r"(?:--name|Started container)\s+(minisweagent-[0-9a-f]+)", text))
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    if not trajectory.exists():
        return record | {"exit_status": "ProcessTimeout" if timed_out else "MissingTrajectory"}
    try:
        data = json.loads(trajectory.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if timed_out:
            return record | {"exit_status": "ProcessTimeout", "trajectory_incomplete": True}
        raise
    info = data.get("info", {})
    record.update(
        {
            "exit_status": info.get("exit_status"),
            "model_stats": info.get("model_stats", {}),
            "agent_type": info.get("config", {}).get("agent_type"),
            "submission": info.get("submission", ""),
            "map_present": any("<repository_map>" in json.dumps(message) for message in data.get("messages", [])),
        }
    )
    if timed_out:
        return record | {
            "exit_status": "ProcessTimeout",
            "submission": "",
            "process_timeout_seconds": entry["process_timeout_seconds"],
        }
    record["valid"] = (
        process.returncode == 0
        and record["exit_status"] in {"Submitted", "LimitsExceeded", "TimeExceeded", "RepeatedFormatError"}
        and record["agent_type"] == "mini_swe_repomap.agent.RepoMapAgent"
        and record["map_present"] == (entry["variant"] == "repomap")
    )
    return record


def offline_run_args(configs: list[str]) -> list[str]:
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
    from minisweagent.config import get_config_from_spec
    from minisweagent.utils.serialize import recursive_merge

    config = recursive_merge(*(get_config_from_spec(spec) for spec in configs))
    run_args = list(config.get("environment", {}).get("run_args", ["--rm"]))
    for index, argument in enumerate(run_args):
        if argument in {"--network", "--net"}:
            network = run_args[index + 1] if index + 1 < len(run_args) else ""
        elif argument.startswith(("--network=", "--net=")):
            network = argument.split("=", 1)[1]
        else:
            continue
        if network != "none":
            raise ValueError("--offline requires Docker network=none")
    if not any(
        argument in {"--network", "--net"} or argument.startswith(("--network=", "--net=")) for argument in run_args
    ):
        run_args.append("--network=none")
    if not any(argument.startswith("--pull") for argument in run_args):
        run_args.append("--pull=never")
    return run_args


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=BASE / "selection.json")
    parser.add_argument("--repos", nargs="+")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", help="Model name (default: MSWEA_MODEL_NAME or MODEL_NAME from environment/.env)")
    parser.add_argument(
        "--variants", nargs="+", choices=["baseline", "plain", "repomap"], default=["baseline", "repomap"]
    )
    parser.add_argument("--image-overrides", type=Path, help="JSON mapping instance IDs to prepared Docker images")
    parser.add_argument("--offline", action="store_true", help="Force Docker network=none and use the selected images")
    parser.add_argument("--map-tokens", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--cost-limit", type=float, default=3.0)
    parser.add_argument("--step-limit", type=int, default=250)
    parser.add_argument("--python-command", default="python3")
    parser.add_argument("--collector-runtime", type=Path, help="Standalone Python root mounted read-only in both modes")
    parser.add_argument("--config", action="append", default=[], help="Additional mini YAML or key=value config")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Print commands without containers or model calls")
    parser.add_argument("--env-file", type=Path, help="Environment file (default: project .env)")
    parser.add_argument(
        "--skip-preflight", action="store_true", help="For custom configs that prepare Python before the agent starts"
    )
    args = parser.parse_args(argv)
    load_environment(args.env_file, default_path=PROJECT / ".env")
    args.model = args.model or os.getenv("MSWEA_MODEL_NAME") or os.getenv("MODEL_NAME")
    if not args.dry_run and not args.model:
        parser.error("Set --model, MODEL_NAME or MSWEA_MODEL_NAME; use --dry-run to inspect the commands")
    if (
        min(args.map_tokens, args.workers, args.step_limit) < 1
        or args.cost_limit < 0
        or (args.limit is not None and args.limit < 1)
    ):
        parser.error("Token budget, workers, step limit and limit must be positive; cost limit cannot be negative")
    items = json.loads(args.selection.read_text(encoding="utf-8"))
    if args.image_overrides:
        overrides = json.loads(args.image_overrides.read_text(encoding="utf-8"))
        unknown = set(overrides) - {item["instance_id"] for item in items}
        if unknown:
            parser.error(f"Unknown image override instances: {', '.join(sorted(unknown))}")
        items = [
            item | {"original_image": item["image"], "image": overrides[item["instance_id"]]}
            if item["instance_id"] in overrides
            else item
            for item in items
        ]
    if args.repos:
        unknown = set(args.repos) - {item["repo"] for item in items}
        if unknown:
            parser.error(f"Unknown repositories: {', '.join(sorted(unknown))}")
        items = [item for item in items if item["repo"] in args.repos]
    if args.limit:
        items = items[: args.limit]
    if not items:
        parser.error("No selected instances")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or PROJECT / "benchmarks" / "results" / f"swebench-{stamp}-{uuid.uuid4().hex[:6]}").resolve()
    commands = []
    batch_config = str(PROJECT / "src" / "mini_swe_repomap" / "config" / "batch.yaml")
    runtime_volume = None
    if args.collector_runtime:
        if not args.offline:
            parser.error("--collector-runtime requires --offline")
        runtime = args.collector_runtime.expanduser().resolve()
        if not (runtime / "bin" / "python3").is_file():
            parser.error(f"Standalone Python not found: {runtime / 'bin' / 'python3'}")
        runtime_volume = f"{runtime}:/opt/repomap-python:ro"
        args.python_command = "/opt/repomap-python/bin/python3"
    try:
        docker_args = offline_run_args(["swebench.yaml", batch_config, *args.config]) if args.offline else None
    except ValueError as error:
        parser.error(str(error))
    if runtime_volume:
        docker_args += ["--volume", runtime_volume]
    for variant in dict.fromkeys(args.variants):
        for item in items:
            directory = output / variant / item["dataset"].rsplit("/", 1)[-1] / item["instance_id"]
            command = [
                sys.executable,
                "-m",
                "minisweagent.run.benchmarks.swebench_single",
                "--subset",
                item["dataset"],
                "--split",
                "test",
                "--instance",
                item["instance_id"],
                "-m",
                args.model or "provider/model",
                "-o",
                str(directory / f"{item['instance_id']}.traj.json"),
                "-c",
                "swebench.yaml",
                "-c",
                batch_config,
            ]
            if args.model == "deterministic":
                command += ["-c", "model.model_class=minisweagent.models.test_models.DeterministicModel"]
            else:
                command += ["-c", "model.model_class=mini_swe_repomap.env_model.EnvLitellmModel"]
            for extra in args.config:
                command += ["-c", extra]
            if args.offline or "original_image" in item:
                # A qualified class name keeps mini from replacing environment.image
                # with its own image name derived from the dataset instance ID.
                command += [
                    "-c",
                    "environment.environment_class=minisweagent.environments.docker.DockerEnvironment",
                    "-c",
                    f"environment.image={item['image']}",
                ]
            if docker_args is not None:
                command += ["-c", "environment.run_args=" + json.dumps(docker_args)]
            command += [
                "-c",
                f"agent.repomap.map_tokens={args.map_tokens if variant == 'repomap' else 0}",
                "-c",
                "agent.repomap.strict=true",
                "-c",
                f"agent.repomap.python_command={args.python_command}",
                "-c",
                f"agent.cost_limit={args.cost_limit}",
                "-c",
                f"agent.step_limit={args.step_limit}",
            ]
            commands.append(
                {
                    "variant": variant,
                    "item": item,
                    "output": str(directory),
                    "command": command,
                }
            )
    for entry in commands:
        print(shlex.join(entry["command"]), flush=True)
    if args.dry_run:
        return
    if output.exists() and any(output.iterdir()):
        parser.error(f"Output directory must be empty: {output}")
    if "repomap" in args.variants and not args.skip_preflight:
        check_python = """exec "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' """
        incompatible = []
        for item in items:
            command = container_command(item["image"], check_python, args.python_command)
            if runtime_volume:
                position = command.index(item["image"])
                command[position:position] = ["--volume", runtime_volume]
            checked = subprocess.run(command, capture_output=True, timeout=60)
            if checked.returncode:
                incompatible.append(item["repo"])
        if incompatible:
            parser.error(
                "Python 3.10+ preflight failed for: "
                + ", ".join(incompatible)
                + ". Select compatible repositories or prepare Python via --config and --skip-preflight."
            )
    output.mkdir(parents=True, exist_ok=True)
    (output / "plan.json").write_text(json.dumps(commands, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    statuses = []
    predictions = defaultdict(dict)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_entry, entry) for entry in commands]
        for future in as_completed(futures):
            record = future.result()
            predictions[record["output_group"]][record["instance_id"]] = {
                "instance_id": record["instance_id"],
                "model_name": args.model,
                "model_patch": record.pop("submission", "") or "",
            }
            group = Path(record["output_group"])
            (group / "preds.json").write_text(json.dumps(predictions[str(group)], indent=2) + "\n", encoding="utf-8")
            statuses.append(record)
            (output / "summary.json").write_text(
                json.dumps({"errors": sum(not r["valid"] for r in statuses), "runs": statuses}, indent=2) + "\n",
                encoding="utf-8",
            )
    print(f"Inference results: {output / 'summary.json'}", flush=True)
    if any(not record["valid"] for record in statuses):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
