# Greenroom — Dev Design Document

**Project:** Greenroom AI Mock Interview Platform  
**Team:** Greenroom  
**Date:** June 2026  
**Version:** 1.0 — POC + Roadmap  

---

## 1. Problem Statement & Goals

### Problem

Students and early-career job seekers have no accessible, realistic way to practice interviews with structured, personalised feedback. Existing options are:

- **Human mock interviews** — hard to schedule, no consistent scoring
- **Static Q&A tools** — no adaptive follow-up, no voice, no code execution
- **General AI chatbots (ChatGPT, Copilot)** — no interview structure, no scoring, no memory of prior answers, no STAR evaluation

### Goals

| Goal | Success Criteria |
|---|---|
| Deliver a realistic AI-driven interview experience | Candidate can complete a full session: speak answers, receive follow-up questions, get a scored report |
| Provide structured, framework-based evaluation | STAR scores, per-dimension feedback, and actionable improvement points generated per session |
| Cover three interview tracks | Behavioral, Technical (with live code execution), System Design (with diagram canvas) |
| Support multiple seniority levels and roles | Entry Level, Senior, and role-specific question sets (SWE, PM, Data Science, etc.) |
| Prove model accuracy is competitive | Evaluation scores benchmarked against human raters and general AI tools |
| Run entirely on free or student-tier infrastructure | Zero cost for POC; Azure for Students credits used for upgrade path |

### Out of Scope (POC)

- Mobile app
- Real-time multi-candidate sessions
- Paid subscription tier
- Live human reviewer integration

---

