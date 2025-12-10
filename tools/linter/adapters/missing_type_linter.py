from __future__ import annotations

import dataclasses as dc
import json
import re
import shlex
import sys
from functools import cached_property, partial
from pathlib import Path
from typing import Any, TYPE_CHECKING, TypeAlias
from typing_extensions import override


_PARENT = Path(__file__).parent.absolute()
_PATH = [Path(p).absolute() for p in sys.path]

if TYPE_CHECKING or _PARENT not in _PATH:
    from ._linter import FileLinter, is_public, LintResult, PythonFile
else:
    from _linter import FileLinter, is_public, LintResult, PythonFile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence


DESCRIPTION = """`missing_type_linter` is a lintrunner linter which uses pyrefly to detect
new symbols that have been created but have not been given a type.
"""
EPILOG = """
"""

_log = partial(print, file=sys.stderr)

GRANDFATHER = Path(__file__).parent / "missing_type_linter_grandfather.txt"

TYPE_CHECK_COMMAND = "pyrefly report --config=pyrefly.toml"
PARAM_RE = re.compile('Type of parameter "(.*)" is unknown')
SUFFIXES = ".py", ".pyi"
PUBLIC_NAMES = "__init__", "__main__"


def _add_arguments(add: Callable[..., Any]) -> None:
    # Also inherits arguments from ._linter.file_linter.FileLinter

    help = "Run, but do not print lint checks or write grandfather file"
    add("--dry-run", "-d", action="store_true", help=help)

    help = f"Set the grandfather file name (default={GRANDFATHER})"
    add("--grandfather", "-g", default=GRANDFATHER, type=Path, help=help)

    help = "The paths to check"
    add("--path", "-p", nargs="*", help=help)

    help = f"The command line for type checking (default='{TYPE_CHECK_COMMAND}')"
    add("--type-check", "-t", default=TYPE_CHECK_COMMAND, help=help)

    help = "The name of a JSON file with type checking results (ignore --type-check)"
    add("--type-result", "-r", default=None, type=Path, help=help)

    help = "Use block_name instead of pyrefly_name (slower but more compatible)"
    add("--use-block-name", "-u", action="store_true", help=help)

    help = "Rewrite the grandfather list"
    add("--write-grandfather", "-w", action="store_true", help=help)


class MissingTypeLinter(FileLinter):
    linter_name = "missing_type_linter"
    description = DESCRIPTION
    epilog = EPILOG
    report_column_numbers = True

    def __init__(self, argv: Sequence[str] | None = None) -> None:
        super().__init__(argv)
        _add_arguments(self.parser.add_argument)

    @override
    def lint_all(self) -> bool:
        if self.must_write_grandfather:
            self._write_grandfather()
        else:
            for path in self.not_grandfathered:
                self._lint_file(path)

        self._report()
        return self.must_write_grandfather or not self.not_grandfathered

    @override
    def _lint(self, pf: PythonFile) -> Iterator[LintResult]:
        lr = [m.lint_result() for m in self.not_grandfathered[pf.path]]
        if not self.args.dry_run:
            yield from lr

    @cached_property
    def all_annotations(self) -> list[Annotation]:
        """All annotations, including empty and non-public annotations"""

        def annotate(filename: str, d: dict[str, Any]) -> Iterator[Annotation]:
            path = Path(filename)
            if path.suffix not in SUFFIXES:
                return

            pf = PythonFile(MissingTypeLinter.linter_name, path=path)
            use = self.args.use_block_name

            for f in d["functions"]:
                name = f["name"]
                yield Annotation.make(f, pf, name, use)
                yield from (Annotation.make(p, pf, name, use) for p in f["parameters"])

        return [i for k, v in sorted(self.type_results.items()) for i in annotate(k, v)]

    @cached_property
    def annotations(self) -> list[Annotation]:
        """All public annotations, perhaps empty: triggers tokenization if needed"""
        return [a for a in self.all_annotations if a.needs_annotation]

    @cached_property
    def empty_annotations_by_path(self) -> dict[Path, list[Annotation]]:
        """All public functions and parameters that have no annotations"""
        empty: dict[Path, list[Annotation]] = {}
        for a in self.annotations:
            if not a.annotation:
                empty.setdefault(a.path, []).append(a)
        return empty

    @cached_property
    def grandfather_file(self) -> set[str]:
        """Read the grandfather file, ignore comments and blank lines, return as set"""
        with self.args.grandfather.open() as fp:
            return {v for i in fp if (v := i.split("#")[0].strip())}

    @cached_property
    def not_grandfathered(self) -> dict[Path, list[Annotation]]:
        """Empty annotations which are not grandfathered, indexed by Path"""

        def not_grandfathered(lm: list[Annotation]) -> list[Annotation]:
            return [m for m in lm if m.grandfather_name not in self.grandfather_file]

        items = self.empty_annotations_by_path.items()
        return {k: g for k, v in items if (g := not_grandfathered(v))}

    @cached_property
    def must_write_grandfather(self) -> bool:
        return self.args.write_grandfather or not self.args.grandfather.exists()

    @cached_property
    def type_results(self) -> dict[Path, Any]:
        """Results from calling `pyrefly` and un-JSONing it, then making names unique"""
        if self.args.type_result:
            text = self.args.type_result.read_text()
        else:
            path = self.args.path or ["torch"]
            cmd = *shlex.split(self.args.type_check), *path
            text = self.call(cmd)

        type_results = json.loads(text)
        name_count: dict[str, int] = {}

        for file, contents in type_results.items():
            for f in contents["functions"]:
                name = f["name"]
                if count := name_count.get(name, 0):
                    f["name"] = f"{name}[{count + 2}]"
                name_count[name] = count + 1

        return type_results

    def _report(self) -> None:
        Number: TypeAlias = int | float
        report: dict[str, Number] = {}

        def count(name: str, x: Number | Sequence[Any]) -> None:
            report[name] = x if isinstance(x, Number) else len(x)

        def percent(name: str, base: str) -> None:
            count(name + "_percent", round(100 * report[name] / report[base], 4))

        def nonempty(name: str, annotations: Sequence[Annotation]) -> None:
            count(name, sum(bool(a.annotation) for a in annotations))

        params = [a for a in self.annotations if a.param_name]
        returns = [a for a in self.annotations if not a.param_name]

        count("files", {a.python_file.path for a in self.annotations})
        count("annotations", self.annotations)
        count("params", params)
        count("functions", returns)

        by_grandfather: dict[str, list[Annotation]] = {}
        for a in self.annotations:
            grandfather = a.grandfather_name.split("(")[0]
            by_grandfather.setdefault(grandfather, []).append(a)

        if len(by_grandfather) != len(returns):
            print(len(returns), len({r.pyrefly_name for r in returns}))
        assert len(by_grandfather) == len(returns), (len(by_grandfather), len(returns))

        full = "fully_annotated_functions"  # Our metric
        part = "partially_annotated_functions"
        un = "unannotated_functions"
        report.update({full: 0, part: 0, un: 0})

        for v in by_grandfather.values():
            annotations = [a.annotation for a in v]
            category = full if all(annotations) else part if any(annotations) else un
            report[category] += 1

        for k in (full, part, un):
            percent(k, "functions")

        nonempty("nonempty_annotations", self.annotations)
        nonempty("nonempty_return_annotations", returns)
        nonempty("nonempty_param_annotations", params)

        percent("nonempty_annotations", "annotations")
        percent("nonempty_return_annotations", "functions")
        percent("nonempty_param_annotations", "params")

        count("grandfather_file", self.grandfather_file)

        # Count all functions and members, including non-public
        aa = self.all_annotations

        count("all_annotations", aa)
        nonempty("all_nonempty_annotations", aa)
        percent("all_nonempty_annotations", "all_annotations")

        count("all_files", {a.python_file.path for a in aa})
        count("all_functions", sum(not a.param_name for a in aa))
        count("all_params", sum(bool(a.param_name) for a in aa))

        print(json.dumps(report, indent=4), file=sys.stderr)

    def _write_grandfather(self) -> None:
        annotations = self.empty_annotations_by_path.values()
        grandfather = sorted({m.grandfather_name for v in annotations for m in v})
        if not self.args.dry_run:
            with self.args.grandfather.open("w") as fp:
                fp.writelines(g + "\n" for g in grandfather)


