"""Pydantic request/response models for the CodeEngine web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProblemSummary(BaseModel):
    id: str = ""
    slug: str = ""
    title: str = ""
    difficulty: str = "Unknown"
    tags: list[str] = []
    paid: bool = False
    file: str = ""
    has_go_code: bool = False


class ProblemDetail(BaseModel):
    title: str = ""
    titleSlug: str = ""
    questionId: str = ""
    questionFrontendId: str = ""
    difficulty: str = "Unknown"
    topicTags: list[dict] = []
    isPaidOnly: bool = False
    url: str = ""
    description_md: str = ""
    content_html: str = ""
    exampleTestcaseList: list[str] = []
    hints: list[str] = []
    codeSnippets: list[dict] = []
    metaData: str = ""
    go_template: str = ""
    has_go_code: bool = False


class GoCodeSummary(BaseModel):
    task_name: str
    file: str
    rel_path: str
    size_bytes: int = 0
    modified_at: str = ""
    line_count: int = 0
    related_problem: str | None = None  # problem slug, best-effort
    verified: bool | None = None  # None = not checked / no test data


class GoCodeDetail(BaseModel):
    task_name: str
    file: str
    rel_path: str
    size_bytes: int = 0
    modified_at: str = ""
    line_count: int = 0
    related_problem: str | None = None
    content: str = ""
    verified: bool | None = None
    verify_result: str | None = None


class Stats(BaseModel):
    problems_count: int = 0
    go_code_count: int = 0
    difficulties: dict[str, int] = {}
    tags: list[str] = []
    problems_dir: str = ""
    go_code_dir: str = ""


class PaginatedProblems(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[ProblemSummary]


class PaginatedGoCode(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[GoCodeSummary]


class PullQuery(BaseModel):
    """Request body for POST /api/problems/pull (bulk pull of new problems)."""
    limit: int = Field(50, ge=1, le=2000, description="Max problems to consider from LeetCode.")
    category: str = ""
    difficulty: str | None = Field(None, description="Filter by difficulty (Easy/Medium/Hard).")
    tags: list[str] | None = Field(None, description="Filter by topic tag slugs.")
    fetch_details: bool = True
    save_markdown: bool = True
    delay: float = Field(0.2, ge=0.0, description="Seconds between detail requests (be polite).")
    force: bool = Field(False, description="Re-fetch problems that are already cached locally.")


class PullOneResult(BaseModel):
    slug: str
    title: str
    difficulty: str
    file: str
    status: str  # "created" | "updated"
    error: str | None = None


class BulkPullResponse(BaseModel):
    pulled: int
    skipped: int
    errors: list[str] = []
    slugs: list[str] = []
    total_indexed: int
    output_dir: str
    index_json_path: str
    index_path: str


class GenerateResult(BaseModel):
    identifier: str
    task_name: str | None = None
    file: str | None = None
    build_result: str = ""
    success: bool = False
    category: str | None = None
    content: str | None = None
    error: str | None = None
    verified: bool = False
    verify_result: str = ""
    verify_details: list = []
