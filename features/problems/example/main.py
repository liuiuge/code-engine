"""
features/problems/example/main.py — CLI entry for fetching LeetCode problems.

Run:
    python -m features.problems.example.main --limit 50
    python -m features.problems.example.main --all --delay 0.3
"""

from __future__ import annotations

import argparse

from features.problems.service import enrich_problem_set
from infrastructure.paths import DEFAULT_PROBLEMS_DIR
from infrastructure.logger import logger


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich the local LeetCode problem set.")
    parser.add_argument("--output", default=str(DEFAULT_PROBLEMS_DIR), help="Output directory.")
    parser.add_argument("--category", default="", help="LeetCode category slug (empty = all).")
    parser.add_argument("--page-limit", type=int, default=50, help="Problems per GraphQL page.")
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max number of problems to fetch (default 50; use --all for everything).",
    )
    parser.add_argument("--all", action="store_true", help="Fetch every available problem.")
    parser.add_argument(
        "--no-details", action="store_true",
        help="Only write the index, skip per-problem files.",
    )
    parser.add_argument(
        "--no-md", action="store_true",
        help="Only write JSON + index; skip the per-problem .md view.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.2,
        help="Delay between detail requests (seconds).",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    max_problems = None if args.all else args.limit
    summary = enrich_problem_set(
        output_dir=args.output,
        category=args.category,
        page_limit=args.page_limit,
        max_problems=max_problems,
        fetch_details=not args.no_details,
        save_markdown=not args.no_md,
        delay=args.delay,
    )
    logger.info(f"[problems] done: {summary}")
