"""FTS5 retrieval over the conftest temp_briefs_db."""

from __future__ import annotations

from pathlib import Path

from responder.retrieval import search_briefs


def test_returns_up_to_top_k(temp_briefs_db: Path):
    briefs = search_briefs("benchmark", top_k=3, db_path=str(temp_briefs_db))
    assert 1 <= len(briefs) <= 3


def test_top_k_respected(temp_briefs_db: Path):
    briefs = search_briefs("benchmark", top_k=2, db_path=str(temp_briefs_db))
    assert len(briefs) <= 2


def test_sector_filter_restricts_results(temp_briefs_db: Path):
    briefs = search_briefs(
        "benchmark",
        sector_filter="B2B SaaS",
        top_k=10,
        db_path=str(temp_briefs_db),
    )
    assert briefs, "expected at least one B2B SaaS brief"
    for b in briefs:
        assert b.sector == "B2B SaaS"


def test_empty_query_returns_empty(temp_briefs_db: Path):
    briefs = search_briefs("!!!", top_k=3, db_path=str(temp_briefs_db))
    # All special chars stripped → empty FTS query → no rows
    assert briefs == []


def test_missing_db_path_returns_empty(tmp_path: Path):
    nonexistent = tmp_path / "does-not-exist.sqlite"
    assert search_briefs("anything", db_path=str(nonexistent)) == []


def test_benchmark_table_normalized_to_dict(temp_briefs_db: Path):
    briefs = search_briefs("benchmark", top_k=1, db_path=str(temp_briefs_db))
    assert briefs, "expected at least one match"
    bt = briefs[0].benchmark_table
    # Seed uses list-shape JSON; retrieval normalizes to dict-keyed-by-name.
    assert isinstance(bt, dict)
    for metric_name, metric_data in bt.items():
        assert "min" in metric_data
        assert "median" in metric_data
        assert "max" in metric_data
