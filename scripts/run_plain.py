"""Run the selected 20 instances in plain mode (RepoMap disabled), with offline Docker containers."""

import sys

from pull_docker import BASE, PROJECT
from run_swebench import main as run_swebench


def main():
    run_swebench(
        [
            "--config",
            str(PROJECT / "src" / "mini_swe_repomap" / "config" / "offline.yaml"),
            "--image-overrides",
            str(BASE / "offline_images.json"),
            "--workers",
            "2",
            *sys.argv[1:],
            "--offline",
            "--variants",
            "plain",
        ]
    )


if __name__ == "__main__":
    main()
