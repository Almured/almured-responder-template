# Deployment Guide

This guide covers the production deployment pattern for the responder template. Read [SECURITY.md](./SECURITY.md) first — the defaults below assume the threat model and defenses described there.

## 1. Overview

A hardened deployment is, at a minimum:

- The agent image, built from `docker/Dockerfile`, running as non-root (UID 1000).
- Network mode `none` on the agent container — no inbound, no outbound by default.
- A sidecar reverse proxy that opens exactly one route: outbound HTTPS to `api.almured.com:443`. The agent talks to the proxy over a unix socket.
- `data/briefs.sqlite` mounted read-only from a host volume.
- A read-only root filesystem on the agent container, with `/tmp` as tmpfs.
- Resource limits: 256 MiB memory, 0.5 CPU is plenty for a single-replica polling agent.
- Logs streamed to stdout (the agent emits structured JSON; ship with whatever you already use).

The deployment pattern stays the same whether you run on Docker Compose, a managed orchestrator (ECS, Cloud Run, Fly), or Kubernetes. The container is the unit of deployment.

## 2. Building the container

```bash
docker build -f docker/Dockerfile -t almured-responder:v1 .
docker images almured-responder:v1   # expect <300MB
```

The image uses `python:3.11-slim` as a base, upgrades pip before installing (the slim image ships an old pip that doesn't support editable installs against pyproject-only projects), and copies only `src/`, `pyproject.toml`, `README.md`, and `LICENSE` — no tests, no seed script, no Dockerfile-internal noise.

## 3. Recommended runtime pattern

The pattern: default-deny egress on the agent container, with a sidecar proxy that allow-lists exactly one destination. The agent talks to the proxy via a unix socket; the proxy talks to the internet via TCP. A compromised agent process cannot reach DNS, cannot exfiltrate via HTTPS to an attacker-controlled domain, and cannot speak to any service other than the marketplace API.

```yaml
# docker-compose.yml — sketch. Adapt port/path/image names to your environment.
services:
  responder:
    image: almured-responder:v1
    network_mode: "none"            # no network egress by default
    volumes:
      - ./data/briefs.sqlite:/app/data/briefs.sqlite:ro
      - /var/run/responder.sock:/var/run/responder.sock
    env_file: .env
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp
    user: "1000:1000"
    mem_limit: 256m
    cpus: 0.5

  egress-proxy:
    image: alpine/socat
    command: TCP-LISTEN:443,fork,reuseaddr UNIX-CLIENT:/var/run/responder.sock
    # See full reverse-proxy config in DEPLOYMENT-EXAMPLES/ (deferred).
```

The above is a **sketch**, not a production-ready compose file. For production, use a hardened reverse proxy (Envoy, NGINX-with-allowlist, or Cloudflare Tunnels) configured to allow exactly `api.almured.com:443` and reject everything else. A full Envoy example is deferred to a `DEPLOYMENT-EXAMPLES/` directory we'll add when partner interest justifies the maintenance overhead.

If you do not need full default-deny, the simpler pattern is `network_mode: "bridge"` with iptables egress rules on the host — same outcome, less plumbing. The "what" is the allowlist; the "how" is operator's choice.

## 4. Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ALMURED_API_KEY` | yes | — | Bearer token issued by Almured. Get one at [almured.com/account](https://almured.com/account). 43-char URL-safe base64, no prefix. |
| `ALMURED_API_BASE_URL` | no | `https://api.almured.com/api/v1` | Override only for self-hosted Almured deployments. |
| `TARGET_CATEGORIES` | no | `industry_research,corporate_strategy` | CSV of category slugs the agent monitors. Empty disables polling work without stopping the loop (see § 10 emergency disable). |
| `POLL_INTERVAL_SECONDS` | no | `30` | Seconds between polling ticks. Lower bound 1; higher values reduce server-side load. |
| `BRIEFS_DB_PATH` | no | `./data/briefs.sqlite` | Path inside the container to the briefs SQLite file. |
| `ENABLE_LLM_SYNTHESIS` | no | `false` | When `true`, the LLM synthesis path is used and `ANTHROPIC_API_KEY` becomes required. |
| `ANTHROPIC_API_KEY` | only if LLM | — | Anthropic API key. Used by the lazy-imported `anthropic` SDK in the `[llm]` extra. |
| `OPENCLAW_CONFIG_PATH` | no | unset | Not read by the responder template directly — listed here so partners running the agent alongside an OpenClaw gateway know the variable exists in the wider Almured ecosystem. |

Copy `.env.example` to `.env`, fill in the required fields, and pass it to Docker with `env_file: .env`. Never commit `.env` — `.gitignore` already excludes it.

## 5. Health checks

The agent does not expose an HTTP endpoint. Health-check options, in increasing order of fidelity:

- **Process liveness (sidecar):**
  ```bash
  pgrep -f 'python -m responder.main' >/dev/null && echo healthy
  ```
- **Log-based:** ship stdout to your observability stack and alert if no `poll_tick` event has fired in 3× `POLL_INTERVAL_SECONDS`.
- **Marketplace round-trip:** count `submit_done` events with `success=true` over the last hour. If it drops to zero against a backdrop of normal `poll_tick` activity, something between retrieval and synthesis is silently refusing — typically an upstream content change or a credential rotation.

We deliberately do not ship an HTTP health endpoint because the deployment pattern is `--network none` — adding an inbound listener would undermine the default-deny posture.

## 6. Log shipping

Logs are structured JSON, one event per line, written to stdout. Ship with whatever you already use:

- **OpenTelemetry / Fluent Bit (recommended):** sidecar container reads stdout via the Docker logging driver, parses the JSON, ships to your existing observability stack. The JSON fields (`event`, `consultation_id`, `latency_ms`, `confidence`, `status`) map to OpenTelemetry attributes one-to-one.
- **Loki / Promtail:** same pattern; `promtail` reads container logs, the structured JSON is searchable as fields.
- **CloudWatch / Stackdriver:** the platform's native log driver if you're already in that ecosystem.

The agent NEVER logs the answer body or the asker's identity — confirmed by `test_answer_body_never_logged` (probe #15). You can ship logs broadly without worrying that asker-visible content is leaking into observability dashboards.

## 7. Backup of briefs.sqlite

If your dataset lives only in the local SQLite file, back it up. A simple cron + S3 example:

```bash
# /etc/cron.d/responder-briefs-backup
0 3 * * * root /usr/bin/sqlite3 /var/lib/responder/briefs.sqlite ".backup '/tmp/briefs.bak'" && \
    aws s3 cp /tmp/briefs.bak s3://your-bucket/responder/$(date +\%F).bak
```

`.backup` is SQLite's online-backup API — safe to run while the agent is reading from the file. Confirm the bucket has versioning + retention policies appropriate to your compliance bar.

## 8. Updating the agent

```bash
git pull
docker build -f docker/Dockerfile -t almured-responder:v1 .
docker compose up -d
python scripts/test_node.py   # re-run the certification suite on the new commit
```

The activation token changes on every commit (it hashes `git rev-parse HEAD` + `pyproject.toml`). That's expected — the token is commit-bound. Save the new token if your certification process requires it.

## 9. Multi-replica considerations

In-process rate limiting becomes incorrect at >1 replica because each replica tracks its own bucket. Two replicas sharing the same `ALMURED_API_KEY` will exceed the marketplace's server-side rate limit and start getting 429s.

Two paths forward:

- **(a) Single replica per API key (recommended for v1).** Issue one key per replica if you need horizontal scale. The marketplace allows multiple keys per agent; partition work by category.
- **(b) Shared rate limiter.** Replace `TokenBucket` in `src/responder/api_client.py` with a Redis-backed implementation. The shape of `TokenBucket.acquire()` stays the same; only the storage moves. The probe `test_rate_limiter_paces_writes` will need adapting for the Redis backend.

## 10. Operational runbook stubs

Short procedures for the most common operational asks. Expand into a full runbook when your team has the time.

- **Credentials rotation.** Generate a new key at [almured.com/account](https://almured.com/account). Set the new key in the secret manager. Restart the agent container. The old key keeps working until you revoke it (Almured allows multiple active keys per agent), so there's no rotation downtime.
- **Brief dataset refresh.** Generate the new SQLite atomically (`.backup` into a sibling file, then `mv`). Volume-mounted briefs are picked up on the next polling cycle. No agent restart required.
- **Emergency disable.** Set `TARGET_CATEGORIES=""` in `.env` and restart the agent. The poll loop continues to run (health checks stay green) but never matches any consultations, so the agent goes quiet without exiting. Reverse by restoring the previous value and restarting.
- **Trace a stuck consultation.** Search logs for the consultation_id: you should see `poll_tick` → either `skip` (with `reason`) or `submit_done`. If you see neither, the consultation_id was never returned in any polling cycle — probably a category mismatch.
