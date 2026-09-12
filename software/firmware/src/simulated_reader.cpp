#include "simulated_reader.h"

#include <cmath>
#include <cstdio>

TelemetryReading SimulatedReader::read(std::uint32_t uptimeMs, const char *timestamp) {
  TelemetryReading reading{};
  reading.sampleSequence = ++sequence_;
  reading.deviceUptimeMs = uptimeMs;
  std::snprintf(reading.timestamp, sizeof(reading.timestamp), "%s", timestamp);

  const std::uint32_t phase = (reading.sampleSequence - 1U) % 72U;
  const bool heating = (phase >= 6U && phase <= 29U) || (phase >= 42U && phase <= 71U);
  const bool extendedAnomaly = phase >= 66U && phase <= 71U;
  reading.voltageV = 239.0F + std::sin(static_cast<float>(reading.sampleSequence) * 0.3F);
  reading.frequencyHz = 50.0F;
  reading.batteryVoltageV = std::fmax(3.72F, 4.08F - reading.sampleSequence * 0.0014F);

  if (heating) {
    reading.activePowerW = 2050.0F + 20.0F * std::sin(static_cast<float>(reading.sampleSequence));
    reading.powerFactor = 0.997F;
    reading.currentA = reading.activePowerW / (reading.voltageV * reading.powerFactor);
    reading.applianceState = "heating";
    reading.anomalyStatus = extendedAnomaly ? "anomaly" : "normal";
  } else {
    reading.activePowerW = 0.0F;
    reading.powerFactor = 0.0F;
    reading.currentA = 0.0F;
    reading.applianceState = "off";
    reading.anomalyStatus = "not_evaluated";
  }

  cumulativeEnergyKwh_ += reading.activePowerW * 5.0F / 3600000.0F;
  reading.cumulativeEnergyKwh = cumulativeEnergyKwh_;
  reading.connectionState = "online";
  reading.qualityStatus = "valid";
  return reading;
}

