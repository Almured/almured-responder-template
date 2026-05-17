"""compose_answer — templated and LLM-mocked paths."""

from __future__ import annotations

import json

import pytest

from responder.schemas import Brief
from responder.synthesis import (
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_TEMPLATE,
    compose_answer,
)


def _stub_brief(**overrides):
    base = dict(
        id="x",
        sector="B2B SaaS",
        subsector="legal-tech",
        title="Title",
        body="Body content of the brief.",
        benchmark_table={
            "gross_margin": {"min": 0.4, "median": 0.5, "max": 0.6, "unit": "pct"},
            "ebitda_margin": {"min": 0.1, "median": 0.18, "max": 0.25, "unit": "pct"},
        },
        sample_size=10,
        methodology_note="Survey methodology note.",
        confidence_tier="medium",
    )
    base.update(overrides)
    return Brief(**base)


# Templated (no-LLM) path ─────────────────────────────────────────────────


def test_templated_path_returns_answer():
    a = compose_answer("What is the gross margin?", [_stub_brief()], force_llm=False)
    assert a is not None
    assert "gross margin" in a.body
    assert "0.4" in a.body and "0.6" in a.body
    assert "0.5" in a.body  # median
    assert a.confidence == "medium"
    assert a.sources, "expected at least one source"
    assert "Acme Research" in a.sources[0]


def test_templated_path_returns_none_on_empty_briefs():
    assert compose_answer("question", [], force_llm=False) is None


def test_templated_path_falls_back_to_first_metric_when_question_does_not_match():
    a = compose_answer("unrelated query about widgets", [_stub_brief()], force_llm=False)
    # Should still produce an Answer using the brief's first metric.
    assert a is not None
    assert a.confidence == "medium"


def test_templated_path_skips_brief_without_complete_metric():
    bad = _stub_brief(benchmark_table={"x": {"unit": "pct"}})  # missing min/median/max
    assert compose_answer("x?", [bad], force_llm=False) is None


# LLM-mocked path ─────────────────────────────────────────────────────────


def test_llm_path_returns_answer_when_mock_returns_valid_json(mock_anthropic_response):
    a = compose_answer("what is the margin?", [_stub_brief()], force_llm=True)
    assert a is not None
    # Mock returns the default stub body.
    assert "Stub answer body" in a.body
    assert a.confidence == "medium"
    captured = mock_anthropic_response.last_captured()
    assert captured["system"] == SYNTHESIS_SYSTEM_PROMPT


def test_llm_path_returns_none_on_insufficient_data(mock_anthropic_response):
    mock_anthropic_response.set_text("INSUFFICIENT_DATA")
    assert compose_answer("q?", [_stub_brief()], force_llm=True) is None


def test_llm_path_returns_none_on_malformed_json(mock_anthropic_response):
    """LLM mode is authoritative — malformed JSON ⇒ caller skips the
    consultation. No silent fallback to the template path."""
    mock_anthropic_response.set_text("this is not json at all")
    assert compose_answer("q?", [_stub_brief()], force_llm=True) is None


def test_llm_path_handles_code_fenced_json(mock_anthropic_response):
    mock_anthropic_response.set_text(
        "```json\n" + json.dumps({"body": "fenced ok", "sources": [], "confidence": "high"}) + "\n```"
    )
    a = compose_answer("q?", [_stub_brief()], force_llm=True)
    assert a is not None
    assert "fenced ok" in a.body


# Module constants — guard against future edits that weaken them. ────────


def test_synthesis_constants_exist_and_have_expected_shape():
    assert isinstance(SYNTHESIS_SYSTEM_PROMPT, str)
    assert isinstance(SYNTHESIS_USER_TEMPLATE, str)
    assert "<question>" in SYNTHESIS_USER_TEMPLATE
    assert "{briefs_xml}" in SYNTHESIS_USER_TEMPLATE