## 2. Solutions Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                            │
│                                                                 │
│  React + Vite SPA          Monaco Code Editor                   │
│  Web Speech API (STT)      Excalidraw Canvas (System Design)    │
│  edge-tts Neural TTS       Question Bank Browser                │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTPS / REST
┌──────────────────────────────▼──────────────────────────────────┐
│                       BACKEND SERVICES                          │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │          Interview Orchestrator  (FastAPI)                │  │
│  │   Session state · Question sequencing · Role/level router │  │
│  └──────────────────┬───────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────▼──────────┐  ┌────────────────────────┐   │
│  │  LangChain LCEL Agent       │  │   Code Judge Service   │   │
│  │  ChatGroq (Llama 3.3 70B)   │  │   Piston / Sandbox     │   │
│  │  STAR Evaluation Chain      │  │   Multi-language exec  │   │
│  │  Pydantic Output Parser     │  │   Test case runner     │   │
│  └──────────────────┬──────────┘  └────────────────────────┘   │
│                     │                                           │
│  ┌──────────────────▼──────────────────────────────────────┐   │
│  │               LLM Fallback Layer                        │   │
│  │  Primary:  Groq — Llama 3.3 70B (free tier)             │   │
│  │  Fallback: Ollama Cloud — Llama 3.3 70B (free tier)     │   │
│  │  Upgrade:  Azure OpenAI GPT-4o via AI Foundry           │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                          DATA LAYER                             │
│   Supabase — PostgreSQL + Auth + Row-Level Security             │
│   sessions · messages · evaluations · questions tables          │
└─────────────────────────────────────────────────────────────────┘
```

### Key User Flows

**Flow 1 — Behavioral Interview**
1. Candidate selects "Behavioral" track + seniority level + role
2. AI interviewer opens with a STAR-appropriate question from the question bank
3. Candidate speaks answer (Web Speech API → transcript)
4. LangChain interview chain analyses which STAR element is missing → generates targeted follow-up
5. Loop repeats 4–6 times
6. Candidate ends session → evaluation chain produces STAR scores + report
7. Results page shows overall score, STAR breakdown, per-category feedback, full transcript

**Flow 2 — Technical Interview**
1. Candidate selects "Technical" track + seniority (Entry/Senior) + language
2. AI presents a coding problem sourced from the question bank (LeetCode / InterviewBit tagged)
3. Candidate explains approach verbally, then codes in Monaco editor
4. Candidate runs code → sandboxed execution returns stdout/stderr + test case results
5. AI asks follow-up on complexity, edge cases, or trade-offs based on actual code submitted
6. End → evaluation scores clarity, code correctness, technical depth

**Flow 3 — System Design Interview**
1. Candidate selects "System Design" track + seniority
2. AI presents a design prompt (e.g. "Design a URL shortener")
3. Candidate draws architecture on the embedded Excalidraw canvas AND explains verbally
4. AI probes for scale, trade-offs, failure modes, data models
5. End → evaluation scores structure, depth, and design decisions

---

## 3. Scope & Constraints

### In Scope

| Feature | Status |
|---|---|
| Behavioral interview with STAR evaluation | ✅ Built |
| Technical interview with Monaco code editor | ✅ Built |
| Sandboxed code execution (Piston) | ⚠️ Partially working — needs fix |
| LangChain LCEL agent (not plain API calls) | ✅ Built |
| Pydantic-validated structured evaluation output | ✅ Built |
| STAR analysis panel in results | ✅ Built |
| LLM primary/fallback (Groq → Ollama Cloud) | ✅ Built |
| Auth + session history (Supabase) | ✅ Built |
| Delete session from dashboard | ✅ Built |
| System Design track with diagram canvas | 🔲 Planned |
| Question bank (LeetCode / InterviewBit sourced) | 🔲 Planned |
| Seniority levels (Entry / Senior) | 🔲 Planned |
| Role selector (SWE / PM / Data Science / etc.) | 🔲 Planned |
| Evaluation accuracy benchmarking vs competitors | 🔲 Planned |
| Test case runner for coding problems | 🔲 Planned |

### Constraints & Assumptions

- LLM is not fine-tuned — it uses prompt engineering and chain structure only
- Code execution relies on public Piston API (rate-limited); self-hosted Piston via Docker is the upgrade path
- Voice STT requires Chrome or Edge (Web Speech API limitation)
- No real-time WebSocket STT in POC — full streaming STT is an Azure Speech Services upgrade
- Question bank is sourced from public datasets and API wrappers (not scraping)

---

## 4. Key Design Decisions

### 4.1 LangChain LCEL Instead of Plain API Calls

**Decision:** Use LangChain Expression Language chains for all LLM interactions.

**Why:**
- Plain API calls (`groq.chat.completions.create(...)`) have no memory, no typed message sequencing, and no output schema
- LCEL chains use `MessagesPlaceholder` to inject the full conversation history as typed `AIMessage`/`HumanMessage` objects — exactly the format the model was trained on
- `JsonOutputParser(EvaluationResult)` validates LLM output against a Pydantic schema at runtime — malformed output is caught, not silently passed to the UI
- The chain is provider-agnostic: swapping `ChatGroq(...)` for `AzureChatOpenAI(...)` is one line — no other code changes

**Comparison:**

| | Plain API Call | Greenroom LangChain Chain |
|---|---|---|
| Conversation memory | None — single turn | Full typed history via MessagesPlaceholder |
| Role awareness | Manual string concat | Typed SystemMessage persona |
| Output validation | None | Pydantic schema enforcement |
| Provider coupling | Groq-specific | Provider-agnostic |
| Fallback | None | Auto-retry on Ollama Cloud (429/5xx) |

---

### 4.2 Sandbox Code Execution

**Current problem:** The Piston public API is rate-limited and occasionally times out, making the "Run code" button unreliable.

**Decision:** Self-host Piston via Docker as the primary executor; keep public Piston as fallback.

```
Candidate runs code
    → POST /api/interview/code/run
        → Try self-hosted Piston (Docker, localhost)
        → Fallback: public Piston API (emkc.org)
        → Return stdout / stderr / exit code
        → Run against hidden test cases → pass/fail per case
```

**Self-hosting Piston:**
```bash
docker run --rm -d \
  -p 2000:2000 \
  --name piston \
  ghcr.io/engineer-man/piston
```

Cost: free on any machine with Docker. On Azure: Azure Container Instances (free tier).

---

### 4.3 System Design Diagram Canvas

**Decision:** Embed Excalidraw (open source, MIT licence) as the whiteboard for System Design sessions.

**Why Excalidraw:**
- Open source, no API key, no usage limits
- React component (`@excalidraw/excalidraw`) embeds directly in the frontend
- Canvas state (JSON) can be serialised and stored in Supabase alongside the transcript
- The LLM can be given a text description of the diagram elements to probe design decisions

**Flow:**
```
Candidate draws components (boxes, arrows, labels) on canvas
    → Canvas JSON serialised every 10 seconds (debounced)
    → On "Send answer" → diagram JSON + verbal answer both sent to backend
    → Backend converts diagram JSON to a readable description for the LLM
    → LLM probes based on what the candidate drew AND said
