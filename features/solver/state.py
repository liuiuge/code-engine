from typing import Optional, TypedDict


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
    # --- verifier (code_verifier_node) ---
    # Carries the example inputs / expected outputs the verifier needs. None for
    # freeform questions, in which case the verifier no-ops (VERIFY_SKIP_MESSAGE).
    problem_record: Optional[dict]
    # Overrides the global default read from CODE_ENGINE_VERIFY_MODE.
    verify_mode: str
    # Populated by code_verifier_node: pass / fail / skip message.
    verify_result: str
    # Per-case results (list of dicts) populated by code_verifier_node.
    verify_details: list
