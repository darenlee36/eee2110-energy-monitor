# U3 â€” ESP32 sampling cadence reliability

Status: **PASS — requested USB-only simulated-telemetry U3 scope**. U1/U2 baseline: `d03f835` on
`codex/u1-esp32-telemetry-reliability`. No push, merge or deployment authorized.

## Diagnosis and minimal change

The U2 run delivered all 66 collected simulated readings, but recorded-uptime
intervals reached 13,621 ms (UTC seconds rounded the largest gap to 14 s).
`loop()` previously called `TelemetryUpload::poll()` synchronously. That called
TLS connection, HTTP POST and receipt-body reads; reconnect initiation was also
on the acquisition loop. Changing the nominal timer alone cannot unblock this I/O.

U3 retains that transport and receipt implementation, moving its sole consumer
into one built-in FreeRTOS task pinned to core 0, priority 1, 12,288-byte stack.
Arduino core 1 retains the sole reader, sequence allocator and serial control.
The existing FIFO/pending-count/flush state uses one standard-library mutex;
queue copying and acknowledged-prefix removal are short locked operations.
JSON serialization, receipt parsing and HTTP never hold the queue lock. The
network task exclusively owns frozen ID/body/retry state. No new package,
backend table, API, dashboard flow or general task framework was added.

`sampling_cadence.h` provides a small portable fixed-phase deadline calculation.
Late loops take one current measurement, count missed slots, and advance to the
next original deadline. No synthetic historical catch-up samples are fabricated.
Sample UTC and `device_uptime_ms` retain the existing API. Safe serial diagnostics
add actual interval, lateness, missed slots and HTTP duration. The first interval
is zero because no earlier reading exists. Sampling starts only after setup;
startup scanning is not claimed as a measured acquisition interval.

## Software verification

- Test-first: the cadence suite initially failed to compile because its new
  header did not exist; the uploader regression rejected the old copyable,
  unsynchronized delivery object. Both passed after implementation.
- Native PlatformIO: **16 passed** (3 cadence, 3 existing reader/FIFO, 10 delivery).
- Concurrent stress: 600 ordered readings through a 12-entry ring, repeated
  wraparound, slow mocked requests, offline polls, failed first attempts and
  replay receipts; final drain delivered exactly 1â€“600. This is native evidence,
  not measured ESP32 cadence or a physical lost-ack test.
- Blocked-send test permits concurrent append/full rejection/final-flush request,
  rejects enqueue after flush and preserves frozen prefix plus remaining tail.
- Existing U1 partial sizes 1â€“5, exact retry payload, invalid receipts, backoff,
  uptime wrap and full/partial final-drain tests remain passing.
- Relevant Python regressions: **32 passed**, models, cycles and Supabase sync.
  Ruff: clean. No Python production changes.
- Simulated `esp32dev` build: PASS. Static RAM 59,024/327,680 bytes; firmware
  949,073/1,310,720 bytes. This excludes dynamic task/TLS heap consumption.

## Physical setup and evidence boundary

User confirmed USB only / disconnected mains and prepared the FYP-Test hotspot.
COM3 identified as CP210x VID 10C4 / PID EA60, same ESP32-D0WD-V3 rev 3.1.
U3 simulated firmware is `sim-fw-0.2.4`; isolated device scope is
`sim-esp32-u3-20261102-a`. Working authentication was preserved.
The initial identity replacement expected a macro while the private header uses
constexpr; the first flash therefore retained the older U2 identity. It was
corrected and reflashed before the controlled U3 evidence run. No cloud rows
were deleted. Any startup/old-scope records outside the chosen run are excluded.

Controlled measured run began at 06:03:04 UTC (14:03:04 MYT), first sequence
46,000,001. The final sample was 46,000,133 at 06:14:11 UTC. Full results follow.
All values remain simulated, despite being acquired/sent by the physical ESP32.

## Remaining limits

- Physical electrical values are simulated; no PZEM, mains, calibration or
  model-training claim. The PZEM reader's own serial latency is not measured.
