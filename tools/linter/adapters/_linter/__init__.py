from __future__ import annotations

import dataclasses
import token
from functools import cache
from pathlib import Path
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence
    from tokenize import TokenInfo


__all__ = (
    "Block",
    "FileLinter",
    "LineWithSets",
    "LintResult",
    "MultiFile",
    "ParseError",
    "PythonFile",
    "ROOT",
    "ScopeInfo",
)

NO_TOKEN = -1

# Python 3.12 and up have two new token types, FSTRING_START and FSTRING_END
_START_OF_LINE_TOKENS = token.DEDENT, token.INDENT, token.NEWLINE
_NON_CODE_TOKENS = token.COMMENT, token.ENDMARKER, token.ENCODING, token.NL
_IGNORED_TOKENS = dict.fromkeys(_START_OF_LINE_TOKENS + _NON_CODE_TOKENS)


def is_ignored_token(t: TokenInfo) -> bool:
    return t.type in _IGNORED_TOKENS


_LINTER = Path(__file__).absolute().parents[0]
ROOT = _LINTER.parents[3]


class ParseError(ValueError):
    def __init__(self, token: TokenInfo, *args: str) -> None:
        super().__init__(*args)
        self.token = token


from .block import Block
from .file_linter import FileLinter
from .messages import LintResult
from .multi_file import MultiFile
from .python_file import PythonFile
from .python_info import FileInfo
