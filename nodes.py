import re
import subprocess
import os
from state import AgentState
from config import llm, PROMPTS, BASE_DIR

BuildSuccessMessage = "static analysis passed, compilation successful"

def intent_classifier_node(state: AgentState):
    intent_classifier = PROMPTS["intent_classifier"].format(input_question=state["input_question"])
    response = llm.invoke(intent_classifier)
    category = "coding" if "coding" in response.content.lower() else "general"
    return {"category": category}

def code_generator_node(state: AgentState):
    code_generator = PROMPTS["code_generator"].format(input_question=state["input_question"])
    response = llm.invoke(code_generator)
    return {"final_output": response.content}

def general_assistant_node(state: AgentState):
    general_assistant = PROMPTS["general_assistant"].format(input_question=state["input_question"])
    response = llm.invoke(general_assistant)
    return {"final_output": response.content}

def code_executor_node(state: AgentState):
    match = re.search(r"```(?:go|golang)?\n(.*?)```", state["final_output"], re.DOTALL)
    if not match:
        return {"build_result": "Error: No Go code block found in the output."}
    
    code = match.group(1).strip()
    
    # Ensure the output directory exists
    task_name = state.get("task_dir", "default_task")
    dynamic_output_dir = BASE_DIR / "output" / "go-code" / task_name
    dynamic_output_dir.mkdir(parents=True, exist_ok=True)
    file_path = dynamic_output_dir / f"{task_name}.go"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    
    subprocess.run(["go", "fmt", str(file_path)], capture_output=True)
    
    build_cmd = ["go", "build", "-o", os.devnull, str(file_path)]
    build_process = subprocess.run(build_cmd, capture_output=True, text=True)
    
    if build_process.returncode != 0:
        return {"code_path": str(file_path), "build_result": f"compile error:\n{build_process.stderr}"}
    
    return {"code_path": str(file_path), "build_result": BuildSuccessMessage}

def code_fixer_node(state: AgentState):
    current_retry = state.get("retry_count", 0) + 1
    fix_prompt = PROMPTS["code_fixer"].format(
        final_output=state["final_output"],
        build_result=state["build_result"]
    )
    response = llm.invoke(fix_prompt)
    return {
        "final_output": response.content, 
        "retry_count": current_retry
    }

def task_summarizer_node(state: AgentState):
    prompt = PROMPTS["task_summarizer"].format(input_question=state["input_question"])
    response = llm.invoke(prompt)
    
    # Generate a slug from the response content
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', response.content.strip()).lower()
    if not slug:
        slug = "default_task"
        
    return {"task_dir": slug}
