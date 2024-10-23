from __future__ import annotations
import token
from pathlib import Path
from tokenize import TokenInfo
import tokenize
from typing import List, Sequence

from .match_tokens import match_set_tokens


OMIT_COMMENT = "# noqa: set_linter"

"""
Python's tokenizer splits Python code into lexical tokens tagged with one of many
token names. We are only interested in a few of these: references to the built-in `set`
will have to be in a NAME token, and we're only care about enough context to see if it's a
really `set` or, say, a method `set`.
"""

def split_lines(lines: Sequence[str]) -> List[str]:
    return [s for i in lines for s in i.splitlines(keepends=True)]


def generate_tokens(lines: Sequence[str]) -> List[TokenInfo]:
    return list(tokenize.generate_tokens(iter(lines).__next__))


class PythonFile:
    path: Path
    lines: List[str]
    tokens: List[TokenInfo]
    token_lines: List[List[TokenInfo]]
    set_tokens: List[TokenInfo]

    @staticmethod
    def create(path: Path) -> 'PythonFile':
        return PythonFile(path.read_text())

    def __init__(self, *lines: str) -> None:
        self.lines = split_lines(lines)
        self.tokens = generate_tokens(self.lines)

        self.token_lines = [[]]
        for t in self.tokens:
            self.token_lines[-1].append(t)
            if t.type == token.NEWLINE:
                self.token_lines.append([])

        self.omitted = OmittedLines(self.lines)
        lines = [tl for tl in self.token_lines if not self.omitted(tl)]
        self.set_tokens = [t for tl in lines for t in match_set_tokens(tl)]


class OmittedLines:
    def __init__(self, lines: List[str]) -> None:
        self.lines = lines
        self.omitted = {i + 1 for i, s in enumerate(lines) if s.rstrip().endswith(OMIT_COMMENT)}

    def __call__(self, tokens: List[TokenInfo]) -> bool:
        # A token_line might span multiple physical lines
        lines = sorted(i for t in tokens for i in (t.start[0], t.end[0]))
        lines_covered = list(range(lines[0], lines[-1] + 1)) if lines else []
        return bool(self.omitted.intersection(lines_covered))
