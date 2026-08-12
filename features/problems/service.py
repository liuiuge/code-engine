"""High-level LeetCode problem resolution and enrichment service."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from features.problems.client import fetch_problem_detail, fetch_problem_list
from features.problems.models import _slugify_filename, normalize_problem
from features.problems.storage import (
    load_problem_file,
    save_index,
    save_index_json,
    save_problem,
)
from infrastructure.logger import logger
from infrastructure.paths import DEFAULT_PROBLEMS_DIR

DEFAULT_OUTPUT_DIR = DEFAULT_PROBLEMS_DIR


def _normalize_query(query: str) -> dict:
    """Parse a user query into structured lookup hints."""
    q = (query or "").strip()
    info = {"raw": q, "slug": None, "id": None, "title": None, "url": None}
    if not q:
        return info
    m = re.search(r"leetcode\.com/problems/([^/?#]+)", q, re.IGNORECASE)
    if m:
        info["slug"] = m.group(1).lower()
        info["url"] = q
        return info
    if q.isdigit():
        info["id"] = q
        return info
    # Treat as a slug candidate (allow spaces -> dashes) or a free title.
    slug_candidate = q.lower().replace(" ", "-")
    info["slug"] = slug_candidate
    info["title"] = q
    return info


def find_local_problem(query: str, output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict | None:
    """
    Resolve a problem from the local cache (no network).

    Accepts a slug, frontend ID, title (exact or substring), or a LeetCode URL.
    Looks up ``problems_index.json`` first, then falls back to scanning
    ``.json`` / ``.md`` files in ``output_dir``.
    """
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None
    info = _normalize_query(query)
    slug = info["slug"]
    qid = info["id"]
    title = info["title"]

    candidates: list[Path] = []
    index_path = output_dir / "problems_index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = {}
        for entry in index.get("problems", []):
            eslug = (entry.get("slug") or "").lower()
            eid = str(entry.get("id") or "")
            etitle = (entry.get("title") or "").lower()
            if slug and eslug == slug.lower():
                candidates.append(output_dir / entry.get("file", f"{eslug}.json"))
            elif qid and eid == qid:
                candidates.append(output_dir / entry.get("file", f"{eslug}.json"))
            elif title and etitle and title.lower() in etitle:
                candidates.append(output_dir / entry.get("file", f"{eslug}.json"))
    else:
        for f in sorted(output_dir.glob("*")):
            if f.suffix in (".json", ".md"):
                candidates.append(f)

    seen: set = set()
    for cand in candidates:
        if not cand.exists() or cand in seen:
            continue
        seen.add(cand)
        rec = load_problem_file(cand)
        if not rec:
            continue
        rslug = (rec.get("titleSlug") or "").lower()
        rid = str(rec.get("questionFrontendId") or "")
        rtitle = (rec.get("title") or "").lower()
        if slug and rslug == slug.lower():
            return rec
        if qid and rid == qid:
            return rec
        if title and title.lower() in rtitle:
            return rec
    return None


def fetch_live_problem(query: str) -> dict | None:
    """Fetch a single problem live from LeetCode by slug/URL and return its record."""
    info = _normalize_query(query)
    slug = info["slug"]
    if not slug:
        return None
    try:
        detail = fetch_problem_detail(slug)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning(f"[problems] live fetch failed for '{query}': {exc}")
        return None
    if not detail:
        return None
    detail.setdefault("titleSlug", slug)
    return normalize_problem(detail)


def resolve_problem(query: str, output_dir: str | Path = DEFAULT_OUTPUT_DIR,
                    live: bool = True) -> dict | None:
    """Resolve a problem locally first; optionally fall back to a live fetch."""
    local = find_local_problem(query, output_dir=output_dir)
    if local:
        return local
    if live:
        return fetch_live_problem(query)
    return None


def list_local_problems(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> list[dict]:
    """Return the list of locally cached problem summaries."""
    output_dir = Path(output_dir)
    index_path = output_dir / "problems_index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            return index.get("problems", [])
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: scan both .json and .md (covers legacy Markdown-only caches).
    out: list[dict] = []
    seen: set = set()
    for f in sorted(output_dir.glob("*")):
        if f.suffix not in (".json", ".md") or f.name in ("README.md", "problems_index.json"):
            continue
        rec = load_problem_file(f)
        if not rec:
            continue
        key = rec.get("titleSlug")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": rec.get("questionFrontendId", ""),
            "slug": key,
            "title": rec.get("title", ""),
            "difficulty": rec.get("difficulty", "Unknown"),
            "tags": [t.get("name", "") for t in rec.get("topicTags", [])],
            "paid": bool(rec.get("isPaidOnly")),
            "file": f"{key}.json",
        })
    return out


def problem_to_input(problem: dict) -> str:
    """
    Build the ``input_question`` string consumed by the workflow from a problem
    record. The result is plain Markdown text the code generator can read.
    """
    description = problem.get("description_md") or problem.get("content_html") or ""
    examples = problem.get("exampleTestcaseList") or []
    go_template = problem.get("go_template") or ""

    lines: list[str] = []
    lines.append("使用Golang 完成题目")
    lines.append("## 题目描述")
    lines.append("")
    lines.append(description.strip() if description else "_No description available._")
    lines.append("")
    if examples:
        lines.append("## 示例")
        lines.append("")
        for i, ex in enumerate(examples, 1):
            lines.append(f"**Example {i}:**")
            lines.append("")
            lines.append("```text")
            lines.append(ex)
            lines.append("```")
            lines.append("")
    if go_template:
        lines.append("## Go 模板")
        lines.append("")
        lines.append("```go")
        lines.append(go_template.rstrip("\n"))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def enrich_problem_set(output_dir: str | Path = DEFAULT_OUTPUT_DIR,
                       category: str = "",
                       filters: dict | None = None,
                       page_limit: int = 50,
                       max_problems: int | None = None,
                       fetch_details: bool = True,
                       delay: float = 0.2,
                       save_markdown: bool = True) -> dict:
    """
    Enrich the local problem set and persist it under ``output_dir``.

    Args:
        output_dir:    Where to write ``<slug>.json`` (+ optional ``.md``) files.
        category:      LeetCode category slug (e.g. ``"algorithms"``); ``""`` = all.
        filters:       ``QuestionListFilterInput`` filter dict (difficulty, tags, etc.).
        page_limit:    Problems fetched per GraphQL page.
        max_problems:  Stop after this many problems (``None`` = all available).
        fetch_details: If True, fetch & save a record per problem.
        delay:         Seconds to sleep between detail requests (be polite).
        save_markdown: If True, also write a human-readable ``.md`` per problem.

    Returns:
        A summary dict with counts and the output directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[problems] fetching problem list...")
    problems = fetch_problem_list(
        category=category, filters=filters, page_limit=page_limit, max_problems=max_problems
    )
    logger.info(f"[problems] list complete: {len(problems)} problems")

    records: list[dict] = []
    if fetch_details:
        logger.info(f"[problems] fetching details for {len(problems)} problems (delay={delay}s)...")
        for i, p in enumerate(problems, 1):
            slug = p.get("titleSlug")
            if not slug:
                continue
            try:
                detail = fetch_problem_detail(slug)
                if detail:
                    # Merge list-level fields that the detail query does not return.
                    detail.setdefault("titleSlug", slug)
                    detail.setdefault("title", p.get("title"))
                    detail.setdefault("difficulty", p.get("difficulty"))
                    detail.setdefault("topicTags", p.get("topicTags", []))
                    detail.setdefault("questionFrontendId", p.get("frontendQuestionId"))
                    detail.setdefault("isPaidOnly", p.get("paidOnly", False))
                    record = save_problem(detail, output_dir, save_markdown=save_markdown)
                    records.append(record)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning(f"[problems] failed to save {slug}: {exc}")
            if i % 50 == 0:
                logger.info(f"[problems] saved {i}/{len(problems)} problem files")
            if delay:
                time.sleep(delay)
    else:
        # Build lightweight records from the list payload so the index still works.
        for p in problems:
            slug = p.get("titleSlug") or _slugify_filename(p.get("title", "problem"))
            records.append({
                "title": p.get("title", slug),
                "titleSlug": slug,
                "questionFrontendId": str(p.get("frontendQuestionId") or p.get("questionId") or ""),
                "difficulty": p.get("difficulty", "Unknown"),
                "topicTags": p.get("topicTags", []),
                "isPaidOnly": bool(p.get("paidOnly") or p.get("isPaidOnly", False)),
            })

    index_path = save_index(records, output_dir)
    index_json_path = save_index_json(records, output_dir)
    logger.info(f"[problems] wrote index -> {index_path}")
    logger.info(f"[problems] wrote index json -> {index_json_path}")

    return {
        "output_dir": str(output_dir),
        "problem_count": len(records),
        "index_path": str(index_path),
        "index_json_path": str(index_json_path),
    }