- This is best-effort five-second acquisition, not a hard real-time guarantee.
  Sensor calls, OS scheduling, serial backpressure and rare NVS writes may delay it.
- Queue capacity remains 120 RAM readings, roughly ten nominal minutes. A full
  queue explicitly defers acquisition; unsampled instants are not recovered.
- Sudden power loss/reset can lose unsent RAM data. Sequence-block allocation
  prevents identifier reuse, not telemetry loss.
- RTC/NTP clock corrections and long-duration/wraparound physical runs are not
  validated. Millisecond cadence must use uptime, not rounded UTC timestamps.
- Final flush proves receipt/drain only, not dashboard cycle closure or reports.
- OLED remains deferred until U3 review; fixed software milestone score remains
  60% until actual sensor flow/dataset/model/final evaluation gates are evidenced.


## Measured physical results — 2 November 2026

All times below are MYT (UTC+8). Cadence uses complete cloud `device_uptime_ms`
records at 1 ms resolution, not host arrival times or rounded UTC seconds.
Percentiles use nearest rank; the median is the conventional median.

| Check | Result | Evidence |
|---|---|---|
| Startup/time | PASS | Controlled reset 14:03:04; hotspot found 14:03:06, IP 14:03:07; first valid UTC sample 14:03:11. |
| Cadence during slow requests | PASS | 133 stored readings / 132 intervals over an 11-minute sample span; every recorded interval is 5,000 ms. HTTP requests took up to 9,628 ms while acquisition continued. |
| Offline retention/cadence | PASS | Disconnect observed 14:09:38, one upload failed with HTTP -1 after 4,015 ms. At 14:10:17 cloud still held 72 readings; queue later reached 36 in the log. 28 offline-tagged readings were recovered; all 27 internal offline intervals are 5,000 ms. |
| Reconnection/backlog | PASS | IP returned 14:12:01; retained six-reading request and backlog received 201 receipts. Sampling continued while backlog drained. |
| Queue/data integrity | PASS | Cloud contains exactly 46,000,001–46,000,133, unique and contiguous, matching all 133 expected fixed slots. 23 completed batches accepted 133 readings (22 x 6 + 1 x 1), zero stored duplicates; no other device wrote in the evidence window. |
| Immutable retry/receipt regressions | PASS, bounded evidence | Actual failed upload then successful retry observed. Native tests verify exact ID/body, replay and malformed receipts under concurrent appends. A new post-commit lost-ack/replay was not forced in U3; U2's historical physical replay is separate evidence. |
| Serial flush during HTTP | PASS | Sent 14:14:11.928, accepted 14:14:11.937 (8.505 ms host-log difference) while the six-reading HTTP request was still in flight until 14:14:13.716. Last one-reading batch acknowledged 14:14:20.896; final completion 14:14:20.905. |
| Stopped acquisition | PASS | No subsequent samples observed until monitor closed 14:16:59. Fresh cloud check at 14:17:03 still had exactly 133 unique rows, last recorded 14:14:11. |

### Interval comparison

| Metric | U2 measured baseline (65 intervals) | U3 measured run (132 intervals) |
|---|---:|---:|
| Minimum | 5,000 ms | 5,000 ms |
| Median | 5,000 ms | 5,000 ms |
| 95th percentile | 9,156 ms | 5,000 ms |
| 99th percentile | 13,621 ms | 5,000 ms |
| Worst gap | 13,621 ms | 5,000 ms |
| Mean | 5,783.585 ms | 5,000 ms |

No interval exceeded the five-second target at the stored millisecond resolution.
This finite run does not establish sub-millisecond precision or a universal
real-time guarantee. Zero missed slots were reported in captured diagnostics;
complete cloud timestamps/uptimes confirm no missed fixed slots in this run.

The sanitized serial capture contains **128 of 133 sample lines**. Its five absent
console lines are a capture/console-evidence limitation, not five missing cloud
readings. Their precise console omission cause was not investigated. All 128
captured sample uptimes match the corresponding cloud record; printed powers
match within 0.051 W rounding tolerance. The full 133-row cloud series, rather
than an assumption of complete serial capture, supplies cadence/sequence checks.

