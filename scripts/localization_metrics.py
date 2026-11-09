"""Post-hoc localization evidence from explicit reads and model-visible tool outputs."""

import re
import shlex
from collections import Counter
from pathlib import PurePosixPath

from search_metrics import SearchCounter


def normalized(text):
    return " ".join(text.split())


def reference_regions(files, sources):
    """Keep unique baseline code lines near official old-side hunk starts; never used in inference."""
    result = {}
    for file in files:
        path = file["base_path"]
        if path is None or not sources.get(path):
            continue
        lines = sources[path].splitlines()
        occurrences = Counter(normalized(line) for line in lines)
        candidates = {}
        for hunk in file["hunks"]:
            center = hunk["old_line"] - 1
            for index in range(max(0, center - 12), min(len(lines), center + 13)):
                line = normalized(lines[index])
                if (
                    len(line) >= 24
                    and len(re.findall(r"[A-Za-z_]\w*", line)) >= 3
                    and occurrences[line] == 1
                    and not line.startswith(("#", "//", "/*", "*"))
                ):
                    candidates[index + 1] = line
        if candidates:
            result[path] = [{"line": line, "text": text} for line, text in sorted(candidates.items())]
    return result


def explicit_reads(counter, command, targets):
    raw = command.encode()
    nodes, stack = [], [counter.parser.parse(raw).root_node]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        if node.type == "command":
            nodes.append(node)
    cwd, reads = PurePosixPath("/testbed"), set()
    for node in nodes:
        try:
            argv = shlex.split(raw[node.start_byte : node.end_byte].decode())
        except ValueError:
            continue
        if not argv:
            continue
        name = argv[0].rsplit("/", 1)[-1]
        if name == "cd" and len(argv) == 2 and not any(c in argv[1] for c in "$~*"):
            cwd = PurePosixPath(argv[1]) if argv[1].startswith("/") else cwd / argv[1]
        if name not in {"cat", "sed", "head", "tail", "grep", "rg", "awk", "nl", "bat", "git"}:
            continue
        if name in {"grep", "rg"} and any(
            arg in {"-l", "-L", "-c", "-q", "--files", "--files-with-matches", "--count", "--quiet"} for arg in argv[1:]
        ):
            continue
        if name == "git":
            if len(argv) < 3 or argv[1] != "show":
                continue
            args = [arg.split(":", 1)[1] for arg in argv[2:] if ":" in arg]
            directory = PurePosixPath("/testbed")
        else:
            args, directory = argv[1:], cwd
        for arg in args:
            if arg.startswith("-") or any(c in arg for c in "$*?\n"):
                continue
            path = PurePosixPath(arg) if arg.startswith("/") else directory / arg
            # PurePosixPath does not collapse '..'; resolve lexically without touching host files.
            parts = []
            for part in path.parts:
                if part == "..":
                    if parts:
                        parts.pop()
                elif part not in {"/", "."}:
                    parts.append(part)
            resolved = "/" + "/".join(parts)
            if resolved.startswith("/testbed/") and resolved[len("/testbed/") :] in targets:
                reads.add(resolved[len("/testbed/") :])
    return reads


def localization(messages, regions):
    counter, calls, actions, searches = SearchCounter(), 0, 0, 0
    outputs = {m.get("tool_call_id"): m for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
    for mi, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        calls += 1
        for ai, action in enumerate(message.get("extra", {}).get("actions", [])):
            actions += 1
            command = action.get("command", "")
            segments, _ = counter.action(command)
            before = searches
            searches += len(segments)
            output = outputs.get(action.get("tool_call_id"))
            if not output or output.get("extra", {}).get("returncode") != 0:
                continue
            content = output.get("content", "")
            if not isinstance(content, str):
                continue
            visible = normalized(content)
            for path in sorted(explicit_reads(counter, command, regions)):
                for needle in regions[path]:
                    if needle["text"] in visible:
                        return {
                            "status": "observed_explicit_read",
                            "reference": "official_baseline_hunk_context",
                            "search_commands_before_action": before,
                            "search_commands_through_action": searches,
                            "assistant_calls": calls,
                            "tool_actions": actions,
                            "message_index": mi,
                            "action_index": ai,
                            "path": path,
                            "baseline_line": needle["line"],
                            "matched_text": needle["text"],
                            "command": command,
                            "boundary": "whole_tool_action",
                        }
    return {
        "status": "not_observed" if regions else "no_eligible_reference",
        "search_commands_through_action": None,
        "assistant_calls": None,
        "reference": "official_baseline_hunk_context",
    }
