"""Select and run paired plain/once-full SCBench problem chains and official evaluation."""

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import dotenv_values
from scb_infer import write_json
from search_metrics import SearchCounter

from mini_swe_repomap.env import load_environment

PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / ".venv/bin/python"


def revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def select_problems(root, count=25, seed=20261109):
    items = []
    excluded = []
    for path in sorted(root.glob("*/config.yaml")):
        config = yaml.safe_load(path.read_text())
        if path.parent.name in {"mocked_http", "test_translator"}:
            excluded.append(
                {
                    "name": path.parent.name,
                    "reason": "Requires additional broker/multi-language toolchain setup outside the Python runner scope.",
                }
            )
            continue
        checkpoints = sorted(config["checkpoints"], key=lambda k: config["checkpoints"][k]["order"])
        items.append(
            {k: config[k] for k in ["name", "category", "difficulty", "entry_file"]}
            | {"checkpoints": checkpoints, "test_dependencies": config.get("test_dependencies", [])}
        )
    if not 20 <= count <= 30 or count > len(items):
        raise ValueError("Choose 20–30 independent problems")
    rng = random.Random(seed)
    rng.shuffle(items)
    levels = ["Easy", "Medium", "Hard"]
    quota = {level: count // 3 + int(i < count % 3) for i, level in enumerate(levels)}
    chosen = []
    # Cover each category first, then fill the balanced difficulty quotas.
    categories = sorted({x["category"] for x in items}, key=lambda c: sum(x["category"] == c for x in items))
    for category in categories:
        candidates = [x for x in items if x["category"] == category and quota[x["difficulty"]] > 0]
        item = candidates[0]
        chosen.append(item)
        quota[item["difficulty"]] -= 1
    for item in items:
        if item not in chosen and quota[item["difficulty"]] > 0:
            chosen.append(item)
            quota[item["difficulty"]] -= 1
    return sorted(chosen, key=lambda x: (len(x["checkpoints"]), x["name"])), excluded


def clean_evaluation_env(protocol):
    # Official evaluation needs no model credentials.
    env = {
        k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    # Upstream's dotenv 1.1.1 ignores PYTHON_DOTENV_DISABLED. Empty existing
    # variables prevent load_dotenv(override=False) from restoring credentials.
    for name in (
        set(os.environ)
        | set(dotenv_values(PROJECT / ".env"))
        | {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "MODEL_API_KEY"}
    ):
        if any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            env[name] = ""
    env["SCBENCH_PROBLEMS_PATH"] = protocol["problems_path"]
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PATH"] = str(Path(shutil.which("uv")).parent) + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def execute(command, log_path, *, timeout, env=None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return -signal.SIGTERM


def execute_chain(output, protocol, item, variant, command):
    """Reattach to an existing chain after a controller restart, without rerunning it."""
    root = output / variant / item["name"]
    container = root / "container.json"
    deadline = time.monotonic() + len(item["checkpoints"]) * 1400
    while container.exists():
        identifier = json.loads(container.read_text())["id"]
        state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", identifier],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if state.returncode or state.stdout.strip() != "true":
            break
        if time.monotonic() > deadline:
            return -signal.SIGTERM
        time.sleep(5)
    finished = root / "chain-finished.json"
    if finished.exists():
        record = json.loads(finished.read_text())
        if record.get("complete") or record.get("error"):
            return 0
    return execute(command, root / "runner.log", timeout=len(item["checkpoints"]) * 1400)


def evaluate(output, protocol, problem, checkpoint, snapshot, destination):
    from evaluate_scb_snapshots import evaluate_prepared

    return evaluate_prepared(output, protocol, problem, checkpoint, snapshot, destination)


def evaluate_without_setup(output, protocol, problem, checkpoint, snapshot, destination):
    command = [
        str(Path(protocol["harness_path"]) / ".venv/bin/slop-code"),
        "--quiet",
        "eval-snapshot",
        str(snapshot),
        "-o",
        str(destination),
        "-p",
        problem,
        "-c",
        checkpoint,
        "-e",
        str(output / "evaluation-env.yaml"),
        "--json",
    ]
    code = execute(command, destination / "driver.log", timeout=1800, env=clean_evaluation_env(protocol))
    record = {"returncode": code, "command": command}
    reports = sorted(destination.glob("evaluation.json"))
    if reports:
        record["report"] = str(reports[0])
    write_json(destination / "execution.json", record)
    return record


def prepare(args):
    selection, excluded = select_problems(args.problems, args.count, args.seed)
    args.output.mkdir(parents=True, exist_ok=False)
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True
    ).strip()
    protocol = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "harness_path": str(args.harness),
        "harness_commit": revision(args.harness),
        "problems_path": str(args.problems),
        "problems_commit": revision(args.problems),
        "selection": selection,
        "excluded_before_inference": excluded,
        "model": os.getenv("MSWEA_MODEL_NAME") or os.environ["MODEL_NAME"],
        "repeats": 1,
        "variants": ["plain", "once-full"],
        "inference_image_id": image_id,
        "step_limit": 250,
        "cost_limit_per_checkpoint": 3.0,
        "wall_time_limit_seconds": 1200,
        "inference_workers": args.workers,
        "evaluation_workers": 2,
        "context_policy": "Fresh conversation at each checkpoint; workspace preserved along each independent variant chain; official current-checkpoint prompt only.",
        "map_policy": "Full map once at each checkpoint, retained unchanged during that checkpoint; no scan limits. Plain disables collection.",
        "evaluation_policy": "Official slop-code eval-snapshot and local runtime. Prepare a uv project and lock from requirements.txt in a disposable copy before official uv run, because LocalExecRuntime skips setup commands. Prior tests follow unmodified configs; no test feedback to inference.",
        "selection_policy": "Seeded category coverage followed by balanced difficulty quotas; no outcome-based replacement.",
        "interpretation": "Independent generated-code chains measure end-to-end policy outcomes, not a controlled same-code navigation effect; checkpoint 1 usually has no source map.",
    }
    protocol["implementation_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), PROJECT / "scripts/scb_infer.py"]
    }
    write_json(args.output / "protocol.json", protocol)
    write_json(args.output / "selection.json", selection)
    evaluation_env = yaml.safe_load((args.harness / "configs/environments/local-py.yaml").read_text())
    evaluation_env.pop("setup", None)
    evaluation_env["commands"]["command"] = "uv run --quiet"
    (args.output / "evaluation-env.yaml").write_text(yaml.safe_dump(evaluation_env, sort_keys=False))
    (args.output / "evaluation-env-v2.yaml").write_text(yaml.safe_dump(evaluation_env, sort_keys=False))
    return protocol


