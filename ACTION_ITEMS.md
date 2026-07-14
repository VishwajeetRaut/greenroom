# Action Items

Living tracker for open work across the project. Update this file (or link out
to it) whenever an item is picked up, finished, or dropped — keep it in sync
with actual PR/issue state rather than letting it drift.

For day-to-day task assignment and status, use a proper tracker instead of
editing this file constantly — **GitHub Projects** (free, built into this repo,
no new tool to onboard people onto) is the recommended default. This file
stays as the durable, versioned summary of *what* the items are and *why*.

## Code and Product

| Item | Status | Notes |
|---|---|---|
| Fix boilerplate generation (function signature, not full `main`) | In progress | [PR #12](https://github.com/VishwajeetRaut/greenroom/pull/12), [PR #11](https://github.com/VishwajeetRaut/greenroom/pull/11) |
| Smooth question-answering flow (interviewer/candidate turn friction) | In progress | [PR #11](https://github.com/VishwajeetRaut/greenroom/pull/11) |
| Reset button in code editor to restore original boilerplate | In progress | [PR #12](https://github.com/VishwajeetRaut/greenroom/pull/12) |
| Self-improvement loop for evaluation engine (LLM critiques/refines its own scoring) | In progress | [PR #18](https://github.com/VishwajeetRaut/greenroom/pull/18) — reviewer pass in `backend/services/llm.py::evaluate_session` |

## Usage and Monitoring

| Item | Status | Notes |
|---|---|---|
| Track real user counts and click activity (usage spikes / drop-off) | In progress | [PR #19](https://github.com/VishwajeetRaut/greenroom/pull/19) — `analytics_events` table + `POST /api/analytics/event` |
| Explore Microsoft/Azure monitoring tools for observability | Not started | candidates: Azure Application Insights, Azure Monitor / Log Analytics (backend already emits JSON logs structured for Log Analytics ingestion, see `backend/services/logger.py`) |
| Error tracking (Sentry) | Not started | was in the original v1.0 POC roadmap's "Next Steps & Owners" table (Week 7, alongside structured logging) — structured JSON logging shipped, Sentry never did, and it isn't listed as a Non-Goal anywhere. Genuine gap, not a dropped-on-purpose item. |

## Process and Collaboration

| Item | Status | Notes |
|---|---|---|
| Open more PRs, actively invite teammates to review | Ongoing | team habit, not a code task |
| Improve CI/CD pipeline efficiency | In progress | [PR #16](https://github.com/VishwajeetRaut/greenroom/pull/16) — path-filtered CI jobs |
| CI checks required before merge | Not started | `main` currently has **no branch protection at all** (`gh api repos/.../branches/main/protection` → 404) — CI runs but nothing blocks a merge if it fails. The v1.0 POC roadmap's "wire tests as required CI gate before deploy" item was never actually completed, it just stopped being mentioned once the test suites themselves (pytest/Vitest) were added. Those two are different things. |

## Stress Testing

Results and changes to support higher load (including component tests for the
evaluation engine, guardrails engine, and dynamic interviewer across all three
tracks) were completed by the team — see stress-test results shared
separately rather than duplicated here.

## Documentation

| Item | Status | Notes |
|---|---|---|
| Maintain all versions of design docs in the repo | Done | [PR #20](https://github.com/VishwajeetRaut/greenroom/pull/20) — `design-doc-history/`, all 9 unique versions from git history (deduped by content) plus the live `DESIGN.md` + `docs/diagrams/` |
| Shared doc for tracking action items | Done | this file |
| Project tracker (free resource) | Recommended | GitHub Projects (free, native to this repo) |
| Encourage broader team contributions / peer-reviewed PRs | Ongoing | team habit, not a code task |

### Findings from auditing all 9 design-doc versions

Diffed every version in `design-doc-history/` against current code to check
whether anything from earlier plans got silently lost. Two categories:

**Deliberately dropped — no action needed.** The v1.0 POC roadmap's "Next
Steps & Owners" table (present through the `f3dd313` version, removed in
`8511c88` — "Remove a redundant section from the design doc") listed
seniority/role differentiation, persona parameterization by level, and
evaluation-accuracy benchmarking against human raters. All three now appear
explicitly under **Non-Goals** (§1.3) in the current `DESIGN.md` — a real
scope decision, not something that fell through the cracks.

**Actually still open — added above.** Two items from that same removed
table were never finished and were never declared out of scope either:
Sentry/error-tracking, and CI checks being a *required* merge gate (as
opposed to just existing and running). Both added to their respective
sections above.
