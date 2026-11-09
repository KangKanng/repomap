"""Summarize paired SCBench chains, keeping missing/failed trials explicit."""

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path
from statistics import mean

import yaml
from run_scb_compare import trial_metrics
from scb_infer import write_json


def mean_or_none(values):
    return mean(values) if values else None


def generated_code_quality(directory, asset_paths=()):
    """Use official per-file/symbol metrics, excluding supplied static assets."""
    files_path = directory / "quality_analysis/files.jsonl"
    symbols_path = directory / "quality_analysis/symbols.jsonl"
    if not files_path.exists():
        return None

    def generated(name):
        return not any(name == p or name.startswith(p.rstrip("/") + "/") for p in asset_paths)

    files = [json.loads(line) for line in files_path.read_text().splitlines() if line.strip()]
    files = [f for f in files if generated(f["file_path"])]
    symbols = []
    if symbols_path.exists():
        symbols = [json.loads(line) for line in symbols_path.read_text().splitlines() if line.strip()]
        symbols = [s for s in symbols if generated(s["file_path"]) and s["type"] in {"function", "method"}]
    loc = sum(f.get("loc", 0) for f in files)
    lint = sum(f.get("lint_errors", 0) for f in files)
    return {
        "files": len(files),
        "loc": loc,
        "lint_diagnostics": lint,
        "lint_per_100_loc": 100 * lint / loc if loc else None,
        "functions": len(symbols),
        "function_cc_mean": mean_or_none([s["complexity"] for s in symbols]),
        "function_cc_max": max((s["complexity"] for s in symbols), default=None),
        "function_nesting_max": max((s.get("max_nesting_depth", 0) for s in symbols), default=None),
    }


def final_quality_pairs(trials, protocol):
    indexed = {(t["problem"], t["checkpoint"], t["variant"]): t for t in trials}
    rows = []
    for item in protocol["selection"]:
        checkpoint = item["checkpoints"][-1]
        group = [indexed.get((item["name"], checkpoint, variant)) for variant in protocol["variants"]]
        if not all(
            t and t["exit_status"] == "Submitted" and t.get("evaluated") and not t.get("infrastructure_failure")
            for t in group
        ):
            continue
        config = yaml.safe_load((Path(protocol["problems_path"]) / item["name"] / "config.yaml").read_text())
        assets = [a.get("save_path", a["path"]) for a in config.get("static_assets", {}).values()]
        quality = [generated_code_quality(Path(t["evaluation_directory"]), assets) for t in group]
        if not all(quality):
            continue
        row = {
            "problem": item["name"],
            "checkpoint": checkpoint,
            "both_strict_success": all(t.get("all_non_error_passed") for t in group),
        }
        for variant, metrics in zip(protocol["variants"], quality, strict=True):
            row.update({variant + "_" + k: v for k, v in metrics.items()})
        rows.append(row)
    aggregates = {}
    for name, subset in [
        ("all_completed_final_pairs", rows),
        ("mutually_successful_final_pairs", [r for r in rows if r["both_strict_success"]]),
    ]:
        aggregates[name] = {"problems": len(subset)}
        for variant in protocol["variants"]:
            for metric in ["loc", "lint_per_100_loc", "function_cc_mean", "function_cc_max", "function_nesting_max"]:
                key = variant + "_" + metric
                aggregates[name][key] = mean_or_none([r[key] for r in subset if r.get(key) is not None])
    return rows, aggregates


