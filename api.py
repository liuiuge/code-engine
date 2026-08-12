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
  POST /api/problems/pull                pull NEW problems from LeetCode (bulk)
  POST /api/problems/{identifier}/pull   pull a single problem by slug/URL

Run
---
  uvicorn api:app --reload --port 8000

The list/detail endpoints are read-only. The POST /api/problems/pull endpoints
fetch from LeetCode and persist results under output/problems (skipping already
cached slugs so the local set is only ever grown, never clobbered).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Reuse the existing, dependency-light loaders from problems.py
# (problems.py only imports `logger`, so no heavy ML deps are pulled in).
from problems import (
    DEFAULT_OUTPUT_DIR,
    fetch_live_problem,
    fetch_problem_detail,
    fetch_problem_list,
    find_local_problem,
    list_local_problems,
    load_problem_file,
    save_index,
    save_index_json,
    save_problem,
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
    related_problem: str | None = None  # problem slug, best-effort


class GoCodeDetail(BaseModel):
    task_name: str
    file: str
    rel_path: str
    size_bytes: int = 0
    modified_at: str = ""
    line_count: int = 0
    related_problem: str | None = None
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


class PullQuery(BaseModel):
    """Request body for POST /api/problems/pull (bulk pull of new problems)."""
    limit: int = Field(50, ge=1, le=2000, description="Max problems to consider from LeetCode.")
    category: str = ""
    difficulty: str | None = Field(None, description="Filter by difficulty (Easy/Medium/Hard).")
    tags: list[str] | None = Field(None, description="Filter by topic tag slugs.")
    fetch_details: bool = True
    save_markdown: bool = True
    delay: float = Field(0.2, ge=0.0, description="Seconds between detail requests (be polite).")
    force: bool = Field(False, description="Re-fetch problems that are already cached locally.")


class PullOneResult(BaseModel):
    slug: str
    title: str
    difficulty: str
    file: str
    status: str  # "created" | "updated"
    error: str | None = None


class BulkPullResponse(BaseModel):
    pulled: int
    skipped: int
    errors: list[str] = []
    slugs: list[str] = []
    total_indexed: int
    output_dir: str
    index_json_path: str
    index_path: str


class GenerateResult(BaseModel):
    identifier: str
    task_name: str | None = None
    file: str | None = None
    build_result: str = ""
    success: bool = False
    category: str | None = None
    content: str | None = None
    error: str | None = None


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
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"problems": list_local_problems(PROBLEMS_DIR)}


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


def _rebuild_index() -> int:
    """Rescan every local problem JSON and rewrite the index files.

    Returns the number of problems indexed. We rebuild from disk (rather than
    trusting the existing index) so a partial pull never loses cached problems.
    """
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


