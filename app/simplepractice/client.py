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
from app.simplepractice.jsonapi import Document, IncludedIndex, Resource

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
    "clientRelationships.relatedClient.addresses,clientContacts,insuranceInfos,"
    "insuranceInfos.insurancePlan,insuranceInfos.primaryPlan,currentInsuranceAuthorization,"
    "upcomingAppointments"
)

_DEFAULT_INCLUDE_OVERVIEW = (
    "progressNote.notable,progressNote.notable.client,"
    "progressNote.noteSignatureOverview,"
    "psychotherapyNote,psychotherapyNote.noteSignatureOverview,"
    "notable,notable.diagnosisTreatmentPlanOverview,"
    "diagnosisTreatmentPlan.globalDsmCodes,overviewDocuments,"
    "appointmentClientForOverview.progressNote,goodFaithEstimateOverview,"
    "globalMonarchChannel,appointmentMemo,client.insuranceCardDocuments"
)

_DEFAULT_INCLUDE_APPOINTMENT = (
    "office,client.phones,client.emails,coupleClient,"
    "overviewDocuments,overviewDocuments.client,"
    "diagnosisTreatmentPlan,diagnosisTreatmentPlan.globalDsmCodes,"
    "diagnosisTreatmentPlan.note.notable,"
    "progressNote,progressNote.noteSignatureOverview,"
    "progressNote.clientNoteRequests,progressNote.comments.user,"
    "psychotherapyNote,psychotherapyNote.noteSignatureOverview,"
    "complexNote,complexNote.intakeQuestionnaire,nextAppointment,previousAppointment,"
    "wileyTreatmentPlans,"
    "treatmentProgress.diagnosisTreatmentPlan.globalDsmCodes,"
    "treatmentProgress.treatmentProgressItems,"
    "appointmentClients,appointmentClients.client.clientRelationships.relatedClient,"
    "appointmentClients.progressNote.noteSignatureOverview,"
    "appointmentClients.complexNote.intakeQuestionnaire,"
    "appointmentClients.client.phones,appointmentClients.client.emails,"
    "appointmentClients.client.insuranceInfos,appointmentClients.appointmentCheck,"
    "appointmentMemo,globalMonarchChannel"
)

_DEFAULT_FIELDS_OVERVIEW: dict[str, str] = {
    "fields[appointments]": (
        "cursorId,startTime,endTime,fullDay,attendanceStatus,cptCodes,clinician,"
        "progressNote,psychotherapyNote,overviewDocuments,permissions,rankNum,"
        "appointmentMemo,globalMonarchChannel"
    ),
    "fields[groupAppointments]": (
        "progressNote,hasGroupNote,hasNoteSignature,appointmentClientForOverview,"
        "cursorId,startTime,endTime,fullDay,cptCodes,clinician,permissions,rankNum,"
        "thisType,overviewDocuments,appointmentMemo,globalMonarchChannel"
    ),
    "fields[intakeNotes]": "id,completedBy,isMeasure",
    "fields[diagnosisTreatmentPlanCustoms]": (
        "client,recipient,note,previousTreatmentPlan,closestDiagnosisTreatmentPlan,"
        "intakeQuestionnaire,diagnosisTreatmentPlanOverview,draftAiTreatmentPlan,"
        "permissions,thisType,frequency,notedAt,hasDetails,problem,goal,"
        "formattedGoal,objective,formattedObjective,reminderOption,reminderAfterPeriod,"
        "reminderOnDate,structure,source"
    ),
    "fields[clients]": "id,insuranceCardDocuments",
    "fields[assessments]": "id,permissions",
    "fields[mentalStatusExams]": "id",
    "fields[documents]": "name,documentMimeType",
    "fields[goodFaithEstimates]": (
        "id,dateProvided,expirationDate,permissions,submissionData,user,goodFaithEstimateOverview,cursorId"
    ),
    "fields[goodFaithEstimateOverviews]": "isLocked,isEditable",
    "fields[globalMonarchChannels]": "name",
    "fields[inquiriesNotes]": "id,cursorId,client,content,createdByUser,updatedByUser,createdAt,updatedAt",
}


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

    @staticmethod
    def _merge_documents(pages: list[Document]) -> Document:
        if not pages:
            return Document(data=[], included=IncludedIndex())
        merged_data: list[Resource] = []
        seen_primary: set[tuple[str, str]] = set()
        included = IncludedIndex()
        for page in pages:
            for resource in page.primary_list():
                key = (resource.type, resource.id)
                if key not in seen_primary:
                    seen_primary.add(key)
                    merged_data.append(resource)
            included.extend(page.included)
        last = pages[-1]
        return Document(data=merged_data, included=included, meta=last.meta, links=last.links)

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
        base_params = {
            "filter[clientId]": numeric_id,
            "include": _DEFAULT_INCLUDE_OVERVIEW,
            "page[size]": str(page_size),
            **_DEFAULT_FIELDS_OVERVIEW,
        }
        pages: list[Document] = []
        seen_page_signatures: set[tuple[tuple[str, str], ...]] = set()
        page_number = 1
        next_url: str | None = None

        while True:
            if next_url:
                page = await self._api_get(next_url)
            else:
                params = {**base_params, "page[number]": str(page_number)}
                page = await self._api_get("/frontend/overview-items", params=params)

            primary = page.primary_list()
            signature = tuple((resource.type, resource.id) for resource in primary)
            if signature in seen_page_signatures:
                break
            seen_page_signatures.add(signature)
            pages.append(page)

            next_link = page.links.get("next")
            next_url = next_link if isinstance(next_link, str) and next_link else None
            if next_url:
                continue
            if len(primary) < page_size:
                break
            page_number += 1

        return self._merge_documents(pages)

    async def get_appointment(self, appointment_id: str) -> Document:
        return await self._api_get(
            f"/frontend/appointments/{appointment_id}",
            params={"include": _DEFAULT_INCLUDE_APPOINTMENT},
        )
