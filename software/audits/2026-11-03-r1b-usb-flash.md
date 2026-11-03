# R1B USB-only real-PZEM flash verification - 3 November 2026

**USB flash/boot checkpoint: PASS.** Genuine PZEM communication/electrical
measurement validation: **PENDING / NOT TESTED**.

## Scope and repository

User confirmed ESP32 directly connected by USB, isolated from expansion board/PZEM;
mains disconnected and the battery not providing usable power. No enclosure wiring,
mains/PZEM connection, charging operation or live measurement was requested/performed.

Clean start at R1A `c83bdf2`; ancestry checks confirmed U1 `2290be7`, U2 `d03f835`,
U3 `7dcb408` and R1A present. Isolated branch `codex/r1b-usb-flash`.
The only firmware change is one startup Serial.printf for its existing version
constant. Acquisition, credentials, networking, batching, retries and reports are
unchanged. The ignored hardware header was absent; it was created from the R1A
example with planned RX14/TX27. This is a USB-only pin configuration, not evidence
of correct wiring to the disconnected sensor. It remains ignored locally.

## Identification and flash

- Actual serial port: **COM3**, Silicon Labs CP210x USB UART, VID:PID 10C4:EA60.
  COM5/COM6 are Bluetooth serial ports and were not selected.
- Esptool bootloader query confirmed **ESP32-D0WD-V3, revision v3.1**, dual core,
  240 MHz, 40 MHz crystal. It was left in bootloader to avoid starting the old app.
  The generic PlatformIO `esp32dev` board profile matches this ESP32 family;
  the selected environment was explicitly **esp32dev-pzem**, never the simulated target.
- Real mode: TELEMETRY_SIMULATED=0, **pzem-fw-0.2.5**, Serial2 RX14/TX27,
  9600 baud / 8N1, unchanged default single-device address 0xF8.
- Library: existing PZEM004Tv30 **v1.2.1**, pinned Git commit
  ebd9acf770ae0e189d479c847c9b472883a546b2. Actual library built, not native doubles.
- Build: **PASS**, static RAM 59088/327680 bytes; flash 947713/1310720 bytes.
- Upload: **PASS**, COM3 at 115200 baud. Uploader verified hashes after writing,
  including the application at 0x10000. No whole-flash erase was performed.
- Application firmware.bin SHA-256:
  F254C29B38C8C99973173366A9F2252BC7C11E2559C7B34FE36E16BA13CD1779.
  The compiled image stays local because it includes existing private configuration.

## Actual serial observations

Two bounded windows were captured at 115200 baud; they are not one uninterrupted
185-second recording. First window started 2026-11-03T12:06:55.672163+08:00, lasted 120.131 s,
with one deliberate EN reset to capture boot. Second started 2026-11-03T12:09:29.541372+08:00, lasted
65.016 s, without reset. The monitor closed/released COM3 afterward.

Observed boot lines include:

```text
PZEM telemetry reader
Firmware: pzem-fw-0.2.5
PZEM UART2 RX=14 TX=27 baud=9600 8N1 address=0xF8; sensor reply unverified.
```

Initial hotspot scan did not find the saved network; 20 clock-unsynchronised slots
were deferred, as required by the existing timestamp gate. The user then enabled
the configured hotspot. Wi-Fi IP assignment was captured 102.550 s into window 1;
subsequent sensor-read attempts establish that the existing valid-clock gate passed.
No fake clock or alternative Wi-Fi credential was introduced.

- Window 1: **3 read_error diagnostics**, 0 sample/enqueue success messages,
  0 crash headlines; one expected controlled-reset boot and one firmware banner.
- Window 2: **13 read_error diagnostics**, 0 sample messages, 0 resets,
  0 crash headlines; every captured line was a sensor-unavailable diagnostic.
- All **16** read failures reported **100 ms**, with `no reading queued`.
  Representative actual line:

```text
Sensor read_error uptime_ms=156807 duration_ms=100; no reading queued.
```

Successive failure-attempt uptimes in window 2 are 5000 ms apart. This demonstrates
this missing-sensor path in this short USB test only; it is not a measurement of
actual PZEM acquisition cadence, successful frames, sensor accuracy or cloud delivery.
No valid/simulated measurements or upload attempts were observed. No unexpected
crash/boot-loop/reset diagnostics appeared in either captured window.

Safe logs use an allowlist; the first boot had one unrecognised line omitted.
Crash headlines would be flagged without saving memory/register dumps. No credential
values were logged. Full local upload output/capture helper are ignored under
`data/esp32-backups/r1b-usb-2026-11-03/`; firmware binaries remain outside Git.

## Verification and remaining boundary

| Check | Result |
|---|---|
| Actual COM port and compatible chip family established | PASS |
| Real PZEM target selected and built | PASS |
| Firmware written and uploader hash verification | PASS |
| Actual serial firmware identity and intended UART configuration | PASS |
| Stable boot in the two captured USB-only windows | PASS, bounded observation |
| Missing sensor produces read_error without fabricated/enqueued readings | PASS |
| Genuine Modbus readings, cloud rows and completed dashboard cycle | NOT TESTED |
| Supervised mains/physical PZEM validation | PENDING |

The existing R1A 22-native/297-Python results are historical, not rerun for this
one-line boot-banner change. The new build, actual flash and actual serial capture
are fresh evidence. No reporting/AI calls, OLED, credentials changes, cloud test-data
uploads, public deployment, push or merge occurred. Campus enterprise Wi-Fi was
raised afterward as a future configuration question; it was not configured here.

Next: review this USB checkpoint, arrange qualified supervision, then follow the
R1A supervised R1B checklist for a distinct physical-device/session ID, six genuine
quantities, receipts/cloud export and one completed dashboard cycle. No genuine
measurement milestone or fixed software percentage increase is justified by flashing.
