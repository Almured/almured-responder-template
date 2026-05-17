"""Shared fixtures for the responder test suite."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any, Iterable, Optional

import pytest


# ── Test corpus: small, deterministic, just enough to exercise FTS ─────────


def _make_brief(
    idx: int,
    sector: str,
    subsector: str,
    *,
    metric: str = "gross_margin_pct",
    body: Optional[str] = None,
    title: Optional[str] = None,
    confidence_tier: str = "medium",
    sample_size: int = 14,
) -> dict[str, Any]:
    return {
        "id": f"test-brief-{idx:03d}",
        "sector": sector,
        "subsector": subsector,
        "title": title or f"{subsector}: FY2026 operating benchmarks",
        "body": body or (
            f"Benchmark study of {sample_size} {subsector} firms in 2026. "
            f"Sample skews toward mid-market operators in Europe."
        ),
        "benchmark_table": json.dumps(
            [
                {"metric": metric, "unit": "%", "min": 30, "median": 45, "max": 58},
                {"metric": "ebitda_margin_pct", "unit": "%", "min": 8, "median": 14, "max": 22},
            ]
        ),
        "sample_size": sample_size,
        "methodology_note": "Anonymized survey, Q1 2026.",
        "last_updated": "2026-02-15",
        "confidence_tier": confidence_tier,
    }


_DEFAULT_BRIEFS: list[dict[str, Any]] = [
    _make_brief(1, "B2B SaaS", "legal-tech matter management",
                metric="gross_margin_pct"),
    _make_brief(2, "B2B SaaS", "dental-tech practice management",
                metric="gross_margin_pct"),
    _make_brief(3, "EU mid-market industrials", "precision machining",
                metric="ebitda_margin_pct"),
    _make_brief(4, "EU mid-market industrials", "metal fabrication",
                metric="ebitda_margin_pct"),
    _make_brief(5, "Regional logistics (EU)", "DACH last-mile urban delivery",
                metric="on_time_delivery_pct"),
]


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE briefs (
            id TEXT PRIMARY KEY,
            sector TEXT NOT NULL,
            subsector TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            benchmark_table TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            methodology_note TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            confidence_tier TEXT NOT NULL CHECK (confidence_tier IN ('low', 'medium', 'high'))
        );
        CREATE VIRTUAL TABLE briefs_fts USING fts5(
            title, body, benchmark_table, tokenize = 'porter unicode61'
        );
        """
    )


def _insert_brief(conn: sqlite3.Connection, brief: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO briefs
            (id, sector, subsector, title, body, benchmark_table,
             sample_size, methodology_note, last_updated, confidence_tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brief["id"], brief["sector"], brief["subsector"], brief["title"],
            brief["body"], brief["benchmark_table"], brief["sample_size"],
            brief["methodology_note"], brief["last_updated"], brief["confidence_tier"],
        ),
    )
    conn.execute(
        "INSERT INTO briefs_fts(title, body, benchmark_table) VALUES (?, ?, ?)",
        (brief["title"], brief["body"], brief["benchmark_table"]),
    )


@pytest.fixture
def temp_briefs_db(tmp_path: Path) -> Path:
    """Create a fresh briefs.sqlite under tmp_path and seed it with the
    default test corpus. Returns the path."""
    db = tmp_path / "briefs.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        _create_schema(conn)
        for brief in _DEFAULT_BRIEFS:
            _insert_brief(conn, brief)
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture
def insert_brief(temp_briefs_db: Path):
    """Return a callable for tests that need to inject extra briefs into
    the temp DB after the default corpus is seeded."""

    def _inject(**overrides: Any) -> dict[str, Any]:
        idx = overrides.pop("idx", 999)
        brief = _make_brief(idx, overrides.pop("sector", "B2B SaaS"),
                            overrides.pop("subsector", "test-subsector"),
                            **overrides)
        conn = sqlite3.connect(str(temp_briefs_db))
        try:
            _insert_brief(conn, brief)
            conn.commit()
        finally:
            conn.close()
        return brief

    return _inject


@pytest.fixture
def monkeypatch_briefs_path(temp_briefs_db: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point BRIEFS_DB_PATH at the temp DB for the duration of the test."""
    monkeypatch.setenv("BRIEFS_DB_PATH", str(temp_briefs_db))
    return temp_briefs_db


# ── Almured API stub via respx ────────────────────────────────────────────


@pytest.fixture
def stub_almured_api(monkeypatch: pytest.MonkeyPatch):
    """Return a respx mock router pre-configured against the prod base URL.
    Tests register routes on `router.get("/consultations").mock(...)`."""
    monkeypatch.setenv("ALMURED_API_KEY", "test-key-at-least-8-chars")
    monkeypatch.setenv("ALMURED_API_BASE_URL", "https://api.almured.com/api/v1")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "1")

    import respx

    with respx.mock(
        base_url="https://api.almured.com/api/v1",
        assert_all_called=False,
    ) as router:
        yield router


# ── Anthropic SDK mock ────────────────────────────────────────────────────


class _StubAnthropicResponse:
    """Stand-in for anthropic.Messages.create return value."""

    def __init__(self, text: str):
        self.content = [types.SimpleNamespace(text=text)]


class _StubAnthropicMessages:
    def __init__(self, captured: list[dict[str, Any]], text_fn):
        self._captured = captured
        self._text_fn = text_fn

    def create(self, **kwargs: Any) -> _StubAnthropicResponse:
        self._captured.append(kwargs)
        text = self._text_fn(kwargs)
        return _StubAnthropicResponse(text)


class _StubAnthropic:
    def __init__(self, text_fn):
        self.captured: list[dict[str, Any]] = []
        self.messages = _StubAnthropicMessages(self.captured, text_fn)


@pytest.fixture
def mock_anthropic_response(monkeypatch: pytest.MonkeyPatch):
    """Replace the anthropic SDK with a stub.

    Default returns a valid Answer JSON. Tests can call
    `mock_anthropic_response.set_text(...)` to override what the stub
    returns, OR pass a callable for per-request behavior.

    Exposes `.captured` — the list of kwargs passed to messages.create —
    so tests can assert on the system + user messages we built.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-anthropic-key")

    state: dict[str, Any] = {
        "text_fn": lambda _kwargs: json.dumps({
            "body": "Stub answer body. The benchmark is X.",
            "sources": ["stub-source-1"],
            "confidence": "medium",
        }),
        "stubs": [],
    }

    def _make_stub_client(*_args: Any, **_kwargs: Any) -> _StubAnthropic:
        stub = _StubAnthropic(state["text_fn"])
        state["stubs"].append(stub)
        return stub

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _make_stub_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    class _Handle:
        @staticmethod
        def set_text(text: str) -> None:
            state["text_fn"] = lambda _kwargs: text

        @staticmethod
        def set_text_fn(fn) -> None:
            state["text_fn"] = fn

        @staticmethod
        def last_captured() -> dict[str, Any]:
            for stub in reversed(state["stubs"]):
                if stub.captured:
                    return stub.captured[-1]
            raise AssertionError("anthropic.messages.create was not called")

        @staticmethod
        def all_captured() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for stub in state["stubs"]:
                out.extend(stub.captured)
            return out

    return _Handle()


# ── Common monkeypatches ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_responder_stop_flag():
    """The main module keeps a module-level asyncio.Event; reset between
    tests so cycle-based tests don't inherit a previous run's stop state."""
    yield
    try:
        from responder import main as responder_main
        responder_main._STOP.clear()
    except Exception:
        pass
