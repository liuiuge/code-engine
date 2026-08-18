"""Regression tests for P1-9 model tuning (specs/model-tuning/MODEL_TUNING_SPEC.md).

Coverage (1:1 with the spec's Acceptance Criteria)
--------------------------------------------------
* PF-01 – speed/quality preference drives the FIRST-try model routing
  (speed -> local, quality -> minimax) and the speed path never costs more
  wall-clock time than the quality path. Determinism: the model *call* is
  stubbed with fixed delays (local fast / online slow), so the assertion never
  depends on real model speed.
* PF-02 – ``multi_answer=true`` problems accept a valid but reordered answer
  (two-sum ``[1,0]`` vs expected ``[0,1]``) -> ``pass``; without the flag the
  same output must still be ``verified_fail`` (order-sensitive guard).
* PF-FE (backend side) – ``code_generator_node`` records the first-try model in
  ``StateKey.USED_MODEL`` (speed -> local, quality -> minimax), which the web
  layer surfaces as ``GenerateResult.used_model``.

The Go toolchain really runs for PF-02; every LLM call is stubbed, so the suite
is offline and fast.

Run:
    PYTHONPATH=. python features/solver/tests/test_model_tuning_regression.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from infrastructure import config
from infrastructure.constants import (
    PREFERENCE_QUALITY,
    PREFERENCE_SPEED,
    StateKey,
    VERIFY_FAIL_PREFIX,
    VERIFY_PASS_MESSAGE,
)

PROB = Path("output/problems")

LOCAL_MODEL = "local"
ONLINE_MODEL = "minimax"

# --- stubbed model output ---------------------------------------------------- #
# Valid two-sum solution that returns the index pair in the *reverse* order of
# the canonical LeetCode answer ([1,0] instead of [0,1]). Correct, but a
# order-sensitive comparison rejects it -> the exact PF-02 false negative.
REVERSED_TWO_SUM = '''package main

import "fmt"

func twoSum(nums []int, target int) []int {
\tm := make(map[int]int)
\tfor i, num := range nums {
\t\tif j, ok := m[target-num]; ok {
\t\t\t// Deliberately reversed: a valid answer in a different order.
\t\t\treturn []int{i, j}
\t\t}
\t\tm[num] = i
\t}
\treturn nil
}

func main() {
\tfmt.Println(twoSum([]int{2, 7, 11, 15}, 9))
}
'''


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """Stand-in for a routed ChatOllama with a fixed, deterministic latency."""

    def __init__(self, registry_name: str, delay: float):
        self.registry_name = registry_name
        self.model = f"stub-{registry_name}"
        self.delay = delay

    def invoke(self, prompt, **kwargs):
        time.sleep(self.delay)
        return _Resp(f"answer from {self.registry_name}")


# local answers fast, online answers slowly — the whole point of "speed" mode.
_STUB_DELAYS = {LOCAL_MODEL: 0.02, ONLINE_MODEL: 0.30}


def _make_two_sum_record(multi_answer: bool | None = None) -> dict:
    """Real two-sum record + canonical expected outputs (optionally multi_answer)."""
    rec = json.load(open(PROB / "two-sum.json", encoding="utf-8"))
    rec["description_md"] = (
        "**Example 1:**\n"
        "**Input:** nums = [2,7,11,15], target = 9\n**Output:** [0,1]\n\n"
        "**Example 2:**\n"
        "**Input:** nums = [3,2,4], target = 6\n**Output:** [1,2]\n\n"
        "**Example 3:**\n"
        "**Input:** nums = [3,3], target = 6\n**Output:** [0,1]\n"
    )
    if multi_answer is not None:
        rec["multi_answer"] = multi_answer
    else:
        rec.pop("multi_answer", None)
    return rec


# --------------------------------------------------------------------------- #
# PF-01 — routing
# --------------------------------------------------------------------------- #
class PreferenceRoutingTest(unittest.TestCase):
    """get_llm_for_role must honour `preference` on the FIRST attempt."""

    def test_pf01_speed_first_try_is_local(self):
        for role in ("code_generator", "code_fixer"):
            with self.subTest(role=role):
                self.assertIs(
                    config.get_llm_for_role(role, preference=PREFERENCE_SPEED),
                    config.get_llm(LOCAL_MODEL),
                )

    def test_pf01_quality_first_try_is_online(self):
        for role in ("code_generator", "code_fixer"):
            with self.subTest(role=role):
                self.assertIs(
                    config.get_llm_for_role(role, preference=PREFERENCE_QUALITY),
                    config.get_llm(ONLINE_MODEL),
                )

    def test_pf01_speed_and_quality_differ(self):
        self.assertIsNot(
            config.get_llm_for_role("code_generator", preference=PREFERENCE_SPEED),
            config.get_llm_for_role("code_generator", preference=PREFERENCE_QUALITY),
        )

    def test_pf01_default_and_none_keep_local_baseline(self):
        # No preference argument at all, and an explicit None, must both keep the
        # documented local-first baseline (no behavior change for old callers).
        self.assertIs(config.get_llm_for_role("code_generator"), config.get_llm(LOCAL_MODEL))
        self.assertIs(
            config.get_llm_for_role("code_generator", preference=None),
            config.get_llm(LOCAL_MODEL),
        )

    def test_pf01_quality_does_not_leak_to_non_escalatable_roles(self):
        # Guard: only escalatable roles are affected; cheap roles stay local.
        for role in ("intent_classifier", "task_summarizer", "problem_match"):
            with self.subTest(role=role):
                self.assertIs(
                    config.get_llm_for_role(role, preference=PREFERENCE_QUALITY),
                    config.get_llm(LOCAL_MODEL),
                )

    def test_pf01_retry_escalation_unchanged_under_speed(self):
        # `preference` only changes the FIRST try: a retry still escalates.
        self.assertIs(
            config.get_llm_for_role("code_fixer", retry_count=1, preference=PREFERENCE_SPEED),
            config.get_llm(ONLINE_MODEL),
        )
        # Hard-difficulty preemptive escalation also stays intact.
        self.assertIs(
            config.get_llm_for_role(
                "code_generator", difficulty="Hard", preference=PREFERENCE_SPEED
            ),
            config.get_llm(ONLINE_MODEL),
        )

    def test_pf01_resolve_role_model_name_reports_registry_name(self):
        self.assertEqual(
            config.resolve_role_model_name("code_generator", preference=PREFERENCE_SPEED),
            LOCAL_MODEL,
        )
        self.assertEqual(
            config.resolve_role_model_name("code_generator", preference=PREFERENCE_QUALITY),
            ONLINE_MODEL,
        )


class PreferenceLatencyTest(unittest.TestCase):
    """PF-01 hard gate: speed path total time <= quality path total time."""

    def setUp(self):
        self.calls: list[dict] = []
        real_get = config.get_llm_for_role

        def recorder(role, retry_count=0, difficulty=None, preference=None):
            # Route for real, then swap in a stub with a deterministic delay so
            # the timing assertion never depends on live model speed.
            name = config.model_registry_name(
                real_get(role, retry_count, difficulty, preference=preference)
            )
            self.calls.append({
                "role": role, "retry_count": retry_count,
                "difficulty": difficulty, "preference": preference, "model": name,
            })
            return _FakeLLM(name, _STUB_DELAYS.get(name, 0.30))

        self._patcher = mock.patch.object(
            config, "get_llm_for_role", side_effect=recorder
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _timed_invoke(self, preference: str) -> float:
        started = time.perf_counter()
        config.invoke_model("code_generator", "solve two-sum", preference=preference)
        return time.perf_counter() - started

    def test_pf01_speed_is_not_slower_than_quality(self):
        speed_s = self._timed_invoke(PREFERENCE_SPEED)
        quality_s = self._timed_invoke(PREFERENCE_QUALITY)

        first_try_models = [c["model"] for c in self.calls]
        self.assertEqual(
            first_try_models, [LOCAL_MODEL, ONLINE_MODEL],
            f"first-try routing must be local then minimax, got {first_try_models}",
        )
        self.assertLessEqual(
            speed_s, quality_s,
            f"speed path ({speed_s:.3f}s) must not exceed quality path ({quality_s:.3f}s)",
        )

    def test_pf01_preference_reaches_the_router(self):
        config.invoke_model("code_generator", "p", preference=PREFERENCE_QUALITY)
        self.assertEqual(self.calls[-1]["preference"], PREFERENCE_QUALITY)
        self.assertEqual(self.calls[-1]["model"], ONLINE_MODEL)


# --------------------------------------------------------------------------- #
# PF-FE (backend side) — used_model
# --------------------------------------------------------------------------- #
class UsedModelTest(unittest.TestCase):
    """code_generator_node records the first-try model as `used_model`."""

    def setUp(self):
        self._patcher = mock.patch(
            "features.solver.nodes.invoke_model",
            side_effect=lambda role, prompt, **kw: _Resp("```go\n// code\n```"),
        )
        self.invoke = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _generate(self, state: dict) -> dict:
        from features.solver.nodes import code_generator_node
        return code_generator_node({StateKey.INPUT_QUESTION: "Two Sum", **state})

    def test_pffe_speed_used_model_is_local(self):
        out = self._generate({StateKey.PREFERENCE: PREFERENCE_SPEED})
        self.assertEqual(out[StateKey.USED_MODEL], LOCAL_MODEL)

    def test_pffe_quality_used_model_is_online(self):
        out = self._generate({StateKey.PREFERENCE: PREFERENCE_QUALITY})
        self.assertEqual(out[StateKey.USED_MODEL], ONLINE_MODEL)

    def test_pffe_missing_preference_defaults_to_speed(self):
        out = self._generate({})
        self.assertEqual(out[StateKey.USED_MODEL], LOCAL_MODEL)

    def test_pffe_preference_is_forwarded_to_invoke_model(self):
        self._generate({StateKey.PREFERENCE: PREFERENCE_QUALITY})
        self.assertEqual(self.invoke.call_args.kwargs.get("preference"), PREFERENCE_QUALITY)

    def test_pffe_fixer_forwards_preference(self):
        from features.solver.nodes import code_fixer_node

        code_fixer_node({
            StateKey.FINAL_OUTPUT: "code",
            StateKey.BUILD_RESULT: "boom",
            StateKey.PREFERENCE: PREFERENCE_QUALITY,
        })
        self.assertEqual(self.invoke.call_args.kwargs.get("preference"), PREFERENCE_QUALITY)


class UsedModelPipelineTest(unittest.TestCase):
    """The preference/used_model contract survives the real LangGraph pipeline."""

    GO = '''package main

import "fmt"

func main() {
\tfmt.Println("ok")
}
'''

    def setUp(self):
        def fake_invoke(role, prompt, retry_count=0, difficulty=None,
                        preference=None, **kwargs):
            if role == "intent_classifier":
                return _Resp("coding")
            if role == "task_summarizer":
                return _Resp("pf-fe-pipeline")
            if role in ("code_generator", "code_fixer"):
                return _Resp("```go\n" + self.GO + "\n```")
            return _Resp("")

        self._patcher = mock.patch(
            "features.solver.nodes.invoke_model", side_effect=fake_invoke
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _run(self, preference: str | None) -> dict:
        from features.solver.service import run_pipeline
        return run_pipeline(
            input_question="Two Sum",
            difficulty=None,
            leetcode_slug=f"pf-fe-{preference or 'default'}",
            problem_record=None,
            verify_mode="off",
            preference=preference,
        )

    def test_pffe_pipeline_quality_state_used_model_is_online(self):
        state = self._run(PREFERENCE_QUALITY)
        self.assertEqual(state.get(StateKey.PREFERENCE), PREFERENCE_QUALITY)
        self.assertEqual(state.get(StateKey.USED_MODEL), ONLINE_MODEL)

    def test_pffe_pipeline_default_state_used_model_is_local(self):
        state = self._run(None)
        self.assertEqual(state.get(StateKey.PREFERENCE), PREFERENCE_SPEED)
        self.assertEqual(state.get(StateKey.USED_MODEL), LOCAL_MODEL)


# --------------------------------------------------------------------------- #
# PF-02 — multi_answer verifier normalization
# --------------------------------------------------------------------------- #
class MultiAnswerVerifierTest(unittest.TestCase):
    """A valid but reordered answer passes only for multi_answer problems."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.code_path = Path(self._tmp) / "two_sum.go"
        self.code_path.write_text(REVERSED_TWO_SUM, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _verify(self, record: dict) -> dict:
        from features.solver.verifier import verify_go_code
        return verify_go_code(code_path=self.code_path, record=record, mode="assert")

    def test_pf02_multi_answer_reordered_answer_passes(self):
        out = self._verify(_make_two_sum_record(multi_answer=True))
        self.assertEqual(
            out["verify_result"], VERIFY_PASS_MESSAGE,
            f"reordered but valid two-sum answer must pass, got {out}",
        )

    def test_pf02_order_sensitive_guard_still_fails(self):
        # Guard: without multi_answer the comparison stays order-sensitive, so
        # the sorting generalization must NOT let a reordered answer through.
        out = self._verify(_make_two_sum_record(multi_answer=False))
        self.assertTrue(
            str(out["verify_result"]).startswith(VERIFY_FAIL_PREFIX),
            f"multi_answer=false must remain order-sensitive, got {out}",
        )

    def test_pf02_missing_flag_behaves_like_false(self):
        out = self._verify(_make_two_sum_record(multi_answer=None))
        self.assertTrue(
            str(out["verify_result"]).startswith(VERIFY_FAIL_PREFIX),
            f"missing multi_answer must remain order-sensitive, got {out}",
        )

    def test_pf02_emitted_helper_carries_normalize_parameter(self):
        # Contract of the emitted Go harness (MODEL_TUNING_SPEC §3.2).
        from features.solver.verifier import _emit_helpers, _emit_test, _parse_meta, parse_test_cases

        record = _make_two_sum_record(multi_answer=True)
        meta = _parse_meta(record)
        helpers, _ = _emit_helpers(meta)
        self.assertIn("func cevEqual(got, expected string, normalize bool) bool", helpers)
        self.assertIn("func cevSorted(xs []interface{}) string", helpers)

        cases = parse_test_cases(record)
        on = _emit_test(meta, cases, "assert", normalize=True)
        off = _emit_test(meta, cases, "assert", normalize=False)
        self.assertIn("cevEqual(gotJSON, c.expected, true)", on)
        self.assertIn("cevEqual(gotJSON, c.expected, false)", off)


if __name__ == "__main__":
    unittest.main(verbosity=2)
