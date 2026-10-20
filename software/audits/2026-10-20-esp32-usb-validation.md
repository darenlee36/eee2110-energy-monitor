# ESP32 USB bench validation — 20 October 2026

## Scope and verified device

USB power only; no PZEM or mains connection was used. The board photo shows an ESP-32
module, Micro-USB, and BOOT/RST buttons. Windows reports a running Silicon Labs CP210x
USB-to-UART bridge on COM3 (VID 10C4, PID EA60).

Espressif's ROM utility identified ESP32-D0WD-V3 revision v3.1, a 40 MHz crystal and 4 MB
flash. Chip identity and flash-ID reads passed without changing flash.

The separate project at `Documents/PlatformIO/Projects/ESP32_Hardware_Test` contains an
`esp32dev` Arduino configuration but no source file or completed application image.
Its configuration does not establish what application was on the device.

## Original flash backup

The full original flash was read before uploading replacement firmware:

- Private path: `data/esp32-backups/esp32-original-20261020-115200.bin`.
- Size: 4,194,304 bytes.
- SHA-256: `0fe88570c7b2bbd88a85ae86a76febc640a7f8e595fbe59baaf4798014108b5f`.
- Read duration: 379.3 seconds at 115200 baud.
- 460800-baud backup attempts stalled; only those test helpers were stopped. A 64 KiB
  read at 115200 passed, followed by the complete backup. No full-chip erase was used.

Backups and private firmware settings are Git-ignored. Both backups and compiled firmware
may contain credentials; do not publish or share them. Backup restoration has not been tested.

## Secure simulated firmware

The existing `esp32dev` target was retained. No new dependency was introduced.

- Firmware version: `sim-fw-0.2.1`; device ID: `sim-esp32-03b248`.
- Profile: `tefal-kettle`; five-second samples, six-reading batches.
- Owner entered hotspot settings into ignored `firmware/include/local_secrets.h`.
  Configuration syntax and matching compiled values were checked without displaying them.
- The installed HTTPClient implementation used insecure TLS for its bare URL overload.
  Uploads now use WiFiClientSecure with a CA certificate and an HTTPS-only configuration.
- GTS Root R4 was obtained from `https://pki.goog/repo/certs/gtsr4.pem`. Its SHA-256 is
  `349dfa4058c5e263123b398ae795573c4e1313c83fe68f93556cd5e8031b3c7d`.
  An anonymous desktop TLS probe verified the actual Supabase hostname using only that CA.
  Recheck the CA if the endpoint's chain changes; never fall back to insecure TLS.
- Ten focused contract checks passed and Ruff was clean. The security regression failed
  before the fix and passed after it. The complete Python suite was not rerun.
- ESP32 build passed: 58,876 bytes RAM and 935,341 bytes program usage.
- Upload at 115200 baud passed with flash hash verification and a hardware reset.
  The previous application was replaced only after the original flash backup completed.

## Runtime state at this checkpoint

The new firmware runs, but serial output repeatedly reports
`Clock not synchronized; sample deferred.` No sample is invented when the clock is invalid.
The cloud query for `sim-esp32-03b248` returned zero readings.

Wi-Fi association versus internet/NTP failure is not yet distinguished. The owner has been
asked whether the phone hotspot shows the ESP32 as connected. Do not claim successful Wi-Fi,
TLS from the ESP32, cloud delivery, or dashboard integration until runtime evidence exists.

Overall software progress remains 60%. This is an active USB/simulated-transport test,
not a completed real PZEM measurement milestone or training-data collection.

## Follow-up: Wi-Fi visibility isolated

- Diagnostic firmware `sim-fw-0.2.2` built and flashed with hash verification.
  Eleven focused contract checks passed; Ruff was clean. Diagnostics do not print
  credentials or nearby network names.
- Startup scan detected seven networks, but not the configured hotspot.
  Repeated disconnect reason 201 matches `WIFI_REASON_NO_AP_FOUND` in the installed SDK.
  Wi-Fi visibility/association is therefore the current blocker; successful password
  authentication, NTP and ESP32 cloud delivery remain unverified.
- Owner identified the hotspot phone as an iPhone, enabled compatibility settings,
  and confirmed the configured name matches the iPhone name. Private validation found
  no leading/trailing whitespace or non-ASCII characters in the configured name.
- An independent Windows network scan returned no usable network list; it provides
  no additional evidence for or against hotspot visibility.
