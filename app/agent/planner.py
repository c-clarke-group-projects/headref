"""Planner, stage S1 of the agent (P3-004, DEC-007).

The plan is a fixed dictionary, not a model call. For a question type it says which
sources are required, which are optional, how far back to look, and which read tools
stage S2 will call. Nothing here touches the database or the network.
"""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import RECENT_ACTIVITY_DAYS

QuestionType = Literal["current_work", "blockers", "risks"]
Source = Literal["jira", "github"]

# The risks question looks further back than the other two (DEC-007).
RISK_WINDOW_DAYS = 30


class RetrievalPlan(BaseModel):
    """What stage S2 must retrieve to answer one question type."""

    model_config = ConfigDict(frozen=True)

    question_type: QuestionType
    required_sources: tuple[Source, ...]
    optional_sources: tuple[Source, ...]
    window_days: int
    include_open_due_items: bool
    tools: tuple[str, ...]


PLANS: dict[str, RetrievalPlan] = {
    # Jira is optional for these 2 so a member seen only in repository history can still
    # be analysed from GitHub. The answer then says Jira was not checked (DEC-020).
    "current_work": RetrievalPlan(
        question_type="current_work",
        required_sources=("github",),
        optional_sources=("jira",),
        window_days=RECENT_ACTIVITY_DAYS,
        include_open_due_items=False,
        tools=("T-002", "T-003", "T-004", "T-005", "T-006"),
    ),
    "blockers": RetrievalPlan(
        question_type="blockers",
        required_sources=("github",),
        optional_sources=("jira",),
        window_days=RECENT_ACTIVITY_DAYS,
        include_open_due_items=False,
        tools=("T-002", "T-003", "T-004", "T-005", "T-006"),
    ),
    "risks": RetrievalPlan(
        question_type="risks",
        required_sources=("jira",),
        optional_sources=("github",),
        window_days=RISK_WINDOW_DAYS,
        include_open_due_items=True,
        tools=("T-002", "T-003", "T-004", "T-006"),
    ),
}


class InvalidQuestionTypeError(HTTPException):
    """Raised for a question type the agent does not know.

    It is an HTTPException, so FastAPI answers it with 422 by itself, and no stage
    runs before it is raised.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            detail=f"question must be one of: {', '.join(PLANS)}",
        )


def build_plan(question_type: str) -> RetrievalPlan:
    """Return the plan for a question type, or raise InvalidQuestionTypeError."""
    plan = PLANS.get(question_type)
    if plan is None:
        raise InvalidQuestionTypeError()
    return plan
