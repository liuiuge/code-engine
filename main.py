from typing import TypedDict
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from pathlib import Path
import subprocess
import re

import os

# Bypass proxy interception for Python processes
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["ALL_PROXY"] = ""
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

PROMPT_DIR = Path("prompts")
try:
    PROMPTS = {
        "classify": (PROMPT_DIR / "classify.md").read_text(encoding="utf-8"),
        "coding": (PROMPT_DIR / "coding.md").read_text(encoding="utf-8"),
        "general": (PROMPT_DIR / "general.md").read_text(encoding="utf-8"),
    }
except FileNotFoundError as e:
    raise RuntimeError(f"load prompts failed，please check the path: {e}")

# 1. Initialize the local Ollama model
llm = ChatOllama(
    model="reecdev/qwen3.5-lowvram:9b",
    base_url="http://127.0.0.1:11434",
    temperature=0.1,
    num_predict=4096,
    reasoning=False
)

# 2. Define the State
class AgentState(TypedDict):
    input_question: str
    category: str
    final_output: str
    code_path: str
    build_result: str
    retry_count: int

# 3. Define node functions (Nodes)

# Node A: Intent classifier
def classify_node(state: AgentState):
    classify_prompt = PROMPTS["classify"].format(input_question=state["input_question"])
    response = llm.invoke(classify_prompt)
    category = "coding" if "coding" in response.content.lower() else "general"
    return {"category": category}

# Node B: Handle coding questions
def coding_node(state: AgentState):
    coding_prompt = PROMPTS["coding"].format(input_question=state["input_question"])
    response = llm.invoke(coding_prompt)
    return {"final_output": response.content}

# Node C: Handle general questions
def general_node(state: AgentState):
    general_prompt = PROMPTS["general"].format(input_question=state["input_question"])
    response = llm.invoke(general_prompt)
    return {"final_output": response.content}

# 4. Define routing logic (Conditional Edge)
def route_by_category(state: AgentState):
    if state["category"] == "coding":
        return "coding_node"
    return "general_node"

def execute_node(state: AgentState):
    # 1. 正则提取纯净代码
    match = re.search(r"```(?:go|golang)?\n(.*?)```", state["final_output"], re.DOTALL)
    if not match:
        return {"build_result": "Error: 未在输出中找到标准 Go 代码块"}
    
    code = match.group(1).strip()
    file_path = "./output/go-code/260811/tree_traversal.go"
    
    # 2. 写入本地文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)
    
    # 3. 调用本地 go fmt 格式化代码
    subprocess.run(["go", "fmt", file_path], capture_output=True)
    
    # 4. 调用 go build 进行静态检查 (不生成可执行文件，仅检查)
    # 使用 -o os.DevNull 丢弃二进制文件，加速编译检查
    build_cmd = ["go", "build", "-o", os.devnull, file_path]
    build_process = subprocess.run(build_cmd, capture_output=True, text=True)
    
    if build_process.returncode != 0:
        return {"code_path": file_path, "build_result": f"编译失败:\n{build_process.stderr}"}
    
    return {"code_path": file_path, "build_result": "静态检查通过，编译成功"}

def fix_code_node(state: AgentState):
    # 初始化或累加重试次数
    current_retry = state.get("retry_count", 0) + 1
    
    fix_prompt = f"""
你之前生成的 Go 代码存在语法错误，编译失败。
请仔细阅读报错信息，修复代码，并输出完整的、可直接编译的 Go 代码。
**约束：只输出一个 Markdown 格式的 go 代码块，绝对不要输出任何其他解释性文字！**

【原始代码】：
{state["final_output"]}

【编译器报错】：
{state["build_result"]}
"""
    # 让大模型根据报错进行修复
    response = llm.invoke(fix_prompt)
    
    # 覆盖之前的输出，并更新重试次数
    return {
        "final_output": response.content, 
        "retry_count": current_retry
    }

def route_after_execute(state: AgentState):
    # 如果编译成功，直接结束
    if "编译成功" in state.get("build_result", ""):
        return END
    
    # 如果编译失败，且重试次数小于 3 次，进入修复节点
    if state.get("retry_count", 0) < 3:
        print(f"\n[系统日志] 检测到编译失败，正在进行第 {state.get('retry_count', 0) + 1} 次自动修复...")
        return "fix_code_node"
    
    # 如果超过 3 次依然失败，强制结束（及时止损）
    print("\n[系统日志] 达到最大重试次数，修复失败。")
    return END

# 5. Build the LangGraph workflow
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("classify_node", classify_node)
workflow.add_node("coding_node", coding_node)
workflow.add_node("general_node", general_node)
workflow.add_node("execute_node", execute_node)
workflow.add_node("fix_code_node", fix_code_node)

# Set entry point
workflow.set_entry_point("classify_node")

# Add conditional edges (route based on classification result)
workflow.add_conditional_edges(
    "classify_node",
    route_by_category,
    {
        "coding_node": "coding_node",
        "general_node": "general_node"
    }
)

# Set end points
workflow.add_edge("coding_node", "execute_node")
workflow.add_conditional_edges(
    "execute_node",
    route_after_execute,
    {
        "fix_code_node": "fix_code_node",
        END: END
    }
)
workflow.add_edge("fix_code_node", "execute_node")
workflow.add_edge("general_node", END)

# Compile the graph application
app = workflow.compile()

# 6. Run workflow test
if __name__ == "__main__":
    result = app.invoke({"input_question": "请用 Golang 写一段非递归二叉树中序遍历"})
    if result.get("category") == "coding":
        print(f"代码已保存至: {result.get('code_path')}")
        print(f"编译检查结果:\n{result.get('build_result')}")