import json

import httpx
import pytest
from test_reporting import seeded_cycles

from energy_monitor.report_ai import (
    MODEL,
    AnalysisError,
    analysis_evidence,
    analysis_report,
    generate_analysis,
    select_insights,
)
from energy_monitor.reporting import build_report


@pytest.fixture(scope="module")
def report_source(tmp_path_factory):
    store, ids = seeded_cycles(tmp_path_factory.mktemp("hosted-ai"), 20)
    # Test-only fixture for the sensor path. No genuine production claims.
    with store._connect() as connection, connection:
        connection.execute(
            "UPDATE appliance_cycles SET summary_json = "
            "json_set(summary_json, '$.data_origin', 'sensor')"
        )
    scope = store.fetch_cycle(ids[-1])
    return {
        count: build_report(
            store, profile_id=scope["profile_id"], device_id=scope["device_id"],
            cycle_id=ids[-1] if count == 1 else None, cycle_count=count,
        )
        for count in (1, 5, 10, 20)
    }


@pytest.mark.parametrize("count", (1, 5, 10, 20))
def test_evidence_contains_saved_measurements_and_omits_sensitive_fields(report_source, count):
    source = analysis_report(report_source[count])
    evidence = analysis_evidence(source, None)
    serialized = json.dumps(evidence)
    assert source.device_id not in serialized
    assert str(source.report_id) not in serialized
    assert "sample_sequence" not in serialized
    assert "Comparable anomaly scores were unavailable" in evidence["anomaly"]
    if count > 1:
        assert f"{count} of {count} cycles" in evidence["overview"]
        assert "Total energy" in evidence["overview"]
        assert "spread" in evidence["choices"]["findings"]


def provider_response(selection):
    final_text = json.dumps(selection)
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(200, request=request, json={
        "status": "completed", "output": [
            {"type": "message", "phase": "commentary", "content": [
                {"type": "output_text", "text": "Comparing readings."},
            ]},
            {"type": "message", "phase": "final_answer", "content": [
                {"type": "output_text", "text": final_text[:10]},
                {"type": "output_text", "text": final_text[10:]},
            ]},
        ],
        "usage": {"input_tokens": 120, "output_tokens": 35},
    })


class ReservationStore:
    can_edit = True

    def __init__(self):
        self.result = None
        self.reserved = False
        self.limit = False

    def _rpc(self, name, payload):
        if name == "read_report_ai_analysis":
            return self.result
        if name == "reserve_report_ai_analysis":
            if self.limit:
                return "daily_limit"
            if self.reserved:
                return "existing"
            self.reserved = True
            return "reserved"
        assert name == "finish_report_ai_analysis" and self.reserved
        self.result = payload["result"]
        return True


def test_public_reservation_provider_and_reopen(report_source, monkeypatch):
    requests = []
    store = ReservationStore()
    assessment = {"summary": "Experimental score 73.52 / 100.", "pattern": "unusual",
                  "comparison": "The run drew 12.1% less mean power."}

    def post(url, **kwargs):
        assert store.reserved
        assert url == "https://api.openai.com/v1/responses"
        request = kwargs["json"]
        requests.append(request)
        assert request["model"] == MODEL and request["store"] is False and not request["tools"]
        assert request["text"]["format"]["strict"] is True
        assert "test-key" not in request["input"]
        return provider_response({
            "findings": ["baseline"], "explanations": ["unusual"], "actions": ["matched_run"],
        })

    monkeypatch.setattr(httpx, "post", post)
    source = report_source[1]
    store.can_edit = False
    with pytest.raises(AnalysisError, match="currently unavailable"):
        generate_analysis(store, source, assessment, "")
    store.limit = True
    with pytest.raises(AnalysisError, match="daily"):
        generate_analysis(store, source, assessment, "test-key")
    assert not requests
    store.limit = False
    result = generate_analysis(store, source, assessment, "test-key")
    assert result["anomaly"] == f"{assessment['summary']}\n\n{assessment['comparison']}"
    assert result["sections"]["findings"] == [assessment["comparison"]]
    assert result["usage"] == {"input_tokens": 120, "output_tokens": 35}
    assert generate_analysis(store, source, None, "test-key") == result
    assert len(requests) == 1


