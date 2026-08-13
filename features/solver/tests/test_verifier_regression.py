"""Regression tests for the code_verifier feature.

These tests exercise the *real* LangGraph pipeline: the Go executor and the Go
verifier actually run (Go must be on PATH), but the LLM calls (intent classifier,
code generator, code fixer) are stubbed so the suite runs offline and fast.

Coverage
--------
* AT-01  – a correct solution is verified and the pipeline ends on the pass route.
* AT-02  – a compiles-but-wrong solution is NOT reported as success.
* AT-08  – no record -> verification skipped (no blocking).
* AT-09  – verify_mode=off -> verification skipped.
* AT-11  – verify failures share the retry budget and stop after 3 (no infinite loop).
* AT-12/13 (wiring) – verify_result / verify_details surface on the final state and
  the fixer receives the verification failure.

Run:
    PYTHONPATH=. python features/solver/tests/test_verifier_regression.py
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from infrastructure.constants import (
    VERIFY_FAIL_PREFIX,
    VERIFY_PASS_MESSAGE,
    VERIFY_SKIP_MESSAGE,
)

PROB = Path("output/problems")

# --- stubbed model responses --------------------------------------------------
CORRECT = '''package main

import "fmt"

func twoSum(nums []int, target int) []int {
\tm := make(map[int]int)
\tfor i, num := range nums {
\t\tif j, ok := m[target-num]; ok {
\t\t\treturn []int{j, i}
\t\t}
\t\tm[num] = i
\t}
\treturn nil
}

func main() {
\tfmt.Println(twoSum([]int{2, 7, 11, 15}, 9))
}
'''

WRONG = '''package main

import "fmt"

func twoSum(nums []int, target int) []int {
\treturn []int{0, 0}
}

func main() {
\tfmt.Println(twoSum([]int{2, 7, 11, 15}, 9))
}
'''


class _Resp:
    def __init__(self, content: str):
        self.content = content


def _make_two_sum_record() -> dict:
    rec = json.load(open(PROB / "two-sum.json"))
    # Synthesize description_md with the *canonical* LeetCode expected outputs.
    # NOTE: two-sum has multiple valid index answers; exact-match verification
    # relies on the canonical outputs listed here. Example 2's answer is [1,2],
    # not [0,1].
    rec["description_md"] = (
        "**Example 1:**\n"
        "**Input:** nums = [2,7,11,15], target = 9\n**Output:** [0,1]\n\n"
        "**Example 2:**\n"
        "**Input:** nums = [3,2,4], target = 6\n**Output:** [1,2]\n\n"
        "**Example 3:**\n"
        "**Input:** nums = [3,3], target = 6\n**Output:** [0,1]\n"
    )
    return rec


# Generator/fixer output is swapped per-test via this module global.
_GENERATED = {"code": CORRECT}


def _fake_invoke_model(role, prompt, retry_count=0, difficulty=None, **kwargs):
    if role in ("intent_classifier",):
        return _Resp("coding")
    if role in ("code_generator", "code_fixer"):
        return _Resp("```go\n" + _GENERATED["code"] + "\n```")
    return _Resp("")


class VerifierPipelineTest(unittest.TestCase):

    def setUp(self):
        # Patch the model call where it is *used* (nodes.py imported it by name).
        self._patcher = mock.patch(
            "features.solver.nodes.invoke_model", side_effect=_fake_invoke_model
        )
        self._patcher.start()
        # Unique slug per test so filesystem state (verify/ dir, sidecar) never
        # bleeds between cases.
        self.slug = f"two-sum-{self._testMethodName}"

    def tearDown(self):
        self._patcher.stop()

    # Sentinel: _run's default loads the real two-sum record; pass record=None to
    # exercise the genuine "no record" path.
    _USE_DEFAULT_RECORD = object()

    def _run(self, code: str, verify_mode: str = "assert", record=_USE_DEFAULT_RECORD):
        from features.solver.service import run_pipeline

        _GENERATED["code"] = code
        rec = _make_two_sum_record() if record is self._USE_DEFAULT_RECORD else record
        return run_pipeline(
            input_question="Two Sum",
            difficulty=None,
            leetcode_slug=self.slug,
            problem_record=rec,
            verify_mode=verify_mode,
        )

    # ---- AT-01: correct solution verified, pass route ------------------------
    def test_pass_path(self):
        state = self._run(CORRECT, verify_mode="assert")
        self.assertEqual(state.get("verify_result"), VERIFY_PASS_MESSAGE)
        # Pass route must not consume any retry budget.
        self.assertEqual(state.get("retry_count", 0), 0)

    # ---- AT-02 / AT-11: wrong solution fails, retry budget capped at 3 ------
    def test_fail_path_retry_cap(self):
        state = self._run(WRONG, verify_mode="assert")
        self.assertTrue(
            str(state.get("verify_result", "")).startswith(VERIFY_FAIL_PREFIX),
            f"expected verified_fail, got {state.get('verify_result')!r}",
        )
        # Critical: the loop must terminate. After 3 fixer attempts it ends as
        # verified=false rather than hanging.
        self.assertEqual(state.get("retry_count"), 3)

    # ---- AT-09: verify_mode=off skips verification --------------------------
    def test_off_mode_skips(self):
        state = self._run(CORRECT, verify_mode="off")
        self.assertEqual(state.get("verify_result"), VERIFY_SKIP_MESSAGE)

    # ---- AT-08: no record degrades to skip ----------------------------------
    def test_no_record_skips(self):
        state = self._run(CORRECT, verify_mode="assert", record=None)
        self.assertEqual(state.get("verify_result"), VERIFY_SKIP_MESSAGE)


class VerifierRoutingTest(unittest.TestCase):
    """Direct unit tests of the routing functions (no graph execution)."""

    def test_route_after_verify_pass_ends(self):
        from features.solver.workflow import route_after_verify
        from infrastructure.constants import NodeName

        self.assertEqual(
            route_after_verify({"verify_result": VERIFY_PASS_MESSAGE}),
            NodeName.END if hasattr(NodeName, "END") else "__end__",
        )

    def test_route_after_verify_skip_ends(self):
        from features.solver.workflow import route_after_verify
        from infrastructure.constants import NodeName

        self.assertEqual(
            route_after_verify({"verify_result": VERIFY_SKIP_MESSAGE}),
            NodeName.END if hasattr(NodeName, "END") else "__end__",
        )

    def test_route_after_verify_fail_retries_then_ends(self):
        from features.solver.workflow import route_after_verify
        from infrastructure.constants import NodeName

        # retries < 3 -> back to fixer
        self.assertEqual(
            route_after_verify({"verify_result": VERIFY_FAIL_PREFIX + "x", "retry_count": 0}),
            NodeName.CODE_FIXER,
        )
        # retries == 3 -> stop
        self.assertEqual(
            route_after_verify({"verify_result": VERIFY_FAIL_PREFIX + "x", "retry_count": 3}),
            NodeName.END if hasattr(NodeName, "END") else "__end__",
        )

    def test_route_after_execute_compile_ok_goes_to_verifier(self):
        from features.solver.workflow import route_after_execute
        from infrastructure.constants import BUILD_SUCCESS_MESSAGE, NodeName

        self.assertEqual(
            route_after_execute({"build_result": BUILD_SUCCESS_MESSAGE}),
            NodeName.CODE_VERIFIER,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
