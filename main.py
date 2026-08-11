from typing import TypedDict
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from pathlib import Path

import os

# 屏蔽 Python 进程的代理拦截
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

# 1. 初始化 Ollama 本地模型
llm = ChatOllama(
    model="reecdev/qwen3.5-lowvram:9b",
    base_url="http://127.0.0.1:11434",
    temperature=0.1,
    num_predict=4096,
    reasoning=False
)

# 2. 定义状态 (State)
class AgentState(TypedDict):
    input_question: str
    category: str      # 分类结果：coding / general
    final_output: str
    

# 3. 定义节点函数 (Nodes)

# 节点 A：意图分类器
def classify_node(state: AgentState):
    classify_prompt = PROMPTS["classify"].format(input_question=state["input_question"])
    response = llm.invoke(classify_prompt)
    category = "coding" if "coding" in response.content.lower() else "general"
    return {"category": category}

# 节点 B：处理编程问题
def coding_node(state: AgentState):
    coding_prompt = PROMPTS["coding"].format(input_question=state["input_question"])
    response = llm.invoke(coding_prompt)
    return {"final_output": response.content}

# 节点 C：处理通用问题
def general_node(state: AgentState):
    general_prompt = PROMPTS["general"].format(input_question=state["input_question"])
    response = llm.invoke(general_prompt)
    return {"final_output": response.content}

# 4. 定义路由逻辑 (Conditional Edge)
def route_by_category(state: AgentState):
    if state["category"] == "coding":
        return "coding_node"
    return "general_node"

# 5. 构建 LangGraph 图结构
workflow = StateGraph(AgentState)

# 添加节点
workflow.add_node("classify_node", classify_node)
workflow.add_node("coding_node", coding_node)
workflow.add_node("general_node", general_node)

# 设置入口
workflow.set_entry_point("classify_node")

# 添加条件边（根据分类结果分流）
workflow.add_conditional_edges(
    "classify_node",
    route_by_category,
    {
        "coding_node": "coding_node",
        "general_node": "general_node"
    }
)

# 设置终点
workflow.add_edge("coding_node", END)
workflow.add_edge("general_node", END)

# 编译图应用
app = workflow.compile()

# 6. 运行工作流测试
if __name__ == "__main__":
    result = app.invoke({"input_question": "请用 Golang 写一段非递归二叉树遍历"})
    print("--- 最终输出结果 ---")
    print(result["final_output"])