"""Evidence, claim, and insight contracts (P1-002, Issue #8)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.tools import PullRequestOut, SourceHealthOut, WorkItemOut


class EvidenceItem(BaseModel):
    """Frozen, code-generated evidence unit with a stable identifier within 1 agent run.

    Per DEC-004, the model cites these by ID; it never generates EvidenceItem objects.
    """

    id: str = Field(description="Sequential identifier within 1 run e.g. ev_1")
    source: Literal["jira", "github"] = Field(description="Authoritative source platform")
    entity_type: Literal["work_item", "pull_request", "commit", "review", "work_item_link"] = Field(
        description="Underlying entity type"
    )
    entity_key: str = Field(
        description="Natural identifier e.g. AUTH-245, acme/api#182, or commit sha"
    )
    source_url: str = Field(description="Deep link to source record")
    summary: str = Field(
        description="One factual line generated strictly by application code, never by the model"
    )
    excerpt: str | None = Field(
        default=None,
        description="Untrusted source text, capped at EXCERPT_MAX_CHARS and URL-stripped",
    )
    observed_at: datetime = Field(description="When event occurred at source")
    retrieved_at: datetime = Field(description="When fetched into Headref")
    source_state: Literal["fresh", "stale"] = Field(
        description="Freshness state of source at evidence construction time"
    )


class Claim(BaseModel):
    """Structured claim produced by deterministic rules or narrative extraction.

    The model returns evidence_ids only. It MUST NOT return EvidenceItem objects (DEC-004).
    """

    text: str = Field(description="Factual or inferential statement")
    classification: Literal["fact", "inference", "unknown"] = Field(
        description="Epistemic status of claim"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs citing pre-built evidence set, strictly strings and never objects",
    )

    @property
    def claim(self) -> str:
        """Alias for text for compatibility across codebase contexts."""
        return self.text

    @model_validator(mode="before")
    @classmethod
    def _normalize_claim_text(cls, data: Any) -> Any:
        """Allow claim key as alias for text when parsing dictionaries."""
        if isinstance(data, dict):
            if "text" not in data and "claim" in data:
                data = dict(data)
                data["text"] = data["claim"]
        return data

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _reject_embedded_objects(cls, v: Any) -> list[str]:
        """Enforce DEC-004: evidence_ids must be string IDs, not embedded evidence objects."""
        if not isinstance(v, list):
            raise ValueError("evidence_ids must be a list of string identifiers")
        for item in v:
            if not isinstance(item, str):
                raise ValueError(
                    f"evidence_ids must only contain string identifiers, got {type(item).__name__}"
                )
        return v


class Insight(BaseModel):
    """A claim joined with resolved evidence items and calculated confidence."""

    claim: str = Field(description="Statement of insight")
    classification: Literal["fact", "inference", "unknown"] = Field(
        description="Epistemic status of insight"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = Field(
        description="Computed confidence level assigned by code, not by the model"
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Resolved evidence objects backing this claim",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Human-readable conflict notes produced by code",
    )

    @property
    def text(self) -> str:
        """Alias for claim for compatibility across codebase contexts."""
        return self.claim

    @model_validator(mode="before")
    @classmethod
    def _normalize_claim_text(cls, data: Any) -> Any:
        """Allow text key as alias for claim when parsing dictionaries."""
        if isinstance(data, dict):
            if "claim" not in data and "text" in data:
                data = dict(data)
                data["claim"] = data["text"]
        return data


class ActivitySummary(BaseModel):
    """This member's own activity counts inside the window (Gap G3).

    Context for the reader, never a score: no totals across members, no averages and
    no trend (CLAUDE.md rule 15, AI_BEHAVIOR.md 5.11).
    """

    window_days: int = Field(description="Length of the window the counts cover")
    open_pull_requests: int = Field(default=0, description="Open pull requests in the window")
    commits: int = Field(default=0, description="Commits in the window")
    reviews_given: int = Field(default=0, description="Reviews this member gave in the window")
    unlinked_pull_requests: list[PullRequestOut] = Field(
        default_factory=list,
        description="Pull requests in the window with no stored work item link",
    )


class MemberInsight(BaseModel):
    """Full API response contract for a team member's insight view."""

    user_id: int = Field(description="Internal user ID of subject")
    likely_current_work: Insight | None = Field(
        default=None, description="Primary current work insight if determined"
    )
    assigned: list[WorkItemOut] = Field(
        default_factory=list, description="Currently assigned work items"
    )
    blockers: list[Insight] = Field(default_factory=list, description="Detected blocker insights")
    risks: list[Insight] = Field(default_factory=list, description="Detected risk insights")
    unknowns: list[str] = Field(default_factory=list, description="Explicit unknowns or data gaps")
    last_synced: dict[str, datetime | None] = Field(
        default_factory=dict,
        description="Per-source last successful sync timestamp",
    )
    source_health: list[SourceHealthOut] = Field(
        default_factory=list,
        description="Per-source operational health and availability state",
    )
    summary: str | None = Field(default=None, description="Optional high-level narrative summary")
    fallback_used: bool = Field(
        default=False, description="Whether deterministic fallback was used"
    )
    activity: ActivitySummary | None = Field(
        default=None,
        description="Activity counts, or None when a tool they need failed or was not run",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Jira and GitHub disagreements found by code (CF-1 to CF-4), unresolved",
    )
    truncated: dict[str, int] | None = Field(
        default=None,
        description="What a read tool capped, mapped to the cap applied, e.g. {'commits': 100}",
    )


class AttentionItem(BaseModel):
    """An attention item on the team overview (blocker or risk, Gap G6)."""

    kind: Literal["blocker", "risk"] = Field(description="Signal category")
    claim: str = Field(description="Statement describing the attention item")
    classification: Literal["fact", "inference", "unknown"] = Field(
        default="fact", description="Epistemic classification"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = Field(
        default="HIGH", description="Confidence level assigned by code"
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="Resolved evidence backing this item"
    )


class TeamMemberOverview(BaseModel):
    """Per-member state rollup on the team overview."""

    user_id: int = Field(description="Internal user ID")
    display_name: str = Field(description="Display name of the team member")
    role_label: str | None = Field(default=None, description="Role label if configured")
    state: Literal["on_track", "needs_attention", "blocked", "unknown"] = Field(
        description="Computed member state"
    )
    state_reason: str | None = Field(default=None, description="Explanation when state is unknown")
    attention_count: int = Field(default=0, description="Count of attention items")
    attention_items: list[AttentionItem] = Field(
        default_factory=list, description="Items needing attention"
    )
    backing_evidence: list[EvidenceItem] = Field(
        default_factory=list, description="Evidence items backing this member state"
    )


class TeamOverview(BaseModel):
    """Full API response contract for GET /api/teams/{team_id}/overview."""

    team_id: int = Field(description="Internal team ID")
    team_name: str = Field(description="Name of the team")
    overall_status: Literal["On Track", "Needs Attention", "Blocked", "Unknown"] = Field(
        description="High-level status of the team"
    )
    source_health: list[SourceHealthOut] = Field(
        default_factory=list, description="Per-source health and availability"
    )
    last_synced: dict[str, datetime | None] = Field(
        default_factory=dict, description="Per-source last sync timestamp"
    )
    unmatched_count: int = Field(default=0, description="Count of unmatched accounts for the team")
    members: list[TeamMemberOverview] = Field(
        default_factory=list, description="Team member state rollups in alphabetical order"
    )
