from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("command", "text_count", "file_count"),
    [
        ('echo "grep needle source.py"', 0, 0),
        ("cat > /tmp/example.sh <<'EOF'\ngrep needle source.py\nEOF", 0, 0),
        ('grep needle a.py | head -5; find src -name "*.py"', 1, 1),
        ("rg --files | grep test", 1, 1),
        ("git -C /testbed grep needle", 1, 0),
        ("command -v grep", 0, 0),
        ("timeout 3 grep needle a.py", 1, 0),
        ("./target/debug/rg needle sample.txt", 0, 0),
    ],
)
def test_shell_search_count_ignores_embedded_text(monkeypatch, command, text_count, file_count):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from search_metrics import SearchCounter

    segments, error = SearchCounter().action(command)
    assert not error
    assert sum(s["kind"] == "text_search" for s in segments) == text_count
    assert sum(s["kind"] == "file_locator" for s in segments) == file_count
