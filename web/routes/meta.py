"""Meta routes: /health and /api/stats."""

from __future__ import annotations

from fastapi import APIRouter

from features.problems.service import list_local_problems
from web.dependencies import PROBLEMS_DIR, GO_CODE_DIR, _iter_go_code_files
from web.schemas import Stats

router = APIRouter(tags=["meta"])


@router.get("/health", tags=["meta"])
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


@router.get("/api/stats", response_model=Stats, tags=["meta"])
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
