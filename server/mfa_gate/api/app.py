from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ..config import settings
from ..db import sessionmaker
from ..services import events
from . import auth, device, manage

log = logging.getLogger("mfa_gate.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    events.hub.start()
    yield
    await events.hub.stop()


def create_app() -> FastAPI:
    cfg = settings()
    app = FastAPI(title="MFA Gate API", version=cfg.version, lifespan=lifespan,
                  docs_url="/api/docs" if cfg.docs_enabled else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if cfg.docs_enabled else None,
                  description="REST API MFA Gate. Интеграции: Basic-аутентификация `key_id:secret` API-клиента, права — scopes. "
                              "Панель управления использует те же методы с сессией администратора.")

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(detail, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"code": "validation", "message": "Проверьте поля формы", "fields": fields}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy",
                                        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health/live", tags=["system"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["system"])
    async def ready():
        try:
            async with sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)
        return {"status": "ok", "version": cfg.version, "events": events.hub.connected}

    app.include_router(auth.router)
    app.include_router(manage.router)
    app.include_router(device.router)

    static = Path(cfg.static_dir)
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str = ""):
            if path.startswith(("api/", "health/")):
                raise HTTPException(404, {"code": "not_found", "message": "Не найдено"})
            return FileResponse(static / "index.html")

    return app


app = create_app()
