"""sFlow Analytics REST API."""

import logging
import time

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from . import auth, security
from .routes import admin, alerts, certificate, enrichment, groups, health, history, inventory, names, traffic, users, utilization

settings = get_settings()

logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","service":"api","level":"%(levelname)s","msg":%(message)r}')
log = logging.getLogger("api")

app = FastAPI(
    title=settings.app_name + " API",
    version=settings.app_version,
    description="Traffic analytics from sFlow. All traffic volumes are **estimates** "
                "(sampled packet size × sampling rate).",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
)

app.include_router(health.public_router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
for r in (health.router, inventory.router, traffic.router, utilization.router, admin.router, enrichment.router,
          names.router, groups.router, history.router, users.router, alerts.router, certificate.router):
    app.include_router(r, prefix="/api/v1", dependencies=[Depends(auth.require_ready_user)])


@app.on_event("startup")
def _startup() -> None:
    if settings.auth_enabled:
        security.ensure_admin()


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    if request.url.path != "/api/v1/health" or response.status_code != 200:
        log.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, ms)
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Never leak SQL or stack traces to clients; log them instead.
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "internal error"}, status_code=500)
