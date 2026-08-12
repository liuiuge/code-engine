"""
problems.py — Enrich the local LeetCode problem set for code-engine.

Uses the public LeetCode GraphQL endpoint (the same queries documented in
https://github.com/akarsh1995/leetcode-graphql-queries) to:

  1. Fetch the problem *list* (paginated, `problemsetQuestionList`).
  2. Fetch *details* for each problem (`question` by `titleSlug`):
     title, difficulty, description (HTML), topic tags, hints, examples,
     and the official code template.
  3. Persist everything as Markdown under ``output/problems``:
       - ``output/problems/README.md``  -> the full problem index
       - ``output/problems/<slug>.md``  -> one file per problem

No third-party dependencies are required — only the Python standard library.

Quick start
-----------
    from problems import enrich_problem_set

    # Pull the first 50 problems (default) and save them locally.
    enrich_problem_set()

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
        pre_blocks.append(f"```text\n{code}\n```")
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
# Saving
# --------------------------------------------------------------------------- #

def _slugify_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug).lower()


def save_problem(problem: dict, output_dir: Path) -> Path:
    """Write one problem to ``<output_dir>/<slug>.md`` and return the path."""
    slug = problem.get("titleSlug") or _slugify_filename(problem.get("title", "problem"))
    file_path = output_dir / f"{slug}.md"

    title = problem.get("title", slug)
    difficulty = problem.get("difficulty", "Unknown")
    qid = problem.get("questionFrontendId") or problem.get("questionId") or ""
    tags = ", ".join(t.get("name", "") for t in problem.get("topicTags", []))
    paid = "Yes" if problem.get("isPaidOnly") else "No"
    url = f"https://leetcode.com/problems/{slug}/"
    hints = problem.get("hints") or []

    description = html_to_markdown(problem.get("content", ""))
    go_code = _go_template(problem)
    examples = problem.get("exampleTestcaseList") or []

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **ID:** {qid}")
    lines.append(f"- **Difficulty:** {difficulty}")
    lines.append(f"- **Tags:** {tags or 'N/A'}")
    lines.append(f"- **Paid only:** {paid}")
    lines.append(f"- **Link:** {url}")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(description if description else "_No description available._")
    lines.append("")
    lines.append("## Examples")
    lines.append("")
    if examples:
        for i, ex in enumerate(examples, 1):
            lines.append(f"**Example {i}:**")
            lines.append("")
            lines.append(f"```text\n{ex}\n```")
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
    lines.append(go_code.rstrip("\n"))
    lines.append("```")
    lines.append("")

    file_path.write_text("\n".join(lines), encoding="utf-8")
    return file_path


def save_index(problems: list[dict], output_dir: Path) -> Path:
    """Write the problem index to ``<output_dir>/README.md``."""
    index_path = output_dir / "README.md"
    lines: list[str] = []
    lines.append("# LeetCode Problem Set")
    lines.append("")
    lines.append(f"_Generated locally from LeetCode's GraphQL API. Total problems in index: **{len(problems)}**._")
    lines.append("")
    lines.append("| ID | Title | Difficulty | Tags | Paid | File |")
    lines.append("|----|-------|------------|------|------|------|")

    for p in problems:
        qid = p.get("frontendQuestionId") or p.get("questionFrontendId") or p.get("questionId") or ""
        title = p.get("title", "Unknown")
        slug = p.get("titleSlug", "")
        difficulty = p.get("difficulty", "Unknown")
        tags = ", ".join(t.get("name", "") for t in p.get("topicTags", []))
        paid = "Yes" if p.get("paidOnly") or p.get("isPaidOnly") else "No"
        file_link = f"[{slug}.md]({slug}.md)"
        title_link = f"[{title}](https://leetcode.com/problems/{slug}/)"
        # Escape pipes inside markdown table cells.
        tags = tags.replace("|", "\\|")
        lines.append(f"| {qid} | {title_link} | {difficulty} | {tags} | {paid} | {file_link} |")

    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def enrich_problem_set(output_dir: str | Path = DEFAULT_OUTPUT_DIR,
                       category: str = "",
                       filters: dict | None = None,
                       page_limit: int = 50,
                       max_problems: int | None = None,
                       fetch_details: bool = True,
                       delay: float = 0.2) -> dict:
    """
    Enrich the local problem set and persist it under ``output_dir``.

    Args:
        output_dir:    Where to write ``README.md`` + ``<slug>.md`` files.
        category:      LeetCode category slug (e.g. ``"algorithms"``); ``""`` = all.
        filters:       ``QuestionListFilterInput`` filter dict (difficulty, tags, etc.).
        page_limit:    Problems fetched per GraphQL page.
        max_problems:  Stop after this many problems (``None`` = all available).
        fetch_details: If True, fetch & save a Markdown file per problem.
        delay:         Seconds to sleep between detail requests (be polite).

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
                    save_problem(detail, output_dir)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning(f"[problems] failed to save {slug}: {exc}")
            if i % 50 == 0:
                logger.info(f"[problems] saved {i}/{len(problems)} problem files")
            if delay:
                time.sleep(delay)

    index_path = save_index(problems, output_dir)
    logger.info(f"[problems] wrote index -> {index_path}")

    return {
        "output_dir": str(output_dir),
        "problem_count": len(problems),
        "index_path": str(index_path),
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
        delay=args.delay,
    )
    logger.info(f"[problems] done: {summary}")
