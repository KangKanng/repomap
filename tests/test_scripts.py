import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from minisweagent.config import get_config_from_spec
from minisweagent.utils.serialize import recursive_merge

PROJECT = Path(__file__).resolve().parents[1]


def test_dry_runs_and_legacy_entry_from_another_directory(tmp_path):
    def invoke(script, *args):
        return subprocess.run(
            [sys.executable, str(PROJECT / script), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout

    images = invoke("scripts/pull_docker.py", "--dry-run")
    assert len(images.splitlines()) == 20
    assert invoke("benchmarks/docker_repositories/pull.py", "--dry-run") == images
    assert len(invoke("scripts/run_experiments.py", "--dry-run", "--limit", "2").splitlines()) == 2
    commands = invoke("scripts/run_swebench.py", "--dry-run", "--repos", "pallets/flask")
    assert commands.count("minisweagent.run.benchmarks.swebench_single") == 2
    assert "agent.repomap.map_tokens=0" in commands and "agent.repomap.map_tokens=1024" in commands
    assert not list(tmp_path.iterdir())


def test_plain_script_is_offline_and_disables_map(tmp_path):
    selection = tmp_path / "selection.json"
    overrides = tmp_path / "images.json"
    selection.write_text(
        json.dumps(
            [{"repo": "fixture/repo", "instance_id": "fixture__repo-1", "image": "original", "dataset": "fixture"}]
        )
    )
    overrides.write_text(json.dumps({"fixture__repo-1": "prepared-image"}))
    command = [
        sys.executable,
        str(PROJECT / "scripts" / "run_plain.py"),
        "--dry-run",
        "--selection",
        str(selection),
        "--image-overrides",
        str(overrides),
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True, timeout=30)
    assert result.stdout.count("minisweagent.run.benchmarks.swebench_single") == 1
    assert "agent.repomap.map_tokens=0" in result.stdout
    assert "environment.image=prepared-image" in result.stdout
    assert "--network=none" in result.stdout
    assert "/plain/" in result.stdout
    assert not (tmp_path / "plan.json").exists()
    conflict = subprocess.run(
        [*command, "--config", 'environment.run_args=["--rm","--network=host"]'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert conflict.returncode != 0
    assert "requires Docker network=none" in conflict.stderr
    runtime = tmp_path / "standalone-python"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python3").touch()
    mounted = subprocess.run(
        [*command, "--collector-runtime", str(runtime)], capture_output=True, text=True, check=True, timeout=30
    )
    assert f"{runtime}:/opt/repomap-python:ro" in mounted.stdout
    assert "agent.repomap.python_command=/opt/repomap-python/bin/python3" in mounted.stdout


def test_static_script_compares_only_initial_1024_map_with_plain(tmp_path):
    selection = tmp_path / "selection.json"
    overrides = tmp_path / "images.json"
    selection.write_text(
        json.dumps(
            [{"repo": "fixture/repo", "instance_id": "fixture__repo-1", "image": "original", "dataset": "fixture"}]
        )
    )
    overrides.write_text(json.dumps({"fixture__repo-1": "prepared-image"}))
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "scripts/run_static.py"),
            "--dry-run",
            "--model",
            "deterministic",
            "--selection",
            str(selection),
            "--image-overrides",
            str(overrides),
            "--map-tokens",
            "4096",
            "--config",
            "agent.repomap.refresh=interval",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    commands = [shlex.split(line) for line in result.stdout.splitlines()]
    assert len(commands) == 2
    configs = []
    for command in commands:
        config = {}
        for index, argument in enumerate(command):
            if argument == "-c":
                config = recursive_merge(config, get_config_from_spec(command[index + 1]))
        assert config["agent"]["repomap"]["refresh"] == "once"
        assert config["agent"]["repomap"]["context_messages"] == 0
        assert config["agent"]["repomap"]["strict"] is True
        assert config["environment"]["image"] == "prepared-image"
        assert "--network=none" in config["environment"]["run_args"]
        configs.append(config)
    assert configs[0]["agent"]["repomap"].pop("map_tokens") == 0
    assert configs[1]["agent"]["repomap"].pop("map_tokens") == 1024
    assert configs[0] == configs[1]
    assert not (tmp_path / "plan.json").exists()


def test_full_script_compares_complete_initial_map_with_plain(tmp_path):
    selection = tmp_path / "selection.json"
    overrides = tmp_path / "images.json"
    selection.write_text(
        json.dumps(
            [{"repo": "fixture/repo", "instance_id": "fixture__repo-1", "image": "original", "dataset": "fixture"}]
        )
    )
    overrides.write_text(json.dumps({"fixture__repo-1": "prepared-image"}))
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "scripts/run_full.py"),
            "--dry-run",
            "--model",
            "deterministic",
            "--selection",
            str(selection),
            "--image-overrides",
            str(overrides),
            "--config",
            "agent.repomap.mode=budget",
            "--config",
            "agent.repomap.refresh=interval",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    commands = [shlex.split(line) for line in result.stdout.splitlines()]
    assert len(commands) == 2
    configs = []
    for command in commands:
        config = {}
        for index, argument in enumerate(command):
            if argument == "-c":
                config = recursive_merge(config, get_config_from_spec(command[index + 1]))
        settings = config["agent"]["repomap"]
        assert settings["mode"] == "full" and settings["refresh"] == "once"
        assert settings["context_messages"] == 0 and settings["strict"] is True
        assert all(settings[limit] is None for limit in ["max_files", "max_file_bytes", "max_total_bytes"])
        assert config["environment"]["image"] == "prepared-image"
        assert "--network=none" in config["environment"]["run_args"]
        configs.append(config)
    assert configs[0]["agent"]["repomap"].pop("map_tokens") == 0
    assert configs[1]["agent"]["repomap"].pop("map_tokens") > 0
    assert configs[0] == configs[1]
    assert not (tmp_path / "plan.json").exists()


def test_budget_sweep_requires_coverage_and_balances_offline_conditions(tmp_path):
    output = tmp_path / "sweep"
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python3").touch()
    command = [
        sys.executable,
        str(PROJECT / "scripts" / "run_budget_sweep.py"),
        "--output",
        str(output),
        "--collector-runtime",
        str(runtime),
        "--model",
        "deterministic",
        "--dry-run",
    ]
    refused = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    assert "prepare_budget_sweep.py" in refused.stderr
    output.mkdir()
    (output / "scan-coverage.json").write_text(json.dumps({"verified": True}))
    (output / "scan-config.yaml").write_text("agent:\n  repomap:\n    refresh_interval: 1\n")
    selected = json.loads((PROJECT / "benchmarks/docker_repositories/selection.json").read_text())
    selected = [item | {"image": "fixture-pinned-image"} for item in selected]
    (output / "selection-pinned.json").write_text(json.dumps(selected))
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    plan = json.loads(result.stdout)
    assert plan["tasks"] == 80
    assert not (output / "plan.json").exists()
    from collections import Counter, defaultdict

    positions = defaultdict(Counter)
    for index, row in enumerate(plan["order"]):
        positions[row["budget"]][index % 4] += 1
    assert set(positions) == {0, 1024, 2048, 4096}
    assert all(dict(counts) == {0: 5, 1: 5, 2: 5, 3: 5} for counts in positions.values())
    for entry in plan["entries"]:
        args = entry["command"]
        assert f"agent.repomap.map_tokens={entry['budget']}" in args
        assert "environment.image=fixture-pinned-image" in args
        assert "--network=none" in " ".join(args)
        assert "--volume" in " ".join(args)
        assert entry["variant"] == ("plain" if entry["budget"] == 0 else "repomap")

    static = subprocess.run(
        [*command, "--budgets", "0", "1024", "--map-refresh", "once"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    static_plan = json.loads(static.stdout)
    assert static_plan["tasks"] == 40
    assert static_plan["budgets"] == [0, 1024]
    for entry in static_plan["entries"]:
        assert str(PROJECT / "src/mini_swe_repomap/config/once.yaml") in entry["command"]
    static_positions = defaultdict(Counter)
    for index, row in enumerate(static_plan["order"]):
        static_positions[row["budget"]][index % 2] += 1
    assert all(dict(counts) == {0: 10, 1: 10} for counts in static_positions.values())


@pytest.mark.parametrize("save_trajectory", [False, True])
def test_process_timeout_preserves_last_model_cost(monkeypatch, tmp_path, save_trajectory):
    import time

    monkeypatch.syspath_prepend(str(PROJECT / "scripts"))
    from run_swebench import run_entry

    directory = tmp_path / "trial"
    trajectory = directory / "fixture__repo-1.traj.json"
    payload = {"info": {"model_stats": {"api_calls": 1, "instance_cost": 0.123}}, "messages": []}
    code = "import json,time; from pathlib import Path; "
    if save_trajectory:
        code += f"Path({str(trajectory)!r}).write_text({json.dumps(json.dumps(payload))}); "
    code += "time.sleep(30)"
    entry = {
        "variant": "plain",
        "item": {"repo": "fixture/repo", "instance_id": "fixture__repo-1"},
        "output": str(directory),
        "command": [sys.executable, "-c", code],
        "process_timeout_seconds": 1,
    }
    started = time.monotonic()
    record = run_entry(entry)
    assert time.monotonic() - started < 5
    assert record["exit_status"] == "ProcessTimeout"
    assert record["valid"] is False
    if save_trajectory:
        assert record["model_stats"] == payload["info"]["model_stats"]
        assert json.loads(trajectory.read_text()) == payload


def test_swebench_script_loads_custom_agent_and_writes_both_predictions(repo, tmp_path):
    try:
        image = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Docker with python:3.12-slim is unavailable")
    if image.returncode:
        pytest.skip("Docker with python:3.12-slim is unavailable")
    dataset = tmp_path / "local_dataset"
    dataset.mkdir()
    instance_id = "fixture__repo-1"
    (dataset / "test.jsonl").write_text(
        json.dumps(
            {
                "repo": "fixture/repo",
                "instance_id": instance_id,
                "base_commit": "fixture",
                "problem_statement": "Fix Service",
                "image_name": "unavailable-image-that-must-be-overridden:latest",
            }
        )
        + "\n"
    )
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            [
                {
                    "repo": "fixture/repo",
                    "instance_id": instance_id,
                    "image": "python:3.12-slim",
                    "dataset": str(dataset),
                }
            ]
        )
    )
    output = tmp_path / "experiment"
    overrides = tmp_path / "images.json"
    overrides.write_text(json.dumps({instance_id: "python:3.12-slim"}))
    outputs = [
        {
            "role": "assistant",
            "content": "done",
            "extra": {
                "actions": [
                    {"command": 'test "$(ls /sys/class/net)" = lo && echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'}
                ],
            },
        }
    ]
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "scripts" / "run_swebench.py"),
            "--selection",
            str(selection),
            "--model",
            "deterministic",
            "--variants",
            "baseline",
            "plain",
            "repomap",
            "--offline",
            "--image-overrides",
            str(overrides),
            "--output",
            str(output),
            "--config",
            str(PROJECT / "src" / "mini_swe_repomap" / "config" / "offline.yaml"),
            "--config",
            "model.outputs=" + json.dumps(outputs),
            "--config",
            "environment.run_args="
            + json.dumps(
                [
                    "--rm",
                    "--mount",
                    f"type=bind,src={repo},dst=/testbed,readonly",
                ]
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ | {"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert report["errors"] == 0
    records = {row["variant"]: row for row in report["runs"]}
    assert records["baseline"]["map_present"] is False
    assert records["plain"]["map_present"] is False
    assert records["repomap"]["map_present"] is True
    for variant, record in records.items():
        assert record["valid"] is True
        assert record["agent_type"] == "mini_swe_repomap.agent.RepoMapAgent"
        assert record["model_stats"]["api_calls"] == 1
        predictions = json.loads((output / variant / dataset.name / "preds.json").read_text())
        assert predictions[instance_id]["model_name"] == "deterministic"
