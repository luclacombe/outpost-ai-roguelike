"""Structured request logging.

`configure_logging` is called once at app startup (in main.py, before the
FastAPI instance is constructed). Dev gets a pretty console renderer; anything
else (staging, prod) gets JSON so Railway/Langfuse can parse the stream.

`log_requests` is registered as an HTTP middleware and records method, path,
status, and wall-clock duration per response. Exceptions propagate — we do
not want to swallow them here.
"""

import logging
import time

import structlog
from fastapi import Request, Response

from app.config import settings


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")

    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.ENVIRONMENT == "dev"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("outpost")


async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    return response
