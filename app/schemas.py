"""Validerte inndata til API-et. Ingen lagring eller videobehandling her."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .media import MAX_EXPORT_SECONDS


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Prosjektnamnet må innehalde lesbar tekst.")
        return value


class EditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(default="", max_length=4000)
    duration_seconds: int = Field(ge=1, le=MAX_EXPORT_SECONDS, strict=True)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    fit: Literal["pad", "crop"] = "pad"
    style: Literal["calm", "energetic", "professional"] = "professional"
    subtitles: bool = False
    mode: Literal["basic", "gemini"] = "basic"
    ai_consent: bool = Field(default=False, strict=True)
    basic_fallback: bool = Field(default=False, strict=True)


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    channel: Literal["YouTube", "Instagram", "TikTok"]
    scheduled_at: datetime
    timezone: str = Field(default="Europe/Oslo", max_length=80)
    approved: bool = False
