"""Profile Repo Map on the selected Docker repositories without calling an LLM."""

import argparse
import csv
import json
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from pull_docker import BASE, PROJECT, repository_archive

from mini_swe_repomap._collect import BINARY_SUFFIXES, EXCLUDED_DIRS
from mini_swe_repomap.env import load_environment
from mini_swe_repomap.repomap import RepoMap
from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.symbols import LANGUAGES


def write_json(path: Path, data: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_sources(
    item: dict, settings: RepoMapConfig, log: Path, container_name: str
) -> tuple[dict[str, str], list[str]]:
    sources, warnings, total_bytes = {}, set(), 0
    image = item["repo_digests"][0] if item.get("repo_digests") else item["image"]
    root = item.get("repository", {}).get("root", "/testbed")
    with repository_archive(image, root, log, name=container_name) as archive:
        for member in archive:
            path = Path(member.name)
            if not member.isfile() or any(p in EXCLUDED_DIRS or p.endswith(".egg-info") for p in path.parts):
                continue
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            if settings.max_file_bytes is not None and member.size > settings.max_file_bytes:
                warnings.add("Some files exceed max_file_bytes and were skipped.")
                continue
            if settings.max_files is not None and len(sources) >= settings.max_files:
                warnings.add("File count reached max_files; the map is partial.")
                break
            source = ""
            if path.suffix.lower() in LANGUAGES:
                if settings.max_total_bytes is not None and total_bytes + member.size > settings.max_total_bytes:
                    warnings.add("Source size reached max_total_bytes; the map is partial.")
                    continue
                total_bytes += member.size
                with archive.extractfile(member) as stream:
                    data = stream.read()
                if b"\0" in data:
                    continue
                try:
                    source = data.decode("utf-8-sig")
                except UnicodeError:
                    warnings.add("Some non-UTF-8 source files were skipped.")
                    continue
            sources[path.as_posix()] = source
    return sources, sorted(warnings)


def run_case(spec: dict) -> dict:
    item, config = spec["item"], spec["config"]
    output = Path(spec["output"])
    settings = RepoMapConfig(**config["scan_limits"], tokenizer=config["tokenizer"])
    started = time.perf_counter()
    sources, warnings = read_sources(item, settings, Path(spec["log"]), spec["container_name"])
    collection_seconds = time.perf_counter() - started
    if not sources:
        raise RuntimeError("No files collected; check the image, repository root, and scan limits")
    maps_dir = output / "maps" / spec["slug"]
    maps_dir.mkdir(parents=True)
    measurements = []
    for budget in config["budgets"]:
        mapper = RepoMap(config["tokenizer"])
        started = time.perf_counter()
        text = mapper.build(sources, config["task"], budget)
        cold_seconds = time.perf_counter() - started
        tokens = mapper.token_count(text)
        if tokens > budget:
            raise RuntimeError(f"Map exceeded budget: {tokens} > {budget}")
        warm_seconds = []
        for _ in range(config["repeats"]):
            started = time.perf_counter()
            warm = mapper.build(sources, config["task"], budget)
            warm_seconds.append(time.perf_counter() - started)
            if warm != text:
                raise RuntimeError("Map changed for an identical snapshot and task")
        map_path = maps_dir / f"{budget}.txt"
        map_path.write_text(text, encoding="utf-8")
        measurements.append(
            {
                "budget": budget,
                "map_tokens": tokens,
                "cold_build_seconds": cold_seconds,
                "warm_build_seconds": warm_seconds,
                "warm_build_median_seconds": statistics.median(warm_seconds),
                "parsed_definitions": sum(len(cached[1].symbols) for cached in mapper._cache.values()),
                "rendered_files": sum(line.endswith(":") and line[:-1] in sources for line in text.splitlines()),
                "rendered_lines": sum(line.startswith("│") for line in text.splitlines()),
                "map_path": str(map_path.relative_to(output)),
            }
        )
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "repo": item["repo"],
        "instance_id": item["instance_id"],
        "image": item["image"],
        "image_id": item.get("image_id"),
        "repo_digests": item.get("repo_digests", []),
        "status": "ok",
        "source_mode": "docker_tracked_worktree_stream",
        "collection_seconds": collection_seconds,
        "snapshot_files": len(sources),
        "snapshot_source_files": sum(Path(path).suffix.lower() in LANGUAGES for path in sources),
        "snapshot_source_bytes": sum(len(source.encode("utf-8")) for source in sources.values()),
        "repository_source_lines": item.get("repository", {}).get("source_lines"),
        "peak_rss_mib": peak_rss / (1024 if sys.platform != "darwin" else 1024**2),
        "warnings": warnings,
        "measurements": measurements,
    }


