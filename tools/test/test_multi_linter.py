# mypy: ignore-errors

import sys
from pathlib import Path

from tools.linter.adapters._linter import python_info, PythonFile
from tools.linter.adapters._linter.multi_file import _split_import
from tools.linter.adapters.multi_linter import AllFiles


_PARENT = Path(__file__).parent.absolute()
_PATH = [Path(p).absolute() for p in sys.path]

if _PARENT in _PATH:
    from linter_test_case import LinterTestCase
else:
    from .linter_test_case import LinterTestCase

ROOT = _PARENT / "multi_linter_testdata"
PROJECT = ROOT / "project"

SPLITS = (
    ("import math", ["math"]),
    ("import math.sqrt", ["math.sqrt"]),
    ("from math import sqrt", ["math.sqrt"]),
    ("from . import seven", [".seven"]),
    (
        "from . seven import Seven, ONE",
        [".seven.Seven", ".seven.ONE"],
    ),
    (
        "from .seven import Seven, ONE",
        [".seven.Seven", ".seven.ONE"],
    ),
    ("import torch._refs as refs # avoid import cycle in mypy", ["torch._refs"]),
)


class TestMultiLinter(LinterTestCase):
    def test_all_files(self):
        files = AllFiles([str(p) for p in PROJECT.glob("**/*.py")])
        d = files.asdict()
        metadata = d.pop("metadata")
        self.assertEqual(sorted(metadata), ["commit", "timestamp"])
        self.assertExpected(ROOT / "info", self.dumps(d), "json")

    def test_split_import(self):
        lines, expected = zip(*SPLITS)
        actual = [_split_import(line) for line in lines]
        self.assertEqual(list(expected), actual)


class TestPythonInfo(LinterTestCase):
    def test_serialize_empty(self):
        self.assertEqual({}, python_info.Ignores().asdict())
        self.assertEqual({}, python_info.ScopeInfo().asdict())
        self.assertEqual({}, python_info.FileInfo().asdict())

    def test_serialize_this_file(self):
        pf = PythonFile.make("multi_liner", Path(__file__))
        file_info = python_info.FileInfo.make(pf).asdict()

        self.assertExpected(ROOT / "self_test", self.dumps(file_info), "json")
