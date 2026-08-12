"""GraphQL HTTP client for LeetCode."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from infrastructure.logger import logger

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
query problemsetQuestionList(
  $categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput
) {
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
