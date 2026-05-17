"""Responder agent entry point.

Polling loop:
  1. List unanswered consultations in target categories.
  2. For each, sanitize-check the question, retrieve top briefs,
     compose an Answer, submit.
  3. Sleep until next tick (or until SIGTERM/SIGINT).

Logging is structured JSON-lines. The answer body and asker identity
are NEVER logged in full — only the consultation_id, status, and
synthesis confidence.

Run with `python -m responder.main`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from typing import Any

from .api_client import AlmuredClient, AuthenticationError
from .config import Config, load_config
from .retrieval import search_briefs
from .sanitization import check_injection
from .synthesis import compose_answer

logger = logging.getLogger("responder")

_STOP = asyncio.Event()


class _JsonFormatter(logging.Formatter):
    """One JSON object per log line. Extras land at the top level."""

    _STD_FIELDS = {
        "args", "msg", "name", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in self._STD_FIELDS:
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _request_stop() -> None:
    if not _STOP.is_set():
        logger.info("shutdown_signal", extra={"event": "shutdown_signal"})
        _STOP.set()


async def _handle_consultation(
    client: AlmuredClient, consultation: dict[str, Any]
) -> None:
    cid = consultation.get("id") or consultation.get("consultation_id")
    if not cid:
        return
    cid = str(cid)

    question = (
        consultation.get("question")
        or consultation.get("title")
        or consultation.get("body")
        or ""
    )
    if not question.strip():
        return

    started = time.monotonic()

    # Defense-in-depth: log if the question contains injection markers.
    # We do NOT echo the suspicious content — only the boolean signal.
    # We proceed with synthesis anyway because scrub_for_prompt
    # neutralizes the LLM-path risk, and the template path has no
    # prompt-injection surface.
    if check_injection(question):
        logger.info(
            "injection_marker_detected",
            extra={
                "event": "injection_marker_detected",
                "consultation_id": cid,
                "action": "proceed",
            },
        )

    briefs = search_briefs(question, top_k=3)
    if not briefs:
        logger.info(
            "skip",
            extra={
                "event": "skip",
                "consultation_id": cid,
                "reason": "no_matching_briefs",
            },
        )
        return

    answer = compose_answer(question, briefs)
    if answer is None:
        logger.info(
            "skip",
            extra={
                "event": "skip",
                "consultation_id": cid,
                "reason": "insufficient_data",
            },
        )
        return

    success, status = await client.submit_response(
        consultation_id=cid,
        body=answer.body,
        sources=answer.sources,
        confidence=answer.confidence,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "submit_done",
        extra={
            "event": "submit_done",
            "consultation_id": cid,
            "action": "submitted" if success else "submit_failed",
            "status": status,
            "latency_ms": latency_ms,
            "confidence": answer.confidence,
            "word_count": answer.word_count,
        },
    )


async def _poll_once(client: AlmuredClient, categories: list[str]) -> None:
    started = time.monotonic()
    try:
        consultations = await client.list_unanswered_consultations(categories)
    except AuthenticationError as exc:
        logger.error(
            "auth_failed",
            extra={
                "event": "auth_failed",
                "hint": "rotate ALMURED_API_KEY at https://almured.com/account",
                "error": str(exc),
            },
        )
        _STOP.set()
        return
    except Exception as exc:  # noqa: BLE001 — we never want polling to crash the loop
        logger.warning(
            "poll_failed",
            extra={"event": "poll_failed", "error": type(exc).__name__},
        )
        return

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "poll_tick",
        extra={
            "event": "poll_tick",
            "consultations_found": len(consultations),
            "latency_ms": latency_ms,
        },
    )

    for c in consultations:
        if _STOP.is_set():
            break
        try:
            await _handle_consultation(client, c)
        except AuthenticationError:
            _STOP.set()
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "handle_failed",
                extra={"event": "handle_failed", "error": type(exc).__name__},
            )


async def run(config: Config | None = None) -> int:
    _setup_logging()
    cfg = config or load_config()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except (NotImplementedError, RuntimeError):
                # Windows / non-main thread / restricted env — fall through.
                pass
    except RuntimeError:
        pass

    logger.info(
        "startup",
        extra={
            "event": "startup",
            "categories": list(cfg.target_categories),
            "poll_interval_s": cfg.poll_interval_seconds,
            "llm_synthesis_enabled": cfg.enable_llm_synthesis,
            "base_url": cfg.almured_api_base_url,
        },
    )

    async with AlmuredClient(cfg) as client:
        try:
            while not _STOP.is_set():
                await _poll_once(client, list(cfg.target_categories))
                if _STOP.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        _STOP.wait(), timeout=cfg.poll_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("shutdown", extra={"event": "shutdown"})

    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
