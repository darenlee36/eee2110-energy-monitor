#pragma once

// Copy to local_hardware.h only after board and supervised UART wiring are confirmed.
// Do not use these placeholders as a wiring recommendation.
constexpr int PZEM_RX_PIN = -1;
constexpr int PZEM_TX_PIN = -1;

static_assert(PZEM_RX_PIN >= 0, "Set confirmed PZEM_RX_PIN in local_hardware.h");
static_assert(PZEM_TX_PIN >= 0, "Set confirmed PZEM_TX_PIN in local_hardware.h");
