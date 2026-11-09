"""Count explicit search requests from Bash syntax, excluding quoted script bodies."""

import shlex

from tree_sitter_language_pack import get_parser

TEXT_TOOLS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "ugrep", "zgrep", "ripgrep"}
FILE_TOOLS = {"find", "fd", "fdfind", "locate"}


def classify(argv):
    if not argv:
        return None
    name, *args = argv
    base = name.rsplit("/", 1)[-1]
    if base == "command":
        return None if any(a in ("-v", "-V") for a in args) else classify([a for a in args if a != "--"])
    if base in ("exec", "nohup"):
        return classify([a for a in args if a != "--"])
    if base == "timeout":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            option = args[i]
            i += 1
            if option in ("-s", "--signal", "-k", "--kill-after"):
                i += 1
        return classify(args[i + 1 :]) if i < len(args) else None
    if base in TEXT_TOOLS:
        if base == "rg" and "target/" in name:
            return None
        return ("file_locator" if base == "rg" and "--files" in args else "text_search", base)
    if base in FILE_TOOLS:
        return ("file_locator", base)
    if base == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            option = args[i]
            i += 1
            if option in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"):
                i += 1
        if i < len(args) and args[i] in ("grep", "ls-files"):
            return ("text_search" if args[i] == "grep" else "file_locator", "git " + args[i])
    return None


class SearchCounter:
    def __init__(self):
        self.parser = get_parser("bash")

    def action(self, command: str):
        raw = command.encode()
        tree = self.parser.parse(raw)
        stack, segments = [tree.root_node], []
        while stack:
            node = stack.pop()
            stack.extend(reversed(node.named_children))
            if node.type != "command" or node.child_by_field_name("name") is None:
                continue
            text = raw[node.start_byte : node.end_byte].decode()
            try:
                found = classify(shlex.split(text))
            except ValueError:
                found = None
            if found:
                segments.append({"kind": found[0], "tool": found[1], "command": text})
        return segments, tree.root_node.has_error

    def messages(self, messages: list[dict]):
        totals = {
            "search_commands": 0,
            "search_actions": 0,
            "text_search_commands": 0,
            "file_locator_commands": 0,
            "search_parse_error_actions": 0,
        }
        audit = []
        for mi, message in enumerate(messages):
            for ai, action in enumerate(message.get("extra", {}).get("actions", [])):
                segments, error = self.action(action.get("command", ""))
                totals["search_parse_error_actions"] += int(error)
                totals["search_actions"] += bool(segments)
                for segment in segments:
                    totals["search_commands"] += 1
                    totals[segment["kind"] + "_commands"] += 1
                if segments or error:
                    audit.append({"message_index": mi, "action_index": ai, "parse_error": error, "segments": segments})
        return totals, audit
