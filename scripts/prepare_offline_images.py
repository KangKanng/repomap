"""Warm Gson/Tokio dependency caches, then verify the derived images without network."""

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pull_docker import BASE

REPAIRS = {
    "google/gson": (
        "gson",
        "mini-swe-repomap/gson-1014:offline-v1",
        "mvnd test -o -B -T 1 -pl gson -Dtest=com.google.gson.stream.JsonReaderPathTest",
    ),
    "tokio-rs/tokio": (
        "tokio",
        "mini-swe-repomap/tokio-4384:offline-v1",
        "RUSTFLAGS=-Awarnings cargo test --offline --locked --package tokio --features full --lib",
    ),
}


def prepare(item: dict) -> dict:
    slug, tag, verification = REPAIRS[item["repo"]]
    log_path = BASE / "logs" / f"offline-{slug}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    base = json.loads(subprocess.check_output(["docker", "image", "inspect", item["image"]]))[0]
    base_reference = next(iter(base.get("RepoDigests") or []), item["image"])
    print(f"Preparing {item['repo']} dependency cache: {tag}", flush=True)
    with tempfile.TemporaryDirectory(prefix="repomap-image-") as context, log_path.open("w") as log:
        subprocess.run(
            [
                "docker",
                "build",
                "--network=default",
                "--pull=false",
                "--build-arg",
                f"BASE_IMAGE={base_reference}",
                "--tag",
                tag,
                "--file",
                str(BASE / "offline" / f"{slug}.Dockerfile"),
                context,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=1800,
        )
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
        verify_command = f"cd /testbed && {verification} && git diff --exit-code"
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--env",
                "BASH_ENV=/dev/null",
                "--entrypoint",
                "/bin/sh",
                image["Id"],
                "-c",
                verify_command,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=900,
        )
        tree = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--entrypoint",
                "/bin/sh",
                image["Id"],
                "-c",
                'cd /testbed && git rev-parse HEAD^{tree} && git rev-parse "$1^{tree}"',
                "verify",
                item["base_commit"],
            ],
            text=True,
        ).splitlines()
        if len(tree) != 2 or tree[0] != tree[1]:
            raise RuntimeError(f"Source tree differs from the dataset: {item['repo']}")
    print(f"Verified offline: {item['repo']}", flush=True)
    return {
        "repo": item["repo"],
        "instance_id": item["instance_id"],
        "base_image": item["image"],
        "base_image_id": base["Id"],
        "image": tag,
        "image_id": image["Id"],
        "source_tree": tree[0],
        "offline_verified": True,
        "verification_command": verification,
        "log": str(log_path),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("Workers must be positive")
    items = [item for item in json.loads((BASE / "selection.json").read_text()) if item["repo"] in REPAIRS]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(prepare, items))
    (BASE / "offline_images.json").write_text(
        json.dumps({record["instance_id"]: record["image_id"] for record in records}, indent=2) + "\n"
    )
    (BASE / "offline_image_inventory.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "images": records}, indent=2) + "\n"
    )
    print(f"Image overrides: {BASE / 'offline_images.json'}", flush=True)


if __name__ == "__main__":
    main()
