import subprocess

import pytest
from minisweagent.environments.docker import DockerEnvironment
from minisweagent.models.test_models import make_output
from test_agent import RecordingTextModel, maps

from mini_swe_repomap.agent import RepoMapAgent
from mini_swe_repomap.repomap import RepoMap
from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.source import EnvironmentSource


@pytest.mark.parametrize("map_mode", ["budget", "full"])
def test_map_is_collected_inside_real_docker_environment(map_mode):
    try:
        available = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Docker with python:3.12-slim is not available")
    if available.returncode:
        pytest.skip("Docker with python:3.12-slim is not available")
    env = DockerEnvironment(
        image="python:3.12-slim",
        interpreter=["bash", "-c"],
        cwd="/tmp",
        run_args=["--rm", "--network=none", "--pull=never"],
    )
    try:
        assert env.execute({"command": "mkdir /tmp/only-in-container"})["returncode"] == 0
        assert (
            env.execute({"command": "printf 'def container_only(): pass\\n' > /tmp/only-in-container/api.py"})[
                "returncode"
            ]
            == 0
        )
        settings = {"mode": map_mode, "refresh": "once", "strict": True}
        if map_mode == "full":
            settings |= {"max_files": None, "max_file_bytes": None, "max_total_bytes": None, "map_tokens": 1}
        source = EnvironmentSource(env, RepoMapConfig(root="only-in-container", **settings))
        assert source.read() == {"api.py": "def container_only(): pass\n"}
        assert source.root == "/tmp/only-in-container"
        env.config.cwd = "/tmp/only-in-container"
        if map_mode == "full":
            assert (
                env.execute(
                    {
                        "command": 'python3 -c "from pathlib import Path; '
                        "Path('bulk.py').write_text('\\n'.join(f'def extra_{i}(): pass' for i in range(200)))\""
                    }
                )["returncode"]
                == 0
            )
        model = RecordingTextModel(
            outputs=[make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}])]
        )
        agent = RepoMapAgent(model, env, system_template="SYSTEM", instance_template="{{task}}", repomap=settings)
        assert agent.run("Fix container_only")["exit_status"] == "Submitted"
        assert "def container_only" in maps(model.requests[0])[0]
        if map_mode == "full":
            assert RepoMap().token_count(maps(model.requests[0])[0]) > 1024
            assert "def extra_199():" in maps(model.requests[0])[0]
    finally:
        env.cleanup()