def find_evaluation(directory):
    for path in directory.glob("*.json"):
        if path.name == "execution.json":
            continue
        try:
            obj = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if "infrastructure_failure" in obj or "infrastructure_failure" in obj.get("summary", {}):
            return obj
    return None


def trial_metrics(path):
    record = json.loads(path.read_text())
    data = json.loads(Path(record["trajectory"]).read_text())
    search, _ = SearchCounter().messages(data["messages"])
    usage = Counter()
    models = set()
    saved_responses = 0
    cost_sources = Counter()
    for message in data["messages"]:
        response = message.get("extra", {}).get("response", {})
        if isinstance(response, dict):
            saved_responses += bool(response.get("id") or response.get("choices"))
            if response.get("model"):
                models.add(response["model"])
            response_usage = response.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] += response_usage.get(key, 0) or 0
        if message.get("role") == "assistant" and "cost" in message.get("extra", {}):
            cost_sources[message["extra"].get("cost_source", "local_price_estimate")] += 1
    evaluation_dir = path.parent / "evaluation"
    if len(path.parents) > 3 and (path.parents[3] / "evaluation-env-v2.yaml").exists():
        evaluation_dir = path.parent / "evaluation-v2"
        if not evaluation_dir.exists():
            execution = path.parent / "evaluation" / "execution.json"
            if execution.exists() and json.loads(execution.read_text()).get("setup_policy"):
                evaluation_dir = execution.parent
    evaluation = find_evaluation(evaluation_dir)
    record["evaluation_directory"] = str(evaluation_dir)
    record.update(search | dict(usage) | {"returned_models": sorted(models), "evaluated": evaluation is not None})
    record["unrecorded_model_calls"] = max(record.get("model_calls", saved_responses) - saved_responses, 0)
    record["cost_sources"] = dict(cost_sources)
    if evaluation:
        summary = evaluation.get("summary", evaluation)
        passed = evaluation.get("pass_counts", {})
        total = evaluation.get("total_counts", {})
        infrastructure_failure = summary.get("infrastructure_failure", False)
        core_pass = total.get("Core", 0) > 0 and passed.get("Core", 0) == total.get("Core", 0)
        record.update(
            {
                "infrastructure_failure": infrastructure_failure,
                "passed_policy": core_pass and not infrastructure_failure,
                "all_non_error_passed": not infrastructure_failure
                and all(passed.get(k, 0) == total.get(k, 0) for k in ["Core", "Functionality", "Regression"]),
                "tests_passed": sum(passed.values()),
                "tests_total": sum(total.values()),
                "core_passed": passed.get("Core", 0),
                "core_total": total.get("Core", 0),
                "regression_passed": passed.get("Regression", 0),
                "regression_total": total.get("Regression", 0),
            }
        )
    return record


