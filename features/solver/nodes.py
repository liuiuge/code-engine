import re

from infrastructure.config import PROMPTS, invoke_model
from infrastructure.constants import (
    Category,
    DEFAULT_TASK_NAME,
    PromptKey,
    StateKey,
)
from infrastructure.logger import trace_node, trace_node_detailed
from infrastructure.paths import DEFAULT_GO_CODE_DIR
from features.solver.executor import execute_go_code
from features.solver.state import AgentState


@trace_node
def intent_classifier_node(state: AgentState):
    prompt = PROMPTS[PromptKey.INTENT_CLASSIFIER].format(
        input_question=state[StateKey.INPUT_QUESTION]
    )
    response = invoke_model(PromptKey.INTENT_CLASSIFIER, prompt)
    category = Category.CODING if Category.CODING in response.content.lower() else Category.GENERAL
    return {StateKey.CATEGORY: category}


@trace_node_detailed
def task_summarizer_node(state: AgentState):
    # When the input is a resolved LeetCode problem, name the task after its
    # canonical LeetCode slug (e.g. "two-sum") instead of asking the LLM to
    # summarize. This keeps task directories stable and recognizable across runs.
    leetcode_slug = state.get(StateKey.LEETCODE_SLUG)
    if leetcode_slug:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", str(leetcode_slug).strip()).lower()
        if slug:
            return {StateKey.TASK_DIR: slug}

    prompt = PROMPTS[PromptKey.TASK_SUMMARIZER].format(
        input_question=state[StateKey.INPUT_QUESTION]
    )
    response = invoke_model(PromptKey.TASK_SUMMARIZER, prompt)

    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", response.content.strip()).lower()
    if not slug:
        slug = DEFAULT_TASK_NAME

    return {StateKey.TASK_DIR: slug}


@trace_node_detailed
def code_generator_node(state: AgentState):
    prompt = PROMPTS[PromptKey.CODE_GENERATOR].format(
        input_question=state[StateKey.INPUT_QUESTION]
    )
    # Pass difficulty so a LeetCode "Hard" problem escalates straight to online.
    response = invoke_model(
        PromptKey.CODE_GENERATOR,
        prompt,
        difficulty=state.get(StateKey.DIFFICULTY),
    )
    return {StateKey.FINAL_OUTPUT: response.content}


@trace_node
def general_assistant_node(state: AgentState):
    prompt = PROMPTS[PromptKey.GENERAL_ASSISTANT].format(
        input_question=state[StateKey.INPUT_QUESTION]
    )
    response = invoke_model(PromptKey.GENERAL_ASSISTANT, prompt)
    return {StateKey.FINAL_OUTPUT: response.content}


@trace_node
def code_executor_node(state: AgentState):
    from features.solver.executor import extract_go_code

    code = extract_go_code(state[StateKey.FINAL_OUTPUT])
    if not code:
        return {StateKey.BUILD_RESULT: "Error: No Go code block found in the output."}

    task_name = state.get(StateKey.TASK_DIR, DEFAULT_TASK_NAME)
    return execute_go_code(code, task_name, DEFAULT_GO_CODE_DIR)


@trace_node_detailed
def code_fixer_node(state: AgentState):
    current_retry = state.get(StateKey.RETRY_COUNT, 0) + 1
    fix_prompt = PROMPTS[PromptKey.CODE_FIXER].format(
        final_output=state[StateKey.FINAL_OUTPUT],
        build_result=state[StateKey.BUILD_RESULT],
    )
    # Pass the post-increment retry count so online escalation kicks in after
    # `escalate_after_retries` builds; "Hard" problems escalate on first try.
    response = invoke_model(
        PromptKey.CODE_FIXER,
        fix_prompt,
        retry_count=current_retry,
        difficulty=state.get(StateKey.DIFFICULTY),
    )
    return {
        StateKey.FINAL_OUTPUT: response.content,
        StateKey.RETRY_COUNT: current_retry,
    }
