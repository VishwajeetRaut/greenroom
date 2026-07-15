# Action Items

Living tracker for open work across the project. Update this file (or link out
to it) whenever an item is picked up, finished, or dropped — keep it in sync
with actual PR/issue state rather than letting it drift.

For day-to-day task assignment and status, use a proper tracker instead of
editing this file constantly — **GitHub Projects** (free, built into this repo,
no new tool to onboard people onto) is the recommended default. This file
stays as the durable, versioned summary of *what* the items are and *why*.

Sourced from a full audit of every version of the design doc in
`design-doc-history/` (9 versions, 2026-06-17 through today) cross-checked
against current code — not just recent PR activity — so this reflects the
project's full lifecycle, not a snapshot of the last few days.

## Project Timeline

What actually shipped, version by version, per the design doc's own
"Built"/"Scope" tables at each point in time:

| Version | Date | What shipped since the last version |
|---|---|---|
| v1.0 POC | 2026-06-17 | Behavioral + Technical tracks, LangChain LCEL agent, Pydantic-validated evaluation, Groq→Ollama Cloud fallback, Supabase auth/session history. Piston code execution flagged as unreliable (public API rate-limited). System Design track, question bank, seniority/role selectors, and benchmarking all still planned. |
| v2.0 | 2026-06-24 | Deployed to Azure Container Apps (Sweden Central) with full CI/CD via GitHub Actions. Self-hosted Piston + Wandbox fallback (fixed the v1.0 reliability problem). Dynamic test runner (LLM generates test *data*, not code — a harness template we control runs it). Four-layer guardrail against answer leaks. System Design track (Excalidraw canvas) built. |
| v3.0 ("industry-grade" rewrite) | 2026-06-30 | Question bank grew to 210 verified LeetCode problems (sandboxed dual-solution verification before import). Dynamic interviewer (`question_generator.py`) — decides bank vs. generated question per session. Automated test suite and structured logging identified as new gaps (not yet built). |
| 2026-07-01 (2 revisions) | 2026-07-01 | Question bank grew again: 210 LeetCode + 77 CodeContests (DeepMind) + 8 hand-written = 295 technical questions, all test-verified pre-import. |
| v4.0 | 2026-07-08 (`e52a88f`) | Question bank reached its current 357 (295 technical + 42 behavioral + 20 system-design). Postgres-backed sliding-window rate limiter, session concurrency cap (max 3) + idle timeout, async code-execution job queue, system-design diagram scoring, structured logging, pytest+Vitest CI suite — all landed. JD upload for personalized question selection shipped (`Dashboard.jsx`). This version also recorded specific reviewer feedback on concurrency/scalability/rate-limiting that was never resolved — see **Performance and Scalability** below. |
| v4.0 (current) | 2026-07-08 (`92b5fa3`) | Consolidated into today's `DESIGN.md`. Seniority/role differentiation and human-rater benchmarking formally reclassified as **Non-Goals** rather than left as stale "planned" items (see below). |
| Since | 2026-07-08 → today | Boilerplate/reset-button fix, evaluation self-critique pass, usage analytics, CI path-filtering + mypy/tsc gates, real deploy automation, design-doc history archive — tracked in the sections below. |

## Non-Goals (explicit scope decisions, not dropped work)

The v1.0 POC roadmap originally planned these; all three were later moved
into `DESIGN.md`'s explicit **Non-Goals** (§1.3) rather than silently
dropped — confirmed against current code (no seniority field, no benchmark
artifacts anywhere in the repo):

- Seniority level differentiation (Entry / Senior)
- Role-specific question sets beyond Software Engineer (PM, Data Science, DevOps)
- Evaluation accuracy benchmarking against human raters

## Code and Product

