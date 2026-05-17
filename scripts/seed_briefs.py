"""
Deterministic generator for the Acme Research synthetic dataset.

Acme Research is a fictional firm used to demonstrate the Almured responder
template against realistic-looking but entirely synthetic industry-research
content. NOTHING in this dataset describes real companies, real surveys, or
real benchmarks.

Output: data/briefs.sqlite with a `briefs` table and a `briefs_fts` FTS5
virtual table for keyword retrieval. 50 briefs across 5 sectors × 10
subsectors each. Idempotent — rerunning regenerates the same file.

Usage: `python scripts/seed_briefs.py`
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "briefs.sqlite"
SEED = 42

# ── Sector / subsector taxonomy ───────────────────────────────────────────
# 5 sectors × 10 subsectors = 50 briefs.

SECTORS: dict[str, list[str]] = {
    "EU mid-market industrials": [
        "precision machining",
        "industrial automation",
        "metal fabrication",
        "food processing equipment",
        "packaging machinery",
        "specialty pumps and valves",
        "industrial coatings",
        "aerospace tier-2 components",
        "rail components",
        "industrial heating systems",
    ],
    "Vertical B2B SaaS (niche)": [
        "legal-tech matter management",
        "dental-tech practice management",
        "agri-tech precision farming",
        "construction-tech field operations",
        "aquaculture management",
        "fleet maintenance SaaS",
        "insurance broker tooling",
        "pharmacy compliance",
        "laboratory informatics",
        "restaurant operations",
    ],
    "Regional logistics (EU)": [
        "DACH groupage road freight",
        "DACH last-mile urban delivery",
        "Iberian pharma cold chain",
        "Nordic e-commerce fulfilment",
        "Benelux less-than-truckload",
        "Mediterranean port forwarding",
        "CEE rail freight",
        "UK-EU customs intermediation",
        "Alpine line-haul",
        "cross-Adriatic ferry freight",
    ],
    "Specialty chemicals": [
        "adhesives and sealants",
        "coatings additives",
        "electronic chemicals",
        "food preservatives",
        "industrial enzymes",
        "water treatment chemicals",
        "pigments and dyes",
        "lubricant additives",
        "specialty polymers",
        "biological agricultural chemicals",
    ],
    "Niche consumer (D2C, regional retail)": [
        "premium pet nutrition",
        "sustainable swimwear",
        "craft non-alcoholic beverages",
        "home fragrance subscription",
        "children's natural toys",
        "kitchen tools D2C",
        "specialty coffee subscription",
        "eco home cleaning",
        "premium sleepwear",
        "sustainable activewear",
    ],
}

# ── Metric pools per sector ───────────────────────────────────────────────
# Each entry: (metric_name, unit, plausible (min_low, min_high, med_low,
# med_high, max_low, max_high) ranges). The generator picks specific values
# inside these brackets for each brief.

METRIC_POOLS: dict[str, list[tuple]] = {
    "EU mid-market industrials": [
        ("gross_margin_pct", "%", (22, 28, 30, 36, 40, 48)),
        ("ebitda_margin_pct", "%", (8, 12, 14, 18, 20, 26)),
        ("capex_pct_revenue", "%", (3, 5, 6, 9, 11, 15)),
        ("days_inventory", "days", (45, 65, 70, 90, 100, 130)),
        ("employee_count", "FTE", (60, 90, 110, 180, 220, 340)),
        ("revenue_per_employee_eur_k", "EUR k / FTE", (140, 180, 200, 260, 290, 360)),
    ],
    "Vertical B2B SaaS (niche)": [
        ("gross_margin_pct", "%", (62, 70, 73, 80, 82, 88)),
        ("net_revenue_retention_pct", "%", (88, 96, 100, 110, 115, 128)),
        ("payback_months", "months", (10, 14, 16, 22, 25, 34)),
        ("rule_of_40", "%", (12, 22, 28, 38, 42, 56)),
        ("magic_number", "ratio", (40, 60, 70, 95, 105, 130)),  # divide by 100 at render
        ("gross_dollar_retention_pct", "%", (82, 88, 90, 94, 95, 98)),
        ("arpa_eur", "EUR / yr", (1800, 3200, 4800, 9500, 12000, 24000)),
    ],
    "Regional logistics (EU)": [
        ("gross_margin_pct", "%", (8, 12, 14, 20, 22, 30)),
        ("asset_utilization_pct", "%", (55, 65, 70, 80, 82, 92)),
        ("fuel_cost_pct_revenue", "%", (12, 16, 18, 24, 26, 34)),
        ("on_time_delivery_pct", "%", (86, 90, 92, 96, 96, 99)),
        ("claims_ratio_pct", "%", (0, 1, 1, 2, 2, 4)),  # tiny — handle decimals
        ("employee_turnover_pct", "%", (8, 14, 16, 24, 26, 38)),
    ],
    "Specialty chemicals": [
        ("gross_margin_pct", "%", (24, 30, 32, 40, 42, 52)),
        ("ebitda_margin_pct", "%", (10, 14, 16, 22, 24, 32)),
        ("r_and_d_pct_revenue", "%", (2, 4, 5, 8, 9, 14)),
        ("capacity_utilization_pct", "%", (62, 72, 76, 86, 88, 95)),
        ("energy_cost_pct_revenue", "%", (5, 9, 11, 16, 18, 26)),
        ("working_capital_days", "days", (50, 70, 80, 110, 115, 150)),
    ],
    "Niche consumer (D2C, regional retail)": [
        ("gross_margin_pct", "%", (42, 52, 56, 66, 68, 78)),
        ("cac_eur", "EUR", (18, 32, 38, 65, 70, 120)),
        ("ltv_cac_ratio", "ratio", (15, 25, 28, 42, 45, 70)),  # divide by 10 at render
        ("repeat_purchase_rate_pct", "%", (22, 32, 36, 48, 50, 65)),
        ("return_rate_pct", "%", (2, 5, 6, 12, 14, 24)),
        ("marketing_pct_revenue", "%", (12, 18, 22, 32, 35, 48)),
    ],
}

# Metrics that should be rendered as floats with one decimal place because
# the raw integer ranges encode them at 10x or 100x precision.
SCALED_METRICS: dict[str, int] = {
    "magic_number": 100,       # raw 60 → 0.60 ratio
    "ltv_cac_ratio": 10,       # raw 25 → 2.5x
    "claims_ratio_pct": 1,     # treat as raw — small but acceptable
}

# ── Prose templates ───────────────────────────────────────────────────────

TITLE_TEMPLATES = [
    "{Subsector}: {year} operating benchmarks",
    "Margin and capital efficiency in {subsector}, FY{year}",
    "{Subsector} performance review — {quarter} {year}",
    "Operator metrics: {subsector} in {region}, {year}",
    "Benchmark study: {subsector} ({year})",
]

OPENING_TEMPLATES = [
    (
        "Acme Research surveyed {n} operators in {subsector} during {period}. "
        "Respondents skew toward firms with annual revenue between €{rev_low}M "
        "and €{rev_high}M; the median respondent has been operating in this "
        "subsector for {years} years."
    ),
    (
        "This brief covers operator-level benchmarks for {subsector}, drawn "
        "from an anonymized survey conducted by Acme Research in {period}. "
        "Sample is {n} firms; coverage is {region}-weighted."
    ),
    (
        "Acme Research compiled financial and operational metrics for {n} "
        "firms in {subsector} ({region}, {period}). All values are anonymized; "
        "individual respondents are not identified."
    ),
]

MIDDLE_TEMPLATES = [
    (
        "The headline finding is that {finding_metric} clusters tightly around "
        "the median ({median_label}), but the dispersion in {dispersion_metric} "
        "is wider than in previous waves of this survey. The driver appears to "
        "be {driver}."
    ),
    (
        "Operators in the top quartile of {finding_metric} share two "
        "structural traits: {trait_a} and {trait_b}. The bottom quartile is "
        "more heterogeneous; common drag factors include {drag}."
    ),
    (
        "Comparing {year} against {prior_year}, the most material movement is "
        "in {finding_metric}, which {direction} by approximately {delta_pct}% "
        "at the median. {dispersion_metric} also moved, but within the "
        "historical range."
    ),
]

CLOSING_TEMPLATES = [
    (
        "Caveats: sample size is moderate ({n} firms), and the survey skews "
        "toward operators who self-identify as actively benchmarking. Readers "
        "should treat these figures as directional rather than authoritative."
    ),
    (
        "We expect the {finding_metric} range to compress in subsequent waves "
        "as a handful of larger operators consolidate share. Sample composition "
        "may shift accordingly."
    ),
    (
        "This brief is updated {cadence}. The next refresh is scheduled for "
        "{next_refresh_quarter} {year}."
    ),
]

DRIVERS = [
    "regulatory shifts in the trailing 18 months",
    "input cost volatility (notably energy and freight)",
    "a wave of private-equity rollups in 2024-2025",
    "post-pandemic normalization of operating tempo",
    "selective demand softness in adjacent end markets",
    "scaling pains at the upper end of the sample",
]

TRAITS = [
    "vertical specialization rather than horizontal breadth",
    "tight working-capital discipline",
    "an early move into adjacent product lines",
    "operator-owner founders still in seat",
    "explicit pricing power on at least one core SKU",
    "anchor customers under multi-year contracts",
    "regionally concentrated headcount",
]

DRAG_FACTORS = [
    "customer concentration above 30% from a single account",
    "legacy ERP migrations that overran budget",
    "exposure to a single energy-intensive process step",
    "talent attrition in field-service roles",
    "passive distribution channels with low pull-through",
    "underinvestment in customer-success motion",
]

CADENCE_OPTIONS = ["annually", "semi-annually", "quarterly", "on a 9-month cycle"]

REGION_FOR_SECTOR = {
    "EU mid-market industrials": "EU-15",
    "Vertical B2B SaaS (niche)": "EMEA",
    "Regional logistics (EU)": "EU + UK",
    "Specialty chemicals": "EU + UK",
    "Niche consumer (D2C, regional retail)": "EU-DACH + Nordics",
}


# ── Determinism helpers ───────────────────────────────────────────────────


def det_uuid(rng: random.Random) -> str:
    """Generate a deterministic UUID from the seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128)))


