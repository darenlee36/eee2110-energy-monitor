#pragma once

// Documented plan, not a claim of verified wiring. Supervisor must confirm before flash.
// ESP32 RX receives PZEM TX; ESP32 TX sends to PZEM RX through the approved level converter.
constexpr int PZEM_RX_PIN = 14;
constexpr int PZEM_TX_PIN = 27;
static_assert(PZEM_RX_PIN == 14 && PZEM_TX_PIN == 27,
              "R1B plan requires RX14/TX27; review hardware evidence before changing pins");
