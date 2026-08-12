from typing import TypedDict


class AgentState(TypedDict):
    input_question: str
    category: str
    final_output: str
    code_path: str
    build_result: str
    retry_count: int
    task_dir: str
    difficulty: str
    leetcode_slug: str
