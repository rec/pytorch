from __future__ import annotations

import argparse
import contextlib
import dataclasses as dc
import datetime
import json
import operator
import subprocess
import sys
from collections import Counter
from functools import cached_property, partial
from itertools import pairwise
from pathlib import Path
from typing import Any, Callable, cast, Iterator, Sequence, TYPE_CHECKING, TypeVar
from typing_extensions import TypeAlias


if TYPE_CHECKING:
    from collections.abc import Iterable

_T = TypeVar("_T")

_msg = partial(print, file=sys.stderr)

JSON_INDENT = 4
COMMIT_ID_LENGTH = 11  # Same as git log
MAX_ERROR_CHARS = 2_048
INTERRUPT_COUNT = 128

DESCRIPTION = """
`pyright_compare` reports on typing for function and method
return values and arguments.
""".strip()


assert sys.version_info >= (3, 10), sys.version_info

Number = int | float
NumberOrDict: TypeAlias = Number | dict[str, Number]
NumberOrDictDict: TypeAlias = dict[str, NumberOrDict]
Numbers: TypeAlias = NumberOrDict | NumberOrDictDict

Op: TypeAlias = Callable[[Number, Number], Number]

Strs: TypeAlias = list[str]
StrDict: TypeAlias = dict[str, Any]
StrDictDict: TypeAlias = dict[str, StrDict]


def main() -> None:
    sys.exit(PyrightCompare().compare())


