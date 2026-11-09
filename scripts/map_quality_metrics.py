"""Post-hoc map metrics. Semantic references require reviewed annotations."""

import ast
import re
import shlex
from pathlib import PurePosixPath


def relative_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
        raise ValueError(f"Expected a repository-relative path: {value!r}")
    return path.as_posix()


def patch_path(value):
    value = value.split("\t", 1)[0]
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        value = ast.literal_eval(value)
        try:
            value = value.encode("latin-1").decode("utf-8")
        except (UnicodeError, UnicodeEncodeError):
            pass
    return relative_path(value[2:] if value.startswith(("a/", "b/")) else value)


def gold_files(patch):
    """Read official source-patch paths, including rename/add/delete metadata."""
    files, current, in_hunk = [], None, False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            words = shlex.split(line)
            current = {"base_path": None, "new_path": None, "hunks": []}
            in_hunk = False
            if len(words) == 4:
                current.update(base_path=patch_path(words[2]), new_path=patch_path(words[3]))
            files.append(current)
        elif current is not None:
            if in_hunk and not line.startswith("@@"):
                continue
            if line.startswith("--- "):
                current["base_path"] = patch_path(line[4:])
            elif line.startswith("+++ "):
                current["new_path"] = patch_path(line[4:])
            elif line.startswith("new file mode "):
                current["base_path"] = None
            elif line.startswith("deleted file mode "):
                current["new_path"] = None
            elif line.startswith("rename from "):
                current["base_path"] = relative_path(line[len("rename from ") :])
            elif line.startswith("rename to "):
                current["new_path"] = relative_path(line[len("rename to ") :])
            elif match := re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)", line):
                in_hunk = True
                current["hunks"].append(
                    {"old_line": int(match[1]), "new_line": int(match[3]), "context": match[5].strip()}
                )
    for entry in files:
        if entry["base_path"] is None and entry["new_path"] is None:
            raise ValueError("Patch contains a file with no recognizable path")
        entry["map_path"] = entry["base_path"] or entry["new_path"]
        entry["change"] = (
            "added" if entry["base_path"] is None else "deleted" if entry["new_path"] is None else "modified"
        )
        if entry["base_path"] and entry["new_path"] and entry["base_path"] != entry["new_path"]:
            entry["change"] = "renamed"
    return files


def parse_map(text):
    files, lines, current = set(), {}, None
    for raw in text.splitlines():
        if raw.endswith(":") and not raw.startswith(("│", "⋮", "<")):
            current = relative_path(raw[:-1])
            files.add(current)
            lines.setdefault(current, {})
        elif current and (match := re.match(r"│(\d+): ?(.*)", raw)):
            lines[current].setdefault(int(match[1]), []).append(match[2])
    return {"files": files, "lines": lines}


def fraction(hits, denominator, *, status="complete"):
    return {
        "numerator": hits,
        "denominator": denominator,
        "value": hits / denominator if denominator else None,
        "status": status if denominator else "not_applicable",
    }


def pending(reason="reviewed reference labels required"):
    return {"numerator": None, "denominator": None, "value": None, "status": "pending_annotation", "reason": reason}


def visible(view, item):
    path = relative_path(item["path"])
    if item.get("kind") == "file":
        return path in view["files"]
    if not item.get("id") or not item.get("name") or not isinstance(item.get("line"), int) or item["line"] < 1:
        raise ValueError("A symbol needs id, path, name and a positive base-source declaration line")
    texts = view["lines"].get(path, {}).get(item["line"], [])
    token = re.compile(r"(?<![\w$])" + re.escape(item["name"]) + r"(?![\w$])")
    signature = item.get("declaration")
    return any(token.search(text) and (not signature or signature in text) for text in texts)


