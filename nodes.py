import re
import subprocess
import os
from state import AgentState
from logger import trace_node, trace_node_detailed
from config import llm, PROMPTS, BASE_DIR
from constants import StateKey, Category, PromptKey, DEFAULT_TASK_NAME, BUILD_SUCCESS_MESSAGE

@trace_node
def intent_classifier_node(state: AgentState):
    prompt = PROMPTS[PromptKey.INTENT_CLASSIFIER].format(input_question=state[StateKey.INPUT_QUESTION])
    response = llm.invoke(prompt)
    category = Category.CODING if Category.CODING in response.content.lower() else Category.GENERAL
    return {StateKey.CATEGORY: category}

@trace_node_detailed
def task_summarizer_node(state: AgentState):
    prompt = PROMPTS[PromptKey.TASK_SUMMARIZER].format(input_question=state[StateKey.INPUT_QUESTION])
    response = llm.invoke(prompt)
    
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', response.content.strip()).lower()
    if not slug:
        slug = DEFAULT_TASK_NAME
        
    return {StateKey.TASK_DIR: slug}

@trace_node_detailed
def code_generator_node(state: AgentState):
    prompt = PROMPTS[PromptKey.CODE_GENERATOR].format(input_question=state[StateKey.INPUT_QUESTION])
    response = llm.invoke(prompt)
    return {StateKey.FINAL_OUTPUT: response.content}

@trace_node
def general_assistant_node(state: AgentState):
    prompt = PROMPTS[PromptKey.GENERAL_ASSISTANT].format(input_question=state[StateKey.INPUT_QUESTION])
    response = llm.invoke(prompt)
    return {StateKey.FINAL_OUTPUT: response.content}

@trace_node
def code_executor_node(state: AgentState):
    match = re.search(r"```(?:go|golang)?\n(.*?)```", state[StateKey.FINAL_OUTPUT], re.DOTALL)
    if not match:
        return {StateKey.BUILD_RESULT: "Error: No Go code block found in the output."}
    
    code = match.group(1).strip()
    
    task_name = state.get(StateKey.TASK_DIR, DEFAULT_TASK_NAME)
    dynamic_output_dir = BASE_DIR / "output" / "go-code" / task_name
    dynamic_output_dir.mkdir(parents=True, exist_ok=True)
    file_path = dynamic_output_dir / f"{task_name}.go"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    
    subprocess.run(["go", "fmt", str(file_path)], capture_output=True)
    
    build_cmd = ["go", "build", "-o", os.devnull, str(file_path)]
    build_process = subprocess.run(build_cmd, capture_output=True, text=True)
    
    if build_process.returncode != 0:
        return {StateKey.CODE_PATH: str(file_path), StateKey.BUILD_RESULT: f"compile error:\n{build_process.stderr}"}
    
    return {StateKey.CODE_PATH: str(file_path), StateKey.BUILD_RESULT: BUILD_SUCCESS_MESSAGE}

@trace_node_detailed
def code_fixer_node(state: AgentState):
    current_retry = state.get(StateKey.RETRY_COUNT, 0) + 1
    fix_prompt = PROMPTS[PromptKey.CODE_FIXER].format(
        final_output=state[StateKey.FINAL_OUTPUT],
        build_result=state[StateKey.BUILD_RESULT]
    )
    response = llm.invoke(fix_prompt)
    return {
        StateKey.FINAL_OUTPUT: response.content, 
        StateKey.RETRY_COUNT: current_retry
    }