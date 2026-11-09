from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter_language_pack import get_parser

LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}
DEFINITIONS = {
    "class_definition",
    "function_definition",
    "class_declaration",
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "method_signature",
    "function_signature",
    "method_declaration",
    "type_spec",
    "function_item",
    "struct_item",
    "enum_item",
    "trait_item",
    "type_item",
    "constructor_declaration",
    "struct_specifier",
    "class_specifier",
    "type_definition",
    "struct_declaration",
    "method",
    "singleton_method",
    "class",
    "module",
}
SCOPES = {
    "class_definition",
    "class_declaration",
    "interface_declaration",
    "class_specifier",
    "struct_item",
    "trait_item",
    "class",
    "module",
    "impl_item",
}
IDENTIFIERS = {"identifier", "type_identifier", "field_identifier", "property_identifier", "constant"}


@dataclass(frozen=True)
class Symbol:
    name: str
    line: int
    lines: tuple[tuple[int, str], ...]


@dataclass
class FileSymbols:
    symbols: list[Symbol]
    references: Counter[str]


def _name_node(node):
    name = node.child_by_field_name("name")
    if name is not None:
        return name
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type in IDENTIFIERS:
            return declarator
        declarator = declarator.child_by_field_name("declarator")
    return None


def _signature(node, source: bytes) -> tuple[tuple[int, str], ...]:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    start = node.parent if node.parent is not None and node.parent.type == "decorated_definition" else node
    text = source[start.start_byte : end].decode("utf-8", errors="replace").rstrip()
    if body is not None and source[body.start_byte : body.start_byte + 1] == b"{":
        text += " {"
    lines = text.splitlines()[:12]
    if lines:
        lines[0] = " " * start.start_point.column + lines[0]
    return tuple((start.start_point.row + i + 1, line[:240]) for i, line in enumerate(lines))


def extract_symbols(path: str, source: str) -> FileSymbols:
    language = LANGUAGES.get(PurePosixPath(path).suffix.lower())
    if not language or not source:
        return FileSymbols([], Counter())
    data = source.encode("utf-8")
    tree = get_parser(language).parse(data)
    symbols, references, names = [], Counter(), set()
    stack = [(tree.root_node, ())]
    while stack:
        node, parents = stack.pop()
        is_arrow = node.type == "variable_declarator" and (
            (value := node.child_by_field_name("value")) is not None
            and value.type in {"arrow_function", "function_expression", "generator_function"}
        )
        if node.type in DEFINITIONS or is_arrow:
            if (name := _name_node(node)) is not None:
                names.add((name.start_byte, name.end_byte))
                signature_node = node.child_by_field_name("value") if is_arrow else node
                lines = _signature(signature_node, data)
                if is_arrow:
                    lines = (
                        (
                            node.start_point.row + 1,
                            " " * node.start_point.column
                            + data[node.start_byte : name.end_byte].decode()
                            + " = "
                            + lines[0][1].lstrip(),
                        ),
                        *lines[1:],
                    )
                symbols.append(
                    Symbol(data[name.start_byte : name.end_byte].decode(), node.start_point.row + 1, parents + lines)
                )
                if node.type in SCOPES:
                    parents += lines
        elif node.type == "impl_item":
            parents += _signature(node, data)
        if node.type in IDENTIFIERS and (node.start_byte, node.end_byte) not in names:
            references[data[node.start_byte : node.end_byte].decode()] += 1
        stack.extend((child, parents) for child in reversed(node.named_children))
    return FileSymbols(symbols, references)
