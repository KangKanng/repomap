import json
from pathlib import Path


def make_trial(root, budget):
    condition = f"budget-{budget}"
    variant = "plain" if budget == 0 else "repomap"
    directory = root / condition
    directory.mkdir()
    iid = "pallets__flask-5014"
    trajectory = directory / "trial.json"
    trajectory.write_text(
        json.dumps(
            {
                "info": {"model_stats": {"api_calls": 1}},
                "messages": [
                    {
                        "role": "assistant",
                        "extra": {
                            "actions": [{"command": "grep needle source.py; find src -name '*.py'"}],
                            "cost": 0.010000000001,
                            "cost_source": "server:usage.cost",
                            "response": {
                                "usage": {
                                    "cost": 0.010000000001,
                                    "prompt_tokens": 100,
                                    "completion_tokens": 10,
                                    "total_tokens": 110,
                                    "prompt_tokens_details": {"cached_tokens": 20},
                                }
                            },
                        },
                    }
                ],
            }
        )
    )
    entry = {
        "condition": condition,
        "budget": budget,
        "item": {"repo": "pallets/flask", "instance_id": iid, "image": "fixture-image"},
    }
    record = {
        "condition": condition,
        "instance_id": iid,
        "valid": True,
        "exit_status": "Submitted",
        "trajectory": str(trajectory),
        "elapsed_seconds": 2.0,
    }
    settings = {"directory": str(directory), "variant": variant}
    report = directory / "evaluation" / variant / "logs" / "report.json"
    return entry, record, settings, report


def test_budget_recommendation_waits_for_all_grades_and_keeps_plain(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from analyze_budget_sweep import summarize

    entries, records, conditions, reports = [], [], {}, []
    for budget in (0, 1024):
        entry, record, settings, report = make_trial(tmp_path, budget)
        entries.append(entry)
        records.append(record)
        conditions[entry["condition"]] = settings
        reports.append(report)
    (tmp_path / "plan.json").write_text(json.dumps(entries))
    (tmp_path / "summary.json").write_text(json.dumps({"runs": records}))
    (tmp_path / "experiment.json").write_text(
        json.dumps({"model": "fixture", "budgets": [0, 1024], "conditions": conditions})
    )
    reports[0].parent.mkdir(parents=True)
    reports[0].write_text(json.dumps({"pallets__flask-5014": {"resolved": True}}))
    partial = summarize(tmp_path)
    assert partial["graded"] == 1
    assert partial["size_groups"][0]["candidate_budget"] is None
    assert partial["minimum_successful_budgets"][0]["minimum_tested_successful_budget"] is None
    reports[1].parent.mkdir(parents=True)
    reports[1].write_text(json.dumps({"pallets__flask-5014": {"resolved": False}}))
    complete = summarize(tmp_path)
    assert complete["graded"] == 2
    assert complete["size_groups"][0]["candidate_budget"] == 0
    assert complete["minimum_successful_budgets"][0]["minimum_tested_successful_budget"] == 0
    assert complete["minimum_successful_budgets"][0]["non_monotonic"] is True
    for total in complete["totals"].values():
        assert total["server_cost_usd"] == "0.010000000001"
        assert total["search_commands"] == 2
        assert total["search_actions"] == 1
    assert complete["billing_issues"] == []

    retry_root = tmp_path / "retry"
    retry_root.mkdir()
    _, retry_record, retry_settings, retry_report = make_trial(retry_root, 0)
    retry_data = json.loads(Path(retry_record["trajectory"]).read_text())
    retry_data["messages"][0]["extra"]["cost"] = 0.02
    retry_data["messages"][0]["extra"]["response"]["usage"]["cost"] = 0.02
    Path(retry_record["trajectory"]).write_text(json.dumps(retry_data))
    retry_report.parent.mkdir(parents=True)
    retry_report.write_text(json.dumps({"pallets__flask-5014": {"resolved": True}}))
    records[0]["valid"] = False
    (tmp_path / "summary.json").write_text(json.dumps({"runs": records}))
    reports[0].write_text(json.dumps({"pallets__flask-5014": {"resolved": False}}))
    timeout = tmp_path / "timeout.json"
    timeout.write_text(json.dumps({"pending_request_charge_unknown": True}))
    (tmp_path / "trial-overrides.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "condition": "budget-0",
                        "instance_id": "pallets__flask-5014",
                        "record": retry_record,
                        "evaluation_root": str(Path(retry_settings["directory"]) / "evaluation" / "plain"),
                        "previous_trajectory": records[0]["trajectory"],
                        "timeout_event": str(timeout),
                    }
                ]
            }
        )
    )
    recovered = summarize(tmp_path)
    assert recovered["original_attempt_run_errors"] == 1
    assert recovered["totals"]["0"]["resolved"] == 1
    assert recovered["totals"]["0"]["inference_errors"] == 0
    assert recovered["totals"]["0"]["server_cost_usd"] == "0.030000000001"
    assert recovered["totals"]["0"]["unreturned_request_charges_unknown"] == 1
    assert recovered["billing_issues"] == []