def _go_code_summary(go_path: Path, problem_map: dict | None = None) -> GoCodeSummary:
    folder = go_path.parent
    task_name = folder.name
    stat = go_path.stat()
    try:
        text = go_path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    m = problem_map if problem_map is not None else _problem_norm_map()
    return GoCodeSummary(
        task_name=task_name,
        file=go_path.name,
        rel_path=str(go_path.relative_to(BASE_DIR)),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        line_count=text.count("\n") + 1 if text else 0,
        related_problem=m.get(_norm_key(task_name)),
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
            "/api/problems/pull",
            "/api/problems/{identifier}/pull",
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
    difficulty: str | None = Query(None, description="Filter by difficulty (Easy/Medium/Hard)"),
    tag: str | None = Query(None, description="Filter by topic tag (case-insensitive)"),
    paid: bool | None = Query(None, description="Filter by paid-only status"),
    search: str | None = Query(None, description="Substring match on title (case-insensitive)"),
    order_by: str = Query("id", description="Sort field: id, title, slug, or difficulty."),
    order: str = Query("asc", description="Sort direction: asc or desc."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if order_by not in ("id", "title", "slug", "difficulty"):
        order_by = "id"
    order = order if order in ("asc", "desc") else "asc"

    problems = list_local_problems(PROBLEMS_DIR)

    if difficulty:
        problems = [
            p for p in problems
            if (p.get("difficulty") or "").lower() == difficulty.lower()
        ]
    if tag:
        problems = [
            p for p in problems
            if tag.lower() in [t.lower() for t in (p.get("tags") or [])]
        ]
    if paid is not None:
        problems = [p for p in problems if bool(p.get("paid")) == paid]
    if search:
        s = search.lower()
        problems = [p for p in problems if s in (p.get("title") or "").lower()]

    problems = _sort_problems(problems, order_by, order)

    total = len(problems)
    page = problems[offset: offset + limit]
    go_map = _go_code_norm_map()
    items = [
        ProblemSummary(
            id=p.get("id", ""),
            slug=p.get("slug", ""),
            title=p.get("title", ""),
            difficulty=p.get("difficulty", "Unknown"),
            tags=p.get("tags", []) or [],
            paid=bool(p.get("paid")),
            file=p.get("file", ""),
            has_go_code=_go_code_dir_for_problem(p.get("slug", ""), go_map) is not None,
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


# --------------------------------------------------------------------------- #
# Pull endpoints (write operations that fetch from LeetCode)
# --------------------------------------------------------------------------- #
def _do_pull_one(identifier: str) -> PullOneResult:
    """Fetch a single problem live from LeetCode and persist it."""
    record = fetch_live_problem(identifier)
    if not record:
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch problem from LeetCode: {identifier}",
        )
    slug = record.get("titleSlug", "")
    local_json = PROBLEMS_DIR / f"{slug}.json"
    existed = local_json.exists()
    try:
        save_problem(record, PROBLEMS_DIR, save_markdown=True)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        raise HTTPException(status_code=500, detail=f"Failed to save problem {slug}: {exc}")
    _rebuild_index()
    return PullOneResult(
        slug=slug,
        title=record.get("title", ""),
        difficulty=record.get("difficulty", "Unknown"),
        file=str(local_json),
        status="updated" if existed else "created",
    )


def _do_pull(q: PullQuery) -> BulkPullResponse:
    """Fetch the problem list from LeetCode and save only the new ones."""
    filters: dict = {}
    if q.difficulty:
        # LeetCode's QuestionListFilterInput.difficulty is an UPPERCASE enum.
        _diff_map = {"easy": "EASY", "medium": "MEDIUM", "hard": "HARD"}
        filters["difficulty"] = _diff_map.get(q.difficulty.lower(), q.difficulty.upper())
    if q.tags:
        filters["tags"] = q.tags

    try:
        listing = fetch_problem_list(
            category=q.category,
            filters=filters,
            page_limit=50,
            max_problems=q.limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch problem list from LeetCode: {exc}",
        )

    new_slugs: list[str] = []
    skipped = 0
    errors: list[str] = []
    for p in listing:
        slug = p.get("titleSlug")
        if not slug:
            continue
        local_json = PROBLEMS_DIR / f"{slug}.json"
        if local_json.exists() and not q.force:
            skipped += 1
            continue
        try:
            detail = fetch_problem_detail(slug)
            if not detail:
                errors.append(slug)
                continue
            # Merge list-level fields the detail query does not return.
            detail.setdefault("titleSlug", slug)
            detail.setdefault("title", p.get("title"))
            detail.setdefault("difficulty", p.get("difficulty"))
            detail.setdefault("topicTags", p.get("topicTags", []))
            detail.setdefault("questionFrontendId", p.get("frontendQuestionId"))
            detail.setdefault("isPaidOnly", p.get("paidOnly", False))
            save_problem(detail, PROBLEMS_DIR, save_markdown=q.save_markdown)
            new_slugs.append(slug)
        except Exception as exc:
            errors.append(f"{slug}: {exc}")
        if q.delay:
            time.sleep(q.delay)

    total_indexed = _rebuild_index()
    return BulkPullResponse(
        pulled=len(new_slugs),
        skipped=skipped,
        errors=errors,
        slugs=new_slugs,
        total_indexed=total_indexed,
        output_dir=str(PROBLEMS_DIR),
        index_json_path=str(PROBLEMS_DIR / "problems_index.json"),
        index_path=str(PROBLEMS_DIR / "README.md"),
    )


@app.post("/api/problems/pull", response_model=BulkPullResponse, tags=["problems"])
async def pull_problems(q: PullQuery):
    """Pull new problems from LeetCode (skips already-cached slugs, rebuilds index)."""
    return await asyncio.to_thread(_do_pull, q)


@app.post("/api/problems/{identifier}/pull", response_model=PullOneResult, tags=["problems"])
async def pull_one_problem(identifier: str):
    """Pull a single problem from LeetCode by slug or URL."""
    return await asyncio.to_thread(_do_pull_one, identifier)


def _do_generate(identifier: str) -> GenerateResult:
    """Run the code-engine workflow to generate Go code for a problem.

    Reuses ``main.generate_for_problem`` (which wraps the LangGraph pipeline) so
    the CLI and the API share the same code path. Heavy deps (langgraph,
    langchain-ollama, pyyaml) are imported lazily so the rest of the API stays
    importable even if they are missing.
    """
    try:
        from main import generate_for_problem
    except Exception as exc:  # pragma: no cover - import guard
        raise HTTPException(
            status_code=503, detail=f"Generation unavailable (missing deps): {exc}"
        )

    try:
        res = generate_for_problem(identifier, problems_dir=str(PROBLEMS_DIR), live=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    code_path = res.get("code_path") or ""
    build_result = res.get("build_result") or ""
    success = "static analysis passed, compilation successful" in build_result

    content = None
    if code_path and Path(code_path).exists():
        try:
            content = Path(code_path).read_text(encoding="utf-8")
        except Exception:
            content = None

    return GenerateResult(
        identifier=identifier,
        task_name=res.get("task_dir"),
        file=code_path or None,
        build_result=build_result,
        success=success,
        category=res.get("category"),
        content=content,
    )


@app.post(
    "/api/problems/{identifier}/generate",
    response_model=GenerateResult,
    tags=["problems"],
)
async def generate_problem_code(identifier: str):
    """Generate Go code for a problem by running the code-engine workflow.

    Resolves the problem from the local cache (no live LeetCode fetch), then runs
    the intent→summarize→generate→compile(+fix) pipeline and returns the result.
    """
    return await asyncio.to_thread(_do_generate, identifier)


@app.get("/api/go-code", response_model=PaginatedGoCode, tags=["go-code"])
def list_go_code(
    search: str | None = Query(
        None, description="Substring match on task name (case-insensitive)"
    ),
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
    problem_map = _problem_norm_map()
    items = [_go_code_summary(g, problem_map) for g in page]
    return PaginatedGoCode(total=total, count=len(items), limit=limit, offset=offset, items=items)


@app.get("/api/go-code/{task_name}", response_model=GoCodeDetail, tags=["go-code"])
def get_go_code(task_name: str):
    folder = _resolve_go_code_folder(task_name)
    if folder is None:
        raise HTTPException(status_code=404, detail=f"Go code task not found: {task_name}")
    go_files = sorted(folder.glob("*.go"))
    if not go_files:
        raise HTTPException(status_code=404, detail=f"No .go file in task: {task_name}")
    return _go_code_detail(go_files[0])


@app.get("/api/go-code/{task_name}/raw", tags=["go-code"])
def get_go_code_raw(task_name: str):
    folder = _resolve_go_code_folder(task_name)
    if folder is None:
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