def test_provider_rejects_unsupported_ids_and_never_retries(report_source, monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return provider_response({
            "findings": ["Invented 900 W"], "explanations": ["conditions"],
            "actions": ["log_conditions"],
        })

    monkeypatch.setattr(httpx, "post", post)
    store = ReservationStore()
    with pytest.raises(AnalysisError, match="did not pass validation"):
        generate_analysis(store, report_source[5], None, "test-key")
    assert "error" in generate_analysis(store, report_source[5], None, "test-key")
    assert len(calls) == 1


def test_provider_quota_is_reported_without_response_contents(report_source, monkeypatch):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(
        429, request=request, json={"error": {"message": "private diagnostics"}},
    ))
    evidence = analysis_evidence(report_source[5], None)
    with pytest.raises(AnalysisError, match="OpenAI reported a usage or rate limit"):
        select_insights(evidence, "test-key")



@pytest.mark.parametrize("count", (1, 5))
def test_hosted_reports_offer_generation_to_visitors_and_reopen_saved_chart(
    report_source, tmp_path, monkeypatch, count,
):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from energy_monitor.cloud_edits import SupabaseCycleStore

    source = report_source[count]
    saved = source.model_dump(mode="json")
    cloud = ReservationStore()
    calls = []
    score_available = False

    def rpc(self, name, payload):
        if name == "dashboard_cycle_annotation":
            return {"label": "Complete"}
        return cloud._rpc(name, payload)

    def assess(cycle, store):
        assert cycle["custom_label"] == "Complete"
        if not score_available:
            return None
        return (
            {"mean_power_w": 2000.0, "power_change_w": 89.0, "cycle_count": 36},
            {"summary": "Isolation Forest score 37.81 / 100.", "pattern": "normal",
             "comparison": "The cycle averaged 89.0 W more than the model's training mean."},
        )

    def post(*args, **kwargs):
        calls.append(1)
        return provider_response({
            "findings": ["spread" if count > 1 else "coverage"],
            "explanations": ["conditions"], "actions": ["matched_run"],
        })

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(SupabaseCycleStore, "_rpc", rpc)
    monkeypatch.setattr(SupabaseCycleStore, "refresh_edits", lambda self: None)
    monkeypatch.setattr(SupabaseCycleStore, "refresh_appliance_rules", lambda self: None)
    monkeypatch.setattr(SupabaseCycleStore, "fetch_reports", lambda self, *a: [saved])
    monkeypatch.setattr("energy_monitor.supabase_sync.sync_supabase_telemetry", lambda *a: 0)
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://preview.invalid")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "preview-server-only")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("ENERGY_MONITOR_EDIT_PASSWORD", "owner-fixture")
    monkeypatch.setenv("ENERGY_MONITOR_AI_REPORTS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    dashboard = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    monkeypatch.syspath_prepend(str(dashboard.parent))
    monkeypatch.setattr("components.render_cycle_assessment", assess)
    app = AppTest.from_file(str(dashboard), default_timeout=30)
    app.session_state["mode"] = "Reports"
    app.run()
    assert not app.exception
    assert not calls
    assert all(heading.value != "AI-assisted analysis" for heading in app.subheader)
    analysis_buttons = [b for b in app.button if "AI Analysis" in b.label]
    assert [b.label for b in analysis_buttons] == ["Generate AI Analysis"]
    assert not calls
    analysis_buttons[0].click().run()
    assert not app.exception
    assert all("AI Analysis" not in button.label for button in app.button)
    assert [heading.value for heading in app.subheader].count("AI-assisted analysis") == 1
    assert any(text.value == "**Performance summary**" for text in app.markdown)
    if count == 5:
        assert any("5 of 5 cycles" in text.value for text in app.markdown)
    else:
        assert any("The cycle ran for" in text.value for text in app.markdown)
    assert all("####" not in text.value for text in app.markdown if "cycles" in text.value)
    assert any("120 input tokens" in caption.value for caption in app.caption)
    if count == 5:
        assert len(app.get("plotly_chart")) >= 3
    assert "unavailable" in cloud.result["anomaly"]
    score_available = True
    app.run()
    assert not app.exception and len(calls) == 1
    assert [heading.value for heading in app.subheader].count("AI-assisted analysis") == 1
    if count == 1:
        comparison = next(text.value for text in app.markdown if "37.81 / 100" in text.value)
        assert "**2,000.0 W**" in comparison and "**36 verified cycles**" in comparison
        assert "**+89.0 W (+4.5%)**" in comparison
        assert not any("scores were unavailable" in t.value for t in app.markdown)
        assert "unavailable" in cloud.result["anomaly"]
