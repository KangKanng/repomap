import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import litellm
import pytest
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel

from mini_swe_repomap.agent import RepoMapAgent
from mini_swe_repomap.env import load_environment
from mini_swe_repomap.env_model import EnvLitellmModel

PROJECT = Path(__file__).resolve().parents[1]
MODEL_VARIABLES = ["MODEL_NAME", "MSWEA_MODEL_NAME", "MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_STREAMING"]


def test_request_maps_are_frozen_metadata_and_never_reinjected(monkeypatch):
    content = "<repository_map>\nsrc/api.py:\n</repository_map>"
    messages = [{"role": "user", "content": content}]
    result = {"role": "assistant", "content": "read it", "extra": {"cost": 0.01}}
    monkeypatch.setattr(LitellmModel, "query", lambda self, history, **kwargs: result)
    model = EnvLitellmModel(model_name="openai/test", cost_tracking="ignore_errors")
    recorded = model.query(messages)
    snapshot = recorded["extra"]["request_repository_maps"][0]
    assert snapshot == {"message_index": 0, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
    messages[0]["content"] = "<repository_map>\nchanged.py:\n</repository_map>"
    assert snapshot["content"] == content
    prepared = model._prepare_messages_for_api([recorded])
    assert prepared == [{"role": "assistant", "content": "read it"}]


def test_format_errors_also_preserve_request_map_snapshots(monkeypatch):
    def fail(self, messages, **kwargs):
        raise FormatError({"role": "assistant", "content": "bad", "extra": {"cost": 0.01}})

    monkeypatch.setattr(LitellmModel, "query", fail)
    model = EnvLitellmModel(model_name="openai/test", cost_tracking="ignore_errors")
    with pytest.raises(FormatError) as caught:
        model.query([{"role": "user", "content": "<repository_map>\na.py:\n</repository_map>"}])
    assert caught.value.messages[0]["extra"]["request_repository_maps"][0]["content"].startswith("<repository_map>")


def test_environment_file_priority_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for name in MODEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "custom.env"
    config.write_text("MODEL_NAME=local-model\nMODEL_BASE_URL=https://example.invalid/v1\nMODEL_API_KEY=file-key\n")
    monkeypatch.setenv("MODEL_API_KEY", "exported-key")
    assert load_environment(config, default_path=tmp_path / "unused.env") == config
    assert os.environ["MODEL_API_KEY"] == "exported-key"
    assert os.environ["MSWEA_MODEL_NAME"] == "openai/local-model"
    monkeypatch.setenv("MSWEA_MODEL_NAME", "native-model")
    load_environment(config, default_path=config)
    assert os.environ["MSWEA_MODEL_NAME"] == "native-model"
    assert load_environment(None, default_path=tmp_path / "missing") is None
    with pytest.raises(FileNotFoundError, match="Environment file not found"):
        load_environment(tmp_path / "missing", default_path=config)


def test_script_env_file_model_override_and_no_secret_in_commands(tmp_path):
    config = tmp_path / "custom.env"
    secret = "test-key-that-must-not-appear-in-commands"
    config.write_text(
        f"MODEL_NAME=openai/file-model\nMODEL_API_KEY={secret}\nMODEL_BASE_URL=https://example.invalid/v1\n"
    )
    env = {key: value for key, value in os.environ.items() if key not in MODEL_VARIABLES}
    env["MODEL_NAME"] = "openai/exported-model"
    for extra, expected in [([], "openai/exported-model"), (["--model", "openai/cli-model"], "openai/cli-model")]:
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT / "scripts" / "run_swebench.py"),
                "--env-file",
                str(config),
                "--dry-run",
                "--repos",
                "pallets/flask",
                *extra,
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            command = shlex.split(line)
            assert command[command.index("-m", 2) + 1] == expected
            assert "model.model_class=mini_swe_repomap.env_model.EnvLitellmModel" in command
        assert secret not in result.stdout + result.stderr
        assert "https://example.invalid/v1" not in result.stdout + result.stderr
    assert not (tmp_path / "plan.json").exists()