def report(output, protocol, *, state, chains_done=0, errors=None):
    trials = [
        trial_metrics(path)
        for variant in protocol["variants"]
        for path in sorted((output / variant).glob("*/checkpoint_*/inference.json"))
    ]
    expected = sum(len(x["checkpoints"]) for x in protocol["selection"])
    status = {
        "state": state,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "chains_done": chains_done,
        "chains_expected": len(protocol["selection"]) * 2,
        "inference_done": len(trials),
        "inference_expected": expected * 2,
        "evaluations_done": sum(t["evaluated"] for t in trials),
        "errors": errors or [],
    }
    write_json(output / "status.json", status)
    write_json(output / "trials.json", trials)
    if trials:
        fields = sorted({k for t in trials for k in t})
        with (output / "trials.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(trials)
    lines = [
        "# SCBench plain 与 once-full 对比",
        "",
        f"状态：{state}；25题完整链（实际题数 {len(protocol['selection'])}）；每组预定 {expected} 个 checkpoint。",
        "",
        f"模型：`{protocol['model']}`；题库版本：`{protocol['problems_commit']}`。",
        "",
        "每个 checkpoint 重置对话、保留本组既有代码；只提供当前规格。once-full 在每个 checkpoint 开始注入完整地图一次。",
        "官方 eval-snapshot 使用 local-py 环境判分；测试与参考实现不进入 Agent 工作区，判题反馈不提供给 Agent。",
        "",
        "| 指标 | plain | once-full |",
        "| --- | ---: | ---: |",
    ]
    groups = {v: [t for t in trials if t["variant"] == v] for v in protocol["variants"]}
    for label, fn in [
        ("已推理 checkpoint", lambda ts: len(ts)),
        ("正常提交", lambda ts: sum(t["exit_status"] == "Submitted" for t in ts)),
        ("已判题", lambda ts: sum(t["evaluated"] for t in ts)),
        ("官方 passed_policy", lambda ts: sum(t.get("passed_policy") is True for t in ts)),
        ("显式搜索子命令", lambda ts: sum(t["search_commands"] for t in ts)),
        ("模型调用", lambda ts: sum(t["model_calls"] for t in ts)),
        ("输入 token", lambda ts: sum(t.get("prompt_tokens", 0) for t in ts)),
        ("输出 token", lambda ts: sum(t.get("completion_tokens", 0) for t in ts)),
        ("已返回响应费用 / USD", lambda ts: round(sum(t["cost"] for t in ts), 6)),
    ]:
        lines.append(f"| {label} | {fn(groups['plain'])} | {fn(groups['once-full'])} |")
    lines += [
        "",
        "首个 checkpoint 通常从空代码目录开始。主配对分析限双方正常提交、判题有效的后续 checkpoint；失败运行不会以零搜索计作收益。",
        "两组代码随链独立演化，因此差异反映整体策略结果。每题只重复一次，checkpoint 在题内相关，不能视作独立样本。",
        "",
        "| 题目 | 难度 | 类别 | checkpoint 数 |",
        "| --- | --- | --- | ---: |",
    ]
    for item in protocol["selection"]:
        lines.append(f"| {item['name']} | {item['difficulty']} | {item['category']} | {len(item['checkpoints'])} |")
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return status


def run(output, protocol):
    errors = []
    with (
        ThreadPoolExecutor(max_workers=protocol["inference_workers"]) as infer_pool,
        ThreadPoolExecutor(max_workers=2) as eval_pool,
    ):
        inference = []
        for i, item in enumerate(protocol["selection"]):
            variants = protocol["variants"][:: (-1 if i % 2 else 1)]
            for variant in variants:
                command = [
                    str(PYTHON),
                    str(PROJECT / "scripts/scb_infer.py"),
                    "--output",
                    str(output),
                    "--problem",
                    item["name"],
                    "--variant",
                    variant,
                ]
                inference.append(infer_pool.submit(execute_chain, output, protocol, item, variant, command))
        queued = set()
        evaluation = []
        while True:
            for variant in protocol["variants"]:
                for path in (output / variant).glob("*/checkpoint_*/inference.json"):
                    if path in queued:
                        continue
                    queued.add(path)
                    record = json.loads(path.read_text())
                    if protocol.get("evaluation_managed_externally"):
                        continue
                    evaluation.append(
                        eval_pool.submit(
                            evaluate,
                            output,
                            protocol,
                            record["problem"],
                            record["checkpoint"],
                            Path(record["snapshot"]),
                            path.parent / "evaluation",
                        )
                    )
            done = sum(x.done() for x in inference)
            status = report(output, protocol, state="running", chains_done=done)
            print(json.dumps(status), flush=True)
            if done == len(inference) and all(x.done() for x in evaluation):
                break
            time.sleep(20)
        for future in inference:
            if future.result() != 0:
                errors.append({"stage": "inference", "returncode": future.result()})
        for future in evaluation:
            try:
                result = future.result()
                if result["returncode"] != 0:
                    errors.append({"stage": "evaluation", "result": result})
            except Exception as exc:
                errors.append({"stage": "evaluation", "error": str(exc)})
    report(
        output,
        protocol,
        state="completed_with_errors" if errors else "completed",
        chains_done=len(inference),
        errors=errors,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--harness", type=Path, default=PROJECT / "benchmarks/slop-code-bench")
    parser.add_argument("--problems", type=Path, default=PROJECT / "benchmarks/scb-problems")
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--prepared", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    load_environment(None, default_path=PROJECT / ".env")
    protocol = (
        json.loads((args.output / "protocol.json").read_text()) if args.prepared or args.report_only else prepare(args)
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "problems": len(protocol["selection"]),
                "checkpoints_per_variant": sum(len(x["checkpoints"]) for x in protocol["selection"]),
            }
        ),
        flush=True,
    )
    if args.report_only:
        report(args.output, protocol, state=json.loads((args.output / "status.json").read_text())["state"])
    elif not args.prepare_only:
        run(args.output, protocol)
    else:
        report(args.output, protocol, state="prepared")


if __name__ == "__main__":
    main()
