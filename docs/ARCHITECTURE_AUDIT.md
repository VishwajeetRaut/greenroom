# Architecture diagram audit

Every component and arrow in `docs/diagrams/architecture.puml`, checked against
the code. Verdicts are evidence-backed — each row names the file that settles
it.

**Most of what this audit originally found has already been fixed upstream.**
The audit was written against the PNG-only diagram that preceded
`architecture.puml`; the regeneration for Judge0, the guardrail/eval split and
telemetry addressed the majority of it independently. What follows is the
current state: two findings that survive (fixed in this change), three that
were overtaken, and one where the audit itself turned out to be wrong.

## Surviving findings — fixed here

### 1. `Groq --fallback--> Ollama` implies a provider-side failover that doesn't exist

Providers do not talk to each other. The backend catches a 429/5xx from Groq
and re-issues the same request against the Ollama-compatible endpoint itself:

```python
# services/llm.py — _ask / opening_message / next_question
except Exception as exc:
    status = getattr(exc, "status_code", None)
    if status is None or status == 429 or status >= 500:
        return _fallback_chat(...)          # the backend calls Ollama
```

Drawn as `groq -down-> ollama`, the diagram says Groq fails over to Ollama on
its own. It doesn't, and someone debugging an outage would look in the wrong
place. Changed to `llmorchestrator -down-> ollama : on 429 / 5xx`.

### 2. `Auth Guard — JWT + dual ownership check` merges two components

The auth guard makes exactly one outbound call, and it goes to Supabase Auth
(GoTrue), not Postgres:

```python
# auth.py
response = supabase.auth.get_user(token)   # GoTrue /auth/v1/user
```

The "dual ownership check" half of that label is a different concern that
touches no database at all — an in-memory comparison against the session dict:

```python
# services/session_guard.py
def check_ownership(session: dict, user: AuthenticatedUser) -> None:
    owner = session.get("user_id")
    if owner and owner != user.id:
        raise HTTPException(status_code=403, ...)
```

What *does* query Postgres is `check_session_limit` — a Session Guard concern.
Labelling one box with both concerns means the diagram draws the union of their
dependencies, which is why the Auth Guard appeared to need the database.
Ownership moved onto the Session Guard label where it belongs.

## Findings already fixed upstream

Recorded so the same ground isn't re-audited:

- **The async code-execution API.** `POST /code/run`, `GET /code/job/{id}` and
  `{job_id}` polling appeared in all three of the old PNG diagrams. No such
  endpoints exist — execution is synchronous via `POST /interview/code/test`.
  The regenerated `architecture.puml` no longer shows them.
- **Analytics/telemetry was missing.** `routers/analytics.py` serves four
  endpoints, two of which the frontend calls. Now present in the diagram.
- **The guardrail and evaluation engines were drawn as one.** Now split, which
  also makes the Azure OpenAI evaluation path visible.

## A finding this audit got wrong

**"Local subprocess execution doesn't exist — remove it."** It does now.
`services/piston.py` has `_local_subprocess`, a last-resort tier that runs code
directly in the backend container when both Judge0 endpoints are unavailable.
The audit was written before that landed; the diagram is correct and the
finding is withdrawn.

## Still worth a look, not changed here

**The browser reads Postgres directly.** `frontend/src/pages/Results.jsx` calls
`supabase.from("sessions")` and `supabase.from("evaluations")` without going
through the backend. The diagram routes every data access through the API,
which makes RLS look like defence-in-depth. On that path it is the *only*
control. Left alone here because adding the edge is a judgement call about how
much detail an HLD should carry, not a factual correction.

## Why the drift happened

The diagrams were PNGs with no source in the repository — an image nobody can
diff is an image nobody reviews, which is how a deleted API survived in three
of them. `docs/diagrams/architecture.puml` fixes that at the root: the diagram
is now text, so it shows up in a pull request like any other stale line.
