import itertools
from functools import cached_property
import json
import keyword
import sys
import token
from pathlib import Path
from tokenize import generate_tokens, TokenInfo
from typing import Any, Iterator, Sequence, TYPE_CHECKING
import dataclasses as dc


_FILE = Path(__file__).absolute()
_PATH = [Path(p).absolute() for p in sys.path]

if TYPE_CHECKING or _FILE.parent not in _PATH:
    from . docstring_linter import Block, DocstringFile, file_summary
else:
    from docstring_linter import Block, DocstringFile, file_summary


@dc.dataclass
class AllFiles:
    files: Sequence[str]

    def asdict(self) -> dict[str, dict[str, Any]]:
        def asdict(file: str, d: [str, Any]) -> dict[str, Any]:
            if incoming := self.incoming_imports.get(file):
                d["incoming_imports"] = incoming
            return d

        return {k: asdict(k, v) for k, v in self.info.items()}

    @cached_property
    def info(self) -> dict[str, dict[str, Any]]:
        def expand(s: str) -> Iterator[Path]:
            if any(i in s for i in "*?["):
                yield from Path().glob(s)
            else:
                yield Path(s)

        expanded = sorted({p for f in self.files for p in expand(f)})
        info = (_PythonFile(p).asdata() for p in expanded)
        return {i.pop("module_name"): i for i in info}

    @cached_property
    def incoming_imports(self) -> dict[str, dict[str, list[str]]]:
        """The inverse of all the self.info.outgoing_imports tables"""
        imports: dict[str, dict[str, list[str]]] = {}

        for name, i in self.info.items():
            for out in i.get("outgoing_imports", []):
                if out in self.info or "." not in out:
                    root, symbol = out, "(module)"
                else:
                    root, _, symbol = out.rpartition(".")
                imports.setdefault(root, {}).setdefault(symbol, []).append(name)

        return imports


"""

Full list of features

Per file
    file size
    imports
        incoming
        outgoing
   __all__
   module level file #ignores


Per file and per block:
    count lines
    count tokens
    number of comments
    length of comments
    number of subblocks
    (number of uses of `set`)
    # type: ignores

    counts of keywords (`import keywords`)

"""


@dc.dataclass
class PythonCodeInfo:
    """This is information that is common to both files and blocks"""
    line_count: int = 0
    token_count: int = 0
    comment_count: int = 0
    comment_total_length: int = 0
    block_count: int = 0

    keyword_counts: dict[str, int] = dc.field(default_factory=dict)
    noqas: dict[str, int] = dc.field(default_factory=dict)
    type_ignores: dict[str, int] = dc.field(default_factory=dict)

    def apply_token(self, token: TokenInfo) -> None:
        def count(d: dict[str, int], s: str) -> None:
            for i in s.split(","):
                if i := i.strip()
                    d[i] = 1 + d.get(i, 0)

        def extract(begin: str, end: str) -> dict[int, Sequence[str]]:
            return token.string.partition(begin)[2].partition(end)[0]

        self.token_count += 1
        s = token.string

        if token.type == tokens.COMMENT:
            self.comment_count += 1
            self.comment_total_length += len(s.partition("#")[2])

            count(self.noqas, s.partition("# noqa: ")[2])
            count(
                self.type_ignores,
                s.partition("# type: ignore[")[2].partition("]")[0]
            )
        elif token.type == tokens.NAME and keyword.iskeyword(s := token.string):
            self.keyword_counts[s] = 1 + self.keyword_counts.get(s, 0)

    def set_line_count(self, begin: TokenInfo, end: TokenInfo) -> None:
        self.line_count = 1 + end.end[0] - begin.begin[0]


