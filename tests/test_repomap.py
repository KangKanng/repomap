from unittest.mock import patch

import pytest
import tiktoken

from mini_swe_repomap.repomap import RepoMap


@pytest.mark.parametrize(("budget",), [(0,), (1,), (40,), (80,), (128,), (256,), (512,), (1024,)])
def test_token_budget_with_unicode_and_special_tokens(budget):
    sources = {
        "模块/接口.py": 'def 登录(名称: str = "<|endoftext|>"):\n    return 名称\n',
        **{f"module_{i}.py": f"def function_{i}(value: str) -> str:\n    return value\n" for i in range(30)},
    }
    result = RepoMap().build(sources, "修复 登录", budget)
    assert len(tiktoken.get_encoding("cl100k_base").encode(result, disallowed_special=())) <= budget
    if budget >= 128:
        assert "模块/接口.py" in result
        assert "登录" in result
        assert result.startswith("<repository_map>") and result.endswith("</repository_map>")


def test_references_and_task_personalization_change_selection():
    sources = {
        "a_unused.py": "def unused(): pass\n",
        "z_shared.py": "def shared(): pass\n",
        **{f"caller_{i}.py": "shared()\n" for i in range(8)},
    }
    neutral = RepoMap().build(sources, map_tokens=65)
    assert "z_shared.py" in neutral
    assert "a_unused.py" not in neutral
    focused = RepoMap().build(sources, "Fix unused in a_unused.py", map_tokens=65)
    assert "a_unused.py" in focused
    assert "z_shared.py" not in focused


def test_cache_invalidation_determinism_and_filename_fallback():
    mapper = RepoMap()
    sources = {"b.py": "def b(): pass\n", "a.py": "def a(): pass\n"}
    assert mapper.build(sources) == mapper.build(dict(reversed(list(sources.items()))))
    cached = mapper._cache["a.py"][1]
    mapper.build({"a.py": sources["a.py"], "c.py": "def c(): pass\n"})
    assert mapper._cache["a.py"][1] is cached
    assert "b.py" not in mapper._cache
    assert "def changed" in mapper.build({"a.py": "def changed(): pass\n"})
    huge = "def long_function(\n" + "    argument: str,\n" * 100 + "): pass\n"
    result = mapper.build({"huge.py": huge}, map_tokens=75)
    assert "huge.py:" in result and "argument" not in result
    assert mapper.build({}, map_tokens=1024) == ""


def test_full_map_includes_all_paths_and_symbols_without_ranking_or_token_selection():
    sources = {
        "README.md": "",
        "empty.py": "",
        "calls.py": "function_99()\n",
        "模块/接口.py": 'class 接口:\n    def 登录(self, name="<|endoftext|>"):\n        return name\n',
        **{f"module_{i}.py": f"def function_{i}(): pass\n" for i in range(100)},
    }
    mapper = RepoMap()
    with patch.object(mapper, "_rank", side_effect=AssertionError("A full map must not rank definitions")):
        result = mapper.build(sources, "only function_0", map_tokens=1, mode="full")
        assert result == mapper.build(dict(reversed(list(sources.items()))), "登录", mode="full")
    assert mapper.token_count(result) > 1024
    assert result.startswith("<repository_map>") and result.endswith("</repository_map>")
    for path in sources:
        assert f"{path}:\n" in result
    for index in range(100):
        assert f"def function_{index}():" in result
    assert "class 接口:" in result and "def 登录" in result


def test_full_map_cache_handles_updates_deletions_and_disabled_or_empty_maps():
    mapper = RepoMap()
    sources = {"a.py": "def a(): pass\n", "b.py": "def b(): pass\n"}
    mapper.build(sources, mode="full")
    cached = mapper._cache["a.py"][1]
    mapper.build({"a.py": sources["a.py"]}, mode="full")
    assert mapper._cache["a.py"][1] is cached
    assert "b.py" not in mapper._cache
    assert "def changed" in mapper.build({"a.py": "def changed(): pass\n"}, mode="full")
    assert mapper.build(sources, map_tokens=0, mode="full") == ""
    assert mapper.build({}, mode="full") == ""