@dc.dataclass(frozen=True)
class Annotation:
    annotation: str | None
    location: dict[str, Any]
    param_name: str | None
    pyrefly_name: str
    python_file: PythonFile
    use_block_name: bool

    @staticmethod
    def make(
        d: dict[str, Any], pf: PythonFile, pyrefly_name: str, use_block_name: bool
    ) -> Annotation:
        if (annotation := d.get("annotation", d)) is d:
            # It's a return annotation
            annotation = d["return_annotation"]
            param_name = None
        else:
            # It's a parameter annotation
            param_name = d["name"]

        return Annotation(
            annotation=annotation,
            location=d["location"],
            param_name=param_name,
            pyrefly_name=pyrefly_name,
            python_file=pf,
            use_block_name=use_block_name,
        )

    @cached_property
    def block_name(self) -> str:
        # This triggers a somewhat expensive tokenization of the file.
        return self.python_file.block_name(self.line + 1)

    @cached_property
    def column(self) -> int:
        column = self.location["start"]["column"]
        assert isinstance(column, int)
        return column

    @cached_property
    def grandfather_name(self) -> str:
        if self.use_block_name:
            name = ".".join((*self.python_file.python_parts, self.block_name))
        else:
            name = self.pyrefly_name
        assert not name.endswith("."), (name, self)
        return f"{name}({self.param_name}=)" if self.param_name else name

    @cached_property
    def needs_annotation(self) -> bool:
        return (
            self.param_name not in ("self", "cls")
            and (self.param_name is None or is_public(self.param_name))
            and is_public(self.pyrefly_name)
            and (
                not self.use_block_name
                or (self.python_file.is_public and is_public(self.block_name))
            )
        )

    @cached_property
    def length(self) -> int | None:
        end = self.location["end"]
        return 1 + end["column"] - self.column if self.line == end["line"] else 0

    @cached_property
    def line(self) -> int:
        line = self.location["start"]["line"]
        assert isinstance(line, int)
        return line

    def lint_result(self) -> LintResult:
        category = "parameter" if self.param_name else "return"
        name = f"Missing {category} type for {self.pyrefly_name}"
        return LintResult(
            char=self.column, length=self.length, line=self.line, name=name
        )

    @cached_property
    def path(self) -> Path:
        return self.python_file.path


if __name__ == "__main__":
    MissingTypeLinter.run()
