#if !TELEMETRY_SIMULATED

#include "pzem_reader.h"

#include <cmath>
#include <cstdio>

TelemetryReading PzemReader::read(std::uint32_t uptimeMs, const char *timestamp) {
  TelemetryReading reading{};
  reading.deviceUptimeMs = uptimeMs;
  std::snprintf(reading.timestamp, sizeof(reading.timestamp), "%s", timestamp);
  reading.voltageV = reading.currentA = reading.activePowerW = NAN;
  reading.cumulativeEnergyKwh = reading.frequencyHz = reading.powerFactor = NAN;
  reading.hasBatteryVoltage = false;
  reading.applianceState = "unknown";
  reading.connectionState = "online";
  reading.anomalyStatus = "not_evaluated";
  reading.qualityStatus = "read_error";
  const auto failed = [&]() { hadReadError_ = true; return reading; };

  const std::uint32_t startedMs = millis();
  // v1.2.1 refreshes on voltage(), then caches all registers for 200 ms.
  // A failed first read must not expose cached getters as fresh measurements.
  const float voltage = pzem_.voltage();
  if (!std::isfinite(voltage)) return failed();
  const float current = pzem_.current();
  const float power = pzem_.power();
  const float energy = pzem_.energy();  // Library already converts Wh to kWh.
  const float frequency = pzem_.frequency();
  const float powerFactor = pzem_.pf();
  // Reject mixed snapshots if preemption could have crossed the library cache window.
  if (millis() - startedMs >= 200 ||
      !std::isfinite(current) || !std::isfinite(power) || !std::isfinite(energy) ||
      !std::isfinite(frequency) || !std::isfinite(powerFactor) ||
      voltage < 80 || voltage > 260 || current < 0 || current > 100 ||
      power < 0 || power > 23000 || energy < 0 ||
      frequency < 45 || frequency > 65 || powerFactor < 0 || powerFactor > 1) {
    return failed();
  }
  reading.voltageV = voltage;
  reading.currentA = current;
  reading.activePowerW = power;
  reading.cumulativeEnergyKwh = energy;
  reading.frequencyHz = frequency;
  reading.powerFactor = powerFactor;
  reading.qualityStatus = hadReadError_ ? "communication_gap" : "valid";
  hadReadError_ = false;
  return reading;
}

#endif