def pick_value(rng: random.Random, low_range: tuple, high_range: tuple) -> int:
    """Pick a value inside [low_range, high_range] using the RNG."""
    return rng.randint(low_range[0], low_range[1])


def build_benchmark_table(rng: random.Random, sector: str) -> tuple[list[dict], str]:
    """Pick 3-5 metrics from the sector pool and synthesize plausible
    min/median/max for each. Returns (table, headline_metric)."""
    pool = METRIC_POOLS[sector]
    k = rng.randint(3, 5)
    chosen = rng.sample(pool, k)
    rows: list[dict] = []
    for name, unit, ranges in chosen:
        min_low, min_high, med_low, med_high, max_low, max_high = ranges
        raw_min = rng.randint(min_low, min_high)
        raw_med = rng.randint(max(raw_min + 1, med_low), med_high)
        raw_max = rng.randint(max(raw_med + 1, max_low), max_high)
        scale = SCALED_METRICS.get(name, 1)
        if scale > 1:
            min_v: float | int = round(raw_min / scale, 2)
            med_v: float | int = round(raw_med / scale, 2)
            max_v: float | int = round(raw_max / scale, 2)
        else:
            min_v, med_v, max_v = raw_min, raw_med, raw_max
        rows.append({
            "metric": name,
            "unit": unit,
            "min": min_v,
            "median": med_v,
            "max": max_v,
        })
    headline = rng.choice(rows)["metric"]
    return rows, headline


