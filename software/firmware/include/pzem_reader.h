#pragma once

#if !TELEMETRY_SIMULATED

#include <Arduino.h>
#include <PZEM004Tv30.h>

#include "telemetry.h"

class PzemReader {
 public:
  PzemReader(HardwareSerial &serial, std::uint8_t receivePin, std::uint8_t transmitPin)
      : pzem_(serial, receivePin, transmitPin) {}

  bool begin() { return true; }
  TelemetryReading read(std::uint32_t uptimeMs, const char *timestamp);

 private:
  PZEM004Tv30 pzem_;
};

#endif
