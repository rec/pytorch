# mypy: ignore-errors

from __future__ import annotations


class Seven:
    def __init__(self):
        pass

    def do_it(self):
        return 1


ONE = False


def seven():
    if ONE:

        class Inner:
            """Inner is some sort of class"""

            X = 1

    else:

        class Inner:
            """Inner is another sort of class"""

            X = 2
