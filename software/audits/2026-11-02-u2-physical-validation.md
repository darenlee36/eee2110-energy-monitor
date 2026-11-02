# U2 — physical ESP32 reliability validation

## Final result — PASSED for USB-only simulated telemetry

All eight listed U2 checks now have physical ESP32 evidence. The failed initial
network attempts below are retained as history and superseded by the successful
run using the earlier working hotspot name. No genuine PZEM or mains measurements
were taken. This result does not complete real-sensor software milestone 7.

| Test | Final result | Physical evidence (2 November, MYT) |
| --- | --- | --- |
| U2.1 Startup | PASS | Controlled USB startup at 13:12:17; hotspot found at 13:12:19, IP assigned at 13:12:20, valid UTC samples from 13:12:22. |
| U2.2 Normal uploads | PASS | First two six-reading batches returned 201 at 13:12:57 and 13:13:30; cloud contained the corresponding 12 unique sequences. |
| U2.3 Timed partial uploads | PASS | Five-reading batch 42000055–42000059 uploaded at 13:18:05, before any flush command. Oldest sample was collected at 13:17:28; a batch of five had aged past the 30-second threshold. This physically covers size 5, not every partial size. |
| U2.4 Wi-Fi interruption | PASS | User disabled hotspot sharing without resetting USB power. Failed requests retained the pending prefix; queue grew to 38. Cloud later recovered 31 readings recorded with offline connection state. |
| U2.5 Reconnection | PASS | User restored sharing; IP assigned at 13:16:48 and pending upload succeeded at 13:16:57. Backlog drained without a device reset. |
| U2.6 Retry integrity | PASS | Server completed a six-reading batch at 13:14:03, but ESP32 reported timeout -11 at 13:14:09 and kept it pending. After reconnection, HTTP 200 replay released it. Cloud sequences remain unique and contiguous. Current deployed function v4 checks raw-body SHA-256 and returns 422 on same-ID/different-body conflict, so successful replay also checks retry identity/body integrity under that contract. |
| U2.7 Serial final flush | PASS | Serial command sent at 13:18:40; sampling stopped, last single reading returned 201 at 13:18:47, then completion printed. No samples followed the flush request during the remaining observation. |
| U2.8 Cloud verification | PASS | All 66 serial sequences exactly match cloud records, 42000001–42000066, no sequence gaps or duplicates. Printed power values match stored values within 0.051 W (serial rounds to 0.1 W). Twelve completed batches accepted 66 readings; no other device wrote in the evidence window. |

### Evidence and tested configuration

- U1 production commit remains `2290be7`; no production source changes for U2.
- Board: ESP32-D0WD-V3 revision 3.1, CP210x on COM3, isolated USB power only.
- Firmware: `sim-fw-0.2.3`, PlatformIO `esp32dev`, sampling interval nominally 5 s.
- Successful configured binary SHA-256:
  `07f357292129244ba980260cab0d87e999373ddaf7a8c21be4cceca5bc581300`.
- Separate synthetic identity: `sim-esp32-u2-20261102-a`; no AI reports invoked.
- Existing password, device token, backend schema/authentication and dashboard retained.
  Only the ignored test configuration's hotspot/device identity fields were changed.
- [Sanitized serial capture](2026-11-02-u2-serial-safe.log).
- [Read-only cloud evidence](2026-11-02-u2-cloud-evidence.json).
- [Serial/cloud comparison](2026-11-02-u2-comparison.json).
- Ingestion batch sizes: ten batches of 6, one timed batch of 5, one final batch of 1.
  One successful HTTP 200 replay is not a new ingestion batch or six additional rows.
- Lost-ack batch: `c9089e0f-3181-42d1-99ee-88ca22898afa`, accepted 6,
  completed 13:14:03 MYT; timed partial batch:
  `11d158c9-cdc3-4339-9c77-bc118f9f694a`; final batch:
  `63ead439-dfc5-40dc-8ed9-bacb7a392c06`.