def reviewed_recall(view, annotation, *, allow_excluded=True):
    if not annotation.get("reviewed"):
        return pending()
    excluded = [item for item in annotation.get("items", []) if not item.get("eligible", True)]
    if excluded and (not allow_excluded or any(not item.get("exclusion_reason") for item in excluded)):
        raise ValueError("Excluded references need reasons; in-repository target symbols cannot be excluded")
    items = [item for item in annotation.get("items", []) if item.get("eligible", True)]
    identifiers = [item["id"] for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Reference IDs must be unique")
    hits = [item["id"] for item in items if visible(view, item)]
    return fraction(len(hits), len(items)) | {
        "hit_ids": hits,
        "missed_ids": sorted(set(identifiers) - set(hits)),
        "excluded_ids": [item["id"] for item in excluded],
    }


def precision(view, annotation):
    items = annotation.get("items", [])
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError("Map inventory symbol IDs must be unique")
    if any(not visible(view, item) for item in items):
        raise ValueError("Map inventory includes symbols whose declarations are not visible in the actual map")
    reviewed = annotation.get("relevance_reviewed", False)
    labels = [item.get("relevance", "unknown") if reviewed else "unknown" for item in items]
    if any(label not in {"relevant", "irrelevant", "unknown"} for label in labels):
        raise ValueError("Symbol relevance must be relevant, irrelevant or unknown")
    relevant, irrelevant = labels.count("relevant"), labels.count("irrelevant")
    unknown = labels.count("unknown")
    complete = annotation.get("inventory_complete", False)
    result = (
        fraction(relevant, len(items))
        if complete and not unknown
        else pending("complete inventory and relevance review required")
    )
    return result | {
        "inventory_complete": complete,
        "inventory_size": len(items),
        "relevant": relevant,
        "irrelevant": irrelevant,
        "unknown": unknown,
        "labeled_subset_precision": fraction(relevant, relevant + irrelevant),
        "label_coverage": (relevant + irrelevant) / len(items) if complete and items else None,
        "bounds": [relevant / len(items), (relevant + unknown) / len(items)] if complete and items else None,
    }


def utilization(view, annotation, messages, complete_trace):
    if not annotation.get("reviewed"):
        return pending("trace annotation required") | {"confirmed_reference": None, "can_observe": complete_trace}
    status = annotation.get("status", "unknown")
    if status not in {"confirmed_reference", "consistent", "not_observed", "unknown"}:
        raise ValueError("Unknown utilization status")
    if status == "not_observed" and not complete_trace:
        raise ValueError("Cannot mark an incomplete trajectory not_observed")
    events = annotation.get("events", [])
    if status == "confirmed_reference":
        if not events:
            raise ValueError("Confirmed use requires a map reference followed by an evidenced action")
        for event in events:
            ri, ai = event["reference_message_index"], event["action_message_index"]
            if not (0 <= ri <= ai < len(messages)):
                raise ValueError("Utilization evidence has invalid temporal ordering")
            reference = messages[ri]
            quote = event.get("quote", "")
            if reference.get("role") != "assistant" or not quote or quote not in reference.get("content", ""):
                raise ValueError("Map-reference quote not present in the cited assistant message")
            if not event.get("explicit_map_reference"):
                raise ValueError("Matching a path alone does not establish map utilization")
            if not re.search(r"\bmap\b|repomap|repository_map|地图", quote, re.I):
                raise ValueError("Confirmed reference quote must explicitly mention the map")
            if not visible(view, event["map_item"]):
                raise ValueError("Utilized item is not visible in the supplied map")
            actions = messages[ai].get("extra", {}).get("actions", [])
            index = event["action_index"]
            if messages[ai].get("role") != "assistant" or not (0 <= index < len(actions)):
                raise ValueError("Cited locating/reading action does not exist")
            command = actions[index].get("command", "")
            if not event.get("action_contains") or event["action_contains"] not in command:
                raise ValueError("Cited action excerpt does not match the recorded command")
            if event.get("operation") not in {"locate", "read"}:
                raise ValueError("Utilization needs a locating or reading action")
    # Other states describe observability; absence of evidence is not actual non-use.
    return {
        "value": 1.0 if status == "confirmed_reference" else None,
        "status": status,
        "confirmed_reference": status == "confirmed_reference",
        "can_observe": complete_trace,
        "evidence_count": len(events),
        "causal_effect_established": False,
    }


def evaluate(text, reference_files, annotation, messages, complete_trace):
    view = parse_map(text)
    targets = {item["map_path"] for item in reference_files}
    hit_files = sorted(targets & view["files"])
    existing = {item["map_path"] for item in reference_files if item["base_path"] is not None}
    extra = annotation.get("additional_target_files", {})
    augmented = pending("task-related additional files need review")
    if extra.get("reviewed"):
        if any(not item.get("reason") or not item.get("evidence") for item in extra.get("items", [])):
            raise ValueError("Additional task files need reasons and task/source evidence")
        expanded = targets | {relative_path(item["path"]) for item in extra.get("items", [])}
        augmented = fraction(len(expanded & view["files"]), len(expanded))
    return {
        "target_file_recall": fraction(len(hit_files), len(targets))
        | {
            "hit_files": hit_files,
            "missed_files": sorted(targets - view["files"]),
            "new_files_not_present_at_base": sorted(
                item["map_path"] for item in reference_files if item["base_path"] is None
            ),
            "existing_target_file_recall": fraction(len(existing & view["files"]), len(existing)),
        },
        "target_symbol_recall": reviewed_recall(view, annotation.get("target_symbols", {}), allow_excluded=False),
        "augmented_target_file_recall": augmented,
        "dependency_coverage": reviewed_recall(view, annotation.get("dependencies", {})),
        "symbol_precision": precision(view, annotation.get("map_symbols", {})),
        "map_utilization": utilization(view, annotation.get("utilization", {}), messages, complete_trace),
        "map_files": sorted(view["files"]),
    }
