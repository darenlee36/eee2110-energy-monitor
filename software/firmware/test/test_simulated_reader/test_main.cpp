#include <unity.h>

#include "simulated_reader.h"
#include "telemetry_queue.h"

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

void test_queue_preserves_order_and_rejects_overflow() {
  TelemetryQueue<2> queue;
  TelemetryReading first{};
  TelemetryReading second{};
  TelemetryReading third{};
  first.sampleSequence = 10;
  second.sampleSequence = 11;
  third.sampleSequence = 12;

  TEST_ASSERT_TRUE(queue.push(first));
  TEST_ASSERT_TRUE(queue.push(second));
  TEST_ASSERT_FALSE(queue.push(third));

  TelemetryReading output[2]{};
  TEST_ASSERT_EQUAL_UINT32(2, queue.copyFront(output, 2));
  TEST_ASSERT_EQUAL_UINT64(10, output[0].sampleSequence);
  TEST_ASSERT_EQUAL_UINT64(11, output[1].sampleSequence);
  queue.popFront(1);
  TEST_ASSERT_TRUE(queue.push(third));
  TEST_ASSERT_EQUAL_UINT32(2, queue.size());
}

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_simulated_reader_advances_sequence_and_time);
  RUN_TEST(test_simulated_reader_keeps_values_in_contract_range);
  RUN_TEST(test_queue_preserves_order_and_rejects_overflow);
  return UNITY_END();
}