### Observed limitations and next gate

- RAM retention across a Wi-Fi outage passed; sudden power-loss/reset persistence
  is neither implemented nor proved. The optional full-flash backup did not complete.
- Synchronous HTTP blocks nominal five-second sampling: the largest recorded gap
  was 14 seconds. Sequence continuity proves delivery of collected readings, not
  that every scheduled sampling instant was collected. A strict 5-second cadence
  would require a separate change and physical evaluation; do not hide this limitation.
- Timed size 5 and explicit-flush size 1 were observed physically. Other partial sizes,
  malformed receipt variants, queue overflow and long outages retain native-test
  evidence only. One board/hotspot run is not an endurance or RF compatibility study.
- The longer restored phone name did not become visible in these attempts; the
  previously working ASCII name did. This does not prove a general apostrophe ban
  or identify the exact name/visibility cause. Keep the known-working test setting.
- Upload/flush acknowledgement does not prove Streamlit cycle closure, report
  generation, measurement calibration or anomaly-model performance. None was claimed.
- Public deployment/main and the separate scheduled-reporting task were unchanged.
  Its previously reported executable-path fault remains outside U2.
- U2 can be checked off in the central unified tracker. Under the established ten
  software milestones, software remains 60% because real PZEM flow is unverified;
  no new numeric unified percentage is inferred from this test alone.

## Historical preparation and attempts

## Preparation checkpoint — 2 November 2026

U1 branch `codex/u1-esp32-telemetry-reliability`, commit `2290be7`, confirmed.
Working tree was clean before this evidence record. Simulated PlatformIO environment
is `esp32dev`, board `esp32dev`, `TELEMETRY_SIMULATED=1`, serial 115200 baud.
The U1 simulated binary exists, but its build did not use private network configuration.
Existing private configuration is present in the previous development checkout;
credential values were not read or printed during preparation.

Windows CIM, present Ports devices and pyserial enumeration agree: only Bluetooth
COM5/COM6 are present. No USB ESP32 serial interface is currently detected. No port
was opened; no device was reset, flashed or tested. No cloud rows were written or
AI reporting invoked by this preparation. No mains/PZEM testing is authorized here.

| Test | Result | Physical evidence / requirement |
| --- | --- | --- |
| U2.1 Startup | NOT TESTED | Requires USB-connected board, boot log, Wi-Fi connection and valid timestamps. |
| U2.2 Normal uploads | NOT TESTED | Requires device-produced six-reading batches and matching cloud rows/receipts. |
| U2.3 Timed partial uploads | NOT TESTED | Requires device-produced 1–5-reading batch triggered by age, not by explicit final flush. |
| U2.4 Wi-Fi interruption | NOT TESTED | Requires observed physical disconnection and retained queue evidence. |
| U2.5 Reconnection | NOT TESTED | Requires restored Wi-Fi and observed queue drain. |
| U2.6 Retry integrity | NOT TESTED | Requires physical failed/repeated request, matching retry identity and cloud duplicate check; native tests alone do not establish this. |
| U2.7 Serial final flush | NOT TESTED | Requires serial flush command, stopped sampling and acknowledged queue drain. |
| U2.8 Cloud verification | NOT TESTED | Requires matching physical-device sequences, sample counts and ingestion batch receipts. |

Verdict: **NOT TESTED / physical participation required**, not PASSED or PARTIALLY
PASSED. Zero of eight physical tests have been executed. Software score remains
60%; no unified progress-score increase is justified by preparation.

## Next physical step

User connects the standalone ESP32 to the laptop using a data-capable USB cable,
with all external module/power connections removed and all mains circuitry
disconnected. Confirm only USB is attached, then enumerate the new port and identify
the board before any firmware read/flash. Preserve working authentication settings.

## Test-design caveats to resolve after connection

