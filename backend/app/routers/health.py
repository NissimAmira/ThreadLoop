from typing import Literal

import httpx
import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db

router = APIRouter(tags=["health"])

DependencyStatus = Literal["ok", "degraded", "down"]


class HealthResponse(BaseModel):
    status: DependencyStatus
    version: str
    db: DependencyStatus
    redis: DependencyStatus
    meili: DependencyStatus


def _check_db(db: Session) -> DependencyStatus:
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "down"


def _check_redis(url: str) -> DependencyStatus:
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=1)
        client.ping()
        return "ok"
    except Exception:
        return "down"


def _check_meili(url: str) -> DependencyStatus:
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"{url.rstrip('/')}/health")
            return "ok" if r.status_code == 200 else "degraded"
    except Exception:
        return "down"


@router.get("/health", response_model=HealthResponse)
def health(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    db_s = _check_db(db)
    redis_s = _check_redis(settings.redis_url)
    meili_s = _check_meili(settings.meili_url)

    overall: DependencyStatus = (
        "ok"
        if all(s == "ok" for s in (db_s, redis_s, meili_s))
        else "down"
        if any(s == "down" for s in (db_s, redis_s, meili_s))
        else "degraded"
    )

    return HealthResponse(
        status=overall,
        version=settings.version,
        db=db_s,
        redis=redis_s,
        meili=meili_s,
    )
