"""FTS5-backed retrieval over data/briefs.sqlite.

The seed script (scripts/seed_briefs.py) stores benchmark_table as a JSON
array of metric objects. retrieval normalizes that to a dict keyed by
metric name (matching schemas.Brief.benchmark_table) before returning.
Body fields go through sanitize_input as defense-in-depth — a partner
who replaces the dataset with their own must not assume that data is
clean of injection markers, control chars, or HTML.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .sanitization import sanitize_input
from .schemas import Brief

# FTS5 has its own query syntax; characters outside the alphanumeric class
# can produce SyntaxError. Lower the query to a safe whitespace-separated
# token list and OR-join — good enough for prose questions.
_FTS5_TOKEN_SAFE = re.compile(r"[^A-Za-z0-9 ]+")


def _to_fts5_query(raw: str) -> str:
    if not raw:
        return '""'
    cleaned = _FTS5_TOKEN_SAFE.sub(" ", raw).strip()
    tokens = [t for t in cleaned.split() if len(t) >= 2]
    if not tokens:
        return '""'
    # OR-join so any token can hit; FTS5 ranks more-matching rows higher.
    return " OR ".join(tokens)


def _normalize_benchmarks(raw_json: str) -> dict[str, dict[str, Any]]:
    """Convert seed-shape [{metric, unit, min, median, max}, ...] →
    {metric_name: {min, median, max, unit}}. Already-dict-shaped input
    is returned as-is (forward-compat for datasets stored that way)."""
    try:
        parsed = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        # Already normalized — assume each value is a metric dict.
        return {k: v for k, v in parsed.items() if isinstance(v, dict)}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(parsed, list):
        for row in parsed:
            if not isinstance(row, dict):
                continue
            name = row.get("metric")
            if not name:
                continue
            out[name] = {
                "min": row.get("min"),
                "median": row.get("median"),
                "max": row.get("max"),
                "unit": row.get("unit"),
            }
    return out


def _resolve_db_path(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    # Try config — if ALMURED_API_KEY isn't set, fall back to the
    # documented default so search_briefs is usable in standalone scripts
    # (the W5b verification commands rely on this).
    env_path = os.environ.get("BRIEFS_DB_PATH")
    if env_path:
        return env_path
    return "./data/briefs.sqlite"


def search_briefs(
    query: str,
    sector_filter: Optional[str] = None,
    top_k: int = 3,
    db_path: Optional[str] = None,
) -> list[Brief]:
    """Return up to `top_k` Acme Research briefs ranked by FTS5 match.

    `query` is the asker's question (or a distilled keyword set). When
    `sector_filter` is set, only that sector is searched. Returns an
    empty list if the dataset doesn't exist, the query has no matchable
    tokens, or no rows match.
    """
    path = _resolve_db_path(db_path)
    p = Path(path)
    if not p.exists():
        return []

    fts_query = _to_fts5_query(query)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        # FTS5 returns rowids ranked by relevance. Pull more than top_k
        # when a sector filter is in play so we can drop non-matching
        # sectors and still return up to top_k.
        over_fetch = top_k * 5 if sector_filter else top_k
        fts_rows = conn.execute(
            "SELECT rowid FROM briefs_fts WHERE briefs_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, over_fetch),
        ).fetchall()
        rowids = [r["rowid"] for r in fts_rows]
        if not rowids:
            return []

        # Resolve each rowid against the base table, preserving FTS5 order.
        out: list[Brief] = []
        for rid in rowids:
            row = conn.execute(
                """
                SELECT id, sector, subsector, title, body, benchmark_table,
                       sample_size, methodology_note, confidence_tier
                FROM briefs
                WHERE rowid = ?
                """,
                (rid,),
            ).fetchone()
            if row is None:
                continue
            if sector_filter and row["sector"] != sector_filter:
                continue
            out.append(
                Brief(
                    id=row["id"],
                    sector=row["sector"],
                    subsector=row["subsector"],
                    title=row["title"],
                    # Defense-in-depth: even our own seed data goes through
                    # sanitize_input on the body so partners who replace the
                    # dataset inherit the same trust boundary.
                    body=sanitize_input(row["body"], max_len=50_000),
                    benchmark_table=_normalize_benchmarks(row["benchmark_table"]),
                    sample_size=row["sample_size"],
                    methodology_note=row["methodology_note"],
                    confidence_tier=row["confidence_tier"],
                )
            )
            if len(out) >= top_k:
                break
        return out
    finally:
        conn.close()
