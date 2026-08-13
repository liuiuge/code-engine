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