The command-to-accept timing is measured from laptop capture timestamps, so it
includes serial/host delivery and is not a device interrupt-latency measurement.
Aged partial sizes 1–5 remain native-tested and U2 size 5 remains physically
verified; U3 physically verifies the controlled final one-reading tail.

### Saved evidence

- `2026-11-02-u3-cloud-evidence.json`: 133 safe telemetry records, 23 batch
  receipts/hashes and a zero-other-device count. Batches have no direct per-row
  device foreign key; attribution uses the bounded window with no other writer.
- `2026-11-02-u3-comparison.json`: distribution, sequence/queue checks, native-vs-
  physical evidence boundaries and final-flush timings.
- `2026-11-02-u3-serial-safe.log`: controlled startup through final stopped-state
  observation, excluding unrecognized lines/credential values.
- Flashed private binary SHA-256:
  `27db66d2fb0a6b1ce9db9a3b8d6e3390955035389de9a3b23fae07f0a09ebbfa`.
  Binary and private configuration are ignored and are not committed.

### Reproduction commands

Use the installed PlatformIO environment and a space-free generated build path:

```powershell
$env:PATH = 'C:/msys64/ucrt64/bin;' + $env:PATH
$env:PLATFORMIO_BUILD_DIR = 'C:/pio-build/eee2110-u3-cadence'
$pio = 'C:/Users/Daren Lee/.platformio/penv/Scripts/pio.exe'
& $pio test -d software/firmware -e native
& $pio run -d software/firmware -e esp32dev -j 1
```

Python regressions, from `software`, using its existing virtual environment:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
./.venv/Scripts/python.exe -m pytest tests/test_models.py tests/test_cycles.py tests/test_supabase_sync.py -q
./.venv/Scripts/ruff.exe check src tests
```

USB-only bench sequence: confirm COM3/board identity and isolation; upload
`esp32dev`; capture at 115200 baud; collect normal batches; user disables hotspot
while USB remains powered; observe queued readings; restore hotspot; observe
backlog plus continued five-second acquisition; send `flush` plus newline during
an active upload; await final receipt/completion; compare bounded cloud sample
sequences and uptime intervals, then confirm stopped state. Do not replace these
physical steps with native simulations when claiming physical validation.

## Handover / review gate

U3 acceptance criteria pass for the requested physical USB ESP32 using simulated
telemetry: network blocking no longer produced the U2 cadence gap in this run,
all measured slots reached storage without duplicate/corrupted sequence entries,
and U1/U2 delivery/final-flush regressions passed. No firmware issue requiring a
further fix was found within this scope. The limitations above remain open.

Modified production areas are only the uploader synchronization, fixed-cadence
helper and main task split/diagnostics/version; tests and documentation accompany
them. No backend, dashboard, authentication or data-model implementation changed.
Nothing was pushed, merged or deployed. U3 may be recorded complete in the central
checkpoint tracker; the separate ten-milestone software score remains 60% because
this is still simulated electrical telemetry. OLED must wait for U3 review.
The user has completed the required hotspot off/on actions; no manual USB bench
step in this measured run was skipped. Genuine PZEM end-to-end measurements and
physical reader latency require a later approved integration checkpoint.


### File inventory

- `software/firmware/src/main.cpp`
- `software/firmware/include/telemetry_upload.h`
- `software/firmware/include/sampling_cadence.h` (new)
- `software/firmware/test/test_sampling_cadence/test_main.cpp` (new)
- `software/firmware/test/test_telemetry_upload/test_main.cpp`
- `software/README.md`
- `software/audits/2026-11-02-u3-sampling-cadence.md` (this handover)
- `software/audits/2026-11-02-u3-cloud-evidence.json`
- `software/audits/2026-11-02-u3-comparison.json`
- `software/audits/2026-11-02-u3-serial-safe.log`

Private `local_secrets.h` changed only the non-secret test-device identity.
It remains ignored; credentials and the firmware binary are not included.
