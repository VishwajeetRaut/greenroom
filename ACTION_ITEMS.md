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
| Self-improvement loop for evaluation engine (LLM critiques/refines its own scoring) | In progress | see `backend/services/llm.py::evaluate_session` |

## Usage and Monitoring

| Item | Status | Notes |
|---|---|---|
| Track real user counts and click activity (usage spikes / drop-off) | In progress | minimal event-tracking scaffold being added |
| Explore Microsoft/Azure monitoring tools for observability | Not started | candidates: Azure Application Insights, Azure Monitor / Log Analytics (backend already emits JSON logs structured for Log Analytics ingestion, see `backend/services/logger.py`) |

## Process and Collaboration

| Item | Status | Notes |
|---|---|---|
| Open more PRs, actively invite teammates to review | Ongoing | team habit, not a code task |
| Improve CI/CD pipeline efficiency | In progress | [PR #16](https://github.com/VishwajeetRaut/greenroom/pull/16) — path-filtered CI jobs |

## Stress Testing

Results and changes to support higher load (including component tests for the
evaluation engine, guardrails engine, and dynamic interviewer across all three
tracks) were completed by the team — see stress-test results shared
separately rather than duplicated here.

## Documentation

| Item | Status | Notes |
|---|---|---|
| Maintain all versions of design docs in the repo | Done | `DESIGN.md` + `docs/diagrams/`, versioned via normal PRs |
| Shared doc for tracking action items | Done | this file |
| Project tracker (free resource) | Recommended | GitHub Projects (free, native to this repo) |
| Encourage broader team contributions / peer-reviewed PRs | Ongoing | team habit, not a code task |
