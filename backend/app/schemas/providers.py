"""Admin provider-account schemas (Phase 12a).

``api_key`` is write-only: it is encrypted at rest and only its masked suffix
(``key_masked``) is ever returned.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.reference import ProviderRole


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    roles: list[str]
    priority: int
    key_masked: str | None = None
    requests_per_minute: int | None
    requests_per_day: int | None
    quota_state: dict[str, Any]
    is_enabled: bool


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    roles: list[ProviderRole] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    requests_per_minute: int | None = Field(default=None, ge=0)
    requests_per_day: int | None = Field(default=None, ge=0)
    is_enabled: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    roles: list[ProviderRole] | None = None
    priority: int | None = Field(default=None, ge=0)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    requests_per_minute: int | None = Field(default=None, ge=0)
    requests_per_day: int | None = Field(default=None, ge=0)
    is_enabled: bool | None = None
