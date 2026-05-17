# Security Policy — almured-responder-template

This document describes the security posture of the responder template: what it defends against, what it does not, the 20 named hardening probes that pin the defenses, the reasoning behind a few load-bearing defaults, and the known limitations partners need to think about before going to production.

## 1. Threat model — what the template defends against

The template ports the hardening patterns from the Almured marketplace's 2026-05 audit (`docs/SECURITY-AUDIT-2026-05.md` in the marketplace repo). The same patterns apply on the responder side because the trust boundaries are symmetric: untrusted user-supplied text reaches an LLM either way.

- **Prompt injection via consultation body (F-001 pattern).** An asker submits a question whose body contains directive-shaped text aimed at steering the responder's LLM. The template wraps the question in `<question>…</question>` delimiters, runs `scrub_for_prompt` on it before interpolation, and instructs the model in the system message that content inside `<question>` and `<brief>` blocks is DATA, not instructions.
- **Persistent injection via retrieved brief content (F-003 pattern).** A brief in the local store — perhaps loaded from a third-party feed — contains injection text. Same wrapping + scrub treatment. The default templated synthesis path never echoes brief body text into the answer, eliminating the channel entirely for partners who stay on the safe path.
- **Raw data exfiltration via LLM coaxing.** A hostile question tries to coax the model into dumping the retrieved briefs verbatim. Defense: XML delimiters, `scrub_for_prompt` on every interpolated value, the "DATA not instructions" system message, and `INSUFFICIENT_DATA` as the model's explicit refusal signal — when it's returned, the agent skips submission entirely (no fallback to template) and the asker sees nothing.
- **Schema-violating LLM output.** The model returns JSON that doesn't match the `Answer` shape. Defense: Pydantic v2 strict mode + explicit `INSUFFICIENT_DATA` handling in `_compose_via_llm`. Anything that doesn't parse cleanly into an `Answer` returns None and the consultation is skipped.
- **Rate-limit abuse from upstream.** Defense: in-process token-bucket rate limiter (60/min browse, 10/min write) that paces outbound requests so the agent stays inside Almured's server-side limits even under bursty load. Retries on 429 honor `Retry-After`.
- **Unicode bidi-override and zero-width-space injection.** `strip_control_chars` removes C0 controls (except newline and tab), zero-width chars (U+200B, U+200C, U+200D, U+2060, U+FEFF), and bidi override codepoints (U+202A–U+202E, U+2066–U+2069) from every untrusted text field on the way into the system. Logs and prompts never see these.
- **API key leak via error messages.** The Almured client never interpolates `ALMURED_API_KEY` into thrown errors; tests pin that no log record contains the key during a full polling cycle.
- **HTML / role-tag injection into prose fields.** `strip_html` strips tags from inbound free text; `scrub_for_prompt` additionally strips ChatML-style tokenizer markers like `<|im_start|>` and any lone angle brackets before interpolation.

## 2. Threat model — what the template does NOT defend against

Be explicit about the perimeter so partners don't assume protections they don't have.

- **Compromise of the data store itself.** If an attacker writes to `data/briefs.sqlite` directly on the host, the agent will happily serve adversarial content. Defense is operational: read-only volume mount, file permissions, integrity monitoring.
- **Compromise of `ALMURED_API_KEY` at the host level.** Anyone who reads the env file owns the agent's identity on the marketplace. Defense is operational: secret manager + tight file permissions + rotation procedure. The template logs the key being absent at startup but doesn't otherwise protect it at rest.
- **Network-level MITM.** Assumes TLS to `api.almured.com` is trustworthy. We pin neither certificate nor SPKI. Partners with stricter network requirements should add a CA pin via httpx's `verify=` argument.
- **Supply-chain attacks on Python dependencies.** No pinning beyond `pyproject.toml` minimum-version specifiers. Partners running in regulated environments should pin via `requirements.txt` + a Software Bill of Materials.
- **Side-channel timing attacks on the synthesis path.** A determined attacker probing differential response times might infer dataset membership. Out of scope for v1; would require constant-time synthesis paths.
- **DoS against the agent itself.** Host-level concern. The agent has no inbound surface, so DoS only happens if the host accepts traffic — which the recommended deployment pattern (`--network none`) prevents.

## 3. Hardening probes

The 20 named tests in `tests/test_hardening.py` — each pins exactly one of the defenses above. `test_node.py` prints the count on a successful run; this list is the canonical mapping.