| Item | Status | Notes |
|---|---|---|
| Fix boilerplate generation (function signature, not full `main`) | Done | [PR #12](https://github.com/VishwajeetRaut/greenroom/pull/12) |
| Smooth question-answering flow (interviewer/candidate turn friction) | Done | [PR #12](https://github.com/VishwajeetRaut/greenroom/pull/12) (superseded [PR #11](https://github.com/VishwajeetRaut/greenroom/pull/11)) |
| Reset button in code editor to restore original boilerplate | Done | [PR #12](https://github.com/VishwajeetRaut/greenroom/pull/12) |
| Self-improvement loop for evaluation engine (LLM critiques/refines its own scoring) | Done | [PR #18](https://github.com/VishwajeetRaut/greenroom/pull/18) — reviewer pass in `backend/services/llm.py::evaluate_session` |

## Performance and Scalability

Specific, still-unresolved feedback recorded in the v4.0 design doc
(`e52a88f`, §1.5 "Feedback & Open Issues") — none of these were ever
declared out of scope, and none are implemented in current code (checked):

| Item | Status | Notes |
|---|---|---|
| Move session state off in-memory `SESSIONS` dict to Redis | Not started | With 2+ backend replicas, a session started on one replica 404s if routed to another. Sticky sessions are the interim mitigation (in place); Redis is the real fix. `session_store.py` is already isolated so this is a single-file change. |
| Stream LLM responses (SSE) | Not started | Every `/message` call currently blocks on a full Groq round-trip (~1-3s). Fine at low concurrency, queues under load. Fix identified: `ChatGroq(..., streaming=True)` + Server-Sent Events on the frontend. |
| Scale Piston beyond 1 replica | Not started | Piston is currently a single container (max 1.0 vCPU per `DESIGN.md`'s Container Resources table) — code execution queues under concurrent load. |
| Batch/pool Supabase connections | Not started | Free tier's 2 connections/second ceiling gets hit quickly since the rate limiter inserts one row per request. |
| Session-level (not just user-level) rate limiting | Not started | A user with 3 concurrent active sessions can currently triple their effective rate limit, since limiting is per-user-per-endpoint, not counted against a session-aware budget. |
| Move rate-limit prune to a background task | Not started | `rate_limit.py` currently prunes old rows synchronously on every check (`sb.table("rate_limit_events").delete()...`), adding latency to the hot path. Should move to FastAPI `BackgroundTasks`. |
| `GET /api/rate-limit/status` endpoint | Not started | So the frontend can show candidates how many requests they have left, instead of only finding out via a 429. |

## Usage and Monitoring

| Item | Status | Notes |
|---|---|---|
| Track real user counts and click activity (usage spikes / drop-off) | Done | [PR #19](https://github.com/VishwajeetRaut/greenroom/pull/19) — `analytics_events` table + `POST /api/analytics/event` |
| Explore Microsoft/Azure monitoring tools for observability | Not started | candidates: Azure Application Insights, Azure Monitor / Log Analytics (backend already emits JSON logs structured for Log Analytics ingestion, see `backend/services/logger.py`) |
| Error tracking (Sentry) | Not started | Was in the v1.0 POC roadmap (Week 7, alongside structured logging) and still listed as "Planned" as late as the v4.0 doc (`e52a88f`). Structured JSON logging shipped; Sentry never did, and it isn't a declared Non-Goal. Genuine, long-standing gap. |

## Process and Collaboration

| Item | Status | Notes |
|---|---|---|
| Open more PRs, actively invite teammates to review | Ongoing | team habit, not a code task |
| Improve CI/CD pipeline efficiency | Done | [PR #21](https://github.com/VishwajeetRaut/greenroom/pull/21) — path-filtered CI jobs, mypy/tsc gates, real `az containerapp update` deploy step (previously build-only), CI-gated deploy, post-deploy smoke test |
| CI checks required before merge | Not started | `main` currently has **no branch protection at all** (`gh api repos/.../branches/main/protection` → 404) — CI runs but nothing blocks a merge if it fails. The v1.0 POC roadmap's "wire tests as required CI gate before deploy" item was never actually completed — it just stopped being mentioned once the test suites themselves (pytest/Vitest) were added in v4.0. Those two are different things. |

## Stress Testing

Results and changes to support higher load (including component tests for the
evaluation engine, guardrails engine, and dynamic interviewer across all three
tracks) were completed by the team — see stress-test results shared
separately rather than duplicated here.

## Documentation

| Item | Status | Notes |
|---|---|---|
| Maintain all versions of design docs in the repo | Done | [PR #20](https://github.com/VishwajeetRaut/greenroom/pull/20) — `design-doc-history/`, all 9 unique versions from git history (deduped by content), including the diagrams each version embedded, plus the live `DESIGN.md` + `docs/diagrams/` |
| Shared doc for tracking action items | Done | this file |
| Project tracker (free resource) | Recommended | GitHub Projects (free, native to this repo) |
| Encourage broader team contributions / peer-reviewed PRs | Ongoing | team habit, not a code task |
