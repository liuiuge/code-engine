"""
problems.py — Enrich the local LeetCode problem set for code-engine.

Uses the public LeetCode GraphQL endpoint (the same queries documented in
https://github.com/akarsh1995/leetcode-graphql-queries) to:

  1. Fetch the problem *list* (paginated, `problemsetQuestionList`).
  2. Fetch *details* for each problem (`question` by `titleSlug`):
     title, difficulty, description (HTML), topic tags, hints, examples,
     and the official code template.
  3. Persist everything under ``output/problems``:
       - ``output/problems/<slug>.json``        -> the canonical record (machine-readable)
       - ``output/problems/<slug>.md``          -> a human-readable view (optional, derived)
       - ``output/problems/README.md``          -> the full problem index
       - ``output/problems/problems_index.json``-> a lightweight index for fast lookup

The canonical record (JSON) is what the workflow reads when you ask ``main.py``
to run a problem "from LeetCode" — it allows reliable lookup by slug / ID /
title / URL and is the single source of truth that the Markdown view and the
``input_question`` string are both derived from.

No third-party dependencies are required — only the Python standard library.

Quick start
-----------
    from problems import enrich_problem_set, resolve_problem, problem_to_input

    # Pull the first 50 problems (default) and save them locally.
    enrich_problem_set()

    # Build a workflow input from a LeetCode problem (local cache, then live).
    record = resolve_problem("two-sum")
    input_question = problem_to_input(record)

    # Or from the command line:
    #   python problems.py --limit 50
    #   python problems.py --all --delay 0.3
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from logger import logger

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "problems"

GRAPHQL_URL = "https://leetcode.com/graphql/"

# A browser-like User-Agent is required; LeetCode rejects empty agents.
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://leetcode.com/problemset/all/",
}

LIST_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total: totalNum
    questions: data {
      acRate
      difficulty
      freqBar
      frontendQuestionId: questionFrontendId
      isFavor
      paidOnly: isPaidOnly
      status
      title
      titleSlug
      topicTags {
        name
        id
        slug
      }
      hasSolution
      hasVideoSolution
    }
  }
}
"""

# A single combined query that returns everything we need for one problem.
DETAIL_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId
    questionFrontendId
    title
    titleSlug
    difficulty
    isPaidOnly
    content
    hints
    exampleTestcaseList
    topicTags {
      name
      slug
    }
    codeSnippets {
      lang
      langSlug
      code
    }
    metaData
  }
}
"""


# --------------------------------------------------------------------------- #
# Low-level GraphQL helper
# --------------------------------------------------------------------------- #

def _graphql(query: str, variables: dict) -> dict:
    """POST a GraphQL request to LeetCode and return the ``data`` payload."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(GRAPHQL_URL, data=body, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        raise RuntimeError(f"LeetCode GraphQL HTTP {exc.code}: {exc.reason}") from exc

    if "errors" in payload and payload["errors"]:
        # Surface GraphQL-level errors but keep going when data is present.
        logger.warning(f"[problems] GraphQL errors: {payload['errors']}")
    if "data" not in payload:
        raise RuntimeError(f"Unexpected LeetCode response: {payload}")
    return payload["data"]


# --------------------------------------------------------------------------- #
# HTML -> Markdown (lightweight, dependency-free)
# --------------------------------------------------------------------------- #

