"""Pull the selected source-bearing SWE-bench images and inventory their repositories."""

import argparse
import json
import shutil
import subprocess
import tarfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mini_swe_repomap.env import load_environment

PROJECT = Path(__file__).resolve().parents[1]
BASE = PROJECT / "benchmarks" / "docker_repositories"
LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".c": "C/C++",
    ".h": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".hpp": "C/C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
}
EXCLUDED = {"node_modules", "vendor", ".venv", "venv", "build", "dist", "__pycache__"}
FIND_ROOT = """
for directory in /testbed "$PWD" /workspace /app; do
    if [ -e "$directory/.git" ]; then
        cd "$directory" || exit 1
        pwd
        git -c safe.directory="$PWD" log -1 --format='%H%n%T%n%s'
        git -c safe.directory="$PWD" rev-parse "$1^{tree}"
        exit $?
    fi
done
echo 'Repository not found' >&2
exit 1
"""


def container_command(image: str, script: str, *args: str, name: str = "") -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--memory=1g",
        "--cpus=1",
        "--pids-limit=128",
        "--env",
        "GIT_OPTIONAL_LOCKS=0",
        "--entrypoint",
        "/bin/sh",
        *(["--name", name] if name else []),
        image,
        "-c",
        script,
        "inventory",
        *args,
    ]


@contextmanager
def repository_archive(image: str, root: str, log_path: Path, *, name: str = ""):
    archive = """
cd "$1" || exit 1
git -c safe.directory="$PWD" ls-files -z | tar --null --no-recursion --ignore-failed-read -T - -cf -
"""
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            container_command(image, archive, root, name=name), stdout=subprocess.PIPE, stderr=log
        )
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as stream:
                yield stream
            while process.stdout.read(1024 * 1024):
                pass
            if process.wait(timeout=180):
                raise RuntimeError(f"Repository streaming failed; see {log_path}")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def measure(image: str, log_path: str, base_commit: str) -> dict:
    metadata = subprocess.run(
        container_command(image, FIND_ROOT, base_commit), capture_output=True, text=True, check=True, timeout=60
    )
    root, commit, tree, subject, base_tree = metadata.stdout.strip().splitlines()
    tracked = files = source_bytes = source_lines = eligible_files = eligible_bytes = 0
    line_counts = Counter()
    with repository_archive(image, root, Path(log_path)) as stream:
        for member in stream:
            if member.isdir():
                continue
            tracked += 1
            language = LANGUAGES.get(Path(member.name).suffix.lower())
            if not language or not member.isfile():
                continue
            size = newlines = 0
            last = b""
            binary = False
            with stream.extractfile(member) as content:
                for block in iter(lambda: content.read(1024 * 1024), b""):
                    size += len(block)
                    newlines += block.count(b"\n")
                    binary |= b"\0" in block
                    last = block[-1:]
            if binary:
                continue
            count = newlines + int(bool(size) and last != b"\n")
            files += 1
            source_bytes += size
            source_lines += count
            line_counts[language] += count
            if not any(part in EXCLUDED for part in Path(member.name).parts) and size <= 256000:
                eligible_files += 1
                eligible_bytes += size
    return {
        "root": root,
        "commit": commit,
        "commit_subject": subject,
        "tree": tree,
        "dataset_base_tree": base_tree,
        "tracked_files": tracked,
        "source_files": files,
        "source_lines": source_lines,
        "source_bytes": source_bytes,
        "language_lines": dict(line_counts.most_common()),
        "eligible_source_files_before_global_limits": eligible_files,
        "eligible_source_bytes_before_global_limits": eligible_bytes,
        "inventory_method": "Read-only tracked worktree tar stream; counted on the host without extracting files",
    }


def pull_one(item: dict, pull: bool) -> dict:
    result = dict(item)
    slug = item["repo"].replace("/", "__")
    result["log"] = str(BASE / "logs" / f"{slug}.log")
    started = time.monotonic()
    print(f"Starting {item['repo']}: {item['image']}", flush=True)
    try:
        if pull:
            with Path(result["log"]).open("w", encoding="utf-8") as log:
                subprocess.run(
                    ["docker", "pull", "--platform", "linux/amd64", item["image"]],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=1800,
                )
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", item["image"]], timeout=30))[0]
        result.update(
            {
                "image_id": image["Id"],
                "repo_digests": image.get("RepoDigests", []),
                "image_bytes": image["Size"],
                "architecture": image["Architecture"],
            }
        )
        repository = measure(item["image"], result["log"], item["base_commit"])
        if not repository["source_files"]:
            raise RuntimeError("Image contains no recognized repository source files")
        result["repository"] = repository
        result["commit_matches_dataset"] = repository["commit"] == item["base_commit"]
        result["tree_matches_dataset"] = repository["tree"] == repository["dataset_base_tree"]
        if not result["tree_matches_dataset"]:
            raise RuntimeError("Image HEAD source tree differs from the dataset base commit tree")
        result["status"] = "ready"
        print(
            f"Ready {item['repo']}: {repository['source_files']:,} source files, {repository['source_lines']:,} lines",
            flush=True,
        )
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, RuntimeError, tarfile.TarError) as error:
        result["status"] = "failed"
        result["error"] = (
            f"Docker command exited with {error.returncode}"
            if isinstance(error, subprocess.CalledProcessError)
            else str(error)
        )
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            result["error"] += "\n" + error.stderr
        print(f"Failed {item['repo']}: {result['error']}", flush=True)
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