def write_report(output: Path, config: dict, cases: list[dict]):
    successful = [case for case in cases if case["status"] == "ok"]
    write_json(
        output / "report.json",
        {
            "config": config,
            "completed": len(cases),
            "successful": len(successful),
            "failed": len(cases) - len(successful),
            "cases": cases,
        },
    )
    columns = [
        "repo",
        "status",
        "budget",
        "map_tokens",
        "snapshot_files",
        "snapshot_source_files",
        "snapshot_source_bytes",
        "collection_seconds",
        "cold_build_seconds",
        "warm_build_median_seconds",
        "parsed_definitions",
        "rendered_files",
        "rendered_lines",
        "peak_rss_mib",
        "error",
    ]
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for case in cases:
            for measurement in case.get("measurements", [{}]):
                writer.writerow(case | measurement)
    lines = [
        "# Repo Map 性能实验\n",
        f"完成 {len(cases)} / {config['case_count']} 个仓库，成功 {len(successful)}，失败 {len(cases) - len(successful)}。\n",
        "从只读容器读取 tracked 工作区，在宿主机生成地图；不调用模型。",
        "冷启动列指空符号缓存的 build，热缓存列指相同快照重新排序和渲染的中位耗时；均不包含源码传输。",
        "扫描限额触发情况记录在 report.json 的 warnings 中。\n",
        "| 仓库 | 预算 | 实际 tokens | 地图文件数 | 空缓存生成 s | 热缓存生成 s |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in successful:
        for measurement in case["measurements"]:
            lines.append(
                f"| {case['repo']} | {measurement['budget']} | {measurement['map_tokens']} | "
                f"{measurement['rendered_files']} | {measurement['cold_build_seconds']:.3f} | "
                f"{measurement['warm_build_median_seconds']:.3f} |"
            )
    for case in cases:
        if case["status"] != "ok":
            lines.append(f"\n失败：`{case['repo']}`：{case['error']}")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=BASE / "inventory.json")
    parser.add_argument("--repos", nargs="+", help="Only run these owner/repository names")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--budgets", type=int, nargs="+", default=[1024, 2048, 4096])
    parser.add_argument("--repeats", type=int, default=3, help="Warm builds per budget")
    parser.add_argument("--timeout", type=int, default=300, help="Maximum seconds per repository")
    parser.add_argument("--task", default="Understand the public APIs, main entry points, and dependencies")
    parser.add_argument("--tokenizer", default="cl100k_base")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--max-file-bytes", type=int, default=256000)
    parser.add_argument("--max-total-bytes", type=int, default=16000000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", type=Path, help="Environment file (default: project .env)")
    parser.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        spec = json.loads(args._worker.read_text(encoding="utf-8"))
        write_json(Path(spec["result"]), run_case(spec))
        return
    load_environment(args.env_file, default_path=PROJECT / ".env")
    if (
        min(args.budgets) < 0
        or min(args.repeats, args.timeout, args.max_files, args.max_file_bytes, args.max_total_bytes) < 1
    ):
        parser.error("Budgets must be nonnegative; repeats, timeout and scan limits must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest["images"]
    if args.repos:
        unknown = set(args.repos) - {item["repo"] for item in items}
        if unknown:
            parser.error(f"Unknown repositories: {', '.join(sorted(unknown))}")
        items = [item for item in items if item["repo"] in args.repos]
    if args.limit:
        items = items[: args.limit]
    if not items or any(not re.fullmatch(r"[\w.-]+/[\w.-]+", item["repo"]) for item in items):
        parser.error("Manifest must contain valid owner/repository names")
    if args.dry_run:
        for item in items:
            print(item["repo"], item["image"])
        return
    if not shutil.which("docker"):
        parser.error("Docker is not installed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or PROJECT / "benchmarks" / "results" / f"{stamp}-{uuid.uuid4().hex[:6]}").resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"Output directory must be empty: {output}")
    for name in ["cases", "logs", "maps"]:
        (output / name).mkdir(parents=True, exist_ok=True)
    config = {
        "manifest": str(args.manifest.resolve()),
        "case_count": len(items),
        "budgets": list(dict.fromkeys(args.budgets)),
        "repeats": args.repeats,
        "timeout": args.timeout,
        "task": args.task,
        "tokenizer": args.tokenizer,
        "created_at": stamp,
        "scan_limits": {
            "max_files": args.max_files,
            "max_file_bytes": args.max_file_bytes,
            "max_total_bytes": args.max_total_bytes,
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "versions": {
            package: version(package)
            for package in ["mini-swe-repomap", "tree-sitter", "tree-sitter-language-pack", "tiktoken"]
        },
    }
    write_json(output / "config.json", config)
    cases = []
    for index, item in enumerate(items, 1):
        slug = item["repo"].replace("/", "__")
        result_path = output / "cases" / f"{slug}.json"
        log_path = output / "logs" / f"{slug}.log"
        name = f"repomap-profile-{uuid.uuid4().hex}"
        spec = {
            "item": item,
            "config": config,
            "output": str(output),
            "slug": slug,
            "log": str(log_path),
            "container_name": name,
            "result": str(result_path),
        }
        spec_path = output / "cases" / f"{slug}.input.json"
        write_json(spec_path, spec)
        print(f"[{index}/{len(items)}] {item['repo']}", flush=True)
        try:
            with log_path.open("a", encoding="utf-8") as log:
                subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--_worker", str(spec_path)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=args.timeout,
                    check=True,
                    env=os.environ | {"MSWEA_SILENT_STARTUP": "1"},
                )
            case = json.loads(result_path.read_text(encoding="utf-8"))
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            case = {
                "repo": item["repo"],
                "image": item["image"],
                "status": "failed",
                "error": type(error).__name__ + ": " + str(error),
                "log": str(log_path),
            }
            write_json(result_path, case)
            print(f"  failed; see {log_path}", flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        cases.append(case)
        write_report(output, config, cases)
    print(f"Report: {output / 'summary.md'}", flush=True)
    if any(case["status"] != "ok" for case in cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
