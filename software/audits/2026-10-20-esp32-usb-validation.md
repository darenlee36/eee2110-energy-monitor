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
