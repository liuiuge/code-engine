"""Centralized project path definitions.

All internal modules should import paths from here instead of deriving
``Path(__file__).resolve().parent`` themselves, so the project remains
movable and refactor-friendly.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROMPT_DIR = PROJECT_ROOT / "prompts"
MODEL_CONFIG_PATH = PROJECT_ROOT / "infrastructure" / "models.yaml"

DEFAULT_PROBLEMS_DIR = PROJECT_ROOT / "output" / "problems"
DEFAULT_GO_CODE_DIR = PROJECT_ROOT / "output" / "go-code"
