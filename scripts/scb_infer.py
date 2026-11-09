"""Run one SCBench problem chain with the project's mini-swe-agent v2."""

import argparse
import hashlib
import json
import logging
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import yaml
from jinja2 import Template
from minisweagent.config import get_config_from_spec
from minisweagent.environments.docker import DockerEnvironment

from mini_swe_repomap.agent import RepoMapAgent
from mini_swe_repomap.env import load_environment
from mini_swe_repomap.env_model import EnvLitellmModel

PROJECT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}


class CheckpointHardTimeout(TimeoutError):
    pass


def checkpoint_timeout(signum, frame):
    raise CheckpointHardTimeout("Checkpoint exceeded its process-level time limit")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def snapshot(source, target):
    shutil.copytree(source, target, ignore=lambda _, names: [n for n in names if n in IGNORED or n.endswith(".pyc")])


def render_task(problem_dir, item, checkpoint, harness):
    # Match upstream common.render.render_prompt without importing its v1 stack.
    spec = (problem_dir / f"{checkpoint}.md").read_text()
    spec = re.sub(r"^<!-- [\s\S]+ -->\n?", "", spec)
    spec = spec.replace("%%%ENTRYPOINT:entry_file%%%", item["entry_file"] + ".py")
    spec = spec.replace("%%%ENTRYPOINT:entry_command%%%", "python " + item["entry_file"] + ".py")
    template = (harness / "configs/prompts/just-solve.jinja").read_text()
    return Template(template).render(spec=spec, is_continuation=checkpoint != item["checkpoints"][0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--variant", choices=["plain", "once-full"], required=True)
    parser.add_argument("--max-checkpoints", type=int)
    args = parser.parse_args()
    args.output = args.output.resolve()
    load_environment(None, default_path=PROJECT / ".env")
    protocol = json.loads((args.output / "protocol.json").read_text())
    item = next(x for x in protocol["selection"] if x["name"] == args.problem)
    problem_dir = Path(protocol["problems_path"]) / args.problem
    root = args.output / args.variant / args.problem
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    cfg = yaml.safe_load((problem_dir / "config.yaml").read_text())
    for asset in cfg.get("static_assets", {}).values():
        src = problem_dir / asset["path"]
        dst = workspace / asset.get("save_path", asset["path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    defaults = get_config_from_spec("mini.yaml")
    defaults["agent"].pop("mode", None)
    env = DockerEnvironment(
        image=protocol["inference_image_id"],
        cwd="/workspace",
        timeout=120,
        container_timeout="12h",
        run_args=[
            "--rm",
            "--pull=never",
            "--network=bridge",
            "--label",
            "repomap.scb=" + args.output.name,
            "--volume",
            f"{workspace}:/workspace",
        ],
        env={
            "PAGER": "cat",
            "PIP_PROGRESS_BAR": "off",
            "PYTHONUNBUFFERED": "1",
            "VIRTUAL_ENV": "/workspace/.venv",
            "PATH": "/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        },
    )
    write_json(root / "container.json", {"id": env.container_id, "workspace": str(workspace)})
    try:
        result = env.execute({"command": "python3 -m venv .venv && touch requirements.txt"})
        if result["returncode"]:
            raise RuntimeError(result["output"])
        completed_now = 0
        error = None
        for checkpoint in item["checkpoints"]:
            directory = root / checkpoint
            if (directory / "inference.json").exists():
                previous = json.loads((directory / "inference.json").read_text())
                if previous.get("error"):
                    error = previous["error"]
                    break
                continue
            directory.mkdir()
            prompt = render_task(problem_dir, item, checkpoint, Path(protocol["harness_path"]))
            (directory / "prompt.md").write_text(prompt)
            model = EnvLitellmModel(
                **(
                    defaults["model"]
                    | {
                        "model_name": protocol["model"],
                        "model_kwargs": defaults["model"].get("model_kwargs", {}) | {"timeout": 120},
                    }
                )
            )
            repomap = yaml.safe_load((PROJECT / "src/mini_swe_repomap/config/full-once.yaml").read_text())["agent"][
                "repomap"
            ]
            repomap["map_tokens"] = 0 if args.variant == "plain" else 1024
            agent = RepoMapAgent(
                model,
                env,
                **(
                    defaults["agent"]
                    | {
                        "repomap": repomap,
                        "step_limit": protocol["step_limit"],
                        "cost_limit": protocol["cost_limit_per_checkpoint"],
                        "wall_time_limit_seconds": protocol["wall_time_limit_seconds"],
                        "output_path": directory / "trajectory.json",
                    }
                ),
            )
            started = time.time()
            record = {
                "problem": args.problem,
                "variant": args.variant,
                "checkpoint": checkpoint,
                "checkpoint_index": item["checkpoints"].index(checkpoint) + 1,
            }
            write_json(directory / "started.json", record | {"started_unix": started})
            error = None
            previous_handler = signal.signal(signal.SIGALRM, checkpoint_timeout)
            model.abort_exceptions = [*model.abort_exceptions, CheckpointHardTimeout]
            signal.setitimer(signal.ITIMER_REAL, protocol.get("checkpoint_hard_timeout_seconds", 1380))
            try:
                result = agent.run(prompt)
            except Exception as exc:
                error = type(exc).__name__
                result = {"exit_status": error}
                logging.exception("Inference failed: %s %s %s", args.problem, args.variant, checkpoint)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
            agent.save(directory / "trajectory.json")
            maps = [
                m["content"]
                for m in agent.messages
                if isinstance(m.get("content"), str) and m["content"].startswith("<repository_map>")
            ]
            if maps:
                (directory / "repository-map.txt").write_text(maps[0])
            snapshot(workspace, directory / "snapshot")
            write_json(
                directory / "inference.json",
                record
                | {
                    "exit_status": result.get("exit_status"),
                    "error": error,
                    "elapsed_seconds": time.time() - started,
                    "model_calls": agent.n_calls,
                    "cost": agent.cost,
                    "map_count": len(maps),
                    "map_tokens": agent.repo_map.token_count(maps[0]) if maps else 0,
                    "map_sha256": hashlib.sha256(maps[0].encode()).hexdigest() if maps else None,
                    "snapshot": str(directory / "snapshot"),
                    "trajectory": str(directory / "trajectory.json"),
                },
            )
            if error:
                break
            completed_now += 1
            if args.max_checkpoints and completed_now >= args.max_checkpoints:
                break
        completed = [c for c in item["checkpoints"] if (root / c / "inference.json").exists()]
        write_json(
            root / "chain-finished.json",
            {
                "finished_unix": time.time(),
                "error": error,
                "completed_checkpoints": completed,
                "complete": len(completed) == len(item["checkpoints"]),
            },
        )
    finally:
        subprocess.run(["docker", "rm", "-f", env.container_id], capture_output=True, timeout=30)
        env.container_id = None


if __name__ == "__main__":
    main()