class PyrightCompare:
    def compare(self) -> str | int:
        """Returns an error string, or 0 on success"""
        with self._git_back_to_head():
            try:
                self._compare()
            except CompareError as e:
                return " ".join(["ERROR: ", *(str(i) for i in e.args)])
            except KeyboardInterrupt:
                return "KeyboardInterrupt"
            return 0

    def _compare(self) -> None:
        if self.N < 2:
            raise CompareError("Need at least two commits to compare but have {self.N}")
        if self.args.verbose:
            _msg("Performing", self.N - 1, "comparison" + ("s" * (self.N > 2)))

        # The loop goes backward from most recent to least recent commit.
        commit_pairs = pairwise(Commit(c, self) for c in self.commit_ids)
        for index, (after, before) in enumerate(commit_pairs):
            try:
                self._compare_one(before, after, index)
            except Exception as e:
                err = (
                    f"In comparing {before.commit_id} and {after.commit_id}"
                    f" ({index} out of {self.N})"
                )
                e.args = err, *e.args
                raise

    def _compare_one(self, before: Commit, after: Commit, index: int) -> None:
        after.report  # Compute `after` first so we always move backwards in git
        if self.args.verbose:
            _msg("Comparing", before.commit_id, "to", after.commit_id, "at", index)

        file_index = self.N - index - 2 if self.args.index_invert else index
        d = self._diff(before, after, file_index)
        dkeys = d.get("diff", {}).keys()
        if self.args.write_empty or dkeys & {"absolute", "symbols"}:
            path = self.output / d["filename"]
            path.write_text(json.dumps(d, indent=JSON_INDENT))
            _msg(f"Wrote {path}")
        else:
            _msg(f"Did not write empty result for {before.commit_id}.{after.commit_id}")

    def _clean(self, x: _T) -> _T:
        if isinstance(x, dict):
            d = {k: c for k, v in x.items() if (c := self._clean(v))}
            return cast(_T, d)
        else:
            return x

    def _diff(self, before: Commit, after: Commit, index: int) -> StrDict:
        bc, ac = before.commit_id, after.commit_id
        diff = {
            "before": before.asdict(),
            "after": after.asdict(),
            "timestamp": _timestamp(),
            "filename": f"pyright_compare.{index:0{self.digits}}.{bc}-{ac}.json",
            "diff": dc.asdict(ReportDiff.create(before.report, after.report)),
        }
        return diff if self.args.keep_empty else self._clean(diff)

    def _run(self, *cmds: str, ignore_errors: bool = False, **kwargs: Any) -> str:
        cmd = " ".join(cmds).split()  # No need for shlex yet
        if self.args.verbose:
            print("$", *cmd)
        cp = subprocess.run(cmd, text=True, capture_output=True, **kwargs)

        if self.args.verbose or (not ignore_errors and cp.returncode):
            _msg(cp.stdout[:MAX_ERROR_CHARS])
            _msg(cp.stderr[:MAX_ERROR_CHARS])
        if not ignore_errors:
            cp.check_returncode()
        assert isinstance(cp.stdout, str)
        return cp.stdout.strip()

    @cached_property
    def args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser()

        help = "The last commit in the series"
        parser.add_argument("last_commit", default="HEAD", nargs="?", help=help)

        help = "The first commit to start from"
        parser.add_argument("first_commit", default="", nargs="?", help=help)

        help = "Range  of commits to compare. None means 'cover first to last'"
        parser.add_argument("-c", "--commit-range", type=int, default=0, help=help)

        help = (
            "Minimum number of digits for the index number in the file name."
            " 0 means automatic."
        )
        parser.add_argument("-d", "--index-digits", type=int, default=0, help=help)

        help = (
            "Offset for the numerical index in filenames, used to continue a previous run"
            " from where it left off"
        )
        parser.add_argument("-f", "--index-offset", type=int, default=0, help=help)

        help = "Count index numbers backwards so they decrease as time goes on"
        parser.add_argument("-i", "--index-invert", action="store_true", help=help)

        help = "Keep empty diffs (zeroes and empty lists)"
        parser.add_argument("-k", "--keep-empty", action="store_true", help=help)

        help = "Output directory for the diff files"
        parser.add_argument("-o", "--output", default="pyright_compare", help=help)

        help = (
            "How many commits to step by. None means 'compare first and last commit'"
            " if `first_commit` is set, else 1"
        )
        parser.add_argument("-s", "--step", default=None, type=int, help=help)

        help = "Print more debug info"
        parser.add_argument("-v", "--verbose", action="store_true", help=help)

        help = "Write files even when there are no type diffs"
        parser.add_argument("-w", "--write-empty", action="store_true", help=help)

        return parser.parse_args()

    @cached_property
    def commit_ids(self) -> Sequence[str]:
        if not self.args.first_commit:
            stop = self.args.commit_range
        elif self.args.commit_range:
            raise CompareError("--commit-range/-c and first_commit cannot both be set")
        elif not (
            stop := self._git_count(self.args.first_commit, self.args.last_commit)
        ):
            raise CompareError(
                f"{self.args.first_commit} isn't an ancestor of {self.args.last_commit}"
            )

        stop = stop or self.args.step or 1
        step = self.args.step or (1 if self.args.commit_range else stop)
        last_commit_id = self._git_commit_id(self.args.last_commit)[:COMMIT_ID_LENGTH]
        indices = range(self.args.index_offset, stop + 1, step)
        return [f"{last_commit_id}~{i}" for i in indices]

    @cached_property
    def digits(self) -> int:
        return self.args.index_digits or max(len(str(self.N - 1)), 3)

    @cached_property
    def N(self) -> int:
        return len(self.commit_ids)

    @cached_property
    def output(self) -> Path:
        if not (output := Path(self.args.output)).exists():
            if self.args.verbose:
                _msg("Creating directory", output)
            output.mkdir(parents=True)
        return output

    def _build_stubs(self) -> None:
        self._run("cmake --build . --target torch_python_stubs", cwd="build")

    def _git_commit_id(self, ref: str) -> str:
        return self._run("git rev-parse", ref).strip()

    def _git_commit_message(self, ref: str) -> str:
        return self._run("git log --format=%B -n 1", ref).strip()

    def _git_count(self, child: str, parent: str) -> int:
        return int(self._run(f"git rev-list --count {parent}..{child}"))

    def _git_reset(self, ref: str) -> None:
        self._run("git reset --hard", ref)

    @contextlib.contextmanager
    def _git_back_to_head(self) -> Iterator[None]:
        head = self._git_commit_id("HEAD")
        try:
            yield
        finally:
            for _ in range(INTERRUPT_COUNT):
                with contextlib.suppress(KeyboardInterrupt):
                    _msg("\nRestoring HEAD, wait a moment...")
                    self._git_reset(head)
                    break

    def _pyright(self) -> str:
        # pyright always seems to return errorcode = 1 :-/ so we ignore it
        cmd = "pyright --verifytypes torch --ignoreexternal --outputjson"
        return self._run(cmd, ignore_errors=True)


@dc.dataclass(frozen=True)
class Commit:
    name: str
    comp: PyrightCompare

    @cached_property
    def commit_id(self) -> str:
        return self.comp._git_commit_id(self.name)[:COMMIT_ID_LENGTH]

    @cached_property
    def message(self) -> str:
        return self.comp._git_commit_message(self.name).splitlines()[0]

    @cached_property
    def pyright(self) -> StrDict:
        self.comp._git_reset(self.commit_id)
        self.comp._build_stubs()
        pyright = json.loads(self.comp._pyright() or "{}")
        assert isinstance(pyright, dict)
        return pyright

    @cached_property
    def report(self) -> Report:
        return Report.create(self)

    def asdict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("name", "commit_id", "message")}


