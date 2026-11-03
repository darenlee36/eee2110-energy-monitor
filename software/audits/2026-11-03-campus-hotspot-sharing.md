# Campus internet via Windows hotspot - 3 November 2026

**PASS: ESP32 connected through the laptop's 2.4 GHz hotspot using campus internet.**
Genuine PZEM communication and cloud measurement delivery remain pending.

The user chose Windows sharing after direct enterprise preflight, then supplied
new laptop hotspot credentials and explicitly instructed configuring the ESP32 to
use them. No laptop computer-name or Windows sign-in-password change was required
or performed. Campus student credentials were not read, stored on ESP32 or changed.

## Changes and preservation

- Base 831cfb4; branch codex/campus-hotspot-sharing. Production source unchanged.
- Updated only WIFI_SSID and WIFI_PASSWORD in ignored local_secrets.h. A redacted
  comparison before/after verified all other content unchanged, including ingestion
  token, HTTPS endpoint, device/profile IDs and the real-PZEM selection.
- Existing private configuration was backed up under ignored
  data/esp32-backups/campus-hotspot-2026-11-03/. Neither configuration nor binary is
  committed. Do not copy private files into Git or a logbook.
- Windows hotspot settings were supplied/set locally by the user. Read-only Windows
  APIs confirmed: upstream **Newinti-Secure**, hotspot **On**, configured **2.4 GHz**,
  name matching the user-supplied laptop hotspot. Password was not queried or printed.
- This is ESP32 -> laptop hotspot -> campus Wi-Fi -> internet, not direct PEAP login.

## Fresh build, flash and actual connection evidence

- PlatformIO environment esp32dev-pzem, TELEMETRY_SIMULATED=0;
  firmware identity pzem-fw-0.2.5; Serial2 RX14/TX27, 9600/8N1, existing address0xF8
  and PZEM004Tv30 v1.2.1 dependency retained.
- Actual ESP32-D0WD-V3 rev3.1/CP210x on COM3. Real build PASS: RAM 59088/327680,
  flash 947729/1310720 bytes. Upload PASS, written-data hashes verified.
- Updated application SHA-256:
  907BB7C666202284B609258B5B4E319C761FC6E92E00DD2FB88943689F6F2505.
- One controlled EN reset with safe serial capture at 115200 baud. Boot scan found
  the saved network at 2.614 s; real firmware banner at 2.617 s; Wi-Fi IP assigned
  at 2.802 s. First missing-sensor read at 7.725 s demonstrates that the existing
  clock-readiness gate passed. NTP packets/UTC value were not separately captured.
- Independent Windows hotspot status showed ClientCount=1; the known ESP32 MAC
  matched a dynamic neighbor on the laptop's hotspot subnet. This identifies the
  attached ESP32, rather than merely observing a generic Wi-Fi-connected message.
- Capture started 2026-11-03T04:41:38.846513+00:00 UTC, lasted **90.224 seconds**:
  **17 read_error messages**, all **100 ms**, each explicitly `no reading queued`;
  zero sample/enqueue messages, zero crash headlines, one expected controlled-reset
  boot. No unexpected boot/reset appeared during this bounded observation.
- PZEM and expansion board stayed disconnected as confirmed in the active USB-only
  task. No synthetic or genuine measurements were generated/uploaded in this test;
  no Supabase ingestion/HTTPS receipt or dashboard cycle was exercised by this pass.
- Safe allowlisted log/summary accompany this audit. One unrecognised boot line was
  omitted; no credential values or crash/register dumps were saved. Serial monitor
  closed and COM3 released after the capture.

## Limits and next use

Laptop must remain powered/awake, connected to campus internet, with Mobile hotspot
On at 2.4 GHz. Phone hotspot is no longer needed for this configuration. Changing
Windows hotspot name/password later requires updating the two private firmware
Wi-Fi fields and rebuilding/flashing. Prior phone values remain in the ignored
backup; no automatic multi-network fallback was added.

No production code changed, so unchanged 22-native/297-Python R1A results were not
rerun or represented as fresh. Fresh evidence is the real build, hash-verified
flash, Windows hotspot/client status and actual serial capture. Diff check clean.
No reporting worker, daily progress checker, model, paid AI, mains wiring, OLED,
public deployment, push or merge changed. Fixed software score stays 60% until
genuine PZEM/cloud/cycle evidence completes the relevant milestones.

Next: use this connection for USB-only development; then arrange qualified
supervision and follow the existing R1B genuine-measurement checklist. Keep missing
sensor faults distinct from real measurements and calibration results.
