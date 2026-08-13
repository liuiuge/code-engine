"""Shared web-layer dependencies: paths and cross-cutting helpers.

Centralizes the configurable output directories and the helper functions used
by multiple route modules (problems, go-code, meta). The heavy solver
(langgraph / langchain-ollama) is intentionally NOT imported here — the
``/generate`` route imports it lazily so API startup never depends on it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import os

from infrastructure.constants import (
    VERIFY_FAIL_PREFIX,
    VERIFY_PASS_MESSAGE,
)
from infrastructure.paths import (
    DEFAULT_GO_CODE_DIR,
    DEFAULT_PROBLEMS_DIR,
    PROJECT_ROOT,
)

# Overridable via env for non-standard layouts.
PROBLEMS_DIR = Path(os.environ.get("CODE_ENGINE_PROBLEMS_DIR", str(DEFAULT_PROBLEMS_DIR)))
GO_CODE_DIR = Path(os.environ.get("CODE_ENGINE_GO_CODE_DIR", str(DEFAULT_GO_CODE_DIR)))
FRONTEND_DIR = Path(os.environ.get("CODE_ENGINE_FRONTEND_DIR", str(PROJECT_ROOT / "frontend")))


# --------------------------------------------------------------------------- #
# Go-code file enumeration
# --------------------------------------------------------------------------- #
def _iter_go_code_files() -> list[Path]:
    """Return every .go file living under output/go-code/*/."""
    if not GO_CODE_DIR.is_dir():
        return []
    found: list[Path] = []
    for folder in sorted(GO_CODE_DIR.iterdir()):
        if not folder.is_dir():
            continue
        for go in folder.glob("*.go"):
            found.append(go)
    return found


# --------------------------------------------------------------------------- #
# Problem index loading
# --------------------------------------------------------------------------- #
def _load_index() -> dict:
    """Load problems_index.json, or fall back to scanning on disk."""
    index_path = PROBLEMS_DIR / "problems_index.json"
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    from features.problems.service import list_local_problems
    return {"problems": list_local_problems(PROBLEMS_DIR)}


def _rebuild_index() -> int:
    """Rescan every local problem JSON and rewrite the index files.

    Returns the number of problems indexed. We rebuild from disk (rather than
    trusting the existing index) so a partial pull never loses cached problems.
    """
    from features.problems.storage import load_problem_file, save_index, save_index_json

    records: list[dict] = []
    for f in sorted(PROBLEMS_DIR.glob("*.json")):
        if f.name == "problems_index.json":
            continue
        rec = load_problem_file(f)
        if rec:
            records.append(rec)
    save_index_json(records, PROBLEMS_DIR)
    save_index(records, PROBLEMS_DIR)
    return len(records)


# --------------------------------------------------------------------------- #
# Sorting helpers (problems list)
# --------------------------------------------------------------------------- #
_DIFFICULTY_RANK = {"unknown": 0, "easy": 1, "medium": 2, "hard": 3}


def _sort_key_for(problem: dict, order_by: str):
    """Return a comparable sort key for a problem summary under ``order_by``."""
    if order_by == "difficulty":
        return _DIFFICULTY_RANK.get((problem.get("difficulty") or "unknown").lower(), 99)
    if order_by == "id":
        # Natural numeric ordering when the id is numeric, else fall back to string.
        s = str(problem.get("id", "") or "")
        return (0, int(s)) if s.isdigit() else (1, s)
    # title / slug: case-insensitive text
    return str(problem.get(order_by, "") or "").lower()


def _sort_problems(problems: list[dict], order_by: str, order: str) -> list[dict]:
    """Return ``problems`` sorted by ``order_by`` (asc/desc)."""
    reverse = order == "desc"
    return sorted(problems, key=lambda p: _sort_key_for(p, order_by), reverse=reverse)


# --------------------------------------------------------------------------- #
# Problem <-> Go-code linkage (separator-insensitive)
# --------------------------------------------------------------------------- #
def _norm_key(name: str) -> str:
    """Normalize a slug/task name to a separator-insensitive key.

    Lowercases and strips every non-alphanumeric character, so that e.g.
    ``median-of-two-sorted-arrays``, ``median_of_two_sorted_arrays`` and
    ``medianoftwosortedarrays`` all collapse to the same key. This keeps the
    problem<->go-code link working even though folder names mix dashes and
    underscores across the repo.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _go_code_norm_map() -> dict[str, Path]:
    """Map normalized key -> go-code folder (only folders with a .go file)."""
    m: dict[str, Path] = {}
    if GO_CODE_DIR.is_dir():
        for folder in sorted(GO_CODE_DIR.iterdir()):
            if folder.is_dir() and any(folder.glob("*.go")):
                m.setdefault(_norm_key(folder.name), folder)
    return m


def _problem_norm_map() -> dict[str, str]:
    """Map normalized key -> problem slug (from the problems index)."""
    m: dict[str, str] = {}
    index = _load_index()
    for entry in index.get("problems", []):
        slug = entry.get("slug") or ""
        if slug:
            m.setdefault(_norm_key(slug), slug)
    return m


def _go_code_dir_for_problem(slug: str, go_map: dict | None = None) -> Path | None:
    """Find the go-code folder for a problem slug (separator-insensitive)."""
    m = go_map if go_map is not None else _go_code_norm_map()
    return m.get(_norm_key(slug))


def _resolve_go_code_folder(task_name: str) -> Path | None:
    """Resolve a go-code task folder, tolerating dash/underscore name drift.

    Tries the literal folder name first (fast, exact), then falls back to a
    separator-insensitive lookup so that ``two_sum`` and ``two-sum`` resolve to
    the same task directory regardless of how the pipeline named it.
    """
    literal = GO_CODE_DIR / task_name
    if literal.is_dir():
        return literal
    return _go_code_norm_map().get(_norm_key(task_name))


# --------------------------------------------------------------------------- #
# Go-code summaries
# --------------------------------------------------------------------------- #
def _read_verify_result(folder: Path) -> tuple[bool | None, str | None]:
    """Read the verification sidecar written next to generated code, if present.

    Returns ``(verified, verify_result)`` where ``verified`` is True/False on a
    real check, or None when no verification was performed.
    """
    sidecar = folder / "verify_result.json"
    if not sidecar.exists():
        return None, None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    result = data.get("verify_result") or ""
    if result == VERIFY_PASS_MESSAGE:
        return True, result
    if result.startswith(VERIFY_FAIL_PREFIX):
        return False, result
    return None, result or None


def _go_code_summary(go_path: Path, problem_map: dict | None = None) -> "GoCodeSummary":
    from web.schemas import GoCodeSummary

    folder = go_path.parent
    task_name = folder.name
    stat = go_path.stat()
    try:
        text = go_path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    m = problem_map if problem_map is not None else _problem_norm_map()
    verified, verify_result = _read_verify_result(folder)
    return GoCodeSummary(
        task_name=task_name,
        file=go_path.name,
        rel_path=str(go_path.relative_to(PROJECT_ROOT)),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        line_count=text.count("\n") + 1 if text else 0,
        related_problem=m.get(_norm_key(task_name)),
        verified=verified,
    )


def _go_code_detail(go_path: Path):
    from web.schemas import GoCodeDetail

    s = _go_code_summary(go_path)
    try:
        content = go_path.read_text(encoding="utf-8")
    except Exception:
        content = ""
    _, verify_result = _read_verify_result(go_path.parent)
    return GoCodeDetail(**s.model_dump(), content=content, verify_result=verify_result)
