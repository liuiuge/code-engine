"""Go-code routes: /api/go-code and per-task detail/raw."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path

from web.dependencies import (
    _go_code_detail,
    _go_code_summary,
    _problem_norm_map,
    _resolve_go_code_folder,
    _iter_go_code_files,
)
from web.schemas import GoCodeDetail, PaginatedGoCode

router = APIRouter(tags=["go-code"])


@router.get("/api/go-code", response_model=PaginatedGoCode, tags=["go-code"])
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


@router.get("/api/go-code/{task_name}", response_model=GoCodeDetail, tags=["go-code"])
def get_go_code(task_name: str):
    folder = _resolve_go_code_folder(task_name)
    if folder is None:
        raise HTTPException(status_code=404, detail=f"Go code task not found: {task_name}")
    go_files = sorted(folder.glob("*.go"))
    if not go_files:
        raise HTTPException(status_code=404, detail=f"No .go file in task: {task_name}")
    return _go_code_detail(go_files[0])


@router.get("/api/go-code/{task_name}/raw", tags=["go-code"])
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
