"""Regression tests for the multi_answer producer (P1-9 / PF-03).

Spec: specs/model-tuning/MODEL_TUNING_SPEC.md §1 (multi_answer 生产者) + PF-03 AC.

These cover the three code-side production points that make ``record["multi_answer"]``
available without hand-editing a problem record, so PF-02's order-insensitive
verifier comparison can trigger in production:

  1. ``is_multi_answer_problem(slug, title)`` — allowlist + "two-sum" substring fallback.
  2. ``normalize_problem`` — canonical record constructor (shared by save/fetch/live)
     writes ``multi_answer`` from the slug.
  3. ``load_problem_file`` — backfills ``multi_answer`` for historical caches that
     predate the producer, and never overrides an explicitly-stored value.

No real LeetCode access: records are synthesized and the on-disk cache is simulated
with temporary files.
"""

import json
import tempfile
import unittest
from pathlib import Path

from infrastructure.constants import MULTI_ANSWER_SLUGS, is_multi_answer_problem
from features.problems.models import normalize_problem
from features.problems.storage import load_problem_file


class IsMultiAnswerProblemTest(unittest.TestCase):
    """Allowlist + same-family substring fallback (spec §1, PF-03 row 1)."""

    def test_allowlisted_slugs_are_multi_answer(self):
        for slug in MULTI_ANSWER_SLUGS:
            with self.subTest(slug=slug):
                self.assertTrue(is_multi_answer_problem(slug))

    def test_two_sum_variant_substring_is_multi_answer(self):
        # Same-family variants containing "two-sum" are covered by the fallback.
        # (LeetCode slug "two-sum-iv-input-is-a-bst" is a real example.)
        self.assertTrue(is_multi_answer_problem("two-sum-iv-input-is-a-bst"))
        self.assertTrue(is_multi_answer_problem("two-sum-less-than-k"))

    def test_non_whitelisted_slug_is_not_multi_answer(self):
        for slug in ("climbing-stairs", "valid-parentheses",
                     "best-time-to-buy-and-sell-stock", "merge-two-sorted-lists"):
            with self.subTest(slug=slug):
                self.assertFalse(is_multi_answer_problem(slug))

    def test_case_insensitive_and_whitespace_tolerant(self):
        self.assertTrue(is_multi_answer_problem("Two-Sum"))
        self.assertTrue(is_multi_answer_problem("  two-sum  "))
        self.assertFalse(is_multi_answer_problem("Climbing-Stairs"))

    def test_none_and_empty_slug_are_false(self):
        self.assertFalse(is_multi_answer_problem(None))
        self.assertFalse(is_multi_answer_problem(""))


class NormalizeProblemTest(unittest.TestCase):
    """normalize_problem writes multi_answer from the slug (spec §1 point 1, PF-03)."""

    def test_two_sum_record_has_multi_answer_true(self):
        rec = normalize_problem({"titleSlug": "two-sum", "title": "Two Sum"})
        self.assertTrue(rec["multi_answer"])

    def test_two_sum_ii_record_has_multi_answer_true(self):
        rec = normalize_problem(
            {"titleSlug": "two-sum-ii-input-array-is-sorted", "title": "Two Sum II"}
        )
        self.assertTrue(rec["multi_answer"])

    def test_two_sum_variant_record_has_multi_answer_true(self):
        rec = normalize_problem(
            {"titleSlug": "two-sum-iv-input-is-a-bst", "title": "Two Sum IV"}
        )
        self.assertTrue(rec["multi_answer"])

    def test_non_whitelisted_record_has_multi_answer_false(self):
        rec = normalize_problem(
            {"titleSlug": "climbing-stairs", "title": "Climbing Stairs"}
        )
        self.assertFalse(rec["multi_answer"])

    def test_other_record_fields_are_untouched(self):
        # Producer must add multi_answer without dropping existing canonical fields.
        rec = normalize_problem(
            {"titleSlug": "two-sum", "title": "Two Sum", "difficulty": "Easy"}
        )
        self.assertEqual(rec["titleSlug"], "two-sum")
        self.assertEqual(rec["title"], "Two Sum")
        self.assertEqual(rec["difficulty"], "Easy")
        self.assertIn("go_template", rec)
        self.assertIn("description_md", rec)


class LoadProblemFileTest(unittest.TestCase):
    """load_problem_file backfills / honours multi_answer (spec §1 point 2, PF-03)."""

    def _write_record(self, record) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(record, tmp, ensure_ascii=False)
        tmp.close()
        return Path(tmp.name)

    def _load_and_cleanup(self, record):
        path = self._write_record(record)
        try:
            return load_problem_file(path)
        finally:
            path.unlink()

    def test_missing_key_backfilled_true_for_two_sum(self):
        loaded = self._load_and_cleanup({"titleSlug": "two-sum", "title": "Two Sum"})
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["multi_answer"])

    def test_missing_key_backfilled_false_for_non_whitelist(self):
        loaded = self._load_and_cleanup(
            {"titleSlug": "climbing-stairs", "title": "Climbing Stairs"}
        )
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded["multi_answer"])

    def test_explicit_true_not_overridden_on_non_whitelist(self):
        # A non-whitelist slug with an explicit True must stay True (ops override).
        loaded = self._load_and_cleanup(
            {"titleSlug": "climbing-stairs", "title": "Climbing Stairs",
             "multi_answer": True}
        )
        self.assertTrue(loaded["multi_answer"])

    def test_explicit_false_not_overridden_on_whitelist(self):
        # A whitelist slug with an explicit False must stay False (ops override).
        loaded = self._load_and_cleanup(
            {"titleSlug": "two-sum", "title": "Two Sum", "multi_answer": False}
        )
        self.assertFalse(loaded["multi_answer"])

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_problem_file(Path(tempfile.gettempdir()) / "nope.json"))


if __name__ == "__main__":
    unittest.main()
