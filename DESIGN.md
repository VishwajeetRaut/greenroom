# Greenroom — Dev Design Document

**Team:** Vishwajeet, Geet, Anurag, Nithin, Mahati, Yuang
**Version:** 3.0 · June 2026
**Live app:** https://greenroom-frontend.orangeground-05e56063.swedencentral.azurecontainerapps.io

---

## 1. Dev Design Document

### 1.1 Problem Statement & Goals

Students and early-career candidates have no free, realistic way to practice interviews with structured feedback. Existing options all fall short in different ways:

- **Human mock interviews** — hard to schedule, inconsistent scoring, often cost money
- **Static Q&A tools** — no adaptive follow-up, no voice, no live coding
- **General AI chatbots** — no interview structure, no scoring rubric, no STAR evaluation

Greenroom gives candidates a full interview experience: an AI that asks real questions, adapts to their answers, speaks out loud, runs their code, and gives a scored evaluation at the end — all for free.

| Goal | How we know we've hit it |
|---|---|
| Realistic AI-driven interview | Candidate completes a full session end-to-end: voice, follow-ups, scored report |
| STAR-based evaluation | STAR scores, per-dimension feedback, and improvement points on every session |
| Three interview tracks | Behavioral, Technical (live code execution), System Design (live canvas) |
| Multiple seniority levels and roles | Entry / Senior with SWE, PM, Data Science role-specific sets (planned) |
| Prove evaluation accuracy | Scores benchmarked against human raters — target Pearson r > 0.80 (planned) |
| Free infrastructure | Zero cost on Azure for Students credits |

---

### 1.2 Solutions Overview

Greenroom is a three-service web application. The candidate interacts through a browser. The backend handles all intelligence — LLM calls, code execution, evaluation, and session management. Supabase stores everything.

#### System Architecture

![System Architecture](docs/diagrams/architecture.png)

> **Color guide:** Green = primary/public-facing · Red = internal-only or fallback · Purple = CI/CD · Yellow = data layer

**How a typical session works, step by step:**

1. Candidate opens the app in Chrome or Edge and logs in via email/password. Supabase handles all authentication using a PKCE flow — no passwords touch our backend code.
2. They pick a track (Behavioral, Technical, or System Design) on the dashboard and click Start.
3. The frontend sends `POST /api/interview/start` with a Bearer JWT. The backend validates the JWT server-side against Supabase, picks an appropriate question from the question bank (or generates one), inserts a session row in Postgres, and returns an opening message.
4. The interview runs as a loop: the candidate speaks or types a response, the frontend sends `POST /api/interview/message`, the backend runs it through the LLM (Groq primary, Ollama Cloud fallback), filters the response through the guardrail to prevent answer leaks, and returns the interviewer's next question as text. The frontend reads it aloud via the TTS endpoint.
5. For the Technical track, the candidate writes code in a Monaco editor. Clicking "Run Tests" triggers `POST /api/interview/code/test`. The backend generates a test harness (or uses a cached one for Java/C++), runs the candidate's code through Piston (internal, sandboxed), falls through to Wandbox if Piston is unreachable, and returns pass/fail per test case.
6. When the candidate clicks End, `POST /api/interview/end` fetches the full session transcript, sends it to the LLM for evaluation, stores the scores, and returns a structured scorecard.

#### User Flow

![User Flow](docs/diagrams/user-flow.png)

#### Developer Request Flow

![Developer Request Flow](docs/diagrams/developer-flow.png)

---

### 1.3 Scope & Constraints

**Built and deployed:**
- Behavioral interview track — multi-turn STAR-format Q&A with TTS voice
- Technical interview track — Monaco editor, code execution (Python, JS, Java, C++), dynamic test runner, lazy Java/C++ harness generation
- System Design track — Excalidraw canvas with LLM feedback on diagram elements
- Session history and scorecard on the dashboard
- Delete session
- Supabase Auth (email/password + PKCE OAuth)
- Guardrail filter (four-layer defense against answer leaks)
- Question bank — 295 verified problems: 210 from LeetCodeDataset (Kaggle, newfacade, MIT, arXiv:2504.14655) imported via `scripts/import_leetcode_dataset.py`, 77 from CodeContests (DeepMind, CC-BY-4.0) imported via `scripts/import_codecontests.py`, and 8 hand-written — every test case verified by running a canonical solution through a sandboxed container before import; structured constraints and examples extracted via `scripts/extract_constraints_examples.py`
- Dynamic interviewer — LLM-driven question selection: `question_generator.py` decides between using an existing bank problem or generating a new one, verifies it with dual-solution sandbox execution before persisting
- Groq → Ollama Cloud LLM fallback
- Self-hosted Piston + Wandbox fallback for code execution
- GitHub Actions CI/CD with OIDC (no stored credentials)

