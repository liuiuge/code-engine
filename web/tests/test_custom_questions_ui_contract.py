"""
UI contract tests for the "自定义题目" (custom questions) front-end tab.

Two layers:

1. Static assertions — parse ``frontend/index.html`` text and verify the
   required structural hooks are present and ``detail-back`` was left untouched.

2. Backend-contract assertions (TestClient + ``mock.patch``) — stub
   ``features.solver.service.{generate,confirm,precheck}_custom_question`` and
   ``features.problems.custom_storage.{list,load}_custom_question`` and assert the
   API shape the front-end depends on (bare-array list, 404 for invalid ``C-1``,
   ``text``-required create, raw-record detail).

3. Front-end runtime assertions — actually execute the REAL ``index.html``
   ``<script>`` in Node (see ``_ui_harness.js``, a self-contained DOM + fetch
   shim, no jsdom/playwright needed) and assert the UI behaves per the PM
   acceptance criteria CU-05 / CU-08..CU-18.

Run:
    PYTHONPATH=. python web/tests/test_custom_questions_ui_contract.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent          # web/tests
REPO = HERE.parent.parent                        # repo root
INDEX = REPO / "frontend" / "index.html"
HARNESS = HERE / "_ui_harness.js"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _find_node() -> str | None:
    candidates = [
        shutil.which("node"),
        "/c/Users/ltyal/.workbuddy/binaries/node/versions/22.22.2/node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def _run_harness() -> dict:
    node = _find_node()
    if not node:
        raise unittest.SkipTest("node executable not found; cannot run front-end UI harness")
    proc = subprocess.run(
        [node, str(HARNESS), str(INDEX)],
        capture_output=True, text=True, cwd=str(REPO), timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "node UI harness failed (rc=%s)\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (proc.returncode, proc.stdout, proc.stderr)
        )
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# 1. Static structure assertions
# --------------------------------------------------------------------------- #
class StaticStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_custom_tab_exists_after_gocode(self):
        self.assertIn('data-tab="custom"', self.html)
        self.assertIn('data-tab="gocode"', self.html)
        self.assertLess(
            self.html.index('data-tab="gocode"'),
            self.html.index('data-tab="custom"'),
            "custom tab must come after the gocode tab",
        )

    def test_showview_array_includes_custom(self):
        self.assertIn("['problems', 'gocode', 'custom', 'detail']", self.html)

    def test_view_custom_section_exists(self):
        self.assertIn('id="view-custom"', self.html)

    def test_trim_empty_guard_exists(self):
        self.assertIn("'c-text').value.trim()", self.html)
        self.assertIn("请输入题目内容后再提交", self.html)

    def test_detail_back_unchanged(self):
        # The generic detail-back handler must still be present and unmodified.
        self.assertIn("$('detail-back').addEventListener('click'", self.html)
        self.assertIn("document.querySelector('.tab.active').dataset.tab", self.html)
        self.assertIn("showView(active)", self.html)

    def test_no_problems_dir_in_ui(self):
        # problems_dir is a test override and must never surface to the UI.
        self.assertNotIn("problems_dir", self.html)


# --------------------------------------------------------------------------- #
# 2. Backend contract assertions (TestClient + mock.patch)
# --------------------------------------------------------------------------- #
class BackendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from web.api import app
        cls.client = TestClient(app)

    def test_list_returns_bare_array(self):
        with mock.patch("features.problems.custom_storage.list_custom_questions",
                        return_value=[]) as m:
            r = self.client.get("/api/custom-questions")
            self.assertEqual(r.status_code, 200)
            self.assertIsInstance(r.json(), list)  # NOT {items, total} envelope
        with mock.patch("features.problems.custom_storage.list_custom_questions",
                        return_value=[{"number": "C-0001"}, {"number": "C-0002"}]):
            r = self.client.get("/api/custom-questions")
            self.assertIsInstance(r.json(), list)
            self.assertEqual(len(r.json()), 2)

    def test_detail_404_for_invalid_number(self):
        # Mirror the real regex guard (^C-\d{4,}$) so C-1 is rejected, valid ids served.
        def fake_load(number, custom_dir=None):
            if not re.match(r"^C-\d{4,}$", number or ""):
                return None
            return {
                "number": number, "source": "custom", "created_at": "2026-01-01T00:00:00",
                "input_question": "q", "category": "coding", "task_dir": "lru",
                "code_path": "/x.go", "build_result": "b", "final_output": None,
                "verify_result": None, "verify_details": [], "precheck": {},
            }

        with mock.patch("features.problems.custom_storage.load_custom_question",
                        side_effect=fake_load):
            bad = self.client.get("/api/custom-questions/C-1")     # invalid: only 1 digit
            self.assertEqual(bad.status_code, 404)
            good = self.client.get("/api/custom-questions/C-1234")  # valid
            self.assertEqual(good.status_code, 200)
            self.assertEqual(good.json()["code_path"], "/x.go")

    def test_create_requires_text_field(self):
        captured = {}

        def fake_generate(text, problems_dir=None, custom_dir=None, no_confirm=False, verify_mode=None):
            captured["text"] = text
            captured["no_confirm"] = no_confirm
            return {
                "status": "needs_confirm", "number": None, "needs_confirm": True,
                "matched_slug": "x", "reason": "r", "input": text,
            }

        with mock.patch("features.solver.service.generate_custom_question",
                        side_effect=fake_generate):
            ok = self.client.post("/api/custom-questions", json={"text": "hello"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(captured["text"], "hello")
            self.assertFalse(captured["no_confirm"])
            # Missing `text` must be rejected by the request schema (422).
            missing = self.client.post("/api/custom-questions", json={"no_confirm": True})
            self.assertEqual(missing.status_code, 422)

    def test_confirm_and_precheck_endpoints_are_wired(self):
        with mock.patch("features.solver.service.confirm_custom_question",
                        return_value={"status": "reused", "number": None,
                                      "needs_confirm": False, "matched_slug": "two-sum"}):
            r = self.client.post("/api/custom-questions/confirm",
                                 json={"text": "t", "decision": "reuse", "matched_slug": "two-sum"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "reused")
        with mock.patch("features.solver.precheck.precheck_custom_question",
                        return_value={"status": "match", "matched_slug": "two-sum", "reason": "r"}):
            r = self.client.post("/api/custom-questions/precheck", json={"text": "t"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "match")
            self.assertNotIn("needs_confirm", r.json())  # precheck has no needs_confirm


# --------------------------------------------------------------------------- #
# 3. Front-end runtime assertions (Node DOM+fetch harness)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_find_node(), "node executable not available")
class FrontendRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.OBS = _run_harness()

    def test_harness_scenarios_ran_without_errors(self):
        for name, o in self.OBS.items():
            self.assertNotIn("__error", o, "scenario %s errored: %s" % (name, o.get("__error")))

    def test_cu05_request_body_fields(self):
        body = self.OBS["CU-05"]["body"]
        self.assertIn("text", body)
        self.assertNotIn("question", body)        # must be `text`, not `question`
        self.assertNotIn("problems_dir", body)    # test override must not leak
        self.assertEqual(body["text"], "用 Go 实现 LRU Cache")

    def test_cu08_needs_confirm_panel_branch(self):
        h = self.OBS["CU-08"]["confirmHTML"]
        for token in ("c-reuse", "c-new", "c-cancel", "two-sum", "发现相似题目"):
            self.assertIn(token, h)

    def test_cu09_reuse_decision(self):
        body = self.OBS["CU-09"]["body"]
        self.assertEqual(body["decision"], "reuse")
        self.assertEqual(body["matched_slug"], "two-sum")

    def test_cu10_not_related_decision(self):
        body = self.OBS["CU-10"]["body"]
        self.assertEqual(body["decision"], "not_related")
        self.assertNotIn("matched_slug", body)

    def test_cu11_no_confirm_hides_panel(self):
        h = self.OBS["CU-11"]["confirmHTML"]
        self.assertNotIn("c-reuse", h)

    def test_cu13_list_bare_array_no_envelope(self):
        o = self.OBS["CU-13"]
        self.assertIn("empty", o["emptyHTML"])   # empty state for []
        self.assertEqual(o["emptyCards"], 0)
        self.assertEqual(o["listCards"], 2)       # 2-element array renders 2 cards, no TypeError

    def test_cu14_empty_state(self):
        o = self.OBS["CU-14"]
        self.assertIn("empty", o["emptyHTML"])

    def test_cu15_coding_renders_build_result(self):
        h = self.OBS["CU-15"]["detailHTML"]
        self.assertIn("CODING_BUILD_MARKER", h)
        self.assertIn("build_result", h)          # compile block rendered
        self.assertIn("c-to-gocode", h)           # best-effort Go-code link present

    def test_cu16_noncoding_renders_final_no_build(self):
        h = self.OBS["CU-16"]["detailHTML"]
        self.assertIn("NONCODING_MARKER_最终输出", h)
        self.assertNotIn("CODING_BUILD_MARKER", h)
        self.assertNotIn("build_result", h)       # no empty compile block for non-coding

    def test_cu17_invalid_number_graceful_err(self):
        o = self.OBS["CU-17"]
        self.assertFalse(o["threw"], "invalid number must not throw uncaught")
        self.assertIn("err", o["detailHTML"])     # graceful .err, no white screen

    def test_cu18_xss_content_escaped(self):
        h = self.OBS["CU-18"]["detailHTML"]
        self.assertNotIn("<script", h)            # raw tag must not survive
        self.assertIn("&lt;script&gt;", h)        # esc() applied

    def test_cu19_gocode_link_clickable(self):
        # Regression for the `goTask is not defined` scope bug: clicking the
        # "查看生成的 Go 代码" button on a CODING detail must (a) not throw the
        # ReferenceError and (b) call openGoCode with the right task_name — i.e.
        # fetch must hit /api/go-code/custom_task_x.
        o = self.OBS["CU-19"]
        self.assertNotIn("__error", o, "scenario CU-19 errored: %s" % o.get("__error"))
        self.assertTrue(o["btnExists"], "guard: #c-to-gocode button+listener must exist after render")
        self.assertFalse(o["threw"], "clicking #c-to-gocode must NOT throw goTask is not defined: %s" % o.get("errMsg"))
        self.assertIsNotNone(o["calledGoCode"], "openGoCode must issue a fetch for the go-code API")
        self.assertIn("/api/go-code/custom_task_x", o["calledGoCode"],
                      "openGoCode must receive task_name=custom_task_x, got %s" % o.get("calledGoCode"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