@dc.dataclass
class PythonFileInfo(PythonCodeInfo):
    file_size: int = 0
    incoming_imports: dict[str, dict[str, list[str]]] = dc.field(default_factory=dict)
    outgoing_imports: list[str] = dc.field(default_factory=list)
    double_underscore_all_lines: list[str] = dc.field(default_factory=list)
    top_level_noqas: dict[str, int] = dc.field(default_factory=dict)
    top_level_type_ignores: dict[str, int] = dc.field(default_factory=dict)


class _PythonFile(DocstringFile):
    """_PythonFile keeps the tokens and other information about the file in memory while
    calculations are being performed
    """
    def __init__(self, *a: Any, **ka: Any) -> None:
        super().__init__("multi_linter", *a, **ka)

        assert self.path and not self.path.is_absolute()
        self._parent_parts = [i.name for i in reversed(self.path.parents) if i.name]

    def asdata(self) -> dict[str, Any]:
        items = ((k, v) for k, v in vars(_PythonFile).items())
        keys = (k for k, v in items if isinstance(v, cached_property))
        return {k: v for k in ('path', *keys) if (v := getattr(self, k))}

    @cached_property
    def module_name(self) -> str:
        p = tuple(self._parent_parts)
        if (stem := self.path.stem) != "__init__":
            p = *p, stem
        return ".".join(p)

    @cached_property
    def block_data(self) -> Sequence[dict[str, Any]]:
        return file_summary([b.as_data() for b in self.blocks])

    @cached_property
    def double_underscore_all(self) -> Sequence[str]:
        return [_join_tokens(t) for t in self.token_lines if t[0].string == "__all__"]

    @cached_property
    def noqa(self) -> dict[int, Sequence[str]]:
        return self._comments(" noqa:", "#")

    @cached_property
    def outgoing_imports(self) -> Sequence[str]:
        """Imports of other modules from this module."""
        names = (t for t in self.token_lines if t[0].type == token.NAME)
        imports = (t for t in names if t[0].string in ("from", "import"))
        return sorted(self._fix_module(s) for t in imports for s in _split_import(t))

    @cached_property
    def type_ignore(self) -> dict[int, Sequence[str]]:
        return self._comments(" type: ignore[", "]")

    def _comments(self, begin: str, end: str) -> dict[int, Sequence[str]]:
        def extract(t: TokenInfo) -> str:
            return t.string.partition(begin)[2].partition(end)[0]

        comments = (t for t in self.tokens if t.type == token.COMMENT)
        tr = ((t, r) for t in comments if (r := extract(t)))
        return {t.start[0]: [i.strip() for i in r.split(",")] for t, r in tr}

    def _fix_module(self, module: str) -> str:
        root = module.lstrip(".")
        if not (diff := len(module) - len(root)):
            return module
        return ".".join(self._parent_parts[:(1 - diff) or None] + [root])


def _join_tokens(tl: Sequence[TokenInfo]) -> str:
    # Gets rid of carriage returns and indents
    lines = {j.start[0]: j.line for j in tl}
    return " ".join(" ".join(lines.values()).split()).strip()


def _split_import(tl: Sequence[TokenInfo]) -> Sequence[str]:
    """
    Split an import line.

      "import math.sqrt" -> ["math.sqrt"]
      "from .a.b import fix, break, lose" -> [".a.b.fix", '.a.b.break", ".a.b.lose"]

    """
    line = _join_tokens(tl)
    if "*" in line:
        return []
    words = line.split()
    loc = words.index("import")
    before = " ".join(words[0:loc])
    after = " ".join(words[loc + 1:])
    assert after, line
    after = after.replace("(", "").replace(")", "").strip()
    parts = [ps.split()[0] for p in after.split(",") if (ps := p.strip())]

    if before:
        before, _, imp = before.strip().partition("from ")
        assert not before, f"{before=}, {line[:120]=}"
        parts = [f"{imp}.{p}" for p in parts]

    return parts


if __name__ == '__main__':
    all_files = AllFiles(sys.argv[1:])
    print(json.dumps(all_files.asdict(), indent=2, default=str))
