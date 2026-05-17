"""Environment-driven configuration.

Loads .env via python-dotenv on import. Raises if required env vars are
missing — fail-fast at startup rather than NoneType errors later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # python-dotenv is a hard requirement in pyproject.toml, but if the
    # environment is otherwise valid we don't want a missing .env to be
    # a fatal import error.
    pass


@dataclass(frozen=True)
class Config:
    almured_api_key: str
    almured_api_base_url: str
    target_categories: tuple[str, ...]
    poll_interval_seconds: int
    briefs_db_path: str
    enable_llm_synthesis: bool


def _as_bool(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    api_key = os.environ.get("ALMURED_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ALMURED_API_KEY is required. Set it in the environment or in a "
            ".env file alongside this repo. Get a key at https://almured.com/account."
        )

    base_url = os.environ.get(
        "ALMURED_API_BASE_URL", "https://api.almured.com/api/v1"
    ).rstrip("/")

    raw_cats = os.environ.get(
        "TARGET_CATEGORIES", "industry_research,corporate_strategy"
    )
    categories = tuple(c.strip() for c in raw_cats.split(",") if c.strip())
    if not categories:
        raise RuntimeError(
            "TARGET_CATEGORIES is empty after parsing — provide at least one category."
        )

    try:
        poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
    except ValueError as exc:
        raise RuntimeError(
            f"POLL_INTERVAL_SECONDS must be an integer, got: {os.environ.get('POLL_INTERVAL_SECONDS')!r}"
        ) from exc
    if poll_interval < 1:
        raise RuntimeError("POLL_INTERVAL_SECONDS must be >= 1.")

    db_path = os.environ.get("BRIEFS_DB_PATH", "./data/briefs.sqlite")

    enable_llm = _as_bool(os.environ.get("ENABLE_LLM_SYNTHESIS"))

    return Config(
        almured_api_key=api_key,
        almured_api_base_url=base_url,
        target_categories=categories,
        poll_interval_seconds=poll_interval,
        briefs_db_path=db_path,
        enable_llm_synthesis=enable_llm,
    )
