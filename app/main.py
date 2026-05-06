"""FastAPI app entrypoint."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import extraction as extraction_api
from app.api.schemas import HealthResponse
from app.settings import get_settings
from app.simplepractice import FixtureBackend, SimplePracticeBackend, SimplePracticeClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("perspectives.main")

    backend: SimplePracticeBackend
    if settings.use_fixtures:
        log.info("Using FixtureBackend (dir=%s)", settings.fixture_dir)
        backend = FixtureBackend(settings.fixture_dir)
    else:
        log.info("Using live SimplePracticeClient (%s)", settings.sp_base_url)
        backend = SimplePracticeClient(settings)

    app.state.backend = backend

    def _backend_dep() -> SimplePracticeBackend:
        return backend

    app.dependency_overrides[extraction_api.get_backend_dep] = _backend_dep

    try:
        yield
    finally:
        await backend.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PerspectivesOA",
        description=(
            "Reverse-engineered SimplePractice extraction service "
            "(Task 2). Task 3 intelligence endpoints to follow."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/api/v1/healthz", response_model=HealthResponse, tags=["meta"])
    async def healthz() -> HealthResponse:
        return HealthResponse(fixture_mode=settings.use_fixtures)

    app.include_router(extraction_api.router)

    return app


app = create_app()