```

---

### 4.4 Question Bank

**Decision:** Build a structured question bank seeded from public sources.

**Sources:**
- LeetCode problem set (public dataset / API wrappers)
- InterviewBit problem set (public)
- STAR behavioral question corpus (curated)
- System design prompt library (curated)

**Schema:**
```sql
questions (
  id          uuid PRIMARY KEY,
  track       text,       -- behavioral | technical | system-design
  difficulty  text,       -- entry | mid | senior
  role        text[],     -- ['software-engineer', 'data-scientist', ...]
  title       text,
  body        text,
  source      text,       -- leetcode | interviewbit | internal
  tags        text[],     -- ['arrays', 'dynamic-programming', ...]
  test_cases  jsonb,      -- [{input, expected_output}] for coding problems
  created_at  timestamptz
)
```

**Selection logic:** On session start, the backend selects a question matching `track + difficulty + role` from the question bank, rather than using a hardcoded opening question.

---

### 4.5 Seniority Levels and Roles

**Decision:** Add `level` (Entry / Senior) and `role` (SWE / PM / Data Science / DevOps / Product Design) selectors to the session start flow.

**How it changes the system:**

| Parameter | Entry Level | Senior Level |
|---|---|---|
| Question difficulty | Easy–Medium | Medium–Hard |
| Follow-up depth | Explain your reasoning | Justify trade-offs, scale, team impact |
| Evaluation rubric | Understanding + clarity | Architecture decisions + leadership |
| STAR expectation | Basic Situation + Action | Result must include team/business impact |
| Technical depth | Correct solution | Optimal solution + complexity analysis |

The interviewer persona in the LangChain system prompt is parameterised by both `track` and `level`:

```python
PERSONAS = {
  ("behavioral", "entry"):  "You are interviewing a new graduate ...",
  ("behavioral", "senior"): "You are interviewing a senior candidate with 5+ years ...",
  ("technical",  "entry"):  "Focus on correctness and problem understanding ...",
  ("technical",  "senior"): "Expect optimal solutions, complexity analysis, and edge case handling ...",
}
```

---

### 4.6 Evaluation Accuracy — Benchmarking Against Competitors

**Problem:** Any LLM can produce scores. We need to prove ours are accurate and consistent.

**Approach:** Build an evaluation benchmark with 3 tiers.

**Tier 1 — Inter-rater Reliability (Human vs. Model)**
- Collect 20 real interview transcripts
- Have 3 human raters score each on the same rubric (clarity, structure, confidence, STAR)
- Run the same transcripts through Greenroom's evaluation chain
- Compute Pearson correlation and mean absolute error between human scores and model scores
- Target: correlation > 0.80

**Tier 2 — Consistency (Model vs. Model)**
- Run the same transcript through Greenroom 5 times
- Measure score variance across runs
- Target: standard deviation < 0.5 points on a 10-point scale

**Tier 3 — Competitor Comparison**
- Submit the same transcript to ChatGPT (GPT-4o) and Gemini with a plain "score this interview" prompt
- Compare output structure, STAR coverage, actionability of feedback
- Greenroom advantages: STAR schema-enforced, per-element breakdown, missing_elements field, consistent JSON output

**Metrics table (to be filled post-benchmarking):**

| Metric | Greenroom | ChatGPT (plain prompt) | Gemini (plain prompt) |
|---|---|---|---|
| Human correlation (Pearson r) | TBD | TBD | TBD |
| Score variance (std dev) | TBD | TBD | TBD |
| STAR element coverage | ✅ Always (schema-enforced) | ❌ Inconsistent | ❌ Inconsistent |
| Missing element identification | ✅ Structured list | ❌ Free text only | ❌ Free text only |
| Output structure guaranteed | ✅ Pydantic-validated | ❌ Unstructured | ❌ Unstructured |
| Adaptive follow-up | ✅ Based on missing STAR elements | ❌ Static | ❌ Static |

---

## 5. Risks & Open Questions

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Piston public API rate-limits during demo | High | High | Self-host Piston via Docker before demo |
| LLM produces invalid JSON despite json_mode | Low | Medium | JsonOutputParser has fallback default response |
| Groq rate-limit during heavy usage | Medium | High | Ollama Cloud fallback already implemented |
| Excalidraw diagram → LLM description quality | Unknown | Medium | Spike needed: test how well LLM interprets diagram JSON |
| Question bank sourcing (legal/ToS) | Medium | Medium | Use only public datasets and API wrappers; avoid scraping |
| Benchmarking human raters — sourcing 3 raters | Medium | Medium | Can use team members + mentor as raters for POC |
| Score variance from LLM temperature | Low | Medium | Temperature fixed at 0.3 for evaluation chain |

### Open Questions

1. Should the question bank be seeded manually or pulled live from a LeetCode API wrapper?
2. What roles should be in V1? (SWE confirmed; PM, Data Science, DevOps — confirm with mentor)
3. For the diagram canvas — should the diagram be saved as a screenshot (image) or JSON? Image would require vision model.
4. How many human raters are needed for the benchmark to be statistically defensible?
5. Is self-hosting Piston allowed within Azure for Students quota constraints?

---

## 6. Next Steps & Owners

| Action | Owner | Target |
|---|---|---|
| Fix Piston integration — self-host via Docker | Dev team | Before next demo |
| Integrate Excalidraw canvas into System Design page | Dev team | Week 3 |
| Seed question bank (behavioral + technical) | Dev team | Week 3 |
| Add seniority + role selector to session start | Dev team | Week 2 |
| Parameterise LangChain personas by level | Dev team | Week 2 |
| Source 20 transcripts for benchmark | Dev team | Week 4 |
| Recruit 3 human raters for benchmark | Team lead | Week 4 |
| Run benchmark and fill metrics table | Dev team | Week 5 |
| Migrate to Azure OpenAI GPT-4o (if credits approved) | Dev team | Week 4 |
| Deploy backend to Azure Container Apps | Dev team | Week 6 |
| Deploy frontend to Vercel | Dev team | Week 6 |

---

## 7. Links & References

| Resource | Link |
|---|---|
| GitHub Repository | https://github.com/VishwajeetRaut/greenroom |
| LangChain LCEL Docs | https://python.langchain.com/docs/expression_language |
| Piston API (self-host) | https://github.com/engineer-man/piston |
| Excalidraw React Component | https://github.com/excalidraw/excalidraw |
| Groq Free Tier | https://console.groq.com |
| Ollama Cloud | https://ollama.com |
| Supabase | https://supabase.com |
| Azure for Students | https://azure.microsoft.com/en-us/free/students |
| Azure OpenAI (upgrade path) | https://azure.microsoft.com/en-us/products/ai-services/openai-service |
| Azure Speech Services | https://azure.microsoft.com/en-us/products/ai-services/speech-services |

---

## Appendix A — Current Data Model

```sql
sessions (
  id            uuid PRIMARY KEY,
  user_id       uuid → auth.users,
  track         text,           -- behavioral | technical | system-design
  role          text,           -- e.g. Software Engineer
  level         text,           -- entry | senior  [PLANNED]
  status        text,           -- active | completed
  overall_score int,
  summary       text,
  star_analysis jsonb,          -- STARAnalysis structured object
  created_at    timestamptz,
  ended_at      timestamptz
)

