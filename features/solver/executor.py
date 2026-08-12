"""Go code executor: extract, write, fmt and build."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from infrastructure.constants import DEFAULT_TASK_NAME
from infrastructure.logger import logger


def extract_go_code(final_output: str) -> str | None:
    """Pull the first ```go / ```golang / ``` fenced code block from LLM output."""
    pattern = r"```(?:go|golang)?\n(.*?)```"
    match = re.search(pattern, final_output, re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def execute_go_code(code: str, task_name: str, output_dir: Path) -> dict:
    """
    Write the Go ``code`` to ``<output_dir>/<task_name>/<task_name>.go``, run
    ``go fmt`` and ``go build``, and return a result dict.

    Returns a dict with keys ``code_path`` and ``build_result`` (matching the
    solver AgentState's ``StateKey.CODE_PATH`` / ``StateKey.BUILD_RESULT``):
      - On success: ``build_result`` is the ``BUILD_SUCCESS_MESSAGE`` sentinel.
      - On compile error: ``build_result`` contains the captured stderr.
    """
    task_name = task_name or DEFAULT_TASK_NAME
    dynamic_output_dir = Path(output_dir) / task_name
    dynamic_output_dir.mkdir(parents=True, exist_ok=True)
    file_path = dynamic_output_dir / f"{task_name}.go"

    file_path.write_text(code, encoding="utf-8")

    subprocess.run(["go", "fmt", str(file_path)], capture_output=True)

    build_process = subprocess.run(
        ["go", "build", "-o", os.devnull, str(file_path)],
        capture_output=True,
        text=True,
    )

    if build_process.returncode != 0:
        return {
            "code_path": str(file_path),
            "build_result": f"compile error:\n{build_process.stderr}",
        }

    from infrastructure.constants import BUILD_SUCCESS_MESSAGE
    return {"code_path": str(file_path), "build_result": BUILD_SUCCESS_MESSAGE}
