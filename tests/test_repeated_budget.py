import json
from pathlib import Path


def test_repeated_report_preserves_missing_metrics_and_exposes_resolved_constraint(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from analyze_repeated_budget import summarize

    queue = []
    for phase in ("once", "interval"):
        for repeat in (1, 2):
            directory = tmp_path / phase / str(repeat)
            directory.mkdir(parents=True)
            queue.append({"phase": phase, "repeat": repeat, "directory": str(directory)})
            if phase == "interval":
                continue
            rows = []
            for budget in (0, 1024):
                trajectory = directory / f"{budget}.json"
                trajectory.write_text(json.dumps({"messages": []}))
                rows.append(
                    {
                        "instance_id": "fixture__repo-1",
                        "repo": "fixture/repo",
                        "budget": budget,
                        "inference_completed": True,
                        "run_valid": True,
                        "graded": True,
                        "resolved": budget == 0 or repeat == 2,
                        "search_commands": 2 * repeat + (1 if budget == 0 else -1),
                        "search_actions": repeat,
                        "server_cost_usd": "0.01",
                        "trajectory": str(trajectory),
                    }
                )
            (directory / "budget-report.json").write_text(json.dumps({"trials": rows, "billing_issues": []}))
    (tmp_path / "protocol.json").write_text(
        json.dumps(
            {
                "instances": 1,
                "repeats": 2,
                "phases": ["once", "interval"],
                "budgets": [0, 1024],
                "planned_trials": 8,
                "queue": queue,
            }
        )
    )
    report = summarize(tmp_path)
    assert report["inference_completed"] == 4 and report["planned"] == 8
    totals = {(r["phase"], r["budget"]): r for r in report["totals"]}
    assert totals[("once", 0)]["search_calls_per_task"] == 4
    assert totals[("once", 1024)]["search_calls_per_task"] == 2
    assert totals[("once", 1024)]["localization_searches_through_action_mean"] is None
    assert totals[("interval", 0)]["search_calls_per_task"] is None
    assert totals[("interval", 0)]["resolved_rate"] is None
    comparison = next(r for r in report["paired_comparisons"] if r["phase"] == "once")
    assert comparison["paired_search_delta_mean"] == -2
    assert comparison["observed_resolved_constraint_met"] is False
    assert comparison["success_preservation_established"] is None
    assert (tmp_path / "report.md").is_file()
