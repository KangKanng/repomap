"""Grade completed SWE-bench predictions with the official harness in offline containers."""

import argparse
import importlib.metadata
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed scripts/run_swebench.py output directory")
    parser.add_argument("--variant", choices=["repomap", "baseline", "plain"], default="repomap")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=900, help="Test timeout per instance, in seconds")
    parser.add_argument("--instances", nargs="+", help="Only grade these instance IDs")
    parser.add_argument("--image-overrides", type=Path, help="Prepared image mapping for evaluation-only retries")
    parser.add_argument("--output", type=Path, help="New evaluation directory (required when overriding images)")
    parser.add_argument(
        "--completed-only", action="store_true", help="Grade finished instances while inference continues"
    )
    args = parser.parse_args()
    if args.workers < 1 or args.timeout < 1:
        parser.error("Workers and timeout must be positive")
    if args.image_overrides and args.output is None:
        parser.error("Use a new --output directory when changing evaluation images")

    # Keep the harness optional: install swebench in a separate evaluation environment.
    from datasets import load_dataset
    from docker.models.containers import ContainerCollection
    from swebench.harness.run_evaluation import main as evaluate

    root = args.run.resolve()
    entries = [entry for entry in json.loads((root / "plan.json").read_text()) if entry["variant"] == args.variant]
    if not entries:
        parser.error(f"No {args.variant} instances in {root}")
    overrides = json.loads(args.image_overrides.read_text()) if args.image_overrides else {}
    if args.instances:
        unknown = set(args.instances) - {entry["item"]["instance_id"] for entry in entries}
        if unknown:
            parser.error(f"Unknown instances: {', '.join(sorted(unknown))}")
        entries = [entry for entry in entries if entry["item"]["instance_id"] in args.instances]
    completed = {
        row["instance_id"]
        for row in json.loads((root / "summary.json").read_text())["runs"]
        if row["variant"] == args.variant
    }
    if args.completed_only:
        entries = [entry for entry in entries if entry["item"]["instance_id"] in completed]
        if not entries:
            parser.error("No completed instances to grade")
    elif missing := {entry["item"]["instance_id"] for entry in entries} - completed:
        parser.error(f"Inference has not finished for: {', '.join(sorted(missing))}")

    output = args.output.resolve() if args.output else root / "evaluation" / args.variant
    if args.output and output.exists() and any(output.iterdir()):
        parser.error(f"Evaluation output must be a new directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    instances = []
    for dataset_name in dict.fromkeys(entry["item"]["dataset"] for entry in entries):
        selected = {
            entry["item"]["instance_id"]: entry["item"] for entry in entries if entry["item"]["dataset"] == dataset_name
        }
        found = set()
        for row in load_dataset(dataset_name, split="test"):
            if row["instance_id"] not in selected:
                continue
            item = selected[row["instance_id"]]
            if row["base_commit"] != item["base_commit"]:
                raise ValueError(f"Dataset base commit changed for {row['instance_id']}")
            # Use exactly the inference image, including a dependency-warmed image.
            row["image"] = overrides.get(row["instance_id"], item["image"])
            instances.append(row)
            found.add(row["instance_id"])
        if found != set(selected):
            raise ValueError(f"Dataset instances missing: {sorted(set(selected) - found)}")

    predictions = []
    for entry in entries:
        instance_id = entry["item"]["instance_id"]
        trajectory = Path(entry["output"]) / f"{instance_id}.traj.json"
        submission = (
            json.loads(trajectory.read_text()).get("info", {}).get("submission", "") if trajectory.exists() else ""
        )
        predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": f"{args.variant}-offline",
                "model_patch": submission or "",
            }
        )

    dataset_path = output / "dataset.json"
    predictions_path = output / "predictions.json"
    dataset_path.write_text(json.dumps(instances, indent=2) + "\n")
    predictions_path.write_text(json.dumps(predictions, indent=2) + "\n")
    (output / "config.json").write_text(
        json.dumps(
            {
                "swebench_version": importlib.metadata.version("swebench"),
                "variant": args.variant,
                "network_mode": "none",
                "workers": args.workers,
                "timeout": args.timeout,
                "instance_ids": [row["instance_id"] for row in instances],
                "images": {row["instance_id"]: row["image"] for row in instances},
                "inference_run": str(root),
                "image_overrides": str(args.image_overrides.resolve()) if args.image_overrides else None,
            },
            indent=2,
        )
        + "\n"
    )

    original_create = ContainerCollection.create
    audit_lock = threading.Lock()

    def create_offline(collection, *create_args, **kwargs):
        # Enforce isolation at container creation, before the harness starts any process.
        kwargs.pop("network", None)
        kwargs["network_mode"] = "none"
        container = original_create(collection, *create_args, **kwargs)
        container.reload()
        network = container.attrs["HostConfig"]["NetworkMode"]
        if network != "none":
            container.remove(force=True)
            raise RuntimeError("Evaluation container was not created with network_mode=none")
        with audit_lock, (output / "network-audit.jsonl").open("a") as audit:
            audit.write(
                json.dumps(
                    {"container": container.name, "image": container.attrs["Config"]["Image"], "network_mode": network}
                )
                + "\n"
            )
        return container

    previous_cwd = Path.cwd()
    try:
        os.chdir(output)
        with patch.object(ContainerCollection, "create", create_offline):
            report = evaluate(
                dataset_name=str(dataset_path),
                split="test",
                instance_ids=[row["instance_id"] for row in instances],
                predictions_path=str(predictions_path),
                max_workers=args.workers,
                open_file_limit=4096,
                run_id=f"{root.name}-{args.variant}" + (f"-{output.name}" if args.output else ""),
                timeout=args.timeout,
                rewrite_reports=False,
                modal=False,
                report_dir=str(output),
            )
        print(f"Official offline evaluation report: {report}", flush=True)
    finally:
        os.chdir(previous_cwd)


if __name__ == "__main__":
    main()
