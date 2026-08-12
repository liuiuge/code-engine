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


def run_pipeline(
    input_question: str,
    difficulty: str | None,
    leetcode_slug: str | None,
) -> dict:
    """Run the code-generation workflow and return its final state dict."""
    return app.invoke({
        "input_question": input_question,
        "difficulty": difficulty,
        "leetcode_slug": leetcode_slug,
    })


def generate_for_problem(
    query: str,
    problems_dir: str | Path = DEFAULT_OUTPUT_DIR,
    live: bool = True,
) -> dict:
    """
    Resolve a LeetCode problem and generate its Go code via the workflow.

    Shared by the CLI (features/solver/example/main.py) and the FastAPI layer
    (web/routes/go_code.py). Returns the workflow result dict (contains
    ``code_path``, ``build_result``, ``category``, ``task_dir``, etc.). Raises
    ``ValueError`` if the problem cannot be resolved.
    """
    record = resolve_problem(query, output_dir=problems_dir, live=live)
    if not record:
        raise ValueError(f"Could not resolve problem: {query}")
    input_question = problem_to_input(record)
    difficulty = record.get("difficulty")
    leetcode_slug = record.get("titleSlug")
    return run_pipeline(input_question, difficulty, leetcode_slug)