def paired(trials, *, later_only=True, require_success=False):
    indexed = {(t["problem"], t["checkpoint"], t["variant"]): t for t in trials}
    pairs = []
    for (problem, checkpoint, variant), plain in sorted(indexed.items()):
        if variant != "plain" or (later_only and plain["checkpoint_index"] == 1):
            continue
        full = indexed.get((problem, checkpoint, "once-full"))
        if full is None:
            continue
        if not all(
            t["exit_status"] == "Submitted" and t.get("evaluated") and not t.get("infrastructure_failure")
            for t in [plain, full]
        ):
            continue
        if require_success and not all(t.get("all_non_error_passed") for t in [plain, full]):
            continue
        pairs.append(
            {
                "problem": problem,
                "checkpoint": checkpoint,
                "plain_search": plain["search_commands"],
                "once_full_search": full["search_commands"],
                "search_delta": full["search_commands"] - plain["search_commands"],
                "plain_calls": plain["model_calls"],
                "once_full_calls": full["model_calls"],
                "plain_cost": plain["cost"],
                "once_full_cost": full["cost"],
                "plain_success": plain.get("all_non_error_passed"),
                "once_full_success": full.get("all_non_error_passed"),
            }
        )
    return pairs


def summarize_pairs(pairs):
    by_problem = {}
    for pair in pairs:
        by_problem.setdefault(pair["problem"], []).append(pair)
    plain = mean_or_none([x["plain_search"] for x in pairs])
    full = mean_or_none([x["once_full_search"] for x in pairs])
    # Resample independent problems; keep all checkpoints belonging to each draw.
    rng = random.Random(20261109)
    clusters = list(by_problem.values())
    interval = None
    if len(clusters) >= 2:
        draws = []
        for _ in range(5000):
            sample = [pair for cluster in rng.choices(clusters, k=len(clusters)) for pair in cluster]
            draws.append(mean(x["search_delta"] for x in sample))
        draws.sort()
        interval = [draws[124], draws[4874]]
    return {
        "checkpoints": len(pairs),
        "problems": len(by_problem),
        "plain_mean_search": plain,
        "once_full_mean_search": full,
        "mean_search_delta": mean_or_none([x["search_delta"] for x in pairs]),
        "plain_mean_model_calls": mean_or_none([x["plain_calls"] for x in pairs]),
        "once_full_mean_model_calls": mean_or_none([x["once_full_calls"] for x in pairs]),
        "plain_mean_recorded_cost_usd": mean_or_none([x["plain_cost"] for x in pairs]),
        "once_full_mean_recorded_cost_usd": mean_or_none([x["once_full_cost"] for x in pairs]),
        "relative_search_change": full / plain - 1 if plain else None,
        "cluster_bootstrap_95_interval": interval,
        "lower_search_checkpoints": sum(x["search_delta"] < 0 for x in pairs),
        "higher_search_checkpoints": sum(x["search_delta"] > 0 for x in pairs),
        "equal_search_checkpoints": sum(x["search_delta"] == 0 for x in pairs),
    }