def html_to_markdown(content: str) -> str:
    """Convert LeetCode's HTML problem description into readable Markdown."""
    if not content:
        return ""

    text = content

    # Drop <style>/<script> blocks entirely.
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 1) Extract <pre> blocks FIRST into placeholders. Their multi-line content
    #    is converted to a clean fenced code block up front (with DOTALL). This
    #    keeps the rest of the document as plain text so the inline <code> rule
    #    below (intentionally non-DOTALL) can never merge across a <pre> boundary
    #    and corrupt unrelated <code> spans.
    pre_blocks: list[str] = []

    def _stash_pre(m):
        code = re.sub(r"</?pre[^>]*>", "", m.group(0), flags=re.IGNORECASE)
        code = re.sub(r"<code[^>]*>", "", code, flags=re.IGNORECASE)
        code = re.sub(r"</code>", "", code, flags=re.IGNORECASE)
        # Resolve the few inline tags that appear inside examples.
        code = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", code, flags=re.IGNORECASE)
        code = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", code, flags=re.IGNORECASE)
        code = re.sub(r"<[^>]+>", "", code)
        # NOTE: do NOT html.unescape here — leave &lt; / &gt; as entities so the
        # later "strip remaining tags" step cannot mistake `2 <= x` for a tag.
        # The final html.unescape() at the end resolves all entities.
        code = code.strip("\n")
        pre_blocks.append("```text\n" + code + "\n```")
        return f"\x00PRE{len(pre_blocks) - 1}\x00"

    text = re.sub(r"<pre[^>]*>.*?</pre>", _stash_pre, text, flags=re.DOTALL | re.IGNORECASE)

    # Superscript / subscript (e.g. 10<sup>4</sup> -> 10^4).
    text = re.sub(r"<sup[^>]*>(.*?)</sup>", lambda m: f"^{_unescape(m.group(1))}", text, flags=re.IGNORECASE)
    text = re.sub(r"<sub[^>]*>(.*?)</sub>", lambda m: f"_{_unescape(m.group(1))}", text, flags=re.IGNORECASE)

    # Inline code. Use [^<]* (not .*?) so the match can never cross a tag
    # boundary and swallow unrelated <code> spans elsewhere in the document.
    # Do NOT unescape here — leave &lt;/&gt; as entities so the later "strip
    # remaining tags" step cannot mistake `a <= b` for a tag; the final
    # html.unescape() resolves everything at the end.
    text = re.sub(r"<code[^>]*>([^<]*)</code>", lambda m: f"`{m.group(1)}`", text, flags=re.IGNORECASE | re.ASCII)

    # Lists. Consume any leading indentation *before* <li> and the whitespace
    # LeetCode puts right after it.
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]*<li[^>]*>\s*", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"- +", "- ", text)
    text = re.sub(r"</ul>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<ul[^>]*>", "", text, flags=re.IGNORECASE)

    # Links -> [text](href).
    text = re.sub(
        r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        lambda m: f"[{_unescape(m.group(2))}]({m.group(1)})",
        text,
        flags=re.IGNORECASE,
    )

    # Bold / italic.
    text = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>",
                  lambda m: _fmt_emphasis("**", m.group(2)), text, flags=re.IGNORECASE)
    text = re.sub(r"<(em|i)[^>]*>(.*?)</\1>",
                  lambda m: _fmt_emphasis("*", m.group(2)), text, flags=re.IGNORECASE)

    # Paragraph / line breaks -> newlines.
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Strip any remaining tags (re.ASCII keeps '>' strictly ASCII so it can never
    # be matched by Unicode case-folding).
    text = re.sub(r"<[^>]+>", "", text, flags=re.ASCII)

    # Restore the stashed <pre> code blocks now that all tag-stripping is done.
    def _restore_pre(m):
        idx = int(m.group(1))
        return pre_blocks[idx] if 0 <= idx < len(pre_blocks) else ""
    text = re.sub(r"\x00PRE(\d+)\x00", _restore_pre, text)

    # Unescape HTML entities (&nbsp; -> space, &lt; -> <, etc.) and tidy whitespace.
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u2009", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)

    # Collapse 3+ newlines into a double newline and trim.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() + "\n"


def _unescape(s: str) -> str:
    return html.unescape(s).strip()


def _fmt_emphasis(marker: str, raw: str) -> str:
    """
    Format a <strong>/<em> span as Markdown emphasis.

    Leading/trailing whitespace inside the source (often a ``&nbsp;`` used as a
    word separator) is moved OUTSIDE the emphasis markers, so we never produce
    glued output like ``**Follow-up:**Can you`` nor a stray space like
    ``**Follow-up: **``.
    """
    lead = raw[:1].isspace() or raw.startswith("&nbsp;")
    trail = (len(raw) > 0 and (raw[-1:].isspace() or raw.endswith("&nbsp;")))
    s = _unescape(raw)                  # unescape (handles &nbsp;) + strip
    s = re.sub(r"\s+", " ", s).strip()  # collapse any internal whitespace
    left = " " if lead else ""
    right = " " if trail else ""
    return f"{left}{marker}{s}{marker}{right}"


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def fetch_problem_list(category: str = "", filters: dict | None = None,
                       page_limit: int = 50, max_problems: int | None = None) -> list[dict]:
    """
    Fetch the problem list, paginating through LeetCode's GraphQL endpoint.

    Returns a list of lightweight problem dicts (no description yet).
    """
    filters = filters or {}
    collected: list[dict] = []
    skip = 0

    while True:
        data = _graphql(LIST_QUERY, {
            "categorySlug": category,
            "skip": skip,
            "limit": page_limit,
            "filters": filters,
        })
        block = data["problemsetQuestionList"]
        questions = block.get("questions") or []
        if not questions:
            break

        for q in questions:
            collected.append(q)
            if max_problems is not None and len(collected) >= max_problems:
                logger.info(f"[problems] reached max_problems={max_problems}, stopping list fetch")
                return collected

        total = block.get("total", 0)
        logger.info(f"[problems] fetched {len(collected)}/{total} problems (skip={skip})")
        if len(collected) >= total:
            break
        skip += page_limit

    return collected


def fetch_problem_detail(title_slug: str) -> dict | None:
    """Fetch full detail for one problem by its ``titleSlug``."""
    data = _graphql(DETAIL_QUERY, {"titleSlug": title_slug})
    return data.get("question")


def _go_template(problem: dict) -> str:
    """
    Build a LeetCode-style Go template for the problem.

    The official ``codeSnippets`` list does not include Go server-side, so we
    reconstruct a standard template from the problem's ``metaData`` (which
    describes the function signature / struct shapes).
    """
    meta_raw = problem.get("metaData") or ""
    try:
        meta = json.loads(meta_raw) if meta_raw.strip() else {}
    except json.JSONDecodeError:
        meta = {}

    name = (meta.get("name") or "Solution").replace(" ", "")
    params = meta.get("params", [])
    returns = meta.get("return", {})

    def _go_type(t: str) -> str:
        if not t:
            return "interface{}"
        # Array types are expressed as "integer[]", "string[]", etc.
        m = re.match(r"^(\w+)\[\]$", t)
        if m:
            base = {
                "integer": "int",
                "string": "string",
                "double": "float64",
                "boolean": "bool",
                "TreeNode": "*TreeNode",
                "ListNode": "*ListNode",
            }.get(m.group(1), m.group(1))
            return "[]" + base
        return {
            "integer": "int",
            "string": "string",
            "TreeNode": "*TreeNode",
            "ListNode": "*ListNode",
            "boolean": "bool",
            "double": "float64",
        }.get(t, t)

    args = ", ".join(f"p{i}: {_go_type(p.get('type'))}" for i, p in enumerate(params))
    ret = _go_type(returns.get("type")) if returns else ""
    ret_decl = f" {ret}" if ret else ""

    return (
        f"func {name}({args}){ret_decl} {{\n"
        f"\t// TODO: implement\n"
        f"\treturn{(' ' + _zero(ret)) if ret else ''}\n"
        f"}}\n"
    )


def _zero(go_type: str) -> str:
    if go_type in ("int", "float64"):
        return "0"
    if go_type == "bool":
        return "false"
    if go_type == "string":
        return '""'
    return "nil"


# --------------------------------------------------------------------------- #
# Normalization, rendering & saving
# --------------------------------------------------------------------------- #

def _slugify_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug).lower()


