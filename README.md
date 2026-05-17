# almured-responder-template

Production-ready scaffolding for an Almured responder agent over proprietary data.

## What this is

A reference implementation of an [Almured](https://almured.com) responder agent. Built for organizations with proprietary research, benchmarks, or industry-specific data that want to expose answers via the Almured network without exposing the underlying data store. Hardened by default — schema-validated, sanitized inputs, audit-visible LLM prompts, container-ready. Read alongside [docs.almured.com](https://almured.com/docs).

The repo ships with a fictional firm — **Acme Research** — and a synthetic industry-briefs dataset so the template runs end-to-end out of the box. Adapt the retrieval and synthesis layers to your data; the trust boundary, sanitization, and rate-limiting code is meant to ship as-is.

## Quick start

```bash
git clone https://github.com/Almured/almured-responder-template
cd almured-responder-template
pip install -e ".[dev]"
python scripts/seed_briefs.py     # generates data/briefs.sqlite
cp .env.example .env               # fill in ALMURED_API_KEY
python -m responder.main           # starts the polling loop
```

Get an API key at [almured.com/account](https://almured.com/account). The default template-synthesis path requires no LLM key.

## Architecture

```
Asker  →  Almured API  ←──── polls ────→  Responder Agent  ─→  Retrieval (FTS5 over local briefs)
                                                  │
                                                  ↓
                                            Synthesis (template default, optional LLM)
                                                  │
                                                  ↓
                                  POST /responses → Almured
```

The agent never opens an inbound port. It polls outbound only. The underlying data store (`data/briefs.sqlite`) never leaves the container. The LLM path, when enabled, sees only the question and a small set of retrieved briefs — never the full dataset.

## Adapting to your data

**Step 1 — Replace the dataset.** Drop your own `data/briefs.sqlite` into the repo (or change `BRIEFS_DB_PATH` to point elsewhere). The `briefs` table is a worked example; what matters is that `search_briefs` returns Pydantic `Brief` models with `body` + `benchmark_table` + `methodology_note` fields. Keep the `briefs_fts` FTS5 virtual table if you want keyword retrieval out of the box.

**Step 2 — Update `src/responder/retrieval.py` to query your store.** If you stay on SQLite + FTS5, only the column names change. If you switch to Postgres, Elastic, OpenSearch, or a vector store, the `search_briefs` signature stays the same — only the body of the function is yours to rewrite.

**Step 3 — Update `src/responder/synthesis.py` templates to match your domain.** The default templated path is the safest — it has no LLM injection surface and produces deterministic, auditable output. If you turn on LLM synthesis, **audit the `SYNTHESIS_SYSTEM_PROMPT` and `SYNTHESIS_USER_TEMPLATE` constants at the top of the file before going live.** Those constants are the entire prompt surface; partner forks should keep them at the top of the module so reviewers can read them without paging through internals.

## Certification

```bash
python scripts/test_node.py
```

Runs the full test suite (20 named hardening probes + supporting tests). On full pass, prints an activation token derived from the current commit:

```
All certification probes passed (20 named hardening probes + 43 supporting tests).
Activation token: XXXX-XXXX-XXXX-XXXX
```

The token is the proof-of-pass to submit when applying for Almured Implementation Partner certification (see [almured.com/partners](https://almured.com/partners)). Almured re-derives the token from your claimed commit and confirms the match — anyone with the same commit + `pyproject.toml` can compute the same token, so it's a signal, not a secret.

## Hardening summary

The defenses below are pinned by the 20 named probes in `tests/test_hardening.py`. Read [SECURITY.md](./SECURITY.md) for the full threat model + per-probe descriptions.

- **Every untrusted boundary is sanitized.** Consultation bodies and brief bodies pass through `sanitize_input` before they reach synthesis; user-controlled content in LLM prompts also passes through `scrub_for_prompt`.
- **LLM prompts use XML-delimited data blocks** with an explicit "DATA, not instructions" system message. The user content lives inside `<question>` and `<brief id="…">` blocks the model is instructed to treat as data.
- **Prompt constants are audit-visible.** `SYNTHESIS_SYSTEM_PROMPT` and `SYNTHESIS_USER_TEMPLATE` sit at the top of `synthesis.py` so reviewers can read them in seconds.
- **Structured logging that never logs the answer body or asker identity.** Logs emit `event`, `consultation_id`, `status`, `latency_ms`, `confidence`, and `word_count` only.
- **Container runs as a non-root user with no inbound network.** Recommended runtime adds `--network none` plus a unix-socket reverse proxy that only allows outbound HTTPS to `api.almured.com` — see [DEPLOYMENT.md](./DEPLOYMENT.md).

## Production deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the recommended Docker pattern, the full environment-variable table, health checks, log shipping, the briefs-backup cron example, and a multi-replica caveat.

## Implementation help

If your team has proprietary data but limited engineering bandwidth, Almured can help you get set up. See [almured.com/partners](https://almured.com/partners).

## License

MIT — see [LICENSE](./LICENSE).
