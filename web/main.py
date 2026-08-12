"""
web/main.py — uvicorn entry point for the code-engine web service.

Production start:
    uvicorn web.main:app --reload --port 8000

The ``app`` object is created in ``web.api``; this module only supplies the
``if __name__ == "__main__"`` block so the service can also be launched with
``python -m web.main``.
"""

from __future__ import annotations

import os

from web.api import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