def normalize_problem(problem: dict) -> dict:
    """
    Enrich a raw LeetCode problem dict into a canonical, self-contained record.

    The record is the single source of truth stored as JSON. The Markdown view
    and the workflow's ``input_question`` string are both derived from it.
    """
    slug = problem.get("titleSlug") or _slugify_filename(problem.get("title", "problem"))
    qid = problem.get("questionFrontendId") or problem.get("questionId") or ""
    tags = problem.get("topicTags") or []
    record = {
        "title": problem.get("title", slug),
        "titleSlug": slug,
        "questionId": problem.get("questionId") or "",
        "questionFrontendId": str(qid) if qid != "" else "",
        "difficulty": problem.get("difficulty", "Unknown"),
        "topicTags": [{"name": t.get("name", ""), "slug": t.get("slug", "")} for t in tags],
        "isPaidOnly": bool(problem.get("isPaidOnly") or problem.get("paidOnly", False)),
        "url": f"https://leetcode.com/problems/{slug}/",
        "content_html": problem.get("content", ""),
        "description_md": html_to_markdown(problem.get("content", "")),
        "exampleTestcaseList": problem.get("exampleTestcaseList") or [],
        "hints": problem.get("hints") or [],
        "codeSnippets": problem.get("codeSnippets") or [],
        "metaData": problem.get("metaData") or "",
        "go_template": _go_template(problem),
    }
    return record


