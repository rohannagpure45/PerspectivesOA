"""Async DB session helpers.

The app stays correct if Postgres is offline: ``DATABASE_ENABLED=false`` in
``.env`` (the default) makes every helper a no-op. The helpers are also
defensive against runtime failures so DB outages never propagate as 5xx
responses to API callers.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AsamAudit, Extraction, TjcAudit
from app.settings import get_settings

log = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _factory() -> async_sessionmaker[AsyncSession] | None:
    global _engine, _session_factory
    settings = get_settings()
    if not settings.database_enabled:
        return None
    if _session_factory is None:
        _engine = create_async_engine(settings.database_url, future=True, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


async def read_latest_extraction(hashed_id: str) -> dict[str, Any] | None:
    factory = _factory()
    if factory is None:
        return None
    try:
        async with factory() as session:
            result = await session.execute(
                select(Extraction.payload)
                .where(Extraction.hashed_id == hashed_id)
                .order_by(Extraction.created_at.desc())
                .limit(1)
            )
            payload = result.scalar_one_or_none()
            return dict(payload) if payload is not None else None
    except Exception:  # pragma: no cover — best-effort
        log.exception("read_latest_extraction failed")
        return None


async def write_extraction(hashed_id: str, payload: dict[str, Any]) -> None:
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as session:
            session.add(
                Extraction(
                    hashed_id=hashed_id,
                    payload=payload,
                    source=str(payload.get("source", "fixture")),
                )
            )
            await session.commit()
    except Exception:  # pragma: no cover — best-effort
        log.exception("write_extraction failed")


async def write_asam_audit(hashed_id: str, payload: dict[str, Any]) -> None:
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as session:
            session.add(AsamAudit(hashed_id=hashed_id, payload=payload))
            await session.commit()
    except Exception:  # pragma: no cover — best-effort
        log.exception("write_asam_audit failed")


async def write_tjc_audit(hashed_id: str, payload: dict[str, Any]) -> None:
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as session:
            session.add(TjcAudit(hashed_id=hashed_id, payload=payload))
            await session.commit()
    except Exception:  # pragma: no cover — best-effort
        log.exception("write_tjc_audit failed")
