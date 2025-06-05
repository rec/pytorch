import dataclasses as dc
import keyword
import token
from collections import Counter
from collections.abc import Sequence
from functools import cache
from tokenize import TokenInfo
from typing import Any

from .python_file import PythonFile


INVERSE_TOKEN_TYPES = {v: k for k, v in token.EXACT_TOKEN_TYPES.items()}
_IGNORES = {
    "mypy": "# mypy: ",
    "noqa": "# noqa: ",
    "type": "# type: ignore[",
}

# Placeholder for possible empty ignores like "# type\: ignore[]"
EMPTY_IGNORES = "(None)"


class AsData:
    def asdict(self) -> dict[str, Any]:
        d = _asdata(self)
        assert isinstance(d, dict)
        return d


@dc.dataclass
class Ignores(AsData):
    mypy: Counter[str] = dc.field(default_factory=Counter)
    noqa: Counter[str] = dc.field(default_factory=Counter)
    type: Counter[str] = dc.field(default_factory=Counter)

    def update(self, other: "Ignores") -> None:
        self.mypy.update(other.mypy)
        self.noqa.update(other.noqa)
        self.type.update(other.type)

    def apply_line(self, line: str) -> None:
        for f in dc.fields(Ignores):
            ignore = f"# {f.name}: " + (f.name == "type") * "ignore["
            _, sep, after = line.partition(ignore)
            if sep:
                split = after.partition("]")[0].split(",")
                ignores = [j for i in split if (j := i.strip())] or [EMPTY_IGNORES]
                assert all(isinstance(i, str) for i in ignores), ignores
                getattr(self, f.name).update(ignores)


@dc.dataclass
class ScopeInfo(AsData):
    """Metrics on sections of Python code"""

    display_name: str = ""
    block_count: int = 0
    line_start: int = -1
    line_count: int = 0
    token_count: int = 0

    comment_total_length: int = 0

    keyword_count: Counter[str] = dc.field(default_factory=Counter)
    op_count: Counter[str] = dc.field(default_factory=Counter)
    ignores: Ignores = dc.field(default_factory=Ignores)
    children: list[int] = dc.field(default_factory=list)

    def update(self, other: "ScopeInfo") -> None:
        self.line_start = min(self.line_start, other.line_start)

        for k, v in vars(other).items():
            if k == "line_start":
                continue
            sv = getattr(self, k)
            if isinstance(v, int):
                setattr(self, k, sv + v)
            elif isinstance(v, (Counter, Ignores)):
                sv.update(v)
            else:
                for j, u in v.items():
                    assert all(isinstance(i, str) for i in u), (j, u)
                    sv.setdefault(j, Counter()).update(u)

    def apply_token(self, tok: TokenInfo) -> None:
        self.token_count += 1
        s, t = tok.string, tok.type
        end = tok.end[0]
        if self.line_start == -1:
            self.line_start = tok.start[0]
        self.line_count = end - self.line_start

        op_name = INVERSE_TOKEN_TYPES.get(t) or token.tok_name[t].lower()
        self.op_count[op_name] += 1

        if t == token.NAME and keyword.iskeyword(s):
            self.keyword_count[s] += 1

        elif t == token.COMMENT:
            self.comment_total_length += len(s.partition("#")[2])
            self.ignores.apply_line(s)

    @staticmethod
    @cache
    def _default() -> dict[str, Any]:
        return dc.asdict(ScopeInfo())

    def asdict(self, omit: Sequence[str] = ()) -> dict[str, Any]:
        d = super().asdict()
        return {k: v for k, v in d.items() if k not in omit and v != self._default()[k]}


@dc.dataclass
class FileInfo(AsData):
    blocks: list[ScopeInfo] = dc.field(default_factory=list)
    top: ScopeInfo = dc.field(default_factory=ScopeInfo)

    def asdict(self, omit: Sequence[str] = ()) -> dict[str, Any]:
        d = {
            "top": self.top.asdict(omit),
            "blocks": [b.asdict(omit) for b in self.blocks],
        }
        return {k: v for k, v in d.items() if v}

    @staticmethod
    def make(pf: PythonFile) -> "FileInfo":
        def fill(info: ScopeInfo, begin: int, end: int, kids: Sequence[int]) -> None:
            info.children = list(kids)
            kids = info.children[::-1]

            while begin < end:
                if kids and begin >= (block := pf.blocks[kids[-1]]).begin:
                    begin = block.dedent
                    kids.pop()
                else:
                    info.apply_token(pf.tokens[begin])
                    begin += 1

        N = len(pf.blocks)
        file_info = FileInfo([ScopeInfo() for _ in range(N)])
        for info, block in reversed(list(zip(file_info.blocks, pf.blocks))):
            fill(info, block.begin, block.dedent + 1, block.children)
            info.display_name = block.display_name

        fill(file_info.top, 0, N, range(N))
        file_info.top.display_name = "(module)"
        file_info.top.children.clear()
        return file_info


def _asdata(data: Any, use_asdict: bool = False) -> Any:
    if asdict_method := use_asdict and getattr(data, "asdict", None):
        return asdict_method()

    if data is None or isinstance(data, (bool, float, int, str)):
        return data

    if isinstance(data, (tuple, list)):
        return [_asdata(d) for d in data]

    if isinstance(data, dict):
        return {k: dv for k, v in data.items() if (dv := _asdata(v))}

    try:
        fields = dc.fields(data)
    except TypeError:
        pass
    else:
        return _asdata({f.name: getattr(data, f.name) for f in fields})

    raise TypeError(f"Cannot understand {data=}, {type(data)=}")
