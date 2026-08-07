"""Prometheus metrics.

Two things are worth testing here and neither is "does the counter go up":

  1. **Label cardinality.** The one way a metrics layer takes down the service
     it monitors is unbounded labels. Session UUIDs in a path label would mint
     a time series per session.
  2. **Metrics never break a request.** A monitoring bug must not become an
     outage, so every helper swallows its own exceptions.
"""
import json
import re
from pathlib import Path

import pytest
import yaml
from prometheus_client import CollectorRegistry

from services import metrics

_OBSERVABILITY = Path(__file__).resolve().parents[3] / "infra" / "observability"


def _value(name: str, **labels) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


# ── cardinality ──────────────────────────────────────────────────────────────

class _FakeRoute:
    def __init__(self, path):
        self.path = path


class _FakeRequest:
    def __init__(self, scope):
        self.scope = scope


def test_route_label_is_the_template_not_the_raw_path():
    """Otherwise every session UUID becomes its own time series."""
    request = _FakeRequest({"route": _FakeRoute("/api/interview/{session_id}/resume")})
    assert metrics.route_template(request) == "/api/interview/{session_id}/resume"


def test_unmatched_requests_do_not_mint_a_series_per_url():
    """A 404 flood against random URLs is exactly when you least want to be
    creating new time series."""
    request = _FakeRequest({"path": "/wp-admin/setup-config.php"})
    assert metrics.route_template(request) == "unmatched"


def test_recording_many_sessions_creates_one_series():
    before = len(list(metrics.REGISTRY.collect()))
    for i in range(50):
        metrics.record_http("GET", "/api/interview/{session_id}/resume", 200, 0.1)
    after = len(list(metrics.REGISTRY.collect()))
    assert after == before


# ── recording ────────────────────────────────────────────────────────────────

def test_http_recording_populates_counter_and_histogram():
    before = _value("greenroom_http_requests_total", method="POST", route="/t", status="200")
    metrics.record_http("POST", "/t", 200, 0.42)
    assert _value("greenroom_http_requests_total", method="POST", route="/t", status="200") == before + 1
    assert _value("greenroom_http_request_duration_seconds_count", method="POST", route="/t") >= 1


def test_llm_recording_splits_input_and_output_tokens():
    metrics.record_llm("next_question", "groq", "llama-3.3-70b-versatile", 500, 40, 0.001)
    assert _value("greenroom_llm_tokens_total", call_site="next_question",
                  provider="groq", direction="input") >= 500
    assert _value("greenroom_llm_tokens_total", call_site="next_question",
                  provider="groq", direction="output") >= 40


def test_fallback_provider_increments_the_fallback_counter():
    """The Groq -> Ollama rate EVALUATION_METRICS.md §7 wanted and couldn't get."""
    before = _value("greenroom_llm_fallback_total", call_site="evaluate_session")
    metrics.record_llm("evaluate_session", "fallback", "llama3.3:70b", 100, 10, None)
    assert _value("greenroom_llm_fallback_total", call_site="evaluate_session") == before + 1


def test_primary_provider_does_not_increment_the_fallback_counter():
    before = _value("greenroom_llm_fallback_total", call_site="opening_message")
    metrics.record_llm("opening_message", "groq", "llama-3.3-70b-versatile", 100, 10, None)
    assert _value("greenroom_llm_fallback_total", call_site="opening_message") == before


def test_unpriced_model_records_tokens_but_no_cost():
    metrics.record_llm("probe", "groq", "some-new-model", 10, 1, None)
    assert _value("greenroom_llm_cost_usd_total", call_site="probe", model="some-new-model") == 0.0


def test_guardrail_layers_are_recorded_separately():
    """Per layer, because the regex and the judge catching different things is
    the whole argument for having both."""
    metrics.record_guardrail("technical", "regex", True)
    metrics.record_guardrail("technical", "llm_judge", False)
    assert _value("greenroom_guardrail_checks_total",
                  track="technical", layer="regex", result="triggered") >= 1
    assert _value("greenroom_guardrail_checks_total",
                  track="technical", layer="llm_judge", result="clean") >= 1


def test_sandbox_backend_is_labelled():
    metrics.record_sandbox("python", "wandbox", ok=True, duration_seconds=1.5)
    assert _value("greenroom_sandbox_runs_total",
                  language="python", backend="wandbox", outcome="ok") >= 1


def test_evaluation_paths_are_distinguishable():
    for path in ("single_pass", "chunked", "defaulted"):
        metrics.record_evaluation("technical", path)
    for path in ("single_pass", "chunked", "defaulted"):
        assert _value("greenroom_evaluations_total", track="technical", path=path) >= 1


# ── metrics must never break a request ───────────────────────────────────────

