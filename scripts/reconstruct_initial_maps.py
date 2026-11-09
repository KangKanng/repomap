"""Reconstruct interval first-query maps from verified historical code and local pinned images; no model calls."""

import argparse
import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

MODULES = ("agent.py", "settings.py", "source.py", "repomap.py", "symbols.py", "_collect.py", "__init__.py")
COLLECT = r"""
import json, runpy, subprocess, sys
from pathlib import Path
o = json.loads(sys.argv[1])
root = Path('/testbed')
def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=/testbed', *args], cwd=root).decode().strip()
data = {
    'head_tree': git('rev-parse', 'HEAD^{tree}'),
    'base_tree': git('rev-parse', o.pop('base_commit') + '^{tree}'),
    'network_interfaces': sorted(p.name for p in Path('/sys/class/net').iterdir()),
}
data['snapshot'] = runpy.run_path('/opt/map-collect.py')['collect'](o)
print(json.dumps(data))
"""


def load(path):
    return json.loads(path.read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed budget sweep with source_hashes and immutable image IDs")
    parser.add_argument(
        "--historical-package", type=Path, required=True, help="Original mini_swe_repomap package directory"
    )
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    root, output = args.run.resolve(), args.output.resolve()
    if output.exists():
        parser.error("Output must be a new directory")
    historical = args.historical_package.resolve()
    experiment = load(root / "experiment.json")
    expected = experiment["source_hashes"]
    verified = {}
    for module in MODULES:
        path = historical / module
        actual = sha(path.read_bytes())
        if expected.get("src/mini_swe_repomap/" + module) != actual:
            parser.error(f"Historical source hash mismatch: {module}")
        verified[module] = actual
    # Import only the verified generator and context logic; never instantiate an API model.
    sys.path.insert(0, str(historical.parent))
    from minisweagent.models.utils.content_string import get_content_string

    from mini_swe_repomap.agent import RepoMapMixin
    from mini_swe_repomap.source import MARKER
    from mini_swe_repomap.symbols import LANGUAGES

    class SnapshotEnvironment:
        def __init__(self, snapshot):
            self.snapshot = snapshot

        def execute(self, action):
            return {"returncode": 0, "output": MARKER + json.dumps(self.snapshot)}

    class CaptureBase:
        def __init__(self, settings, snapshot, initial, task):
            self.config = SimpleNamespace(repomap=SimpleNamespace(**settings))
            self.env = SnapshotEnvironment(snapshot)
            self.model = SimpleNamespace(format_message=lambda **kwargs: kwargs)
            self.messages = list(initial)
            self.extra_template_vars = {"task": task}
            self.n_calls = 0
            self.logger = logging.getLogger("map-reconstruction")

        def query(self):
            return self.messages

    class Probe(RepoMapMixin, CaptureBase):
        pass

    datasets = {}
    for path in root.rglob("dataset.json"):
        if "evaluation" in path.parts:
            for row in load(path):
                if row["instance_id"] in datasets and datasets[row["instance_id"]] != row:
                    parser.error(f"Conflicting official rows: {row['instance_id']}")
                datasets[row["instance_id"]] = row
    grouped = {}
    for entry in load(root / "plan.json"):
        if entry["variant"] == "repomap":
            grouped.setdefault(entry["item"]["instance_id"], []).append(entry)
    output.mkdir(parents=True)
    frozen = output / "code" / "mini_swe_repomap"
    frozen.mkdir(parents=True)
    for module in MODULES:
        shutil.copyfile(historical / module, frozen / module)
    # Preserve the exact modules imported by this process as the reviewable reconstruction input.
    if Path(sys.modules["mini_swe_repomap.agent"].__file__).resolve() != historical / "agent.py":
        raise RuntimeError("Historical package import was shadowed")

    def reconstruct(entries):
        item = entries[0]["item"]
        iid = item["instance_id"]
        records = []
        shared = None
        snapshot = None
        directory = output / iid
        directory.mkdir()
        for entry in entries:
            trajectory = Path(entry["output"]) / f"{iid}.traj.json"
            data = load(trajectory)
            settings = data["info"]["config"]["agent"]["repomap"]
            environment = data["info"]["config"]["environment"]
            image = environment["image"]
            if not image.startswith("sha256:") or image != item["image"]:
                raise ValueError(f"Immutable image mismatch: {iid}")
            if settings.get("refresh", "interval") != "interval":
                raise ValueError(f"Not an interval run: {iid}")
            initial = []
            for message in data["messages"]:
                if message.get("role") == "assistant":
                    break
                if not get_content_string(message).startswith("<repository_map>"):
                    initial.append(message)
            if [m["role"] for m in initial] != ["system", "user"]:
                raise ValueError(f"Unexpected pre-query history: {iid}")
            reference = datasets[iid]
            if reference["base_commit"] != item["base_commit"]:
                raise ValueError(f"Base commit mismatch: {iid}")
            # Only the public task is used for generation. The official patch is never passed to Probe.
            task = reference["problem_statement"]
            context = task
            if settings["context_messages"]:
                context += "\n" + get_content_string(initial[1])[-24000:]
            options = {
                "root": "/testbed",
                "base_commit": item["base_commit"],
                "suffixes": list(LANGUAGES),
                **{key: settings[key] for key in ("max_files", "max_file_bytes", "max_total_bytes")},
            }
            identity = (image, options, context, settings["tokenizer"])
            if shared is not None and shared != identity:
                raise ValueError(f"Budgets differ in more than map size: {iid}")
            if snapshot is None:
                mounts = environment["run_args"]
                runtime = next(
                    Path(arg.split("src=")[1].split(",")[0]) if "src=" in arg else Path(arg.split(":")[0])
                    for arg in mounts
                    if "/opt/repomap-python" in arg
                )
                command = [
                    "docker",
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--memory=2g",
                    "--pids-limit=128",
                    "--workdir=/testbed",
                    "--env",
                    "GIT_OPTIONAL_LOCKS=0",
                    "--volume",
                    f"{runtime}:/opt/repomap-python:ro",
                    "--volume",
                    f"{frozen / '_collect.py'}:/opt/map-collect.py:ro",
                    "--entrypoint",
                    "/opt/repomap-python/bin/python3",
                    "--name",
                    "map-quality-reconstruct-" + uuid.uuid4().hex[:12],
                    image,
                    "-c",
                    COLLECT,
                    json.dumps(options),
                ]
                try:
                    process = subprocess.run(command, capture_output=True, check=True, timeout=180)
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f"Collector failed for {iid}: {error.stderr.decode()[-1500:]}") from error
                collected = json.loads(process.stdout)
                if collected["head_tree"] != collected["base_tree"] or collected["network_interfaces"] != ["lo"]:
                    raise ValueError(f"Base tree or isolation mismatch: {iid}")
                snapshot = collected.pop("snapshot")
                raw_snapshot = json.dumps(snapshot, ensure_ascii=False).encode()
                with gzip.open(directory / "sources.json.gz", "wb") as stream:
                    stream.write(raw_snapshot)
                (directory / "initial-context.txt").write_text(context)
                shared = identity
            probe = Probe(settings, snapshot, initial, task)
            messages = probe.query()
            maps = [get_content_string(m) for m in messages if get_content_string(m).startswith("<repository_map>")]
            if len(maps) != 1:
                raise ValueError(f"Expected one reconstructed map: {iid}")
            text = maps[0]
            map_path = directory / f"{settings['map_tokens']}.txt"
            map_path.write_text(text)
            records.append(
                (
                    settings["map_tokens"],
                    iid,
                    {
                        "base_commit": item["base_commit"],
                        "map_path": str(map_path.relative_to(output)),
                        "map_sha256": sha(text.encode()),
                        "tokens": probe.repo_map.token_count(text),
                        "trajectory": str(trajectory),
                        "trajectory_sha256": sha(trajectory.read_bytes()),
                        "image": image,
                        "context_sha256": sha(context.encode()),
                        "context_path": str((directory / "initial-context.txt").relative_to(output)),
                        "source_snapshot": str((directory / "sources.json.gz").relative_to(output)),
                        "source_snapshot_sha256": sha(raw_snapshot),
                        "source_fingerprint": snapshot["fingerprint"],
                        "scan_options": options,
                        "scan_warnings": snapshot["warnings"],
                        **collected,
                        "historical_request_capture": False,
                    },
                )
            )
        print(f"{iid}: reconstructed {len(records)} budgets, no model calls", flush=True)
        return records

    manifests = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for records in pool.map(reconstruct, grouped.values()):
            for budget, iid, record in records:
                manifests.setdefault(budget, {})[iid] = record
    for budget, instances in sorted(manifests.items()):
        manifest = {
            "schema_version": 1,
            "run": str(root),
            "map_snapshot": "reconstructed_initial",
            "budget": budget,
            "source_hashes": verified,
            "historical_package": str(frozen),
            "model_calls": 0,
            "context_policy": "Original verified RepoMapMixin.query, n_calls=0, raw task plus first user message tail",
            "instances": instances,
        }
        (output / f"budget-{budget}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "maps": sum(map(len, manifests.values())), "model_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
