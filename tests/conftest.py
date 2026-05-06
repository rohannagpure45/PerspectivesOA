"""Pytest configuration — wire every test to ``FixtureBackend``."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force fixture mode + disable DB before importing app (settings cache).
os.environ.setdefault("SP_FORCE_FIXTURES", "true")
os.environ.setdefault("DATABASE_ENABLED", "false")
os.environ.setdefault("PERSPECTIVES_FIXTURE_DIR", str(REPO_ROOT / "fixtures"))

from app.api import extraction as extraction_api  # noqa: E402
from app.main import create_app  # noqa: E402
from app.simplepractice import FixtureBackend, SimplePracticeBackend  # noqa: E402

HASHED_ID = "0c39dadff6972e0f"


@pytest.fixture
def fixture_dir() -> Path:
    return REPO_ROOT / "fixtures"


@pytest.fixture
def fixture_backend(fixture_dir: Path) -> FixtureBackend:
    return FixtureBackend(fixture_dir)


@pytest.fixture
async def client(fixture_backend: FixtureBackend) -> AsyncIterator[AsyncClient]:
    app = create_app()

    def _backend_dep() -> SimplePracticeBackend:
        return fixture_backend

    app.dependency_overrides[extraction_api.get_backend_dep] = _backend_dep
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
