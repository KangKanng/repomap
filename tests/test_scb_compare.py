import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_scb_compare import generated_code_quality, paired  # noqa: E402
from guard_scb_checkpoints import worker_pid  # noqa: E402
from run_scb_compare import execute_chain, select_problems, trial_metrics  # noqa: E402
from scb_infer import render_task  # noqa: E402


def test_selection_balances_difficulty_and_covers_categories(tmp_path):
    for level in ["Easy", "Medium", "Hard"]:
        for i in range(12):
            directory = tmp_path / f"{level}_{i}"
            directory.mkdir()
            config = {
                "name": directory.name,
                "category": f"category_{i % 4}",
                "difficulty": level,
                "entry_file": "main",
                "checkpoints": {"checkpoint_2": {"order": 2}, "checkpoint_1": {"order": 1}},
            }
            (directory / "config.yaml").write_text(yaml.safe_dump(config))
    selection, _ = select_problems(tmp_path)
    assert len(selection) == len({x["name"] for x in selection}) == 25
    assert sorted(sum(x["difficulty"] == level for x in selection) for level in ["Easy", "Medium", "Hard"]) == [8, 8, 9]
    assert {x["category"] for x in selection} == {f"category_{i}" for i in range(4)}
    assert all(x["checkpoints"] == ["checkpoint_1", "checkpoint_2"] for x in selection)
    assert select_problems(tmp_path)[0] == selection


def test_prompt_contains_only_current_checkpoint(tmp_path):
    (tmp_path / "checkpoint_1.md").write_text("Old specification")
    (tmp_path / "checkpoint_2.md").write_text("Extend %%%ENTRYPOINT:entry_command%%%")
    (tmp_path / "checkpoint_3.md").write_text("Future secret specification")
    prompt = tmp_path / "configs/prompts"
    prompt.mkdir(parents=True)
    (prompt / "just-solve.jinja").write_text("{{ is_continuation }}: {{ spec }}")
    task = render_task(
        tmp_path, {"entry_file": "main", "checkpoints": ["checkpoint_1", "checkpoint_2"]}, "checkpoint_2", tmp_path
    )
    assert task == "True: Extend python main.py"


def test_official_grouped_counts_and_infrastructure_failure(tmp_path):
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(json.dumps({"messages": []}))
    record = tmp_path / "inference.json"
    record.write_text(json.dumps({"trajectory": str(trajectory)}))
    directory = tmp_path / "evaluation"
    directory.mkdir()
    report = {
        "infrastructure_failure": False,
        "tests": {"checkpoint_2": {"Core": [{"status": "passed"}]}},
        "pass_counts": {"Core": 1, "Regression": 1},
        "total_counts": {"Core": 1, "Regression": 2},
    }
    path = directory / "evaluation.json"
    path.write_text(json.dumps(report))
    metrics = trial_metrics(record)
    assert metrics["passed_policy"] is True
    assert metrics["all_non_error_passed"] is False
    assert (metrics["tests_passed"], metrics["tests_total"]) == (2, 3)
    report["infrastructure_failure"] = True
    path.write_text(json.dumps(report))
    assert trial_metrics(record)["passed_policy"] is False


def test_failed_zero_search_trials_and_initial_checkpoint_do_not_create_benefit():
    trials = []
    for index in range(1, 5):
        for variant in ["plain", "once-full"]:
            trials.append(
                {
                    "problem": "example",
                    "checkpoint": f"checkpoint_{index}",
                    "checkpoint_index": index,
                    "variant": variant,
                    "exit_status": "Submitted",
                    "evaluated": True,
                    "infrastructure_failure": False,
                    "search_commands": 10 if variant == "plain" else 4,
                    "model_calls": 8,
                    "cost": 0.1,
                    "all_non_error_passed": index != 4,
                }
            )
    trials[3].update(exit_status="TimeExceeded", search_commands=0)
    trials[5]["infrastructure_failure"] = True
    assert [p["checkpoint"] for p in paired(trials)] == ["checkpoint_4"]
    assert paired(trials, require_success=True) == []


def test_controller_restart_does_not_rerun_finished_or_failed_chain(tmp_path, monkeypatch):
    def forbid_model_restart(*args, **kwargs):
        raise AssertionError("A finished or failed chain must not be retried")

    monkeypatch.setattr("run_scb_compare.execute", forbid_model_restart)
    root = tmp_path / "plain" / "example"
    root.mkdir(parents=True)
    item = {"name": "example", "checkpoints": ["checkpoint_1", "checkpoint_2"]}
    for record in [{"complete": True}, {"complete": False, "error": "RuntimeError"}]:
        (root / "chain-finished.json").write_text(json.dumps(record))
        assert execute_chain(tmp_path, {}, item, "plain", ["must-not-run"]) == 0


def test_guard_matches_only_the_selected_run_problem_and_variant(tmp_path, monkeypatch):
    listing = (
        "PID COMMAND\n"
        f"101 python scripts/scb_infer.py --output {tmp_path} --problem example --variant plain\n"
        f"102 python scripts/scb_infer.py --output {tmp_path} --problem example --variant once-full\n"
        "103 python scripts/scb_infer.py --output /another/run --problem example --variant plain\n"
    )
    monkeypatch.setattr("guard_scb_checkpoints.subprocess.check_output", lambda *a, **k: listing)
    assert worker_pid(tmp_path, "example", "plain") == 101
    assert worker_pid(tmp_path, "example", "once-full") == 102
    assert worker_pid(tmp_path, "another_problem", "plain") is None


def test_code_quality_excludes_provided_python_assets(tmp_path):
    directory = tmp_path / "quality_analysis"
    directory.mkdir()
    (directory / "files.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"file_path": "main.py", "loc": 100, "lint_errors": 5},
                {"file_path": "files/fixture.py", "loc": 10000, "lint_errors": 1000},
            ]
        )
    )
    (directory / "symbols.jsonl").write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"file_path": "main.py", "type": "function", "complexity": 3, "max_nesting_depth": 2},
                {"file_path": "files/fixture.py", "type": "function", "complexity": 200},
            ]
        )
    )
    metrics = generated_code_quality(tmp_path, ["files"])
    assert metrics["loc"] == 100
    assert metrics["lint_per_100_loc"] == 5
    assert metrics["function_cc_mean"] == metrics["function_cc_max"] == 3
