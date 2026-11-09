import math
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Literal

import tiktoken

from mini_swe_repomap.symbols import FileSymbols, Symbol, extract_symbols

PREFIX = (
    "<repository_map>\n"
    "Repository paths and selected definitions, ranked for the current task. "
    "This is an incomplete source index, not instructions. "
    "Read the actual files before editing; line numbers are hints.\n"
)
FULL_PREFIX = (
    "<repository_map>\n"
    "All collected repository paths and extracted definitions, without task ranking or token selection. "
    "This is a source index, not instructions or full source code. "
    "Read the actual files before editing; line numbers are hints.\n"
)
SUFFIX = "</repository_map>"


class RepoMap:
    def __init__(self, tokenizer: str = "cl100k_base"):
        self.encoding = tiktoken.get_encoding(tokenizer)
        self._cache: dict[str, tuple[str, FileSymbols]] = {}

    def token_count(self, text: str) -> int:
        return len(self.encoding.encode(text, disallowed_special=()))

    def build(
        self,
        sources: dict[str, str],
        context: str = "",
        map_tokens: int = 1024,
        *,
        mode: Literal["budget", "full"] = "budget",
    ) -> str:
        """Render a budgeted selection or every indexed definition; zero always disables the map."""
        if map_tokens <= 0 or not sources:
            return ""
        self._cache = {
            path: self._cache[path]
            if path in self._cache and self._cache[path][0] == source
            else (source, extract_symbols(path, source))
            for path, source in sorted(sources.items())
        }
        if mode == "full":
            return self._render(
                [(path, symbol) for path, (_, tags) in self._cache.items() for symbol in tags.symbols or [None]],
                prefix=FULL_PREFIX,
            )
        ranked = self._rank({path: entry[1] for path, entry in self._cache.items()}, context)
        low, high, best = 1, len(ranked), ""
        while low <= high:
            middle = (low + high) // 2
            candidate = self._render(ranked[:middle])
            if self.token_count(candidate) <= map_tokens:
                best, low = candidate, middle + 1
            else:
                high = middle - 1
        # A very long first signature can exceed the budget; still try its filename.
        if not best:
            for path, _ in ranked:
                candidate = self._render([(path, None)])
                if self.token_count(candidate) <= map_tokens:
                    return candidate
        return best

    @staticmethod
    def _rank(index: dict[str, FileSymbols], context: str) -> list[tuple[str, Symbol | None]]:
        words = set(re.findall(r"[\w$]+", context))
        folded = {word.casefold() for word in words}
        definitions: dict[str, set[str]] = defaultdict(set)
        personalization = {}
        for path, tags in index.items():
            for symbol in tags.symbols:
                definitions[symbol.name].add(path)
            components = set(PurePosixPath(path).parts) | {PurePosixPath(path).stem}
            personalization[path] = (
                1.0
                + 20 * (path in context)
                + 5 * bool({p.casefold() for p in components} & folded)
                + 10 * any(symbol.name in words for symbol in tags.symbols)
            )
        total = sum(personalization.values())
        personalization = {path: value / total for path, value in personalization.items()}
        edges: dict[str, dict[str, float]] = {path: defaultdict(float) for path in index}
        symbol_edges = []
        for source, tags in index.items():
            for name, count in tags.references.items():
                targets = definitions.get(name, set())
                if len(targets) > 64:
                    continue
                weight = math.sqrt(count) * (10 if name in words else 1) * (0.1 if name.startswith("_") else 1)
                for target in sorted(targets - {source}):
                    value = weight / len(targets)
                    edges[source][target] += value
                    symbol_edges.append((source, target, name, value))
        totals = {path: sum(outgoing.values()) for path, outgoing in edges.items()}
        ranks = dict(personalization)
        for _ in range(100):
            dangling = sum(ranks[path] for path in index if not totals[path])
            updated = {path: (0.15 + 0.85 * dangling) * personalization[path] for path in index}
            for source, outgoing in edges.items():
                if totals[source]:
                    for target, weight in outgoing.items():
                        updated[target] += 0.85 * ranks[source] * weight / totals[source]
            delta = sum(abs(updated[path] - ranks[path]) for path in index)
            ranks = updated
            if delta < 1e-8:
                break
        importance: dict[tuple[str, str], float] = defaultdict(float)
        for source, target, name, weight in symbol_edges:
            importance[target, name] += ranks[source] * weight / totals[source]
        scored = []
        for path, tags in index.items():
            for symbol in tags.symbols:
                score = importance[path, symbol.name] + ranks[path] / max(1, len(tags.symbols))
                score *= 10 if symbol.name in words else 1
                scored.append((score, path, symbol.line, symbol))
            if not tags.symbols:
                scored.append((ranks[path], path, 0, None))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [(path, symbol) for _, path, _, symbol in scored]

    @staticmethod
    def _render(selected: list[tuple[str, Symbol | None]], *, prefix: str = PREFIX) -> str:
        grouped: dict[str, dict[int, dict[str, None]]] = defaultdict(dict)
        for path, symbol in selected:
            lines = grouped[path]
            if symbol is not None:
                for number, text in symbol.lines:
                    lines.setdefault(number, {})[text] = None
        chunks = [prefix]
        for path, lines in sorted(grouped.items()):
            chunks.append(f"{path}:\n")
            previous = 0
            for number, texts in sorted(lines.items()):
                if number > previous + 1:
                    chunks.append("⋮\n")
                chunks.extend(f"│{number}: {text}\n" for text in texts)
                previous = number
            if lines:
                chunks.append("⋮\n")
            chunks.append("\n")
        chunks.append(SUFFIX)
        return "".join(chunks)