- Next owner action: toggle Allow Others to Join off/on, retain Maximise Compatibility,
  and keep the hotspot settings screen open nearby before another ESP32 startup scan.
  No mains or PZEM test has been performed. Progress remains 60%.
- After the owner toggled the hotspot and replied ready, another startup scan saw
  eight networks but still not the configured hotspot; reason 201 persisted.
  A Windows command-line scan was unavailable (exit code 1), so the owner was asked
  whether the hotspot appears in the laptop's Wi-Fi list. No root cause is yet proven.
- Owner confirmed the hotspot appears in the laptop's Wi-Fi list. An elevated,
  credential-filtered Windows scan found one name matching after apostrophe
  normalization, but no exact match. The mismatch was curly versus straight
  apostrophe only; matching entries reported WPA2-Personal, 2.4 GHz, channel 6.
- Corrected only the ignored local SSID setting to the observed name. Verified the
  rebuilt binary contains the corrected UTF-8 value and unchanged private settings.
  Build and flash hash verification passed. Nevertheless, a fresh ESP32 startup
  scan saw four networks, not the configured hotspot, and reason 201 persisted.
  Thus the name mismatch is verified but is not a sufficient explanation or fix.
- Next controlled test requires the owner to temporarily use an ASCII-only iPhone
  name, restart the hotspot, and keep compatibility enabled. No password changes
  or backend changes were made. ESP32 cloud delivery remains unverified.
- Owner renamed the iPhone to `FYP-Test`. Updated only the ignored local network
  name, retaining the password and device credentials. A fresh scan requested via
  the Windows WLAN API (status 0) confirmed `FYP-Test`, WPA2-Personal, 2.4 GHz,
  channel 6. This refreshed scan is stronger evidence than the prior cached list.
  The rebuilt image contains the expected settings and `sim-fw-0.2.2`; build passed
  at 58,884 bytes RAM and 936,761 bytes program usage.

## Verified outcome: physical ESP32 with simulated readings

- Flash hash verification passed. Startup found the configured hotspot and received
  an IP address. After NTP synchronisation, sampling began; no invalid-clock samples
  were uploaded. The ESP32 reported authenticated upload HTTP 201.
- Supabase received the first six `sim-fw-0.2.2` readings at 15:29:43–15:30:08 UTC
  on 20 October. At a later checkpoint, all 72 stored sample sequences were unique;
  18 readings were from after the controlled restart.
- Public Streamlit was asleep, not broken. Waking it restored the cloud-backed Live
  view, which showed Online and explicitly identified the latest origin as simulated.
- Live and Cycles both showed the first completed ESP32-generated test cycle:
  122 seconds, 24 cycle samples, 0.068344 kWh simulated meter energy,
  0.06947739013888889 kWh integrated energy, and zero detected gaps.
  The displayed gross charge range was RM0.030–RM0.037, not a final household bill.
- A fresh analytical cache imported the physical ESP32's simulated readings through
  seven-row pages and produced the expected dashboard view. Recovery of the completed
  cycle also passed. Initial temporary-cache cleanup encountered a Windows SQLite
  handle-lifetime error; explicitly collecting handles allowed the validation rerun
  and removal of only the disposable cache. This did not change application storage code.
- A controlled ESP32 reset rejoined the hotspot, synchronised time and uploaded with
  HTTP 201. The first new sequence was 8000001, above the preceding observed 7000054.
  The reserved sequence-block gap is intentional; it is not proof of lost sensor samples.
- Eleven focused contract tests passed freshly and Ruff passed for the changed Python
  test file. The full application test suite was not rerun in this follow-up.
- Evidence images: [Live completed cycle](esp32-first-cloud-cycle-20261020.jpg) and
  [Cycles history](esp32-cycle-history-20261020.jpg).

### Boundaries and next work

The owner has only the ESP32 available and confirms the sensor-side hardware is not
finished. Pause real PZEM integration until the module, communication harness and safe
hardware arrangement are ready. Do not assign GPIO pins or energise mains based on this test.
This verifies real-board transport of generated readings, not real electrical measurement,
calibration, training data, or milestone 7 completion. Overall progress remains 60%.

The RAM queue is not a power-loss-durable outbox. The restart test proves reconnection
and non-reused sample IDs, not recovery of every unuploaded sample. The test firmware
continues producing simulated cycles while powered; unplug USB to stop the test stream.
Software-only next work can cover dataset validation/export, model-pipeline preparation
without real-data performance claims, and deterministic per-cycle reporting. Real-data
evaluation remains gated on validated sensor cycles.
