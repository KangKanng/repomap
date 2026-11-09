import copy
import shlex
from unittest.mock import patch

import pytest
from minisweagent.agents import get_agent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import (
    DeterministicModel,
    DeterministicResponseAPIToolcallModel,
    DeterministicToolcallModel,
    make_output,
    make_response_api_output,
    make_toolcall_output,
)
from minisweagent.models.utils.content_string import get_content_string

from mini_swe_repomap.agent import InteractiveRepoMapAgent, RepoMapAgent
from mini_swe_repomap.repomap import RepoMap
from mini_swe_repomap.source import RepoMapError


class RecordingMixin:
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    def query(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        return super().query(messages, **kwargs)


class RecordingTextModel(RecordingMixin, DeterministicModel):
    pass


class RecordingToolModel(RecordingMixin, DeterministicToolcallModel):
    pass


class RecordingResponsesModel(RecordingMixin, DeterministicResponseAPIToolcallModel):
    pass


def output_for(model_class, command, index):
    if model_class is RecordingTextModel:
        return make_output("Executing command", [{"command": command}])
    action = {"command": command, "tool_call_id": f"call_{index}"}
    if model_class is RecordingResponsesModel:
        return make_response_api_output("Executing command", [action])
    import json

    return make_toolcall_output(
        "Executing command",
        [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": command})},
            }
        ],
        [action],
    )


def maps(messages):
    return [
        get_content_string(message)
        for message in messages
        if get_content_string(message).startswith("<repository_map>")
    ]


