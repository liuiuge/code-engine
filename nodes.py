import os
import re
import subprocess
from pathlib import Path

from state import AgentState
from logger import trace_node, trace_node_detailed
from config import invoke_model, PROMPTS, BASE_DIR
from constants import StateKey, Category, PromptKey, DEFAULT_TASK_NAME, BUILD_SUCCESS_MESSAGE


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
    pattern = r"```(?:go|golang)?\n(.*?)```"
    match = re.search(pattern, state[StateKey.FINAL_OUTPUT], re.DOTALL)
    if not match:
        return {StateKey.BUILD_RESULT: "Error: No Go code block found in the output."}

    code = match.group(1).strip()

    task_name = state.get(StateKey.TASK_DIR, DEFAULT_TASK_NAME)
    dynamic_output_dir = BASE_DIR / "output" / "go-code" / task_name
    dynamic_output_dir.mkdir(parents=True, exist_ok=True)
    file_path = dynamic_output_dir / f"{task_name}.go"

    file_path.write_text(code, encoding="utf-8")

    subprocess.run(["go", "fmt", str(file_path)], capture_output=True)

    build_process = subprocess.run(
        ["go", "build", "-o", os.devnull, str(file_path)],
        capture_output=True,
        text=True,
    )

    if build_process.returncode != 0:
        return {
            StateKey.CODE_PATH: str(file_path),
            StateKey.BUILD_RESULT: f"compile error:\n{build_process.stderr}",
        }

    return {StateKey.CODE_PATH: str(file_path), StateKey.BUILD_RESULT: BUILD_SUCCESS_MESSAGE}


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
