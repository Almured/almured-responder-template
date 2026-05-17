# almured-responder-template

Reference template for building an [Almured](https://almured.com) responder
agent over proprietary data. This repo wraps a fictional research firm —
**Acme Research** — and exposes its synthetic industry-briefs dataset as an
Almured-compatible MCP server.

## Quick start

```bash
pip install -e .
python scripts/seed_briefs.py
```

The seed script writes `data/briefs.sqlite` with 50 deterministic synthetic
briefs across 5 sectors. Rerunning is idempotent.

> More documentation lands in W5d. The responder agent itself (W5b), the
> Docker packaging (W5c), and the test suite (W5c) are not in this commit.
