# U1 — ESP32 Telemetry Reliability

Verified 2 November 2026. Development-only checkpoint; no flash, live upload,
backend change, deployment, merge or push was performed.

## Source and scope

- Remote main refreshed before starting: `5b2c8c331fc89c04613d8778cb20a95b1b0b3ba0`.
- Isolated branch: `codex/u1-esp32-telemetry-reliability`.
- Existing workspace preserved; the reporting-worker repair note on its local branch
  is not part of U1. Its separately reported missing executable remains outside scope.
- Inspected existing firmware, queue and Edge Function. The Edge Function already
  accepts 1–6 readings and returns a batch ID, accepted/duplicate counts and replay flag.
- Reused ArduinoJson and the existing fixed RAM queue; no new runtime package or
  cloud schema/service was introduced. The portable delivery header permits native
  tests of the actual serializer, retry state and receipt validator without Arduino
  Wi-Fi hardware. `main.cpp` keeps TLS, authentication, reader selection and timing.

## Acceptance evidence

| Requirement | Result |
| --- | --- |
| Normal six-reading batching plus timed partial batches | Full batches send normally; 1–5 readings become eligible at oldest-reading age 30 seconds, subject to connectivity/backoff. Native tests cover all five partial sizes and deadline boundaries. |
| Same batch ID, count and payload on retry | Serialized once and retained until verified receipt. New samples queue behind the pending prefix. Lost-response test compares exact ID and JSON bytes after new samples arrive. |
| Verify acknowledgement before queue removal | Only HTTP 200/201 plus a parseable matching batch ID, nonnegative integer counts totalling the pending count and boolean replay flag release that prefix. Wrong/empty/malformed/incomplete receipts leave it pending. |
| Wi-Fi loss, failed requests and duplicate-safe retry | Offline polls make no send/removal. Failure retains the snapshot. Retry delay doubles from one to at most sixty seconds. Replayed 200 and accepted/duplicate 201 receipts are covered. No backend changes. |
| Explicit final flush | `requestFinalFlush()` stops enqueueing; the serial `flush` newline command stops sampling in `main.cpp`, drains full/partial batches and prints completion only after the queue is acknowledged. Native tests cover empty, offline, multi-batch and failed-tail drains; serial binding compiled, not device-tested. |
| Native tests and simulated ESP32 build | 11 native tests pass (3 existing, 8 new). `esp32dev` build passes without private credentials or flashing. |

## Fresh verification

From `software/firmware`, installed PlatformIO, separate generated-build directory
`C:/pio-build/eee2110-u1-reliability`:

```text
pio test -e native
11 test cases: 11 succeeded

pio run -e esp32dev -j 1
SUCCESS
RAM: 58,972 / 327,680 bytes (18.0%)
Flash: 945,785 / 1,310,720 bytes (72.2%)
```

Simulated firmware binary SHA-256:
`4b0fb814c9ddd5b0d16f0979f5ce2cf1f94a5bcf7b3c6e72a072d80e93b8e0cd`.

Fresh Python regression: **226 passed**, eight existing Streamlit/NumPy deprecation
warnings. Ruff: **All checks passed**. Git diff whitespace check clean.

The first Python run had 224 passes and two scheduler-plan failures because the new
worktree did not contain its expected `.venv` path. An ignored local junction reuses
the existing environment; the full rerun passed. No scheduler source or native task
settings were changed to achieve this.

Test-first evidence: new native suite initially could not build because the delivery
module did not exist. After implementation, a mutation restored the six-only gate:
four reliability assertions failed (and the native failed-test process ended with
SIGILL). The fix was immediately restored; all 11 final native tests passed. The
first ESP32 build also exposed the platform's non-const raw POST overload; using
its existing String overload resolved that compile error. Final build passed.

## Operator handoff and limitations

1. Allow the cycle's required idle readings to be collected before requesting final
   flush; stopping sampling too early can prevent software cycle closure.
2. Send `flush` plus newline at 115200 baud. Wait for
   `Final flush complete: all queued readings acknowledged by server.`
3. Verify the completed cycle separately in Streamlit. Upload acknowledgement does
   not certify cycle completeness, report generation, sensor calibration or model
   performance. Reboot starts the next measurement after final-flush mode.

Queue and request state are **RAM only**. Sudden power loss/reset can lose unsent
readings and the pending request. Persistent sequence reservation prevents reuse
of identifiers, not data loss. At capacity, new readings are deferred, not invented.
An invalid payload or permanent authentication/storage rejection remains pending
and needs operator correction; it is never silently discarded to empty the queue.

HTTP is synchronous, so a slow request can delay the nominal five-second sampling
cadence. The 30-second eligibility threshold is not a delivery guarantee while
offline or retrying. Physical Wi-Fi interruption/serial flush, live Supabase replay,
real PZEM compatibility/calibration and an end-to-end genuine cycle remain later
integration checks. OLED/UI flush controls and flash persistence are outside U1.

U1 meets its requested implementation/native-test/simulated-build scope. It does
not complete software milestone 7 or justify increasing the fixed software score
above 60%. No real-sensor or trained-model evidence was created.
