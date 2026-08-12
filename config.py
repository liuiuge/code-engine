import concurrent.futures
import os
from pathlib import Path
from langchain_ollama import ChatOllama
from logger import logger


class ModelTimeout(Exception):
    """Raised when a single model call exceeds its configured wall-clock budget."""

# 1. Bypass proxy interception for Python processes
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["ALL_PROXY"] = ""
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# 2. Define paths for prompts, output, and the model registry
BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"
MODEL_CONFIG_PATH = BASE_DIR / "models.yaml"

try:
    PROMPTS = {
        "intent_classifier": (PROMPT_DIR / "intent_classifier.md").read_text(encoding="utf-8"),
        "code_generator": (PROMPT_DIR / "code_generator.md").read_text(encoding="utf-8"),
        "general_assistant": (PROMPT_DIR / "general_assistant.md").read_text(encoding="utf-8"),
        "code_fixer": (PROMPT_DIR / "code_fixer.md").read_text(encoding="utf-8"),
        "task_summarizer": (PROMPT_DIR / "task_summarizer.md").read_text(encoding="utf-8"),
    }
except FileNotFoundError as e:
    raise RuntimeError(f"load prompts failed，please check the path: {e}")


# 3. Load the model registry from models.yaml and build one ChatOllama per model.
def _load_model_config(path: Path) -> dict:
    """Read models.yaml (requires PyYAML)."""
    if not path.exists():
        raise RuntimeError(f"model config not found: {path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PyYAML is required to read models.yaml. Install it with: pip install pyyaml"
        ) from exc
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not data.get("models"):
        raise RuntimeError(f"no [models] section found in {path}")
    return data


def _build_llm(spec: dict) -> ChatOllama:
    """Build a ChatOllama instance from a single model spec in models.yaml."""
    if not spec.get("model"):
        raise RuntimeError(f"model spec is missing the required 'model' field: {spec}")

    kwargs: dict = {
        "model": spec["model"],
        "base_url": spec.get("base_url", "http://127.0.0.1:11434"),
    }
    if spec.get("temperature") is not None:
        kwargs["temperature"] = float(spec["temperature"])
    if spec.get("top_p") is not None:
        kwargs["top_p"] = float(spec["top_p"])

    # Reasoning / thinking toggle. The underlying field name differs across
    # langchain-ollama versions: newer (>= 1.x) use `reasoning`, older use
    # `thinking`. Map the YAML's `thinking` key to whichever is available.
    thinking = spec.get("thinking")
    if thinking is not None:
        if "reasoning" in ChatOllama.model_fields:
            kwargs["reasoning"] = bool(thinking)
        else:  # pragma: no cover - depends on installed langchain-ollama
            kwargs["thinking"] = bool(thinking)

    # Any extra Ollama options (num_ctx, repeat_penalty, stop, ...) are passed
    # straight through as model_kwargs.
    extra = spec.get("extra_params") or {}
    if extra:
        kwargs["model_kwargs"] = dict(extra)

    return ChatOllama(**kwargs)


_MODEL_CONFIG = _load_model_config(MODEL_CONFIG_PATH)
MODELS = {name: _build_llm(spec) for name, spec in _MODEL_CONFIG["models"].items()}

DEFAULT_MODEL = _MODEL_CONFIG.get("default") or next(iter(MODELS))
if DEFAULT_MODEL not in MODELS:
    raise RuntimeError(
        f"default model '{DEFAULT_MODEL}' is not defined in models.yaml "
        f"(available: {list(MODELS)})"
    )

# Backward-compatible shared instance used by all nodes unless overridden.
llm = MODELS[DEFAULT_MODEL]


def get_llm(name: str | None = None) -> ChatOllama:
    """Return the ChatOllama instance for `name`, or the default model."""
    if name is None:
        return llm
    if name not in MODELS:
        raise KeyError(f"Unknown model '{name}'. Available: {list(MODELS)}")
    return MODELS[name]


def available_models() -> list[str]:
    """Names of all models defined in models.yaml."""
    return list(MODELS)


# 4. Per-role routing — balance local vs online usage across the workflow.
_ROUTING = _MODEL_CONFIG.get("routing", {}) or {}
_ROLE_MODELS = _ROUTING.get("roles", {}) or {}
_ESCALATE_AFTER = int(_ROUTING.get("escalate_after_retries", 1))
_ESCALATE_TO = _ROUTING.get("escalate_to", DEFAULT_MODEL)
_ESCALATE_ROLES = set(_ROUTING.get("escalate_roles", []) or [])
_HARD_ESCALATE_ROLES = set(_ROUTING.get("hard_escalate_roles", []) or [])

