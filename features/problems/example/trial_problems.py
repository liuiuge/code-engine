"""
features/problems/example/trial_problems.py — Trial harness for problem fetching.

Purpose
-------
Reproducibly exercise `features.problems.service.enrich_problem_set()` against a
*throwaway* temporary directory (not the real ``output/problems``), so we can
verify the script works end-to-end without polluting the committed output.

What it does
------------
1. Resolves the project root (parent of ``features/problems/example/``).
2. Points ``enrich_problem_set`` at a temp result dir under ``output/_trial_tmp``.
3. Fetches a small number of problems (``--limit``) WITH details, so we exercise
   both the list query and the per-problem detail query + Markdown rendering.
4. Captures a summary + any exception and writes ``debug/trial_result.json``.

Run:
    python -m features.problems.example.trial_problems           # default: 5 problems
    python -m features.problems.example.trial_problems 20        # fetch 20 problems
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from features.problems.service import enrich_problem_set

# This file lives at <root>/features/problems/example/trial_problems.py.
ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2

    # Temporary result dir — clearly named so it is obviously a throwaway.
    result_dir = ROOT / "output" / "_trial_tmp"
    result_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    summary: dict = {}
    ok = False
    error: str | None = None
    try:
        summary = enrich_problem_set(
            output_dir=result_dir,
            category="",
            page_limit=50,
            max_problems=limit,
            fetch_details=True,
            delay=delay,
        )
        ok = True
    except Exception as exc:  # surface everything to the JSON report
        error = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()

    elapsed = time.time() - started

    # Inspect what actually landed on disk.
    written = sorted(p.name for p in result_dir.glob("*.md")) if result_dir.exists() else []
    index_path = result_dir / "README.md"
    index_lines = index_path.read_text(encoding="utf-8").splitlines() if index_path.exists() else []

    report = {
        "ok": ok,
        "error": error,
        "limit_requested": limit,
        "elapsed_seconds": round(elapsed, 2),
        "summary": summary,
        "result_dir": str(result_dir),
        "files_written": written,
        "file_count": len(written),
        "index_exists": index_path.exists(),
        "index_row_count_estimate": max(0, len(index_lines) - 6),  # minus header lines
    }

    # Keep the report next to the original debug location for continuity.
    debug_dir = ROOT / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    out_path = debug_dir / "trial_result.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 60)
    print("TRIAL RESULT")
    print("=" * 60)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
