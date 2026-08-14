"""Persistence layer for custom (non-LeetCode) questions.

Custom questions are stored independently from the LeetCode problem set
(see specs/custom-questions/CUSTOM_QUESTIONS.md, CQ-04 / storage decision (A)):
  - Physical location: ``output/custom-questions/<number>.json``
  - Each record carries ``source: "custom"`` and an isolated ``number`` (C-<seq>).
  - They are NEVER written into ``problems_index.json`` / the LeetCode index.

Numbering is a monotonic, collision-resistant counter (``custom_seq.json``) so
that ids stay unique and never regress even if a file is deleted (CK-07).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from infrastructure.paths import DEFAULT_CUSTOM_QUESTIONS_DIR

SEQ_FILENAME = "custom_seq.json"
SOURCE = "custom"
_NUMBER_RE = re_compiled = __import__("re").compile(r"^C-\d{4,}$")


def _seq_path(custom_dir: Path) -> Path:
    return custom_dir / SEQ_FILENAME


def _next_number(custom_dir: Path) -> str:
    """Allocate the next sequential ``C-<NNNN>`` id, guaranteeing uniqueness."""
    custom_dir = Path(custom_dir)
    custom_dir.mkdir(parents=True, exist_ok=True)
    seq_path = _seq_path(custom_dir)

    seq = 0
    if seq_path.exists():
        try:
            seq = int(json.loads(seq_path.read_text(encoding="utf-8")).get("seq", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            seq = 0

    # Bump until we land on a number with no existing record file.
    while True:
        seq += 1
        candidate = f"C-{seq:04d}"
        if not (custom_dir / f"{candidate}.json").exists():
            break

    seq_path.write_text(
        json.dumps({"seq": seq}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return candidate


def save_custom_question(
    record_fields: dict,
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
) -> dict:
    """Persist a custom question record and return the enriched record.

    ``record_fields`` is merged under the mandatory ``source`` / ``number`` /
    ``created_at`` envelope. The LeetCode index is intentionally NOT touched.
    """
    custom_dir = Path(custom_dir)
    custom_dir.mkdir(parents=True, exist_ok=True)

    number = _next_number(custom_dir)
    record = {
        "source": SOURCE,
        "number": number,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    record.update(record_fields or {})
    # Ensure the invariant even if a caller passed a contradictory value.
    record["source"] = SOURCE

    path = custom_dir / f"{number}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def list_custom_questions(
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
) -> list[dict]:
    """List all custom question records (does NOT touch the LeetCode index)."""
    custom_dir = Path(custom_dir)
    if not custom_dir.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(custom_dir.glob("C-*.json")):
        if f.name == SEQ_FILENAME:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("source") == SOURCE:
            out.append(rec)
    return out


def load_custom_question(
    number: str,
    custom_dir: str | Path = DEFAULT_CUSTOM_QUESTIONS_DIR,
) -> dict | None:
    """Load a single custom question record by its ``C-<seq>`` number."""
    if not _NUMBER_RE.match(number or ""):
        return None
    path = Path(custom_dir) / f"{number}.json"
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return rec if rec.get("source") == SOURCE else None
