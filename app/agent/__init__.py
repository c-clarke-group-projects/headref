"""Headref agent package.

Contains the complete Phase 4 agent reasoning pipeline:
- S1 Planning & S2 Retrieval (`planner.py`, `orchestrator.py`)
- S3 Evidence Builder (`evidence_builder.py`)
- S4a Deterministic Findings Engine (`conflicts.py`, `blockers.py`, `risks.py`, `confidence.py`,
  `deterministic_summary.py`)
- S4b Narrative Generation (`narrative.py`)
- S5 Narrative Validation (`validation.py`)
- S6 Response, Persistence & Cache (`cache.py`, `orchestrator.py`)
"""

from app.agent.blockers import BlockerFinding, detect_blockers
from app.agent.cache import (
    compute_evidence_hash,
    get_cached_insight,
    persist_agent_run,
    save_cached_insight,
)
from app.agent.confidence import ConfidenceLevel, evaluate_confidence
from app.agent.conflicts import ConflictFinding, detect_conflicts
from app.agent.deterministic_summary import (
    FALLBACK_HEADER,
    PRODUCTIVITY_CONTEXT_NOTE,
    DeterministicSummaryResult,
    generate_deterministic_summary,
)
from app.agent.evidence_builder import build_evidence
from app.agent.narrative import (
    NarrativeOutput,
    NarrativeResult,
    deterministic_fallback,
    generate_narrative,
)
from app.agent.orchestrator import (
    AgentRunContext,
    ReadTools,
    RetrievalResult,
    run_agent,
    run_s1_s2,
    unknown_response,
)
from app.agent.planner import QuestionType, RetrievalPlan, Source, build_plan
from app.agent.risks import RiskFinding, detect_risks
from app.agent.team_overview import generate_team_overview
from app.agent.validation import ValidatedNarrative, validate_narrative

__all__ = [
    "AgentRunContext",
    "BlockerFinding",
    "ConfidenceLevel",
    "ConflictFinding",
    "DeterministicSummaryResult",
    "FALLBACK_HEADER",
    "NarrativeOutput",
    "NarrativeResult",
    "PRODUCTIVITY_CONTEXT_NOTE",
    "QuestionType",
    "ReadTools",
    "RetrievalPlan",
    "RetrievalResult",
    "RiskFinding",
    "Source",
    "ValidatedNarrative",
    "build_evidence",
    "build_plan",
    "compute_evidence_hash",
    "detect_blockers",
    "detect_conflicts",
    "detect_risks",
    "deterministic_fallback",
    "evaluate_confidence",
    "generate_deterministic_summary",
    "generate_narrative",
    "generate_team_overview",
    "get_cached_insight",
    "persist_agent_run",
    "run_agent",
    "run_s1_s2",
    "save_cached_insight",
    "unknown_response",
    "validate_narrative",
]