@pytest.fixture
def model_endpoint(request):
    received = []
    settings = getattr(request, "param", {})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers["Authorization"], payload))
            common = {"id": "chatcmpl-env-test", "created": 1, "model": "env-test"}
            tool = {
                "id": "call_env",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"echo done"}'},
            }
            usage = {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}
            if "cost" in settings:
                usage |= {"cost": settings["cost"], "cost_details": {"upstream_inference_cost": 99.0}}
            self.send_response(200)
            if not payload.get("stream"):
                body = json.dumps(
                    common
                    | {
                        "object": "chat.completion",
                        "usage": usage,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "tool_calls",
                                "message": {"role": "assistant", "content": None, "tool_calls": [tool]},
                            }
                        ],
                    }
                ).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_env",
                            "type": "function",
                            "function": {"name": "bash", "arguments": ""},
                        }
                    ],
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": '{"command":"echo '}}]},
                {"tool_calls": [{"index": 0, "function": {"arguments": 'done"}'}}]},
            ]
            for index, delta in enumerate(chunks):
                body = common | {
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if index == 2 else None,
                        }
                    ],
                }
                self.wfile.write(("data: " + json.dumps(body) + "\n\n").encode())
            self.wfile.write(
                (
                    "data: "
                    + json.dumps(common | {"object": "chat.completion.chunk", "choices": [], "usage": usage})
                    + "\n\n"
                ).encode()
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(("stream", "override"), [("false", False), ("true", False), ("true", True)])
def test_env_model_requests_streams_and_serialized_credentials(model_endpoint, monkeypatch, stream, override):
    endpoint, received = model_endpoint
    secret = "env-test-secret-not-for-trajectories"
    monkeypatch.setenv("MODEL_BASE_URL", endpoint)
    monkeypatch.setenv("MODEL_API_KEY", secret)
    monkeypatch.setenv("MODEL_STREAMING", stream)
    monkeypatch.setenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "1")
    options = {"timeout": 5, "num_retries": 0}
    if override:
        options |= {"api_base": endpoint + "/override", "api_key": "explicit-key", "stream": False}
    model = EnvLitellmModel(model_name="openai/env-test", cost_tracking="ignore_errors", model_kwargs=options)
    response = model.query([{"role": "user", "content": "Execute the command"}])
    assert response["extra"]["actions"] == [{"command": "echo done", "tool_call_id": "call_env"}]
    assert len(received) == 1
    path, authorization, payload = received[0]
    assert path == ("/v1/override/chat/completions" if override else "/v1/chat/completions")
    assert authorization == "Bearer " + ("explicit-key" if override else secret)
    assert payload.get("stream", False) is (stream == "true" and not override)
    assert secret not in json.dumps(model.serialize())
    assert secret not in json.dumps(response)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model_endpoint", [{"cost": 0.012345}, {"cost": 0.0}], indirect=True)
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
def test_server_cost_survives_http_and_stream_assembly(model_endpoint, monkeypatch, stream, provider):
    endpoint, received = model_endpoint
    monkeypatch.setenv("MODEL_BASE_URL", endpoint)
    monkeypatch.setenv("MODEL_API_KEY", "local-test-key")
    monkeypatch.setenv("MODEL_STREAMING", str(stream).lower())
    monkeypatch.setenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "1")
    model = EnvLitellmModel(
        model_name=f"{provider}/env-test", cost_tracking="default", model_kwargs={"timeout": 5, "num_retries": 0}
    )
    response = model.query([{"role": "user", "content": "Execute the command"}])
    usage = response["extra"]["response"]["usage"]
    assert response["extra"]["cost"] == usage["cost"]
    assert response["extra"]["cost_source"] == "server:usage.cost"
    assert response["extra"]["actions"] == [{"command": "echo done", "tool_call_id": "call_env"}]
    assert len(received) == 1


def test_provider_cost_header_and_body_priority():
    model = EnvLitellmModel(model_name="openrouter/env-test", cost_tracking="default")
    response = litellm.ModelResponse(usage={"prompt_tokens": 10, "completion_tokens": 7})
    response._hidden_params["additional_headers"] = {"llm_provider-x-litellm-response-cost": "0.012345"}
    assert model._calculate_cost(response) == {
        "cost": 0.012345,
        "cost_source": "server:llm_provider-x-litellm-response-cost",
    }
    response.usage.cost = 0.0
    assert model._calculate_cost(response) == {"cost": 0.0, "cost_source": "server:usage.cost"}


@pytest.mark.parametrize("cost", [-0.1, float("nan"), float("inf"), "bad-cost", True, {}])
def test_invalid_server_cost_fails_instead_of_becoming_zero(cost):
    model = EnvLitellmModel(model_name="openrouter/env-test", cost_tracking="default")
    response = litellm.ModelResponse(usage={"cost": cost})
    with pytest.raises(ValueError, match="Invalid server-reported cost"):
        model._calculate_cost(response)


def test_missing_server_cost_keeps_native_pricing_policy(monkeypatch):
    response = litellm.ModelResponse(usage={"prompt_tokens": 10, "completion_tokens": 7, "cost": None})
    response._hidden_params["response_cost"] = 9.0

    def missing_price(*args, **kwargs):
        raise ValueError("test model has no registered price")

    monkeypatch.setattr(litellm.cost_calculator, "completion_cost", missing_price)
    model = EnvLitellmModel(model_name="openrouter/env-test", cost_tracking="default")
    with pytest.raises(RuntimeError, match="no registered price"):
        model._calculate_cost(response)
    model.config.cost_tracking = "ignore_errors"
    assert model._calculate_cost(response) == {"cost": 0.0}

    monkeypatch.setattr(litellm.cost_calculator, "completion_cost", lambda *args, **kwargs: 0.1)
    assert model._calculate_cost(response) == {"cost": 0.1}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model_endpoint", [{"cost": 0.012345}], indirect=True)
def test_server_cost_accumulates_in_trajectory_and_stops_at_budget(model_endpoint, monkeypatch, tmp_path, stream):
    endpoint, received = model_endpoint
    monkeypatch.setenv("MODEL_BASE_URL", endpoint)
    monkeypatch.setenv("MODEL_API_KEY", "local-budget-test-key")
    monkeypatch.setenv("MODEL_STREAMING", str(stream).lower())
    monkeypatch.setenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "1")
    model = EnvLitellmModel(
        model_name="openrouter/env-test", cost_tracking="default", model_kwargs={"timeout": 5, "num_retries": 0}
    )
    trajectory = tmp_path / "cost.traj.json"
    agent = RepoMapAgent(
        model,
        LocalEnvironment(cwd=str(tmp_path)),
        system_template="SYSTEM",
        instance_template="{{task}}",
        cost_limit=0.02,
        output_path=trajectory,
        repomap={"map_tokens": 0},
    )
    assert agent.run("Execute the command")["exit_status"] == "LimitsExceeded"
    assert len(received) == 2
    data = json.loads(trajectory.read_text())
    assert data["info"]["model_stats"]["instance_cost"] == pytest.approx(0.02469)
    billed_messages = [message for message in data["messages"] if message.get("extra", {}).get("cost_source")]
    assert len(billed_messages) == 2
    assert all(message["extra"]["cost_source"] == "server:usage.cost" for message in billed_messages)
    assert "local-budget-test-key" not in trajectory.read_text()
