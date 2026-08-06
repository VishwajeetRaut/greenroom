#!/usr/bin/env python3
"""
Generate structured tags and per-difficulty scale tiers for every
system-design question in the bank.

Run:
    cd backend
    python scripts/generate_sd_metadata.py --dry-run     # print, change nothing
    python scripts/generate_sd_metadata.py               # write the local seed
    python scripts/generate_sd_metadata.py --push        # also write Supabase
    python scripts/generate_sd_metadata.py --only url-shortener,chat-system

Why this exists
---------------
System-design questions already carried scale numbers, but only as free text
inside `constraints` ("100M writes/day (~1,200/sec)"). Nothing could read
them, so nothing could act on them: the difficulty of a session was fixed at
whatever the author happened to type, and a senior candidate got the same
"50M daily active users" as a junior one.

This turns those numbers into structured `scale_tiers`, one set per
difficulty, so the same problem can be posed at three different scales — and
adds a controlled-vocabulary `tags` list so questions can be matched on
characteristics (read-heavy, geo-distributed, ...) rather than on the single
coarse `topic` field.

Verification, not trust
-----------------------
Same principle the rest of this codebase applies to LLM output: the model's
answer is checked, not believed. Every generated tier set must satisfy

  * tags drawn only from TAG_VOCABULARY,
  * all three tiers present, sharing an identical field set,
  * every volume figure strictly INCREASING easy -> medium -> hard,
  * every latency budget strictly DECREASING easy -> medium -> hard
    (a harder question means a tighter deadline, not a looser one — the one
    field where "harder" points the other way, and the mistake a model
    reliably makes if nothing checks it).

A question failing validation after all attempts is left exactly as it was
and reported at the end; it is never written half-updated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "question_bank.json"

TAG_VOCABULARY = [
    "read-heavy", "write-heavy", "real-time", "low-latency", "geo-distributed",
    "high-availability", "strong-consistency", "eventual-consistency",
    "storage-heavy", "fan-out", "streaming", "batch-processing", "idempotency",
    "ranking", "search-indexing", "rate-limiting", "caching", "sharding",
    "durability-critical", "regulatory-compliance", "graph-traversal",
    "time-series", "media-processing", "push-notifications",
]

TIERS = ["easy", "medium", "hard"]

# Volume-like fields get bigger with difficulty; latency budgets get smaller.
ASCENDING_FIELDS = [
    "daily_active_users", "writes_per_day", "reads_per_day", "peak_qps", "data_volume",
]
DESCENDING_FIELDS = ["latency_slo"]
ALL_FIELDS = ASCENDING_FIELDS + DESCENDING_FIELDS

_SYSTEM = """\
You add structured metadata to a system-design interview question.

Reply ONLY as valid JSON, no markdown fences, exactly this shape:
{{
  "tags": ["<tag>", ...],
  "core_challenge": "<one sentence: what actually makes this problem hard, the \
thing a strong candidate must confront>",
  "scale_tiers": {{
    "easy":   {{{fields}}},
    "medium": {{{fields}}},
    "hard":   {{{fields}}}
  }}
}}

"tags": 3 to 6 entries, chosen ONLY from this list, copied verbatim:
{vocabulary}

