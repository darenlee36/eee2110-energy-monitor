#if !TELEMETRY_SIMULATED

#include "pzem_reader.h"

#include <cmath>
#include <cstdio>

TelemetryReading PzemReader::read(std::uint32_t uptimeMs, const char *timestamp) {
  TelemetryReading reading{};
  reading.deviceUptimeMs = uptimeMs;
  std::snprintf(reading.timestamp, sizeof(reading.timestamp), "%s", timestamp);
  reading.voltageV = pzem_.voltage();
  reading.currentA = pzem_.current();
  reading.activePowerW = pzem_.power();
  reading.cumulativeEnergyKwh = pzem_.energy();
  reading.frequencyHz = pzem_.frequency();
  reading.powerFactor = pzem_.pf();
  reading.hasBatteryVoltage = false;
  reading.applianceState = "unknown";
  reading.connectionState = "online";
  reading.anomalyStatus = "not_evaluated";

  const bool valid = std::isfinite(reading.voltageV) && std::isfinite(reading.currentA) &&
                     std::isfinite(reading.activePowerW) &&
                     std::isfinite(reading.cumulativeEnergyKwh) &&
                     std::isfinite(reading.frequencyHz) &&
                     std::isfinite(reading.powerFactor);
  if (valid) {
    reading.qualityStatus = "valid";
  } else {
    reading.voltageV = 0.0F;
    reading.currentA = 0.0F;
    reading.activePowerW = 0.0F;
    reading.cumulativeEnergyKwh = 0.0F;
    reading.frequencyHz = 50.0F;
    reading.powerFactor = 0.0F;
    reading.qualityStatus = "read_error";
  }
  return reading;
}

#endif
