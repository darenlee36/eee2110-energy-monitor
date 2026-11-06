"""Public release tests use approved summaries and synthetic quality fixtures."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from energy_monitor.anomaly import CycleAnomalyModel
from energy_monitor.cycles import detect_cycles
from energy_monitor.models import CycleDetectionSettings
from energy_monitor.pilot import (
    PUBLIC_REFERENCE_PATH,
    load_pilot_reference,
    pilot_parameters,
    pilot_reference,
    pilot_reference_member,
    pilot_score,
)
from energy_monitor.simulator import generate_cycle_scenario


@pytest.fixture
def reference():
    return load_pilot_reference()


def report_cycle(reference, index=0):
    member = reference["cycles"][index]
    return {
        **member["features"], "cycle_id": member["cycle_id"],
        "device_id": "sim-esp32-u3-20261102-a", "profile_id": "tefal-kettle",
        "data_origin": "sensor", "record_source": "measured", "status": "completed",
    }


def quality_fixture():
    samples = [r.model_copy(update={"firmware_version": "pzem-fw-fixture"})
               for r in generate_cycle_scenario("normal", datetime(2026, 11, 5, tzinfo=UTC))]
    cycle, = detect_cycles(samples, CycleDetectionSettings.simulation_defaults())
    trace = [r for r in samples if cycle.start_sequence <= r.sample_sequence <= cycle.end_sequence]
    predecessor = next(r for r in samples if r.sample_sequence == cycle.start_sequence - 1)
    def row(r):
        return {**r.model_dump(mode="json"),
                "received_at": (r.timestamp + timedelta(seconds=5)).isoformat()}
    return {"cycle": {**cycle.model_dump(mode="json"), "record_source": "measured"},
            "annotation": {"label": "Complete"}, "readings": [row(r) for r in trace],
            "predecessor": row(predecessor)}


def test_all_six_frozen_scores_and_medians(reference):
    assert reference["baseline_id"] == "pilot-deviation-v1-f1f5564e68e2"
    assert sum(c["sample_count"] for c in reference["cycles"]) == 431
    assert reference["parameters"]["duration_seconds"]["median"] == 220.5
    assert reference["parameters"]["duration_seconds"]["mad"] == 15
    assert reference["parameters"]["average_power_w"]["median"] == pytest.approx(1801.295144)
    scores = [pilot_reference_member(reference, report_cycle(reference, i))["pilot"]["score"]
              for i in range(6)]
    assert scores == pytest.approx([35.358, 5.162, 5.930, 20.470, 57.112, 16.474], abs=0.01)
    for i in range(6):
        result = pilot_reference_member(reference, report_cycle(reference, i))["pilot"]
        assert sum(c["weighted_points"] for c in result["contributions"].values()
                   ) == pytest.approx(result["score"])


def test_reference_numbers_from_energy_derived_power(reference):
    values = [{"duration_seconds": c["features"]["duration_seconds"],
               "average_power_w": c["energy_kwh"] * 3_600_000 /
               c["features"]["duration_seconds"]} for c in reference["cycles"]]
    assert [round(pilot_score(v, pilot_parameters(values))["score"])
            for v in values] == [36, 8, 7, 22, 59, 17]


def test_bounds_and_invalid_values():
    params = pilot_parameters([{"duration_seconds": 200, "average_power_w": 1800}] * 6)
    assert pilot_score({"duration_seconds": 200, "average_power_w": 1800}, params)["score"] == 0
    assert pilot_score({"duration_seconds": 2000, "average_power_w": 1800}, params)["score"] == 100
    for value in (float("nan"), float("inf"), -1):
        with pytest.raises(ValueError):
            pilot_score({"duration_seconds": value, "average_power_w": 1800}, params)


@pytest.mark.parametrize("key,value", [
    ("device_id", "other-meter"), ("profile_id", "other-profile"),
    ("data_origin", "simulated"), ("record_source", "manual"),
    ("status", "active"), ("cycle_id", "00000000-0000-4000-8000-000000000000"),
])
def test_report_outside_reference_scope_is_unavailable(reference, key, value):
    cycle = report_cycle(reference)
    cycle[key] = value
    assert pilot_reference_member(reference, cycle) is None


def test_changed_report_features_are_rejected(reference):
    cycle = report_cycle(reference)
    cycle["duration_seconds"] += 30
    with pytest.raises(ValueError, match="differs"):
        pilot_reference_member(reference, cycle)


def test_artifact_tampering_is_rejected(tmp_path):
    raw = json.loads(PUBLIC_REFERENCE_PATH.read_text())
    raw["parameters"]["duration_seconds"]["median"] = 1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_pilot_reference(path)


def test_no_raw_measurements_notes_or_keys_are_published(reference):
    content = PUBLIC_REFERENCE_PATH.read_text()
    for forbidden in ("readings", "annotation_note", "DEVICE_INGEST_TOKEN",
                      "service_role", "received_at", "sample_sequence"):
        assert forbidden not in content
    assert reference["independently_reviewed"] is False
    assert CycleAnomalyModel().status == "not_trained"


@pytest.mark.parametrize("label", ["Incomplete", "Unlabelled", "", "Normal"])
def test_only_complete_annotations_are_eligible(label):
    item = quality_fixture()
    item["annotation"]["label"] = label
    with pytest.raises(ValueError, match="Complete"):
        pilot_reference(item)


@pytest.mark.parametrize("fault", ["missing", "scope", "invalid", "energy", "predecessor"])
def test_raw_quality_gates_remain_in_force(fault):
    item = quality_fixture()
    if fault == "missing":
        item["readings"].pop(10)
    elif fault == "scope":
        item["readings"][10]["device_id"] = "other"
    elif fault == "invalid":
        item["readings"][10]["quality_status"] = "communication_gap"
    elif fault == "energy":
        item["cycle"]["meter_energy_kwh"] += .01
    else:
        item.pop("predecessor")
    with pytest.raises(ValueError):
        pilot_reference(item)


def test_cycle_display_leads_with_rolling_deviation_and_keeps_experimental_scores():
    cycle_template = report_cycle(load_pilot_reference())
    script = f"""