"scale_tiers" rules — these are checked programmatically and your answer is \
rejected if they fail:
- Include ONLY the fields that genuinely make sense for THIS system. Omit any \
field that doesn't apply (a rate limiter has no "daily_active_users"). \
Whatever fields you choose, all three tiers must use the SAME set.
- Available fields: {field_list}
- Volume fields ({ascending}) must STRICTLY INCREASE from easy to medium to \
hard. Use round, realistic, interview-appropriate numbers with unit suffixes, \
e.g. "1M", "50M", "2B", "12K/sec".
- "latency_slo" must get STRICTLY TIGHTER as difficulty rises — the harder \
tier has the SMALLER number, e.g. easy "p99 < 500ms", medium "p99 < 200ms", \
hard "p99 < 50ms". Always format it as "p99 < <number><unit>".
- The "{native}" tier must stay close to the numbers already stated in the \
question's constraints below — that is the scale this question was written \
for. Scale the other two tiers around it.
- Use a consistent unit for a given field across all three tiers (don't mix \
"500K" and "2M/day" in the same field)."""


def _user_prompt(question: dict) -> str:
    constraints = "\n".join(f"- {c}" for c in question.get("constraints") or [])
    components = ", ".join(question.get("expected_components") or [])
    return (
        f"Title: {question['title']}\n"
        f"Topic: {question.get('topic')}\n"
        f"Native difficulty: {question.get('difficulty')}\n\n"
        f"Problem:\n{question['prompt']}\n\n"
        f"Existing constraints:\n{constraints or '(none)'}\n\n"
        f"Expected components: {components or '(none)'}"
    )


# ── numeric parsing ──────────────────────────────────────────────────────────

_MULTIPLIERS = {
    "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
    "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
    "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "pb": 1e15,
}
# Latency is compared in milliseconds, so a value in seconds must scale up or
# "p99 < 2s" would compare as smaller than "p99 < 500ms".
_TIME_UNITS = {
    "ms": 1.0, "millisecond": 1.0, "milliseconds": 1.0,
    "s": 1000.0, "sec": 1000.0, "secs": 1000.0, "second": 1000.0, "seconds": 1000.0,
    "m": 60000.0, "min": 60000.0, "mins": 60000.0, "minute": 60000.0, "minutes": 60000.0,
}
# Longest-first. A naive "ms|sec|seconds|s|..." order silently fails on
# "1 second": "sec" matches but the trailing \b doesn't (an "o" follows), and
# every shorter alternative fails the same way, so the whole match returns
# None. That isn't a parse error anyone sees — it just quietly switches the
# native-tier latency check off for that question, which is how a "p99 < 50ms"
# tier survived on a question whose stated budget was 1 second.
_TIME_UNIT_PATTERN = "|".join(sorted(_TIME_UNITS, key=len, reverse=True))


def parse_magnitude(value: str) -> float | None:
    """Best-effort numeric value of a scale string, for ordering only.
    Returns None if no number can be found, which the caller treats as a
    validation failure rather than silently skipping the check."""
    if not isinstance(value, str):
        return None
    text = value.lower().replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*([a-z]*)", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    return number * _MULTIPLIERS.get(suffix, 1.0)


def parse_latency_ms(value: str) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.lower().replace(",", "").strip()
    match = re.search(rf"(\d+(?:\.\d+)?)\s*({_TIME_UNIT_PATTERN})\b", text)
    if not match:
        return None
    return float(match.group(1)) * _TIME_UNITS[match.group(2)]


# ── validation ───────────────────────────────────────────────────────────────

def authored_latency_ms(question: dict) -> float | None:
    """The latency budget the question was actually written with, if it states
    one explicitly.

    Deliberately narrow: only constraints that literally mention latency (or a
    p99) are considered. Constraints like "Payouts settle within 2 business
    days" or "Messages persisted for at least 7 years" are durations too, and
    a looser regex would happily read them as a request-latency budget.
    """
    best: float | None = None
    for constraint in question.get("constraints") or []:
        text = constraint.lower()
        if "latency" not in text and "p99" not in text:
            continue
        value = parse_latency_ms(constraint)
        if value is not None and (best is None or value < best):
            best = value
    return best


def validate(spec: dict, question: dict | None = None) -> tuple[bool, str]:
    if not isinstance(spec, dict):
        return False, "reply was not a JSON object"

    tags = spec.get("tags")
    if not isinstance(tags, list) or not 3 <= len(tags) <= 6:
        return False, "tags must be a list of 3 to 6 entries"
    invalid = [t for t in tags if t not in TAG_VOCABULARY]
    if invalid:
        return False, f"these tags are not in the allowed vocabulary: {invalid}"

    if not isinstance(spec.get("core_challenge"), str) or not spec["core_challenge"].strip():
        return False, "core_challenge must be a non-empty string"

    tiers = spec.get("scale_tiers")
    if not isinstance(tiers, dict) or any(t not in tiers for t in TIERS):
        return False, f"scale_tiers must contain all of {TIERS}"

    field_sets = []
    for tier in TIERS:
        values = tiers[tier]
        if not isinstance(values, dict) or not values:
            return False, f"scale_tiers.{tier} must be a non-empty object"
        unknown = [f for f in values if f not in ALL_FIELDS]
        if unknown:
            return False, f"scale_tiers.{tier} has unknown fields {unknown}; allowed: {ALL_FIELDS}"
        field_sets.append(set(values))
    if field_sets[0] != field_sets[1] or field_sets[1] != field_sets[2]:
        return False, (
            "all three tiers must use the SAME field set, got "
            + " vs ".join(str(sorted(f)) for f in field_sets)
        )

    for field in field_sets[0]:
        raw = [tiers[t][field] for t in TIERS]

        # Unit sanity, checked separately from ordering. A monotonic set of
        # values can still be nonsense: "100K/sec" in a *_per_day field, or a
        # bare "100K" in data_volume, both pass an ordering check while being
        # unreadable to anyone using the number.
        if field.endswith("_per_day"):
            bad = [v for v in raw if isinstance(v, str)
                   and re.search(r"/\s*(sec|s|hr|hour|min|minute)\b", v.lower())]
            if bad:
                return False, (
                    f"{field} is already per-DAY, so it must not carry a per-second/hour/minute "
                    f"rate — write the daily total instead. Offending values: {bad}"
                )
        if field == "data_volume":
            bad = [v for v in raw if isinstance(v, str)
                   and not re.search(r"\d\s*(kb|mb|gb|tb|pb)\b", v.lower())]
            if bad:
                return False, (
                    f"data_volume must be a size with a byte unit (GB/TB/PB), got {bad}"
                )

        if field in DESCENDING_FIELDS:
            parsed = [parse_latency_ms(v) for v in raw]
            if any(p is None for p in parsed):
                return False, f"could not read a latency out of {field}: {raw}"
            if not (parsed[0] > parsed[1] > parsed[2]):
                return False, (
                    f"{field} must get TIGHTER as difficulty rises "
                    f"(easy is the loosest, hard is the strictest), got {raw}"
                )
        else:
            parsed = [parse_magnitude(v) for v in raw]
            if any(p is None for p in parsed):
                return False, f"could not read a number out of {field}: {raw}"
            if not (parsed[0] < parsed[1] < parsed[2]):
                return False, (
                    f"{field} must strictly increase easy -> medium -> hard, got {raw}"
                )

    # Anchor the native tier to the question's own authored latency budget.
    # Without this the model builds a generic ladder (1s -> 200ms -> 50ms) and
    # attaches it to every question regardless of the problem, so a chat system
    # whose stated budget is 500ms ends up claiming p99 < 50ms. 11 of the 20
    # questions are natively hard, so that generic ladder was about to assert
    # a 50ms p99 across most of the bank.
    if question and "latency_slo" in field_sets[0]:
        authored = authored_latency_ms(question)
        native = question.get("difficulty", "medium")
        if authored is not None and native in TIERS:
            actual = parse_latency_ms(spec["scale_tiers"][native]["latency_slo"])
            if actual is None or abs(actual - authored) > 1e-6:
                return False, (
                    f"the '{native}' tier is this question's NATIVE difficulty, so its "
                    f"latency_slo must match the budget the question already states "
                    f"({authored:.0f}ms), not a generic ladder — got "
                    f"{spec['scale_tiers'][native]['latency_slo']!r}. Rebuild the ladder "
                    f"around that value: looser for easier tiers, tighter for harder ones."
                )
    return True, ""


# ── generation ───────────────────────────────────────────────────────────────

ATTEMPTS = 4


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```[a-z]*\n?", "", text.strip())
    return re.sub(r"\n?```$", "", text).strip()


def generate(question: dict) -> tuple[dict | None, str]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.llm import _make_llm

    fields = ", ".join(f'"{f}": "<value>"' for f in ALL_FIELDS)
    system = _SYSTEM.format(
        fields=fields,
        vocabulary=", ".join(TAG_VOCABULARY),
        field_list=", ".join(ALL_FIELDS),
        ascending=", ".join(ASCENDING_FIELDS),
        native=question.get("difficulty", "medium"),
    )
    user = _user_prompt(question)
    last_error = "no attempt succeeded"

    for attempt in range(1, ATTEMPTS + 1):
        prompt = user
        if attempt > 1:
            # Feed the exact validation failure back in, same corrective-retry
            # pattern as harness_generator — a model told what it got wrong
            # fixes that, whereas a blind retry just re-rolls the same dice.
            prompt += (
                f"\n\nYour previous attempt was REJECTED for this reason:\n{last_error}\n\n"
                "Fix exactly that problem. Keep everything else the same."
            )
        try:
            chat = _make_llm(temperature=0.3, max_tokens=1200, call_site="sd_metadata")
            raw = chat.invoke([SystemMessage(content=system), HumanMessage(content=prompt)]).content
        except Exception as exc:
            last_error = f"LLM call failed: {exc}"
            continue

        try:
            spec = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            last_error = f"reply was not valid JSON: {exc}"
            continue

        ok, reason = validate(spec, question)
        if ok:
            return spec, ""
        last_error = reason

    return None, last_error


# ── persistence ──────────────────────────────────────────────────────────────

def push_to_supabase(question_id: str, spec: dict) -> str:
    from services.supabase_client import get_supabase
    sb = get_supabase()
    if not sb:
        return "supabase not configured"
    try:
        sb.table("questions").update({
            "tags": spec["tags"],
            "core_challenge": spec["core_challenge"],
            "scale_tiers": spec["scale_tiers"],
        }).eq("id", question_id).execute()
        return "ok"
    except Exception as exc:
        return f"failed: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print results, write nothing")
    ap.add_argument("--push", action="store_true", help="also update Supabase")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--force", action="store_true", help="regenerate questions that already have metadata")
    args = ap.parse_args()

    bank = json.loads(SEED_PATH.read_text())
    targets = [q for q in bank if q.get("track") == "system-design"]
    if args.only:
        wanted = {i.strip() for i in args.only.split(",")}
        targets = [q for q in targets if q["id"] in wanted]
    if not args.force:
        targets = [q for q in targets if not q.get("scale_tiers")]

    if not targets:
        print("Nothing to do (use --force to regenerate existing metadata).")
        return 0

    print(f"Generating metadata for {len(targets)} system-design question(s)\n")
    failures: list[tuple[str, str]] = []
    updated = 0

    for question in targets:
        spec, error = generate(question)
        if not spec:
            failures.append((question["id"], error))
            print(f"  FAIL  {question['id']}: {error}")
            continue

        question["tags"] = spec["tags"]
        question["core_challenge"] = spec["core_challenge"].strip()
        question["scale_tiers"] = spec["scale_tiers"]
        updated += 1

        native = question.get("difficulty", "medium")
        print(f"  ok    {question['id']}")
        print(f"          tags: {', '.join(spec['tags'])}")
        for tier in TIERS:
            marker = " <- native" if tier == native else ""
            values = ", ".join(f"{k}={v}" for k, v in spec["scale_tiers"][tier].items())
            print(f"          {tier:6} {values}{marker}")

        if args.push and not args.dry_run:
            print(f"          supabase: {push_to_supabase(question['id'], spec)}")

    if not args.dry_run and updated:
        SEED_PATH.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n")
        print(f"\nWrote {updated} question(s) to {SEED_PATH}")
    elif args.dry_run:
        print("\n(dry run — nothing written)")

    if failures:
        print(f"\n{len(failures)} question(s) left unchanged:")
        for qid, reason in failures:
            print(f"  {qid}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
