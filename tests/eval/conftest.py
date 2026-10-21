"""Evaluation scenarios EV-01 to EV-12 (P6-003, P6-004, TESTING_AND_EVALUATION.md 11.2).

Every test under tests/eval is marked `eval`, so `pytest -m eval tests/eval` runs the
suite. CI runs `pytest --ignore=tests/eval`, so these never run on a pull request.
Assertions are on structured fields only, never on wording.
"""

from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if EVAL_DIR in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.eval)
