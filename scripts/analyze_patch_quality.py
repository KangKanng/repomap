"""Audit saved model patches offline; size and reference overlap are proxies, not quality scores."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPEATS = ROOT / "benchmarks/results/once-interval-repeats-20261105T180350Z"
FULL = ROOT / "benchmarks/results/full-once-20-20261107T093757Z"
SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rb",
    ".php",
}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
METRICS = ("source_churn", "source_files", "source_hunks", "extra_source_files", "missing_reference_source_files")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_kind(path: str) -> str:
    parts = Path(path).parts
    name = Path(path).name.lower()
    if any(p.lower() in {"test", "tests", "spec", "specs", "__tests__"} for p in parts):
        return "test"
    if name.startswith("test_") or re.search(r"(?:_test\.|\.test\.|\.spec\.|test\.java$)", name):
        return "test"
    if Path(path).suffix.lower() in SOURCE_SUFFIXES:
        return "source"
    return "other"


def parse_patch(patch: str) -> list[dict]:
    """Parse textual git diffs and validate both hunk lengths (including empty hunks)."""
    files = []
    current = None
    remaining = [0, 0]
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            if any(remaining):
                raise ValueError("Truncated hunk before file header")
            current = {"path": "", "old_path": "", "added": [], "deleted": [], "hunks": 0}
            files.append(current)
            in_hunk = False
        elif match := HUNK.match(line):
            if current is None or any(remaining):
                raise ValueError("Invalid hunk boundary")
            current["hunks"] += 1
            remaining = [int(match[2] or 1), int(match[4] or 1)]
            in_hunk = True
        elif in_hunk and any(remaining):
            if line.startswith("\\ No newline"):
                continue
            if line.startswith("+"):
                current["added"].append(line[1:])
                remaining[1] -= 1
            elif line.startswith("-"):
                current["deleted"].append(line[1:])
                remaining[0] -= 1
            elif line.startswith(" "):
                remaining = [n - 1 for n in remaining]
            else:
                raise ValueError(f"Unexpected hunk line: {line!r}")
            if min(remaining) < 0:
                raise ValueError("Hunk count underflow")
        elif current is not None and line.startswith("--- "):
            current["old_path"] = line[4:].removeprefix("a/")
        elif current is not None and line.startswith("+++ "):
            current["path"] = line[4:].removeprefix("b/")
    if any(remaining):
        raise ValueError("Truncated final hunk")
    for f in files:
        if f["path"] == "/dev/null":
            f["path"] = f["old_path"]
        if not f["path"] or '"' in f["path"]:
            raise ValueError("Only unquoted textual file paths are supported")
        f["kind"] = file_kind(f["path"])
    return files


def patch_metrics(patch: str, reference: set[str]) -> dict:
    files = parse_patch(patch)
    source = [f for f in files if f["kind"] == "source"]
    paths = {f["path"] for f in source}
    normalized = [(f["path"], f["deleted"], f["added"]) for f in sorted(source, key=lambda f: f["path"])]
    result = {
        "patch_sha256": sha(patch),
        "source_edits_sha256": sha(json.dumps(normalized, ensure_ascii=False)),
        "files": len(files),
        "added": sum(len(f["added"]) for f in files),
        "deleted": sum(len(f["deleted"]) for f in files),
        "source_files": len(source),
        "source_churn": sum(len(f["added"]) + len(f["deleted"]) for f in source),
        "source_hunks": sum(f["hunks"] for f in source),
        "test_path_files": sum(f["kind"] == "test" for f in files),
        "extra_source_files": len(paths - reference),
        "missing_reference_source_files": len(reference - paths),
        "source_paths": sorted(paths),
        "extra_source_paths": sorted(paths - reference),
        "missing_reference_source_paths": sorted(reference - paths),
    }
    # Independent validation of every nonempty saved diff. This does not apply it.
    if patch.strip():
        check = subprocess.run(
            ["git", "apply", "--numstat", "-"], input=patch, text=True, capture_output=True, check=True
        )
        counts = [line.split("\t", 2) for line in check.stdout.splitlines()]
        assert len(counts) == result["files"]
        assert sum(int(c[0]) for c in counts) == result["added"]
        assert sum(int(c[1]) for c in counts) == result["deleted"]
    return result


def load_trials(repeats: Path, full: Path, references: dict) -> list[dict]:
    trials = []
    for phase, csv_path in (("repeated", repeats / "final-repeat-trials.csv"), ("full", full / "trials.csv")):
        for row in csv.DictReader(csv_path.open()):
            trajectory = Path(row["trajectory"])
            data = json.loads(trajectory.read_text()) if trajectory.exists() else {}
            patch = data.get("info", {}).get("submission", "") or ""
            submitted = row["run_valid" if phase == "repeated" else "submitted"] == "True"
            if submitted and not patch.strip():
                raise ValueError(f"Valid run has no patch: {trajectory}")
            record = {
                "phase": row["phase"] if phase == "repeated" else "full-once",
                "repeat": int(row["repeat"]) if phase == "repeated" else 1,
                "budget": int(row["budget"]) if phase == "repeated" else -1,
                "instance_id": row["instance_id"],
                "submitted": submitted,
                "resolved": row["resolved"] == "True",
                "trajectory": str(trajectory),
                "trajectory_sha256": hashlib.sha256(trajectory.read_bytes()).hexdigest()
                if trajectory.exists()
                else None,
                "patch_present": bool(patch.strip()),
            }
            record.update(patch_metrics(patch, references[row["instance_id"]]))
            trials.append(record)
    return trials


def paired_effect(pairs: list[tuple[dict, dict]], metric: str) -> dict:
    groups = defaultdict(list)
    for a, b in pairs:
        groups[a["instance_id"]].append((a[metric], b[metric]))
    clusters = list(groups.values())
    before = statistics.mean(a[metric] for a, _ in pairs)
    after = statistics.mean(b[metric] for _, b in pairs)
    counts = [len(c) for c in clusters]
    sums = [sum(b - a for a, b in c) for c in clusters]
    rng = random.Random(20261108)
    samples = []
    for _ in range(10000):
        choices = rng.choices(range(len(clusters)), k=len(clusters))
        samples.append(sum(sums[i] for i in choices) / sum(counts[i] for i in choices))
    samples.sort()
    return {
        "pairs": len(pairs),
        "task_clusters": len(clusters),
        "plain_mean": before,
        "map_mean": after,
        "delta": after - before,
        "delta_percent": (after / before - 1) * 100 if before else None,
        "exploratory_95pct_interval": [samples[249], samples[9749]],
        "smaller_equal_larger": [
            sum(b[metric] < a[metric] for a, b in pairs),
            sum(b[metric] == a[metric] for a, b in pairs),
            sum(b[metric] > a[metric] for a, b in pairs),
        ],
    }


def summarize(trials: list[dict]) -> dict:
    aggregates = []
    comparisons = []
    for phase in ("once", "interval", "full-once"):
        budgets = (0, 1024, 2048, 4096) if phase != "full-once" else (-1,)
        baseline_phase = phase if phase != "full-once" else "once"
        baseline = {
            (r["repeat"], r["instance_id"]): r for r in trials if r["phase"] == baseline_phase and r["budget"] == 0
        }
        for budget in budgets:
            selected = [r for r in trials if r["phase"] == phase and r["budget"] == budget]
            valid = [r for r in selected if r["submitted"]]
            aggregates.append(
                {
                    "phase": phase,
                    "budget": budget,
                    "planned": len(selected),
                    "submitted": len(valid),
                    "resolved": sum(r["resolved"] for r in selected),
                    **{metric: statistics.mean(r[metric] for r in valid) for metric in METRICS},
                    "test_path_patches": sum(r["test_path_files"] > 0 for r in valid),
                    "unique_source_edits": len({(r["instance_id"], r["source_edits_sha256"]) for r in valid}),
                }
            )
            if budget == 0:
                continue
            pairs = [
                (baseline[(r["repeat"], r["instance_id"])], r)
                for r in valid
                if baseline[(r["repeat"], r["instance_id"])]["submitted"]
            ]
            both_resolved = [(a, b) for a, b in pairs if a["resolved"] and b["resolved"]]
            comparisons.append(
                {
                    "phase": phase,
                    "budget": budget,
                    "same_source_edits": sum(a["source_edits_sha256"] == b["source_edits_sha256"] for a, b in pairs),
                    "metrics": {m: paired_effect(pairs, m) for m in METRICS},
                    "both_resolved_source_churn": paired_effect(both_resolved, "source_churn"),
                }
            )
    return {"aggregates": aggregates, "comparisons": comparisons}


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: json.dumps(v) if isinstance(v, list) else v for k, v in row.items()} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats-root", type=Path, default=REPEATS)
    parser.add_argument("--full-root", type=Path, default=FULL)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/results/patch-quality-20261108")
    args = parser.parse_args()
    dataset_path = args.repeats_root / "once/repeat-01/conditions/repeat-01-b0/evaluation/plain/dataset.json"
    dataset = json.loads(dataset_path.read_text())
    references = {
        r["instance_id"]: {f["path"] for f in parse_patch(r["patch"]) if f["kind"] == "source"} for r in dataset
    }
    trials = load_trials(args.repeats_root, args.full_root, references)
    result = {
        "protocol": {
            "scope": "480 repeated attempts plus 20 full-once attempts; no new inference or grading",
            "reference_dataset": str(dataset_path),
            "reference_dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "bootstrap": "10000 whole-task resamples; seed 20261108; unadjusted exploratory intervals",
            "source_churn": "Source-path additions plus deletions, including comments, whitespace and inline tests; excludes test paths",
            "source_edits_hash": "Sorted source paths and verbatim deleted/added lines; omits diff headers, coordinates and context",
            "reference_overlap": "Official source-file path overlap only; extra files are not automatically wrong or unnecessary",
            "test_caveat": "The original task prompt prohibits test modifications and submitting reproduction files",
            "limitations": "No AST complexity score, security certification, independent security task set or causal identification",
        },
        "integrity": {
            "attempts": len(trials),
            "submitted": sum(r["submitted"] for r in trials),
            "nonempty_patches_validated_with_git_numstat": sum(r["patch_present"] for r in trials),
            "invalid_attempts_with_patch": sum(not r["submitted"] and r["patch_present"] for r in trials),
        },
        **summarize(trials),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "patch-trials.csv", trials)
    tasks = []
    for iid in references:
        for phase in ("once", "interval", "full-once"):
            for budget in (0, 1024, 2048, 4096) if phase != "full-once" else (-1,):
                group = [r for r in trials if r["instance_id"] == iid and r["phase"] == phase and r["budget"] == budget]
                valid = [r for r in group if r["submitted"]]
                tasks.append(
                    {
                        "instance_id": iid,
                        "phase": phase,
                        "budget": budget,
                        "submitted": len(valid),
                        "resolved": sum(r["resolved"] for r in group),
                        **{m: statistics.mean(r[m] for r in valid) if valid else None for m in METRICS},
                    }
                )
    write_csv(args.output / "patch-tasks.csv", tasks)
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
