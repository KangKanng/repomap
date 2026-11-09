import subprocess

import pytest
from minisweagent.environments.local import LocalEnvironment

from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.source import EnvironmentSource, RepoMapError


def test_git_snapshot_cache_edits_deletions_and_ignored_files(repo):
    (repo / "ignored.py").write_text("def ignored(): pass\n")
    (repo / "source.py").write_text('MARKER = "__MINISWE_REPOMAP_V1__"\n')
    (repo / "binary.py").write_bytes(b"a\0b")
    (repo / "encoded.py").write_bytes(b"\xff")
    (repo / "link.py").symlink_to(repo / "api.py")
    subprocess.run(["git", "add", "api.py"], cwd=repo, check=True)
    source = EnvironmentSource(LocalEnvironment(cwd=str(repo)), RepoMapConfig())
    first = source.read()
    assert set(first) == {".gitignore", "api.py", "helpers.py", "README.md", "source.py"}
    assert "__MINISWE_REPOMAP_V1__" in first["source.py"]
    assert source.read() is first
    (repo / "api.py").unlink()
    (repo / "helpers.py").write_text("def changed(): pass\n")
    assert "api.py" not in source.read()
    assert source.sources["helpers.py"] == "def changed(): pass\n"
    assert source.root == str(repo)


def test_subdirectory_is_the_repository_root(repo):
    subdir = repo / "pkg"
    subdir.mkdir()
    (subdir / "inner.py").write_text("def inner(): pass\n")
    source = EnvironmentSource(LocalEnvironment(cwd=str(repo)), RepoMapConfig(root="pkg"))
    assert set(source.read()) == {"inner.py"}


def test_non_git_limits_and_symlinks(tmp_path):
    (tmp_path / "good.py").write_text("def good(): pass\n")
    (tmp_path / "huge.py").write_text("#" * 100)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("def hidden(): pass\n")
    (tmp_path / "outside.py").symlink_to("/etc/passwd")
    source = EnvironmentSource(LocalEnvironment(cwd=str(tmp_path)), RepoMapConfig(max_file_bytes=50))
    assert set(source.read()) == {"good.py"}
    assert any("max_file_bytes" in warning for warning in source.warnings)
    (tmp_path / "other.py").write_text("def other(): pass\n")
    limited = EnvironmentSource(LocalEnvironment(cwd=str(tmp_path)), RepoMapConfig(max_total_bytes=20))
    assert set(limited.read()) == {"good.py"}
    assert any("max_total_bytes" in warning for warning in limited.warnings)


def test_environment_failures_are_reported(repo):
    with pytest.raises(RepoMapError, match="collector failed"):
        EnvironmentSource(
            LocalEnvironment(cwd=str(repo)), RepoMapConfig(python_command="missing-repomap-python")
        ).read()
    with pytest.raises(RepoMapError, match="collector failed"):
        EnvironmentSource(LocalEnvironment(cwd=str(repo)), RepoMapConfig(root="missing")).read()


def test_null_scan_limits_collect_large_files_and_preserve_standard_exclusions(tmp_path):
    (tmp_path / "a.py").write_text("def a(): pass\n")
    (tmp_path / "large.py").write_text("#" * 256_001 + "\ndef large(): pass\n")
    (tmp_path / "notes.txt").write_text("notes\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.py").write_text("def hidden(): pass\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "a.py")
    bounded = EnvironmentSource(LocalEnvironment(cwd=str(tmp_path)), RepoMapConfig(max_files=1))
    assert set(bounded.read()) == {"a.py"}
    assert bounded.warnings
    unlimited = EnvironmentSource(
        LocalEnvironment(cwd=str(tmp_path)),
        RepoMapConfig(max_files=None, max_file_bytes=None, max_total_bytes=None),
    )
    assert set(unlimited.read()) == {"a.py", "large.py", "notes.txt"}
    assert "def large" in unlimited.sources["large.py"]
    assert not unlimited.warnings
    assert unlimited.read() is unlimited.sources


@pytest.mark.parametrize("limit", ["max_files", "max_file_bytes", "max_total_bytes"])
def test_each_scan_limit_can_be_disabled_independently(tmp_path, limit):
    (tmp_path / "a.py").write_text("def a(): pass\n")
    (tmp_path / "b.py").write_text("def b(): pass\n")
    settings = {limit: 1}
    bounded = EnvironmentSource(LocalEnvironment(cwd=str(tmp_path)), RepoMapConfig(**settings))
    bounded.read()
    assert any(limit in warning for warning in bounded.warnings)
    unlimited = EnvironmentSource(LocalEnvironment(cwd=str(tmp_path)), RepoMapConfig(**{limit: None}))
    assert set(unlimited.read()) == {"a.py", "b.py"}
    assert not unlimited.warnings
