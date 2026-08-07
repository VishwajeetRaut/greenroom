# Greenroom — load test and crash mitigation (2026-08-07)

Measured with `backend/scripts/load_test.py` against a local backend. Every
number below was taken before and after the change it describes; nothing here
is projected.

Reproduce:

```bash
cd backend
python -m uvicorn main:app --port 8000 --no-access-log &
python scripts/load_test.py --profile baseline --users 50 --duration 15
python scripts/load_test.py --profile auth --users 30 --duration 12 --token "$JWT"
```

## Measurement caveat, up front

The load generator runs on the same laptop as the server, so they compete for
CPU. **Per-endpoint measurements repeated across runs are stable and are what
this document quotes.** The combined `baseline` profile swung between 415 and
870 rps across identical runs — that spread is the harness, not the server, and
no conclusion here rests on it. Treat all absolute numbers as "this machine,
this day"; the before/after *ratios* are the durable finding.

The `mixed` profile burns real LLM quota (Groq free tier is 100,000 tokens/day,
~600 per interview turn) and was deliberately not run at volume.

## What was found

### 1. `/api/health` collapsed under concurrency — 87 rps, p50 539 ms

The endpoint Azure Container Apps probes for liveness was **37× slower than a
real endpoint** on the same server (`/metrics`: 3,268 rps, p50 14 ms).

A health endpoint that folds under load is worse than useless: it is what tells
the orchestrator to kill a container that was actually fine. Under real traffic
this would have produced restarts that looked like random instability.

Two causes:

- **A fresh `httpx.AsyncClient` per request.** Constructing one builds a TLS
  context. Isolated, the Piston probe took 14 ms; at 50 concurrent it took
  497 ms of wall time.
- **The Piston probe ran on every single call**, so a burst of health checks
  became a burst of outbound requests — monitoring turning into a self-inflicted
  load source.

Fixed with one process-wide client plus a short-lived cached probe result.

| | Before | After |
|---|---|---|
| rps | 87 | **~4,300** |
| p50 | 539 ms | **10 ms** |
| p99 | 986 ms | **29 ms** |

### 2. The fix introduced a cache stampede — caught by the harness

After caching the probe, throughput was up but `load_test.py` flagged a
**p99/p50 tail ratio of 11.9×**. With a plain TTL cache, every concurrent
caller misses at the same instant the entry expires and they all fire a probe
together. The median looked healthy while a slice of requests waited on a
stampede.

`_piston_health` is now single-flight: one refresh at a time, everyone else
served the previous value. For a liveness check, a few seconds stale is fine.

This is the reason the tail-ratio line exists in the script's output — a mean
that looks fine while p99 is 10× it means a minority of candidates are having a
broken interview.

### 3. Auth was a network round-trip per request — and could starve the threadpool

`get_current_user` called `supabase.auth.get_user(token)` on **every request**,
with no caching. Measured: ~312 ms for an authenticated request before any
actual work happened.

The more serious problem was the retry. It used a **blocking `time.sleep(0.3)`**
inside a dependency FastAPI runs in its threadpool — which defaults to 40
workers and is **shared with every `run_in_threadpool` call in the app**: LLM
calls, persistence, question selection.

```
40 workers ÷ 0.3 s hold ≈ 133 failed auths/sec saturates the entire pool
```

At which point in-flight interviews stall behind them. Reaching that is not
exotic: an expired token, a frontend retry loop, or a Supabase blip — and a
blip is precisely when every retry fires at once.

`services/auth_cache.py` caches validated tokens (default 60 s) and failures
(5 s), and the retry pause dropped to 50 ms.

| | Before | After |
|---|---|---|
| rps (401 path) | 93 | **514** |
| p50 | 312 ms | **41 ms** |

The measured improvement is from the *negative* cache — Supabase was
unreachable from the test environment, so every request took the failure path.
In production the success cache is the larger win: one round-trip per token per
minute instead of one per request.

Three properties the cache holds, each with a test:

- **The raw token is never stored** — entries are keyed on a SHA-256. Otherwise
  this dict is a bag of live credentials, readable from any traceback or heap
  dump.
- **An entry never outlives the token's own `exp`.** The claim is read without
  verifying the signature, which is safe *only* because it is used to expire the
  cache **earlier**, never to authorise anything. Supabase remains the sole
  authority.
- **Bounded**, so a token storm can't grow it without limit.

The trade is explicit: a token revoked server-side keeps working for up to
`AUTH_CACHE_TTL_SECONDS`. Set it to `0` to disable.

### 4. The rate limiter did a table-wide write on every request

`check_rate_limit` ran three Supabase round-trips per request: `SELECT count`,
`INSERT`, and an **unscoped `DELETE`** that swept expired rows for *all* users.
That last one fired on every request to every endpoint — a lock-contention
hotspot that bought nothing, since rows only need removing eventually.

Now sampled at 2% (`RATE_LIMIT_PRUNE_PROBABILITY`), which at any real request
rate still fires many times a minute. Random rather than on a timer, so
replicas don't synchronise on the same instant.

### 5. Session locks grew without bound

`_session_locks` was only cleaned by `evict()`, which runs on an explicit
delete. Every session that timed out or was simply abandoned left its lock
behind forever, so a long-lived replica accumulated one per session it had ever
seen. Now bounded — and eviction skips locks that are currently **held**, since
handing a second caller a fresh lock would break the mutual exclusion the lock
exists for.

## Still open

Real risks that were identified but not fixed, because each is a deployment
decision rather than a code change:

- **`minReplicas: 0`** (`infra/backend-container-app.bicep`). The first request
  after an idle period pays a container cold start. For a live demo this is the
  most likely thing to look broken. Set it to 1 beforehand.
- **`maxReplicas: 2` at 0.5 CPU / 1 Gi.** Combined with the in-memory session
  store, a replica restart loses live sessions — already tracked in DESIGN.md
  §8 as the Redis migration.
- **Groq's 100,000 token/day free tier** is the real ceiling on concurrent
  interviews, not CPU. At ~$0.0086 and roughly 8,600 tokens per completed
  session, that is **~11 full interviews per day** before the quota is gone.
  This is the binding constraint for a demo, and it is a billing decision.
- **The evaluation call can take 20 s+** and holds a threadpool worker for the
  duration. The threadpool is shared, so a burst of simultaneous session-ends
  is the remaining starvation risk. Bounding evaluation concurrency with a
  semaphore is the fix if it ever bites.

## Watching it during a demo

`infra/observability/` has the Prometheus + Grafana stack. The panels that
matter live are **p95 latency by route**, **LLM fallback rate** (Groq quota
exhaustion shows up here first), and **evaluations defaulting** (a candidate
finishing an interview and getting a placeholder report).