def write_inventory(results: list[dict]):
    ready = sorted(
        (item for item in results if item["status"] == "ready"),
        key=lambda item: item["repository"]["source_lines"],
    )
    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection": str(BASE / "selection.json"),
        "source_line_definition": "Physical lines, including comments and blanks, in tracked files of recognized source languages; includes tests and tracked vendored code; excludes binary files and symlinks.",
        "ready": len(ready),
        "failed": sum(item["status"] == "failed" for item in results),
        "images": ready + [item for item in results if item["status"] != "ready"],
    }
    temporary = BASE / "inventory.tmp"
    temporary.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(BASE / "inventory.json")
    lines = [
        "# Docker 仓库样本\n",
        "通过 `docker pull` 拉取的 SWE-bench 官方源码镜像，每个镜像对应一个不同的项目。",
        "源码保留在镜像内的 `/testbed`，无需 clone 到宿主机。\n",
        "按源码物理行数从小到大排序。统计 Git tracked 源码，包含测试、注释、空行及被跟踪的第三方代码；不等于去注释的 SLOC。",
        "`image_bytes` 是 Docker 报告的镜像逻辑大小，共享层不能直接累加为实际磁盘占用。\n",
        "| # | 仓库 | 行数最多的语言 | 源码文件 | 源码行数 | 镜像大小 GiB | 实例 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for number, item in enumerate(ready, 1):
        repo = item["repository"]
        language = next(iter(repo["language_lines"]))
        lines.append(
            f"| {number} | [{item['repo']}](https://github.com/{item['repo']}) | {language} | "
            f"{repo['source_files']:,} | {repo['source_lines']:,} | {item['image_bytes'] / 1024**3:.2f} | `{item['instance_id']}` |"
        )
    extra_commits = [item for item in ready if not item["commit_matches_dataset"]]
    if extra_commits:
        lines.append(
            f"\n版本核对：{len(ready) - len(extra_commits)} 个镜像的 HEAD 与数据集 base commit 相同；"
            f"另外 {len(extra_commits)} 个镜像包含额外构建提交，HEAD 哈希不同，但源码树 SHA 与 base commit 一致。"
            "实际 HEAD、提交说明、源码树 SHA 和数据集 base commit 已分别记录。"
        )
    lines += [
        "\n完整镜像名、固定 digest、仓库 commit、分语言行数和拉取日志见 [inventory.json](inventory.json)。",
        "\n再次拉取并核对：\n\n```bash\npython scripts/pull_docker.py\n```",
        "\n只核对本地镜像：\n\n```bash\npython scripts/pull_docker.py --inspect-only\n```",
        "\n来源：[SWE-bench Multilingual](https://huggingface.co/datasets/SWE-bench/SWE-bench_Multilingual)、[SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)。",
    ]
    (BASE / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="List images without pulling or starting containers")
    parser.add_argument("--env-file", type=Path, help="Environment file (default: project .env)")
    args = parser.parse_args()
    load_environment(args.env_file, default_path=PROJECT / ".env")
    selection = json.loads((BASE / "selection.json").read_text(encoding="utf-8"))
    if len(selection) != 20 or len({item["repo"] for item in selection}) != 20:
        raise ValueError("Selection must contain 20 different repositories")
    if args.dry_run:
        for item in selection:
            print(item["image"])
        return
    if not args.inspect_only and shutil.disk_usage("/var/lib/docker").free < 40 * 1024**3:
        raise RuntimeError("Less than 40 GiB free in the Docker filesystem")
    (BASE / "logs").mkdir(exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(pull_one, item, not args.inspect_only) for item in selection]
        for future in as_completed(futures):
            results.append(future.result())
            write_inventory(results)
    print(
        f"Finished: {sum(item['status'] == 'ready' for item in results)}/20 ready. Inventory: {BASE / 'inventory.json'}",
        flush=True,
    )
    if any(item["status"] != "ready" for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
