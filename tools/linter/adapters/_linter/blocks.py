from __future__ import annotations

import token
from typing import NamedTuple, TYPE_CHECKING

from . import EMPTY_TOKENS, ParseError
from .block import Block
from .python_file import PythonFile


if TYPE_CHECKING:
    from collections.abc import Sequence
    from tokenize import TokenInfo


class BlocksResult(NamedTuple):
    blocks: list[Block]
    parse_errors: dict[str, str]


def blocks(pf: PythonFile) -> BlocksResult:
    blocks: list[Block] = []
    parse_errors: dict[str, str] = {}

    def starts_block(t: TokenInfo) -> bool:
        return t.type == token.NAME and t.string in ("class", "def")

    it = (i for i, t in enumerate(pf.tokens) if starts_block(t))
    blocks = [_make_block(pf, i, parse_errors) for i in it]

    for i, parent in enumerate(blocks):
        for j in range(i + 1, len(blocks)):
            if parent.contains(child := blocks[j]):
                child.parent = i
                parent.children.append(j)
            else:
                break

    for i, b in enumerate(blocks):
        b.index = i
        parents = [b]
        while (p := parents[-1].parent) is not None:
            parents.append(blocks[p])
        parents = parents[1:]

        b.is_local = not all(p.is_class for p in parents)
        b.is_method = not b.is_class and bool(parents) and parents[0].is_class

    _add_full_names(blocks, [b for b in blocks if b.parent is None])
    return BlocksResult(blocks, parse_errors)


def _docstring(tokens: Sequence[TokenInfo], start: int) -> str:
    for i in range(start + 1, len(tokens)):
        tk = tokens[i]
        if tk.type == token.STRING:
            return tk.string
        if tk.type not in EMPTY_TOKENS:
            return ""
    return ""


def _add_full_names(
    blocks: Sequence[Block], children: Sequence[Block], prefix: str = ""
) -> None:
    # Would be trivial except that there can be duplicate names at any level
    dupes: dict[str, list[Block]] = {}
    for b in children:
        dupes.setdefault(b.name, []).append(b)

    for dl in dupes.values():
        for i, b in enumerate(dl):
            suffix = f"[{i + 1}]" if len(dl) > 1 else ""
            b.full_name = prefix + b.name + suffix

    for b in children:
        if kids := [blocks[i] for i in b.children]:
            _add_full_names(blocks, kids, b.full_name + ".")


def _make_block(pf: PythonFile, begin: int, parse_errors: dict[str, str]) -> Block:
    def next_token(start: int, token_type: int, error: str) -> int:
        for i in range(start, len(pf.tokens)):
            if pf.tokens[i].type == token_type:
                return i
        raise ParseError(pf.tokens[-1], error)

    t = pf.tokens[begin]
    category = Block.Category[t.string.upper()]
    first_token = last_token = -1
    docstring = ""
    name = "(not found)"
    try:
        ni = next_token(begin + 1, token.NAME, "Definition but no name")
        name = pf.tokens[ni].string
        # TODO: this is wrong: some blocks have no indents
        first_token = next_token(ni + 1, token.INDENT, "Definition but no indent")
        last_token = pf.indent_to_dedent[first_token]
        docstring = _docstring(pf.tokens, first_token)
    except ParseError as e:
        # This happens on the first line that `tokenizer` can't parse, and then
        # all remaining lines are skipped
        parse_errors[t.line] = " ".join(e.args)

    return Block(
        begin=begin,
        category=category,
        docstring=docstring,
        first_token=first_token,
        last_token=last_token,
        name=name,
        tokens=pf.tokens,
    )
