"""Regression tests for end-to-end custom-question handling (P1-13 / CUSTOM_QUESTIONS).

Covers the classifier-driven routing and storage of free-text questions:
  CQ-01  custom CODING question -> code path -> Go file produced + stored custom
  CQ-02  custom GENERAL question -> Q&A, NO Go file, NO compile/verify
  CQ-04  custom record isolated (source:"custom", not in LeetCode index)
  CQ-05  headless (no_confirm) does not block (reuses CK-08 path)
  CQ-06  new custom question gets an isolated C-<seq> number

The LLM is stubbed; the Go executor still runs (go on PATH) but verification is
off so the suite is fast and offline.

Run:
    PYTHONPATH=. python features/solver/tests/test_custom_questions_regression.py
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

VALID_GO = '''package main

import "fmt"

func main() {
\tfmt.Println("LRU cache implemented")
}
'''

GENERAL_ANSWER = "TCP uses a three-way handshake: SYN, SYN-ACK, ACK."


class _Resp:
    def __init__(self, content: str):
        self.content = content


_CLASSIFY = {"value": "coding"}
_GENERATED = {"code": VALID_GO, "text": GENERAL_ANSWER}


def _fake_invoke(role, prompt, retry_count=0, difficulty=None, **kwargs):
    if role == "intent_classifier":
        return _Resp(_CLASSIFY["value"])
    if role == "task_summarizer":
        return _Resp("custom_task_x")
    if role in ("code_generator", "code_fixer"):
        return _Resp("```go\n" + _GENERATED["code"] + "\n```")
    if role == "general_assistant":
        return _Resp(_GENERATED["text"])
    if role == "problem_match":
        # Default precheck: no existing match -> create.
        return _Resp('{"exists": false}')
    return _Resp("")


class CustomQuestionE2ETest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._custom = tempfile.mkdtemp()
        self._p1 = mock.patch(
            "features.solver.precheck.invoke_model", side_effect=_fake_invoke
        )
        self._p2 = mock.patch(
            "features.solver.nodes.invoke_model", side_effect=_fake_invoke
        )
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)
        shutil.rmtree(self._custom, ignore_errors=True)

    def _run_custom(self, text):
        from features.solver.service import generate_for_query
        return generate_for_query(
            text,
            problems_dir=self._tmp,
            custom_dir=self._custom,
            no_confirm=True,  # headless (CQ-05)
            verify_mode="off",
        )

    # ---- CQ-01: coding -> Go code produced and stored -------------------------
    def test_cq01_coding_produces_go(self):
        _CLASSIFY["value"] = "coding"
        out = self._run_custom("用 Go 实现一个 LRU Cache")
        self.assertEqual(out["status"], "created")
        res = out["result"]
        self.assertEqual(res["category"], "coding")
        self.assertIsNotNone(res.get("code_path"))
        code_path = Path(res["code_path"])
        self.assertTrue(code_path.exists(), "Go file should have been written")
        self.assertTrue(code_path.name.endswith(".go"))
        # Custom record must reference the same task and be isolated.
        rec = out["record"]
        self.assertEqual(rec["source"], "custom")
        self.assertEqual(rec["category"], "coding")
        self.assertEqual(rec["task_dir"], "custom_task_x")

    # ---- CQ-02: general -> Q&A, no Go file -----------------------------------
    def test_cq02_general_no_go(self):
        _CLASSIFY["value"] = "general"
        out = self._run_custom("解释一下 TCP 三次握手")
        self.assertEqual(out["status"], "created")
        res = out["result"]
        self.assertEqual(res["category"], "general")
        # CQ-02: general path must NOT generate/compile Go.
        self.assertIsNone(res.get("code_path"))
        self.assertIn(GENERAL_ANSWER, res.get("final_output", ""))
        rec = out["record"]
        self.assertEqual(rec["source"], "custom")
        self.assertEqual(rec["category"], "general")
        self.assertIsNone(rec.get("code_path"))

    # ---- CQ-04: isolated storage (not in LeetCode index) ---------------------
    def test_cq04_isolated_storage(self):
        _CLASSIFY["value"] = "coding"
        out = self._run_custom("用 Go 写一个红黑树")
        rec = out["record"]
        self.assertEqual(rec["source"], "custom")
        self.assertTrue(rec["number"].startswith("C-"))
        # The LeetCode problems index must NOT contain the custom record.
        index_path = Path(self._tmp, "problems_index.json")
        if index_path.exists():
            import json
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            slugs = {p.get("slug") for p in idx.get("problems", [])}
            self.assertNotIn(rec["number"], slugs)
            self.assertNotIn(rec["task_dir"], slugs)

    # ---- CQ-06: monotonic, isolated numbering --------------------------------
    def test_cq06_numbering(self):
        from features.problems.custom_storage import list_custom_questions
        _CLASSIFY["value"] = "coding"
        a = self._run_custom("Go 实现堆排序")
        b = self._run_custom("Go 实现快速排序")
        self.assertEqual(a["number"], "C-0001")
        self.assertEqual(b["number"], "C-0002")
        numbers = [r["number"] for r in list_custom_questions(custom_dir=self._custom)]
        self.assertEqual(numbers, ["C-0001", "C-0002"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
