"""Pydantic v2 schema tests — strict mode, required-field enforcement,
type coercion limits."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from responder.schemas import Answer, Brief, Question, ResponseSubmission


def _valid_brief_kwargs() -> dict:
    return {
        "id": "b-001",
        "sector": "B2B SaaS",
        "subsector": "legal-tech",
        "title": "t",
        "body": "b",
        "benchmark_table": {"gross_margin": {"min": 0.4, "median": 0.5, "max": 0.6, "unit": "pct"}},
        "sample_size": 10,
        "methodology_note": "m",
        "confidence_tier": "medium",
    }


def test_brief_accepts_valid_payload():
    Brief(**_valid_brief_kwargs())


def test_brief_rejects_extra_field():
    payload = _valid_brief_kwargs() | {"unexpected": "x"}
    with pytest.raises(ValidationError):
        Brief(**payload)


def test_brief_rejects_invalid_confidence_tier():
    payload = _valid_brief_kwargs() | {"confidence_tier": "very_high"}
    with pytest.raises(ValidationError):
        Brief(**payload)


def test_brief_requires_all_fields():
    for missing in ("id", "sector", "subsector", "title", "body",
                    "benchmark_table", "sample_size", "methodology_note",
                    "confidence_tier"):
        payload = _valid_brief_kwargs()
        del payload[missing]
        with pytest.raises(ValidationError):
            Brief(**payload)


def test_question_strict_extra_field_rejected():
    with pytest.raises(ValidationError):
        Question(
            consultation_id="c-1",
            title="t",
            body="b",
            category="industry_research",
            asker_id="should-not-be-accepted",  # type: ignore[call-arg]
        )


def test_answer_round_trip():
    a = Answer(body="b", sources=["s1"], confidence="high", word_count=1)
    assert a.model_dump() == {
        "body": "b",
        "sources": ["s1"],
        "confidence": "high",
        "word_count": 1,
    }


def test_response_submission_strict():
    payload = {
        "consultation_id": "c-1",
        "body": "x",
        "sources": [],
        "confidence": "low",
    }
    ResponseSubmission(**payload)
    with pytest.raises(ValidationError):
        ResponseSubmission(**payload, status="open")  # type: ignore[call-arg]
