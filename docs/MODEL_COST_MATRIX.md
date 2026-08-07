# Greenroom — Model Benchmark & Cost Matrix (measured 2026-08-06)

Every token count in this document is **provider-reported**, captured from
Groq's own `usage` object by `services/token_meter.py` — nothing here is a
chars/4 estimate. Every model ran the **real production prompts** at the
**real production `max_tokens`**, via `scripts/benchmark_models.py`.

Reproduce with:

```bash
cd backend
python scripts/benchmark_models.py --repeat 3 --out ../docs/model_benchmark_raw.json
```

Raw per-run results: [`model_benchmark_raw.json`](./model_benchmark_raw.json).

This closes the "**Cost per completed session — no token-usage tracking wired
up**" gap listed under *not yet measurable* in
[EVALUATION_METRICS.md](./EVALUATION_METRICS.md) §7.

## What is and isn't measured

**Measured:** input tokens, output tokens, latency, and per-workload
*mechanical* correctness — does the JSON parse, does the guardrail regex
catch a leak, does the greeting stay within length.

**Not measured: answer quality.** Nothing here says whether an interviewer
turn was a *good* question, or whether an evaluation score is *fair*. A model
can pass every gate below and still be a worse interviewer. Treat the pass
rates as a floor (a model failing them is unusable) rather than as evidence
of equivalence. Establishing quality equivalence needs the human-correlation
study already listed as the most valuable open item in EVALUATION_METRICS.md.

**Pricing caveat:** token counts are exact; dollar figures are not. The
$/token rates in `token_meter.PRICING` come from third-party pricing
aggregators retrieved 2026-08-06 — Groq renders its pricing table
client-side and `console.groq.com/docs/pricing` 404s, so they could not be
machine-verified against the vendor. **Verify before budgeting.** Relative
comparisons between models are more trustworthy than absolute totals. Two
discounts are also unmodelled (Batch API −50%, Groq-side cached input −50%),
so real spend should land at or below these figures.

## The workloads

| Workload | Real prompt used | Calls per session | Pass criterion |
|---|---|---|---|
| `opening` | `OPENING_SYSTEM_PROMPT` | 1 | Non-empty, ≤700 chars, stays in character |
| `turn` | `TRACK_PERSONAS["technical"]` | 15 | Non-empty, no complexity leak (reuses `guardrail.violates`) |
| `evaluate` | `EVAL_SYSTEM_PROMPT` | 2 | Parses as JSON with all four required keys |
| `testcases` | `_CASES_SYSTEM` | 1+ | Parses as a JSON array of `call`/`expected` objects |
| `judge` | Guardrail YES/NO prompt | 15 | Flags a leaky draft **and** clears a clean one |

The `judge` check runs both directions deliberately — a one-directional test
would be passed by a model that simply always answers NO.

Call counts follow the real flow: `MAX_CANDIDATE_TURNS=15`, one guardrail
judge per turn, and two `evaluate` calls because `EVAL_SELF_CRITIQUE_ENABLED`
defaults to on.

## Results at production token budgets (n=3 per cell)

