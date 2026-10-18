"""FastAPI application entry point.

Wires the health endpoint, the scheduler lifespan and the auth routes. Member-data
routes arrive in P5-001 and MUST take the MemberGuard dependency from app.web.auth.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_settings
from app.scheduler import start_scheduler_task, stop_scheduler_task
from app.web.auth import auth_router
from app.web.connections import connections_pages
from app.web.jira_connections import jira_pages
from app.web.routes import router as api_router
from app.web.ui_format import templates
from app.web.workspace_pages import workspace_pages
from app.web.workspaces import workspace_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify application configuration and manage scheduler background task."""
    settings = get_settings()
    task, stop_event = start_scheduler_task(enabled=settings.scheduler_enabled)
    try:
        yield
    finally:
        await stop_scheduler_task(task, stop_event)


app = FastAPI(
    title="Headref",
    description="Evidence-based engineering team intelligence agent",
    version="0.1.0",
    lifespan=lifespan,
)


# The fonts are repository assets under app/web/static/fonts, committed with their
# OFL licence. Nothing is copied at import time: a docs directory is not a runtime
# asset source and need not exist in a container image.
static_dir = Path(__file__).resolve().parent / "web" / "static"

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(auth_router)
app.include_router(workspace_api)
app.include_router(workspace_pages)
app.include_router(connections_pages)
app.include_router(jira_pages)
app.include_router(api_router)


def _wants_html(request: Request) -> bool:
    """True for a browser page request, false for the JSON API."""
    if request.url.path.startswith("/api"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _error_kind(path: str) -> str:
    """Pick the copy variant error.html uses for 404 and 422."""
    return "member" if path.startswith("/members/") else "page"


def _member_id_from(path: str) -> int | None:
    """The member id in /members/{id}/..., for the 422 tab links."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "members" and parts[1].isdecimal():
        return int(parts[1])
    return None


def _html_error(request: Request, code: int):
    """Render error.html for a page request. The copy comes from the status code."""
    path = request.url.path
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "request": request,
            "code": code,
            "kind": "question" if code == 422 else _error_kind(path),
            "member_id": _member_id_from(path),
            "actor": None,
            "team_name": "",
            "unmatched_count": 0,
            "nav_current": "",
        },
        status_code=code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """A bad path or query parameter on a page route renders the error page.

    FastAPI handles RequestValidationError separately from HTTPException, so without
    this a browser hitting a route with a malformed parameter got a JSON body listing
    the internal field locations.
    """
    if _wants_html(request):
        return _html_error(request, status.HTTP_422_UNPROCESSABLE_ENTITY)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Render page errors as HTML and API errors as JSON.

    An unauthenticated page request is sent to the login form with the path it was
    trying to reach. The API keeps its JSON body unchanged so clients are not broken.
    """
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not request.url.path.startswith("/api"):
        next_url = request.url.path
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?=&')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if _wants_html(request):
        # exc.detail can carry internals. error.html picks fixed copy from the status
        # code instead, so nothing from the exception reaches the page.
        return _html_error(request, exc.status_code)

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


class Health(BaseModel):
    """Response contract for the health endpoint."""

    status: str
    service: str
    version: str
    checked_at: datetime


@app.get("/health", response_model=Health)
def health() -> Health:
    """Report that the API process is up.

    This checks the process only. It deliberately does not touch PostgreSQL or
    Ollama: a readiness check over both belongs with the source health tool
    (T-007) in P3-003, which reports per-source freshness rather than a single
    boolean.
    """
    return Health(
        status="ok",
        service="headref",
        version="0.1.0",
        checked_at=datetime.now(UTC),
    )
