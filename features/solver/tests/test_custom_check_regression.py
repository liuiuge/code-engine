"""Regression tests for the custom-question dedup precheck + storage (P1-13 / CHECK_SPEC).

Covers CK-01..CK-09: Agent match / no-match / degrade, needs_confirm, confirm
reuse (no new record), independent storage, monotonic numbering, headless
``--no-confirm`` (CQ-05), and LeetCode-reference precheck skip (CK-09).

The LLM is stubbed (both the precheck call and the pipeline call) so the suite
runs offline and fast; the Go executor still runs (go must be on PATH) but we use
``verify_mode="off"`` to skip the verifier.

Run:
    PYTHONPATH=. python features/solver/tests/test_custom_check_regression.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROB = Path("output/problems")

# --- stubbed model responses --------------------------------------------------
MIN_PROBLEM = {
    "title": "Fake Problem",
    "titleSlug": "fake-problem",
    "questionFrontendId": "9999",
    "difficulty": "Easy",
    "topicTags": [],
    "isPaidOnly": False,
    "url": "https://leetcode.com/problems/fake-problem/",
    "content_html": "<p>fake</p>",
    "description_md": "fake",
    "exampleTestcaseList": [],
    "hints": [],
    "codeSnippets": [],
    "metaData": "",
    "go_template": "",
}

VALID_GO = '''package main

import "fmt"

func main() {
\tfmt.Println("hello from custom question")
}
'''


class _Resp:
    def __init__(self, content: str):
        self.content = content


# Per-test controllable LLM outputs.
_PRECHECK_JSON = {"value": '{"exists": false}'}
_CLASSIFY = {"value": "coding"}
_GENERATED = {"code": VALID_GO, "text": "a general answer"}


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
        return _Resp(_PRECHECK_JSON["value"])
    return _Resp("")


class PrecheckUnitTest(unittest.TestCase):
    """CK-01 / CK-02 / CK-03 — the precheck function in isolation."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patcher = mock.patch(
            "features.solver.precheck.invoke_model", side_effect=_fake_invoke
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_ck01_match(self):
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = '{"exists": true, "matched_slug": "two-sum", "reason": "same"}'
        res = precheck_custom_question("Implement two sum", problems_dir=self._tmp)
        self.assertEqual(res["status"], "match")
        self.assertEqual(res["matched_slug"], "two-sum")

    def test_ck02_no_match(self):
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = '{"exists": false}'
        res = precheck_custom_question("Explain TCP handshake", problems_dir=self._tmp)
        self.assertEqual(res["status"], "no_match")
        self.assertIsNone(res["matched_slug"])

    def test_ck03_garbage_degrades(self):
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = "Sure! Here is my thoughts: definitely related."
        res = precheck_custom_question("Anything", problems_dir=self._tmp)
        self.assertEqual(res["status"], "no_match")  # safe default, no raise

    def test_ck03_missing_field_degrades(self):
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = '{"foo": 1}'
        res = precheck_custom_question("Anything", problems_dir=self._tmp)
        self.assertEqual(res["status"], "no_match")

    def test_ck03_llm_raises_degrades(self):
        from features.solver.precheck import precheck_custom_question
        with mock.patch(
            "features.solver.precheck.invoke_model",
            side_effect=RuntimeError("model down"),
        ):
            res = precheck_custom_question("Anything", problems_dir=self._tmp)
        self.assertEqual(res["status"], "no_match")

    def test_ck10_negative_invert_vs_sametree_no_match(self):
        """CK-10: Invert Binary Tree (226) must NOT match Same Tree (100).

        The stubbed LLM returns exists:false with a reason pointing out the
        operation differs. The contract mapping must yield status=no_match and
        matched_slug=None. (The tightened prompt.md is what teaches the real
        LLM to emit this; here we lock the parse/map behavior.)
        """
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = json.dumps({
            "exists": False,
            "matched_slug": None,
            "reason": "invert reverses left/right children; same-tree compares two trees for equality — different operation, no_match",
        })
        res = precheck_custom_question("用 go 二叉树反转", problems_dir=self._tmp)
        self.assertEqual(res["status"], "no_match")
        self.assertIsNone(res["matched_slug"])

    def test_ck10a_positive_synonym_match(self):
        """CK-10a: '翻转二叉树' must match the existing 'invert-binary-tree' slug."""
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = json.dumps({
            "exists": True,
            "matched_slug": "invert-binary-tree",
            "reason": "same operation/input structure/goal; synonym wording (翻转二叉树 == invert binary tree)",
        })
        res = precheck_custom_question("翻转二叉树", problems_dir=self._tmp)
        self.assertEqual(res["status"], "match")
        self.assertEqual(res["matched_slug"], "invert-binary-tree")

    def test_ck10b_positive_crosslang_match(self):
        """CK-10b: '用 go 二叉树反转' (language-prefixed) must match invert-binary-tree."""
        from features.solver.precheck import precheck_custom_question
        _PRECHECK_JSON["value"] = json.dumps({
            "exists": True,
            "matched_slug": "invert-binary-tree",
            "reason": "language prefix '用 go' is only a language constraint; same operation as invert-binary-tree",
        })
        res = precheck_custom_question("用 go 二叉树反转", problems_dir=self._tmp)
        self.assertEqual(res["status"], "match")
        self.assertEqual(res["matched_slug"], "invert-binary-tree")


