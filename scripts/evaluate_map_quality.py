"""Experiment A: score maps against official patches, keeping snapshot provenance explicit."""

import argparse
import csv
import hashlib
import io
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from map_quality_metrics import evaluate, gold_files

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
from minisweagent.models.utils.content_string import get_content_string  # noqa: E402

METRICS = ("target_file_recall", "target_symbol_recall", "dependency_coverage", "symbol_precision")


def load(path):
    return json.loads(path.read_text())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def summarize(rows):
    result = {}
    for key in (*METRICS, "existing_target_file_recall", "augmented_target_file_recall"):
        values = [row["target_file_recall"][key] if key == "existing_target_file_recall" else row[key] for row in rows]
        valid = [value for value in values if value["value"] is not None]
        denominator = sum(m["denominator"] for m in valid)
        result[key] = {
            "scored_instances": len(valid),
            "total_instances": len(rows),
            "macro": sum(m["value"] for m in valid) / len(valid) if valid else None,
            "micro": sum(m["numerator"] for m in valid) / denominator if denominator else None,
            "numerator": sum(m["numerator"] for m in valid),
            "denominator": denominator,
        }
    reviewed = [r["map_utilization"] for r in rows if r["map_utilization"]["status"] != "pending_annotation"]
    observable = [m for m in reviewed if m["can_observe"]]
    confirmed = sum(m["status"] == "confirmed_reference" for m in observable)
    result["map_utilization"] = {
        "total_instances": len(rows),
        "reviewed_instances": len(reviewed),
        "complete_trace_instances_reviewed": len(observable),
        "confirmed_references": confirmed,
        "confirmed_reference_lower_bound": confirmed / len(observable) if observable else None,
        "actual_utilization_rate": None,
        "status_counts": dict(Counter(r["map_utilization"]["status"] for r in rows)),
        "reason": "explicit evidence is observable; mental use and causal effects are not directly measurable",
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed run with plan.json and locally saved evaluation datasets")
    parser.add_argument("--labels", type=Path, help="Reviewed annotation-template.json; never read by inference")
    parser.add_argument("--output", type=Path, help="New output directory")
    parser.add_argument("--budget", type=int, help="Select one positive map budget from a multi-condition plan")
    parser.add_argument(
        "--snapshot",
        choices=("initial-once", "retained-final", "reconstructed-initial"),
        default="initial-once",
        help="Never treat a retained interval map as an initial map",
    )
    parser.add_argument("--reconstruction", type=Path, help="Verified reconstruction manifest for one budget")
    parser.add_argument(
        "--trajectory-overrides", type=Path, help="JSON mapping instance IDs to selected retry trajectories"
    )
    args = parser.parse_args()
    root = args.run.resolve()
    output = (
        args.output or root / ("map-quality-A-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    ).resolve()
    if output.exists():
        parser.error("Output must be a new directory")
    overrides = load(args.trajectory_overrides) if args.trajectory_overrides else {}
    reconstruction = load(args.reconstruction) if args.reconstruction else None
    if (args.snapshot == "reconstructed-initial") != bool(reconstruction):
        parser.error("--reconstruction is required only with --snapshot reconstructed-initial")
    if reconstruction and (
        reconstruction.get("schema_version") != 1
        or reconstruction.get("map_snapshot") != "reconstructed_initial"
        or Path(reconstruction["run"]).resolve() != root
        or reconstruction.get("budget") != args.budget
    ):
        parser.error("Reconstruction manifest run/budget/schema mismatch")
    datasets, data_sources = {}, []
    for path in sorted(root.rglob("dataset.json")):
        if "evaluation" not in path.parts:
            continue
        data_sources.append({"path": str(path), "sha256": digest(path.read_bytes())})
        for row in load(path):
            iid = row["instance_id"]
            if iid in datasets and (datasets[iid]["base_commit"], datasets[iid]["patch"]) != (
                row["base_commit"],
                row["patch"],
            ):
                parser.error(f"Conflicting official references for {iid}")
            datasets[iid] = row
    if not datasets:
        parser.error("No local official evaluation dataset.json; finish/export evaluation references first")
    labels = load(args.labels) if args.labels else {"schema_version": 1, "instances": {}}
    if labels.get("schema_version") != 1:
        parser.error("Expected annotation schema_version=1")
    templates, rows, references, maps = {}, [], {}, {}
    for entry in load(root / "plan.json"):
        if entry["variant"] != "repomap":
            continue
        if args.budget is not None and entry.get("budget", args.budget) != args.budget:
            continue
        iid = entry["item"]["instance_id"]
        trajectory = Path(overrides.get(iid, str(Path(entry["output"]) / f"{iid}.traj.json")))
        if not trajectory.is_file():
            parser.error(f"Missing completed trajectory: {iid}")
        data = load(trajectory)
        info = data["info"]
        settings = info["config"]["agent"]["repomap"]
        if args.budget is not None and settings["map_tokens"] != args.budget:
            continue
        if settings["map_tokens"] <= 0:
            parser.error(f"{iid}: positive map budget required")
        if args.snapshot == "initial-once" and settings.get("refresh", "interval") != "once":
            parser.error(f"{iid}: initial once map required; a retained interval map is not an initial-map snapshot")
        if info.get("exit_status") not in {"Submitted", "LimitsExceeded", "TimeExceeded", "RepeatedFormatError"}:
            parser.error(f"Incomplete inference: {iid}")
        if iid not in datasets or datasets[iid]["base_commit"] != entry["item"]["base_commit"]:
            parser.error(f"Official base commit mismatch or missing reference: {iid}")
        messages = data["messages"]
        map_messages = [m for m in messages if get_content_string(m).startswith("<repository_map>")]
        if len(map_messages) > 1:
            parser.error(f"Multiple retained maps: {iid}")
        text = get_content_string(map_messages[0]) if map_messages else ""
        provenance = None
        if reconstruction:
            provenance = reconstruction.get("instances", {}).get(iid)
            if not provenance or (
                provenance["base_commit"] != entry["item"]["base_commit"]
                or provenance["trajectory_sha256"] != digest(trajectory.read_bytes())
            ):
                parser.error(f"Missing or mismatched reconstruction provenance: {iid}")
            map_path = (args.reconstruction.resolve().parent / provenance["map_path"]).resolve()
            text = map_path.read_text()
            if digest(text.encode()) != provenance["map_sha256"]:
                parser.error(f"Reconstructed map hash mismatch: {iid}")
        reference = datasets[iid]
        files = gold_files(reference["patch"])
        sha = digest(text.encode())
        annotation = labels.get("instances", {}).get(iid, {})
        if args.snapshot != "initial-once" and annotation.get("utilization", {}).get("reviewed"):
            parser.error(
                f"{iid}: interval utilization needs per-request maps; one snapshot cannot establish availability"
            )
        if annotation and (
            annotation.get("map_sha256") != sha or annotation.get("reference_base_commit") != reference["base_commit"]
        ):
            parser.error(f"Annotation map hash/base commit mismatch: {iid}")
        complete = bool(messages and messages[-1].get("role") == "exit")
        normalized = [
            {
                "role": m.get("role"),
                "content": get_content_string(m),
                "extra": {"actions": m.get("extra", {}).get("actions", [])},
            }
            for m in messages
        ]
        row = {
            "instance_id": iid,
            "repo": entry["item"]["repo"],
            "map_snapshot": args.snapshot.replace("-", "_"),
            "map_budget": settings["map_tokens"],
            "reconstruction_provenance": provenance,
            "snapshot_limitations": (
                "Retained final map may reflect Agent edits and late context; not initial quality or earlier availability."
                if args.snapshot == "retained-final"
                else "Reconstructed from original code, image and first context; not a historical request capture."
                if reconstruction
                else None
            ),
            "reference_source": "official_patch",
            "reference_base_commit": reference["base_commit"],
            "reference_patch_sha256": digest(reference["patch"].encode()),
            "map_sha256": sha,
            "trajectory": str(trajectory),
            "trajectory_sha256": digest(trajectory.read_bytes()),
            "map_missing": not bool(text),
            **evaluate(text, files, annotation, normalized, complete),
        }
        if any(r["instance_id"] == iid for r in rows):
            parser.error("One map condition per output is required; evaluate each budget separately")
        rows.append(row)
        references[iid] = {
            "base_commit": reference["base_commit"],
            "official_patch_files": files,
            "official_patch": reference["patch"],
            "task": reference["problem_statement"],
        }
        maps[iid] = text
        templates[iid] = {
            "reference_base_commit": reference["base_commit"],
            "map_sha256": sha,
            "reviewers": [],
            "additional_target_files": {"reviewed": False, "items": []},
            "target_symbols": {"reviewed": False, "items": []},
            "dependencies": {"reviewed": False, "items": []},
            "map_symbols": {"inventory_complete": False, "relevance_reviewed": False, "items": []},
            "utilization": {"reviewed": False, "status": "unknown", "events": []},
        }
    if not rows:
        parser.error("No repomap instances found")
    unknown = set(labels.get("instances", {})) - {r["instance_id"] for r in rows}
    if unknown:
        parser.error(f"Annotation contains unknown instances: {sorted(unknown)}")
    aggregate = summarize(rows)
    report = {
        "schema_version": 1,
        "experiment": "A",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run": str(root),
        "post_hoc_only": True,
        "model_calls": 0,
        "dataset_sources": data_sources,
        "labels_source": str(args.labels.resolve()) if args.labels else None,
        "labels_sha256": digest(args.labels.read_bytes()) if args.labels else None,
        "map_snapshot": args.snapshot.replace("-", "_"),
        "budget_filter": args.budget,
        "trajectory_overrides_source": str(args.trajectory_overrides.resolve()) if args.trajectory_overrides else None,
        "trajectory_overrides_sha256": digest(args.trajectory_overrides.read_bytes())
        if args.trajectory_overrides
        else None,
        "reconstruction_source": str(args.reconstruction.resolve()) if reconstruction else None,
        "reconstruction_sha256": digest(args.reconstruction.read_bytes()) if reconstruction else None,
        "aggregate": aggregate,
        "instances": rows,
    }
    output.mkdir(parents=True)

    def write_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    write_json("report.json", report)
    write_json("annotation-template.json", {"schema_version": 1, "instances": templates})
    for iid in references:
        directory = output / "references"
        directory.mkdir(exist_ok=True)
        (directory / f"{iid}.json").write_text(json.dumps(references[iid], ensure_ascii=False, indent=2) + "\n")
        directory = output / "maps"
        directory.mkdir(exist_ok=True)
        (directory / f"{iid}.txt").write_text(maps[iid])
    stream = io.StringIO()
    columns = ["instance_id", "repo", "map_snapshot", "map_budget", *METRICS, "map_utilization_status", "map_missing"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "map_snapshot": row["map_snapshot"],
                "map_budget": row["map_budget"],
                **{key: row[key]["value"] for key in METRICS},
                "map_utilization_status": row["map_utilization"]["status"],
                "map_missing": row["map_missing"],
            }
        )
    (output / "results.csv").write_text(stream.getvalue())
    lines = [
        "# 实验 A：地图质量评测",
        "",
        f"任务数：{len(rows)}。地图口径：`{args.snapshot}`。读取地图、轨迹和官方补丁；评分无模型调用。",
        "",
        (
            "最终保留地图可能包含修改后的源码和后期上下文，不能代表首轮质量或此前可用信息。"
            if args.snapshot == "retained-final"
            else "首轮地图为事后确定性重建，保留代码、镜像、上下文与哈希依据；不是历史请求抓包。"
            if reconstruction
            else "使用轨迹中实际保存且保持不变的once地图。"
        ),
        "",
        "符号、依赖和利用行为需经审阅的标注；N/A表示尚未标注或分母为空，不按0或100%处理。",
        "",
        "| 指标 | 宏平均 | 已评分任务 |",
        "| --- | ---: | ---: |",
    ]
    for key in METRICS:
        result = aggregate[key]
        value = f"{result['macro']:.2%}" if result["macro"] is not None else "N/A"
        lines.append(f"| {key} | {value} | {result['scored_instances']}/{len(rows)} |")
    lines += [
        "| map_utilization | 见证据状态，非心智使用率 | "
        + str(aggregate["map_utilization"]["reviewed_instances"])
        + "/"
        + str(len(rows))
        + " |",
        "",
        "基线既存目标文件召回：宏平均 "
        + (
            f"{aggregate['existing_target_file_recall']['macro']:.2%}"
            if aggregate["existing_target_file_recall"]["macro"] is not None
            else "N/A"
        )
        + "；微平均 "
        + str(aggregate["existing_target_file_recall"]["numerator"])
        + "/"
        + str(aggregate["existing_target_file_recall"]["denominator"])
        + "。",
        "",
        "| 仓库 | 官方修改文件命中/总数 | 文件召回率 | 未命中文件 |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in rows:
        metric = row["target_file_recall"]
        value = f"{metric['value']:.2%}" if metric["value"] is not None else "N/A"
        lines.append(
            f"| {row['repo']} | {metric['numerator']}/{metric['denominator']} | {value} | {', '.join(metric['missed_files'])} |"
        )
    lines += [
        "",
        "文件召回以官方patch为参考，未使用Agent自己提交的patch作为真值。新增文件保留在总分母，另报既存文件召回；重命名按基线旧路径匹配。",
        "",
        "[完整结果](report.json) · [逐任务CSV](results.csv) · [标注模板](annotation-template.json)",
        "",
    ]
    (output / "summary.md").write_text("\n".join(lines))
    print(
        json.dumps(
            {
                "output": str(output),
                "instances": len(rows),
                "target_file_recall": aggregate["target_file_recall"],
                "model_calls": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