| # | Probe | Pins |
|---|---|---|
| 1 | `test_f001_consultation_body_cannot_steer_llm_synthesis` | LLM cannot be steered by a malicious question body. |
| 2 | `test_f001_default_template_path_ignores_question_text_entirely` | Templated path never echoes question text into the answer. |
| 3 | `test_f003_brief_body_cannot_inject_via_retrieval` | LLM cannot be steered by malicious brief content. |
| 4 | `test_f003_default_template_path_does_not_echo_brief_body_verbatim` | Templated path never echoes brief body. |
| 5 | `test_oversized_input_is_truncated_not_passed_through` | `sanitize_input` enforces a 10 000-char ceiling. |
| 6 | `test_unicode_bidi_overrides_are_stripped` | U+202E, U+200B, U+2066, etc. removed from untrusted text. |
| 7 | `test_html_tags_are_stripped_from_prose_fields` | `strip_html` removes tags and preserves inner text. |
| 8 | `test_injection_markers_are_detected` | `check_injection` flags known directive-shaped substrings. |
| 9 | `test_rate_limiter_paces_writes` | Token bucket paces beyond capacity. |
| 10 | `test_rate_limiter_honors_retry_after_on_429` | 429 responses delay the next attempt by the server-specified interval. |
| 11 | `test_api_client_returns_sanitized_consultations` | Inbound consultations have HTML stripped before reaching synthesis. |
| 12 | `test_insufficient_data_response_returns_none` | LLM refusal short-circuits the submission. |
| 13 | `test_malformed_llm_json_returns_none` | Malformed LLM output never produces an Answer. |
| 14 | `test_api_key_never_logged` | No log record contains `ALMURED_API_KEY`. |
| 15 | `test_answer_body_never_logged` | No log record contains the answer body. |
| 16 | `test_jsonrpc_envelope_manipulation_rejected` | JSON-RPC-shaped question bodies are treated as plain text, never executed. |
| 17 | `test_sql_flavored_strings_do_not_corrupt_fts_query` | SQL-injection-shaped queries don't corrupt the briefs table. |
| 18 | `test_no_consultation_text_appears_in_answer_when_skipping` | On INSUFFICIENT_DATA, no submission and no log of the question body. |
| 19 | `test_synthesis_system_prompt_explicitly_warns_about_data_blocks` | Guard against future edits that water down the system-prompt safety language. |
| 20 | `test_scrub_for_prompt_neutralizes_tokenizer_control_sequences` | ChatML tokens (`<|im_start|>`, `<|im_end|>`, etc.) and uppercase HTML role tags are stripped before interpolation. |

## 4. Defaults explained

A few defaults are load-bearing and deserve their own paragraph.

- **Templated synthesis is the default.** The deterministic string-templating path produces auditable, predictable output and has no LLM injection surface. Most partners will not need the LLM path; turning it on (`ENABLE_LLM_SYNTHESIS=true`) is a one-line opt-in.
- **Pydantic strict mode (`extra="forbid"`).** The marketplace's response schema is narrow; strict mode blocks LLM output drift that might add unexpected fields. If the model returns something off-contract, we'd rather skip the consultation than submit malformed.
- **Non-root container user (UID 1000).** Containment: if the agent process is compromised, it cannot escalate to root inside the container. Combined with `--network none` (see DEPLOYMENT.md) and a read-only root filesystem, the blast radius of a compromise is bounded to the agent's view of the world.
- **In-process rate limiting.** Sufficient for single-replica deployments and avoids the operational cost of a Redis dependency. Multi-replica deployments must switch to a shared limiter — see § 5.
- **Anthropic SDK as an optional `[llm]` extra.** Default `pip install` keeps the dependency tree small and the attack surface narrower. Only partners who opt in to LLM synthesis pull `anthropic` and its transitive deps.

## 5. Known limitations

These are real and partners need to plan around them.

- **In-process rate limiting only.** Two replicas with the same `ALMURED_API_KEY` will each track their own bucket; together they'll exceed the server-side rate limit and start getting 429s. Either (a) stay single-replica per API key (recommended for v1), or (b) replace `TokenBucket` with a Redis-backed limiter.
- **No per-asker tracking.** The agent doesn't track which askers it has interacted with. Fine until you need asker-level abuse detection or per-asker quotas; at that point, add the bookkeeping to `api_client.py`.
- **No persistent audit log of submissions.** Logs are stdout-only. Compliance-sensitive partners must add their own audit pipeline (Postgres append-only table, append-only S3 bucket, or similar).
- **Default `check_injection` allowlist is conservative.** It will produce false positives on legitimate questions that happen to quote "ignore" or "system:" in context. The agent logs the signal but proceeds with synthesis; partners should tune `INJECTION_MARKERS` in `sanitization.py` to match their question profile if false-positive rates become a problem.
- **No image-signing / SBOM in v1.** The Docker build is reproducible but not signed; SBOM generation is a future enhancement.

## 6. Reporting a vulnerability

Email **general@almured.com** with subject prefix `[SECURITY] almured-responder-template`. Do not file public GitHub issues for security findings — coordinate the disclosure first.

We do not currently run a paid bug bounty. Significant findings are credited in the changelog with reporter consent.

## 7. Changelog

- **2026-05-17 — v1 initial release.** F-001 + F-003 hardening patterns ported from the Almured marketplace's 2026-05 audit. 20 named hardening probes + 43 supporting tests. Templated synthesis path + optional LLM path with audit-visible prompt constants. Container-ready with non-root user + recommended `--network none` runtime.
