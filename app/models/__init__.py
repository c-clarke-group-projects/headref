"""Canonical models package.

Exports Base and all models for centralized schema registration and Alembic discovery.
"""

from app.db import Base
from app.models.agent import AgentRun, Insight
from app.models.canonical import AppUser, Organization, Project, Repository, Team
from app.models.connections import (
    GitHubInstallation,
    JiraConnection,
    WorkspaceJiraProject,
    WorkspaceRepository,
)
from app.models.feed import FindingSnapshot, WorkspaceEvent
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.operations import SyncCursor, SyncRun
from app.models.work import (
    Commit,
    PullRequest,
    Review,
    ReviewRequest,
    WorkItem,
    WorkItemDependency,
    WorkItemLink,
)
from app.models.workspace import Account, Workspace, WorkspaceInvite, WorkspaceMember

__all__ = [
    "Base",
    "Organization",
    "Team",
    "AppUser",
    "Repository",
    "Project",
    "IdentityLink",
    "UnmatchedEntity",
    "WorkItem",
    "WorkItemDependency",
    "PullRequest",
    "Commit",
    "Review",
    "WorkItemLink",
    "Insight",
    "AgentRun",
    "SyncRun",
    "SyncCursor",
    "Account",
    "Workspace",
    "WorkspaceMember",
    "GitHubInstallation",
    "WorkspaceRepository",
    "JiraConnection",
    "WorkspaceJiraProject",
    "FindingSnapshot",
    "WorkspaceEvent",
    "WorkspaceInvite",
    "ReviewRequest",
]
