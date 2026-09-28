from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .observability import configure_logging
from .routers import admin, ask, auth, dashboard, documents

configure_logging()  # inert par defaut (LOKI_URL non definie) - voir observability.py

app = FastAPI(title="IAFActory")
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(ask.router)


@app.get("/healthz")
def healthz():
    """Sonde de sante Docker : pas d'authentification, pas d'acces base."""
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def redirect_unauthenticated(request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return await http_exception_handler(request, exc)
