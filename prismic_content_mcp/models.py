"""Data models for Prismic read/write operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PrismicDocument(BaseModel):
    """Read model wrapper around Content API document data."""

    model_config = ConfigDict(extra="allow")

    id: str
    uid: str | None = None
    type: str
    lang: str
    data: dict[str, Any] = Field(default_factory=dict)
    alternate_languages: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("id", "type", "lang")
    @classmethod
    def _require_non_blank_identity_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Field value must be non-empty")
        return normalized


class DocumentWrite(BaseModel):
    """Write model for Migration API create/update operations."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    title: str
    type: str
    lang: str
    uid: str | None = None
    alternate_language_id: str | None = None
    data: dict[str, Any]

    @field_validator("title", "type", "lang")
    @classmethod
    def _require_non_blank_required_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Field value must be non-empty")
        return normalized

    @field_validator("id", "uid", "alternate_language_id", mode="before")
    @classmethod
    def _normalize_optional_identifiers(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value
