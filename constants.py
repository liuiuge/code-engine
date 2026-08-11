class StateKey:
    INPUT_QUESTION = "input_question"
    CATEGORY = "category"
    FINAL_OUTPUT = "final_output"
    CODE_PATH = "code_path"
    BUILD_RESULT = "build_result"
    RETRY_COUNT = "retry_count"
    TASK_DIR = "task_dir"

class NodeName:
    INTENT_CLASSIFIER = "intent_classifier_node"
    TASK_SUMMARIZER = "task_summarizer_node"
    CODE_GENERATOR = "code_generator_node"
    GENERAL_ASSISTANT = "general_assistant_node"
    CODE_EXECUTOR = "code_executor_node"
    CODE_FIXER = "code_fixer_node"

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