@pytest.mark.parametrize(("model_class",), [(RecordingTextModel,), (RecordingToolModel,), (RecordingResponsesModel,)])
def test_complete_run_refreshes_one_map_without_breaking_messages(repo, tmp_path, model_class):
    command = shlex.join(
        ["python3", "-c", "from pathlib import Path; Path('api.py').write_text('def new_api(): pass\\n')"]
    )
    model = model_class(
        outputs=[
            output_for(model_class, command, 0),
            output_for(model_class, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", 1),
        ]
    )
    agent = RepoMapAgent(
        model,
        LocalEnvironment(cwd=str(repo)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        output_path=tmp_path / "run.traj.json",
        repomap={"strict": True},
    )
    assert agent.run("Fix Service")["exit_status"] == "Submitted"
    assert len(model.requests) == agent.n_calls == 2
    assert len(maps(model.requests[0])) == len(maps(model.requests[1])) == len(maps(agent.messages)) == 1
    assert "class Service" in maps(model.requests[0])[0]
    assert "def new_api" in maps(model.requests[1])[0]
    assert "class Service" not in maps(model.requests[1])[0]
    assert get_content_string(model.requests[1][0]) == "SYSTEM"
    assert get_content_string(model.requests[1][1]) == "Fix Service"
    assert len(model.requests[1]) > len(model.requests[0])
    assert (tmp_path / "run.traj.json").is_file()
    assert agent.serialize()["info"]["config"]["agent"]["repomap"]["map_tokens"] == 1024


def test_refresh_interval_and_run_reuse(repo):
    command = shlex.join(
        ["python3", "-c", "from pathlib import Path; Path('api.py').write_text('def updated(): pass\\n')"]
    )
    model = RecordingTextModel(
        outputs=[
            make_output("edit", [{"command": command}]),
            make_output("inspect", [{"command": "cat api.py"}]),
            make_output("finish", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]),
            make_output("finish again", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]),
        ]
    )
    agent = RepoMapAgent(
        model,
        LocalEnvironment(cwd=str(repo)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        cost_limit=0,
        repomap={"refresh_interval": 2},
    )
    assert agent.run("Fix Service")["exit_status"] == "Submitted"
    assert "class Service" in maps(model.requests[1])[0]
    assert "def updated" in maps(model.requests[2])[0]
    assert agent.run("New task")["exit_status"] == "Submitted"
    assert len(maps(model.requests[3])) == 1
    assert "def updated" in maps(model.requests[3])[0]


@pytest.mark.parametrize("model_class", [RecordingTextModel, RecordingToolModel, RecordingResponsesModel])
@pytest.mark.parametrize("map_mode", ["budget", "full"])
def test_once_keeps_initial_map_after_edits_and_rebuilds_for_new_task(repo, model_class, map_mode):
    if map_mode == "full":
        (repo / "bulk.py").write_text("\n".join(f"def extra_{i}(): pass" for i in range(200)))
    command = shlex.join(
        ["python3", "-c", "from pathlib import Path; Path('api.py').write_text('def updated(): pass\\n')"]
    )
    model = model_class(
        outputs=[
            output_for(model_class, command, 0),
            output_for(model_class, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", 1),
            output_for(model_class, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", 2),
        ]
    )
    agent = RepoMapAgent(
        model,
        LocalEnvironment(cwd=str(repo)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        cost_limit=0,
        repomap={
            "mode": map_mode,
            "refresh": "once",
            "map_tokens": 1024 if map_mode == "budget" else 1,
            "context_messages": 0,
            "strict": True,
        },
    )
    with patch.object(agent.repo_source, "read", wraps=agent.repo_source.read) as read:
        assert agent.run("Fix Service")["exit_status"] == "Submitted"
        assert read.call_count == 1
        initial = maps(model.requests[0])
        assert len(initial) == 1 and "class Service" in initial[0]
        assert maps(model.requests[1]) == maps(agent.messages) == initial
        assert "def updated" not in initial[0]
        if map_mode == "budget":
            assert RepoMap().token_count(initial[0]) <= 1024
        else:
            assert RepoMap().token_count(initial[0]) > 1024
            assert all(f"def extra_{i}():" in initial[0] for i in range(200))
        assert maps(model.requests[0][2:3]) == initial
        assert len(model.requests[1]) > len(model.requests[0])
        assert agent.serialize()["info"]["config"]["agent"]["repomap"]["refresh"] == "once"

        assert agent.run("Inspect updated")["exit_status"] == "Submitted"
        assert read.call_count == 2
        assert len(maps(model.requests[2])) == 1
        assert "def updated" in maps(model.requests[2])[0]
        assert "class Service" not in maps(model.requests[2])[0]


@pytest.mark.parametrize("missing", [False, True])
def test_once_does_not_retry_empty_map_or_non_strict_collection_failure(tmp_path, missing):
    root = tmp_path / "empty"
    root.mkdir()
    model = RecordingTextModel(
        outputs=[
            make_output("inspect", [{"command": "pwd"}]),
            make_output("finish", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]),
        ]
    )
    agent = RepoMapAgent(
        model,
        LocalEnvironment(cwd=str(root)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        repomap={"refresh": "once", "root": "missing" if missing else "."},
    )
    with patch.object(agent.repo_source, "read", wraps=agent.repo_source.read) as read:
        assert agent.run("Inspect repository")["exit_status"] == "Submitted"
        assert read.call_count == 1
    assert all(not maps(request) for request in model.requests)


def test_disable_and_collection_failure_policy(repo, caplog):
    for settings in [
        {"map_tokens": 0, "root": "missing", "strict": True},
        {"map_tokens": 0, "mode": "full", "refresh": "once", "root": "missing", "strict": True},
        {"root": "missing"},
    ]:
        model = RecordingTextModel(
            outputs=[make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}])]
        )
        agent = RepoMapAgent(
            model,
            LocalEnvironment(cwd=str(repo)),
            system_template="SYSTEM",
            instance_template="{{task}}",
            repomap=settings,
        )
        assert agent.run("Fix Service")["exit_status"] == "Submitted"
        assert not maps(model.requests[0])
    assert "Repo map unavailable" in caplog.text
    agent = RepoMapAgent(
        RecordingTextModel(outputs=[]),
        LocalEnvironment(cwd=str(repo)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        repomap={"root": "missing", "strict": True},
    )
    with pytest.raises(RepoMapError):
        agent.run("Fix Service")


def test_interactive_agent_loaded_by_upstream_factory(repo):
    model = RecordingTextModel(
        outputs=[make_output("done", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}])]
    )
    agent = get_agent(
        model,
        LocalEnvironment(cwd=str(repo)),
        {
            "agent_class": "mini_swe_repomap.agent.InteractiveRepoMapAgent",
            "system_template": "SYSTEM",
            "instance_template": "{{task}}",
            "mode": "yolo",
            "confirm_exit": False,
        },
    )
    assert isinstance(agent, InteractiveRepoMapAgent)
    assert agent.run("Fix Service")["exit_status"] == "Submitted"
    assert "class Service" in maps(model.requests[0])[0]
