"""Persistence layer for LeetCode problem records."""

from __future__ import annotations

import json
from pathlib import Path

from features.problems.models import normalize_problem, render_problem_markdown


def save_problem(problem: dict, output_dir: Path, save_markdown: bool = True) -> dict:
    """
    Persist one problem.

    Canonical storage is ``<output_dir>/<slug>.json`` (machine-readable, used by
    the workflow to resolve problems programmatically). When ``save_markdown`` is
    True, a human-readable ``<slug>.md`` view is also written next to it.
    Returns the canonical record.
    """
    record = normalize_problem(problem)
    output_dir = Path(output_dir)
    slug = record["titleSlug"]

    json_path = output_dir / f"{slug}.json"
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if save_markdown:
        md_path = output_dir / f"{slug}.md"
        md_path.write_text(render_problem_markdown(record), encoding="utf-8")

    return record


def _summarize(record: dict) -> dict:
    """Reduce a canonical record to a lightweight index entry."""
    return {
        "id": record.get("questionFrontendId", ""),
        "slug": record.get("titleSlug", ""),
        "title": record.get("title", ""),
        "difficulty": record.get("difficulty", "Unknown"),
        "tags": [t.get("name", "") for t in record.get("topicTags", [])],
        "paid": bool(record.get("isPaidOnly")),
        "file": f"{record.get('titleSlug', '')}.json",
    }


def save_index(records: list[dict], output_dir: Path) -> Path:
    """Write the problem index to ``<output_dir>/README.md``.

    ``records`` is a list of canonical records (or ``_summarize`` outputs).
    """
    index_path = output_dir / "README.md"
    lines: list[str] = []
    lines.append("# LeetCode Problem Set")
    lines.append("")
    lines.append(
        f"_Generated locally from LeetCode's GraphQL API. "
        f"Total problems in index: **{len(records)}**._"
    )
    lines.append("")
    lines.append("| ID | Title | Difficulty | Tags | Paid | File |")
    lines.append("|----|-------|------------|------|------|------|")

    for r in records:
        qid = r.get("id") or r.get("questionFrontendId") or ""
        title = r.get("title", "Unknown")
        slug = r.get("slug") or r.get("titleSlug") or ""
        difficulty = r.get("difficulty", "Unknown")
        tags = ", ".join(r.get("tags") or [])
        paid = "Yes" if r.get("paid") or r.get("isPaidOnly") else "No"
        file_link = f"[{slug}.md]({slug}.md)"
        title_link = f"[{title}](https://leetcode.com/problems/{slug}/)"
        # Escape pipes inside markdown table cells.
        tags = tags.replace("|", "\\|")
        lines.append(f"| {qid} | {title_link} | {difficulty} | {tags} | {paid} | {file_link} |")

    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def save_index_json(records: list[dict], output_dir: Path) -> Path:
    """Write a lightweight master index (``problems_index.json``) for fast lookup."""
    index_path = output_dir / "problems_index.json"
    payload = {
        "count": len(records),
        "problems": [_summarize(r) for r in records],
    }
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path


def load_problem_file(path) -> dict | None:
    """Load a canonical record from a ``.json`` or ``.md`` problem file."""
    path = Path(path)
    if not path.exists():
        return None
    if path.suffix == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    if path.suffix == ".md":
        text = path.read_text(encoding="utf-8")
        slug = path.stem
        title = slug
        import re
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if m:
            title = m.group(1).strip()
        return {
            "title": title,
            "titleSlug": slug,
            "questionFrontendId": "",
            "difficulty": "Unknown",
            "topicTags": [],
            "isPaidOnly": False,
            "url": f"https://leetcode.com/problems/{slug}/",
            "content_html": text,
            "description_md": text,
            "exampleTestcaseList": [],
            "hints": [],
            "codeSnippets": [],
            "metaData": "",
            "go_template": "",
        }
    return None
