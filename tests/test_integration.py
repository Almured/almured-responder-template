"""End-to-end one-cycle test: stubbed Almured returns one consultation,
agent retrieves briefs, composes an answer (templated path), submits.
Confirms exactly one POST happened with the expected payload shape."""

from __future__ import annotations

from pathlib import Path

import httpx

from responder.api_client import AlmuredClient
from responder.config import load_config
from responder.main import _poll_once


async def test_one_cycle_submits_one_response(
    stub_almured_api,
    monkeypatch_briefs_path: Path,
):
    # Almured returns one open consultation that the briefs corpus can answer.
    consultation = {
        "id": "consult-001",
        "question": "What is the typical gross margin in legal-tech?",
        "category": "industry_research",
    }
    list_route = stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(200, json={"results": [consultation]})
    )
    submit_route = stub_almured_api.post(
        "/consultations/consult-001/responses"
    ).mock(return_value=httpx.Response(201, json={"id": "r-1"}))

    cfg = load_config()
    async with AlmuredClient(cfg) as client:
        await _poll_once(client, ["industry_research"])

    assert list_route.call_count == 1
    assert submit_route.call_count == 1

    # Inspect the submission payload — should match ResponseSubmission shape.
    last_req = submit_route.calls[-1].request
    import json as _json
    payload = _json.loads(last_req.content.decode())
    assert set(payload.keys()) == {"body", "sources", "confidence"}
    assert payload["confidence"] in ("low", "medium", "high")
    assert payload["body"].strip(), "submitted body must not be empty"
    assert "Acme Research" in payload["body"], "templated answer should cite Acme Research"


async def test_no_briefs_match_no_submission(
    stub_almured_api,
    monkeypatch_briefs_path: Path,
):
    consultation = {
        "id": "consult-002",
        "question": "!!!",  # query that produces no FTS5 matches
        "category": "industry_research",
    }
    stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(200, json={"results": [consultation]})
    )
    submit_route = stub_almured_api.post(
        "/consultations/consult-002/responses"
    ).mock(return_value=httpx.Response(201, json={"id": "r-x"}))

    cfg = load_config()
    async with AlmuredClient(cfg) as client:
        await _poll_once(client, ["industry_research"])

    assert submit_route.call_count == 0, "no submission expected when no briefs match"
