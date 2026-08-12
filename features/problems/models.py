"""Problem normalization and Markdown rendering."""

from __future__ import annotations

import html
import json
import re


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
        # NOTE: do NOT html.unescape here — leave &lt;/&gt; as entities so the later
        # "strip remaining tags" step can't mistake `2 <= x`; the final unescape resolves entities.
        code = code.strip("\n")
        pre_blocks.append("```text\n" + code + "\n```")
        return f"\x00PRE{len(pre_blocks) - 1}\x00"

    text = re.sub(r"<pre[^>]*>.*?</pre>", _stash_pre, text, flags=re.DOTALL | re.IGNORECASE)

    # Superscript / subscript (e.g. 10<sup>4</sup> -> 10^4).
    text = re.sub(
        r"<sup[^>]*>(.*?)</sup>",
        lambda m: f"^{_unescape(m.group(1))}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<sub[^>]*>(.*?)</sub>",
        lambda m: f"_{_unescape(m.group(1))}",
        text,
        flags=re.IGNORECASE,
    )

    # Inline code. Use [^<]* (not .*?) so the match can never cross a tag
    # boundary and swallow unrelated <code> spans elsewhere in the document.
    # Do NOT unescape here — leave &lt;/&gt; as entities so the later "strip
    # remaining tags" step cannot mistake `a <= b` for a tag; the final
    # html.unescape() resolves everything at the end.
    text = re.sub(
        r"<code[^>]*>([^<]*)</code>",
        lambda m: f"`{m.group(1)}`",
        text,
        flags=re.IGNORECASE | re.ASCII,
    )

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
    s = _unescape(raw)  # unescape (handles &nbsp;) + strip
    s = re.sub(r"\s+", " ", s).strip()  # collapse any internal whitespace
    left = " " if lead else ""
    right = " " if trail else ""
    return f"{left}{marker}{s}{marker}{right}"


def _slugify_filename(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug).lower()


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
