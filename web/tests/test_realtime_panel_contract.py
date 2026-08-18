"""Front-end / API contract tests for the P1-5 realtime panel readouts.

This file is **shared** by two specs, one test class each, so they never collide:

* ``ObservabilityPanelFEContract`` (O-FE, specs/observability/OBSERVABILITY_SPEC.md §5)
  — the terminal panel must have a place to render the model source, and the
  ``GenerateResult`` schema must carry ``used_model``. (``escalated`` /
  ``per_node_ms`` belong to P1-8 and are intentionally NOT asserted here.)
* ``PreferenceSwitchFEContract`` (PF-FE, specs/model-tuning/MODEL_TUNING_SPEC.md §5)
  — the 速度优先/质量优先 switch must exist, must be sent as
  ``POST /api/problems/{id}/generate?preference=...``, and the terminal
  ``used_model`` must follow the switch (speed -> local, quality -> minimax).

The solver pipeline is stubbed (``mock.patch`` on
``features.solver.service.generate_for_problem``), so no LLM / Go run is needed.

Requires: fastapi + httpx (``fastapi.testclient``). Run:
    PYTHONPATH=. python web/tests/test_realtime_panel_contract.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent      # web/tests
REPO = HERE.parent.parent                    # repo root
INDEX = REPO / "frontend" / "index.html"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

USED_MODEL_ELEMENT_ID = "gen-used-model"
PREFERENCE_ELEMENT_ID = "gen-preference"


def _fake_result(used_model: str, sink: dict):
    """Build a stub `generate_for_problem` that records the preference it got."""

    def _fake(query, problems_dir=None, live=True, verify_mode=None, preference=None):
        sink["preference"] = preference
        sink["query"] = query
        return {
            "task_dir": "two-sum",
            "code_path": "",
            "build_result": "static analysis passed, compilation successful",
            "category": "coding",
            "verify_result": "verification passed",
            "verify_details": [],
            "used_model": used_model,
        }

    return _fake


# --------------------------------------------------------------------------- #
# O-FE — observability panel readout (model source)
# --------------------------------------------------------------------------- #
class ObservabilityPanelFEContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_ofe_panel_has_used_model_element(self):
        self.assertIn(f'id="{USED_MODEL_ELEMENT_ID}"', self.html)
        self.assertIn("模型来源", self.html)

    def test_ofe_generate_result_exposes_used_model(self):
        from web.schemas import GenerateResult

        self.assertIn("used_model", GenerateResult.model_fields)
        self.assertIsNone(GenerateResult(identifier="two-sum").used_model)

    def test_ofe_used_model_rendered_from_backend_field(self):
        # The panel text must be derived from the API field, not hardcoded.
        self.assertIn("renderUsedModel(r.used_model)", self.html)


# --------------------------------------------------------------------------- #
# PF-FE — speed/quality switch drives the first-try model
# --------------------------------------------------------------------------- #
class PreferenceSwitchFEContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from web.api import app

        cls.client = TestClient(app)
        cls.html = INDEX.read_text(encoding="utf-8")

    # ---- static UI contract -------------------------------------------------
    def test_pffe_switch_exists_with_both_options(self):
        self.assertIn(f'id="{PREFERENCE_ELEMENT_ID}"', self.html)
        self.assertIn("速度优先", self.html)
        self.assertIn("质量优先", self.html)
        self.assertIn('value="speed"', self.html)
        self.assertIn('value="quality"', self.html)

    def test_pffe_generate_request_carries_preference_query(self):
        self.assertIn("'/generate?preference=' + encodeURIComponent(pref)", self.html)

    # ---- backend contract ---------------------------------------------------
    def test_pffe_quality_returns_minimax_used_model(self):
        sink: dict = {}
        with mock.patch("features.solver.service.generate_for_problem",
                        side_effect=_fake_result("minimax", sink)):
            r = self.client.post("/api/problems/two-sum/generate?preference=quality")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sink["preference"], "quality")
        self.assertEqual(r.json()["used_model"], "minimax")
        self.assertIn("minimax", r.json()["used_model"])

    def test_pffe_speed_returns_local_used_model(self):
        sink: dict = {}
        with mock.patch("features.solver.service.generate_for_problem",
                        side_effect=_fake_result("local", sink)):
            r = self.client.post("/api/problems/two-sum/generate?preference=speed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sink["preference"], "speed")
        self.assertEqual(r.json()["used_model"], "local")

    def test_pffe_default_preference_is_speed(self):
        sink: dict = {}
        with mock.patch("features.solver.service.generate_for_problem",
                        side_effect=_fake_result("local", sink)):
            r = self.client.post("/api/problems/two-sum/generate")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sink["preference"], "speed")

    def test_pffe_invalid_preference_rejected(self):
        with mock.patch("features.solver.service.generate_for_problem",
                        side_effect=_fake_result("local", {})):
            r = self.client.post("/api/problems/two-sum/generate?preference=turbo")
        self.assertEqual(r.status_code, 422)

    def test_pffe_endpoint_documents_preference_param(self):
        schema = self.client.get("/openapi.json").json()
        params = schema["paths"]["/api/problems/{identifier}/generate"]["post"]["parameters"]
        names = [p["name"] for p in params]
        self.assertIn("preference", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
