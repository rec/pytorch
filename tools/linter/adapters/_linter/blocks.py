from __future__ import annotations

import token
from typing import NamedTuple, TYPE_CHECKING

from . import EMPTY_TOKENS, ParseError
from .block import Block
from .python_file import PythonFile


if TYPE_CHECKING:
    from collections.abc import Sequence
    from tokenize import TokenInfo


def blocks(pf: PythonFile) -> list[Block]:
    blocks: list[Block] = []

    def starts_block(t: TokenInfo) -> bool:
        return t.string in ("class", "def")

    it = (i for i, t in enumerate(pf.tokens) if starts_block(t))
    blocks = [_make_block(pf, i) for i in it]

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
    return blocks


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


def _make_block(pf: PythonFile, begin: int) -> Block:
    end = 0
    name = ""
    docstring = ""

    for i in range(begin, len(pf.tokens)):
        t = pf.tokens[i]
        if not name and t.type == token.NAME:
            name = t.string
        elif not end and t.type == token.INDENT:
            end = pf.indent_to_dedent[i] - 1
        elif not end and t.string == "...":
            end = i
        elif t.type == token.STRING:
            docstring = t.string
            break
        elif t.type not in EMPTY_TOKENS:
            break

    category = Block.Category[pf.tokens[begin].string.upper()]
    return Block(
        begin=begin,
        category=category,
        docstring=docstring,
        last_token=end,
        name=name,
        tokens=pf.tokens,
    )