**Planned (not yet built):**
- Seniority levels (Entry / Senior)
- Role selector (SWE, PM, Data Science, DevOps)
- Evaluation accuracy benchmark vs human raters
- Automated test suite (pytest + Vitest)
- Structured logging and observability


**Known constraints:**
- Azure Container Apps free consumption plan does not support `--privileged` Docker mode. Piston's `isolate` sandbox requires it. Wandbox handles code execution as a fallback. Full isolation needs a dedicated D4 workload profile (~$50/month) or swapping to gVisor/nsjail.
- Supabase free tier: 500MB storage, 2 connections/second ceiling.
- Rate limiter is per-process — with 2 replicas, the effective ceiling doubles silently. Fix planned (move to Postgres-backed table).
- Web Speech API (browser speech recognition) only works in Chrome and Edge, and requires HTTPS in production.

---

### 1.4 Key Design Decisions

#### LangChain LCEL chains, not plain API calls

We use LangChain Expression Language for all LLM interactions. Plain API calls are single-turn — you lose conversation history unless you manually rebuild it on every request. LCEL chains inject the full typed history (`AIMessage` / `HumanMessage`) on every call via `MessagesPlaceholder`. `JsonOutputParser` validates the LLM's JSON output against a Pydantic schema at parse time, so malformed JSON is caught before it reaches the UI. Swapping the LLM provider is one line.

| | Plain API call | Greenroom (LCEL) |
|---|---|---|
| Conversation memory | None — single turn only | Full typed history injected automatically |
| Output validation | None | Pydantic schema enforced at parse time |
| Provider swap | Rewrite every call site | One line — `ChatGroq(...)` → `ChatOpenAI(...)` |
| LLM fallback | None | Auto-retry on Ollama Cloud on 429 / 5xx |

#### Self-hosted Piston + Wandbox fallback

The public Piston API now requires authentication (returns 401). We self-hosted Piston as an internal Azure Container App — it has no public internet ingress, only the backend API can reach it. Wandbox is wired as a fallback.

```
Candidate clicks "Run Tests"
  → POST /api/interview/code/test
      → Tier 1: Self-hosted Piston  (internal Container App)
          if unavailable → fall through
      → Tier 2: Wandbox  (free public API, no auth)
          if unavailable → fall through
      → Tier 3: "Temporarily unavailable" message
```

Wandbox responses are normalised to match Piston's response shape. Nothing else in the codebase knows which tier handled the request.

#### Dynamic test runner — two modes

The test runner handles two problem formats, because the question bank has both:

- **call/expected** (LeetCode-style): The LLM is asked only for test *data* (JSON), never for runnable code. We inject that data into a harness template we control. This prevents LLM syntax errors from crashing the runner.
  ```json
  [{"call": "two_sum([2,7,11,15], 9)", "expected": "[0, 1]"}]
  ```
- **stdin/stdout** (Codeforces-style): The candidate's raw source is the program. Each test case provides `stdin`, and we compare the program's stdout against the expected output.

#### Lazy harness generation for Java/C++

Java and C++ require a full compilable harness around the candidate's function — imports, main, type-safe assertions. We generate this on first request using the LLM (three sections: boilerplate, reference solution, test harness), run the reference solution through the sandbox to verify all test cases pass, then cache the result to Supabase under `questions.harnesses[language]`. Every subsequent request for the same problem and language hits the cache immediately. If verification fails, the harness is not cached and the candidate falls back to Python.

#### Four-layer guardrail against answer leaks

The AI interviewer must never reveal the answer or the optimal complexity. We use four independent layers:

1. **Prompt hardening** — Track personas explicitly forbid stating time/space complexity or recommending specific technologies.
2. **Regex output detection** — Patterns catch leaks the model still produces (e.g. "O(n)", "time complexity is", "you should use").
3. **Regeneration** — On detection, the response is regenerated with a corrective instruction: "your previous draft leaked the answer — rewrite it so it only asks a question."
4. **Safe fallback** — If the regenerated response still leaks, a pre-written safe question is returned instead (e.g. "How would you characterize the efficiency?" without giving the answer).

