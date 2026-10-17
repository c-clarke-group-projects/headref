"""Tests for the planner, stage S1 (P3-004)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.planner import PLANS, RISK_WINDOW_DAYS, InvalidQuestionTypeError, build_plan

ALL_TOOLS = ("T-002", "T-003", "T-004", "T-005", "T-006")


def test_there_are_exactly_three_question_types() -> None:
    """The agent answers the 3 fixed questions and nothing else."""
    assert set(PLANS) == {"current_work", "blockers", "risks"}


@pytest.mark.parametrize(
    ("question", "required", "optional", "days", "open_due", "tools"),
    [
        ("current_work", ("github",), ("jira",), 14, False, ALL_TOOLS),
        ("blockers", ("github",), ("jira",), 14, False, ALL_TOOLS),
        ("risks", ("jira",), ("github",), 30, True, ("T-002", "T-003", "T-004", "T-006")),
    ],
)
def test_each_question_type_has_the_documented_plan(
    question: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    days: int,
    open_due: bool,
    tools: tuple[str, ...],
) -> None:
    """Sources, window and tools match DEC-007, DEC-020 and the P3-004 task."""
    plan = build_plan(question)

    assert plan.question_type == question
    assert plan.required_sources == required
    assert plan.optional_sources == optional
    assert plan.window_days == days
    assert plan.include_open_due_items is open_due
    assert plan.tools == tools


def test_the_risk_window_is_a_named_constant() -> None:
    """The 30 day risk window is not a bare number in the plan."""
    assert build_plan("risks").window_days == RISK_WINDOW_DAYS


@pytest.mark.parametrize(
    "question", ["", "everything", "CURRENT_WORK", "current_work ", "risks; DROP TABLE"]
)
def test_an_invalid_question_type_is_rejected(question: str) -> None:
    """Anything that is not exactly one of the 3 types is refused."""
    with pytest.raises(InvalidQuestionTypeError):
        build_plan(question)


def test_an_invalid_question_type_answers_422() -> None:
    """Raised inside a route, the rejection becomes an HTTP 422."""
    application = FastAPI()

    @application.get("/plan")
    def plan(question: str) -> dict[str, str]:
        return {"question": build_plan(question).question_type}

    client = TestClient(application)

    assert client.get("/plan", params={"question": "everything"}).status_code == 422
    assert client.get("/plan", params={"question": "risks"}).status_code == 200
