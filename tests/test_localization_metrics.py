from pathlib import Path


def test_localization_needs_explicit_read_and_visible_successful_source(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from localization_metrics import localization

    reference = {"src/api.py": [{"line": 20, "text": "def validate(value, strict=False):"}]}
    messages = [
        {"role": "assistant", "extra": {"actions": [{"command": "grep -l validate src/api.py", "tool_call_id": "a"}]}},
        {"role": "tool", "tool_call_id": "a", "content": "src/api.py", "extra": {"returncode": 0}},
        {
            "role": "assistant",
            "extra": {"actions": [{"command": "cd /testbed/src && sed -n '15,30p' api.py", "tool_call_id": "b"}]},
        },
        {
            "role": "tool",
            "tool_call_id": "b",
            "content": "def validate(value, strict=False):",
            "extra": {"returncode": 0},
        },
    ]
    score = localization(messages, reference)
    assert score["status"] == "observed_explicit_read"
    assert score["search_commands_through_action"] == 1
    assert score["assistant_calls"] == 2
    messages[-1]["extra"]["returncode"] = 1
    assert localization(messages, reference)["search_commands_through_action"] is None
    messages[-1]["extra"]["returncode"] = 0
    messages[-1]["extra"]["raw_output"] = messages[-1]["content"]
    messages[-1]["content"] = "[source was truncated from model-visible output]"
    assert localization(messages, reference)["status"] == "not_observed"


def test_references_use_baseline_hunk_context_and_exclude_ambiguous_lines(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from localization_metrics import reference_regions

    source = "def validate(value, strict=False):\n    return is_valid(value, strict)\n" * 2
    files = [{"base_path": "src/api.py", "hunks": [{"old_line": 1}]}]
    assert reference_regions(files, {"src/api.py": source}) == {}
    source = "def validate(value, strict=False):\n    return is_valid(value, strict)\n"
    assert reference_regions(files, {"src/api.py": source})["src/api.py"][0]["line"] == 1
