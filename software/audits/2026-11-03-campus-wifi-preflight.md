# Direct campus Wi-Fi preflight - 3 November 2026

Status: **connection pending current trusted certificate settings and private user
credential entry**. No enterprise login attempted, firmware altered/reflashed,
certificate validation disabled, or account credentials read or retrieved.

Base: edf71d9, the successfully flashed USB-only pzem-fw-0.2.5 checkpoint. The user
selected direct ESP32-to-campus connection, not Windows/phone internet sharing.

Verified locally:
- SSID Newinti-Secure is visible; a fresh Windows WLAN scan found 2.4 GHz APs on
  channels 1/6/11 as well as 5 GHz APs. The ESP32-D0WD-V3 uses the 2.4 GHz band.
- Existing Windows profile uses WPA2-Enterprise, PEAP (EAP type 25), inner
  MSCHAPv2 (type 26), with server certificate validation enabled.
- Profile lists three trusted-CA SHA-1 fingerprints:
  8c749482920e49eb58ae1f6296bd4d3a7eefae66
  adff1a26d76f22e6259c2543bb9a382312fc0c65
  b122954c563bed266a9ed7397bae389ee68bcb06
- The profile has no explicit authentication ServerNames value. Its XML was read
  only for authentication and server-validation fields, not identity/password data.
- Windows certificate provider lookup found no matching referenced CA. Independent
  Python ssl.enum_certificates enumeration of 73 ROOT and 35 CA certificates also
  found no matching fingerprints. No certificate was exported. This does not prove
  campus lacks a valid certificate or that the laptop cannot connect; it means the
  required trust material was not recoverable by these checks.
- COM3 remains the identified CP210x ESP32 port. Installed firmware remains the
  real PZEM acquisition build, pzem-fw-0.2.5, with working hotspot configuration.

Official sources checked:
- https://ecampus.inti.edu.my/NewintiSecureGuide.pdf identifies the SSID/student
  login; its older certificate examples and instructions to skip validation do not
  establish the current trusted certificate for this firmware.
- Installed Arduino-ESP32 2.0.17 WiFiSTA.cpp has a PEAP begin overload accepting a
  CA PEM. Installed esp_wpa2.h exposes CA configuration and certificate time checks.
  Neither an SDK migration nor a new dependency has been introduced.
- ESP32 2.4 GHz specification:
  https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/hw-reference/chip-series-comparison.html
- Version-matched enterprise example:
  https://github.com/espressif/arduino-esp32/tree/2.0.17/libraries/WiFi/examples/WiFiClientEnterprise

Required next input: current INTI authentication CA certificate/chain (public
PEM/CER or official download link), expected authentication-server/domain name,
and confirmation of current PEAP/inner-authentication/identity settings. Student
credentials must later be entered into an ignored local file, never chat/logs.
Do not substitute a website HTTPS certificate for the Wi-Fi authentication CA.

After those settings are known, implement the smallest compatible enterprise
configuration while preserving current hotspot/ingestion credentials, enforce CA
validation, handle cold-boot certificate time without weakening the measurement
clock gate, then build/flash real mode and verify campus IP/time/HTTPS connectivity
on USB only. Limit failed student authentication attempts; do not automatically
retry wrong credentials indefinitely. No live PZEM measurement is part of that
connection test. Campus network policy/port access remains unverified until tested.

The USB flash and missing-sensor test already passed; genuine Modbus/cloud/cycle
measurement validation remains pending supervision. This preflight is not a
successful campus connection and does not increase the fixed 60% software score.
No report worker, progress automation, dashboard, model, OLED, mains wiring,
production data, credential files, push, merge or public deployment was changed.
