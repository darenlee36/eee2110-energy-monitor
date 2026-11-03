# R1A - genuine PZEM firmware preparation - 3 November 2026

Verdict: **R1A software preparation PASS**.
R1B supervised genuine electrical validation: PENDING. No board was connected,
flashed, energized or measured during this checkpoint.

## Confirmed repository state and scope

Started clean at `fed2ae4`, branch `codex/reporting-launcher-repair`; new isolated
branch `codex/r1a-pzem-integration`. Git ancestry checks confirmed U1 `2290be7`,
U2 `d03f835`, U3 `7dcb408`, A1 `ef5f81c`, A2 `bd30942`, A3 `17848c6`, and repair
`fed2ae4` are all ancestors. These are already integrated in this local lineage;
that does not imply they were pushed, merged into main or publicly deployed.

A real reader already existed in `pzem_reader.cpp`, selected by the existing
`esp32dev-pzem` environment and `TELEMETRY_SIMULATED=0`. The default `esp32dev`
remains simulated. Reused the existing PZEM004Tv30 v1.2.1 dependency, resolved by
PlatformIO to commit `ebd9acf770ae0e189d479c847c9b472883a546b2`. No new library,
backend table, model, service or reporting architecture was introduced.

Historical component source: main project
`docs/hardware/as-received-component-log.md`, supported by the current user brief.
It identifies PZEM-004T-100A(V4.0), with TTL socket and VCC=3.3-5.0V marking.
The RX14/TX27 mapping comes from the current handover; electrical continuity and
actual UART operation are not established by either the pin example or a build.

## Hardware/protocol contract and evidence boundary

- Planned ESP32 Serial2 RX=GPIO14, TX=GPIO27, with the already-approved logic-level
  converter. The example now records those planned pins. The real build still
  requires an ignored local_hardware.h. A temporary example copy was used for the
  compile only and removed afterward; this is not wiring approval or installation.
- Existing library constructor sets 9600 baud, 8 data bits, no parity, one stop bit.
  Its unchanged default address is 0xF8, for this one-module bus. No address writes,
  calibration commands or energy-reset commands are used by the reader.
- Manufacturer V4 product page confirms TTL/9600/8N1 and 100 A external CT variant.
  MycilaPZEM's source documentation states V3/V4 use the same register protocol.
  This supports reuse of the existing driver, but is not proof that this individual
  board answers it. Reject contradictory third-party register tables as authority.
- The pinned library reads input registers using function 0x04, start 0, count 10,
  expecting a 25-byte CRC-checked reply. Its receive timeout is 100 ms and cache
  window 200 ms. These are inspected code constants, not physical timings measured
  on the delivered module. It does not expose detailed exception/timeout causes;
  failures are reported generically as read_error.

| Quantity | Library source registers | Conversion to existing telemetry |
|---|---|---|
| Voltage | 0x0000 | uint16 / 10, volts |
| Current | 0x0001 low + 0x0002 high | uint32 / 1000, amperes |
| Active power | 0x0003 low + 0x0004 high | uint32 / 10, watts |
| Accumulated energy | 0x0005 low + 0x0006 high | uint32 Wh / 1000, kWh |
| Frequency | 0x0007 | uint16 / 10, Hz |
| Power factor | 0x0008 | uint16 / 100, dimensionless |

The existing float telemetry fields/JSON numbers remain unchanged. There is no
second energy conversion, inferred battery value, appliance state, or model score.
Float precision at large lifetime energy totals remains an existing limitation.
The level converter and connector orientation must be checked by the supervisor;
software cannot establish a safe voltage level or continuity.

Sources inspected on 3 November 2026:
- Manufacturer: https://en.peacefair.cn/product/772.html
- V3/V4 compatibility documentation (not an added dependency):
  https://raw.githubusercontent.com/mathieucarbou/MycilaPZEM/main/README.md
- Pinned protocol/cache/timeout implementation:
  https://github.com/mandulaj/PZEM-004T-v30/tree/v1.2.1/src

## Defects fixed and preserved behavior

