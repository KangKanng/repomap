"""Export auditable search-purpose annotation templates from existing case evidence; no model calls."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Run containing case-analysis/cases/*.json")
    parser.add_argument("--output", type=Path, required=True, help="New directory")
    args = parser.parse_args()
    root, output = args.run.resolve(), args.output.resolve()
    if output.exists():
        parser.error("Output must be a new directory")
    cases = sorted((root / "case-analysis/cases").glob("*.json"))
    if not cases:
        parser.error("No saved case evidence")
    events, templates, sources, metrics = [], {}, [], []
    for path in cases:
        case = load(path)
        iid = case["instance_id"]
        for variant, evidence in case["variants"].items():
            trajectory = Path(evidence["source_trajectory"])
            digest = sha(trajectory)
            if digest != evidence["source_sha256"]:
                parser.error(f"Case evidence is stale: {iid}/{variant}")
            sources.append(
                {
                    "instance_id": iid,
                    "variant": variant,
                    "trajectory": str(trajectory),
                    "trajectory_sha256": digest,
                    "case": str(path),
                    "case_sha256": sha(path),
                }
            )
            count, early = 0, 0
            for action in evidence["timeline"]:
                if not action["search_segments"]:
                    continue
                for index, segment in enumerate(action["search_segments"]):
                    key = f"{iid}:{variant}:{action['id']}:S{index}"
                    if key in templates:
                        parser.error(f"Duplicate event ID: {key}")
                    count += 1
                    early += action["call"] <= 5
                    events.append(
                        {
                            "event_id": key,
                            "instance_id": iid,
                            "repo": case["repo"],
                            "variant": variant,
                            "call": action["call"],
                            "action_id": action["id"],
                            "message_index": action["message_index"],
                            "action_index": action["action_index"],
                            "segment_index": index,
                            "search_kind": segment["kind"],
                            "tool": segment["tool"],
                            "search_command": segment["command"],
                            "action_command": action["command"],
                            "returncode_of_whole_action": action.get("returncode"),
                            "assistant_excerpt": action["assistant_text"],
                            "output_excerpt_of_whole_action": action.get("output_excerpt", ""),
                            "task": case["task"],
                            "trajectory_sha256": digest,
                        }
                    )
                    templates[key] = {
                        "trajectory_sha256": digest,
                        "reviewed": False,
                        "primary_purpose": "unknown",
                        "phase": "unknown",
                        "evidence": [],
                        "new_information": "unknown",
                        "duplicate_of": None,
                        "reviewer": None,
                    }
            expected = evidence["metrics"]["search_commands"]
            if count != expected or count != len(evidence["search_events"]):
                parser.error(f"Search count mismatch: {iid}/{variant}: {count} vs {expected}")
            metrics.append(
                {
                    "instance_id": iid,
                    "repo": case["repo"],
                    "variant": variant,
                    "searches": count,
                    "first_five_calls": early,
                    "later_calls": count - early,
                }
            )
    totals = Counter()
    for row in metrics:
        totals[row["variant"]] += row["searches"]
    report = {
        "schema_version": 1,
        "run": str(root),
        "unique_tasks": len(cases),
        "trace_count": len(sources),
        "search_events": len(events),
        "totals": dict(totals),
        "reviewed_events": 0,
        "model_calls": 0,
        "annotation_status": "pending_review",
        "instances": metrics,
        "sources": sources,
        "limitations": [
            "Events are explicit search requests; an action return code does not prove a particular branch ran.",
            "Assistant/output excerpts may be truncated; consult the original trajectory before final labels.",
            "Purpose, new information and phases are deliberately not inferred from command names alone.",
            "Variant is visible in these evidence files; blind it in a separate reviewer copy if required.",
        ],
    }
    schema = {
        "primary_purpose": {
            "localization": "寻找任务相关文件或声明的位置",
            "understanding": "理解已定位代码的调用关系、类型、数据流或行为",
            "validation": "查测试、编译/运行输出或补丁来验证候选修改",
            "reference_environment": "查安装副本、依赖、历史实现、缓存或构建环境",
            "maintenance": "清理、删除或其他以维护为目的的命令",
            "unknown": "证据不足、多目的未能确定主目的或不在以上分类",
        },
        "phase": ["before_relevant_read", "after_relevant_read_before_edit", "after_edit", "unknown"],
        "new_information": ["yes", "no", "unknown"],
        "notes": [
            "按每个搜索子命令标注主目的，不能把一个批量动作只标为一类。",
            "同命令重复不等于冗余；源码、环境或验证假设变化时可能是必要复查。",
            "先独立标目的，再关联地图、最终补丁和判题；后者不能代替过程依据。",
            "证据记录真实消息/动作索引、命令和输出，不能编造模型引用。",
        ],
    }
    output.mkdir(parents=True)
    (output / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    (output / "annotation-template.json").write_text(
        json.dumps({"schema_version": 1, "events": templates}, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "annotation-schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "README.md").write_text(
        "# 搜索原因复核材料\n\n"
        f"{len(cases)}条任务、{len(sources)}条轨迹、{len(events)}个搜索子命令；统计：{dict(totals)}。\n\n"
        "已核对原轨迹哈希及逐条搜索总数。当前已标注0条，所有语义标签待复核，没有模型调用。\n\n"
        "- `events.jsonl`：每个搜索子命令的命令、动作索引、任务与已有输出摘录。\n"
        "- `annotation-template.json`：按稳定事件ID填写的标签模板。\n"
        "- `annotation-schema.json`：目的、阶段与信息增量的定义。\n"
        "- `report.json`：原始轨迹来源、哈希和逐任务计数。\n\n"
        "动作退出码不代表每个条件分支均执行；摘录不完整时必须回看原轨迹。"
        "`find ... -delete`保留在原总数中，可在新增维护分类中单列。\n"
    )
    print(
        json.dumps(
            {"output": str(output), "events": len(events), "totals": dict(totals), "model_calls": 0},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