# Wall-clock timeout budget (seconds) per model call. A single call that runs
# longer than this is aborted and (for escalatable roles) retried on the online
# model instead of hanging the whole workflow.
_GLOBAL_TIMEOUT = int(_MODEL_CONFIG.get("timeout", 300))
_MODEL_TIMEOUTS = {
    name: int(spec.get("timeout", _GLOBAL_TIMEOUT))
    for name, spec in _MODEL_CONFIG["models"].items()
}
# Reverse map: ollama model name -> timeout, so we can look up the budget for
# whichever ChatOllama instance routing hands us.
_TIMEOUT_BY_OLLAMA_MODEL = {
    spec["model"]: _MODEL_TIMEOUTS[name]
    for name, spec in _MODEL_CONFIG["models"].items()
}

# Validate the routing references up front so a typo fails loudly at import.
if _ESCALATE_TO not in MODELS:
    raise RuntimeError(
        f"routing.escalate_to '{_ESCALATE_TO}' is not defined in models.yaml "
        f"(available: {list(MODELS)})"
    )
for _role, _model in _ROLE_MODELS.items():
    if _model not in MODELS:
        raise RuntimeError(
            f"routing.roles['{_role}'] -> '{_model}' is not defined in models.yaml "
            f"(available: {list(MODELS)})"
        )
for _role in _HARD_ESCALATE_ROLES:
    if _role not in _ROLE_MODELS:
        raise RuntimeError(
            f"routing.hard_escalate_roles contains '{_role}' which is not a known "
            f"role (known: {list(_ROLE_MODELS)})"
        )


def get_llm_for_role(role: str, retry_count: int = 0, difficulty: str | None = None) -> ChatOllama:
    """
    Pick the ``ChatOllama`` instance for a node role.

    - Most roles just use their configured model (e.g. local for the cheap,
      low-risk classifier / summarizer).
    - Code generation & fixing start local; once the local model has failed
      ``escalate_after_retries`` build attempts, they escalate to the online
      model for the remaining attempts.
    - Preemptive escalation: if the problem is LeetCode "Hard", the coder /
      fixer roles skip the local attempt and go straight to the online model
      (local VRAM / capability can't reliably crack Hard problems within budget).
    """
    base_model = _ROLE_MODELS.get(role, DEFAULT_MODEL)
    # Preemptive escalation for LeetCode Hard problems.
    if (role in _HARD_ESCALATE_ROLES
            and difficulty is not None
            and str(difficulty).strip().lower() == "hard"):
        return get_llm(_ESCALATE_TO)
    # Retry-based escalation: after N failed builds, switch to online.
    if role in _ESCALATE_ROLES and retry_count >= _ESCALATE_AFTER:
        return get_llm(_ESCALATE_TO)
    return get_llm(base_model)


def _invoke_with_timeout(model: ChatOllama, prompt, timeout: int, **kwargs):
    """
    Run ``model.invoke`` on a worker thread and enforce a hard wall-clock
    ``timeout`` (seconds).

    ChatOllama (langchain-ollama 1.x) exposes no timeout knob, and a streaming
    response keeps the httpx read timeout from firing — so we cap wall-clock time
    ourselves with a thread. On timeout we raise ``ModelTimeout`` and leave the
    still-running request to finish in the background (shutdown(wait=False) so
    we never block on the hung call).
    """
    if timeout and timeout > 0:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(model.invoke, prompt, **kwargs)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise ModelTimeout(
                    f"model '{model.model}' exceeded the {timeout}s call budget"
                ) from None
        finally:
            ex.shutdown(wait=False)
    return model.invoke(prompt, **kwargs)


def invoke_model(role: str, prompt, retry_count: int = 0,
                 difficulty: str | None = None, **kwargs):
    """
    Invoke the model selected for ``role`` (handles escalation automatically).

    - Routes by role, retry count, and problem difficulty (LeetCode Hard -> online).
    - Enforces the per-model wall-clock timeout. If the *local* model times out,
      escalatable roles are retried on the online model instead of failing.
    """
    model = get_llm_for_role(role, retry_count, difficulty)
    budget = _TIMEOUT_BY_OLLAMA_MODEL.get(model.model, _GLOBAL_TIMEOUT)
    logger.info(
        f"🔀 [Model Route] {role} -> {model.model} "
        f"(retry={retry_count}, difficulty={difficulty}, timeout={budget}s)"
    )
    try:
        return _invoke_with_timeout(model, prompt, budget, **kwargs)
    except ModelTimeout:
        # Local model blew the budget — fall back to the online model for
        # escalatable roles rather than failing the whole workflow.
        if role in _ESCALATE_ROLES:
            online = get_llm(_ESCALATE_TO)
            if online.model != model.model:
                online_budget = _TIMEOUT_BY_OLLAMA_MODEL.get(online.model, _GLOBAL_TIMEOUT)
                logger.warning(
                    f"⌛ [Model Timeout] {role} on '{model.model}' exceeded {budget}s — "
                    f"escalating to '{online.model}'"
                )
                return _invoke_with_timeout(online, prompt, online_budget, **kwargs)
        raise  # no escalation path (already online or non-escalatable) -> propagate
