"""Identity map parser and database loader (P1-005, Issue #11, DEC-008)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.canonical import AppUser, Organization
from app.models.identity import IdentityLink, UnmatchedEntity


class IdentityMapError(Exception):
    """Raised when an identity map file is malformed, invalid, or contains duplicate accounts."""


class AccountMapping(BaseModel):
    """External account mapping specification in identity map."""

    integration: Literal["github", "jira"] = Field(description="Upstream platform name")
    external_id: str = Field(
        min_length=1,
        description="Stable numeric ID or accountId, not username",
    )
    external_handle: str = Field(
        min_length=1,
        description="Username or handle at mapping time",
    )


class PersonMapping(BaseModel):
    """Internal person specification in identity map."""

    display_name: str = Field(min_length=1, description="Canonical display name")
    role_label: str | None = Field(default=None, description="Optional role description")
    accounts: list[AccountMapping] = Field(
        min_length=1,
        description="List of verified upstream account mappings",
    )


class IdentityMapSchema(BaseModel):
    """Root structure of identity_map.yml."""

    people: list[PersonMapping] = Field(
        default_factory=list,
        description="List of internal person definitions",
    )


def parse_identity_map(source: str | Path) -> IdentityMapSchema:
    """Parse and validate an identity map YAML file or raw string.

    Raises IdentityMapError on malformed YAML (with line/column location),
    schema validation failures, or duplicate (integration, external_id) mappings.
    """
    content: str
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source):
        path = Path(source)
        if path.is_file():
            content = path.read_text(encoding="utf-8")
        else:
            content = str(source)
    else:
        content = str(source)

    try:
        raw = yaml.safe_load(content)
    except yaml.MarkedYAMLError as e:
        line = e.problem_mark.line + 1 if e.problem_mark else "unknown"
        col = e.problem_mark.column + 1 if e.problem_mark else "unknown"
        raise IdentityMapError(f"Malformed YAML on line {line}, column {col}: {e.problem}") from e
    except yaml.YAMLError as e:
        raise IdentityMapError(f"YAML parsing error: {e}") from e

    if not isinstance(raw, dict):
        raise IdentityMapError(
            "Identity map YAML must define a top-level mapping with a 'people' list."
        )

    try:
        parsed = IdentityMapSchema.model_validate(raw)
    except ValidationError as e:
        raise IdentityMapError(f"Identity map schema validation failed: {e}") from e

    # Cross-person duplicate detection: each (integration, external_id) must be unique
    seen_accounts: dict[tuple[str, str], str] = {}
    for person in parsed.people:
        for account in person.accounts:
            key = (account.integration, account.external_id)
            if key in seen_accounts:
                existing_person = seen_accounts[key]
                raise IdentityMapError(
                    f"Duplicate external ID '{account.external_id}' for integration "
                    f"'{account.integration}' mapped to both '{existing_person}' "
                    f"and '{person.display_name}'."
                )
            seen_accounts[key] = person.display_name

    return parsed


def load_identity_map(
    session: Session,
    source: str | Path,
    organization_id: int | None = None,
    team_id: int | None = None,
) -> list[IdentityLink]:
    """Load verified identity mappings into the database.

    Idempotent: updates existing records and does not duplicate rows.
    Mappings are created with match_method='manual' and confidence='HIGH'.
    """
    parsed = parse_identity_map(source)

    if organization_id is None:
        org = session.scalar(select(Organization).limit(1))
        if org is None:
            org = Organization(name="Default Organization")
            session.add(org)
            session.flush()
        org_id = org.id
    else:
        org_id = organization_id

    loaded_links: list[IdentityLink] = []

    for person in parsed.people:
        user = session.scalar(
            select(AppUser).where(
                AppUser.display_name == person.display_name,
                AppUser.organization_id == org_id,
            )
        )
        if user is None:
            user = AppUser(
                organization_id=org_id,
                team_id=team_id,
                display_name=person.display_name,
                role_label=person.role_label,
                is_active=True,
            )
            session.add(user)
            session.flush()
        elif person.role_label is not None:
            user.role_label = person.role_label

        for account in person.accounts:
            link = session.scalar(
                select(IdentityLink).where(
                    IdentityLink.organization_id == org_id,
                    IdentityLink.integration == account.integration,
                    IdentityLink.external_id == account.external_id,
                )
            )
            now = datetime.now(UTC)
            if link is not None:
                link.app_user_id = user.id
                link.external_handle = account.external_handle
                link.match_method = "manual"
                link.confidence = "HIGH"
                link.verified_at = now
            else:
                link = IdentityLink(
                    organization_id=org_id,
                    app_user_id=user.id,
                    integration=account.integration,
                    external_id=account.external_id,
                    external_handle=account.external_handle,
                    match_method="manual",
                    confidence="HIGH",
                    verified_at=now,
                )
                session.add(link)

            loaded_links.append(link)

            # Resolve any existing unmatched entity record for this external account
            unmatched = session.scalar(
                select(UnmatchedEntity).where(
                    UnmatchedEntity.integration == account.integration,
                    UnmatchedEntity.external_id == account.external_id,
                )
            )
            if unmatched is not None:
                unmatched.resolved_app_user_id = user.id

    session.flush()
    return loaded_links
