"""
features/solver/example/main.py — CLI entry for the code-generation pipeline.

Run:
    python -m features.solver.example.main --problem two-sum
    python -m features.solver.example.main -f output/problems/two-sum.json
    python -m features.solver.example.main -c "使用Golang 完成题目 ..."
    python -m features.solver.example.main --list-problems
"""

from __future__ import annotations

import argparse
from pathlib import Path

from features.problems.service import (
    DEFAULT_OUTPUT_DIR,
    list_local_problems,
    load_problem_file,
    problem_to_input,
    resolve_problem,
)
from features.solver.service import generate_for_problem, run_pipeline
from infrastructure.logger import logger

# Default example problem, kept for backward compatibility.
DEFAULT_QUESTION = """
使用Golang 完成题目
## 题目描述

序列化是将一个数据结构或者对象转换为连续的比特位的操作，进而可以将转换后的数据存储在一个文件或内存缓冲区中，同时也可以通过网络传输到另一个计算机环境，采取相反方式重构得到原数据。

请设计一个算法来实现二叉树的序列化与反序列化。这里不限定你的序列化/反序列化算法执行逻辑，你只需要确保一个二叉树可以被序列化为一个字符串并且将这个字符串反序列化为原始的树结构。

**示例：**

```text
输入：root = [1,2,3,null,null,4,5]
输出：[1,2,3,null,null,4,5]

```
"""


def _print_problem_list(output_dir: str) -> None:
    problems = list_local_problems(output_dir)
    if not problems:
        logger.info("[main] no cached problems found. Run `python -m features.problems.example.main` first.")
        return
    logger.info(f"[main] {len(problems)} cached problem(s) in {output_dir}:")
    for p in problems:
        tags = ", ".join(p.get("tags") or [])
        logger.info(f"  - [{p.get('id', '?')}] {p.get('title')} "
                    f"({p.get('difficulty')}, {p.get('slug')}) tags: {tags}")


def build_input_question(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
    """
    Resolve the workflow input from flexible sources:

      - ``--problem`` : a LeetCode problem (slug / ID / title / URL) — resolved
                        from the local cache first, then fetched live if needed.
      - ``--file``    : a saved problem file (``.json`` or ``.md``).
      - ``--custom``  : an arbitrary problem/question string.
      - (none)        : the built-in default example problem.

    Returns ``(input_question, difficulty, leetcode_slug)``. ``difficulty`` is
    the LeetCode difficulty ("Easy"/"Medium"/"Hard"/"Unknown") when a problem
    record is resolved, otherwise ``None`` — it drives the coder's Hard-problem
    escalation. ``leetcode_slug`` is the canonical LeetCode ``titleSlug`` (e.g.
    "two-sum") when a problem record is resolved, otherwise ``None`` — the task
    summarizer uses it to name the task directory.
    """
    if args.problem:
        logger.info(f">>> resolving LeetCode problem: {args.problem}")
        record = resolve_problem(
            args.problem, output_dir=args.problems_dir, live=not args.no_live
        )
        if not record:
            raise SystemExit(
                f"[main] could not resolve problem '{args.problem}'. "
                f"Cache it with `python -m features.problems.example.main` "
                f"or allow live fetch (drop --no-live)."
            )
        return problem_to_input(record), record.get("difficulty"), record.get("titleSlug")

    if args.file:
        logger.info(f">>> loading problem file: {args.file}")
        rec = load_problem_file(Path(args.file))
        if not rec:
            raise SystemExit(f"[main] could not read problem file: {args.file}")
        return problem_to_input(rec), rec.get("difficulty"), rec.get("titleSlug")

    if args.custom is not None:
        return args.custom, None, None

    return DEFAULT_QUESTION, None, None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CodeEngine: generate & compile Go code for a problem."
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--problem", "-p",
        help="LeetCode problem reference: slug, ID, title, or URL "
             "(resolved from local cache, then fetched live if needed).",
    )
    src.add_argument(
        "--file", "-f",
        help="Path to a saved problem file (.json or .md).",
    )
    src.add_argument(
        "--custom", "-c",
        help="A custom problem/question string.",
    )
    parser.add_argument(
        "--problems-dir", default=str(DEFAULT_OUTPUT_DIR),
        help="Where local problems are cached (default: output/problems).",
    )
    parser.add_argument(
        "--no-live", action="store_true",
        help="Do not fetch from LeetCode if the problem is not found locally.",
    )
    parser.add_argument(
        "--list-problems", action="store_true",
        help="List cached problems and exit.",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()

    if args.list_problems:
        _print_problem_list(args.problems_dir)
        raise SystemExit(0)

    logger.info(">>> start workflow...")
    input_question, difficulty, leetcode_slug = build_input_question(args)

    logger.info(f">>> difficulty: {difficulty}")
    logger.info(f">>> leetcode slug: {leetcode_slug}")
    logger.info(f"\n[system log] input question:\n{input_question}")
    result = run_pipeline(input_question, difficulty, leetcode_slug)

    logger.info("\n--- final output ---")
    if result.get("category") == "coding":
        logger.info(f"save code to: {result.get('code_path')}")
        logger.info(f"compile check result:\n{result.get('build_result')}")
    else:
        logger.info(result.get("final_output"))
