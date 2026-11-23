"""DepLens FastAPI application.

Serves the JSON API and, when a built frontend is present, the static SPA.
Binds to localhost by default — DepLens is a local-first tool.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from deplens import __version__
from deplens.api import apply_fix, decisions, impact, projects, reports, scans, settings

app = FastAPI(
    title="DepLens API",
    version=__version__,
    description="Local-first dependency health explorer. Know what your project depends on.",
)

# The frontend dev server runs on a different port; allow it during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(scans.router)
app.include_router(impact.router)
app.include_router(apply_fix.router)
app.include_router(decisions.router)
app.include_router(settings.router)
app.include_router(reports.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "product": "DepLens", "version": __version__}


# -- Static SPA (served only if a production build exists) --------------------
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _resolve_spa_file(dist: Path, full_path: str) -> Path:
    """Resolve an SPA request to a file to serve, guarding against traversal.

    Returns the requested file only when it exists **and** is contained within
    ``dist`` after resolution; otherwise falls back to ``index.html`` (SPA
    client-side routing). This prevents requests like ``..%2f..%2fetc/passwd``
    from escaping the build directory and reading arbitrary files.
    """
    dist = dist.resolve()
    candidate = (dist / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(dist):
        return candidate
    return dist / "index.html"


if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Never shadow the API.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(str(_resolve_spa_file(_FRONTEND_DIST, full_path)))