def analyze(output):
    protocol = json.loads((output / "protocol.json").read_text())
    status = json.loads((output / "status.json").read_text())
    trials = [
        trial_metrics(p)
        for v in protocol["variants"]
        for p in sorted((output / v).glob("*/checkpoint_*/inference.json"))
    ]
    expected = sum(len(x["checkpoints"]) for x in protocol["selection"])
    summary = {"status": status, "expected_per_variant": expected, "groups": {}, "problems": []}
    for variant in protocol["variants"]:
        group = [t for t in trials if t["variant"] == variant]
        valid = [t for t in group if t.get("evaluated") and not t.get("infrastructure_failure")]
        summary["groups"][variant] = {
            "inference_done": len(group),
            "missing_inference": expected - len(group),
            "submitted": sum(t["exit_status"] == "Submitted" for t in group),
            "exit_statuses": dict(Counter(t["exit_status"] for t in group)),
            "evaluations_valid": len(valid),
            "evaluations_missing_or_infrastructure_failure": expected - len(valid),
            "core_success": sum(t.get("passed_policy") is True for t in valid),
            "strict_success": sum(t.get("all_non_error_passed") is True for t in valid),
            "all_tests_success": sum(
                t.get("tests_passed") == t.get("tests_total") and t.get("tests_total", 0) > 0 for t in valid
            ),
            "search_commands": sum(t["search_commands"] for t in group),
            "model_calls": sum(t["model_calls"] for t in group),
            "prompt_tokens": sum(t.get("prompt_tokens", 0) for t in group),
            "completion_tokens": sum(t.get("completion_tokens", 0) for t in group),
            "cost_usd_returned_responses": sum(t["cost"] for t in group),
            "unrecorded_model_calls": sum(t.get("unrecorded_model_calls", 0) for t in group),
            "pending_cost_unknown_checkpoints": sum(bool(t.get("pending_cost_unknown")) for t in group),
            "cases_passed": sum(t["tests_passed"] for t in valid),
            "cases_total": sum(t["tests_total"] for t in valid),
            "regression_passed": sum(t["regression_passed"] for t in valid),
            "regression_total": sum(t["regression_total"] for t in valid),
        }
    for item in protocol["selection"]:
        record = {"problem": item["name"], "difficulty": item["difficulty"], "checkpoints": len(item["checkpoints"])}
        for variant in protocol["variants"]:
            group = [t for t in trials if t["variant"] == variant and t["problem"] == item["name"]]
            record[variant] = {
                "done": len(group),
                "strict_success": sum(t.get("all_non_error_passed") is True for t in group),
                "core_success": sum(t.get("passed_policy") is True for t in group),
                "search_commands": sum(t["search_commands"] for t in group),
                "calls": sum(t["model_calls"] for t in group),
                "cost": sum(t["cost"] for t in group),
            }
        summary["problems"].append(record)
    pairs = paired(trials)
    summary["later_checkpoint_pairs"] = summarize_pairs(pairs)
    summary["later_checkpoint_mutually_successful_pairs"] = summarize_pairs(paired(trials, require_success=True))
    summary["all_checkpoint_pairs"] = summarize_pairs(paired(trials, later_only=False))
    quality_rows, summary["final_code_quality"] = final_quality_pairs(trials, protocol)
    write_json(output / "final-code-quality.json", quality_rows)
    write_json(output / "summary.json", summary)
    if pairs:
        with (output / "paired.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=list(pairs[0]))
            writer.writeheader()
            writer.writerows(pairs)
    p, f = (summary["groups"][v] for v in protocol["variants"])
    lines = [
        "# SCBench plain 与 once-full 对比分析",
        "",
        f"运行状态：{status['state']}。固定 {len(protocol['selection'])} 题，每组预定 {expected} 个 checkpoint；每个已启动 checkpoint 仅尝试一次。",
        "",
        "| 指标 | plain | once-full |",
        "| --- | ---: | ---: |",
    ]
    for title, key in [
        ("完成推理", "inference_done"),
        ("正常提交", "submitted"),
        ("有效官方判题", "evaluations_valid"),
        ("核心测试全部通过", "core_success"),
        ("核心、功能及回归测试全部通过（主要成功指标）", "strict_success"),
        ("全部测试通过（含错误处理）", "all_tests_success"),
        ("显式搜索子命令（描述统计）", "search_commands"),
        ("已记录模型调用", "model_calls"),
        ("已记录输入 token", "prompt_tokens"),
        ("已记录输出 token", "completion_tokens"),
        ("已记录响应费用 / USD（有缺失时为下限）", "cost_usd_returned_responses"),
        ("未完整记录的模型调用（至少）", "unrecorded_model_calls"),
        ("另有在途费用未知的中断 checkpoint", "pending_cost_unknown_checkpoints"),
    ]:
        a, b = p[key], f[key]
        if isinstance(a, float):
            a, b = f"{a:.6f}", f"{b:.6f}"
        lines.append(f"| {title} | {a} | {b} |")
    lines += [
        "",
        "## 后续 checkpoint 配对",
        "",
        "仅纳入双方正常提交且官方判题有效的 checkpoint 2 及之后轮次。失败或缺失不会以零搜索计为收益。",
    ]
    for title, key in [
        ("正常提交配对", "later_checkpoint_pairs"),
        ("双方严格通过的配对", "later_checkpoint_mutually_successful_pairs"),
    ]:
        result = summary[key]
        lines += [
            "",
            f"{title}：{result['checkpoints']} 个 checkpoint，来自 {result['problems']} 题。",
            "",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2),
            "```",
        ]
    lines += [
        "",
        "## 逐题结果",
        "",
        "成功数采用核心、功能及回归测试全部通过。搜索为该题全部已完成 checkpoint 的总量；运行未结束时两组数量可能不同。",
        "",
        "| 题目 | 难度 | checkpoint | 已推理 plain→full | 严格成功 plain→full | 搜索 plain→full | 调用 plain→full |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["problems"]:
        a, b = item["plain"], item["once-full"]
        lines.append(
            f"| {item['problem']} | {item['difficulty']} | {item['checkpoints']} | {a['done']}→{b['done']} | {a['strict_success']}→{b['strict_success']} | {a['search_commands']}→{b['search_commands']} | {a['calls']}→{b['calls']} |"
        )
    lines += [
        "",
        "## 最后一轮代码质量",
        "",
        "只配对两组都正常提交且判题有效的同题最后一个 checkpoint。读取官方 per-file / per-symbol 结果，排除题目自带静态素材目录；覆盖生成的 Python 文件（含生成的自测脚本）。诊断数量来自静态规则，不等同于程序缺陷数。复杂度和行数单独不能证明代码退化或可维护性收益。",
        "",
        "```json",
        json.dumps(summary["final_code_quality"], ensure_ascii=False, indent=2),
        "```",
    ]
    lines += [
        "",
        "## 协议与解释范围",
        "",
        f"模型：`{protocol['model']}`。题库 commit：`{protocol['problems_commit']}`；框架 commit：`{protocol['harness_commit']}`。",
        "",
        "从 36 题中固定抽取 25 题，按类别覆盖和难度配额选择。事先排除需要额外消息代理或多语言工具链的 mocked_http、test_translator；完整清单见 protocol.json。",
        "",
        "使用本项目 mini-swe-agent 2.4.6 和 .env 的模型配置。每个 checkpoint 使用官方 just-solve 当前规格，重置对话，保留本组代码与虚拟环境。plain 关闭地图；once-full 只在该 checkpoint 首次请求前生成完整路径与符号索引，无扫描/token 选择上限，随后保留在历史中。",
        "",
        "官方 slop-code eval-snapshot 读取独立源码快照。上游 LocalExecRuntime 未执行环境 setup 命令，因此先在一次性副本中用 uv init --bare 和 uv add --no-sync -r requirements.txt 准备项目与依赖锁，再使用官方 uv run。实现源码不变；官方测试、检查点配置、分组和判分保持原样。evaluation-v2 为统一重判结果，早期依赖路径失效的报告不参与分析。此适配实验不等同于发布榜单配置。",
        "",
        "首轮通常没有既有实现，部分题目含静态素材，其地图不一定为空。后续两组代码独立演化；配对差异反映完整策略结果，包含不同代码路径的影响。",
        "",
        "搜索使用本项目既有 Bash 语法解析器，只统计显式搜索命令，不包括 ls、cat、sed、嵌入 Python 脚本中的自定义搜索。区间按题目整体重采样 5000 次，不把同题多个 checkpoint 当作独立样本。一次运行不能证明稳定因果收益。",
        "",
        "费用为已返回响应累计费用；未返回请求可能存在未知费用。完整原始轨迹、快照、地图、官方判题输出和 quality_analysis 均保存在各题目录。",
    ]
    (output / "analysis.md").write_text("\n".join(lines) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summary = analyze(args.output.resolve())
    print(
        json.dumps(
            {
                "status": summary["status"]["state"],
                "groups": summary["groups"],
                "paired": summary["later_checkpoint_pairs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
