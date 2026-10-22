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


# Inference is local (DEC-017) and the model is mocked here, so an evaluation run costs
# nothing. The run is still reported, as P6-003 asks: scenarios, outcome, time, cost.
COST_PER_RUN_USD = 0.0


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    reports = [
        r
        for key in ("passed", "failed", "error", "xfailed")
        for r in terminalreporter.stats.get(key, [])
        if getattr(r, "when", "call") == "call" and str(r.nodeid).startswith("tests/eval/")
    ]
    if not reports:
        return
    passed = sum(1 for r in reports if r.outcome == "passed")
    seconds = sum(getattr(r, "duration", 0.0) for r in reports)
    terminalreporter.write_line(
        f"Evaluation run: {passed} of {len(reports)} scenario tests passed in {seconds:.1f} s, "
        f"cost {COST_PER_RUN_USD:.2f} USD (local model, mocked; DEC-017)."
    )
