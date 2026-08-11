import os
from pathlib import Path
from langchain_ollama import ChatOllama

# 1. Bypass proxy interception for Python processes
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["ALL_PROXY"] = ""
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# 2. Define paths for prompts and output
BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"

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

model_local = "reecdev/qwen3.5-lowvram:9b"
model_minimax = " minimax-m3:cloud"

# 3. Initialize the LLM
llm = ChatOllama(
    model=model_minimax, 
    base_url="http://127.0.0.1:11434",
    temperature=0.1
)