#### JWT + RLS — two independent ownership checks

Every request goes through two independent ownership verifications:
1. `auth.py` validates the JWT via `supabase.auth.get_user(token)` — always server-side, never decoded locally.
2. `_check_ownership()` in `interview.py` checks `session.user_id == authenticated_user.id`.
3. Postgres RLS policies enforce the same ownership rule at the database level independently.

Even if application code had a bug, the database would not return another user's rows.

#### Two-key architecture

The frontend only ever holds the Supabase **anon key** (public, safe to expose — used for PKCE login). The backend holds the **service-role key** (secret, injected via environment variable at deploy time, never sent to the browser). The service-role key bypasses RLS so the backend can write on behalf of any user, but it is never exposed outside the server.

---

### 1.5 Risks & Open Questions

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Piston `--privileged` blocked on Azure free tier | High | Medium | Wandbox fallback active; gVisor/nsjail swap or D4 plan for full isolation |
| Groq rate-limited during demo | Medium | High | Ollama Cloud fallback implemented and tested |
| LLM returns invalid JSON despite json_mode | Low | Medium | `JsonOutputParser` + safe default evaluation object returned on parse failure |
| Wandbox down or rate-limited | Low | Medium | "Temporarily unavailable" message; session continues without code execution |
| Rate limiter doubles silently at 2 replicas | Medium | Low | Documented; Postgres-backed fix planned |
| Session state lost on backend restart | Medium | Medium | In-memory `SESSIONS` cache is rebuilt from Supabase on next request — stateless by design |
| Question bank sourcing (legal) | Medium | Medium | Only public datasets; no scraping |
| Benchmark rater recruitment | Medium | Low | Team + mentor can serve as initial raters |
| Java/C++ harness generation is slow on first use | High | Low | Loading hint shown to candidate after 5 seconds; cached on first success |
| Web Speech API incompatible on Safari / Firefox | High | Low | Documented requirement: Chrome or Edge + HTTPS |


---

### 1.6 Next Steps & Owners

| Action | Owner | Target |
|---|---|---|
| Add seniority and role selector to session start | Dev team | Week 2 |
| Parameterise LangChain personas by level and role | Dev team | Week 2 |
| Integrate System Design track end-to-end (canvas → LLM probe) | Dev team | Week 3 |
| Source 20 transcripts for benchmark | Dev team | Week 4 |
| Recruit 3 human raters | Team lead | Week 4 |
| Run benchmark and fill metrics table | Dev team | Week 5 |
| Add pytest backend test suite and Vitest frontend suite | Dev team | Week 6 |
| Wire tests as required CI gate before deploy | Dev team | Week 6 |
| Add structured JSON logging + Sentry | Dev team | Week 7 |
| Move rate limiter to Postgres-backed table | Dev team | Week 7 |

---

### 1.7 Links & References

| Resource | Link |
|---|---|
| GitHub | https://github.com/VishwajeetRaut/greenroom |
| Live app | https://greenroom-frontend.orangeground-05e56063.swedencentral.azurecontainerapps.io |
| LangChain LCEL docs | https://python.langchain.com/docs/expression_language |
| Piston (self-host) | https://github.com/engineer-man/piston |
| Wandbox | https://wandbox.org |
| Excalidraw | https://github.com/excalidraw/excalidraw |
| Groq | https://console.groq.com |
| Ollama Cloud | https://ollama.com |
| Supabase | https://supabase.com |
| Azure for Students | https://azure.microsoft.com/en-us/free/students |
| LeetCodeDataset (Kaggle) | https://www.kaggle.com/datasets/newfacade/leetcode-dataset |
| LeetCodeDataset (arXiv) | https://arxiv.org/abs/2504.14655 |

---

## 2. Implementation Plan & Guidance

### What we're building

A web app where candidates pick an interview track, talk to an AI interviewer, optionally write and run code live, and receive a scored evaluation. FastAPI handles all backend logic. React serves the UI. Code execution runs in a sandboxed internal container.

### Success criteria

