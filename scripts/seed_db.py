#!/usr/bin/env python3
"""Run all migrations and pre-warm the extractions cache against the fixture."""

from __future__ import annotations

import asyncio

from app.db.session import write_extraction
from app.domain.extraction import build_patient_extract
from app.settings import get_settings
from app.simplepractice import FixtureBackend, SimplePracticeClient


async def main() -> None:
    settings = get_settings()
    backend = (
        FixtureBackend(settings.fixture_dir) if settings.use_fixtures else SimplePracticeClient(settings)
    )
    try:
        extract = await build_patient_extract(backend, settings.sp_client_hashed_id)
        await write_extraction(settings.sp_client_hashed_id, extract.model_dump(mode="json"))
        print(
            f"seeded extraction for hashed_id={settings.sp_client_hashed_id} "
            f"(timeline_entries={len(extract.timeline)}, source={extract.source})"
        )
    finally:
        await backend.aclose()


if __name__ == "__main__":
    asyncio.run(main())