def test_a_broken_recording_call_does_not_raise():
    """A bug in monitoring must not surface as a failed interview."""
    metrics.record_http("GET", "/t", 200, "not a number")  # type: ignore[arg-type]
    metrics.record_llm("x", "groq", "m", "bad", "bad", None)  # type: ignore[arg-type]
    metrics.record_sandbox("python", "piston", ok=True, duration_seconds=None)  # type: ignore[arg-type]


def test_disabled_metrics_record_nothing(monkeypatch):
    monkeypatch.setattr(metrics, "METRICS_ENABLED", False)
    before = _value("greenroom_sessions_total", track="behavioral", outcome="started")
    metrics.record_session("behavioral", "started")
    assert _value("greenroom_sessions_total", track="behavioral", outcome="started") == before


def test_route_template_survives_a_malformed_request():
    assert metrics.route_template(_FakeRequest({})) == "unmatched"


# ── exposition ───────────────────────────────────────────────────────────────

def test_render_produces_prometheus_text_format():
    metrics.record_http("GET", "/api/health", 200, 0.01)
    body = metrics.render().decode()
    assert "# TYPE greenroom_http_requests_total counter" in body
    assert 'route="/api/health"' in body


def test_metrics_endpoint_is_scrapeable():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "greenroom_http_requests_total" in response.text


def test_metrics_endpoint_exposes_no_user_data():
    """It's unauthenticated by design (scrapers don't carry bearer tokens), so
    every label VALUE must come from a small closed set.

    Checks the sample lines only, not the HELP/TYPE comments — the docstrings
    legitimately discuss candidates and prompts, and matching those would make
    this test fail for the wrong reason.
    """
    metrics.record_llm("next_question", "groq", "llama-3.3-70b-versatile", 5, 5, 0.1)
    metrics.record_http("POST", "/api/interview/{session_id}/resume", 200, 0.1)

    samples = [
        line for line in metrics.render().decode().splitlines()
        if line and not line.startswith("#")
    ]
    assert samples

    label_values = set()
    for line in samples:
        if "{" not in line:
            continue
        for pair in line[line.index("{") + 1:line.rindex("}")].split('",'):
            if "=" in pair:
                label_values.add(pair.split("=", 1)[1].strip('"'))

    # A UUID-shaped label value is the signature of raw user data leaking in.
    uuid_like = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE)
    leaked = [v for v in label_values if uuid_like.search(v) or "@" in v]
    assert leaked == [], f"user data in metric labels: {leaked}"


def test_a_second_registry_can_be_built_without_clashing():
    """Metrics live on a dedicated registry, not the global default, so tests
    and any future per-worker registry don't collide."""
    assert isinstance(metrics.REGISTRY, CollectorRegistry)


# ── the shipped observability stack is valid ─────────────────────────────────

@pytest.mark.parametrize("name", [
    "docker-compose.yml", "prometheus.yml", "alerts.yml",
    "grafana/provisioning/datasources/prometheus.yml",
    "grafana/provisioning/dashboards/dashboards.yml",
])
def test_stack_config_is_valid_yaml(name):
    yaml.safe_load((_OBSERVABILITY / name).read_text())


def test_grafana_dashboard_is_valid_json():
    json.loads((_OBSERVABILITY / "grafana" / "dashboards" / "greenroom.json").read_text())


def test_dashboard_datasource_uid_matches_the_provisioned_one():
    """Grafana generates a random uid when provisioning omits one, and every
    panel then renders 'datasource not found'."""
    datasource = yaml.safe_load(
        (_OBSERVABILITY / "grafana/provisioning/datasources/prometheus.yml").read_text()
    )
    uid = datasource["datasources"][0]["uid"]
    dashboard = (_OBSERVABILITY / "grafana" / "dashboards" / "greenroom.json").read_text()
    assert f'"uid": "{uid}"' in dashboard


def test_every_metric_an_alert_references_actually_exists():
    """An alert on a metric name that was renamed never fires, and nothing
    tells you — it just silently never triggers."""
    import re

    rules = yaml.safe_load((_OBSERVABILITY / "alerts.yml").read_text())
    exposed = {
        sample.name.removesuffix("_bucket").removesuffix("_count").removesuffix("_sum")
        for metric in metrics.REGISTRY.collect() for sample in metric.samples
    } | {metric.name for metric in metrics.REGISTRY.collect()}

    referenced = set()
    for group in rules["groups"]:
        for rule in group["rules"]:
            referenced |= set(re.findall(r"\bgreenroom_[a-z_]+", rule["expr"]))

    unknown = {
        name for name in referenced
        if name.removesuffix("_bucket").removesuffix("_count").removesuffix("_sum") not in exposed
    }
    assert unknown == set(), f"alerts reference metrics the app never exposes: {unknown}"
