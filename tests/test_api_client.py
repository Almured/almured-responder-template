"""AlmuredClient tests — happy path, auth failure, retry semantics."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from responder.api_client import AlmuredClient, AuthenticationError, TokenBucket
from responder.config import load_config


@pytest.fixture
def client(stub_almured_api, monkeypatch):
    """A live AlmuredClient pointed at the stubbed router. Tests register
    routes on stub_almured_api and exercise client methods."""
    cfg = load_config()
    c = AlmuredClient(cfg)
    yield c
    # Async close happens in event loop teardown; explicit close in tests
    # that need it.


async def test_list_unanswered_happy_path(client, stub_almured_api):
    stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(
            200,
            json={"results": [
                {"id": "c-1", "question": "What is X?", "category": "industry_research"},
            ]},
        )
    )
    rows = await client.list_unanswered_consultations(["industry_research"])
    assert len(rows) == 1
    assert rows[0]["id"] == "c-1"
    await client.aclose()


async def test_list_unanswered_handles_bare_list(client, stub_almured_api):
    stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "c-2", "question": "Q?"}],
        )
    )
    rows = await client.list_unanswered_consultations(["x"])
    assert len(rows) == 1 and rows[0]["id"] == "c-2"
    await client.aclose()


async def test_401_raises_authentication_error(client, stub_almured_api):
    stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
    )
    with pytest.raises(AuthenticationError):
        await client.list_unanswered_consultations(["x"])
    await client.aclose()


def _fast_sleep_patcher(monkeypatch):
    """Replace responder.api_client.asyncio.sleep with a near-instant
    coroutine. Captures the real sleep first so the replacement doesn't
    recurse through the patched name."""
    real_sleep = asyncio.sleep

    async def _instant(_delay):
        await real_sleep(0)

    monkeypatch.setattr("responder.api_client.asyncio.sleep", _instant)


async def test_500_retried_then_succeeds(client, stub_almured_api, monkeypatch):
    _fast_sleep_patcher(monkeypatch)
    route = stub_almured_api.get("/consultations").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(500, text="boom"),
            httpx.Response(200, json={"results": []}),
        ]
    )
    rows = await client.list_unanswered_consultations(["x"])
    assert rows == []
    assert route.call_count == 3
    await client.aclose()


async def test_5xx_exhausted_returns_response(client, stub_almured_api, monkeypatch):
    # 4 attempts all 500 → returns the last response, doesn't raise.
    _fast_sleep_patcher(monkeypatch)
    route = stub_almured_api.get("/consultations").mock(
        return_value=httpx.Response(500, text="boom")
    )
    rows = await client.list_unanswered_consultations(["x"])
    assert rows == []
    assert route.call_count == 4  # 1 + 3 retries
    await client.aclose()


async def test_submit_response_success(client, stub_almured_api):
    stub_almured_api.post("/consultations/abc/responses").mock(
        return_value=httpx.Response(201, json={"id": "r-1"})
    )
    success, status = await client.submit_response(
        "abc", "body text", ["source-1"], "high"
    )
    assert success is True
    assert status == 201
    await client.aclose()


# TokenBucket pacing ─────────────────────────────────────────────────────


async def test_token_bucket_paces_acquires_beyond_capacity():
    """Capacity=2, rate=200/min (≈3.33/sec). 5 acquires:
    - 2 immediate (capacity)
    - 3 paced at ~0.3s each → ~0.9s total."""
    import time
    bucket = TokenBucket(rate_per_minute=200, capacity=2)
    started = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - started
    # Lower bound: at least the rate-limited writes had to wait.
    assert elapsed >= 0.7, f"expected ≥0.7s pacing, got {elapsed:.2f}s"
    # Upper bound: well below 60s — confirms test-speed monkeypatching.
    assert elapsed < 5.0, f"unexpectedly slow: {elapsed:.2f}s"
