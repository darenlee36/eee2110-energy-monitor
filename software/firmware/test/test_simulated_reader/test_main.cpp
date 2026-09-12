#include <unity.h>

#include "simulated_reader.h"

void setUp() {}
void tearDown() {}

void test_simulated_reader_advances_sequence_and_time() {
  SimulatedReader reader;

  TelemetryReading first = reader.read(1000);
  TelemetryReading second = reader.read(6000);

  TEST_ASSERT_EQUAL_UINT32(1, first.sampleSequence);
  TEST_ASSERT_EQUAL_UINT32(2, second.sampleSequence);
  TEST_ASSERT_EQUAL_UINT32(5000, second.deviceUptimeMs - first.deviceUptimeMs);
}

void test_simulated_reader_keeps_values_in_contract_range() {
  SimulatedReader reader;

  TelemetryReading reading = reader.read(31000);

  TEST_ASSERT_TRUE(reading.voltageV >= 0.0F && reading.voltageV <= 300.0F);
  TEST_ASSERT_TRUE(reading.currentA >= 0.0F && reading.currentA <= 100.0F);
  TEST_ASSERT_TRUE(reading.powerFactor >= 0.0F && reading.powerFactor <= 1.0F);
  TEST_ASSERT_TRUE(reading.batteryVoltageV >= 2.5F && reading.batteryVoltageV <= 4.5F);
}

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_simulated_reader_advances_sequence_and_time);
  RUN_TEST(test_simulated_reader_keeps_values_in_contract_range);
  return UNITY_END();
}
