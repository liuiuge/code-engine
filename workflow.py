from langgraph.graph import StateGraph, END
from logger import logger
from state import AgentState
from constants import StateKey, NodeName, Category, BUILD_SUCCESS_MESSAGE
from nodes import (
    intent_classifier_node, 
    task_summarizer_node, 
    code_generator_node, 
    general_assistant_node, 
    code_executor_node, 
    code_fixer_node
)

def route_by_category(state: AgentState):
    if state[StateKey.CATEGORY] == Category.CODING:
        return NodeName.TASK_SUMMARIZER
    return NodeName.GENERAL_ASSISTANT

def route_after_execute(state: AgentState):
    if BUILD_SUCCESS_MESSAGE in state.get(StateKey.BUILD_RESULT, ""):
        return END
    
    if state.get(StateKey.RETRY_COUNT, 0) < 3:
        logger.info(f"\n[system log] compile failed, retry {state.get(StateKey.RETRY_COUNT, 0) + 1} time ...")
        return NodeName.CODE_FIXER
    
    logger.info("\n[system log] reached maximum retry attempts, fix failed.")
    return END

def create_app():
    workflow = StateGraph(AgentState)

    # register nodes
    workflow.add_node(NodeName.INTENT_CLASSIFIER, intent_classifier_node)
    workflow.add_node(NodeName.TASK_SUMMARIZER, task_summarizer_node)
    workflow.add_node(NodeName.CODE_GENERATOR, code_generator_node)
    workflow.add_node(NodeName.GENERAL_ASSISTANT, general_assistant_node)
    workflow.add_node(NodeName.CODE_EXECUTOR, code_executor_node)
    workflow.add_node(NodeName.CODE_FIXER, code_fixer_node)

    # register edges and routes
    workflow.set_entry_point(NodeName.INTENT_CLASSIFIER)
    
    workflow.add_conditional_edges(
        NodeName.INTENT_CLASSIFIER,
        route_by_category,
        {
            NodeName.TASK_SUMMARIZER: NodeName.TASK_SUMMARIZER, 
            NodeName.GENERAL_ASSISTANT: NodeName.GENERAL_ASSISTANT
        }
    )
    
    workflow.add_edge(NodeName.TASK_SUMMARIZER, NodeName.CODE_GENERATOR)
    workflow.add_edge(NodeName.CODE_GENERATOR, NodeName.CODE_EXECUTOR)
    
    workflow.add_conditional_edges(
        NodeName.CODE_EXECUTOR,
        route_after_execute,
        {
            NodeName.CODE_FIXER: NodeName.CODE_FIXER, 
            END: END
        }
    )
    
    workflow.add_edge(NodeName.CODE_FIXER, NodeName.CODE_EXECUTOR)
    workflow.add_edge(NodeName.GENERAL_ASSISTANT, END)

    return workflow.compile()

app = create_app()