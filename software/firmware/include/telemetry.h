#pragma once

#include <cstdint>

struct TelemetryReading {
  char timestamp[25];
  std::uint32_t sampleSequence;
  std::uint32_t deviceUptimeMs;
  float voltageV;
  float currentA;
  float activePowerW;
  float cumulativeEnergyKwh;
  float frequencyHz;
  float powerFactor;
  float batteryVoltageV;
  const char *applianceState;
  const char *connectionState;
  const char *anomalyStatus;
  const char *qualityStatus;
};