Previously, failed reads were changed into zero voltage/current/power/energy/PF
and a fabricated 50 Hz; finite but out-of-range data could also be enqueued.
The library's remaining getters can expose its cached values after an earlier
refresh failure. The reader now stops immediately on a failed voltage refresh,
leaves all six numbers unavailable (NaN in RAM), and returns read_error. It never
serializes those unavailable measurements. Both the acquisition loop and uploader
reject read_error records, avoiding poisoned retries/false off readings.

Successful snapshots must be finite and inside the documented measurement envelope:
80-260 V, 0-100 A, 0-23000 W, nonnegative accumulated kWh, 45-65 Hz and PF 0-1.
A powered no-load reading with valid voltage/frequency and zero I/P/PF is accepted.
Snapshots taking 200 ms or more are rejected to avoid mixing values across the
library's cache refresh window. This is a conservative acceptance guard, not a
hard real-time timeout; task preemption can delay return. Physical R1B timing is
still required. First good recovery is tagged communication_gap, then clean reads
return to valid; the existing cycle/model quality gates retain that distinction.

Network/upload remains on U3 core 0; acquisition/serial commands remain core 1.
Modbus reading is still synchronous on acquisition, so serial-command response may
wait for a read. The five-second fixed-deadline scheduler, missed-slot diagnostics,
120-reading RAM queue, six-reading batches, 30-second partial eligibility, immutable
retry request/ID/count, receipt checks and final flush are preserved. Failed reads
do not allocate sample sequences; elapsed timestamps, recovery quality and serial
errors record missing measurements. No gap is backfilled with invented samples.
An outage with no recovery is visible as stale/offline telemetry; no new remote
fault event was added because the existing wire schema requires numeric readings.

Genuine-mode firmware is `pzem-fw-0.2.5`; simulation remains `sim-fw-0.2.4`.
Origin means firmware acquisition mode, never successful calibration. Detailed
serial diagnostics now include all six quantities/units, read duration and quality;
logs contain no credentials. Existing reporting and 5/10/20 comparisons are unchanged.

## Verification

- Test-first host run reproduced 4 failures in the old reader/uploader: stale getter
  access, range acceptance, error-record enqueue and missing cache-window guard.
- New native-pzem suite: **6 passed**, compiling actual pzem_reader.cpp with clearly
  labelled host Arduino/PZEM API doubles. Covers valid units, zero load, unavailable
  values, invalid/nonfinite fields, recovery, read-window/uptime wrap, serialization
  and receipt-confirmed final flush. These are not physical Modbus/CRC bench tests.
- Existing native U1-U3 suites: **16 passed**, including concurrency, queue wrap,
  slow requests, reconnects, lost acknowledgements, immutable retries and final drain.
- Real `esp32dev-pzem` build: **PASS**, RAM 59088/327680 bytes; flash 947681/1310720.
  This compiles the actual pinned upstream driver, not test doubles. No flash performed.
- Simulated `esp32dev` build: **PASS**, RAM 59024/327680 bytes; flash 949213/1310720.
  Initial concurrent build lost its output directory when another PlatformIO
  environment prepared the same build root. Sequential rerun with its own root
  passed; no firmware source fix was needed for that build-folder collision.
- Final Edge predicate check after the lint-only test edit: **1 passed**.
  Dependency check: **no broken requirements**; `git diff --check`: clean.
- Full Python regressions: **297 passed**, 9 upstream Streamlit/NumPy deprecation
  warnings, 161.40 seconds. Ruff src/dashboard/tests: **clean**.
- New offline integration: actual native serializer fixture passes the production
  Edge isReading predicate executed locally with Node's built-in TypeScript strip;
  mocked Supabase REST imports/reuses one row, all six values survive SQLite,
  and saved Streamlit Live renders 239.4 V / 2030 W. Fixture labels explicitly say
  synthetic_native_pzem_api_double; all writes are temporary local test databases.
  No cloud telemetry, production training record or genuine measurement was created.
- The Edge handler/database/auth network path was not deployed or exercised live;
  offline predicate + mocked REST + local UI evidence must not be called live E2E.

## R1B supervised checklist - do not perform now

1. **Authorization and identification:** qualified supervisor present; record date,
   approved assembly revision, PZEM/CT variant and firmware commit/build hash. Verify
   RX14/TX27, connector direction, converter levels and grounding against approved
   documentation while de-energized. No mains rewiring is part of this plan. Stop
   if documentation and assembly differ. Supervisor controls all energization.
