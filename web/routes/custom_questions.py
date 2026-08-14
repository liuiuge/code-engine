"""Custom-question routes: independent /api/custom-questions resource (P1-13).

Endpoints (decided in specs/custom-questions/CUSTOM_QUESTIONS.md §6.3):
  POST /api/custom-questions/precheck   -> dedup precheck (Agent judgment)
  POST /api/custom-questions            -> create (or return needs_confirm)
  GET  /api/custom-questions           -> list custom questions
  GET  /api/custom-questions/{number}  -> open by number
  POST /api/custom-questions/confirm   -> resolve a needs_confirm outcome

The heavy solver pipeline is imported lazily so the API stays importable without
langgraph / langchain-ollama. (Frontend confirmation popup UI is deferred to W2.)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from web.dependencies import CUSTOM_QUESTIONS_DIR, PROBLEMS_DIR
from web.schemas import (
    CustomConfirmRequest,
    CustomCreateRequest,
    CustomGenerateResult,
    CustomPrecheckRequest,
    CustomPrecheckResult,
    CustomQuestionSummary,
)

router = APIRouter(tags=["custom-questions"])


def _problems_dir(req_dir: str | None) -> Path:
    return Path(req_dir) if req_dir else PROBLEMS_DIR


def _custom_dir(req_dir: str | None) -> Path:
    return Path(req_dir) if req_dir else CUSTOM_QUESTIONS_DIR


@router.post("/api/custom-questions/precheck", response_model=CustomPrecheckResult, tags=["custom-questions"])
async def precheck(req: CustomPrecheckRequest):
    """Run the Agent dedup precheck against the local problem set (CK-01..03)."""
    from features.solver.precheck import precheck_custom_question

    def _run():
        return precheck_custom_question(req.text, problems_dir=_problems_dir(req.problems_dir))

    res = await asyncio.to_thread(_run)
    return CustomPrecheckResult(
        status=res.get("status", "no_match"),
        matched_slug=res.get("matched_slug"),
        reason=res.get("reason", ""),
    )


@router.post("/api/custom-questions", response_model=CustomGenerateResult, tags=["custom-questions"])
async def create_custom_question(req: CustomCreateRequest):
    """Create (or, if a match is found and not --no-confirm, request confirmation)."""
    from features.solver.service import generate_custom_question

    def _run():
        return generate_custom_question(
            req.text,
            problems_dir=_problems_dir(req.problems_dir),
            custom_dir=_custom_dir(None),
            no_confirm=req.no_confirm,
        )

    try:
        res = await asyncio.to_thread(_run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return CustomGenerateResult(**res)


@router.post("/api/custom-questions/confirm", response_model=CustomGenerateResult, tags=["custom-questions"])
async def confirm_custom_question(req: CustomConfirmRequest):
    """Resolve a needs_confirm outcome: reuse matched problem or create new."""
    from features.solver.service import confirm_custom_question

    def _run():
        return confirm_custom_question(
            req.text,
            req.decision,
            matched_slug=req.matched_slug,
            problems_dir=_problems_dir(req.problems_dir),
            custom_dir=_custom_dir(None),
        )

    try:
        res = await asyncio.to_thread(_run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return CustomGenerateResult(**res)


@router.get("/api/custom-questions", response_model=list[CustomQuestionSummary], tags=["custom-questions"])
async def list_custom_questions():
    """List all custom questions (isolated from the LeetCode problem set)."""
    from features.problems.custom_storage import list_custom_questions

    def _run():
        return list_custom_questions(custom_dir=_custom_dir(None))

    records = await asyncio.to_thread(_run)
    out = []
    for r in records:
        code_path = r.get("code_path")
        out.append(CustomQuestionSummary(
            number=r.get("number", ""),
            source=r.get("source", "custom"),
            created_at=r.get("created_at", ""),
            category=r.get("category"),
            task_dir=r.get("task_dir"),
            has_code=bool(code_path),
            title=(r.get("input_question") or "")[:80],
        ))
    return out


@router.get("/api/custom-questions/{number}", response_model=dict, tags=["custom-questions"])
async def open_custom_question(number: str):
    """Open a custom question record by its C-<seq> number."""
    from features.problems.custom_storage import load_custom_question

    def _run():
        return load_custom_question(number, custom_dir=_custom_dir(None))

    record = await asyncio.to_thread(_run)
    if not record:
        raise HTTPException(status_code=404, detail=f"Custom question not found: {number}")
    return record