- A candidate can complete a full Behavioral session: start, multi-turn chat with TTS voice, end, see STAR scorecard.
- A candidate can complete a full Technical session: receive a LeetCode-style problem, load boilerplate code, write a solution, run it, see test case results, chat, end, see scored evaluation.
- A candidate can complete a System Design session: receive a design prompt, draw on the Excalidraw canvas, chat, end, see feedback.
- All endpoints return 401 for requests without a valid JWT.
- Rate-limited endpoints return 429 after the configured threshold.
- Code execution uses Piston primary and Wandbox fallback transparently — the candidate never sees which service ran their code.
- Java/C++ harnesses are generated on first request and cached; the candidate sees a loading message while this happens.

### Code structure

```
backend/
  main.py                    # FastAPI app, CORS middleware, router registration
  auth.py                    # JWT extraction via Supabase, returns AuthenticatedUser
  models.py                  # Pydantic request/response schemas with field constraints
  routers/
    interview.py             # All interview endpoints, session cache, ownership checks
    tts.py                   # TTS endpoint
  services/
    llm.py                   # LangChain LCEL chains: opening, next_question, evaluate_session
    piston.py                # run_code(): Piston primary → Wandbox fallback
    rate_limit.py            # Sliding window per-user rate limiter (in-memory)
    question_bank.py         # 295 problems (210 LeetCode + 77 CodeContests + 8 hand-written), Supabase + local JSON seed
    question_generator.py    # LLM selects existing or generates new problem with dual-solution verification
    test_runner.py           # call/expected and stdin/stdout test modes, harness injection
    harness_generator.py     # Lazy Java/C++ harness build via LLM, sandbox-verified, cached
    guardrail.py             # 4-layer answer-leak prevention (prompt + regex + regeneration + fallback)
    supabase_client.py       # Singleton Supabase client using service-role key
    tts.py                   # edge-tts wrapper → audio/mpeg stream
  data/
    question_bank.json       # 295 problems — 210 LeetCode + 77 CodeContests + 8 hand-written (local seed)

frontend/src/
  pages/
    Landing.jsx              # Public homepage: pitch, how it works, 3-track overview
    Login.jsx                # Email/password login (AuthForm component)
    Signup.jsx               # Email/password signup (AuthForm component)
    AuthCallback.jsx         # Supabase PKCE OAuth redirect handler
    Dashboard.jsx            # Track selector, last 10 sessions with score/status/delete
    Interview.jsx            # Live interview: chat pane, Monaco editor, Excalidraw canvas, TTS
    Results.jsx              # Scorecard: overall score, STAR breakdown, category scores, transcript
  lib/
    api.js                   # REST client — attaches Bearer JWT to every request
    supabaseClient.js        # Supabase auth client using anon key, PKCE flow
```

### Implementation status

| Task | Status |
|---|---|
| FastAPI backend, CORS, auth middleware | ✅ Done |
| Supabase Auth — email/password + PKCE OAuth | ✅ Done |
| Behavioral track — multi-turn LLM chat + STAR evaluation | ✅ Done |
| TTS endpoint (Microsoft Edge neural voice, no API key) | ✅ Done |
| Mute/unmute TTS during interview | ✅ Done |
| Technical track — Monaco editor + code execution | ✅ Done |
| Self-hosted Piston + Wandbox fallback | ✅ Done |
| Dynamic test runner (call/expected + stdin/stdout modes) | ✅ Done |
| Lazy Java/C++ harness generation with loading hint | ✅ Done |
| Per-problem boilerplate endpoint (`GET /boilerplate`) | ✅ Done |
| Structured constraints + examples extracted from prompts | ✅ Done |
| Guardrail filter — 4-layer answer-leak defense | ✅ Done |
| System Design track — Excalidraw canvas | ✅ Done |
| Groq → Ollama Cloud LLM fallback | ✅ Done |
| Question bank — 295 problems (210 LeetCode + 77 CodeContests + 8 hand-written) | ✅ Done |
| Question generator — LLM selects or generates with dual verification | ✅ Done |
| Session history + delete on dashboard | ✅ Done |
| GitHub Actions CI/CD with OIDC (no stored credentials) | ✅ Done |
| Database constraints, indexes, RLS policy | ✅ Done |
| Seniority levels (Entry / Senior) | ⏳ Planned |
| Role selector (SWE, PM, Data Science, DevOps) | ⏳ Planned |
| Automated test suite (pytest + Vitest) | ⏳ Planned |
| Evaluation accuracy benchmark | ⏳ Planned |
| Structured logging + Sentry | ⏳ Planned |

### Error handling

Every failure path has a defined behaviour — nothing silently crashes.

