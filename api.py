"""
api.py — FastAPI service exposing the code-engine's generated artifacts.

Two artifact families are served:

  1. Generated problems   -> output/problems/<slug>.json (canonical records)
                             (reuses the loaders in problems.py)
  2. Generated Go code     -> output/go-code/<task_name>/<task_name>.go

Endpoints
---------
  GET /                                  API info / health
  GET /api/stats                         overview counts (problems, go-code)
  GET /api/problems                      list problems (filter + paginate)
  GET /api/problems/{identifier}         full problem record (slug or id)
  GET /api/problems/{identifier}/go-code best-effort Go code linked to a problem
  GET /api/go-code                       list generated Go code (search + paginate)
  GET /api/go-code/{task_name}           full Go code (metadata + source)
  GET /api/go-code/{task_name}/raw       raw .go file (FileResponse)

Run
---
  uvicorn api:app --reload --port 8000

The service is read-only: it serves what the workflow has already produced.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Reuse the existing, dependency-light loaders from problems.py
# (problems.py only imports `logger`, so no heavy ML deps are pulled in).
from problems import (
    DEFAULT_OUTPUT_DIR,
    find_local_problem,
    list_local_problems,
    load_problem_file,
)

# --------------------------------------------------------------------------- #
# Paths (overridable via env for non-standard layouts)
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
PROBLEMS_DIR = Path(os.environ.get("CODE_ENGINE_PROBLEMS_DIR", str(DEFAULT_OUTPUT_DIR)))
GO_CODE_DIR = Path(os.environ.get("CODE_ENGINE_GO_CODE_DIR", str(BASE_DIR / "output" / "go-code")))
FRONTEND_DIR = Path(os.environ.get("CODE_ENGINE_FRONTEND_DIR", str(BASE_DIR / "frontend")))

# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="CodeEngine API",
    version="1.0.0",
    description="Read-only API for generated LeetCode problems and Go code.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class ProblemSummary(BaseModel):
    id: str = ""
    slug: str = ""
    title: str = ""
    difficulty: str = "Unknown"
    tags: list[str] = []
    paid: bool = False
    file: str = ""
    has_go_code: bool = False


class ProblemDetail(BaseModel):
    title: str = ""
    titleSlug: str = ""
    questionId: str = ""
    questionFrontendId: str = ""
    difficulty: str = "Unknown"
    topicTags: list[dict] = []
    isPaidOnly: bool = False
    url: str = ""
    description_md: str = ""
    content_html: str = ""
    exampleTestcaseList: list[str] = []
    hints: list[str] = []
    codeSnippets: list[dict] = []
    metaData: str = ""
    go_template: str = ""
    has_go_code: bool = False


class GoCodeSummary(BaseModel):
    task_name: str
    file: str
    rel_path: str
    size_bytes: int = 0
    modified_at: str = ""
    line_count: int = 0
    related_problem: Optional[str] = None  # problem slug, best-effort


class GoCodeDetail(BaseModel):
    task_name: str
    file: str
    rel_path: str
    size_bytes: int = 0
    modified_at: str = ""
    line_count: int = 0
    related_problem: Optional[str] = None
    content: str = ""


class Stats(BaseModel):
    problems_count: int = 0
    go_code_count: int = 0
    difficulties: dict[str, int] = {}
    tags: list[str] = []
    problems_dir: str = ""
    go_code_dir: str = ""


class PaginatedProblems(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[ProblemSummary]


class PaginatedGoCode(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[GoCodeSummary]


# --------------------------------------------------------------------------- #
# Helpers
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


def _load_index() -> dict:
    """Load problems_index.json, or fall back to scanning on disk."""
    index_path = PROBLEMS_DIR / "problems_index.json"
    if index_path.exists():
        try:
            import json
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"problems": list_local_problems(PROBLEMS_DIR)}


def _go_code_dir_for_problem(slug: str) -> Optional[Path]:
    """Best-effort: problems use dashes, go-code tasks use underscores."""
    candidate = GO_CODE_DIR / slug.replace("-", "_")
    return candidate if candidate.is_dir() else None


def _problem_slug_for_task(task_name: str) -> Optional[str]:
    """Best-effort reverse lookup of a problem slug for a go-code task."""
    slug_candidate = task_name.replace("_", "-")
    index = _load_index()
    for entry in index.get("problems", []):
        eslug = (entry.get("slug") or "").lower()
        if eslug == slug_candidate:
            return eslug
        if (entry.get("slug") or "").replace("-", "_").lower() == task_name.lower():
            return eslug
    return None


def _go_code_summary(go_path: Path) -> GoCodeSummary:
    folder = go_path.parent
    task_name = folder.name
    stat = go_path.stat()
    try:
        text = go_path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    return GoCodeSummary(
        task_name=task_name,
        file=go_path.name,
        rel_path=str(go_path.relative_to(BASE_DIR)),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        line_count=text.count("\n") + 1 if text else 0,
        related_problem=_problem_slug_for_task(task_name),
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
def root():
    """Landing page: redirect to the static UI when present, else to /docs."""
    if FRONTEND_DIR.is_dir():
        return RedirectResponse(url="/ui/")
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health():
    return {
        "name": "CodeEngine API",
        "version": "1.0.0",
        "status": "ok",
        "endpoints": [
            "/health",
            "/api/stats",
            "/api/problems",
            "/api/problems/{identifier}",
            "/api/problems/{identifier}/go-code",
            "/api/go-code",
            "/api/go-code/{task_name}",
            "/api/go-code/{task_name}/raw",
            "/ui/",
            "/docs",
        ],
    }


@app.get("/api/stats", response_model=Stats, tags=["meta"])
def stats():
    problems = list_local_problems(PROBLEMS_DIR)
    go_files = _iter_go_code_files()

    difficulties: dict[str, int] = {}
    tag_set: set[str] = set()
    for p in problems:
        diff = p.get("difficulty", "Unknown") or "Unknown"
        difficulties[diff] = difficulties.get(diff, 0) + 1
        for t in p.get("tags", []) or []:
            tag_set.add(t)

    return Stats(
        problems_count=len(problems),
        go_code_count=len(go_files),
        difficulties=difficulties,
        tags=sorted(tag_set),
        problems_dir=str(PROBLEMS_DIR),
        go_code_dir=str(GO_CODE_DIR),
    )


@app.get("/api/problems", response_model=PaginatedProblems, tags=["problems"])
def list_problems(
    difficulty: Optional[str] = Query(None, description="Filter by difficulty (Easy/Medium/Hard)"),
    tag: Optional[str] = Query(None, description="Filter by topic tag (case-insensitive)"),
    paid: Optional[bool] = Query(None, description="Filter by paid-only status"),
    search: Optional[str] = Query(None, description="Substring match on title (case-insensitive)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    problems = list_local_problems(PROBLEMS_DIR)

    if difficulty:
        problems = [p for p in problems if (p.get("difficulty") or "").lower() == difficulty.lower()]
    if tag:
        problems = [p for p in problems if tag.lower() in [t.lower() for t in (p.get("tags") or [])]]
    if paid is not None:
        problems = [p for p in problems if bool(p.get("paid")) == paid]
    if search:
        s = search.lower()
        problems = [p for p in problems if s in (p.get("title") or "").lower()]

    total = len(problems)
    page = problems[offset: offset + limit]
    items = [
        ProblemSummary(
            id=p.get("id", ""),
            slug=p.get("slug", ""),
            title=p.get("title", ""),
            difficulty=p.get("difficulty", "Unknown"),
            tags=p.get("tags", []) or [],
            paid=bool(p.get("paid")),
            file=p.get("file", ""),
            has_go_code=_go_code_dir_for_problem(p.get("slug", "")) is not None,
        )
        for p in page
    ]
    return PaginatedProblems(total=total, count=len(items), limit=limit, offset=offset, items=items)


@app.get("/api/problems/{identifier}", response_model=ProblemDetail, tags=["problems"])
def get_problem(identifier: str):
    # find_local_problem resolves by slug, frontend id, title, or LeetCode URL.
    record = find_local_problem(identifier, output_dir=PROBLEMS_DIR)
    if not record:
        raise HTTPException(status_code=404, detail=f"Problem not found: {identifier}")
    slug = record.get("titleSlug", "")
    data = {k: record.get(k) for k in ProblemDetail.model_fields if k != "has_go_code"}
    return ProblemDetail(
        **data,
        has_go_code=_go_code_dir_for_problem(slug) is not None,
    )


@app.get("/api/problems/{identifier}/go-code", response_model=GoCodeDetail, tags=["problems"])
def problem_go_code(identifier: str):
    record = find_local_problem(identifier, output_dir=PROBLEMS_DIR)
    if not record:
        raise HTTPException(status_code=404, detail=f"Problem not found: {identifier}")
    go_dir = _go_code_dir_for_problem(record.get("titleSlug", ""))
    if not go_dir:
        raise HTTPException(
            status_code=404,
            detail=f"No Go code found for problem: {record.get('titleSlug', identifier)}",
        )
    go_files = sorted(go_dir.glob("*.go"))
    if not go_files:
        raise HTTPException(status_code=404, detail=f"Go code directory empty: {go_dir}")
    return _go_code_detail(go_files[0])


@app.get("/api/go-code", response_model=PaginatedGoCode, tags=["go-code"])
def list_go_code(
    search: Optional[str] = Query(None, description="Substring match on task name (case-insensitive)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    go_files = _iter_go_code_files()
    if search:
        s = search.lower()
        go_files = [g for g in go_files if s in g.parent.name.lower()]

    # One entry per task (folder); prefer the file matching the folder name.
    seen: dict[str, Path] = {}
    for g in go_files:
        name = g.parent.name
        if name not in seen:
            seen[name] = g
    unique = list(seen.values())

    total = len(unique)
    page = unique[offset: offset + limit]
    items = [_go_code_summary(g) for g in page]
    return PaginatedGoCode(total=total, count=len(items), limit=limit, offset=offset, items=items)


@app.get("/api/go-code/{task_name}", response_model=GoCodeDetail, tags=["go-code"])
def get_go_code(task_name: str):
    folder = GO_CODE_DIR / task_name
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Go code task not found: {task_name}")
    go_files = sorted(folder.glob("*.go"))
    if not go_files:
        raise HTTPException(status_code=404, detail=f"No .go file in task: {task_name}")
    return _go_code_detail(go_files[0])


@app.get("/api/go-code/{task_name}/raw", tags=["go-code"])
def get_go_code_raw(task_name: str):
    folder = GO_CODE_DIR / task_name
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Go code task not found: {task_name}")
    go_files = sorted(folder.glob("*.go"))
    if not go_files:
        raise HTTPException(status_code=404, detail=f"No .go file in task: {task_name}")
    return FileResponse(
        str(go_files[0]),
        media_type="text/plain",
        filename=go_files[0].name,
    )


def _go_code_detail(go_path: Path) -> GoCodeDetail:
    s = _go_code_summary(go_path)
    try:
        content = go_path.read_text(encoding="utf-8")
    except Exception:
        content = ""
    return GoCodeDetail(**s.model_dump(), content=content)


# --------------------------------------------------------------------------- #
# Static UI (mounted last so /api/* routes take precedence)
# --------------------------------------------------------------------------- #
if FRONTEND_DIR.is_dir():
    app.mount(
        "/ui",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
