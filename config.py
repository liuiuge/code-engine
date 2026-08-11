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

# Ensure the output directory exists
OUTPUT_DIR = BASE_DIR / "output" / "go-code" / "260811"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    PROMPTS = {
        "intent_classifier": (PROMPT_DIR / "intent_classifier.md").read_text(encoding="utf-8"),
        "code_generator": (PROMPT_DIR / "code_generator.md").read_text(encoding="utf-8"),
        "general_assistant": (PROMPT_DIR / "general_assistant.md").read_text(encoding="utf-8"),
        "code_fixer": (PROMPT_DIR / "code_fixer.md").read_text(encoding="utf-8"),
    }
except FileNotFoundError as e:
    raise RuntimeError(f"load prompts failed，please check the path: {e}")

# 3. Initialize the LLM
llm = ChatOllama(
    model="reecdev/qwen3.5-lowvram:9b", 
    base_url="http://127.0.0.1:11434",
    temperature=0.1,
    num_predict=4096,
    reasoning=False
)