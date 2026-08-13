"""Problem routes: list/detail, go-code link, pull, and generate."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from infrastructure.constants import VERIFY_PASS_MESSAGE
from features.problems.service import (
    fetch_live_problem,
    fetch_problem_detail,
    fetch_problem_list,
    find_local_problem,
    list_local_problems,
    save_problem,
)
from web.dependencies import (
    PROBLEMS_DIR,
    _go_code_detail,
    _go_code_dir_for_problem,
    _rebuild_index,
    _sort_problems,
)
from web.schemas import (
    BulkPullResponse,
    GenerateResult,
    GoCodeDetail,
    PaginatedProblems,
    ProblemDetail,
    ProblemSummary,
    PullOneResult,
    PullQuery,
)

router = APIRouter(tags=["problems"])


@router.get("/api/problems", response_model=PaginatedProblems, tags=["problems"])
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
    from web.dependencies import _go_code_norm_map
    go_norm = _go_code_norm_map()
    items = [
        ProblemSummary(
            id=p.get("id", ""),
            slug=p.get("slug", ""),
            title=p.get("title", ""),
            difficulty=p.get("difficulty", "Unknown"),
            tags=p.get("tags", []) or [],
            paid=bool(p.get("paid")),
            file=p.get("file", ""),
            has_go_code=_go_code_dir_for_problem(p.get("slug", ""), go_norm) is not None,
        )
        for p in page
    ]
    return PaginatedProblems(total=total, count=len(items), limit=limit, offset=offset, items=items)


@router.get("/api/problems/{identifier}", response_model=ProblemDetail, tags=["problems"])
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


@router.get("/api/problems/{identifier}/go-code", response_model=GoCodeDetail, tags=["problems"])
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
def _safe_rebuild_index() -> tuple[int, str | None]:
    """Rebuild the problem index, never raising on a write failure.

    Returns ``(count, error)``. The per-problem ``*.json`` files are the source
    of truth; ``problems_index.json`` / ``README.md`` are a cache that
    ``_load_index`` regenerates on the fly, so a transient permission/lock error
    on the index must NOT 500 the whole pull request.
    """
    try:
        return _rebuild_index(), None
    except Exception as exc:  # pragma: no cover - filesystem dependent
        try:
            count = len(
                [f for f in PROBLEMS_DIR.glob("*.json")
                 if f.name != "problems_index.json"]
            )
        except Exception:
            count = -1
        return count, f"index write failed (problem files saved OK): {exc}"


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
    try:
        _rebuild_index()
    except Exception as exc:
        # The problem JSON is saved; only the index cache failed. Surface as a
        # non-fatal warning so the request still succeeds.
        return PullOneResult(
            slug=slug,
            title=record.get("title", ""),
            difficulty=record.get("difficulty", "Unknown"),
            file=str(local_json),
            status="updated" if existed else "created",
            error=f"problem saved, but index rebuild failed: {exc}",
        )
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

    total_indexed, index_err = _safe_rebuild_index()
    if index_err:
        errors.append(index_err)
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


@router.post("/api/problems/pull", response_model=BulkPullResponse, tags=["problems"])
async def pull_problems(q: PullQuery):
    """Pull new problems from LeetCode (skips already-cached slugs, rebuilds index)."""
    return await asyncio.to_thread(_do_pull, q)


@router.post("/api/problems/{identifier}/pull", response_model=PullOneResult, tags=["problems"])
async def pull_one_problem(identifier: str):
    """Pull a single problem from LeetCode by slug or URL."""
    return await asyncio.to_thread(_do_pull_one, identifier)


# --------------------------------------------------------------------------- #
# Generate endpoint (lazy-imports the solver pipeline)
# --------------------------------------------------------------------------- #
def _do_generate(identifier: str) -> GenerateResult:
    """Run the code-engine workflow to generate Go code for a problem.

    Reuses ``features.solver.service.generate_for_problem`` (which wraps the
    LangGraph pipeline) so the CLI and the API share the same code path. Heavy
    deps (langgraph, langchain-ollama, pyyaml) are imported lazily so the rest of
    the API stays importable even if they are missing.
    """
    try:
        from features.solver.service import generate_for_problem
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

    verify_result = res.get("verify_result", "")
    return GenerateResult(
        identifier=identifier,
        task_name=res.get("task_dir"),
        file=code_path or None,
        build_result=build_result,
        success=success,
        category=res.get("category"),
        content=content,
        verified=verify_result == VERIFY_PASS_MESSAGE,
        verify_result=verify_result,
        verify_details=res.get("verify_details") or [],
    )


@router.post(
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
