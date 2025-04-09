import token
from collections.abc import Sequence
from functools import cache, cached_property
from tokenize import TokenInfo
from typing import Any, Optional, Sequence

from .python_file import PythonFile
from .python_info import FileInfo

OMIT_FIELDS = dict.fromkeys(("children", "keyword_count", "op_count"))


class MultiFile(PythonFile):
    """MultiFile keeps the tokens and other information about the file in memory while
    calculations are being performed
    """

    def __init__(self, *args: Any, omit_fields: Sequence[str] = OMIT_FIELDS, **kwargs: Any) -> None:
        super().__init__("multi_linter", *args, **kwargs)
        self.omit_fields = omit_fields

    def asdict(self, fields: Optional[Sequence[str]] = None) -> dict[str, Any]:
        if fields is None:
            fields = self._property_fields()
        return {f: v for f in fields if (v := getattr(self, f))} | self._file_info

    @cached_property
    def module_name(self) -> str:
        p = self._parent_parts
        assert self.path is not None
        if (stem := self.path.stem) != "__init__":
            p = *p, stem
        return ".".join(p)

    @cached_property
    def double_underscore_all(self) -> Sequence[str]:
        return [_join_tokens(t) for t in self.token_lines if t[0].string == "__all__"]

    @cached_property
    def _file_info(self) -> dict[str, Any]:
        return FileInfo.make(self).asdict(self.omit_fields)

    @cached_property
    def outgoing_imports(self) -> Sequence[str]:
        """Imports of other modules from this module."""

        def to_absolute(module: str) -> str:
            if not module.startswith("."):
                return module
            root = module.lstrip(".")
            dot_count = len(module) - len(root)
            end = (1 - dot_count) or None
            parts = *self._parent_parts[:end], root
            return ".".join(parts)

        def get_import(tokens: list[TokenInfo]) -> list[TokenInfo]:
            for i, t in enumerate(tokens):
                if t.type == token.INDENT:
                    continue
                if t.type == token.NAME and t.string in ("from", "import"):
                    return tokens[i:]
                break
            return []

        imports = (i for tl in self.token_lines if (i := get_import(tl)))
        splits = (_split_import(_join_tokens(i)) for i in imports)
        return sorted(to_absolute(i) for s in splits for i in s)

    @staticmethod
    @cache
    def _property_fields() -> tuple[str, ...]:
        items = ((k, v) for k, v in vars(MultiFile).items() if not k.startswith("_"))
        items = (k for k, v in items if isinstance(v, cached_property))
        return "path", *items

    @cached_property
    def _parent_parts(self) -> Sequence[str]:
        assert self.path and not self.path.is_absolute()
        return [i.name for i in reversed(self.path.parents) if i.name]


def _join_tokens(tl: Sequence[TokenInfo]) -> str:
    # Gets rid of carriage returns and indents
    lines = {j.start[0]: j.line for j in tl}
    return " ".join(" ".join(lines.values()).split()).strip()


def _split_import(line: str) -> Sequence[str]:
    """
    Split an import line.

      "import math.sqrt" -> ["math.sqrt"]
      "from .a.b import fix, break, lose" -> [".a.b.fix", '.a.b.break", ".a.b.lose"]

    """
    line = line.partition("#")[0]
    if "*" in line:
        return []

    source, _, symbols = line.partition(" import ")
    if symbols:
        _from, *src = source.split()
        assert _from == "from", f"{line=}"
        assert len(src) == 1 or (len(src) == 2 and src[0] == "."), f"{line=}"
        source = "".join(src)
    else:
        source, _, symbols = line.partition("import ")
        assert not source, f"{line=}"

    # Example: 'import torch._refs as refs, math as _math, json'
    symbols = symbols.replace("(", " ").replace(")", " ").strip()
    dot = (source and not source.endswith(".") and ".") or ""
    parts = (s for i in symbols.split(",") if (s := i.split()))
    return [f"{source}{dot}{p[0]}" for p in parts]
