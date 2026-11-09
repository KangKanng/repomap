"""Summarize budget trials by repository size, using official grading and server costs."""

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pull_docker import BASE
from run_budget_sweep import write_json
from search_metrics import SearchCounter


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def summarize(root: Path):
    experiment = read_json(root / "experiment.json")
    if experiment is None:
        return None
    plan = read_json(root / "plan.json", [])
    records = read_json(root / "summary.json", {"runs": []})["runs"]
    by_key = {(r["condition"], r["instance_id"]): r for r in records}
    overrides = {
        (row["condition"], row["instance_id"]): row
        for row in read_json(root / "trial-overrides.json", {"entries": []})["entries"]
    }
    for key, override in overrides.items():
        by_key[key] = override["record"]
    timeout_targets = {
        (row["condition"], row["instance_id"]): row
        for row in read_json(root / "recovery-plan.json", {}).get("targets", [])
    }
    snapshot = root / "inventory-snapshot.json"
    if not snapshot.exists():
        write_json(snapshot, read_json(BASE / "inventory.json"))
    inventory = {r["instance_id"]: r for r in read_json(snapshot)["images"]}
    official = {}
    for condition, settings in experiment["conditions"].items():
        directory = Path(settings["directory"]) / "evaluation" / settings["variant"]
        for path in directory.rglob("report.json"):
            for iid, report in (read_json(path, {}) or {}).items():
                official[(condition, iid)] = report | {"report_path": str(path)}
        for path in directory.glob(f"{settings['variant']}-offline.*.json"):
            report = read_json(path, {})
            for field, status in [("empty_patch_ids", "empty_patch"), ("error_ids", "evaluation_error")]:
                for iid in report.get(field, []):
                    official.setdefault(
                        (condition, iid), {"resolved": False, "status": status, "report_path": str(path)}
                    )
    for key, override in overrides.items():
        official.pop(key, None)
        directory = Path(override["evaluation_root"])
        for path in directory.rglob("report.json"):
            data = read_json(path, {})
            if key[1] in data:
                official[key] = data[key[1]] | {"report_path": str(path)}
        for path in directory.glob("*-offline.*.json"):
            data = read_json(path, {})
            if key[1] in data.get("empty_patch_ids", []) or key[1] in data.get("error_ids", []):
                official.setdefault(
                    key, {"resolved": False, "report_path": str(path), "status": "retry_empty_or_error"}
                )
    parser = SearchCounter()
    trials, audit, billing_issues = [], [], []
    for entry in plan:
        iid, condition = entry["item"]["instance_id"], entry["condition"]
        record = by_key.get((condition, iid))
        report = official.get((condition, iid))
        row = {
            "condition": condition,
            "budget": entry["budget"],
            "instance_id": iid,
            "repo": entry["item"]["repo"],
            "source_lines": inventory[iid]["repository"]["source_lines"],
            "image": entry["item"]["image"],
            "inference_completed": record is not None,
            "graded": report is not None,
            "resolved": report.get("resolved") if report else None,
            "infra_failure": report.get("infra_failure", False) if report else False,
            "evaluation_report": report.get("report_path") if report else None,
            "run_valid": record.get("valid") if record else None,
            "exit_status": record.get("exit_status") if record else None,
        }
        if (condition, iid) in timeout_targets:
            event_path = timeout_targets[(condition, iid)]["timeout_event"]
            row["timeout_event"] = event_path
            row["unreturned_request_charge_unknown"] = read_json(Path(event_path), {}).get(
                "pending_request_charge_unknown", False
            )
        if record:
            row["elapsed_seconds"] = record.get("elapsed_seconds")
            row["trajectory"] = record["trajectory"]
            trajectory = read_json(Path(record["trajectory"]))
            if trajectory:
                messages = trajectory.get("messages", [])
                counts = Counter()
                cost = Decimal(0)
                sources = Counter()
                for message in messages:
                    extra = message.get("extra", {})
                    if "cost" not in extra:
                        continue
                    charge = Decimal(str(extra["cost"]))
                    cost += charge
                    counts["billed_responses"] += 1
                    source = extra.get("cost_source", "unknown")
                    sources[source] += 1
                    usage = extra.get("response", {}).get("usage", {})
                    if not source.startswith("server:") or (
                        usage.get("cost") is not None and charge != Decimal(str(usage["cost"]))
                    ):
                        billing_issues.append({"condition": condition, "instance_id": iid, "source": source})
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        counts[key] += usage.get(key, 0) or 0
                    counts["cached_prompt_tokens"] += (
                        usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0
                    )
                searches, segments = parser.messages(messages)
                audit.extend({"condition": condition, "instance_id": iid, **segment} for segment in segments)
                row.update(counts)
                row.update(searches)
                row["server_cost_usd"] = str(cost)
                row["cost_sources"] = dict(sources)
                row["api_calls"] = trajectory.get("info", {}).get("model_stats", {}).get("api_calls", 0)
                row["scan_cap_warning"] = False
                log = Path(record["trajectory"]).parent / "runner.log"
                if log.exists():
                    row["scan_cap_warning"] = any(
                        "Repo map:" in line
                        and any(k in line for k in ("max_files", "max_file_bytes", "max_total_bytes"))
                        for line in log.read_text(errors="replace").splitlines()
                    )
        override = overrides.get((condition, iid))
        if override:
            previous = read_json(Path(override["previous_trajectory"]), {})
            previous_cost = Decimal(0)
            for message in previous.get("messages", []):
                extra = message.get("extra", {})
                if "cost" not in extra:
                    continue
                charge = Decimal(str(extra["cost"]))
                previous_cost += charge
                usage = extra.get("response", {}).get("usage", {})
                if not extra.get("cost_source", "").startswith("server:") or (
                    usage.get("cost") is not None and charge != Decimal(str(usage["cost"]))
                ):
                    billing_issues.append({"condition": condition, "instance_id": iid, "previous_attempt": True})
            event = read_json(Path(override["timeout_event"]), {})
            row["selected_attempt_server_cost_usd"] = row.get("server_cost_usd", "0")
            row["previous_attempt_server_cost_usd"] = str(previous_cost)
            row["server_cost_usd"] = str(Decimal(row["selected_attempt_server_cost_usd"]) + previous_cost)
            row["previous_attempt_api_calls"] = previous.get("info", {}).get("model_stats", {}).get("api_calls", 0)
            row["infra_retry"] = True
            row["unreturned_request_charge_unknown"] = event.get("pending_request_charge_unknown", False)
            row["timeout_event"] = override["timeout_event"]
        row["all_attempts_api_calls"] = row.get("api_calls", 0) + row.get("previous_attempt_api_calls", 0)
        trials.append(row)

    def aggregate(rows):
        completed = [r for r in rows if r["inference_completed"]]
        result = {
            "planned": len(rows),
            "inference_completed": len(completed),
            "graded": sum(r["graded"] for r in rows),
            "resolved": sum(r["resolved"] is True for r in rows),
            "inference_errors": sum(r["run_valid"] is False for r in rows),
            "infra_failures": sum(r["infra_failure"] for r in rows),
            "server_cost_usd": str(sum((Decimal(r.get("server_cost_usd", "0")) for r in rows), Decimal(0))),
            "scan_cap_warning_trials": sum(r.get("scan_cap_warning", False) for r in rows),
            "infrastructure_retries": sum(r.get("infra_retry", False) for r in rows),
            "unreturned_request_charges_unknown": sum(r.get("unreturned_request_charge_unknown", False) for r in rows),
        }
        for key in (
            "api_calls",
            "all_attempts_api_calls",
            "billed_responses",
            "prompt_tokens",
            "completion_tokens",
            "cached_prompt_tokens",
            "search_commands",
            "search_actions",
            "text_search_commands",
            "file_locator_commands",
            "search_parse_error_actions",
        ):
            result[key] = sum(r.get(key, 0) for r in rows)
        result["elapsed_seconds_sum"] = sum(r.get("elapsed_seconds", 0) or 0 for r in rows)
        return result

    totals = {str(budget): aggregate([r for r in trials if r["budget"] == budget]) for budget in experiment["budgets"]}
    ordered_ids = sorted(
        {r["instance_id"] for r in trials}, key=lambda iid: (inventory[iid]["repository"]["source_lines"], iid)
    )
    # Equal-sized quartiles for the selected 20 repositories; smaller fixtures use up to four groups.
    size = (len(ordered_ids) + 3) // 4
    groups = []
    for index, start in enumerate(range(0, len(ordered_ids), size), 1):
        ids = ordered_ids[start : start + size]
        group = {
            "group": index,
            "instances": len(ids),
            "instance_ids": ids,
            "source_lines_min": inventory[ids[0]]["repository"]["source_lines"],
            "source_lines_max": inventory[ids[-1]]["repository"]["source_lines"],
            "budgets": {
                str(b): aggregate([r for r in trials if r["instance_id"] in ids and r["budget"] == b])
                for b in experiment["budgets"]
            },
        }
        ready = all(
            s["graded"] == len(ids)
            and not s["inference_errors"]
            and not s["infra_failures"]
            and not s["scan_cap_warning_trials"]
            for s in group["budgets"].values()
        )
        group["candidate_budget"] = None
        group["candidate_positive_map_budget"] = None
        group["recommendation_ready"] = ready
        if ready:
            best = max(s["resolved"] for s in group["budgets"].values())
            if best:
                group["candidate_budget"] = min(int(b) for b, s in group["budgets"].items() if s["resolved"] == best)
            group["best_resolved"] = best
            positive = {b: s for b, s in group["budgets"].items() if int(b) > 0}
            if positive:
                best_positive = max(s["resolved"] for s in positive.values())
                group["best_positive_resolved"] = best_positive
                if best_positive:
                    group["candidate_positive_map_budget"] = min(
                        int(b) for b, s in positive.items() if s["resolved"] == best_positive
                    )
        groups.append(group)
    minimums = []
    for iid in ordered_ids:
        rows = [r for r in trials if r["instance_id"] == iid]
        passed = sorted(r["budget"] for r in rows if r["resolved"] is True)
        positive_passed = [b for b in passed if b > 0]
        complete = all(r["graded"] for r in rows)
        failed = sorted(r["budget"] for r in rows if r["resolved"] is False)
        minimums.append(
            {
                "instance_id": iid,
                "repo": rows[0]["repo"],
                "source_lines": rows[0]["source_lines"],
                "complete": complete,
                "passed_budgets": passed,
                "minimum_tested_successful_budget": min(passed) if passed and complete else None,
                "minimum_tested_successful_positive_budget": min(positive_passed)
                if positive_passed and complete
                else None,
                "non_monotonic": complete and any(a < b for a in passed for b in failed),
            }
        )
    recovery_state = read_json(root / "recovery-plan.json", {}).get("state")
    report = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "model": experiment["model"],
        "planned": len(plan),
        "inference_completed": len(records),
        "graded": sum(r["graded"] for r in trials),
        "main_driver_finished": bool(experiment.get("finished_at")),
        "recovery_state": recovery_state,
        "driver_finished": bool(experiment.get("finished_at"))
        and recovery_state not in ("planned", "waiting", "running"),
        "original_attempt_run_errors": sum(r.get("valid") is False for r in records),
        "totals": totals,
        "size_groups": groups,
        "minimum_successful_budgets": minimums,
        "billing_issues": billing_issues,
        "trials": trials,
        "recommendation_rule": "Only after every planned trial in a size group is graded with no run/infra errors: choose the smallest budget attaining the group's highest observed resolved count.",
        "limitations": [
            "One task per repository and one final run per budget; candidate budgets are exploratory, not general size thresholds.",
            "Search counts identify explicit Bash requests, including log filtering; they exclude custom script searches and internal map scans.",
            "Budget zero is the plain baseline; minimum successful budget zero does not establish that a map can never help.",
            "Costs include completed trials only while running; current in-flight model charges are not included in progress totals.",
        ],
    }
    write_json(root / "budget-report.json", report)
    write_json(root / "search-audit.json", audit)
    columns = [
        "budget",
        "condition",
        "repo",
        "instance_id",
        "source_lines",
        "inference_completed",
        "graded",
        "resolved",
        "run_valid",
        "exit_status",
        "api_calls",
        "billed_responses",
        "prompt_tokens",
        "cached_prompt_tokens",
        "completion_tokens",
        "server_cost_usd",
        "selected_attempt_server_cost_usd",
        "previous_attempt_server_cost_usd",
        "infra_retry",
        "unreturned_request_charge_unknown",
        "search_commands",
        "search_actions",
        "text_search_commands",
        "file_locator_commands",
        "search_parse_error_actions",
        "elapsed_seconds",
        "scan_cap_warning",
        "trajectory",
        "evaluation_report",
    ]
    with (root / "budget-trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trials)
    once = experiment.get("repomap_refresh") == "once"
    lines = [
        "# Plain 与单次 RepoMap 对照" if once else "# 仓库规模与 RepoMap 预算实验",
        "",
        f"模型：`{experiment['model']}`。推理 {len(records)}/{len(plan)}，官方判题 {report['graded']}/{len(plan)}。",
        "",
        (
            "各组使用相同的固定镜像、源码扫描上限、累计历史及运行限制。预算0为plain；非零预算组只根据原始任务生成并注入一次地图，后续保持不变，预算为地图token上限。"
            if once
            else "各档使用相同的固定镜像、源码扫描上限、每轮刷新、累计历史及运行限制。预算0为plain；其他预算为固定地图token上限。"
        ),
        "",
        "## 按预算汇总",
        "",
        "| 预算 | 推理完成 | 判题完成 | 通过 | 搜索子命令 | 搜索请求 | 已返回服务端费用 USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for budget, s in totals.items():
        lines.append(
            f"| {budget} | {s['inference_completed']}/{s['planned']} | {s['graded']}/{s['planned']} | {s['resolved']} | {s['search_commands']} | {s['search_actions']} | {s['server_cost_usd']} |"
        )
    lines += [
        "",
        (
            "各档均已完成推理和官方判题，以下为完整结果。"
            if report["inference_completed"] == report["graded"] == report["planned"] and report["driver_finished"]
            else "运行中各档完成数量不同，当前部分费用和搜索计数不可直接比较。完成后再解读全部结果。"
        ),
        "基础设施补跑状态："
        + str(recovery_state or "无需补跑")
        + "。费用包含原尝试已返回的服务端费用；搜索与通过情况使用补跑后的完整轨迹。",
        "被终止的未返回请求可能产生尚未确认的费用；unreturned_request_charges_unknown单列，不按零费用处理。",
        "",
        "## 按源码长度分组",
        "",
        "每组单元格为：通过数 / 搜索子命令数 / 服务端费用USD。候选预算仅在该组所有档位全部判题、无运行或基础设施错误后计算。",
        "",
        "| 源码行数范围 | "
        + " | ".join(str(b) for b in experiment["budgets"])
        + " | 候选预算（含plain） | 候选非零地图预算 |",
        "| --- | " + " | ".join("---:" for _ in experiment["budgets"]) + " | ---: | ---: |",
    ]
    for group in groups:
        cells = [
            f"{s['resolved']}/{s['search_commands']}/${Decimal(s['server_cost_usd']):.4f}"
            for s in group["budgets"].values()
        ]
        candidate = group["candidate_budget"]
        positive = group["candidate_positive_map_budget"]
        unavailable = "无成功预算" if group["recommendation_ready"] else "待完成/复核"
        lines.append(
            f"| {group['source_lines_min']:,}–{group['source_lines_max']:,} | "
            + " | ".join(cells)
            + f" | {candidate if candidate is not None else unavailable} | {positive if positive is not None else unavailable} |"
        )
    lines += [
        "",
        "## 各仓库的最小已测通过预算",
        "",
        "这是一次实验中的观测值，不代表模型存在确定的预算门槛。较大预算也可能失败。",
        "",
        "| 仓库 | 源码行数 | 已通过预算 | 最小已测通过预算（含plain） | 最小已测通过非零预算 |",
        "| --- | ---: | --- | ---: | ---: |",
    ]
    for row in minimums:
        minimum = row["minimum_tested_successful_budget"]
        state = str(minimum) if minimum is not None else ("均未通过" if row["complete"] else "待完成")
        positive = row["minimum_tested_successful_positive_budget"]
        positive_state = str(positive) if positive is not None else ("无正预算通过" if row["complete"] else "待完成")
        lines.append(
            f"| {row['repo']} | {row['source_lines']:,} | {', '.join(map(str, row['passed_budgets']))} | {state} | {positive_state} |"
        )
    lines += [
        "",
        "## 口径与限制",
        "",
        "- 候选规则：先选本组通过数最高的预算，再取其中最小预算；包括plain=0。费用和搜索次数同时提供，不混入该规则。",
        "- 每个仓库一个任务，每档只运行一次。结果用于选择下一轮候选，不能从20条样本推导普遍的仓库长度阈值。",
        "- 费用来自已完成轨迹的服务端usage.cost；若billing_issues非空，必须先复核再比较费用。",
        "- 统计显式搜索请求，包括日志过滤；一条Bash请求可能包含多个搜索子命令，条件分支不保证每个子命令都执行。解析错误另行记录。",
        "- 扫描扩展后的覆盖、基线镜像预置修改见scan-coverage.json；普通语言过滤和无法解码的文件仍可能被跳过。",
        "",
        "[完整JSON](budget-report.json) · [逐条CSV](budget-trials.csv) · [搜索审计](search-audit.json)",
        "",
    ]
    (root / "budget-report.md").write_text("\n".join(lines))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--watch", action="store_true", help="Update reports until the driver records completion")
    args = parser.parse_args()
    root = args.run.resolve()
    previous = None
    while True:
        report = summarize(root)
        if report:
            state = (report["inference_completed"], report["graded"])
            if state != previous:
                print("REPORT_PROGRESS", *state, "/", report["planned"], flush=True)
                previous = state
            if report["driver_finished"] or not args.watch:
                if report["billing_issues"]:
                    raise SystemExit("Billing validation issues; inspect budget-report.json")
                return
        elif not args.watch:
            raise SystemExit("Experiment plan has not been created")
        time.sleep(20)


if __name__ == "__main__":
    main()
