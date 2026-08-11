import functools
import time
import logging

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CodeEngineTrace")

def trace_node(func):
    """
    logging decorator to trace the execution of workflow nodes.
    """
    @functools.wraps(func)
    def wrapper(state, *args, **kwargs):
        node_name = func.__name__
        logger.info(f"🟢 [Node Start] ──> Entering [{node_name}]")
        
        start_time = time.time()
        try:
            # Execute the original node function
            result = func(state, *args, **kwargs)
            duration = time.time() - start_time
            
            logger.info(f"🔴 [Node End] ────> Exiting  [{node_name}] | Cost: {duration:.3f}s")
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"❌ [Node Error] ──> Failed   [{node_name}] after {duration:.3f}s | Error: {e}")
            raise e
            
    return wrapper

def trace_node_detailed(func):
    """
    deep logging decorator to trace the execution of workflow nodes with detailed state information.
    """
    @functools.wraps(func)
    def wrapper(state, *args, **kwargs):
        node_name = func.__name__
        logger.info(f"┌─────────────────────────────────────────────────────────")
        logger.info(f"│ 🟢 [Node Start] ──> Entering [{node_name}]")
        
        # Print the current key state (e.g., user input, task directory)
        if "input_question" in state:
            logger.info(f"│ 📥 [State Input]: {state.get('input_question')}")
        if "task_dir" in state:
            logger.info(f"│ 📂 [Task Dir]   : {state.get('task_dir')}")

        start_time = time.time()
        try:
            result = func(state, *args, **kwargs)
            duration = time.time() - start_time
            
            logger.info(f"│ 🔴 [Node End] ────> Exiting  [{node_name}] | Cost: {duration:.3f}s")
            
            # If the node output contains content from the large model, print it separately for easy viewing of its "thoughts"
            if isinstance(result, dict):
                for k, v in result.items():
                    logger.info(f"│ 📤 [State Output Key: {k}]")
                    # If the content is long (e.g., generated code or large model response), format and print a preview or the entire content
                    preview = str(v).replace('\n', '\\n')
                    if len(preview) > 300:
                        logger.info(f"│    Content Preview: {preview[:300]}... [Truncated, total len: {len(str(v))}]")
                    else:
                        logger.info(f"│    Content: {preview}")
                        
            logger.info(f"└─────────────────────────────────────────────────────────")
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"│ ❌ [Node Error] ──> Failed [{node_name}] after {duration:.3f}s | Error: {e}")
            logger.info(f"└─────────────────────────────────────────────────────────")
            raise e
            
    return wrapper
