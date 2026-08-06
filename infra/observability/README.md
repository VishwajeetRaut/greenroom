# Greenroom observability stack

Prometheus + Grafana for metrics, Loki + Promtail for logs. Everything is
provisioned from files in this directory, so a fresh `up` comes with the
datasources and dashboard already loaded — nothing to click.

```bash
cd infra/observability
docker compose up -d

# then run the backend with its logs going where Promtail can see them
cd ../../backend
python -m uvicorn main:app --port 8000 --no-access-log \
  | tee ../infra/observability/logs/greenroom.log
```

| | URL | Notes |
|---|---|---|
| Grafana | http://localhost:3001 | `admin` / `admin`. Dashboard: **Greenroom → Overview** |
| Prometheus | http://localhost:9090 | Targets, and the alert rules under Alerts |
| Loki | http://localhost:3100 | Queried through Grafana; no UI of its own |

Grafana defaults to **3001**, not its usual 3000, because a dev frontend
commonly already holds 3000. Override any of them:

```bash
GRAFANA_PORT=3005 PROMETHEUS_PORT=9091 docker compose up -d
```

## What this closes

`docs/EVALUATION_METRICS.md` §6 and §7 list five things as *not yet
measurable*. Token accounting closed "cost per completed session". This stack
closes the other four — all of which failed for the same reason: the data
existed, but only as a line of JSON on stdout that nothing aggregated.

| Gap | Was | Now |
|---|---|---|
| P50/P95 latency | "logs latency per request to stdout only; nothing is persisted anywhere aggregatable" | `greenroom_http_request_duration_seconds` |
| LLM fallback rate | "not currently logged as a countable event" | `greenroom_llm_calls_total{provider}`, `greenroom_llm_fallback_total` |
| Piston vs Wandbox split | "logged per-request but not aggregated anywhere queryable" | `greenroom_sandbox_runs_total{backend}` |
| Guardrail trigger rate | "needs a logged trigger event, which doesn't exist yet" | `greenroom_guardrail_checks_total{layer,result}` |

## What the alerts are actually for

Every rule in `alerts.yml` names a failure that is **invisible to the
candidate but ruins their session**. That distinction is the whole reason this
stack exists — a candidate who gets a placeholder evaluation, or a canned
guardrail fallback question, sees a working product and a worse interview.

- **EvaluationsDefaulting** — both LLM providers failed and the candidate got
  "Could not generate a detailed report" after finishing a whole interview.
  The worst outcome in the app, and completely silent.
- **LLMFallbackSustained** — Groq is 429ing or 5xxing. The free tier is
  100,000 tokens/day and a single long evaluation used to consume 55% of it.
- **SandboxOnWandbox** — Piston is unreachable and candidate code is running
  on a public third party. This has happened for a full week of local testing
  without anyone noticing (EVALUATION_METRICS.md §7).
- **SandboxFullyDown** — Run Tests is broken.
- **GuardrailFallingBack** — the model leaked an answer *and* failed to correct
  itself, so the candidate got a canned question instead of a real follow-up.
- **HighErrorRate**, **SlowInterviewTurns**, **BackendDown** — the ordinary
  ones.

Alerts currently evaluate in Prometheus and show up in its UI. Routing them to
email or Slack needs an Alertmanager container — not included, because where
they should go is a decision, not a default.

## Cardinality

The one way a metrics layer takes down the service it monitors is unbounded
label cardinality. Two rules, both enforced by tests rather than by good
intentions:

- HTTP paths are recorded as the **route template**
  (`/api/interview/{session_id}/resume`), never the raw path. Unmatched
  requests collapse to `unmatched` rather than the raw URL — a 404 flood is
  exactly when you least want to mint a series per URL.
- In Loki, only `level` and `event` are promoted to labels. Both are closed
  sets. `session_id`, `user_id` and `latency_ms` stay in the log body, where
  they're queryable but not indexed.

## Privacy

`/metrics` is unauthenticated by design — Prometheus scrapers don't carry
bearer tokens — and exposes only aggregate counters. No prompts, transcripts,
or user identifiers ever become label values, and a test asserts it. If the
endpoint is ever reachable from outside the VNet, restrict it at the ingress
rather than adding auth here.

Loki, by contrast, holds real log bodies. Keep it internal.

## Production

This compose stack is for local and demo use. In Azure, the backend's stdout
already flows into Log Analytics via the Container Apps environment
(`ContainerAppConsoleLogs_CL`) — see `infra/monitoring.bicep`, which is written
but not yet deployed. The `/metrics` endpoint works the same way there; point a
managed Prometheus (Azure Monitor workspace) or a self-hosted one at it.
