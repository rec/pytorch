# mypy: ignore-errors

CONSTANT = 1
DICT = {3: 2}


def important():
    def not_important():
        return 1

    return not_important
