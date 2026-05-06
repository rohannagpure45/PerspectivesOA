"""Response DTOs for /api/v1/*.

The domain models in :mod:`app.domain.models` are already pydantic BaseModels
and are returned directly by FastAPI. This module exists for any
endpoint-specific shapes that don't belong on the domain (e.g. ``ExtractEnvelope``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "perspectives-oa"
    fixture_mode: bool


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_text_spans: bool = Field(
        default=True,
        description=(
            "When true, every dimension/finding includes the matched note id and "
            "the exact text span(s) the rule fired on."
        ),
    )
