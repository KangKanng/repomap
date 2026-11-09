"""Pin local benchmark images and verify a shared, expanded RepoMap scan offline."""

import argparse
import json
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pull_docker import BASE, PROJECT

from mini_swe_repomap._collect import BINARY_SUFFIXES, EXCLUDED_DIRS
from mini_swe_repomap.symbols import LANGUAGES

CHECK = r"""
import json, os, runpy, subprocess, sys
from pathlib import Path
o = json.loads(sys.argv[1])
root = Path('/testbed')
def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=/testbed', *args], cwd=root)
paths = sorted(set(os.fsdecode(p) for p in git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0') if p))
files, source = [], []
for name in paths:
    p = root / name
    if any(x in o['excluded'] or x.endswith('.egg-info') for x in Path(name).parts) or p.suffix.lower() in o['binary']:
        continue
    if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
        continue
    size = p.stat().st_size
    files.append((name, size))
    if p.suffix.lower() in o['suffixes']:
        source.append((name, size))
head = git('rev-parse', 'HEAD^{tree}').decode().strip()
base = git('rev-parse', o['base_commit'] + '^{tree}').decode().strip()
dirty = [os.fsdecode(p) for p in git('diff', '--name-only', '-z', o['base_commit'], '--').split(b'\0') if p]
snapshot = runpy.run_path('/opt/budget-collect.py')['collect']({
    'root': '/testbed', 'suffixes': o['suffixes'], **o['scan_limits'],
})
data = {
    'candidate_files': len(files), 'candidate_source_files': len(source),
    'candidate_source_bytes': sum(v for _, v in source),
    'max_candidate_file_bytes': max((v for _, v in files), default=0),
    'largest_source_files': sorted(source, key=lambda r: r[1], reverse=True)[:3],
    'head_tree': head, 'base_tree': base, 'baseline_dirty_files': dirty,
    'network_interfaces': sorted(p.name for p in Path('/sys/class/net').iterdir()),
    'snapshot_files': len(snapshot['files']),
    'snapshot_source_files': sum(Path(f['path']).suffix.lower() in o['suffixes'] for f in snapshot['files']),
    'snapshot_nonempty_source_files': sum(bool(f['source']) for f in snapshot['files']),
    'snapshot_source_bytes': sum(len(f['source'].encode()) for f in snapshot['files']),
    'fingerprint': snapshot['fingerprint'], 'warnings': snapshot['warnings'],
    'python': sys.version.split()[0],
}
print(json.dumps(data))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path, default=BASE / "selection.json")
    parser.add_argument("--image-overrides", type=Path, default=BASE / "offline_images.json")
    parser.add_argument("--collector-runtime", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = args.collector_runtime.resolve()
    if not (runtime / "bin/python3").is_file():
        parser.error("Collector runtime has no bin/python3")
    limits = {"max_files": 20000, "max_file_bytes": 1073741824, "max_total_bytes": 134217728}
    items = json.loads(args.selection.read_text())
    overrides = json.loads(args.image_overrides.read_text())
    order = {item["instance_id"]: i for i, item in enumerate(items)}

    def check(item):
        image_ref = overrides.get(item["instance_id"], item["image"])
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", image_ref], text=True, timeout=30))[
            0
        ]["Id"]
        options = {
            "base_commit": item["base_commit"],
            "excluded": sorted(EXCLUDED_DIRS),
            "binary": sorted(BINARY_SUFFIXES),
            "suffixes": sorted(LANGUAGES),
            "scan_limits": limits,
        }
        name = "repomap-budget-check-" + uuid.uuid4().hex[:10]
        command = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--memory=1g",
            "--pids-limit=128",
            "--env",
            "GIT_OPTIONAL_LOCKS=0",
            "--volume",
            f"{runtime}:/opt/repomap-python:ro",
            "--volume",
            f"{PROJECT}/src/mini_swe_repomap/_collect.py:/opt/budget-collect.py:ro",
            "--entrypoint",
            "/opt/repomap-python/bin/python3",
            "--name",
            name,
            image,
            "-c",
            CHECK,
            json.dumps(options),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=True)
        metadata = json.loads(result.stdout)
        if metadata["head_tree"] != metadata["base_tree"] or metadata["network_interfaces"] != ["lo"]:
            raise RuntimeError(f"Base tree or network mismatch for {item['repo']}")
        capped = [
            w for w in metadata["warnings"] if any(k in w for k in ("max_files", "max_file_bytes", "max_total_bytes"))
        ]
        if capped:
            raise RuntimeError(f"Expanded scan still capped for {item['repo']}: {capped}")
        return item | {"original_image": item["image"], "image": image, "scan_metadata": metadata}

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check, item): item for item in items}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            meta = record["scan_metadata"]
            print(
                record["repo"],
                "files",
                meta["snapshot_files"],
                "source_bytes",
                meta["snapshot_source_bytes"],
                "baseline_dirty_files",
                len(meta["baseline_dirty_files"]),
                flush=True,
            )
    records.sort(key=lambda item: order[item["instance_id"]])
    (output / "selection-pinned.json").write_text(
        json.dumps([{k: v for k, v in item.items() if k != "scan_metadata"} for item in records], indent=2) + "\n"
    )
    (output / "scan-coverage.json").write_text(
        json.dumps(
            {
                "verified": True,
                "network_mode": "none",
                "scan_limits": limits,
                "instances": records,
                "note": "Baseline dirty files are baked into the pinned official image and identical across budgets.",
            },
            indent=2,
        )
        + "\n"
    )
    config = ["agent:", "  repomap:", "    refresh_interval: 1"]
    config += [f"    {key}: {value}" for key, value in limits.items()]
    (output / "scan-config.yaml").write_text("\n".join(config) + "\n")
    print("Prepared budget sweep:", output, flush=True)


if __name__ == "__main__":
    main()
