"""Pipeline-precheck dedup judgment for custom (non-LeetCode) questions.

Before the solver runs on a free-text question, this module asks an LLM (Agent)
whether the question is essentially the same as one already in the local problem
set. The decision is intentionally delegated to the model — no independent
string/edit-distance algorithm is implemented (see specs/custom-questions, §6.1).

Contract (mirrors CHECK_SPEC.md CK-01..03 / CK-09):
  - returns {"status": "match"|"no_match", "matched_slug": str|None, "reason": str}
  - degrades to no_match on malformed LLM output (never raises uncaught)
  - skips the LLM entirely when the input is a resolvable LeetCode reference
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from infrastructure.config import PROMPTS, invoke_model
from infrastructure.constants import PromptKey
from infrastructure.paths import DEFAULT_PROBLEMS_DIR
from features.problems.service import find_local_problem, list_local_problems

# Cap how many catalog entries we hand to the model (control token cost).
_CATALOG_LIMIT = 200


def is_leetcode_reference(query: str, problems_dir: str | Path = DEFAULT_PROBLEMS_DIR) -> bool:
    """Cheap structural check: does the query look like a LeetCode reference?

    A LeetCode URL, a numeric frontend id, or a slug that resolves to a cached
    problem all count. When True, the precheck is skipped (CK-09).
    """
    q = (query or "").strip()
    if not q:
        return False
    # URL or pure-numeric id are unambiguous references.
    if re.search(r"leetcode\.com/problems/", q, re.IGNORECASE) or q.isdigit():
        return True
    # A slug/title that resolves against the local cache is a known problem.
    try:
        return find_local_problem(q, output_dir=problems_dir) is not None
    except Exception:
        return False


def _build_catalog(problems_dir: str | Path) -> str:
    """Render a compact title+slug catalog for the prompt (titles/slugs only)."""
    try:
        problems = list_local_problems(problems_dir)[:_CATALOG_LIMIT]
    except Exception:
        problems = []
    if not problems:
        return "(no existing problems)"
    lines = []
    for p in problems:
        title = p.get("title") or p.get("slug") or "?"
        slug = p.get("slug") or "?"
        lines.append(f"- {title} (slug: {slug})")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Best-effort extraction of the first JSON object from model output.

    The model is instructed to return ONLY JSON, but be forgiving: strip
    ```json fences and grab the outermost {...} span. Returns {} on failure.
    """
    if not text:
        return {}
    t = text.strip()
    # Drop code fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL | re.IGNORECASE)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(t[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}


def precheck_custom_question(
    input_text: str,
    problems_dir: str | Path = DEFAULT_PROBLEMS_DIR,
) -> dict:
    """Judge whether ``input_text`` matches an existing local problem.

    Returns a dict with keys: status ("match"|"no_match"), matched_slug, reason.
    On any LLM/parse failure, returns a safe no_match (CK-03) without raising.
    """
    # CK-09: LeetCode references bypass the dedup LLM call entirely.
    if is_leetcode_reference(input_text, problems_dir=problems_dir):
        return {
            "status": "no_match",
            "matched_slug": None,
            "reason": "input is a resolvable LeetCode reference; precheck skipped",
            "skipped": True,
        }

    catalog = _build_catalog(problems_dir)
    prompt = PROMPTS[PromptKey.PROBLEM_MATCH].format(
        existing_problems=catalog,
        input_question=input_text,
    )
    try:
        response = invoke_model(PromptKey.PROBLEM_MATCH, prompt)
        data = _extract_json(response.content)
    except Exception as exc:  # pragma: no cover - defensive degrade
        return {
            "status": "no_match",
            "matched_slug": None,
            "reason": f"precheck model call failed, defaulting to no_match: {exc}",
        }

    # CK-03: malformed / missing field -> safe default, never raise.
    if not isinstance(data, dict) or "exists" not in data:
        return {
            "status": "no_match",
            "matched_slug": None,
            "reason": "precheck response missing 'exists' field; defaulting to no_match",
        }

    exists = bool(data.get("exists"))
    if exists:
        matched = data.get("matched_slug")
        # Only accept a slug string; ignore fabricated/non-string values.
        if not isinstance(matched, str) or not matched:
            matched = None
        return {
            "status": "match",
            "matched_slug": matched,
            "reason": str(data.get("reason", "")),
        }
    return {
        "status": "no_match",
        "matched_slug": None,
        "reason": str(data.get("reason", "")),
    }
