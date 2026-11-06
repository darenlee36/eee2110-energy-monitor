"""MYT metadata filtering; no production writes or raw traces for history queries."""
import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest
from test_dashboard import DASHBOARD_PATH, build_dashboard_cycle_database
from test_dashboard_queries import populated_store

from energy_monitor.dashboard_queries import cycle_start_matches, list_cycle_items
from energy_monitor.storage import SQLiteTelemetryStore

NOW = datetime(2026, 11, 5, 18, tzinfo=UTC)  # 6 November, 02:00 MYT


@pytest.mark.parametrize("preset,stamp,expected", [
    ("Today", "2026-11-05T16:00:00Z", True),
    ("Today", "2026-11-05T15:59:59Z", False),
    ("Yesterday", "2026-11-05T15:59:59Z", True),
    ("Last 7 days", "2026-10-30T16:00:00Z", True),
    ("Last 7 days", "2026-10-30T15:59:59Z", False),
    ("Last 7 days", "2026-11-06T16:00:00Z", False),
    ("All dates", "2020-01-01T00:00:00Z", True),
])
def test_relative_dates_use_malaysia_day(preset, stamp, expected):
    assert cycle_start_matches(stamp, date_filter=preset, now=NOW) is expected


def test_custom_dates_and_minute_window_are_start_inclusive_end_exclusive():
    options = dict(date_filter="Custom date range", date_start=date(2026, 11, 5),
                   date_end=date(2026, 11, 5), time_start=time(13), time_end=time(15))
    for stamp, expected in [
        ("2026-11-05T05:00:00Z", True), ("2026-11-05T06:59:59Z", True),
        ("2026-11-05T07:00:00Z", False), ("2026-11-05T04:59:59Z", False),
        ("2026-11-04T06:00:00Z", False),
    ]:
        assert cycle_start_matches(stamp, **options) is expected
    # Legacy naive input means UTC; already-MYT input is not converted twice.
    assert cycle_start_matches(datetime(2026, 11, 5, 5), **options)
    assert cycle_start_matches("2026-11-05T13:00:00+08:00", **options)
    options.update(date_end=date(2026, 11, 6), time_start=None, time_end=None)
    assert cycle_start_matches("2026-11-06T15:59:59Z", **options)
    assert not cycle_start_matches("2026-11-06T16:00:00Z", **options)


@pytest.mark.parametrize("options", [
    {"date_filter": "Custom date range"},
    {"date_filter": "Custom date range", "date_start": date(2026, 11, 6),
     "date_end": date(2026, 11, 5)},
    {"time_start": time(23), "time_end": time(1)},
    {"time_start": time(13), "time_end": time(13)},
    {"time_start": time(13)},
])
def test_invalid_and_overnight_ranges_are_explicit(options):
    with pytest.raises(ValueError):
        cycle_start_matches("2026-11-05T05:00:00Z", **options)


def test_combined_filters_and_full_history_do_not_load_traces(tmp_path, monkeypatch):
    store = populated_store(tmp_path)
    cycle = store.fetch_cycles()[0]
    store.add_cycle_label("Complete")
    store.save_cycle_label(cycle["cycle_id"], "Complete")
    monkeypatch.setattr(store, "fetch_cycle_readings",
                        lambda *a: pytest.fail("filtering must not load raw traces"))
    options = dict(date_filter="Custom date range", date_start=date(2026, 9, 12),
                   date_end=date(2026, 9, 12), time_start=time(14), time_end=time(15),
                   status_filter="normal", label_filter="Complete", source_filter="simulated")
    assert len(list_cycle_items(store, **options)) == 1
    assert not list_cycle_items(store, **{**options, "source_filter": "sensor"})
    assert not list_cycle_items(store, **{**options, "time_start": time(13), "time_end": time(14)})