2. **Test separation/configuration:** use a distinct physical-device ID (for example
   esp32-pzem-r1b) and session ID; retain the existing appliance profile. Keep secrets
   private, confirm genuine target/pzem-fw-0.2.5, and keep initial records unreviewed
   and outside production training manifests. Record water volume/starting condition
   using A2's collection template. Do not activate a model or paid AI.
3. **First communication:** at 115200 serial logging, capture boot mode, pins,
   network/time readiness, and at least 24 consecutive scheduled genuine snapshots
   (about two minutes). Capture V/A/W/kWh/Hz/PF, quality, sequence, uptime, interval,
   read duration and cadence late/missed counts. No valid frames means FAIL/PENDING;
   indicator lights or boot alone do not count. If unavailable, supervisor stops
   and checks documentation/low-voltage interface de-energized, not blind rewiring.
4. **One complete normal cycle:** under supervisor control and appliance instructions,
   capture powered idle, one ordinary labelled boil and at least 30 seconds of
   powered idle after automatic switch-off. Record initial/final accumulated kWh,
   cycle times, energy difference and any available reference-meter readings. No
   dry boil, overfill, induced faults or repeated U1-U3 Wi-Fi stress tests. Reference
   agreement is recorded as measured; absence of a reference is not calibration.
5. **Cloud and dashboard proof:** capture acknowledgement batch IDs/counts; export
   matching cloud rows with device ID, firmware, sequence, recorded/received times,
   six quantities and quality. Count expected accepted/recovered rows, duplicates
   and explained gaps. Select the physical device in hosted Live/Cycles; capture
   matching V/A/W, graphs and completed cycle with measured energy/elapsed time.
   A flush acknowledgement alone does not prove a cycle was closed. If the current
   public deployment needs a change, obtain deployment approval separately.
6. **Close and verdict:** only after idle closure send flush + newline, await all
   queued receipts, and let the supervisor control shutdown. Save serial log, cloud
   export, dashboard screenshots and collection notes as one session. Report actual
   interval distribution/worst gap/read durations; do not reuse U3 simulated timings.
   R1B PASS requires genuine six-field readings, no unexplained duplicates/loss,
   cloud/UI agreement and a completed cycle. Otherwise label each missing/failed
   item explicitly. Fault/disconnect recovery is software-tested only until a
   separately safe supervised check is actually captured.

## Remaining limits

Individual-module response, address compatibility, electrical scaling/calibration,
converter operation, genuine sampling jitter and completed-cycle thresholds are
unverified. Quality gates prevent unusable snapshots reaching training; they do not
prove measurements are accurate or independently normal. RAM buffering and pending
requests do not survive sudden reset/power loss. Code/test preparation does not
complete fixed milestone 7 or increase the evidence-backed software score from 60%.
No reporting task/ChatGPT checker, OLED, mains wiring, model training, inference,
public deployment, push or merge was changed in R1A.


Reproduction (software-only, no upload target):

```powershell
$pio = 'C:/Users/Daren Lee/.platformio/penv/Scripts/pio.exe'
$env:PLATFORMIO_BUILD_DIR = 'C:/pio-build/eee2110-r1a-host'
& $pio test -d software/firmware -e native
& $pio test -d software/firmware -e native-pzem
$env:PLATFORMIO_BUILD_DIR = 'C:/pio-build/eee2110-r1a-simulated'
& $pio run -d software/firmware -e esp32dev -j 1
# Real build also needs a local_hardware.h compile configuration. The R1A
# temporary copy was removed; do not treat a new copy as supervisor approval.
$env:PLATFORMIO_BUILD_DIR = 'C:/pio-build/eee2110-r1a-preparation'
& $pio run -d software/firmware -e esp32dev-pzem -j 1
# From software/, using this checkout's src and software on PYTHONPATH:
./.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short
./.venv/Scripts/ruff.exe check src dashboard tests
```

Modified production areas: pzem_reader.cpp/.h, main.cpp, telemetry_upload.h and
planned local_hardware.example.h. platformio.ini adds only a host test environment;
three small native test/API-double files, the native-generated labelled JSON fixture,
and test_pzem_integration.py cover the change. README and this audit document it.
