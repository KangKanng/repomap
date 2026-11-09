import subprocess
import sys
import textwrap

import pytest

from mini_swe_repomap.symbols import extract_symbols


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "api.py",
            "class API:\n    def run(self, value: str) -> str:\n        return normalize(value)\n",
            {"API", "run"},
        ),
        (
            "api.js",
            "class API { run(value) { return normalize(value); } }\nfunction start() { return new API(); }",
            {"API", "run", "start"},
        ),
        (
            "api.ts",
            "interface API { run(value: string): string; }\nconst start = (value: string): string => normalize(value);",
            {"API", "run", "start"},
        ),
        ("api.tsx", "function App() { return <div>Hello</div>; }", {"App"}),
        (
            "api.go",
            "package main\ntype API struct {}\nfunc (a API) Run(v string) string { return normalize(v) }",
            {"API", "Run"},
        ),
        ("api.rs", "struct API {}\nimpl API { fn run(&self, v: &str) { normalize(v); } }", {"API", "run"}),
        ("API.java", "class API { public String run(String v) { return normalize(v); } }", {"API", "run"}),
        ("api.c", "int normalize(int x) { return x; }\nint main() { return normalize(1); }", {"normalize", "main"}),
        ("api.cpp", "class API { public: int run(int v) { return normalize(v); } };", {"API", "run"}),
        ("API.cs", "class API { public string Run(string v) { return Normalize(v); } }", {"API", "Run"}),
        ("api.rb", "class API\n  def run(v)\n    normalize(v)\n  end\nend", {"API", "run"}),
        ("api.php", "<?php class API { public function run($v) { return normalize($v); } }", {"API", "run"}),
    ],
)
def test_supported_languages(path, source, expected):
    tags = extract_symbols(path, source)
    assert {symbol.name for symbol in tags.symbols} == expected
    assert all(symbol.lines and symbol.line > 0 for symbol in tags.symbols)
    assert any(
        "run" in text.lower() or "main" in text.lower() or "App" in text for s in tags.symbols for _, text in s.lines
    )


def test_multiline_signatures_scopes_and_references():
    tags = extract_symbols(
        "api.py",
        '# fake_call()\nclass API:\n    def run(\n        self, value: str,\n    ) -> str:\n        "normalize fake_call"\n        return normalize(value)\n',
    )
    method = next(symbol for symbol in tags.symbols if symbol.name == "run")
    assert method.line == 3
    assert dict(method.lines) == {
        2: "class API:",
        3: "    def run(",
        4: "        self, value: str,",
        5: "    ) -> str:",
    }
    assert tags.references["normalize"] == 1
    assert not tags.references["fake_call"]
    assert not tags.references["run"]


def test_syntax_errors_and_unknown_files():
    assert extract_symbols("broken.py", "def valid():\n    pass\n\ndef broken(\n").symbols[0].name == "valid"
    assert not extract_symbols("README.md", "def fake(): pass").symbols


def test_decorators_and_inline_type_context_are_preserved():
    python = extract_symbols(
        "api.py", "class API:\n    @classmethod\n    def create(cls, value: str):\n        return cls(value)\n"
    )
    assert dict(python.symbols[1].lines)[2] == "    @classmethod"
    from mini_swe_repomap.repomap import RepoMap

    result = RepoMap().build({"API.java": "class API { public String run(String v) { return normalize(v); } }"})
    assert "class API" in result and "public String run(String v)" in result
    assert "return normalize" not in result


def test_repeated_medium_files_in_subprocess():
    script = textwrap.dedent("""
        import gc
        from mini_swe_repomap.symbols import extract_symbols
        for batch in range(24):
            source = "".join(
                f"class API_{batch}_{i}:\\n"
                "    @classmethod\\n"
                "    def run(cls, value: str) -> str:\\n"
                "        return normalize(value)\\n\\n"
                for i in range(160)
            )
            tags = extract_symbols(f"module_{batch}.py", source)
            assert len(tags.symbols) == 320
            assert tags.references["normalize"] == 160
            gc.collect()
    """)
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