| Model | Workload | Pass | p50 latency | In tok | Out tok | $/call |
|---|---|---|---|---|---|---|
| `llama-3.3-70b-versatile` | opening | 100% | 414 ms | 110 | 70 | $0.000120 |
| `llama-3.3-70b-versatile` | turn | 100% | 413 ms | 554 | 31 | $0.000352 |
| `llama-3.3-70b-versatile` | evaluate | 100% | 996 ms | 471 | 309 | $0.000522 |
| `llama-3.3-70b-versatile` | testcases | 100% | 596 ms | 285 | 146 | $0.000283 |
| `llama-3.3-70b-versatile` | judge | 100% | 330 ms | 211 | 4 | $0.000128 |
| `llama-3.1-8b-instant` | opening | 100% | 227 ms | 110 | 66 | $0.000011 |
| `llama-3.1-8b-instant` | turn | 100% | 179 ms | 554 | 24 | $0.000029 |
| `llama-3.1-8b-instant` | evaluate | 100% | 580 ms | 471 | 339 | $0.000051 |
| `llama-3.1-8b-instant` | testcases | 100% | 434 ms | 285 | 188 | $0.000029 |
| `llama-3.1-8b-instant` | judge | **0%** | 253 ms | 211 | 4 | $0.000011 |
| `openai/gpt-oss-20b` | opening | 100% | 428 ms | 149 | 89 | $0.000038 |
| `openai/gpt-oss-20b` | turn | 67% | 523 ms | 585 | 98 | $0.000073 |
| `openai/gpt-oss-20b` | evaluate | 100% | 1271 ms | 516 | 650 | $0.000234 |
| `openai/gpt-oss-20b` | testcases | 0% | 1151 ms | 324 | 600 | $0.000204 |
| `openai/gpt-oss-20b` | judge | 0% | 749 ms | 281 | 6 | $0.000023 |
| `openai/gpt-oss-120b` | opening | 67% | 478 ms | 149 | 79 | $0.000069 |
| `openai/gpt-oss-120b` | turn | 100% | 511 ms | 585 | 107 | $0.000152 |
| `openai/gpt-oss-120b` | evaluate | 67% | 1836 ms | 516 | 700 | $0.000497 |
| `openai/gpt-oss-120b` | testcases | 33% | 1681 ms | 324 | 527 | $0.000365 |
| `openai/gpt-oss-120b` | judge | 0% | 622 ms | 281 | 6 | $0.000046 |
| `qwen/qwen3.6-27b` | opening | 100% | 842 ms | 90 | 120 | $0.000414 |
| `qwen/qwen3.6-27b` | turn | 100% | 646 ms | 546 | 200 | $0.000928 |
| `qwen/qwen3.6-27b` | evaluate | 0% | 1611 ms | — | — | — |
| `qwen/qwen3.6-27b` | testcases | 0% | 1462 ms | 277 | 600 | $0.001966 |
| `qwen/qwen3.6-27b` | judge | 0% | 324 ms | 163 | 6 | $0.000116 |

### Most of those failures were the benchmark's fault, not the model's

`gpt-oss-*` and `qwen3.6` are **reasoning models**. Qwen emits a `<think>`
trace into the content channel; the gpt-oss models spend a hidden reasoning
budget. Both count against `max_tokens`. At production budgets
(`judge`=3, `testcases`=600) the visible answer never arrives — which *looks*
like a capability failure but is really a budget one. One `gpt-oss-20b`
`turn` "failure" was a Groq harmony-format parse error whose
`failed_generation` field contained a perfectly good interview question.

Re-running at 8× the token budget (`--token-scale 8`, n=2) separates the two:

| Model | Workload | Pass @1× | Pass @8× | p50 @8× | Out tok @8× |
|---|---|---|---|---|---|
| `openai/gpt-oss-20b` | opening | 100% | 100% | 649 ms | 130 |
| `openai/gpt-oss-20b` | evaluate | 100% | 100% | 1295 ms | 693 |
| `openai/gpt-oss-20b` | testcases | 0% | **100%** | 1534 ms | 1128 |
| `openai/gpt-oss-20b` | judge | 0% | **0%** | 813 ms | 48 |
| `openai/gpt-oss-120b` | testcases | 33% | **100%** | 1459 ms | 542 |
| `openai/gpt-oss-120b` | evaluate | 67% | 100% | **18663 ms** | 577 |
| `openai/gpt-oss-120b` | judge | 0% | **0%** | 794 ms | 48 |
| `qwen/qwen3.6-27b` | opening | 100% | **0%** | 1630 ms | 601 |
| `qwen/qwen3.6-27b` | testcases | 0% | **0%** | 7666 ms | 3510 |
| `qwen/qwen3.6-27b` | evaluate | 0% | 100% | **28964 ms** | 3581 |

Three conclusions survive the correction:

1. **The gpt-oss models are viable for generation** once given room — but
   they need 2-8× the output budget, which erodes the headline price
   advantage, and `gpt-oss-120b` posts an **18.6 s** p50 on `evaluate`.
2. **No reasoning model can serve the guardrail judge.** It is a
   `max_tokens=3` YES/NO call, and a model that reasons first never gets to
   the answer. This is structural, not tunable.
3. **`qwen3.6-27b` is out.** It's the most expensive model tested, it can't
   keep a greeting to 2-3 sentences even with 8× room (2004 and 3159 chars),
   its `<think>` prefix breaks every JSON workload, and `evaluate` takes
   **29 s** and costs $0.011 — 21× the 70B model, for a call that must run
   twice per session.

### The judge failure has a direction, and it matters

