class StateKey:
    INPUT_QUESTION = "input_question"
    CATEGORY = "category"
    FINAL_OUTPUT = "final_output"
    CODE_PATH = "code_path"
    BUILD_RESULT = "build_result"
    RETRY_COUNT = "retry_count"
    TASK_DIR = "task_dir"
    DIFFICULTY = "difficulty"
    LEETCODE_SLUG = "leetcode_slug"
    PROBLEM_RECORD = "problem_record"
    VERIFY_MODE = "verify_mode"
    VERIFY_RESULT = "verify_result"
    VERIFY_DETAILS = "verify_details"
    # --- model tuning (P1-9): speed/quality routing preference ---
    PREFERENCE = "preference"
    USED_MODEL = "used_model"


class NodeName:
    INTENT_CLASSIFIER = "intent_classifier_node"
    TASK_SUMMARIZER = "task_summarizer_node"
    CODE_GENERATOR = "code_generator_node"
    GENERAL_ASSISTANT = "general_assistant_node"
    CODE_EXECUTOR = "code_executor_node"
    CODE_FIXER = "code_fixer_node"
    CODE_VERIFIER = "code_verifier_node"


class PromptKey:
    INTENT_CLASSIFIER = "intent_classifier"
    TASK_SUMMARIZER = "task_summarizer"
    CODE_GENERATOR = "code_generator"
    GENERAL_ASSISTANT = "general_assistant"
    CODE_FIXER = "code_fixer"
    # Precheck: Agent-based dedup judgment for custom (non-LeetCode) questions.
    PROBLEM_MATCH = "problem_match"


class Category:
    CODING = "coding"
    GENERAL = "general"


DEFAULT_TASK_NAME = "default_task"
BUILD_SUCCESS_MESSAGE = "static analysis passed, compilation successful"

# Verification sentinels (the code_verifier_node writes these into StateKey.VERIFY_RESULT).
VERIFY_PASS_MESSAGE = "verification passed"
VERIFY_FAIL_PREFIX = "verified_fail: "
VERIFY_SKIP_MESSAGE = "verification skipped"
VERIFY_MODE_DEFAULT = "assert"  # off | smoke | assert
VERIFY_TIMEOUT_DEFAULT = 30  # seconds per `go test` run

# Model routing preference (P1-9, specs/model-tuning/MODEL_TUNING_SPEC.md §3.1).
# "speed"   -> escalatable roles start on the local model (thinking=false).
# "quality" -> escalatable roles go straight to the online model on the FIRST try.
# Only the *first* attempt is affected; retry/timeout escalation is unchanged.
PREFERENCE_SPEED = "speed"
PREFERENCE_QUALITY = "quality"
PREFERENCE_DEFAULT = PREFERENCE_SPEED

# multi_answer producer (P1-9, specs/model-tuning/MODEL_TUNING_SPEC.md §1 / PF-03).
# LeetCode's API does NOT return a "multi-answer" flag, so code side must produce
# it. A problem is "multi_answer" when its accepted answer is order-independent
# (e.g. two-sum index pairs [0,1] vs [1,0] are both valid). The verifier then
# compares such lists order-insensitively (see verifier.py cevEqual normalize).
# The allowlist is maintained HERE, one place, by PM/ops as more known problems
# are catalogued.
MULTI_ANSWER_SLUGS = frozenset({
    "two-sum",
    "two-sum-ii-input-array-is-sorted",
})


def is_multi_answer_problem(slug: str | None, title: str | None = None) -> bool:
    """Return True iff the problem accepts an order-independent answer.

    LeetCode does not expose this flag, so it is produced code-side from the
    problem slug. The verifier relies on the resulting ``record["multi_answer"]``
    to switch to order-insensitive comparison.

    - slug lower-cased in ``MULTI_ANSWER_SLUGS`` -> True
    - slug containing the substring "two-sum" -> True (covers same-family variants)
    - otherwise -> False (order-sensitive answers stay order-sensitive)
    """
    s = (slug or "").lower().strip()
    if s in MULTI_ANSWER_SLUGS:
        return True
    return "two-sum" in s  # same-family variant fallback
