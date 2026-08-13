"""Solver service: unified entry points for the code-generation pipeline."""

from __future__ import annotations

from pathlib import Path

from features.problems.service import (
    DEFAULT_OUTPUT_DIR,
    load_problem_file,
    problem_to_input,
    resolve_problem,
)
from features.solver.workflow import app
from infrastructure.config import get_verify_mode


def run_pipeline(
    input_question: str,
    difficulty: str | None,
    leetcode_slug: str | None,
    problem_record: dict | None = None,
    verify_mode: str | None = None,
) -> dict:
    """Run the code-generation workflow and return its final state dict.

    ``problem_record`` carries the example test cases the verifier needs; it is
    ``None`` for freeform coding questions (the verifier then no-ops). ``verify_mode``
    overrides the global default when provided.
    """
    return app.invoke({
        "input_question": input_question,
        "difficulty": difficulty,
        "leetcode_slug": leetcode_slug,
        "problem_record": problem_record,
        "verify_mode": verify_mode or get_verify_mode(),
    })


def generate_for_problem(
    query: str,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    live: bool = True,
    verify_mode: str | None = None,
) -> dict:
    """
    Resolve a LeetCode problem and generate its Go code via the workflow.

    Shared by the CLI (features/solver/example/main.py) and the FastAPI layer
    (web/routes/go_code.py). Returns the workflow result dict (contains
    ``code_path``, ``build_result``, ``category``, ``task_dir``, ``verify_result``,
    etc.). Raises ``ValueError`` if the problem cannot be resolved.
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
    )
