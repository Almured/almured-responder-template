"""Async Almured REST client.

- httpx.AsyncClient with bearer auth on every request.
- Token-bucket rate limiting (60/min browse, 10/min write) so we don't
  exhaust the server-side budget and get 429'd on every call.
- Exponential backoff on 429/5xx, max 3 retries.
- Logs requests/responses without secrets (Authorization header is never
  logged; bodies are not logged at all).
- Inbound text fields (consultation.question, etc.) are passed through
  sanitize_input before being returned to the caller — defense-in-depth
  against marketplace-side bugs that might let injection markers slip
  past Almured's own content filter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable, Optional

import httpx

from .config import Config, load_config
from .sanitization import sanitize_input

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when Almured returns 401. Fatal — bad credentials, not a
    transient condition worth retrying."""


class TokenBucket:
    """Single-task in-memory token bucket. Not safe across asyncio tasks
    without external locking — fine for the single-poller pattern this
    template uses."""

    def __init__(self, rate_per_minute: float):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be > 0")
        self._rate_per_second = rate_per_minute / 60.0
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._last = time.monotonic()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_s = (1.0 - self._tokens) / self._rate_per_second
            await asyncio.sleep(wait_s)


class AlmuredClient:
    def __init__(self, config: Optional[Config] = None):
        self._config = config or load_config()
        self._client = httpx.AsyncClient(
            base_url=self._config.almured_api_base_url,
            headers={
                "Authorization": f"Bearer {self._config.almured_api_key}",
                "Accept": "application/json",
                "User-Agent": "almured-responder-template/0.1",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._browse_bucket = TokenBucket(60)
        self._write_bucket = TokenBucket(10)

    async def __aenter__(self) -> "AlmuredClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Up to 3 retries (4 attempts total) with exponential backoff on
        429 and 5xx. 401 short-circuits via AuthenticationError."""
        last_resp: Optional[httpx.Response] = None
        for attempt in range(4):
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                logger.warning(
                    "almured_request_error",
                    extra={
                        "event": "almured_request_error",
                        "method": method,
                        "path": path,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                if attempt >= 3:
                    raise
                await asyncio.sleep(2 ** attempt)
                continue

            last_resp = resp
            if resp.status_code == 401:
                # Don't retry — credentials are bad. Caller handles.
                raise AuthenticationError(
                    "Almured returned 401. Check ALMURED_API_KEY at "
                    "https://almured.com/account."
                )

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= 3:
                    return resp
                backoff = 2 ** attempt  # 1, 2, 4 seconds
                # Honor Retry-After when the server sends one.
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        backoff = max(backoff, float(retry_after))
                    except ValueError:
                        pass
                logger.info(
                    "almured_retry",
                    extra={
                        "event": "almured_retry",
                        "method": method,
                        "path": path,
                        "status": resp.status_code,
                        "attempt": attempt,
                        "backoff_s": backoff,
                    },
                )
                await asyncio.sleep(backoff)
                continue

            return resp

        # Loop exhausted (only reachable if all 4 attempts returned 429/5xx).
        assert last_resp is not None
        return last_resp

    async def list_unanswered_consultations(
        self, categories: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Return the marketplace's current unanswered consultations in
        any of the given categories. Sanitizes free-text fields before
        returning."""
        await self._browse_bucket.acquire()
        params: list[tuple[str, str]] = [("status", "open")]
        for cat in categories:
            if cat:
                params.append(("category", cat))

        resp = await self._request_with_retry("GET", "/consultations", params=params)
        if resp.status_code != 200:
            logger.warning(
                "almured_list_failed",
                extra={
                    "event": "almured_list_failed",
                    "status": resp.status_code,
                },
            )
            return []

        try:
            data = resp.json()
        except ValueError:
            return []

        if isinstance(data, dict):
            results = data.get("results") or data.get("consultations") or []
        elif isinstance(data, list):
            results = data
        else:
            results = []

        return [self._sanitize_consultation(c) for c in results if isinstance(c, dict)]

    async def get_consultation(self, consultation_id: str) -> Optional[dict[str, Any]]:
        await self._browse_bucket.acquire()
        resp = await self._request_with_retry(
            "GET", f"/consultations/{consultation_id}"
        )
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return self._sanitize_consultation(data)

    async def submit_response(
        self,
        consultation_id: str,
        body: str,
        sources: list[str],
        confidence: str,
    ) -> tuple[bool, int]:
        """Submit an answer. Returns (success, status_code).

        `body` and `sources` are template-generated; they pass through
        unchanged. The server validates schema on its side.
        """
        await self._write_bucket.acquire()
        payload = {
            "body": body,
            "sources": sources,
            "confidence": confidence,
        }
        resp = await self._request_with_retry(
            "POST",
            f"/consultations/{consultation_id}/responses",
            json=payload,
        )
        success = 200 <= resp.status_code < 300
        logger.info(
            "almured_submit_done",
            extra={
                "event": "almured_submit_done",
                "consultation_id": consultation_id,
                "status": resp.status_code,
                "success": success,
            },
        )
        return success, resp.status_code

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_consultation(c: dict[str, Any]) -> dict[str, Any]:
        """Pass free-text fields through sanitize_input on the way out
        of the client so callers can treat them as cleaned."""
        out = dict(c)
        for field in ("question", "title", "body"):
            value = out.get(field)
            if isinstance(value, str):
                out[field] = sanitize_input(value, max_len=10_000)
        # Server may nest the question under different keys depending on
        # endpoint version; sanitize any string value we recognise.
        return out
