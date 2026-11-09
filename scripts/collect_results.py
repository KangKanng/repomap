"""Collect completed experiments, traces, patches and official reports into a local ZIP."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values
from search_metrics import SearchCounter

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
from minisweagent.models.utils.content_string import get_content_string  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=PROJECT / "benchmarks/results")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base = args.results_root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or base / f"collected-{stamp}").resolve()
    if output.exists():
        parser.error(f"Collection output must be a new directory: {output}")
    output.mkdir(parents=True)
    sensitive = re.compile(r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)$", re.I)
    settings = dict(dotenv_values(PROJECT / ".env")) | dict(os.environ)
    secrets = sorted(
        {v for k, v in settings.items() if sensitive.search(k) and isinstance(v, str) and len(v) >= 12},
        key=len,
        reverse=True,
    )

    def clean(value):
        if isinstance(value, dict):
            return {
                k: "<redacted>" if sensitive.search(k) and v not in (None, "") else clean(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "<redacted>")
        return value

    manifest = []

    def write(relative, value, source=None):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value.encode() if isinstance(value, str) else value
        path.write_bytes(raw)
        item = {"file": str(relative), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if source:
            original = source.read_bytes()
            item.update(
                source=str(source), source_sha256=hashlib.sha256(original).hexdigest(), redacted=original != raw
            )
        manifest.append(item)
        return str(relative)

    def write_json(relative, value):
        return write(relative, json.dumps(clean(value), indent=2, ensure_ascii=False) + "\n")

    def copy_source(source, relative):
        raw = source.read_bytes()
        if source.suffix == ".json":
            original = json.loads(raw)
            sanitized = clean(original)
            if sanitized != original:
                raw = (json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n").encode()
        else:
            raw = clean(raw.decode("utf-8")).encode()
        return write(relative, raw, source)

    groups, specs, source_roots = {}, [], set()
    for path in sorted(base.glob("*/repomap-vs-plain.json")):
        comparison = read(path)
        metrics = read(path.parent / "additional-metrics.json")
        searches = read(path.parent / "search-behavior.json")
        outcomes = {r["instance_id"]: r for r in comparison["runs"]}
        search_rows = {r["trajectory"]: r for r in searches["attempts"]}
        for variant in ("plain", "repomap"):
            gid = f"{path.parent.name}-{variant}"
            groups[gid] = {
                "series": "historical",
                "variant": variant,
                "map_budget": 1024 if variant == "repomap" else 0,
                "refresh": "interval",
                "source": str(path.parent),
                "expected_resolved": comparison[variant]["resolved"],
                "expected_cost_all_usd": comparison[variant]["server_cost_usd"],
                "comparison_limits": comparison["caveats"],
            }
            for row in (r for r in metrics["attempts"] if r["variant"] == variant):
                outcome = outcomes[row["instance_id"]]
                specs.append(
                    {
                        **row,
                        "group": gid,
                        "selected": row["final_attempt"],
                        "resolved": outcome[f"{variant}_resolved"] if row["final_attempt"] else None,
                        "evaluation_report": outcome[f"{variant}_evaluation_report"] if row["final_attempt"] else None,
                        "expected_search_commands": search_rows[row["trajectory"]]["search_commands"],
                        "scan_cap_warning": any(
                            any(k in warning for k in ("max_files", "max_file_bytes", "max_total_bytes"))
                            for warning in row.get("map_warning_counts", {})
                        ),
                    }
                )
        source_roots.update(Path(value) for value in comparison["sources"].values())

    for path in sorted(base.glob("*/budget-report.json")):
        report = read(path)
        if not report.get("driver_finished") or report["inference_completed"] != report["graded"]:
            continue
        assert report["graded"] == report["planned"] and not report["billing_issues"]
        experiment = read(path.parent / "experiment.json")
        source_roots.add(path.parent)
        for budget, total in report["totals"].items():
            gid = f"{path.parent.name}-b{budget}"
            groups[gid] = {
                "series": path.parent.name,
                "variant": "plain" if int(budget) == 0 else "repomap",
                "map_budget": int(budget),
                "refresh": experiment.get("repomap_refresh", "interval"),
                "source": str(path.parent),
                "expected_resolved": total["resolved"],
                "expected_cost_all_usd": total["server_cost_usd"],
                "unreturned_request_charges_unknown": total.get("unreturned_request_charges_unknown", 0),
            }
        selected = {r["trajectory"] for r in report["trials"]}
        for row in report["trials"]:
            specs.append(
                {
                    **row,
                    "group": f"{path.parent.name}-b{row['budget']}",
                    "selected": True,
                    "expected_search_commands": row["search_commands"],
                }
            )
        for row in read(path.parent / "summary.json")["runs"]:
            if row["trajectory"] not in selected:
                specs.append({**row, "group": f"{path.parent.name}-b{row['budget']}", "selected": False})

    counter, attempts, seen = SearchCounter(), [], set()
    for spec in specs:
        source = Path(spec["trajectory"])
        assert source not in seen, f"Duplicate billed attempt: {source}"
        seen.add(source)
        data = read(source)
        messages, info = data["messages"], data["info"]
        counts, _ = counter.messages(messages)
        if "expected_search_commands" in spec:
            assert counts["search_commands"] == spec["expected_search_commands"], source
        cost, usage, billed, models = Decimal(0), Counter(), 0, Counter()
        for message in messages:
            extra = message.get("extra", {})
            if "cost" not in extra:
                continue
            response = extra.get("response", {})
            tokens = response.get("usage", {})
            charge = Decimal(str(extra["cost"]))
            assert extra.get("cost_source", "").startswith("server:"), source
            assert charge == Decimal(str(tokens["cost"])), source
            cost += charge
            billed += 1
            models[response.get("model", "unknown")] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] += tokens.get(key, 0) or 0
            usage["cached_prompt_tokens"] += (tokens.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        relative = Path("trajectories") / source.relative_to(base)
        trace = copy_source(source, relative)
        row = {
            "group": spec["group"],
            "instance_id": spec["instance_id"],
            "repo": spec["repo"],
            "source_lines": spec.get("source_lines"),
            "selected": spec["selected"],
            "resolved": spec.get("resolved"),
            "exit_status": info.get("exit_status"),
            "api_calls": info.get("model_stats", {}).get("api_calls", billed),
            "billed_responses": billed,
            "server_cost_usd": str(cost),
            **dict(usage),
            **counts,
            "scan_cap_warning": spec.get("scan_cap_warning", False),
            "trajectory_source": str(source),
            "trajectory_bundle": trace,
            "response_models": dict(models),
            "evaluation_source": spec.get("evaluation_report"),
        }
        if row["selected"]:
            location = Path(row["group"]) / row["instance_id"]
            submission = info.get("submission", "")
            row["patch_bundle"] = write(Path("patches") / location.with_suffix(".patch"), clean(submission))
            maps = [get_content_string(m) for m in messages if get_content_string(m).startswith("<repository_map>")]
            assert len(maps) == (1 if groups[row["group"]]["map_budget"] else 0), source
            if maps:
                row["map_bundle"] = write(Path("maps") / location.with_suffix(".txt"), clean(maps[0]))
            if row["evaluation_source"]:
                official = Path(row["evaluation_source"])
                row["evaluation_bundle"] = copy_source(official, Path("official") / location.with_suffix(".json"))
                scored = read(official).get(row["instance_id"], {})
                if "resolved" in scored:
                    assert scored["resolved"] == row["resolved"]
        attempts.append(row)

    trials = [row for row in attempts if row["selected"]]
    summaries = []
    for gid, settings in groups.items():
        all_rows = [r for r in attempts if r["group"] == gid]
        chosen = [r for r in all_rows if r["selected"]]
        final_cost = sum((Decimal(r["server_cost_usd"]) for r in chosen), Decimal(0))
        all_cost = sum((Decimal(r["server_cost_usd"]) for r in all_rows), Decimal(0))
        resolved = sum(r["resolved"] is True for r in chosen)
        assert resolved == settings["expected_resolved"]
        assert all_cost == Decimal(settings["expected_cost_all_usd"])
        prompts = sum(r.get("prompt_tokens", 0) for r in chosen)
        cached = sum(r.get("cached_prompt_tokens", 0) for r in chosen)
        summaries.append(
            {
                "group": gid,
                **settings,
                "instances": len(chosen),
                "resolved": resolved,
                "attempts_all": len(all_rows),
                "api_calls_selected": sum(r["api_calls"] for r in chosen),
                "api_calls_all": sum(r["api_calls"] for r in all_rows),
                "search_commands_selected": sum(r["search_commands"] for r in chosen),
                "search_commands_all": sum(r["search_commands"] for r in all_rows),
                "search_actions_selected": sum(r["search_actions"] for r in chosen),
                "server_cost_selected_usd": str(final_cost),
                "server_cost_all_attempts_usd": str(all_cost),
                "prompt_tokens_selected": prompts,
                "cached_prompt_tokens_selected": cached,
                "cached_prompt_token_fraction": cached / prompts if prompts else None,
                "scan_cap_warning_instances": sum(r["scan_cap_warning"] for r in chosen),
            }
        )
    for row in summaries:
        baseline = next(r for r in summaries if r["series"] == row["series"] and r["map_budget"] == 0)
        row["search_reduction_percent_vs_own_plain"] = 100 * (
            1 - row["search_commands_selected"] / baseline["search_commands_selected"]
        )

    allowed = {".md", ".json", ".jsonl", ".csv", ".yaml"}
    for directory in sorted(source_roots):
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix in allowed:
                copy_source(path, Path("reports") / path.relative_to(base))
        for sub in ("search-cases", "initial-map-reconstruction"):
            for path in sorted((directory / sub).rglob("*")):
                if path.is_file() and path.suffix in allowed | {".txt"}:
                    copy_source(path, Path("reports") / path.relative_to(base))

    performance = None
    performance_path = base / "validation/report.json"
    if performance_path.exists():
        performance = read(performance_path)
        for path in sorted(performance_path.parent.rglob("*")):
            if path.is_file() and path.suffix in allowed | {".txt"}:
                copy_source(path, Path("performance") / path.relative_to(performance_path.parent))

    active = None
    pointer = Path("/tmp/repomap-static-active.json")
    if pointer.exists():
        state = read(pointer)
        directory = Path(state["output"])
        if (directory / "budget-report.json").exists():
            report = read(directory / "budget-report.json")
            active = {
                "output": str(directory),
                "snapshot_at": datetime.now(timezone.utc).isoformat(),
                "inference_completed": report["inference_completed"],
                "graded": report["graded"],
                "planned": report["planned"],
                "driver_finished": report["driver_finished"],
                "included_in_completed_results": directory in source_roots,
                "totals": report["totals"],
            }
            write_json("latest-run-snapshot.json", active)

    catalog = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory == output or directory.name.startswith("collected-"):
            continue
        category = "completed_model_results" if directory in source_roots else "validation_or_early_trial"
        if directory.name.startswith("static1024-once-") and directory not in source_roots:
            category = (
                "latest_static_experiment" if active and str(directory) == active["output"] else "aborted_startup"
            )
        if directory.name == "validation":
            category = "map_performance_without_model_calls"
        catalog.append(
            {
                "directory": str(directory),
                "category": category,
                "included_in_completed_results": directory in source_roots,
            }
        )
    write_json("run-catalog.json", catalog)
    write_json("groups.json", summaries)
    write_json("trials.json", trials)
    write_json("attempts.json", attempts)

    def csv_file(name, rows):
        columns = list(
            dict.fromkeys(key for row in rows for key, value in row.items() if not isinstance(value, (dict, list)))
        )
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        write(name, stream.getvalue())

    csv_file("groups.csv", summaries)
    csv_file("trials.csv", trials)
    csv_file("attempts.csv", attempts)
    total = sum((Decimal(r["server_cost_usd"]) for r in attempts), Decimal(0))
    response_models = sorted({model for row in attempts for model in row["response_models"]})
    lines = [
        "# 已完成实验结果汇总",
        "",
        f"收集时间：{datetime.now(timezone.utc).isoformat()}。",
        f"共 {len(trials)} 条最终有效任务结果、{len(attempts)} 次模型运行尝试，涉及 20 个不同仓库。",
        "服务端模型：" + ", ".join(response_models) + "。这里的通过指官方指定判分项满足，不代表仓库全量测试通过。",
        "",
        "## 模型实验",
        "",
        "| 轮次 | 地图预算 | 刷新 | 官方通过 | 最终搜索子命令 | 相对本轮plain减少 | 最终轨迹费用 USD | 含失败尝试的已返回费用 USD |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        series = "早期历史对照" if row["series"] == "historical" else row["series"]
        lines.append(
            f"| {series} | {row['map_budget']} | {row['refresh'] if row['map_budget'] else '关闭'} | {row['resolved']}/{row['instances']} | "
            f"{row['search_commands_selected']} | {row['search_reduction_percent_vs_own_plain']:+.1f}% | "
            f"{row['server_cost_selected_usd']} | {row['server_cost_all_attempts_usd']} |"
        )
    lines += [
        "",
        "正的减少率表示搜索减少，负值表示增加。搜索按同一份当前 Bash 统计器从原轨迹重算，并逐项核对原报告。",
        "搜索包含文件定位、文本搜索和日志过滤，统计的是显式请求，不保证每个条件分支实际执行；不含自定义脚本内部搜索和地图内部扫描。",
        "",
        "## 配置和费用口径",
        "",
        "- interval 为固定预算、每轮根据任务和最近6条消息重排；once 仅按原始任务生成并注入一次；plain关闭地图。原生累计历史，没有摘要。",
        "- 早期对照使用默认扫描上限，12条RepoMap任务有超限警告；Gson、Tokio在原推理后仅于修复镜像复评补丁，plain推理直接使用修复镜像。",
        "- 预算实验四档使用相同固定镜像和扩展扫描上限，均未触及扫描上限。应在同一轮内比较，不把历史两轮差异归因于地图预算。",
        "- 最终搜索、通过数和最终费用取每任务最终有效轨迹；失败尝试另存attempts，完整已返回费用不丢弃失败尝试。",
        "- 历史RepoMap含一次Ripgrep失败后重跑；预算plain含一次Redis请求挂起后补跑。原尝试完整保留。",
        f"- 上述完整实验全部尝试的已返回服务端费用合计 ${total}；逐条核对extra.cost与usage.cost相等。Redis一条中止请求费用未知，此合计不是最终账单。",
        "- 每仓库仅一个任务、每组一次有效运行；原始映射只保留最终版本，无法恢复每一轮地图。导出的maps均为轨迹中实际保留版本。",
        "",
    ]
    if performance:
        cold = [m["cold_build_seconds"] for c in performance["cases"] for m in c["measurements"]]
        warm = [m["warm_build_median_seconds"] for c in performance["cases"] for m in c["measurements"]]
        lines += [
            "## 无模型地图性能实验",
            "",
            f"{performance['successful']}/{performance['completed']}个仓库生成成功，预算1024；空缓存生成均值{statistics.mean(cold):.3f}秒，热缓存生成均值{statistics.mean(warm):.3f}秒。",
            "耗时不含源码传输；这里的成功指生成地图成功，不是代码修复成功。[性能报告](performance/summary.md)。",
            "",
        ]
    if active and not active["included_in_completed_results"]:
        lines += [
            "## 本轮静态实验进度快照",
            "",
            f"单次1024地图实验仍在运行：推理{active['inference_completed']}/{active['planned']}、判题{active['graded']}/{active['planned']}。",
            "该快照未计入上方完整实验统计。[状态快照](latest-run-snapshot.json)。",
            "",
        ]
    lines += [
        "## 文件",
        "",
        f"- [分组汇总CSV](groups.csv)、[{len(trials)}条任务明细CSV](trials.csv)、[全部尝试CSV](attempts.csv)；对应JSON保留完整字段。",
        "- trajectories/：全部已完成实验的原始轨迹副本；敏感凭据字段及已知密钥已脱敏。",
        "- patches/、maps/、official/：最终提交补丁、实际保留地图和官方逐任务报告。",
        "- reports/：原汇总、逐条搜索、预算分析与失败原因分析快照；performance/：无模型性能报告和20份地图。",
        "- [运行目录分类](run-catalog.json)：标注正式结果、验证、早期试运行和本轮失败启动，避免混入最终比较。",
        "- [SHA-256清单](manifest.json)：来源路径、原文件哈希、打包文件哈希及脱敏状态。未复制.env、runner日志或Docker镜像。",
        "",
    ]
    write("README.md", "\n".join(lines))
    write_json(
        "collection.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "selected_trials": len(trials),
            "attempts": len(attempts),
            "billed_responses": sum(r["billed_responses"] for r in attempts),
            "server_returned_cost_usd": str(total),
            "all_costs_match_server_usage_cost": True,
            "unknown_unreturned_requests": sum(r.get("unreturned_request_charges_unknown", 0) for r in summaries),
            "latest_run": active,
        },
    )
    write_json("manifest.json", manifest.copy())
    for path in output.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert not any(secret.encode() in raw for secret in secrets), f"Credential remained in {path.name}"
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(output.name) / path.relative_to(output))
    result = {
        "directory": str(output),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "selected_trials": len(trials),
        "attempts": len(attempts),
        "groups": len(summaries),
    }
    Path("/tmp/repomap-results-collection.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
