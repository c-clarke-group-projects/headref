"""Import guard test asserting no HTTP client is imported under app/tools/.

Mandatory check enforcing AC-1, DEC-002, and project security baseline.
Read tools must remain strictly offline and query PostgreSQL only.
The scan is recursive, so a subfolder of app/tools/ cannot escape it.
"""

import ast
from pathlib import Path

FORBIDDEN_MODULES = frozenset(
    {
        "httpx",
        "requests",
        "urllib.request",
        "http.client",
        "aiohttp",
        "urllib3",
    }
)

TOOLS_DIR = Path(__file__).resolve().parent.parent / "app" / "tools"


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f"{f}.") for f in FORBIDDEN_MODULES)


def find_http_client_imports(root: Path) -> list[str]:
    """Return 1 line per forbidden import in any Python file under root, at any depth."""
    violations: list[str] = []
    for file_path in sorted(root.rglob("*.py")):
        name = file_path.relative_to(root).as_posix()
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name):
                        violations.append(f"{name}:{node.lineno} imports '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module and _is_forbidden(node.module):
                    violations.append(f"{name}:{node.lineno} imports from '{node.module}'")
    return violations


def test_no_http_client_imported_in_tools() -> None:
    """Scan all Python files in app/tools/ and verify no forbidden HTTP client imports."""
    assert TOOLS_DIR.is_dir(), f"Tools directory {TOOLS_DIR} does not exist"

    python_files = list(TOOLS_DIR.rglob("*.py"))
    assert len(python_files) >= 7, "Expected at least 7 tool modules in app/tools/"

    violations = find_http_client_imports(TOOLS_DIR)

    assert not violations, (
        "Forbidden HTTP client imports found under app/tools/ (violating AC-1/DEC-002):\n"
        + "\n".join(violations)
    )


def test_guard_scans_subfolders(tmp_path: Path) -> None:
    """A forbidden import in a nested package is reported, not skipped."""
    nested = tmp_path / "jira" / "helpers"
    nested.mkdir(parents=True)
    (tmp_path / "ok.py").write_text("import json\n", encoding="utf-8")
    (nested / "fetch.py").write_text("import json\nfrom httpx import get\n", encoding="utf-8")

    assert find_http_client_imports(tmp_path) == ["jira/helpers/fetch.py:2 imports from 'httpx'"]
