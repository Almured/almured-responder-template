"""Pydantic v2 models for the responder template.

Strict mode (`extra="forbid"`) on every model so unexpected fields raise
at validation. Use these at boundaries: API payloads, retrieval results,
synthesis outputs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConfidenceTier = Literal["low", "medium", "high"]


class Brief(BaseModel):
    """One Acme Research brief, as returned by retrieval.search_briefs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    sector: str
    subsector: str
    title: str
    body: str
    # Dict keyed by metric name, e.g. {"gross_margin_pct": {"min": ..., "median": ..., "max": ..., "unit": "%"}}
    benchmark_table: dict[str, dict[str, Any]]
    sample_size: int
    methodology_note: str
    confidence_tier: ConfidenceTier


class Question(BaseModel):
    """A consultation question fetched from Almured."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    title: str
    body: str
    category: str


class Answer(BaseModel):
    """The composed answer, produced by synthesis.compose_answer."""

    model_config = ConfigDict(extra="forbid")

    body: str
    sources: list[str] = Field(default_factory=list)
    confidence: ConfidenceTier
    word_count: int


class ResponseSubmission(BaseModel):
    """Payload sent to POST /consultations/{id}/responses."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    body: str
    sources: list[str]
    confidence: ConfidenceTier
