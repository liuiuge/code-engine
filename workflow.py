from langgraph.graph import StateGraph, END
from state import AgentState
from nodes import (
    intent_classifier_node, 
    task_summarizer_node,
    code_generator_node, 
    general_assistant_node, 
    code_executor_node, 
    code_fixer_node, 
    BuildSuccessMessage
)
# --- route  ---
def route_by_category(state: AgentState):
    if state["category"] == "coding":
        return "task_summarizer_node"
    return "general_node"

def route_after_execute(state: AgentState):
    if BuildSuccessMessage in state.get("build_result", ""):
        return END
    
    if state.get("retry_count", 0) < 3:
        print(f"\n[system log] compile failed, retry {state.get('retry_count', 0) + 1} time ...")
        return "code_fixer_node"
    
    print("\n[system log] reached maximum retry attempts, fix failed.")
    return END

# --- build Graph ---
def create_app():
    workflow = StateGraph(AgentState)

    # register nodes
    workflow.add_node("intent_classifier_node", intent_classifier_node)
    workflow.add_node("task_summarizer_node", task_summarizer_node)
    workflow.add_node("code_generator_node", code_generator_node)
    workflow.add_node("general_assistant_node", general_assistant_node)
    workflow.add_node("code_executor_node", code_executor_node)
    workflow.add_node("code_fixer_node", code_fixer_node)

    # register edges and routes
    workflow.set_entry_point("intent_classifier_node")
    workflow.add_conditional_edges(
        "intent_classifier_node",
        route_by_category,
        {
            "task_summarizer_node": "task_summarizer_node", 
            "general_node": "general_assistant_node"
        }
    )
    workflow.add_edge("task_summarizer_node", "code_generator_node")
    workflow.add_edge("code_generator_node", "code_executor_node")
    workflow.add_conditional_edges(
        "code_executor_node",
        route_after_execute,
        {"code_fixer_node": "code_fixer_node", END: END}
    )
    workflow.add_edge("code_fixer_node", "code_executor_node")
    workflow.add_edge("general_assistant_node", END)

    return workflow.compile()

app = create_app()