def assemble_body(
    rng: random.Random,
    subsector: str,
    sector: str,
    region: str,
    n: int,
    year: int,
    bench_rows: list[dict],
    headline_metric: str,
) -> str:
    """Assemble a 2-3 paragraph body from templates."""
    other_metrics = [r["metric"] for r in bench_rows if r["metric"] != headline_metric]
    dispersion_metric = rng.choice(other_metrics) if other_metrics else headline_metric

    period = f"Q{rng.randint(1, 4)} {year}"
    quarter = period.split()[0]
    rev_low = rng.choice([2, 5, 10, 15, 25, 40])
    rev_high = rev_low * rng.choice([3, 4, 5, 8])
    years = rng.randint(5, 22)
    median_label = f"see benchmark table for {headline_metric}"

    direction = rng.choice(["compressed", "expanded", "shifted upward", "shifted downward"])
    delta_pct = rng.randint(2, 14)
    prior_year = year - 1

    placeholders = {
        "subsector": subsector,
        "Subsector": subsector[0].upper() + subsector[1:],
        "sector": sector,
        "region": region,
        "n": n,
        "year": year,
        "prior_year": prior_year,
        "period": period,
        "quarter": quarter,
        "rev_low": rev_low,
        "rev_high": rev_high,
        "years": years,
        "finding_metric": headline_metric,
        "dispersion_metric": dispersion_metric,
        "median_label": median_label,
        "driver": rng.choice(DRIVERS),
        "trait_a": rng.choice(TRAITS),
        "trait_b": rng.choice([t for t in TRAITS if True]),
        "drag": rng.choice(DRAG_FACTORS),
        "direction": direction,
        "delta_pct": delta_pct,
        "cadence": rng.choice(CADENCE_OPTIONS),
        "next_refresh_quarter": f"Q{rng.randint(1, 4)}",
    }

    opening = rng.choice(OPENING_TEMPLATES).format(**placeholders)
    middle = rng.choice(MIDDLE_TEMPLATES).format(**placeholders)
    closing = rng.choice(CLOSING_TEMPLATES).format(**placeholders)
    # Two or three paragraphs depending on the dice.
    if rng.random() < 0.4:
        return f"{opening}\n\n{closing}"
    return f"{opening}\n\n{middle}\n\n{closing}"


