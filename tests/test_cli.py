import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from minisweagent.config import get_config_from_spec
from minisweagent.utils.serialize import recursive_merge
from typer.testing import CliRunner

from mini_swe_repomap.cli import app


def test_installed_config_paths_merge_with_mini_and_benchmark():
    runner = CliRunner()
    for options, base, expected in [
        (["config"], "mini.yaml", "InteractiveRepoMapAgent"),
        (["config", "--batch"], "swebench.yaml", "RepoMapAgent"),
    ]:
        result = runner.invoke(app, options)
        assert result.exit_code == 0
        path = Path(result.stdout.strip())
        assert path.is_absolute() and path.is_file()
        merged = recursive_merge(get_config_from_spec(base), get_config_from_spec(path))
        assert merged["agent"]["agent_class"] == f"mini_swe_repomap.agent.{expected}"
        assert merged["agent"]["repomap"]["map_tokens"] == 1024
        assert "system_template" in merged["agent"] and "instance_template" in merged["agent"]


def test_cli_preview_and_zero_budget(repo):
    runner = CliRunner()
    result = runner.invoke(app, ["show", str(repo), "--task", "Service", "--tokens", "256"])
    assert result.exit_code == 0, result.output
    assert "class Service" in result.stdout
    assert "<repository_map>" in result.stdout
    assert runner.invoke(app, ["show", "/nonexistent", "--tokens", "0"]).stdout == ""


def test_full_map_preview_and_overlay(repo):
    runner = CliRunner()
    (repo / "large.py").write_text("#" * 256_001 + "\ndef previously_skipped(): pass\n")
    result = runner.invoke(app, ["show", str(repo), "--full", "--tokens", "1"])
    assert result.exit_code == 0, result.output
    assert "class Service" in result.stdout and "def previously_skipped" in result.stdout
    assert "README.md:" in result.stdout
    assert runner.invoke(app, ["show", "/nonexistent", "--full", "--tokens", "0"]).stdout == ""
    path = runner.invoke(app, ["config", "--full-once"])
    assert path.exit_code == 0
    overlay = get_config_from_spec(Path(path.stdout.strip()))
    for agent_options, expected_class in [([], "InteractiveRepoMapAgent"), (["--batch"], "RepoMapAgent")]:
        base = runner.invoke(app, ["config", *agent_options])
        merged = recursive_merge(get_config_from_spec(Path(base.stdout.strip())), overlay)
        assert merged["agent"]["agent_class"].endswith(expected_class)
        settings = merged["agent"]["repomap"]
        assert settings["mode"] == "full" and settings["refresh"] == "once"
        assert settings["context_messages"] == 0 and settings["strict"] is True
        assert all(settings[limit] is None for limit in ["max_files", "max_file_bytes", "max_total_bytes"])
    assert runner.invoke(app, ["config", "--batch", "--full-once"]).exit_code != 0


def test_upstream_mini_cli_runs_with_installed_overlay(repo, tmp_path):
    output = tmp_path / "cli.traj.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "minisweagent.run.mini",
            "-c",
            "mini.yaml",
            "-c",
            str(files("mini_swe_repomap").joinpath("config", "interactive.yaml")),
            "-c",
            f"environment.cwd={repo}",
            "-c",
            'model.outputs=[{"role":"assistant","content":"done","extra":{"actions":[{"command":"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]}}]',
            "--model-class",
            "minisweagent.models.test_models.DeterministicModel",
            "-m",
            "deterministic",
            "-t",
            "Fix Service",
            "-y",
            "--exit-immediately",
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"MSWEA_CONFIGURED": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    trajectory = json.loads(output.read_text())
    assert trajectory["info"]["exit_status"] == "Submitted"
    assert trajectory["info"]["config"]["agent_type"] == "mini_swe_repomap.agent.InteractiveRepoMapAgent"
    assert any("<repository_map>" in message.get("content", "") for message in trajectory["messages"])
