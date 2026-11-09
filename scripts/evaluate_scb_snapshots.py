"""Evaluate SCBench snapshots after preparing the official uv project setup."""

import argparse
import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from run_scb_compare import clean_evaluation_env, execute, find_evaluation
from scb_infer import IGNORED, write_json


def evaluate_prepared(output, protocol, problem, checkpoint, snapshot, destination):
    destination.mkdir(parents=True, exist_ok=True)
    env = clean_evaluation_env(protocol)
    env["UV_PYTHON"] = "/usr/bin/python3"
    env["UV_CACHE_DIR"] = "/root/.cache/uv"
    with tempfile.TemporaryDirectory(prefix="repomap-scb-eval-") as temporary:
        prepared = Path(temporary) / "source"
        shutil.copytree(snapshot, prepared, ignore=lambda _, names: [n for n in names if n in IGNORED])
        commands = []
        if not (prepared / "pyproject.toml").exists():
            commands.append([shutil.which("uv"), "init", "--bare", "--no-workspace", "--python", "/usr/bin/python3"])
        if not (prepared / "requirements.txt").exists():
            (prepared / "requirements.txt").write_text("")
        commands.append([shutil.which("uv"), "add", "--no-sync", "-r", "requirements.txt"])
        commands.append([shutil.which("uv"), "sync"])
        setup_codes = []
        # uv --directory handles the cwd without a shell or changing the parent's cwd.
        for i, command in enumerate(commands):
            command[1:1] = ["--directory", str(prepared)]
            code = execute(command, destination / f"setup-{i}.log", timeout=300, env=env)
            setup_codes.append(code)
            if code:
                record = {"stage": "dependency_setup", "returncode": code, "commands": commands}
                write_json(destination / "execution.json", record)
                return record
        bootstrap = destination / "bootstrap"
        bootstrap.mkdir(exist_ok=True)
        for filename in ["pyproject.toml", "uv.lock", ".python-version", "requirements.txt"]:
            if (prepared / filename).exists():
                shutil.copy2(prepared / filename, bootstrap / filename)
        problem_config = yaml.safe_load((Path(protocol["problems_path"]) / problem / "config.yaml").read_text())
        test_dependencies = [
            "pytest",
            "pytest-json-ctrf",
            "pytest-json-report",
            "pytest-timeout",
            "jsonschema",
            "deepdiff",
        ]
        test_dependencies += problem_config.get("test_dependencies", [])
        warm_command = [shutil.which("uvx"), *[f"--with={dep}" for dep in test_dependencies], "pytest", "--version"]
        warm_code = execute(warm_command, destination / "warm-tests.log", timeout=300, env=env)
        if warm_code:
            record = {"stage": "test_dependency_setup", "returncode": warm_code, "command": warm_command}
            write_json(destination / "execution.json", record)
            return record
        environment_file = output / "evaluation-env-v2.yaml"
        if problem == "execution_server":
            # Its official readiness deadline is 5 seconds. Use the environment
            # already synced above so the first class doesn't spend that budget
            # installing packages in the evaluator's disposable workspace.
            env["VIRTUAL_ENV"] = str(prepared / ".venv")
            evaluation_env = yaml.safe_load(environment_file.read_text())
            evaluation_env["commands"]["command"] = "uv run --active --quiet"
            environment_file = destination / "evaluation-env.yaml"
            environment_file.write_text(yaml.safe_dump(evaluation_env, sort_keys=False))
        command = [
            str(Path(protocol["harness_path"]) / ".venv/bin/slop-code"),
            "--quiet",
            "eval-snapshot",
            str(prepared),
            "-o",
            str(destination),
            "-p",
            problem,
            "-c",
            checkpoint,
            "-e",
            str(environment_file),
            "--json",
        ]
        isolated_command = [
            "unshare",
            "--net",
            "--",
            "/bin/bash",
            "-c",
            'ip link set lo up && exec "$@"',
            "scb-eval",
            *command,
        ]
        code = execute(isolated_command, destination / "driver.log", timeout=1800, env=env | {"UV_OFFLINE": "1"})
        record = {
            "returncode": code,
            "command": command,
            "original_snapshot": str(snapshot),
            "setup_commands": commands,
            "setup_returncodes": setup_codes,
            "setup_policy": "Prepare uv pyproject/lock in a disposable copy, then use official uv run entrypoint; source implementation unchanged.",
            "network_isolation": "Dedicated Linux network namespace; loopback enabled. Application and test dependencies warmed before offline evaluation.",
        }
        write_json(destination / "execution.json", record)
        return record


def watch(output, protocol, workers):
    queued = set()
    futures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            for variant in protocol["variants"]:
                for path in (output / variant).glob("*/checkpoint_*/inference.json"):
                    if path in queued:
                        continue
                    queued.add(path)
                    r = json.loads(path.read_text())
                    destination = path.parent / "evaluation-v2"
                    if (destination / "execution.json").exists():
                        continue
                    futures.append(
                        pool.submit(
                            evaluate_prepared,
                            output,
                            protocol,
                            r["problem"],
                            r["checkpoint"],
                            Path(r["snapshot"]),
                            destination,
                        )
                    )
            state = json.loads((output / "status.json").read_text())["state"]
            ready = sum(find_evaluation(p.parent / "evaluation-v2") is not None for p in queued)
            write_json(
                output / "evaluation-v2-status.json",
                {
                    "original_driver_state": state,
                    "snapshots_seen": len(queued),
                    "reports_ready": ready,
                    "evaluations_active_or_queued": sum(not f.done() for f in futures),
                },
            )
            print(json.dumps({"snapshots": len(queued), "evaluated": ready, "state": state}), flush=True)
            if state != "running" and all(f.done() for f in futures):
                break
            time.sleep(20)
        errors = []
        for f in futures:
            try:
                if (r := f.result())["returncode"]:
                    errors.append(r)
            except Exception as exc:
                errors.append({"error": str(exc)})
        write_json(output / "evaluation-v2-errors.json", errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--problems", nargs="+")
    args = parser.parse_args()
    output = args.output.resolve()
    protocol = json.loads((output / "protocol.json").read_text())
    env = yaml.safe_load((Path(protocol["harness_path"]) / "configs/environments/local-py.yaml").read_text())
    env.pop("setup", None)
    env["commands"]["command"] = "uv run --quiet"
    (output / "evaluation-env-v2.yaml").write_text(yaml.safe_dump(env, sort_keys=False))
    if args.preflight:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = []
            for item in protocol["selection"]:
                if args.problems and item["name"] not in args.problems:
                    continue
                checkpoint = item["checkpoints"][0]
                destination = output / "preflight-v2" / item["name"]
                if (destination / "execution.json").exists():
                    continue
                futures.append(
                    pool.submit(
                        evaluate_prepared,
                        output,
                        protocol,
                        item["name"],
                        checkpoint,
                        Path(protocol["problems_path"]) / item["name"] / "solutions" / checkpoint,
                        destination,
                    )
                )
            for f in futures:
                print(json.dumps(f.result()), flush=True)
        records = []
        for item in protocol["selection"]:
            report = find_evaluation(output / "preflight-v2" / item["name"])
            if report:
                records.append(
                    {
                        "problem": item["name"],
                        "infrastructure_failure": report["infrastructure_failure"],
                        "pass_counts": report["pass_counts"],
                        "total_counts": report["total_counts"],
                    }
                )
        write_json(output / "preflight-v2.json", records)
    else:
        watch(output, protocol, args.workers)


if __name__ == "__main__":
    main()
