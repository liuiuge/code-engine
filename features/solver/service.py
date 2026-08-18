"""Solver service: unified entry points for the code-generation pipeline."""

from __future__ import annotations

from pathlib import Path

from features.problems.custom_storage import save_custom_question
from features.problems.service import (
    DEFAULT_OUTPUT_DIR,
    load_problem_file,
    problem_to_input,
    resolve_problem,
)
from features.solver.precheck import is_leetcode_reference, precheck_custom_question
from features.solver.workflow import app
from infrastructure.config import get_verify_mode
from infrastructure.constants import PREFERENCE_DEFAULT
from infrastructure.paths import DEFAULT_CUSTOM_QUESTIONS_DIR


def run_pipeline(
    input_question: str,
    difficulty: str | None,
    leetcode_slug: str | None,
    problem_record: dict | None = None,
    verify_mode: str | None = None,
    preference: str | None = None,
) -> dict:
    """Run the code-generation workflow and return its final state dict.

    ``problem_record`` carries the example test cases the verifier needs; it is
    ``None`` for freeform coding questions (the verifier then no-ops). ``verify_mode``
    overrides the global default when provided. ``preference`` selects the
    speed/quality routing of the escalatable roles (P1-9, default ``"speed"``).
    """
    return app.invoke({
        "input_question": input_question,
        "difficulty": difficulty,
        "leetcode_slug": leetcode_slug,
        "problem_record": problem_record,
        "verify_mode": verify_mode or get_verify_mode(),
        "preference": preference or PREFERENCE_DEFAULT,
    })


def generate_for_problem(
    query: str,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    live: bool = True,
    verify_mode: str | None = None,
    preference: str | None = None,
) -> dict:
    """
    Resolve a LeetCode problem and generate its Go code via the workflow.

    Shared by the CLI (features/solver/example/main.py) and the FastAPI layer
    (web/routes/problems.py). Returns the workflow result dict (contains
    ``code_path``, ``build_result``, ``category``, ``task_dir``, ``verify_result``,
    ``used_model``, etc.). Raises ``ValueError`` if the problem cannot be resolved.
    """
    record = resolve_problem(query, output_dir=problems_dir, live=live)
    if not record:
        raise ValueError(f"Could not resolve problem: {query}")
    input_question = problem_to_input(record)
    difficulty = record.get("difficulty")
    leetcode_slug = record.get("titleSlug")
    return run_pipeline(
        input_question,
        difficulty,
        leetcode_slug,
        problem_record=record,
        verify_mode=verify_mode,
        preference=preference,
    )


# --------------------------------------------------------------------------- #
# Custom (non-LeetCode) question flow — see specs/custom-questions
# --------------------------------------------------------------------------- #
def generate_for_query(
    query: str,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
    no_confirm: bool = False,
    verify_mode: str | None = None,
    live: bool = False,
    preference: str | None = None,
) -> dict:
    """Unified generate entry point (CK-09 entry).

    - A resolvable LeetCode reference (slug/id/URL) -> legacy ``generate_for_problem``
      path, with **no** dedup precheck.
    - Otherwise -> the custom flow (precheck + confirm-or-create, CQ-01..06).

    ``preference`` (P1-9) is forwarded to the pipeline on both branches.
    Returns a dict with at least ``status`` and ``needs_confirm`` keys.
    """
    if is_leetcode_reference(query, problems_dir=problems_dir):
        record = resolve_problem(query, output_dir=problems_dir, live=live)
        if not record:
            raise ValueError(f"Could not resolve LeetCode problem: {query}")
        result = run_pipeline(
            problem_to_input(record),
            record.get("difficulty"),
            record.get("titleSlug"),
            problem_record=record,
            verify_mode=verify_mode,
            preference=preference,
        )
        return {
            "status": "leetcode",
            "number": None,
            "needs_confirm": False,
            "result": result,
        }
    return generate_custom_question(
        query,
        problems_dir=problems_dir,
        custom_dir=custom_dir,
        no_confirm=no_confirm,
        verify_mode=verify_mode,
        preference=preference,
    )


def generate_custom_question(
    input_text: str,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
    no_confirm: bool = False,
    verify_mode: str | None = None,
    preference: str | None = None,
) -> dict:
    """Run the custom-question flow for a free-text question (CQ-03/04/05/06).

    Pipeline-precheck (Agent dedup). If it matches an existing problem and we are
    NOT in headless mode, return a ``needs_confirm`` payload WITHOUT starting the
    solver. Otherwise create a custom record (numbered C-<seq>) and run the
    pipeline, persisting the result into the custom store.
    """
    pre = precheck_custom_question(input_text, problems_dir=problems_dir)

    if pre["status"] == "match" and not no_confirm:
        # CK-04: hit -> ask for confirmation, do NOT start the solver.
        return {
            "status": "needs_confirm",
            "number": None,
            "needs_confirm": True,
            "matched_slug": pre.get("matched_slug"),
            "reason": pre.get("reason"),
            "input": input_text,
        }

    # no_match, or match + headless (no_confirm) -> create & run (CQ-06 / CQ-05).
    return _create_and_run(
        input_text, pre, problems_dir, custom_dir, verify_mode, preference=preference
    )


def _create_and_run(
    input_text: str,
    pre: dict,
    problems_dir: str | Path,
    custom_dir: str | Path,
    verify_mode: str | None,
    preference: str | None = None,
) -> dict:
    """Run the pipeline for a free-text question and persist a custom record."""
    result = run_pipeline(
        input_question=input_text,
        difficulty=None,
        leetcode_slug=None,
        problem_record=None,
        verify_mode=verify_mode,
        preference=preference,
    )
    record = save_custom_question(
        {
            "input_question": input_text,
            "category": result.get("category"),
            "task_dir": result.get("task_dir"),
            "code_path": result.get("code_path"),
            "build_result": result.get("build_result"),
            "final_output": result.get("final_output"),
            "verify_result": result.get("verify_result"),
            "verify_details": result.get("verify_details"),
            "precheck": pre,
        },
        custom_dir=custom_dir,
    )
    return {
        "status": "created",
        "number": record["number"],
        "needs_confirm": False,
        "result": result,
        "record": record,
    }


def confirm_custom_question(
    input_text: str,
    decision: str,
    matched_slug: str | None = None,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
    verify_mode: str | None = None,
    preference: str | None = None,
) -> dict:
    """Resolve a ``needs_confirm`` outcome (CQ-03 confirm step).

    - ``decision == "reuse"`` and a ``matched_slug`` is given -> run the pipeline
      for the existing problem; NO custom record is created (CK-05).
    - ``decision == "not_related"`` (or reuse without a slug) -> create a new
      custom record and run the pipeline (CQ-06).
    """
    if decision == "reuse" and matched_slug:
        record = resolve_problem(matched_slug, output_dir=problems_dir, live=False)
        if not record:
            raise ValueError(f"Could not resolve matched problem: {matched_slug}")
        result = run_pipeline(
            problem_to_input(record),
            record.get("difficulty"),
            record.get("titleSlug"),
            problem_record=record,
            verify_mode=verify_mode,
            preference=preference,
        )
        return {
            "status": "reused",
            "number": None,
            "needs_confirm": False,
            "matched_slug": matched_slug,
            "result": result,
        }

    # not_related (or reuse without a resolvable slug) -> new custom record.
    return _create_and_run(
        input_text,
        {"status": "no_match", "matched_slug": None,
         "reason": "user confirmed not related / no match"},
        problems_dir,
        custom_dir,
        verify_mode,
        preference=preference,
    )

