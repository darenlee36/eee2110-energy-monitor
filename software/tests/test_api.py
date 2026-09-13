from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
from test_models import valid_reading

from energy_monitor.api import create_server
from energy_monitor.dashboard_queries import get_live_view
from energy_monitor.models import TelemetryBatch
from energy_monitor.simulator import batch_readings, generate_cycle_scenario
from energy_monitor.storage import SQLiteTelemetryStore


def make_payload(batch_id: str, start_sequence: int = 1) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "readings": [
            valid_reading(sample_sequence=start_sequence + offset) for offset in range(2)
        ],
    }


def start_test_server(tmp_path: Path) -> tuple[object, str]:
    store = SQLiteTelemetryStore(tmp_path / "api.db")
    return start_test_server_with_store(store)


def start_test_server_with_store(store: SQLiteTelemetryStore) -> tuple[object, str]:
    server = create_server(store, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_api_accepts_batch_and_replays_same_request(tmp_path: Path) -> None:
    server, base_url = start_test_server(tmp_path)
    batch_id = "55555555-5555-4555-8555-555555555555"
    headers = {"Idempotency-Key": batch_id}
    try:
        first = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers=headers,
            json=make_payload(batch_id),
        )
        replay = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers=headers,
            json=make_payload(batch_id),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert first.status_code == 201
    assert first.json()["data"] == {
        "batch_id": batch_id,
        "accepted": 2,
        "duplicates": 0,
        "replayed": False,
    }
    assert replay.status_code == 200
    assert replay.json()["data"]["replayed"] is True


def test_api_rejects_idempotency_key_reused_with_new_body(tmp_path: Path) -> None:
    server, base_url = start_test_server(tmp_path)
    batch_id = "66666666-6666-4666-8666-666666666666"
    headers = {"Idempotency-Key": batch_id}
    try:
        first = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers=headers,
            json=make_payload(batch_id, start_sequence=1),
        )
        conflict = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers=headers,
            json=make_payload(batch_id, start_sequence=20),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert first.status_code == 201
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_api_rejects_malformed_json_with_consistent_error(tmp_path: Path) -> None:
    server, base_url = start_test_server(tmp_path)
    try:
        response = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers={"Content-Type": "application/json"},
            content=b"{broken",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "INVALID_JSON", "message": "Request body must be valid JSON"}
    }


def test_api_rejects_header_and_body_batch_id_mismatch(tmp_path: Path) -> None:
    server, base_url = start_test_server(tmp_path)
    try:
        response = httpx.post(
            f"{base_url}/api/v1/telemetry/batches",
            headers={"Idempotency-Key": "77777777-7777-4777-8777-777777777777"},
            json=make_payload("88888888-8888-4888-8888-888888888888"),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"


def test_payload_fixture_remains_valid() -> None:
    batch = TelemetryBatch.model_validate(
        make_payload("99999999-9999-4999-8999-999999999999")
    )
    assert len(batch.readings) == 2


def test_simulated_esp32_batches_reach_dashboard_cycle_view(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "cycle-api.db")
    server, base_url = start_test_server_with_store(store)
    readings = generate_cycle_scenario(
        "normal", datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    )
    try:
        for batch in batch_readings(readings):
            response = httpx.post(
                f"{base_url}/api/v1/telemetry/batches",
                headers={"Idempotency-Key": str(batch.batch_id)},
                json=batch.model_dump(mode="json"),
            )
            assert response.status_code == 201
    finally:
        server.shutdown()
        server.server_close()

    cycles = store.fetch_cycles()
    assert len(cycles) == 1
    assert cycles[0]["status"] == "completed"
    view = get_live_view(store, now=readings[-1].timestamp)
    assert view.last_cycle is not None
    assert view.last_cycle.energy_kwh == cycles[0]["meter_energy_kwh"]
    assert view.latest_voltage_v == readings[-1].voltage_v


def test_api_replay_keeps_one_cycle(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "replay-api.db")
    server, base_url = start_test_server_with_store(store)
    batches = batch_readings(
        generate_cycle_scenario("normal", datetime(2026, 9, 12, 6, 0, tzinfo=UTC))
    )
    try:
        for batch in batches:
            for _ in range(2):
                response = httpx.post(
                    f"{base_url}/api/v1/telemetry/batches",
                    headers={"Idempotency-Key": str(batch.batch_id)},
                    json=batch.model_dump(mode="json"),
                )
                assert response.status_code in {200, 201}
    finally:
        server.shutdown()
        server.server_close()

    assert len(store.fetch_cycles()) == 1


def test_api_stores_incomplete_gap_cycle(tmp_path: Path) -> None:
    store = SQLiteTelemetryStore(tmp_path / "gap-api.db")
    server, base_url = start_test_server_with_store(store)
    readings = generate_cycle_scenario(
        "incomplete_gap", datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    )
    try:
        for batch in batch_readings(readings):
            response = httpx.post(
                f"{base_url}/api/v1/telemetry/batches",
                headers={"Idempotency-Key": str(batch.batch_id)},
                json=batch.model_dump(mode="json"),
            )
            assert response.status_code == 201
    finally:
        server.shutdown()
        server.server_close()

    assert store.fetch_cycles()[0]["status"] == "incomplete"