@dc.dataclass
class Report:
    """Extract information from `pyright --outputjson --verifytypes` into Python classes

    https://docs.basedpyright.com/latest/configuration/command-line/#json-output is the
    input format expected, though in fact pyright sometimes includes another
    undocumented field, "alternateNames".
    """

    commit: Commit
    numbers: NumberOrDictDict
    symbols: StrDictDict

    @staticmethod
    def create(commit: Commit) -> Report:
        tc = commit.pyright["typeCompleteness"]

        filesAnalyzed = commit.pyright["summary"]["filesAnalyzed"]
        numbers = dict(_get_numbers(tc), filesAnalyzed=filesAnalyzed)

        counter = Counter(s["name"] for s in tc["symbols"])
        if dupes := [k for k, v in counter.items() if v > 1]:
            raise ValueError(f"{dupes=}")

        symbols = {s["name"]: s for s in tc["symbols"]}
        return Report(commit, numbers=numbers, symbols=symbols)

    def operate(self, other: Report, op: Op) -> Numbers:
        return _operate(op, self.numbers, other.numbers)


@dc.dataclass
class StringsDiff:
    added: Strs
    common: Strs
    removed: Strs

    @staticmethod
    def create(a: Iterable[str], b: Iterable[str]) -> StringsDiff:
        sa, sb = set(a), set(b)
        return StringsDiff(
            added=sorted(sb - sa), removed=sorted(sa - sb), common=sorted(sa & sb)
        )

    def add_remove(self) -> dict[str, Strs]:
        return {"added": self.added, "removed": self.removed}


@dc.dataclass
class SymbolsDiff:
    """A diff between two dicts of "symbols" - the dicts we get back
    from pyright in the "symbol" field."""

    added: Strs
    common: StrDictDict
    removed: Strs

    @staticmethod
    def create(a: StrDictDict, b: StrDictDict) -> SymbolsDiff:
        diff = StringsDiff.create(a, b)
        return SymbolsDiff(
            common={k: v for k in diff.common if (v := _diff_symbol(a[k], b[k]))},
            **diff.add_remove(),
        )


@dc.dataclass
class ReportDiff:
    absolute: Numbers
    percent: Numbers
    symbols: SymbolsDiff

    @staticmethod
    def create(a: Report, b: Report) -> ReportDiff:
        return ReportDiff(
            absolute=b.operate(a, operator.sub),
            percent=a.operate(b, _percent),
            symbols=SymbolsDiff.create(a.symbols, b.symbols),
        )


class CompareError(ValueError):
    pass


def _diff_symbol(a: StrDict, b: StrDict) -> StrDict:
    diff: StrDict = {}
    for k, v in a.items():
        if k == "alternateNames" or v == (w := b[k]):
            continue
        elif isinstance(v, bool):
            diff[k] = w
        elif k == "diagnostics":
            s = StringsDiff.create((i["message"] for i in v), (i["message"] for i in w))
            if d := {k: v for k, v in s.add_remove().items() if v}:
                diff[k] = d
    return diff


def _get_numbers(d: StrDict) -> NumberOrDictDict:
    def is_number_field(v: Any) -> bool:
        if isinstance(v, dict):
            return bool(v) and all(is_number_field(i) for i in v.values())
        elif isinstance(v, list):
            return bool(v) and all(is_number_field(i) for i in v)
        else:
            return isinstance(v, (int, float))

    return {k: v for k, v in d.items() if is_number_field(v)}


def _operate(op: Op, a: Numbers, b: Numbers, key: str = "") -> Numbers:
    if isinstance(a, dict):
        assert isinstance(b, dict), b
        assert a.keys() == b.keys(), (a, b)
        return {k: _operate(op, v, b[k], k) for k, v in a.items()}  # type: ignore[return-value]
    else:
        assert isinstance(a, (float, int)) and isinstance(b, (float, int)), (a, b, key)
        return op(a, b)


def _percent(a: Number, b: Number) -> Number:
    if a:
        return 100 * (b - a) / a
    return float("inf") if b > 0 else 0 if b == 0 else -float("inf")


def _timestamp() -> str:
    return datetime.datetime.utcnow().isoformat()


if __name__ == "__main__":
    main()
