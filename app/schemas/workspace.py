"""Workspace API contracts (W1, DEC-021)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WorkspaceCreate(BaseModel):
    """Body of POST /api/workspaces."""

    name: str = Field(min_length=1, max_length=100, description="Workspace name")
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _blank_description_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class WorkspaceOut(BaseModel):
    """A workspace as the signed-in account sees it."""

    workspace_id: int
    name: str
    description: str | None
    role: Literal["owner", "manager", "viewer"] = Field(description="The caller's role")
    created_at: datetime
