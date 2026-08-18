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
    # --- model tuning (P1-9, specs/model-tuning/MODEL_TUNING_SPEC.md) ---
    # Routing preference for the escalatable roles (code_generator / code_fixer):
    # "speed" (default, first try local) | "quality" (first try online/minimax).
    # A TypedDict cannot carry a literal default, so the default is applied by
    # run_pipeline / the nodes (StateKey.PREFERENCE -> PREFERENCE_DEFAULT).
    preference: str
    # Registry name of the model code_generator actually hit on its FIRST try
    # ("local" for speed, "minimax" for quality). Surfaced as
    # GenerateResult.used_model.
    used_model: Optional[str]
