# mypy: ignore-errors

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock, TestCase

import pyright_compare as pc


TESTDATA = Path(__file__).parent / "testdata"
TEST_FILE1 = TESTDATA / "pyright1.json"
TEST_FILE2 = TESTDATA / "pyright2.json"


class TestPyrightCompare(TestCase):
    maxDiff = 10_240
    rewrite_expected = "REWRITE_EXPECTED" in os.environ

    def assertExpected(self, path: Path, actual: str, suffix: str) -> None:
        expected_file = Path(f"{path}.{suffix}")
        if not self.rewrite_expected and expected_file.exists():
            self.assertEqual(expected_file.read_text(), actual)
        else:
            expected_file.write_text(actual)

    @mock.patch("sys.stdout", new_callable=io.StringIO)
    @mock.patch("sys.argv", new=sys.argv[:1])
    @mock.patch("pyright_compare._timestamp", lambda: "2025-09-26T11:26:55.980131")
    def test_pyright(self, mock_stdout):
        compare = pc.PyrightCompare()
        c1, c2 = "dad54ca7c05", "3034dcc8032"
        compare.commit_ids = [c1, c2]

        def make_commit(name, commit_id, message, pyright_file):
            pyright = json.loads(pyright_file.read_text())
            c = pc.Commit(name, compare)
            c.__dict__.update(commit_id=commit_id, message=message, pyright=pyright)
            return c

        before = make_commit("HEAD~", c1, "A commit message", TEST_FILE1)
        after = make_commit("HEAD", c2, "A comet massage", TEST_FILE2)
        report_diff = compare._diff(before, after, 0)
        diff = json.dumps(report_diff, indent=4)
        self.assertExpected(TESTDATA / "diff", diff, "json")