- Continuous five-second sampling normally fills six readings before the oldest
  reaches 30 seconds. A final-flush upload is immediate and does not independently
  prove the age-triggered path. U2.3 needs a controlled physical condition that
  leaves a partial queue ageing; do not substitute a native-test pass or mislabel
  explicit final flush as the timer test.
- Wi-Fi loss alone does not prove a committed upload's acknowledgement was lost.
  U2.6 must distinguish interrupted-before-send from same-ID post-commit replay.
- Normal serial output contains batch size/status but not batch IDs or payloads.
  Correlate cloud evidence carefully; do not assert byte-identical physical retry
  without adequate evidence. Any necessary diagnostics must exclude credentials.
- Queues are RAM-only. USB removal/reset is not a persistence test that U1 promises
  to pass. A firmware flush receipt is not dashboard cycle completion.

## Physical attempt 1 — board connected, awaiting hotspot visibility

The preparation section above is historical; the following observations supersede
its disconnected-board status.

- User confirmed readiness for the USB-only/hotspot step. Newly detected COM3 is
  a CP210x USB UART bridge. Read-only chip query identified ESP32-D0WD-V3 revision
  3.1, 40 MHz crystal; appropriate for the existing esp32dev target.
- Optional full-flash backup was stopped because it progressed too slowly within
  the test window. No usable full backup was produced. That read did not write flash;
  prior firmware source and existing private configuration remain available.
- Reused existing Wi-Fi, ingestion URL and device token privately. Changed only the
  ignored test configuration's device ID to `sim-esp32-u2-20261102-a` to separate
  these explicitly synthetic measurements from old device history/report workers.
  Both private configuration and backup folder are Git-ignored. No key regeneration.
- Configured simulated build and physical COM3 upload succeeded, with flash hashes
  verified by esptool. Firmware remains U1 `sim-fw-0.2.3`; no U1 production source
  modifications were made for this attempt.
- Sanitized physical startup capture at 12:59:30 MYT: boot succeeds, simulated reader
  starts, scan lists 8 networks but saved network is not found. Repeated Wi-Fi
  disconnect reason 201 and clock-not-synchronized messages follow. Sampling is
  correctly deferred while the clock is invalid. No valid timed samples observed.
- Independent laptop network comparison also did not find the saved hotspot name;
  neither the name nor password was printed. User asked to keep the iPhone Personal
  Hotspot screen open with Allow Others to Join and Maximise Compatibility enabled.
- Supabase project status verified ACTIVE_HEALTHY. U2 cloud sample count is zero
  before this attempt; do not infer a cloud ingestion fault from absent Wi-Fi/time.

U2.1 startup: **FAIL for this attempt** (Wi-Fi and valid time criteria not met;
hotspot visibility unresolved). U2.2–U2.8 remain **NOT TESTED**, not failed by
assumption. Physical testing is incomplete and awaits user hotspot participation.
No U2 completion or unified-score increase is justified at this checkpoint.

## Physical attempt 2 — hotspot name corrected

User reported that the iPhone name had been changed back since the earlier
successful hotspot test. Updated only WIFI_SSID in the ignored U2 configuration;
password, device token and production source were unchanged. Configured build and
COM3 upload passed again. Binary SHA-256:
`8f0351b8d9bd069d5bdb399b487a4b45e0628b782eed8f622f693a6cd37cdf6e`.

Fresh post-boot scan at 13:05:53 MYT still did not find the saved network. After
the user toggled hotspot sharing and kept compatibility enabled, disconnect reason
201 and invalid-clock deferrals continued through at least 13:08:37 MYT. No new
physical test is counted as passed. Requested a controlled return to the earlier
working ASCII hotspot name, keeping the password unchanged.

The second serial capture initially contained buffered data from earlier port use;
that prelude is not used as boot/reset evidence. The capture helper now clears
the input buffer before a controlled reset. Unknown log lines remain excluded,
and private configuration/firmware images are not report attachments.
