#pragma once
// Test-only API double, not a Modbus implementation or physical sensor.
#include "Arduino.h"
struct FakePzem {
  float values[6];
  unsigned calls;
  std::uint32_t delayPerGetter;
};
extern FakePzem fakePzem;
extern std::uint32_t fakeNow;
class PZEM004Tv30 {
 public:
  PZEM004Tv30(HardwareSerial &, std::uint8_t, std::uint8_t) {}
  float voltage() { return get(0); }
  float current() { return get(1); }
  float power() { return get(2); }
  float energy() { return get(3); }
  float frequency() { return get(4); }
  float pf() { return get(5); }
 private:
  float get(unsigned index) {
    ++fakePzem.calls;
    fakeNow += fakePzem.delayPerGetter;
    return fakePzem.values[index];
  }
};
