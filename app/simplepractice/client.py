"""HTTP client for SimplePractice's internal ``/frontend/*`` JSON:API.

Auth model (verified against the captured HAR):

* SimplePractice does not expose a stable public API; the chart UI talks to
  ``/frontend/*`` JSON:API endpoints behind the same session as the browser.
* Each request requires:
    - ``Cookie: _simple_practice_session=<value>`` from a logged-in browser.
    - ``X-CSRF-Token: <value>`` parsed from the ``<meta name="csrf-token">``
      tag of any HTML page in the same session (e.g.
      ``/clients/{hashed_id}/overview``).
    - ``Accept: application/vnd.api+json``
    - ``api-version: 2025-03-21``
    - A modern browser ``User-Agent`` and ``Referer`` header — without
      them the edge returns 403 even with valid auth.

This module only ever issues ``GET`` requests. Writes are out of scope.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.settings import Settings, get_settings
from app.simplepractice.jsonapi import Document

log = logging.getLogger(__name__)


@runtime_checkable
class SimplePracticeBackend(Protocol):
    """Common surface implemented by ``SimplePracticeClient`` and ``FixtureBackend``."""

    async def resolve_hashed_id(self, hashed_id: str) -> str: ...

    async def get_client(self, numeric_id: str) -> Document: ...

    async def get_overview_items(self, numeric_id: str, page_size: int = 20) -> Document: ...

    async def get_appointment(self, appointment_id: str) -> Document: ...

    async def aclose(self) -> None: ...


_DEFAULT_INCLUDE_CLIENT = (
    "clientBillingOverview,billingSettings,emails,phones,addresses,"
    "clientRelationships.relatedClient.emails,clientRelationships.relatedClient.phones,"
    "clientContacts,insuranceInfos,insuranceInfos.insurancePlan,"
    "upcomingAppointments"
)

_DEFAULT_INCLUDE_OVERVIEW = (
    "progressNote.notable,progressNote.notable.client,"
    "progressNote.noteSignatureOverview,"
    "psychotherapyNote,psychotherapyNote.noteSignatureOverview,"
    "notable,notable.diagnosisTreatmentPlanOverview,"
    "diagnosisTreatmentPlan.globalDsmCodes"
)

_DEFAULT_INCLUDE_APPOINTMENT = (
    "office,client.phones,client.emails,"
    "progressNote,progressNote.noteSignatureOverview,"
    "psychotherapyNote,psychotherapyNote.noteSignatureOverview,"
    "diagnosisTreatmentPlan,diagnosisTreatmentPlan.globalDsmCodes,"
    "diagnosisTreatmentPlan.note.notable,"
    "complexNote,complexNote.intakeQuestionnaire,"
    "treatmentProgress.diagnosisTreatmentPlan.globalDsmCodes,"
    "appointmentClients,appointmentMemo,overviewDocuments"
)


class SimplePracticeClient(SimplePracticeBackend):
    """Live JSON:API backend with cookie + CSRF auth."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._csrf_token: str | None = None
        self._client = client or httpx.AsyncClient(
            base_url=self.settings.sp_base_url,
            timeout=httpx.Timeout(20.0, read=30.0),
            follow_redirects=False,
            headers={
                "User-Agent": self.settings.sp_user_agent,
                "Accept": "application/vnd.api+json",
                "Accept-Language": "en-US,en;q=0.9",
                "api-version": self.settings.sp_api_version,
                "Referer": urljoin(
                    self.settings.sp_base_url,
                    f"/clients/{self.settings.sp_client_hashed_id}/overview",
                ),
            },
            cookies={"_simple_practice_session": self.settings.sp_session_cookie}
            if self.settings.sp_session_cookie
            else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SimplePracticeClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # CSRF discovery
    # ------------------------------------------------------------------
    async def _ensure_csrf(self, hashed_id: str | None = None) -> str:
        if self._csrf_token:
            return self._csrf_token
        page = f"/clients/{hashed_id or self.settings.sp_client_hashed_id}/overview"
        r = await self._client.get(
            page,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        token_raw = meta.get("content") if (meta is not None and hasattr(meta, "get")) else None
        token = token_raw if isinstance(token_raw, str) else None
        if not token:
            raise RuntimeError("Could not parse CSRF token from /clients/{hashed}/overview HTML")
        self._csrf_token = token
        log.debug("CSRF token discovered: %s...", token[:12])
        return token

    async def _api_get(self, path: str, params: dict[str, str] | None = None) -> Document:
        token = await self._ensure_csrf()
        for attempt in range(3):
            r = await self._client.get(
                path,
                params=params,
                headers={"X-CSRF-Token": token},
            )
            if r.status_code in (401, 419):
                log.warning("Stale CSRF on %s, refetching", path)
                self._csrf_token = None
                token = await self._ensure_csrf()
                continue
            if 500 <= r.status_code < 600 and attempt < 2:
                backoff = 0.5 * (2**attempt)
                log.warning("SP %s -> %s, retrying in %.1fs", path, r.status_code, backoff)
                await asyncio.sleep(backoff)
                continue
            r.raise_for_status()
            return Document.from_dict(r.json())
        raise RuntimeError(f"SP_API_GET exhausted retries: {path}")

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    async def resolve_hashed_id(self, hashed_id: str) -> str:
        doc = await self._api_get(
            f"/frontend/treatable-clients/{hashed_id}",
            params={"filter[findByHashedId]": "true"},
        )
        primary = doc.primary()
        if primary.type != "clients" or not primary.id:
            raise RuntimeError(f"Unexpected treatable-clients response for {hashed_id}")
        return primary.id

    async def get_client(self, numeric_id: str) -> Document:
        return await self._api_get(
            f"/frontend/clients/{numeric_id}",
            params={"include": _DEFAULT_INCLUDE_CLIENT},
        )

    async def get_overview_items(self, numeric_id: str, page_size: int = 20) -> Document:
        return await self._api_get(
            "/frontend/overview-items",
            params={
                "filter[clientId]": numeric_id,
                "include": _DEFAULT_INCLUDE_OVERVIEW,
                "page[size]": str(page_size),
            },
        )

    async def get_appointment(self, appointment_id: str) -> Document:
        return await self._api_get(
            f"/frontend/appointments/{appointment_id}",
            params={"include": _DEFAULT_INCLUDE_APPOINTMENT},
        )
