"""Audit blind judge evidence and aggregate swapped reviews without changing model scores."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import statistics
from collections import Counter
from pathlib import Path

from judge_patch_maintainability import DEFAULT_OUTPUT, DIMENSIONS


def interval(values: list[float]) -> list[float]:
    rng = random.Random(20261108)
    samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(10000))
    return [samples[249], samples[9749]]


def changed_file_images(patch: str) -> dict[str, str]:
    """Remove unified-diff markers to check code quotations rather than their diff representation."""
    files = {}
    for section in patch.split("diff --git ")[1:]:
        old_path = re.search(r"^--- (?:a/)?(.+)$", section, re.M)
        new_path = re.search(r"^\+\+\+ (?:b/)?(.+)$", section, re.M)
        path = new_path[1] if new_path and new_path[1] != "/dev/null" else old_path[1]
        before, after = [], []
        in_hunk = False
        for line in section.splitlines():
            if line.startswith("@@ "):
                before.append("\n<HUNK GAP>\n")
                after.append("\n<HUNK GAP>\n")
                in_hunk = True
            elif in_hunk and line.startswith(" "):
                before.append(line[1:])
                after.append(line[1:])
            elif in_hunk and line.startswith("+"):
                after.append(line[1:])
            elif in_hunk and line.startswith("-"):
                before.append(line[1:])
        files[path] = section + "\n<BEFORE>\n" + "\n".join(before) + "\n<AFTER>\n" + "\n".join(after)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.input
    manifest = json.loads((root / "manifest.json").read_text())
    cases = {c["instance_id"]: c for c in manifest["cases"]}
    results = {iid: [] for iid in cases}
    evidence = []
    response_hashes = {}
    valid_records = []
    missing = []
    schema_recoveries = []
    for item in manifest["judgments"]:
        path = root / "responses" / f"{item['instance_id']}.order-{item['order']}.json"
        if not path.exists():
            missing.append(path.name)
            continue
        raw = path.read_bytes()
        response_hashes[path.name] = hashlib.sha256(raw).hexdigest()
        record = json.loads(raw)
        if record["status"] != "ok":
            # Keep original files untouched. A finding-label violation does not
            # invalidate complete numeric ratings in an otherwise valid response.
            try:
                content = record["raw_content"]
                content = content[content.index("{") : content.rindex("}") + 1]
                structural_repairs = []
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    # Two responses omit only the closing candidates-object brace.
                    # Insert it at the unique top-level-preference boundary; never
                    # change ratings, wording, candidate assignment or preference.
                    boundary = re.search(r',\s*"pairwise_preference"\s*:', content)
                    if boundary is None:
                        raise
                    parsed = json.loads(content[: boundary.start()] + "}" + content[boundary.start() :])
                    structural_repairs.append("insert_closing_candidates_brace_before_pairwise_preference")
                assert parsed["pairwise_preference"] in ("A", "B", "tie", "insufficient_context")
                deviations = []
                for label in ("A", "B"):
                    scores = parsed["candidates"][label]["scores"]
                    assert set(scores) == set(DIMENSIONS)
                    assert all(v is None or type(v) is int and 1 <= v <= 5 for v in scores.values())
                    for finding in parsed["candidates"][label]["findings"]:
                        if finding["dimension"] not in DIMENSIONS:
                            deviations.append(f"{label}:finding_dimension:{finding['dimension']}")
                record["analysis"] = parsed
                schema_recoveries.append(
                    {
                        "response": path.name,
                        "original_status": record["status"],
                        "deviations": deviations,
                        "structural_repairs": structural_repairs,
                        "scores_modified": False,
                    }
                )
            except (AssertionError, KeyError, ValueError, TypeError):
                missing.append(path.name + ":" + record["status"])
                continue
        valid_records.append(record)
        assert record["input_sha256"] == item["input_sha256"]
        payload_bytes = (root / item["input_file"]).read_bytes()
        assert hashlib.sha256(payload_bytes).hexdigest() == item["input_sha256"]
        payload = json.loads(payload_bytes)
        decoded = {}
        for label, condition in item["labels"].items():
            candidate = record["analysis"]["candidates"][label]
            decoded[condition] = candidate["scores"]
            file_images = changed_file_images(payload["candidates"][label])
            for index, finding in enumerate(candidate["findings"]):
                code = file_images.get(finding["path"], "")
                code += "\n" + payload["baseline_context"].get(finding["path"], {}).get("excerpt", "")
                quote = finding["quote"]
                status = "exact_code_or_diff_match" if quote and quote in code else "unmatched"
                if status == "unmatched" and quote and re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", code):
                    status = "whitespace_only_difference"
                evidence.append(
                    {
                        "instance_id": item["instance_id"],
                        "order": item["order"],
                        "candidate": label,
                        "condition": condition,
                        "finding_index": index,
                        "path": finding["path"],
                        "quote": quote,
                        "validation": status,
                    }
                )
        preference = record["analysis"]["pairwise_preference"]
        results[item["instance_id"]].append(
            {
                "order": item["order"],
                "scores": decoded,
                "preference": item["labels"].get(preference, preference),
                "confidence": record["analysis"]["confidence"],
            }
        )
    scored = []
    for iid, reviews in results.items():
        if len(reviews) != 2 or any(v is None for r in reviews for s in r["scores"].values() for v in s.values()):
            continue
        conditions = {
            c: {d: statistics.mean(r["scores"][c][d] for r in reviews) for d in DIMENSIONS}
            for c in ("plain", "full-once")
        }
        means = {c: statistics.mean(s.values()) for c, s in conditions.items()}
        preferences = [r["preference"] for r in reviews]
        scored.append(
            {
                "instance_id": iid,
                "plain_mean": means["plain"],
                "full_once_mean": means["full-once"],
                "delta": means["full-once"] - means["plain"],
                "dimension_means": conditions,
                "order_1_preference": preferences[0],
                "order_2_preference": preferences[1],
                "consensus_preference": preferences[0] if preferences[0] == preferences[1] else "order_disagreement",
                "same_verbatim_patch": cases[iid]["same_verbatim_patch"],
                "same_changed_lines_and_paths": cases[iid]["same_changed_lines_and_paths"],
            }
        )
    summary = {
        "model": sorted({r["response_model"] for r in valid_records}),
        "completed_judgments": len(valid_records),
        "strictly_valid_judgments": sum(r["status"] == "ok" for r in valid_records),
        "schema_recoveries": schema_recoveries,
        "planned_judgments": len(manifest["judgments"]),
        "complete_pairs": len(scored),
        "missing_or_invalid": missing,
        "evidence_validation": dict(Counter(e["validation"] for e in evidence)),
        "confidence": dict(Counter(r["confidence"] for rs in results.values() for r in rs)),
        "pairwise_review_votes": dict(Counter(r["preference"] for rs in results.values() for r in rs)),
        "pairwise_consensus": dict(Counter(c["consensus_preference"] for c in scored)),
        "response_sha256": response_hashes,
        "cases": scored,
        "limitations": "One judge model, one historical group, 18 tasks; swapped reviews are not independent tasks; ordinal scores are descriptive",
    }
    if scored:
        summary["scores"] = {
            "plain_mean": statistics.mean(c["plain_mean"] for c in scored),
            "full_once_mean": statistics.mean(c["full_once_mean"] for c in scored),
            "mean_delta": statistics.mean(c["delta"] for c in scored),
            "exploratory_95pct_interval": interval([c["delta"] for c in scored]),
            "wins_ties_losses": [
                sum(c["delta"] > 0 for c in scored),
                sum(c["delta"] == 0 for c in scored),
                sum(c["delta"] < 0 for c in scored),
            ],
        }
        summary["dimensions"] = {
            d: {
                "plain_mean": statistics.mean(c["dimension_means"]["plain"][d] for c in scored),
                "full_once_mean": statistics.mean(c["dimension_means"]["full-once"][d] for c in scored),
                "mean_delta": statistics.mean(
                    c["dimension_means"]["full-once"][d] - c["dimension_means"]["plain"][d] for c in scored
                ),
            }
            for d in DIMENSIONS
        }
    (root / "audit-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (root / "evidence-validation.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    if scored:
        flat = [{k: v for k, v in c.items() if k != "dimension_means"} for c in scored]
        with (root / "paired-scores.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("cases", "response_sha256")}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
