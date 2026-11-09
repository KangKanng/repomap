"""Evaluate completed historical RepoMap conditions and export a snapshot-aware comparison, offline."""

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "benchmarks/results"


def load(path):
    return json.loads(path.read_text())


def percent(value):
    return f"{value:.2%}" if value is not None else "N/A"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-run", type=Path, default=RESULTS / "swebench-offline20-20261029T095019Z")
    parser.add_argument("--budget-run", type=Path, default=RESULTS / "budget-sweep-20261102T035731Z")
    parser.add_argument("--once-run", type=Path, default=RESULTS / "static1024-once-20261105T145121Z")
    parser.add_argument(
        "--reconstruction-dir", type=Path, help="Optional verified first-map manifests, budgets 1024/2048/4096"
    )
    parser.add_argument("--output", type=Path, required=True, help="New directory; original runs remain unchanged")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output must be a new directory")
    output.mkdir(parents=True)
    selected = load(args.legacy_run / "offline-report-repaired.json")
    overrides = output / "legacy-selected-trajectories.json"
    overrides.write_text(json.dumps({r["instance_id"]: r["trajectory"] for r in selected["runs"]}, indent=2) + "\n")
    conditions = [
        ("legacy-final-1024", "旧版1024，最终保留", args.legacy_run, "retained-final", 1024),
        *[
            (f"interval-final-{b}", f"Interval {b}，最终保留", args.budget_run, "retained-final", b)
            for b in (1024, 2048, 4096)
        ],
        ("once-initial-1024", "Once 1024，实际初始", args.once_run, "initial-once", 1024),
    ]
    if args.reconstruction_dir:
        conditions += [
            (f"interval-initial-{b}", f"Interval {b}，重建初始", args.budget_run, "reconstructed-initial", b)
            for b in (1024, 2048, 4096)
        ]
    groups, rows, truths = {}, [], {}
    for key, label, run, snapshot, budget in conditions:
        command = [
            sys.executable,
            str(PROJECT / "scripts/evaluate_map_quality.py"),
            str(run.resolve()),
            "--snapshot",
            snapshot,
            "--budget",
            str(budget),
            "--output",
            str(output / key),
        ]
        if key.startswith("legacy-"):
            command += ["--trajectory-overrides", str(overrides)]
        if snapshot == "reconstructed-initial":
            command += ["--reconstruction", str(args.reconstruction_dir.resolve() / f"budget-{budget}.json")]
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError(f"{key}: {process.stderr}")
        report_path = output / key / "report.json"
        report = load(report_path)
        scored = report["instances"]
        file_scores = [r["target_file_recall"] for r in scored]
        groups[key] = {
            "label": label,
            "map_snapshot": report["map_snapshot"],
            "budget": budget,
            "run": report["run"],
            "report": str(report_path.relative_to(output)),
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "aggregate": report["aggregate"],
            "instances": len(scored),
            "any_target_hit": sum(bool(m["numerator"]) for m in file_scores),
            "all_targets_hit": sum(m["value"] == 1 for m in file_scores),
        }
        for row in scored:
            iid = row["instance_id"]
            truth = (row["reference_base_commit"], row["reference_patch_sha256"])
            if iid in truths and truths[iid] != truth:
                raise ValueError(f"Official references differ across conditions: {iid}")
            truths[iid] = truth
            rows.append({"condition": key, **row})
        print(
            f"{key}: {len(scored)} maps, file recall {percent(report['aggregate']['target_file_recall']['macro'])}",
            flush=True,
        )
    manifest = {
        "schema_version": 1,
        "experiment": "A",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "post_hoc_only": True,
        "model_calls": 0,
        "unique_tasks": len(truths),
        "map_views": len(rows),
        "groups": groups,
        "unscored_metrics": ["target_symbol_recall", "dependency_coverage", "symbol_precision", "map_utilization"],
        "reason": "Requires reviewed reference, full symbol inventory or temporally valid utilization annotations",
    }
    (output / "comparison.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    stream = io.StringIO()
    fields = [
        "condition",
        "instance_id",
        "repo",
        "map_snapshot",
        "map_budget",
        "hit_files",
        "target_files",
        "target_file_recall",
        "existing_target_file_recall",
        "map_sha256",
        "trajectory_sha256",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        metric = row["target_file_recall"]
        writer.writerow(
            {
                **{k: row[k] for k in fields if k in row and k != "target_file_recall"},
                "hit_files": metric["numerator"],
                "target_files": metric["denominator"],
                "target_file_recall": metric["value"],
                "existing_target_file_recall": metric["existing_target_file_recall"]["value"],
            }
        )
    (output / "by-instance.csv").write_text(stream.getvalue())
    lines = [
        "# 历史实验 A：地图质量回溯",
        "",
        f"{len(truths)} 条相同任务，{len(groups)} 组地图视图，合计 {len(rows)} 张地图。无模型调用、无新增推理或判题。",
        "同一推理运行可有初始/最终两个视图，不能当作独立任务相加。Plain 没有地图，地图质量为不适用，不记为0。",
        "",
    ]
    for title, initial in (("初始地图", True), ("最终保留地图", False)):
        keys = [k for k, g in groups.items() if (g["map_snapshot"] != "retained_final") == initial]
        lines += [
            f"## {title}",
            "",
            "| 条件 | 文件召回宏平均 | 微平均 | 既存目标微平均 | 至少命中一个 / 全命中 | 报告 |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for key in keys:
            group = groups[key]
            total, existing = (
                group["aggregate"][name] for name in ("target_file_recall", "existing_target_file_recall")
            )
            lines.append(
                f"| {group['label']} | {percent(total['macro'])} | {total['numerator']}/{total['denominator']} ({percent(total['micro'])}) "
                f"| {existing['numerator']}/{existing['denominator']} ({percent(existing['micro'])}) "
                f"| {group['any_target_hit']}/{group['instances']} · {group['all_targets_hit']}/{group['instances']} "
                f"| [明细]({key}/summary.md) |"
            )
        lines += [
            "",
            "| 仓库 | " + " | ".join(groups[k]["label"] for k in keys) + " |",
            "| --- | " + " | ".join("---:" for _ in keys) + " |",
        ]
        lookup = {(r["condition"], r["instance_id"]): r for r in rows}
        for iid in sorted(truths):
            sample = next(r for r in rows if r["instance_id"] == iid)
            scores = [lookup.get((key, iid), {}).get("target_file_recall") for key in keys]
            lines.append(
                f"| {sample['repo']} | "
                + " | ".join(f"{s['numerator']}/{s['denominator']}" if s else "N/A" for s in scores)
                + " |"
            )
        lines.append("")
    lines += [
        "## 解释边界与待完成项",
        "",
        "- Once 使用实际保存且始终不变的地图；Interval 初始地图使用原始源码哈希校验后的历史逻辑和固定镜像重建，保留上下文及源码快照，不冒充历史请求抓包。",
        "- Interval 最终地图受后期对话和 Agent 修改影响，只描述结束时地图，不能解释更早的搜索动作。旧版没有完整代码哈希，未补造其首轮地图。",
        "- 文件真值来自同一 base_commit 的官方 patch；每组均保留全部官方目标，新增文件另外报告，未使用 Agent 自己的补丁作为召回真值。",
        "- 其余四项均尚待标注，N/A：目标符号、关键依赖、全图符号相关性、地图利用证据；不是全五项已经完成。",
        "- 最终地图的声明行可能来自修改后源码，不能未经坐标对齐直接复用基线符号标注；单一 interval 快照也不能证明每个动作之前可用的地图内容。",
        "- 预算增加和文件覆盖变化是描述性对照；不能直接解释解决率或搜索量，更不能据此推导因果。",
        "",
        "[机器可读汇总](comparison.json) · [逐任务CSV](by-instance.csv)",
        "",
    ]
    (output / "summary.md").write_text("\n".join(lines))
    print(json.dumps({"output": str(output), "map_views": len(rows), "model_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