messages (
  id          bigint PRIMARY KEY,
  session_id  uuid → sessions,
  role        text,             -- interviewer | candidate
  content     text,
  created_at  timestamptz
)

evaluations (
  id          bigint PRIMARY KEY,
  session_id  uuid → sessions,
  category    text,             -- clarity | structure | confidence | technical depth
  score       int,
  feedback    text
)

questions (                     -- [PLANNED]
  id          uuid PRIMARY KEY,
  track       text,
  difficulty  text,             -- entry | mid | senior
  roles       text[],
  title       text,
  body        text,
  source      text,
  tags        text[],
  test_cases  jsonb
)
```

---

## Appendix B — Azure Migration Map

Every component in the current free-tier stack has a direct Azure equivalent. Migration requires no architectural changes — only configuration updates.

| Current (Free) | Azure Equivalent | Migration Effort |
|---|---|---|
| ChatGroq (Llama 3.3 70B) | Azure OpenAI GPT-4o via AI Foundry | Change 1 line in llm.py |
| Web Speech API | Azure Speech Services — Real-time STT | Replace browser STT hook |
| edge-tts | Azure Neural TTS (same voices, higher quality) | Update tts.py |
| Supabase (PostgreSQL) | Azure Cosmos DB for PostgreSQL | Update connection string |
| In-memory session dict | Azure Cache for Redis | Update session store |
| Piston (Docker) | Azure Container Instances | Redeploy container |
| Supabase Auth | Azure Active Directory B2C | Update auth client |
| Vercel (frontend) | Azure Static Web Apps | Add azure-staticwebapps.yml |

---

*Greenroom Dev Design Document — v1.0 — June 2026*