| Scenario | What happens |
|---|---|
| Missing or expired JWT | 401 returned; frontend redirects to login |
| Request over rate limit | 429 returned; message shown to candidate |
| Session belongs to different user | 403 returned |
| Groq rate-limited or 5xx | Automatic retry on Ollama Cloud |
| Piston unavailable or returns 401 | Falls through to Wandbox |
| Wandbox unavailable | "Temporarily unavailable" message; session continues |
| LLM returns invalid JSON | Safe default evaluation object returned; no crash |
| Session ends with no candidate answers | Score 0 with a clear explanation instead of empty LLM call |
| Java/C++ harness fails verification | Not cached; candidate shown "try Python" message |
| LLM response leaks the answer | Regenerated once with corrective instruction; pre-written fallback if still leaks |
| Boilerplate language not yet cached | Harness generated in background (5-second loading hint shown) |

---

## 3. Security Review

**What's in place:**
- Every request validated server-side via `supabase.auth.get_user(token)` — JWT never decoded locally
- Ownership checked in application code (`_check_ownership`) *and* independently by Postgres RLS policies
- All inputs validated by Pydantic before any business logic runs: 100KB max source code, 20KB max message, 2,000 chars max TTS text, 50 chars max language/version strings
- No SQL injection risk — all database queries use the Supabase SDK's parameterized methods
- Secrets only in environment variables — confirmed by code grep, nothing hardcoded
- CORS locked to the deployed frontend origin via `ALLOWED_ORIGINS` env var
- Piston has internal-only ingress — not reachable from the internet; only the API container can call it
- Guardrail filter (regex) blocks the LLM from leaking problem answers or optimal solutions
- CI/CD uses OIDC federated identity — no Azure credentials stored as repository secrets

**Known gap — Piston sandbox:**
Piston's `isolate` sandbox requires `--privileged` Docker mode for full namespace-based process isolation. Azure Container Apps free tier blocks this. In practice, Wandbox handles most code execution and runs entirely on Wandbox's own infrastructure, so untrusted code does not run in a privileged context on our side. For a production fix: swap Piston's isolation to gVisor or nsjail (neither needs `--privileged`), or upgrade to a dedicated D4 workload profile.

---

## 4. Testing & Observability

**Testing — current state:** Manual end-to-end testing done on the live Azure deployment across all three tracks. No automated suite yet.

**Testing — planned:**
- `pytest` + `httpx.AsyncClient` for backend endpoints — ownership checks, rate limiter, Pydantic boundary conditions
- `Vitest` for frontend components
- Architecture fitness functions: frontend never imports `SERVICE_ROLE_KEY`, every session endpoint calls `_check_ownership`, schema matches `supabase/schema.sql`
- All tests as a required CI gate before the Docker build step

**Observability — planned:**
- Structured JSON logging per request: endpoint, latency, LLM provider used, execution tier used, error type — never log tokens, message content, or source code
- Sentry free tier for error tracking
- Key metrics via Azure Log Analytics (already captures stdout for free): session completion rate, LLM fallback rate, Piston vs Wandbox usage, guardrail trigger rate, p95 latency on `/interview/message` and `/interview/code/test`

**Privacy:** Candidates can delete all session data at any time via `DELETE /api/interview/{id}`. Source code is sent to Wandbox when Piston is unavailable — this is disclosed. No PII is logged.

---

## 5. Deployment & Rollout

### Live URLs

```
Frontend   https://greenroom-frontend.orangeground-05e56063.swedencentral.azurecontainerapps.io
API        https://greenroom-api.orangeground-05e56063.swedencentral.azurecontainerapps.io
Piston     http://greenroom-piston.internal  (internal only — not reachable from internet)
```

### How deployment works

Every push to `main` that changes files in `backend/` or `piston/` triggers `.github/workflows/deploy-containers.yml`:

1. Builds Docker images for the backend and Piston using Docker Buildx targeting `linux/amd64`
2. Pushes both images to GitHub Container Registry (`ghcr.io`) tagged with the commit SHA and `latest`
3. Authenticates to Azure via OIDC federated identity — no passwords or secrets stored in GitHub
4. Updates each Container App via `az containerapp update` pointing to the new image tag

The frontend is deployed separately via `deploy.sh` (manual) or its own workflow.

### Container configuration

| Container | CPU | Memory | Min replicas | Max replicas |
|---|---|---|---|---|
| Backend API | 0.5 | 1.0Gi | 0 | 2 |
| Piston | 1.0 | 2.0Gi | 0 | 1 |