class GenerateDispatchTest(unittest.TestCase):
    """CK-04 / CK-05 / CK-08 / CK-09 — dispatcher, confirmation, headless, skip."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._custom = tempfile.mkdtemp()
        # Patch BOTH the precheck LLM call and the pipeline LLM call.
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

    def test_ck04_match_returns_confirm_without_solver(self):
        from features.solver.service import generate_custom_question
        _PRECHECK_JSON["value"] = '{"exists": true, "matched_slug": "two-sum"}'
        with mock.patch("features.solver.service.run_pipeline") as run_mock:
            res = generate_custom_question(
                "Implement two sum again",
                problems_dir=self._tmp,
                custom_dir=self._custom,
                no_confirm=False,
            )
        self.assertEqual(res["status"], "needs_confirm")
        self.assertTrue(res["needs_confirm"])
        self.assertEqual(res["matched_slug"], "two-sum")
        run_mock.assert_not_called()  # solver must NOT start (CK-04)

    def test_ck05_confirm_reuse_creates_no_record(self):
        from features.problems.custom_storage import list_custom_questions
        from features.solver.service import confirm_custom_question

        prob_dir = tempfile.mkdtemp()
        Path(prob_dir, "fake-problem.json").write_text(
            json.dumps(MIN_PROBLEM), encoding="utf-8"
        )
        res = confirm_custom_question(
            "Implement fake problem",
            "reuse",
            matched_slug="fake-problem",
            problems_dir=prob_dir,
            custom_dir=self._custom,
            verify_mode="off",
        )
        self.assertEqual(res["status"], "reused")
        self.assertIsNone(res["number"])
        # CK-05: confirm-reuse must NOT create a custom record.
        self.assertEqual(list_custom_questions(custom_dir=self._custom), [])

    def test_ck08_headless_no_confirm_creates(self):
        from features.problems.custom_storage import list_custom_questions
        from features.solver.service import generate_custom_question
        _PRECHECK_JSON["value"] = '{"exists": true, "matched_slug": "two-sum"}'
        res = generate_custom_question(
            "Implement two sum (headless)",
            problems_dir=self._tmp,
            custom_dir=self._custom,
            no_confirm=True,  # --no-confirm: skip confirmation, create directly
            verify_mode="off",
        )
        self.assertEqual(res["status"], "created")
        self.assertEqual(len(list_custom_questions(custom_dir=self._custom)), 1)

    def test_ck09_leetcode_reference_skips_precheck(self):
        from features.solver.service import generate_for_query
        prob_dir = tempfile.mkdtemp()
        Path(prob_dir, "fake-problem.json").write_text(
            json.dumps(MIN_PROBLEM), encoding="utf-8"
        )
        with mock.patch(
            "features.solver.service.precheck_custom_question"
        ) as pre_mock:
            res = generate_for_query(
                "https://leetcode.com/problems/fake-problem/",
                problems_dir=prob_dir,
                custom_dir=self._custom,
                verify_mode="off",
            )
        pre_mock.assert_not_called()  # CK-09: no dedup LLM call for LeetCode refs
        self.assertEqual(res["status"], "leetcode")


class StorageNumberingTest(unittest.TestCase):
    """CK-06 / CK-07 — independent storage + monotonic C-<seq> numbering."""

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

    def _create(self, text):
        from features.solver.service import generate_custom_question
        _PRECHECK_JSON["value"] = '{"exists": false}'
        return generate_custom_question(
            text,
            problems_dir=self._tmp,
            custom_dir=self._custom,
            no_confirm=True,
            verify_mode="off",
        )

    def test_ck06_independent_storage(self):
        from features.problems.custom_storage import (
            list_custom_questions,
            load_custom_question,
        )
        res = self._create("Write a binary search in Go")
        rec = load_custom_question(res["number"], custom_dir=self._custom)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["source"], "custom")  # CK-06: isolation marker
        self.assertNotIn("problems_index.json", rec)  # not mixed into LeetCode index
        # The LeetCode index (if present) must NOT reference the custom record.
        index_path = Path(self._tmp, "problems_index.json")
        if index_path.exists():
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            slugs = {p.get("slug") for p in idx.get("problems", [])}
            self.assertNotIn(rec["number"], slugs)
        self.assertEqual(len(list_custom_questions(custom_dir=self._custom)), 1)

    def test_ck07_monotonic_numbering(self):
        from features.problems.custom_storage import list_custom_questions
        r1 = self._create("First custom question")
        r2 = self._create("Second custom question")
        self.assertEqual(r1["number"], "C-0001")
        self.assertEqual(r2["number"], "C-0002")
        numbers = [r["number"] for r in list_custom_questions(custom_dir=self._custom)]
        self.assertEqual(numbers, ["C-0001", "C-0002"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
