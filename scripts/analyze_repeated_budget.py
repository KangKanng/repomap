"""Report repeated-budget search calls, localization evidence and resolved-rate constraints."""

import argparse
import csv
import hashlib
import io
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from localization_metrics import localization
from run_budget_sweep import write_json


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def mean(values):
    return statistics.mean(values) if values else None


def sd(values):
    return statistics.stdev(values) if len(values) > 1 else None


def fmt(value):
    return f"{value:.2f}" if value is not None else "N/A"


def summarize(root):
    protocol = load(root / "protocol.json")
    references = load(root / "localization-reference.json", {"instances": {}})
    cache = load(root / "analysis-cache.json", {})
    rows, billing_issues = [], []
    for job in protocol["queue"]:
        report = load(Path(job["directory"]) / "budget-report.json", {})
        billing_issues += [
            {"phase": job["phase"], "repeat": job["repeat"], **issue} for issue in report.get("billing_issues", [])
        ]
        for trial in report.get("trials", []):
            row = {"phase": job["phase"], "repeat": job["repeat"], **trial}
            if row.get("inference_completed") and row.get("trajectory"):
                path = Path(row["trajectory"])
                raw = path.read_bytes() if path.exists() else b""
                digest = hashlib.sha256(raw).hexdigest()
                if raw and cache.get(str(path), {}).get("sha256") != digest:
                    data = json.loads(raw)
                    messages = data.get("messages", [])
                    maps, missing, models = [], 0, Counter()
                    for message in messages:
                        extra = message.get("extra", {})
                        if "response" not in extra:
                            continue
                        snapshots = extra.get("request_repository_maps")
                        missing += snapshots is None
                        maps.extend(snapshots or [])
                        response = extra.get("response")
                        if isinstance(response, dict):
                            models[response.get("model", "unknown")] += 1
                    cache[str(path)] = {
                        "sha256": digest,
                        "localization": localization(messages, references["instances"].get(row["instance_id"], {})),
                        "recorded_request_maps": len(maps),
                        "requests_missing_map_metadata": missing,
                        "unique_map_hashes": sorted({m["sha256"] for m in maps}),
                        "map_hashes_valid": all(
                            hashlib.sha256(m["content"].encode()).hexdigest() == m["sha256"] for m in maps
                        ),
                        "response_models": dict(models),
                    }
                row.update(cache.get(str(path), {}))
                row["unreturned_request_charge_unknown"] = row.get("exit_status") == "ProcessTimeout"
            rows.append(row)
    write_json(root / "analysis-cache.json", cache)
    totals, task_rows, comparisons = [], [], []
    planned = protocol["instances"] * protocol["repeats"]
    for phase in protocol["phases"]:
        for budget in protocol["budgets"]:
            subset = [r for r in rows if r["phase"] == phase and r["budget"] == budget]
            valid = [r for r in subset if r.get("run_valid") and "search_commands" in r]
            observed = [
                r["localization"] for r in valid if r.get("localization", {}).get("status") == "observed_explicit_read"
            ]
            graded = [r for r in subset if r.get("graded")]
            repeat_means = []
            for repeat in range(1, protocol["repeats"] + 1):
                sample = [r for r in valid if r["repeat"] == repeat]
                if len(sample) == protocol["instances"]:
                    repeat_means.append({"repeat": repeat, "mean": mean([r["search_commands"] for r in sample])})
            total = {
                "phase": phase,
                "budget": budget,
                "planned": planned,
                "completed": sum(r["inference_completed"] for r in subset),
                "valid_inferences": len(valid),
                "graded": len(graded),
                "resolved": sum(r.get("resolved") is True for r in graded),
                "resolved_rate": sum(r.get("resolved") is True for r in graded) / len(graded) if graded else None,
                "inference_errors": sum(r.get("inference_completed") and not r.get("run_valid") for r in subset),
                "search_calls_per_task": mean([r["search_commands"] for r in valid]),
                "median_search_calls_per_task": statistics.median([r["search_commands"] for r in valid])
                if valid
                else None,
                "repeat_means": repeat_means,
                "sd_of_complete_repeat_means": sd([r["mean"] for r in repeat_means]),
                "localization_observed": len(observed),
                "localization_searches_through_action_mean": mean(
                    [m["search_commands_through_action"] for m in observed]
                ),
                "localization_searches_before_action_mean": mean(
                    [m["search_commands_before_action"] for m in observed]
                ),
                "localization_assistant_calls_mean": mean([m["assistant_calls"] for m in observed]),
                "server_returned_cost_usd": str(
                    sum((Decimal(r.get("server_cost_usd", "0")) for r in subset), Decimal(0))
                ),
                "unreturned_request_charge_unknown": sum(
                    r.get("unreturned_request_charge_unknown", False) for r in subset
                ),
                "provisional": len(valid) != planned or len(graded) != planned,
            }
            totals.append(total)
            by_task = defaultdict(list)
            for row in subset:
                by_task[row["instance_id"]].append(row)
            for iid, sample in sorted(by_task.items()):
                measurements = [r["search_commands"] for r in sample if r.get("run_valid") and "search_commands" in r]
                task_rows.append(
                    {
                        "phase": phase,
                        "budget": budget,
                        "instance_id": iid,
                        "valid_repeats": len(measurements),
                        "search_mean": mean(measurements),
                        "search_sd": sd(measurements),
                        "searches": measurements,
                        "graded_repeats": sum(r.get("graded", False) for r in sample),
                        "resolved_repeats": sum(r.get("resolved") is True for r in sample),
                    }
                )
            if budget:
                baseline = {
                    (r["instance_id"], r["repeat"]): r
                    for r in rows
                    if r["phase"] == phase and r["budget"] == 0 and r.get("run_valid")
                }
                pairs = [
                    (r, baseline[(r["instance_id"], r["repeat"])])
                    for r in valid
                    if (r["instance_id"], r["repeat"]) in baseline
                ]
                localized_pairs = [
                    (a, b)
                    for a, b in pairs
                    if a.get("localization", {}).get("status")
                    == b.get("localization", {}).get("status")
                    == "observed_explicit_read"
                ]
                comparisons.append(
                    {
                        "phase": phase,
                        "budget": budget,
                        "paired_trials": len(pairs),
                        "planned_pairs": planned,
                        "paired_search_delta_mean": mean(
                            [a["search_commands"] - b["search_commands"] for a, b in pairs]
                        ),
                        "paired_localization_observed": len(localized_pairs),
                        "paired_localization_search_delta_mean": mean(
                            [
                                a["localization"]["search_commands_through_action"]
                                - b["localization"]["search_commands_through_action"]
                                for a, b in localized_pairs
                            ]
                        ),
                        "success_preservation_established": None,
                        "reason": "Report resolved-rate differences and repeat variation; no prespecified noninferiority margin or adequate-power claim.",
                    }
                )
    indexed_totals = {(t["phase"], t["budget"]): t for t in totals}
    for comparison in comparisons:
        current = indexed_totals[(comparison["phase"], comparison["budget"])]
        baseline = indexed_totals.get((comparison["phase"], 0))
        complete = baseline and current["graded"] == baseline["graded"] == planned
        comparison["observed_resolved_constraint_met"] = (
            current["resolved"] >= baseline["resolved"] if complete else None
        )
        comparison["resolved_rate_difference_percentage_points"] = (
            100 * (current["resolved_rate"] - baseline["resolved_rate"]) if complete else None
        )
    policy_comparisons = []
    lookup = {(r["phase"], r["budget"], r["instance_id"], r["repeat"]): r for r in rows if r.get("run_valid")}
    if {"once", "interval"} <= set(protocol["phases"]):
        for budget in protocol["budgets"]:
            pairs, adjusted = [], []
            for key, current in lookup.items():
                phase, candidate_budget, iid, repeat = key
                other = lookup.get(("once", budget, iid, repeat))
                if phase != "interval" or candidate_budget != budget or other is None:
                    continue
                pairs.append((current, other))
                plain_interval = lookup.get(("interval", 0, iid, repeat))
                plain_once = lookup.get(("once", 0, iid, repeat))
                if budget and plain_interval and plain_once:
                    adjusted.append(
                        (current["search_commands"] - plain_interval["search_commands"])
                        - (other["search_commands"] - plain_once["search_commands"])
                    )
            policy_comparisons.append(
                {
                    "budget": budget,
                    "paired_trials": len(pairs),
                    "planned_pairs": planned,
                    "interval_minus_once_search_mean": mean(
                        [a["search_commands"] - b["search_commands"] for a, b in pairs]
                    ),
                    "baseline_adjusted_pairs": len(adjusted),
                    "baseline_adjusted_search_difference_mean": mean(adjusted),
                }
            )
    report = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "planned": protocol["planned_trials"],
        "inference_completed": sum(r["inference_completed"] for r in rows),
        "graded": sum(r.get("graded", False) for r in rows),
        "totals": totals,
        "paired_comparisons": comparisons,
        "policy_comparisons": policy_comparisons,
        "per_task": task_rows,
        "billing_issues": billing_issues,
        "trials": rows,
        "limitations": [
            "Search Calls counts explicit Bash search subcommands, not model calls or necessarily executed branches.",
            "Localization is an explicit read with successful model-visible output matching unique baseline code near official hunks.",
            "Unknown script-based reads, alternate correct fix paths, and undetected reads remain not_observed, never zero.",
            "Localization costs include the full locating action; compare observed coverage and paired coverage alongside means.",
            "Intervals use recent-six context; once uses raw task only. Their contrast is a combined policy contrast.",
            "Sequential phases can be confounded by provider changes; each phase includes newly repeated plain controls.",
            "Three repeats quantify observed variation but do not prove success-rate noninferiority.",
            "Partial averages cover completed valid inferences and are provisional; invalid/unfinished runs are not zero-search wins.",
        ],
    }
    write_json(root / "repeat-report.json", report)
    columns = [
        "phase",
        "repeat",
        "budget",
        "instance_id",
        "repo",
        "inference_completed",
        "run_valid",
        "graded",
        "resolved",
        "search_commands",
        "search_actions",
        "server_cost_usd",
        "exit_status",
        "trajectory",
    ]
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream, fieldnames=columns + ["localization_status", "localization_searches", "localization_calls"]
    )
    writer.writeheader()
    for row in rows:
        metric = row.get("localization", {})
        writer.writerow(
            {
                **{key: row.get(key) for key in columns},
                "localization_status": metric.get("status"),
                "localization_searches": metric.get("search_commands_through_action"),
                "localization_calls": metric.get("assistant_calls"),
            }
        )
    (root / "repeat-trials.csv").write_text(stream.getvalue())
    lines = [
        "# Once / Interval：预算与重复实验报告",
        "",
        f"更新：{report['updated_at']}；推理 {report['inference_completed']}/{report['planned']}，判题 {report['graded']}/{report['planned']}。",
        "所有均值在完成前均为阶段性结果，不据此提前宣布收益。",
        "",
        "主指标：Search Calls per Task；关键辅助：Target Localization Cost；必须同时检查：Resolved Rate。",
        "",
        "| 策略 | 预算 | 有效推理 | 搜索数/任务 | 完整重复均值的标准差 | 定位前搜索（含定位动作） | 定位模型轮数 | 定位可观察覆盖 | Resolved Rate（通过/已判） | 已返回费用USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for t in totals:
        rate = f"{t['resolved_rate']:.1%}" if t["resolved_rate"] is not None else "N/A"
        lines.append(
            f"| {t['phase']} | {t['budget']} | {t['valid_inferences']}/{planned} | {fmt(t['search_calls_per_task'])} "
            f"| {fmt(t['sd_of_complete_repeat_means'])} | {fmt(t['localization_searches_through_action_mean'])} "
            f"| {fmt(t['localization_assistant_calls_mean'])} | {t['localization_observed']}/{t['valid_inferences']} "
            f"| {t['resolved']}/{t['graded']} ({rate}) | {t['server_returned_cost_usd']} |"
        )
    lines += [
        "",
        "## Interval与Once直接对照",
        "",
        "差值为interval减once；按任务及重复编号配对。基线调整值为两阶段各自相对plain的增量之差，不能消除全部时间或模型漂移影响。",
        "",
        "| 预算 | 已配对/计划 | 每任务搜索差值 | 基线调整配对数 | 基线调整搜索差值 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for c in policy_comparisons:
        lines.append(
            f"| {c['budget']} | {c['paired_trials']}/{planned} | {fmt(c['interval_minus_once_search_mean'])} "
            f"| {c['baseline_adjusted_pairs']} | {fmt(c['baseline_adjusted_search_difference_mean'])} |"
        )
    lines += [
        "",
        "## 相对同阶段plain的配对比较",
        "",
        "| 策略 | 预算 | 已配对/计划 | 每任务搜索差值 | 双方定位可观察 | 定位搜索差值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in comparisons:
        lines.append(
            f"| {c['phase']} | {c['budget']} | {c['paired_trials']}/{planned} | {fmt(c['paired_search_delta_mean'])} "
            f"| {c['paired_localization_observed']}/{c['paired_trials']} | {fmt(c['paired_localization_search_delta_mean'])} |"
        )
    lines += [
        "",
        "## 逐任务重复波动",
        "",
        "| 策略 | 预算 | 任务 | 有效重复 | 搜索均值 | 重复间标准差 | 通过/已判 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for t in task_rows:
        lines.append(
            f"| {t['phase']} | {t['budget']} | {t['instance_id']} | {t['valid_repeats']} "
            f"| {fmt(t['search_mean'])} | {fmt(t['search_sd'])} | {t['resolved_repeats']}/{t['graded_repeats']} |"
        )
    lines += [
        "",
        "## 解释边界",
        "",
        *["- " + note for note in report["limitations"]],
        "",
        "[完整JSON](repeat-report.json) · [逐次CSV](repeat-trials.csv) · [固定实验协议](protocol.json)",
        "",
    ]
    (root / "report.md").write_text("\n".join(lines))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    report = summarize(args.run.resolve())
    print(json.dumps({key: report[key] for key in ("planned", "inference_completed", "graded")}, indent=2))


if __name__ == "__main__":
    main()
