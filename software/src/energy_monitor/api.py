from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from energy_monitor.models import TelemetryBatch
from energy_monitor.storage import IdempotencyConflict, SQLiteTelemetryStore


def _error(code: str, message: str, details: Any | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return {"error": payload}


def create_server(
    store: SQLiteTelemetryStore, host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    class TelemetryHandler(BaseHTTPRequestHandler):
        server_version = "EnergyMonitorDevelopmentAPI/0.1"

        def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json(HTTPStatus.OK, {"data": {"status": "ok"}})
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                _error("NOT_FOUND", "The requested endpoint does not exist"),
            )

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/v1/telemetry/batches":
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    _error("NOT_FOUND", "The requested endpoint does not exist"),
                )
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body)
            except (ValueError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    _error("INVALID_JSON", "Request body must be valid JSON"),
                )
                return

            try:
                batch = TelemetryBatch.model_validate(payload)
            except ValidationError as exc:
                details = [
                    {"location": list(error["loc"]), "message": error["msg"]}
                    for error in exc.errors()
                ]
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    _error("VALIDATION_ERROR", "Telemetry batch is invalid", details),
                )
                return

            idempotency_key = self.headers.get("Idempotency-Key")
            if idempotency_key != str(batch.batch_id):
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    _error(
                        "IDEMPOTENCY_KEY_MISMATCH",
                        "Idempotency-Key must match the batch_id in the request body",
                    ),
                )
                return

            try:
                result = store.insert_batch(batch)
            except IdempotencyConflict:
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    _error(
                        "IDEMPOTENCY_CONFLICT",
                        "The batch_id was already used for a different request body",
                    ),
                )
                return

            status = HTTPStatus.OK if result.replayed else HTTPStatus.CREATED
            self._send_json(status, {"data": result.model_dump(mode="json")})

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), TelemetryHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local telemetry ingestion API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=Path("data/telemetry.db"))
    args = parser.parse_args()

    server = create_server(SQLiteTelemetryStore(args.database), args.host, args.port)
    print(f"Local telemetry API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

