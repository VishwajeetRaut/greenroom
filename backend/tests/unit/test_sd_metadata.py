"""System-design question metadata: tags, core_challenge, and scale tiers.

Includes a data-integrity sweep over the real bank, so a hand-edit or a
regenerated question that breaks the invariants fails here rather than
reaching a candidate.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from services import jd_analyzer, question_bank

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_sd_metadata.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_sd_metadata", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def sd_questions():
    seed = Path(__file__).resolve().parents[2] / "data" / "question_bank.json"
    return [q for q in json.loads(seed.read_text()) if q.get("track") == "system-design"]


# ── the real bank satisfies every rule ───────────────────────────────────────

def test_every_system_design_question_has_metadata(sd_questions):
    missing = [q["id"] for q in sd_questions if not q.get("scale_tiers") or not q.get("tags")]
    assert missing == [], f"missing tags/scale_tiers: {missing}"


def test_every_question_passes_the_generator_validation(sd_questions):
    failures = []
    for q in sd_questions:
        ok, why = gen.validate(
            {"tags": q["tags"], "core_challenge": q["core_challenge"], "scale_tiers": q["scale_tiers"]},
            q,
        )
        if not ok:
            failures.append(f"{q['id']}: {why}")
    assert failures == [], "\n".join(failures)


def test_native_tier_latency_matches_the_authored_constraint(sd_questions):
    """The generic ladder problem: a model asked for three latency tiers
    invents 1s/200ms/50ms and attaches it to every question, so a chat system
    whose stated budget is 500ms ends up claiming 50ms."""
    for q in sd_questions:
        authored = gen.authored_latency_ms(q)
        tier = q["scale_tiers"].get(q["difficulty"], {})
        if authored is None or "latency_slo" not in tier:
            continue
        assert gen.parse_latency_ms(tier["latency_slo"]) == authored, q["id"]


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("p99 < 500ms", 500),
    ("Query latency < 1 second for dashboards", 1000),   # singular unit
    ("Delivery latency < 10 seconds for push", 10000),
    ("latency < 2 minutes", 120000),
    ("p99 < 30s", 30000),
])
def test_latency_parsing_handles_singular_and_plural_units(text, expected):
    """A miss here returns None, which silently switches the native-tier
    anchoring check off rather than failing loudly — so it must not miss."""
    assert gen.parse_latency_ms(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("100M", 1e8), ("10B", 1e10), ("115K/sec", 115e3), ("1PB", 1e15), ("1,200/sec", 1200),
])
def test_magnitude_parsing(text, expected):
    assert gen.parse_magnitude(text) == expected


# ── validation rules ─────────────────────────────────────────────────────────

def _spec(**tiers):
    return {"tags": ["read-heavy", "caching", "sharding"], "core_challenge": "Something hard.",
            "scale_tiers": tiers}


def test_rejects_latency_that_loosens_as_difficulty_rises():
    ok, why = gen.validate(_spec(
        easy={"latency_slo": "p99 < 50ms"},
        medium={"latency_slo": "p99 < 200ms"},
        hard={"latency_slo": "p99 < 1s"},
    ))
    assert not ok and "TIGHTER" in why


def test_rejects_volume_that_shrinks_as_difficulty_rises():
    ok, why = gen.validate(_spec(
        easy={"peak_qps": "100K/sec"}, medium={"peak_qps": "10K/sec"}, hard={"peak_qps": "1K/sec"},
    ))
    assert not ok and "strictly increase" in why


def test_rejects_mismatched_field_sets_between_tiers():
    ok, why = gen.validate(_spec(
        easy={"peak_qps": "1K/sec"},
        medium={"peak_qps": "10K/sec", "data_volume": "1TB"},
        hard={"peak_qps": "100K/sec"},
    ))
    assert not ok and "SAME field set" in why


def test_rejects_a_per_second_rate_in_a_per_day_field():
    ok, why = gen.validate(_spec(
        easy={"writes_per_day": "100K/sec"},
        medium={"writes_per_day": "500K/sec"},
        hard={"writes_per_day": "1M/sec"},
    ))
    assert not ok and "per-DAY" in why


def test_rejects_data_volume_without_a_byte_unit():
    ok, why = gen.validate(_spec(
        easy={"data_volume": "100K"}, medium={"data_volume": "1M"}, hard={"data_volume": "10M"},
    ))
    assert not ok and "byte unit" in why


def test_rejects_tags_outside_the_vocabulary():
    spec = _spec(easy={"peak_qps": "1K/sec"}, medium={"peak_qps": "2K/sec"}, hard={"peak_qps": "3K/sec"})
    spec["tags"] = ["read-heavy", "caching", "blockchain"]
    ok, why = gen.validate(spec)
    assert not ok and "vocabulary" in why


def test_accepts_a_well_formed_spec():
    ok, why = gen.validate(_spec(
        easy={"peak_qps": "1K/sec", "latency_slo": "p99 < 500ms"},
        medium={"peak_qps": "10K/sec", "latency_slo": "p99 < 200ms"},
        hard={"peak_qps": "100K/sec", "latency_slo": "p99 < 50ms"},
    ))
    assert ok, why


# ── serving ──────────────────────────────────────────────────────────────────

def test_scale_for_returns_the_requested_tier():
    q = {"difficulty": "medium", "scale_tiers": {"easy": {"peak_qps": "1K/sec"},
                                                  "medium": {"peak_qps": "10K/sec"},
                                                  "hard": {"peak_qps": "100K/sec"}}}
    assert question_bank.scale_for(q, "hard") == {"peak_qps": "100K/sec"}


def test_scale_for_falls_back_to_the_native_tier():
    q = {"difficulty": "medium", "scale_tiers": {"medium": {"peak_qps": "10K/sec"}}}
    assert question_bank.scale_for(q, None) == {"peak_qps": "10K/sec"}
    assert question_bank.scale_for(q, "hard") == {"peak_qps": "10K/sec"}


def test_scale_for_returns_none_without_metadata():
    """Questions predating this metadata keep using their authored constraints."""
    assert question_bank.scale_for({"difficulty": "medium"}, "hard") is None


def test_format_scale_uses_a_stable_field_order():
    lines = question_bank.format_scale({
        "latency_slo": "p99 < 50ms", "peak_qps": "1M/sec", "daily_active_users": "50M",
    })
    assert lines == ["Daily active users: 50M", "Peak QPS: 1M/sec", "Latency SLO: p99 < 50ms"]


def test_format_scale_of_nothing_is_empty():
    assert question_bank.format_scale(None) == []


def test_tag_filter_matches_any_not_all():
    """Tags describe characteristics; requiring all of them would almost
    always return nothing."""
    picked = question_bank.pick_system_design_question(tags=["read-heavy", "not-a-real-tag"])
    assert picked is not None
    assert "read-heavy" in picked["tags"]


def test_tag_filter_returns_none_when_no_tag_matches():
    assert question_bank.pick_system_design_question(tags=["not-a-real-tag"]) is None


# ── seniority drives the tier ────────────────────────────────────────────────

@pytest.mark.parametrize("seniority,expected", [
    ("junior", "easy"), ("mid", "medium"), ("senior", "hard"), ("staff", "hard"),
])
def test_seniority_maps_to_a_scale_tier(seniority, expected):
    assert jd_analyzer.scale_tier_for({"seniority": seniority}) == expected


def test_unknown_seniority_leaves_the_tier_to_the_question():
    assert jd_analyzer.scale_tier_for({"seniority": "wizard"}) is None
    assert jd_analyzer.scale_tier_for(None) is None


def test_the_same_question_poses_different_numbers_by_seniority(sd_questions):
    """The point of the whole feature: a senior candidate and a junior one can
    get the same problem at genuinely different scales."""
    q = next(x for x in sd_questions if "peak_qps" in x["scale_tiers"]["easy"])
    junior = question_bank.scale_for(q, jd_analyzer.scale_tier_for({"seniority": "junior"}))
    senior = question_bank.scale_for(q, jd_analyzer.scale_tier_for({"seniority": "senior"}))
    assert gen.parse_magnitude(junior["peak_qps"]) < gen.parse_magnitude(senior["peak_qps"])