from dashboard.components import render_cycle_assessment
from energy_monitor.pilot import load_pilot_reference
ref = load_pilot_reference()
cycle = {cycle_template!r}
for record in ref['cycles']:
    selected = {{**cycle, **record['features'], 'cycle_id': record['cycle_id']}}
    render_cycle_assessment(selected, None)
"""
    app = AppTest.from_string(script, default_timeout=10).run()
    assert not app.exception
    values = [m.value for m in app.metric if m.label == "Isolation Forest Anomaly Score"]
    assert values == ["48.66 / 100", "44.76 / 100", "45.54 / 100", "45.75 / 100",
                      "61.00 / 100", "45.43 / 100"]
    assert [m.value for m in app.metric if m.label.startswith("Current rolling baseline")] == [
        "1,862.4 W"
    ] * 6
    assert app.metric[0].label == "Power deviation from rolling baseline"
    assert app.metric[0].value == "-2.7%"
    assert len(app.dataframe) == 6
    assert app.expander[0].label == "How this rolling comparison was calculated"
    assert app.expander[1].label == "Experimental Isolation Forest details"
    assert any("−100 × (-0.486551110) = 48.66" in text.value for text in app.markdown)
    assert any("Training-reference cycle" in c.value for c in app.caption)


def test_rolling_insights_use_the_same_earlier_cycles_as_the_display():
    from dashboard.components import rolling_power_insights
    from energy_monitor.experimental_if import current_rolling_power_comparison, load_release

    _, release = load_release()
    high_power = list(release["assessments"].values())[-1]
    rolling = current_rolling_power_comparison(high_power, release, None)
    power_messages = " ".join(rolling_power_insights(
        {"average_voltage_v": 242.446}, rolling,
    ))
    assert "current mean of 10 verified cycles" in power_messages
    assert "+285.9 W (+15.4%)" in power_messages
    assert "242.4 V" in power_messages

