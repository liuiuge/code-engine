import functools
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("CodeEngineTrace")


def _trace(detailed: bool = False):
    """Build a node-tracing decorator (timing + optional state dump)."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(state, *args, **kwargs):
            name = func.__name__
            logger.info(f"🟢 [Node Start] ──> Entering [{name}]")
            if detailed:
                if "input_question" in state:
                    logger.info(f"│ 📥 [State Input]: {state.get('input_question')}")
                if "task_dir" in state:
                    logger.info(f"│ 📂 [Task Dir]   : {state.get('task_dir')}")

            start = time.time()
            try:
                result = func(state, *args, **kwargs)
            except Exception as exc:
                cost = time.time() - start
                logger.error(
                    f"│ ❌ [Node Error] ──> Failed [{name}] "
                    f"after {cost:.3f}s | Error: {exc}"
                )
                raise

            cost = time.time() - start
            logger.info(f"🔴 [Node End] ────> Exiting  [{name}] | Cost: {cost:.3f}s")

            if detailed and isinstance(result, dict):
                for key, value in result.items():
                    logger.info(f"│ 📤 [State Output Key: {key}]")
                    preview = str(value).replace("\n", "\\n")
                    if len(preview) > 300:
                        logger.info(
                            f"│    Content Preview: {preview[:300]}... "
                            f"[Truncated, total len: {len(str(value))}]"
                        )
                    else:
                        logger.info(f"│    Content: {preview}")
            return result

        return wrapper

    return decorator


def trace_node(func):
    """Trace a workflow node's execution (timing only)."""
    return _trace()(func)


def trace_node_detailed(func):
    """Trace a workflow node with detailed state input/output dumps."""
    return _trace(detailed=True)(func)
