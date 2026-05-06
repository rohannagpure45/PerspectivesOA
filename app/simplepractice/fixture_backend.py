"""Offline backend that reads ``fixtures/*.json`` produced from the captured HAR.

The backend implements the same Protocol as ``SimplePracticeClient`` so the
extraction layer is identical between live and offline runs. This is what the
test suite uses, and what production falls back to when ``SP_SESSION_COOKIE``
is not configured.

Fixture layout (see ``scripts/parse_har.py``):

    fixtures/
        treatable-client.json
        client.json
        overview-items.json
        appointments/3505428542.json
        appointments/3505428529.json
        appointments/3505428553.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.simplepractice.client import SimplePracticeBackend
from app.simplepractice.jsonapi import Document

log = logging.getLogger(__name__)


class FixtureBackend(SimplePracticeBackend):
    def __init__(self, fixture_dir: Path | str) -> None:
        self.fixture_dir = Path(fixture_dir)
        if not self.fixture_dir.exists():
            log.warning("Fixture dir %s does not exist", self.fixture_dir)

    async def aclose(self) -> None:
        return None

    def _load(self, relative: str) -> Document:
        path = self.fixture_dir / relative
        if not path.exists():
            raise FileNotFoundError(f"Missing fixture: {path}")
        with path.open("r", encoding="utf-8") as f:
            return Document.from_dict(json.load(f))

    async def resolve_hashed_id(self, hashed_id: str) -> str:
        doc = self._load("treatable-client.json")
        primary = doc.primary()
        fixture_hash = primary.attr("hashedId")
        if fixture_hash and str(fixture_hash) != hashed_id:
            raise FileNotFoundError(f"No fixture client for hashed_id={hashed_id}")
        if primary.id:
            return primary.id
        raise RuntimeError("Fixture treatable-client.json has no primary id")

    async def get_client(self, numeric_id: str) -> Document:
        doc = self._load("client.json")
        primary = doc.primary()
        if primary.id != str(numeric_id):
            raise FileNotFoundError(f"No fixture client for numeric_id={numeric_id}")
        return doc

    async def get_overview_items(self, numeric_id: str, page_size: int = 20) -> Document:
        return self._load("overview-items.json")

    async def get_appointment(self, appointment_id: str) -> Document:
        return self._load(f"appointments/{appointment_id}.json")
