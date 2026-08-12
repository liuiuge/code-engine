"""
web/api.py — FastAPI app factory for the code-engine web layer.

This module only constructs the ``FastAPI`` instance, registers middleware,
mounts the static UI, and includes the route modules under ``web/routes/``.
All business logic lives in the route modules; the heavy solver pipeline is
imported lazily by ``web/routes/problems.py`` so API startup never depends on
langgraph / langchain-ollama.

Run (production entry point lives in web/main.py):
    uvicorn web.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from web.dependencies import FRONTEND_DIR
from web.routes import go_code, meta, problems


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodeEngine API",
        version="1.0.0",
        description="Read-only API for generated LeetCode problems and Go code.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register route modules.
    app.include_router(meta.router)
    app.include_router(problems.router)
    app.include_router(go_code.router)

    @app.get("/", include_in_schema=False)
    def root():
        """Landing page: redirect to the static UI when present, else to /docs."""
        if FRONTEND_DIR.is_dir():
            return RedirectResponse(url="/ui/")
        return RedirectResponse(url="/docs")

    # Static UI mounted last so /api/* routes take precedence.
    if FRONTEND_DIR.is_dir():
        app.mount(
            "/ui",
            StaticFiles(directory=str(FRONTEND_DIR), html=True),
            name="frontend",
        )

    return app


app = create_app()