def assemble_title(rng: random.Random, subsector: str, sector: str, region: str, year: int) -> str:
    tmpl = rng.choice(TITLE_TEMPLATES)
    return tmpl.format(
        subsector=subsector,
        Subsector=subsector[0].upper() + subsector[1:],
        region=region,
        year=year,
        quarter=f"Q{rng.randint(1, 4)}",
    )


def methodology_note(rng: random.Random, n: int, year: int) -> str:
    opts = [
        f"Anonymized survey of {n} firms, Q{rng.randint(1, 4)} {year}.",
        f"Aggregated public filings plus management interviews, FY{year - 1}–{year}.",
        f"Operator-supplied survey responses, n={n}, calendar year {year}.",
        f"Cross-section of {n} firms; data normalized to FY{year} basis.",
    ]
    return rng.choice(opts)


def confidence_tier(n: int) -> str:
    if n >= 20:
        return "high"
    if n >= 12:
        return "medium"
    return "low"


def last_updated_date(rng: random.Random, year: int) -> str:
    # Cluster updates in Q1-Q2 of `year` so the dataset feels recent.
    start = date(year, 1, 1)
    offset_days = rng.randint(0, 180)
    return (start + timedelta(days=offset_days)).isoformat()


# ── DB setup ──────────────────────────────────────────────────────────────


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS briefs_fts;
        DROP TABLE IF EXISTS briefs;

        CREATE TABLE briefs (
            id TEXT PRIMARY KEY,
            sector TEXT NOT NULL,
            subsector TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            benchmark_table TEXT NOT NULL,  -- JSON
            sample_size INTEGER NOT NULL,
            methodology_note TEXT NOT NULL,
            last_updated TEXT NOT NULL,     -- ISO date
            confidence_tier TEXT NOT NULL CHECK (confidence_tier IN ('low', 'medium', 'high'))
        );

        CREATE INDEX idx_briefs_sector ON briefs(sector);
        CREATE INDEX idx_briefs_subsector ON briefs(subsector);

        -- FTS5 virtual table for keyword retrieval. Stores text directly
        -- (not external-content) so a fresh seed run starts clean.
        CREATE VIRTUAL TABLE briefs_fts USING fts5(
            title,
            body,
            benchmark_table,
            tokenize = 'porter unicode61'
        );
        """
    )


def insert_brief(conn: sqlite3.Connection, brief: dict) -> None:
    conn.execute(
        """
        INSERT INTO briefs
            (id, sector, subsector, title, body, benchmark_table,
             sample_size, methodology_note, last_updated, confidence_tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brief["id"],
            brief["sector"],
            brief["subsector"],
            brief["title"],
            brief["body"],
            brief["benchmark_table"],
            brief["sample_size"],
            brief["methodology_note"],
            brief["last_updated"],
            brief["confidence_tier"],
        ),
    )
    conn.execute(
        "INSERT INTO briefs_fts(title, body, benchmark_table) VALUES (?, ?, ?)",
        (brief["title"], brief["body"], brief["benchmark_table"]),
    )


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> int:
    rng = random.Random(SEED)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Idempotent: drop the existing file so a fresh deterministic build runs.
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)

        year = 2026
        count = 0
        for sector, subsectors in SECTORS.items():
            region = REGION_FOR_SECTOR[sector]
            for subsector in subsectors:
                n = rng.randint(8, 28)
                bench_rows, headline_metric = build_benchmark_table(rng, sector)
                brief = {
                    "id": det_uuid(rng),
                    "sector": sector,
                    "subsector": subsector,
                    "title": assemble_title(rng, subsector, sector, region, year),
                    "body": assemble_body(
                        rng, subsector, sector, region, n, year, bench_rows, headline_metric
                    ),
                    "benchmark_table": json.dumps(bench_rows),
                    "sample_size": n,
                    "methodology_note": methodology_note(rng, n, year),
                    "last_updated": last_updated_date(rng, year),
                    "confidence_tier": confidence_tier(n),
                }
                insert_brief(conn, brief)
                count += 1

        conn.commit()
        print(f"seed_briefs: wrote {count} briefs to {DB_PATH}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
