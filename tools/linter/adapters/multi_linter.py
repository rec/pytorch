import dataclasses as dc
import datetime
import json
import sys
import traceback
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from subprocess import CalledProcessError, run
from typing import Any, TYPE_CHECKING


_FILE = Path(__file__).absolute()
_PATH = [Path(p).absolute() for p in sys.path]
VERBOSE = False


if TYPE_CHECKING or _FILE.parent not in _PATH:
    from . import _linter
else:
    import _linter

_COMMIT_IDS_TO_REPORT = 3
_COMMITS_COMMAND = f"git log -{_COMMIT_IDS_TO_REPORT} --pretty=format:%h"


# TODO: count whether functions have full annotations
# TODO: a linter for boolean
# TODO: Count `from __future__ import annotations` somewhere!
# This line can be found in `outgoing_imports`.
#
# TODO type-ignores report (then and now)
#
# TODO: __all__ pull requester: for each file or directory
#   * fill in or improve the __all__
#   * give reasons for each file as to where the imports come from.
#
#  # mypy: allow-untyped-defs!!!

"""
@dc.dataclass
class PythonFileInfo(PythonCodeInfo):
    # Not used, Fit in somewhere into the main program.
    file_size_bytes: int = 0
    incoming_imports: dict[str, dict[str, list[str]]] = dc.field(default_factory=dict)
    outgoing_imports: list[str] = dc.field(default_factory=list)
    double_underscore_all_lines: list[str] = dc.field(default_factory=list)
    top_level_noqas: dict[str, int] = dc.field(default_factory=dict)
    top_level_type_ignores: dict[str, int] = dc.field(default_factory=dict)
"""


@dc.dataclass
class AllFiles:
    files: Sequence[str]

    def asdict(self) -> dict[str, dict[str, Any]]:
        def asdict(file: str, d: dict[str, Any]) -> dict[str, Any]:
            if incoming := self.incoming_imports.get(file):
                d["incoming_imports"] = incoming
            d["path"] = str(d["path"])
            return d

        modules = {k: asdict(k, v) for k, v in self.info.items()}
        return {"metadata": _metadata(), "modules": modules}

    @cached_property
    def info(self) -> dict[str, dict[str, Any]]:
        dicts: list[dict[str, Any]] = []
        has_wildcard = set("[*?").intersection
        for file_pattern in self.files:
            if has_wildcard(file_pattern):
                files = Path().glob(file_pattern)
            else:
                files = [Path(file_pattern)]
            for f in files:
                try:
                    d = _linter.MultiFile(f).asdict()
                    if VERBOSE:
                        print(f, file=sys.stderr)
                except Exception as e:
                    print(file=sys.stderr)
                    traceback.print_exc()
                    print(file=sys.stderr)
                    d = {"error": str(e)}
                    if True:
                        raise
                dicts.append(d)

        return dict(sorted((d.pop("module_name"), d) for d in dicts))

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


def _metadata() -> dict[str, Any]:
    res = {"timestamp": datetime.datetime.now().isoformat()}
    try:
        return res | {"commit": _run(_COMMITS_COMMAND)}
    except CalledProcessError:
        return res


def _run(cmd: str, print_error: bool = True, verbose: bool = False) -> list[str]:
    if verbose:
        print("$", cmd, file=sys.stderr)
    try:
        s = run(cmd, capture_output=True, text=True, check=True, shell=True).stdout
        return [i.strip() for i in s.strip().splitlines()]

    except CalledProcessError as e:
        if print_error:
            print(f"Error on command `{cmd}`", file=sys.stderr)
            if e.stdout.strip():
                print(f"stdout\n------\n{e.stdout}\n", file=sys.stderr)
            if e.stderr.strip():
                print(f"stderr\n------\n{e.stderr}\n", file=sys.stderr)
        raise


_JSON_TYPES = bool, dict, float, int, list, str


def default_json(x: Any) -> Any:
    if x is None or type(x) in _JSON_TYPES:
        return x
    if isinstance(x, tuple):
        return list(x)
    try:
        return next(t(x) for t in _JSON_TYPES if isinstance(x, t))
    except StopIteration:
        print(f"json did not understand {x=}, f{type(x)=}", file=sys.stderr)
        return str(x)


if __name__ == "__main__":
    all_files = AllFiles(sys.argv[1:])
    if not (data := all_files.asdict()):
        sys.exit(f"No files found in {all_files.files}")

    print(json.dumps(data, indent=2, default=default_json))