def render_problem_markdown(record: dict) -> str:
    """Render a canonical record back into the human-readable Markdown view."""
    slug = record["titleSlug"]
    tags = ", ".join(t.get("name", "") for t in record.get("topicTags", []))
    paid = "Yes" if record.get("isPaidOnly") else "No"
    examples = record.get("exampleTestcaseList") or []
    hints = record.get("hints") or []

    lines: list[str] = []
    lines.append(f"# {record.get('title', slug)}")
    lines.append("")
    lines.append(f"- **ID:** {record.get('questionFrontendId', '')}")
    lines.append(f"- **Difficulty:** {record.get('difficulty', 'Unknown')}")
    lines.append(f"- **Tags:** {tags or 'N/A'}")
    lines.append(f"- **Paid only:** {paid}")
    lines.append(f"- **Link:** {record.get('url', '')}")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(record.get("description_md") or "_No description available._")
    lines.append("")
    lines.append("## Examples")
    lines.append("")
    if examples:
        for i, ex in enumerate(examples, 1):
            lines.append(f"**Example {i}:**")
            lines.append("")
            lines.append("```text")
            lines.append(ex)
            lines.append("```")
            lines.append("")
    else:
        lines.append("_No example test cases provided._")
        lines.append("")
    if hints:
        lines.append("## Hints")
        lines.append("")
        for i, h in enumerate(hints, 1):
            lines.append(f"{i}. {h}")
        lines.append("")
    lines.append("## Go Template")
    lines.append("")
    lines.append("```go")
    lines.append((record.get("go_template") or "").rstrip("\n"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


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
    lines.append(f"_Generated locally from LeetCode's GraphQL API. Total problems in index: **{len(records)}**._")
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


# --------------------------------------------------------------------------- #
# Resolution & workflow input
# --------------------------------------------------------------------------- #

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
        out.append(_summarize(rec))
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


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

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

    index_path = save_index([_summarize(r) for r in records], output_dir)
    index_json_path = save_index_json(records, output_dir)
    logger.info(f"[problems] wrote index -> {index_path}")
    logger.info(f"[problems] wrote index json -> {index_json_path}")

    return {
        "output_dir": str(output_dir),
        "problem_count": len(records),
        "index_path": str(index_path),
        "index_json_path": str(index_json_path),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich the local LeetCode problem set.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--category", default="", help="LeetCode category slug (empty = all).")
    parser.add_argument("--page-limit", type=int, default=50, help="Problems per GraphQL page.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max number of problems to fetch (default 50; use --all for everything).")
    parser.add_argument("--all", action="store_true", help="Fetch every available problem.")
    parser.add_argument("--no-details", action="store_true", help="Only write the index, skip per-problem files.")
    parser.add_argument("--no-md", action="store_true",
                        help="Only write JSON + index; skip the per-problem .md view.")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between detail requests (seconds).")
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