### How to roll back

```bash
# Get the previous working SHA from git log or ghcr.io
az containerapp update \
  --name greenroom-api \
  --resource-group <rg> \
  --image ghcr.io/vishwajeetraut/greenroom-api:<previous-sha>
```

### Environment variables

**Backend:**
```
GROQ_API_KEY=                          # https://console.groq.com/keys
GROQ_MODEL=llama-3.3-70b-versatile
SUPABASE_URL=https://...
SUPABASE_SERVICE_ROLE_KEY=...          # Server-only — never expose to frontend
FALLBACK_BASE_URL=https://api.ollama.ai/v1   # Optional — Ollama Cloud
FALLBACK_API_KEY=...                   # Optional
FALLBACK_MODEL=llama3.3:70b            # Optional
ALLOWED_ORIGINS=https://greenroom-frontend...azurecontainerapps.io
```

**Frontend:**
```
VITE_SUPABASE_URL=https://...
VITE_SUPABASE_ANON_KEY=...             # Public key — safe to expose
VITE_API_URL=/api                      # Proxied by Vite dev server locally
```

---

## 6. Documentation & WIKI

- [x] `README.md` — local setup, env var reference, how to run both services
- [x] `DEPLOYMENT.md` — full Azure Container Apps deployment guide with cost breakdown
- [x] `DESIGN.md` — this document
- [x] `docs/diagrams/` — architecture, user flow, developer flow diagrams
- [ ] Common issues and fixes (in progress)
- [ ] Benchmark methodology and results table (pending data collection)

---

## Appendix A: Question Bank Sample

The question bank lives in `backend/data/question_bank.json` (210 problems) and is synced to the Supabase `questions` table on startup. Each problem was sourced from the LeetCodeDataset (Kaggle, newfacade, MIT licence), imported via `scripts/import_leetcode_dataset.py`, and had its constraints and examples extracted by `scripts/extract_constraints_examples.py`. Every test case was verified by running the dataset's own canonical solution through the sandbox before import — problems with any failing assertion are dropped rather than included.

**Sample entry (`two-sum`):**

```json
{
  "id": "two-sum",
  "track": "technical",
  "topic": "arrays",
  "difficulty": "easy",
  "title": "Two Sum",
  "prompt": "Given an array of integers `nums` and an integer `target`, return the indices of the two numbers that add up to `target`. Implement it as a function `two_sum(nums, target)` that returns a list of two indices. You may assume exactly one valid answer exists, and you may not use the same element twice.",
  "function_name": "two_sum",
  "languages": ["python", "node"],
  "tests": [
    { "call": "two_sum([2, 7, 11, 15], 9)",   "expected": "[0, 1]" },
    { "call": "two_sum([3, 2, 4], 6)",          "expected": "[1, 2]" },
    { "call": "two_sum([3, 3], 6)",             "expected": "[0, 1]" },
    { "call": "two_sum([-1, -2, -3, -4, -5], -8)", "expected": "[2, 4]" },
    { "call": "two_sum([0, 4, 3, 0], 0)",       "expected": "[0, 3]" },
    { "call": "two_sum([1, 5, 7, 9, 11], 16)",  "expected": "[2, 3]" }
  ],
  "constraints": [],
  "examples": [
    { "input": "two_sum([2, 7, 11, 15], 9)", "output": "[0, 1]", "explanation": "" },
    { "input": "two_sum([3, 2, 4], 6)",      "output": "[1, 2]", "explanation": "" }
  ],
  "harnesses": null
}
```

The first 3 test cases are shown to the candidate as "visible" (with input, expected, and their output). The remaining 3 run as "hidden" (pass/fail count only). Java and C++ harnesses are generated on first request and stored in the `harnesses` field once verified.

---

## Appendix B: Data Model

