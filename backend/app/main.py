from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Connection, text

from app.auth import routes as auth_routes
from app.claims import routes as claim_routes
from app.config import REPO_ROOT, get_settings
from app.db import get_conn
from app.ingest import routes as upload_routes
from app.ingest.worker import Worker
from app.mapping import routes as mapping_routes
from app.overview import routes as overview_routes
from app.partners import routes as partner_routes
from app.recon import routes as recon_routes
from app.users import routes as user_routes

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Every API router; the access-control tests walk this list.
API_ROUTERS = (
    auth_routes.router,
    user_routes.router,
    upload_routes.router,
    mapping_routes.router,
    recon_routes.router,
    claim_routes.router,
    partner_routes.router,
    overview_routes.router,
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = Worker() if get_settings().run_worker else None
    if worker:
        worker.start()
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Settle",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response

    @app.get("/api/health", tags=["ops"])
    def health(conn: Connection = Depends(get_conn)) -> dict[str, str]:
        conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    for router in API_ROUTERS:
        app.include_router(router)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({"detail": "Not found."}, status_code=404)

    if FRONTEND_DIST.is_dir():
        _serve_spa(app, FRONTEND_DIST)
    return app


def _serve_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built React app; unknown paths get index.html so client routing works."""
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
