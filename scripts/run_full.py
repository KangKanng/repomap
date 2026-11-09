"""Compare plain with one complete initial RepoMap, without scan or token caps, in offline Docker containers."""

import sys

from pull_docker import BASE, PROJECT
from run_swebench import main as run_swebench


def main():
    configs = PROJECT / "src" / "mini_swe_repomap" / "config"
    run_swebench(
        [
            "--config",
            str(configs / "offline.yaml"),
            "--image-overrides",
            str(BASE / "offline_images.json"),
            "--workers",
            "2",
            *sys.argv[1:],
            "--config",
            str(configs / "full-once.yaml"),
            "--map-tokens",
            "1024",
            "--offline",
            "--variants",
            "plain",
            "repomap",
        ]
    )


if __name__ == "__main__":
    main()