```sql
sessions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES auth.users ON DELETE CASCADE,
  track               TEXT NOT NULL CHECK (track IN ('behavioral','technical','system-design')),
  role                TEXT,                     -- e.g. "Software Engineer"
  status              TEXT DEFAULT 'active' CHECK (status IN ('active','completed','abandoned')),
  overall_score       INT CHECK (overall_score BETWEEN 0 AND 10),
  summary             TEXT,
  star_analysis       JSONB,                    -- {situation, task, action, result, star_score, missing_elements[]}
  assigned_question_id TEXT REFERENCES questions(id),
  created_at          TIMESTAMPTZ DEFAULT now(),
  ended_at            TIMESTAMPTZ,
  updated_at          TIMESTAMPTZ               -- auto-updated by trigger
)
-- Indexes: idx_sessions_user_id, idx_sessions_user_created
-- RLS: users see only their own rows

messages (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id  UUID NOT NULL REFERENCES sessions ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('interviewer','candidate')),
  content     TEXT NOT NULL,
  sequence_no INT,
  created_at  TIMESTAMPTZ DEFAULT now()
)
-- Index: idx_messages_session_id
-- RLS: users see only messages from their own sessions

evaluations (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id  UUID NOT NULL REFERENCES sessions ON DELETE CASCADE,
  category    TEXT,     -- "Clarity" | "Structure" | "Confidence" | "Technical Depth"
  score       INT CHECK (score BETWEEN 0 AND 10),
  feedback    TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
)
-- Index: idx_evaluations_session_id
-- RLS: users see only evaluations from their own sessions

questions (
  id            TEXT PRIMARY KEY,              -- e.g. "two-sum", "gen-{slug}-{hex6}"
  track         TEXT,                          -- technical | behavioral | system-design
  topic         TEXT,                          -- e.g. "arrays", "graphs"
  difficulty    TEXT,                          -- easy | medium | hard
  title         TEXT,
  prompt        TEXT,                          -- full problem statement
  function_name TEXT,                          -- method name for call/expected problems
  languages     TEXT[] DEFAULT '{python}',     -- supported languages
  tests         JSONB,                         -- [{call, expected}] or [{stdin, stdout}]
  constraints   JSONB,                         -- structured constraints extracted from prompt
  examples      JSONB,                         -- structured examples extracted from prompt
  harnesses     JSONB,                         -- {java: {boilerplate, harness}, cpp: {...}}
  created_at    TIMESTAMPTZ DEFAULT now()
)
-- Index: idx_questions_track
-- RLS: anyone can read (public table)
```

---

## Appendix C: API Reference

### Interview — `/api/interview`

| Method | Path | Rate limit | Description |
|---|---|---|---|
| `POST` | `/api/interview/start` | 30/min | Creates session, picks a question, returns `{session_id, track, question}` |
| `POST` | `/api/interview/message` | — | Sends candidate message, returns `{question, done}` |
| `POST` | `/api/interview/code/run` | 20/min | Executes source code, returns `{run: {stdout, stderr, code}}` |
| `POST` | `/api/interview/code/test` | 20/min | Runs test harness, returns `{status, visible_tests[], hidden_tests[], passed, total}` |
| `GET` | `/api/interview/{id}/boilerplate?language=` | — | Returns `{boilerplate, supported}` for the session's problem in the given language |
| `POST` | `/api/interview/end` | — | Evaluates session, returns `{overall_score, summary, star_analysis, evaluations[]}` |
| `DELETE` | `/api/interview/{id}` | — | Deletes session and all associated messages and evaluations |

### TTS — `/api/tts`

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tts/speak?text=` | Returns `audio/mpeg` stream via Microsoft Edge neural TTS. Text: 1–2,000 chars. |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Returns `{status: "ok"}` — used by Azure health probes |

All endpoints (except `/api/health`) require `Authorization: Bearer <JWT>`.

---

## Appendix D: Azure Migration Path

Every component has a direct Azure equivalent. Moving to Azure-native services is configuration only — no architectural rewrites.

| Current (free tier) | Azure equivalent | Effort |
|---|---|---|
| Groq (Llama 3.3 70B) | Azure OpenAI GPT-4o via AI Foundry | 1 line in `llm.py` |
| Web Speech API (browser STT) | Azure Speech Services real-time STT | Replace browser STT hook |
| edge-tts | Azure Neural TTS (same voices, higher quality) | Update `tts.py` |
| Supabase Postgres | Azure Cosmos DB for PostgreSQL | Update connection string |
| In-memory `SESSIONS` dict | Azure Cache for Redis | Update session store module |
| Piston (Docker, internal) | Azure Container Apps Dynamic Sessions | Replace `piston.py` caller |
| Supabase Auth | Azure Active Directory B2C | Update auth client |
| ACA consumption plan (free) | ACA dedicated D4 workload profile | Enables full Piston sandbox (~$50/month) |

---

*Last updated: June 2026 · Greenroom v3.0*