| Model | Judge failure mode | Consequence |
|---|---|---|
| `llama-3.3-70b-versatile` | none (8/8 over a larger sample) | — |
| `llama-3.1-8b-instant` | **false positive** (8/8): flags a clean question | Safe. Triggers an unnecessary regeneration — costs latency and tokens |
| `gpt-oss-20b` / `120b` / `qwen3.6` | **false negative**: misses a real leak | **Unsafe. The complexity leak reaches the candidate** |

`llama-3.1-8b-instant` errs toward over-blocking; the reasoning models err
toward letting leaks through. Those are not equivalent failures, and only one
of them is tolerable.

## Where the money actually goes (all-70B session)

| Workload | Calls | Cost | Share |
|---|---|---|---|
| `turn` | 15 | $0.005275 | **61.0%** |
| `judge` | 15 | $0.001920 | 22.2% |
| `evaluate` | 2 | $0.001043 | 12.1% |
| `testcases` | 1 | $0.000283 | 3.3% |
| `opening` | 1 | $0.000120 | 1.4% |
| **Total** | | **$0.008641** | |

At ~$0.0086/session, 1,000 completed interviews cost about **$8.60**. Cost is
not currently a scaling problem — which is itself a useful finding, and an
argument against trading interview quality for a cheaper model.

**83% of session cost is the two per-turn calls.** `turn` alone is 61%, and
its input grows every turn because the whole history is re-sent — so cost per
session scales roughly quadratically in turn count, not linearly. The highest-
leverage optimization is not a cheaper model; it's sending less history
(windowing or a rolling summary). That's tracked separately from this task.

### What the response cache (task #1) is actually worth

The per-session table above counts `testcases` once. In a real coding
interview a candidate clicks "Run tests" repeatedly, and before
`services/llm_cache.py` every click paid for a fresh generation:

| Run-tests clicks | Uncached | Share of session | Cached |
|---|---|---|---|
| 1 | $0.000283 | 3.3% | $0.000283 |
| 5 | $0.001415 | 16.4% | $0.000283 |
| 10 | $0.002830 | 32.7% | $0.000283 |
| 20 | $0.005660 | 65.5% | $0.000283 |

So the cache is worth little on a session where the candidate runs tests once,
and worth roughly a third of total session cost on a session with ten clicks.
It only applies to problems the interviewer invented ad hoc or where the
candidate's language isn't in the bank entry's `languages` — bank questions in
a supported language never made this call at all.

## Routing options

| Strategy | $/session | vs. all-70B | Safe? |
|---|---|---|---|
| **All `llama-3.3-70b-versatile`** (current) | $0.008641 | — | Yes — only config passing every gate |
| **Mixed: 8B generation + 70B judge** | $0.002502 | **−71%** | Judge stays safe; generation quality unverified |
| All `llama-3.1-8b-instant` | $0.000747 | −91% | **No** — judge over-blocks on every turn |
| All `gpt-oss-20b` @8× budget | ~$0.0014 | −84% | **No** — judge misses real leaks |
| All `qwen/qwen3.6-27b` | $0.018+ | +108% | **No** — fails 3 of 5 workloads, 29 s evaluate |

### Recommendation

**Keep `llama-3.3-70b-versatile` as the default.** At $0.0086/session cost
isn't the binding constraint, and it's the only configuration that passes
every gate.

**If cost becomes binding, the mixed strategy is the one to pursue** — 8B for
generation, 70B for the guardrail judge, a 71% reduction. `_make_llm` already
accepts a per-call `model` override, so this is a routing table, not a
refactor. Two things must happen first:

1. Validate 8B evaluation quality against 70B on real transcripts. The gate
   here only proves the JSON parses, and the `evaluate` call is what the
   candidate actually receives as their report.
2. Root-cause the 47.7% missing-score rate (EVALUATION_METRICS.md §2) before
   changing the model underneath it — otherwise a pre-existing bug will be
   misattributed to the model swap.

**Don't chase the cheap models for the guardrail.** Every non-70B model tested
fails that gate, and the reasoning models fail it in the unsafe direction.

## Live instrumentation

`GET /api/analytics/llm-usage` returns the same accounting for real traffic,
broken down by call site — so the projections above can be checked against
what sessions actually cost. Counters are in-process (reset on restart,
per-replica). `LLM_METER_ENABLED=false` disables recording.
