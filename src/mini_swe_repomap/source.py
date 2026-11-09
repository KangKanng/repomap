import json
import shlex
from importlib.resources import files

from minisweagent import Environment

from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.symbols import LANGUAGES

MARKER = "__MINISWE_REPOMAP_V1__"


class RepoMapError(RuntimeError):
    """The repository could not be read from the execution environment."""


class EnvironmentSource:
    def __init__(self, env: Environment, config: RepoMapConfig):
        self.env = env
        self.config = config
        self.root = ""
        self.fingerprint = ""
        self.sources: dict[str, str] = {}
        self.warnings: list[str] = []
        self._script = files("mini_swe_repomap").joinpath("_collect.py").read_text(encoding="utf-8")

    def read(self) -> dict[str, str]:
        options = {
            "root": self.config.root,
            "fingerprint": self.fingerprint,
            "suffixes": list(LANGUAGES),
            "max_files": self.config.max_files,
            "max_file_bytes": self.config.max_file_bytes,
            "max_total_bytes": self.config.max_total_bytes,
        }
        command = shlex.join([self.config.python_command, "-c", self._script, json.dumps(options)])
        output = self.env.execute({"command": command})
        if output.get("returncode") != 0:
            raise RepoMapError(
                f"Repository collector failed: {output.get('exception_info') or output.get('output', '')}"
            )
        try:
            payload = next(line[len(MARKER) :] for line in output["output"].splitlines() if line.startswith(MARKER))
            snapshot = json.loads(payload)
            sources = (
                self.sources
                if snapshot.get("unchanged")
                else {entry["path"]: entry["source"] for entry in snapshot["files"]}
            )
        except (ValueError, KeyError, IndexError, TypeError, StopIteration) as error:
            raise RepoMapError("Repository collector returned an invalid or truncated snapshot.") from error
        self.root, self.sources, self.fingerprint = snapshot["root"], sources, snapshot["fingerprint"]
        self.warnings = snapshot["warnings"]
        return self.sources
