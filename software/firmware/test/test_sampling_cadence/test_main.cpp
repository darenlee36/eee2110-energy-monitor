#include <unity.h>
#include "sampling_cadence.h"
void setUp() {}
void tearDown() {}
void test_fixed_phase_does_not_accumulate_loop_jitter() {
  SamplingCadence cadence(5000, 100);
  TEST_ASSERT_FALSE(cadence.poll(5099).due);
  auto first = cadence.poll(5107);
  TEST_ASSERT_TRUE(first.due);
  TEST_ASSERT_EQUAL_UINT32(7, first.lateMs);
  TEST_ASSERT_EQUAL_UINT32(0, first.missedSlots);
  TEST_ASSERT_FALSE(cadence.poll(10099).due);
  TEST_ASSERT_TRUE(cadence.poll(10100).due);
}
void test_late_loop_reports_missed_slots_without_fake_catchup() {
  SamplingCadence cadence(5000, 0);
  auto late = cadence.poll(19000);
  TEST_ASSERT_TRUE(late.due);
  TEST_ASSERT_EQUAL_UINT32(14000, late.lateMs);
  TEST_ASSERT_EQUAL_UINT32(2, late.missedSlots);
  TEST_ASSERT_FALSE(cadence.poll(19001).due);
  TEST_ASSERT_TRUE(cadence.poll(20000).due);
}
void test_cadence_survives_uptime_wrap() {
  SamplingCadence cadence(5000, UINT32_MAX - 2499);
  TEST_ASSERT_FALSE(cadence.poll(2499).due);
  TEST_ASSERT_TRUE(cadence.poll(2500).due);
  TEST_ASSERT_FALSE(cadence.poll(7499).due);
  TEST_ASSERT_TRUE(cadence.poll(7500).due);
}
int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_fixed_phase_does_not_accumulate_loop_jitter);
  RUN_TEST(test_late_loop_reports_missed_slots_without_fake_catchup);
  RUN_TEST(test_cadence_survives_uptime_wrap);
  return UNITY_END();
}
