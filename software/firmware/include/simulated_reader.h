#pragma once

#include "telemetry.h"

class SimulatedReader {
 public:
  bool begin() { return true; }
  TelemetryReading read(std::uint32_t uptimeMs, const char *timestamp = "1970-01-01T00:00:00Z");

 private:
  std::uint32_t sequence_ = 0;
  float cumulativeEnergyKwh_ = 0.125F;
};

