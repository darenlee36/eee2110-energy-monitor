# EEE2110 Multi-Purpose Energy Monitor

Software-first prototype for an ESP32 appliance energy monitor. Current implementation provides
five-second electrical telemetry, automatic load-cycle detection, measured cycle energy, TNB cost
estimates, manual test records, user-defined labels, and a Streamlit dashboard.

The household kettle is the first validation load. Kettle results do not establish accuracy for
every appliance.

## Repository layout

- [`software/`](software/) contains dashboard, local API, ESP32 firmware, Supabase schema,
  simulations, tests, and detailed setup instructions.
- [`docs/`](docs/) contains design specifications, implementation plans, and supporting research.

Start with [`software/README.md`](software/README.md).

## Current status

- Local simulated telemetry path: implemented and tested.
- ESP32 PZEM-004T reader path: compile-verified; physical pins remain unassigned.
- Supabase ingestion and dashboard synchronization: implemented; cloud project not deployed yet.
- Hardware calibration, supervised mains-side validation, volume-model training, and agentic
  reporting: pending.

## Safety boundary

This repository does not provide exposed-mains wiring instructions. Physical integration requires
the confirmed ESP32 board, approved UART pins, an enclosed isolation arrangement, and supervised
lab testing.