def test_history_over_ten_thousand_is_not_truncated(tmp_path):
    store = populated_store(tmp_path)
    with store._connect() as conn:
        source = conn.execute("SELECT * FROM appliance_cycles LIMIT 1").fetchone()
        columns = source.keys()
        rows = []
        for index in range(10_005):
            row = dict(source)
            row["cycle_id"] = str(uuid4())
            row["start_sequence"] = 1_000_000 + index * 100
            row["end_sequence"] = row["start_sequence"] + 29
            row["started_at"] = (datetime(2020, 1, 1, tzinfo=UTC)
                                 + timedelta(days=index)).isoformat()
            summary = json.loads(row["summary_json"])
            summary.update(cycle_id=row["cycle_id"], started_at=row["started_at"],
                           start_sequence=row["start_sequence"], end_sequence=row["end_sequence"],
                           ended_at=(datetime.fromisoformat(row["started_at"])
                                     + timedelta(seconds=summary["duration_seconds"])).isoformat())
            row["summary_json"] = json.dumps(summary)
            rows.append(tuple(row[c] for c in columns))
        conn.executemany(f"INSERT INTO appliance_cycles ({','.join(columns)}) VALUES "
                         f"({','.join('?' for _ in columns)})", rows)
    assert len(store.fetch_cycles(limit=None)) == 10_006
    # Query returns all metadata, not the old 100-row page.
    assert len(list_cycle_items(store)) == 10_006
    assert len(list_cycle_items(
        store, date_filter="Custom date range",
        date_start=date(2020, 1, 1), date_end=date(2020, 1, 1))) == 1


def test_complete_sensor_cycle_numbers_ignore_filters_and_skip_incomplete(tmp_path, monkeypatch):
    store = populated_store(tmp_path)
    source = store.fetch_cycles()[0]
    rows = [
        {**source, "cycle_id": str(uuid4()), "started_at": f"2026-11-0{day}T06:00:00+00:00",
         "data_origin": "sensor", "record_source": "measured", "custom_label": label}
        for day, label in [(5, "Complete"), (4, "Incomplete"), (3, "Complete")]
    ]
    monkeypatch.setattr(store, "fetch_cycles", lambda **kwargs: rows)
    assert [row.cycle_number for row in list_cycle_items(store)] == [2, None, 1]
    visible = list_cycle_items(store, date_filter="Custom date range",
                               date_start=date(2026, 11, 5), date_end=date(2026, 11, 5))
    assert [row.cycle_number for row in visible] == [2]


def test_filter_reruns_preserve_selection_annotations_and_reset(tmp_path, monkeypatch):
    database = build_dashboard_cycle_database(tmp_path)
    monkeypatch.setenv("ENERGY_MONITOR_SOURCE", "local")
    monkeypatch.setenv("ENERGY_MONITOR_DB", str(database))
    store = SQLiteTelemetryStore(database)
    identity = store.fetch_cycles()[0]["cycle_id"]
    store.save_cycle_annotation(identity, label="Complete", note="Preserve this note",
                                expected_id=None)
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=15)
    app.session_state["mode"] = "Cycles"
    app.run()
    assert not app.exception
    assert app.session_state["selected_cycle_id"] == identity
    app.selectbox(key="cycle_date_filter").set_value("Custom date range").run()
    app.date_input(key="cycle_date_start").set_value(date(2026, 9, 12)).run()
    app.date_input(key="cycle_date_end").set_value(date(2026, 9, 12)).run()
    assert app.session_state["selected_cycle_id"] == identity
    app.date_input(key="cycle_date_start").set_value(date(2026, 9, 13)).run()
    assert any("end date" in w.value for w in app.warning)
    app.button(key="reset_cycle_filters").click().run()
    assert not app.exception
    assert app.session_state["selected_cycle_id"] == identity
    assert app.selectbox(key="cycle_date_filter").value == "All dates"
    assert store.fetch_cycle_annotation(identity)["note"] == "Preserve this note"
    assert any(b.label == "Save annotation" for b in app.button)
