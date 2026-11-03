#include <unity.h>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <limits>
#include "pzem_reader.h"
#include "telemetry_upload.h"

FakePzem fakePzem;
std::uint32_t fakeNow;
std::uint32_t millis() { return fakeNow; }
HardwareSerial serial;
void setUp() {
  fakePzem = {{239.4F, 8.5F, 2030.0F, 12.345F, 50.0F, 0.997F}, 0, 0};
  fakeNow = 5000;
}
void tearDown() {}

void test_complete_snapshot_preserves_units_and_quality() {
  PzemReader reader(serial, 14, 27);
  auto r = reader.read(5000, "2026-11-03T00:00:00Z");
  TEST_ASSERT_EQUAL_STRING("valid", r.qualityStatus);
  TEST_ASSERT_FLOAT_WITHIN(0.00001F, 12.345F, r.cumulativeEnergyKwh);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 239.4F, r.voltageV);
  TEST_ASSERT_FLOAT_WITHIN(0.001F, 8.5F, r.currentA);
  TEST_ASSERT_EQUAL_FLOAT(2030.0F, r.activePowerW);
  TEST_ASSERT_EQUAL_FLOAT(50.0F, r.frequencyHz);
  TEST_ASSERT_EQUAL_FLOAT(0.997F, r.powerFactor);
  TEST_ASSERT_FALSE(r.hasBatteryVoltage);
  TEST_ASSERT_EQUAL_STRING("unknown", r.applianceState);
  TEST_ASSERT_EQUAL_STRING("not_evaluated", r.anomalyStatus);
  TEST_ASSERT_EQUAL_UINT32(5000, r.deviceUptimeMs);
  TEST_ASSERT_EQUAL_STRING("2026-11-03T00:00:00Z", r.timestamp);
}
void test_missing_voltage_aborts_without_cached_values_or_fake_zeroes() {
  PzemReader reader(serial, 14, 27);
  reader.read(5000, "2026-11-03T00:00:00Z");
  fakePzem.calls = 0;
  fakePzem.values[0] = NAN;
  auto r = reader.read(10000, "2026-11-03T00:00:05Z");
  TEST_ASSERT_EQUAL_STRING("read_error", r.qualityStatus);
  TEST_ASSERT_EQUAL_UINT32(1, fakePzem.calls);
  TEST_ASSERT_TRUE(std::isnan(r.voltageV));
  TEST_ASSERT_TRUE(std::isnan(r.currentA));
  TEST_ASSERT_TRUE(std::isnan(r.activePowerW));
  TEST_ASSERT_TRUE(std::isnan(r.cumulativeEnergyKwh));
  TEST_ASSERT_TRUE(std::isnan(r.frequencyHz));
  TEST_ASSERT_TRUE(std::isnan(r.powerFactor));
}
void test_invalid_fields_reject_entire_snapshot() {
  const float invalid[6] = {0.0F, -0.1F, 23001.0F, -0.01F, 0.0F, 1.01F};
  for (unsigned i = 0; i < 6; ++i) {
    setUp();
    fakePzem.values[i] = invalid[i];
    PzemReader reader(serial, 14, 27);
    auto r = reader.read(5000, "2026-11-03T00:00:00Z");
    TEST_ASSERT_EQUAL_STRING("read_error", r.qualityStatus);
    setUp();
    fakePzem.values[i] = std::numeric_limits<float>::infinity();
    r = reader.read(5000, "2026-11-03T00:00:00Z");
    TEST_ASSERT_EQUAL_STRING("read_error", r.qualityStatus);
  }
}
void test_zero_load_is_valid_and_recovery_uses_new_measurement() {
  PzemReader reader(serial, 14, 27);
  fakePzem.values[0] = NAN;
  auto failed = reader.read(5000, "2026-11-03T00:00:00Z");
  TelemetryUpload<6> uploader("r1a-fixture", "tefal-kettle", "pzem-fw-0.2.5");
  TEST_ASSERT_FALSE(uploader.enqueue(failed));
  TEST_ASSERT_EQUAL_UINT32(0, uploader.queued());
  fakePzem.values[0] = 240.0F;
  fakePzem.values[1] = fakePzem.values[2] = fakePzem.values[5] = 0.0F;
  auto r = reader.read(10000, "2026-11-03T00:00:05Z");
  TEST_ASSERT_EQUAL_STRING("communication_gap", r.qualityStatus);
  TEST_ASSERT_EQUAL_FLOAT(240.0F, r.voltageV);
  TEST_ASSERT_EQUAL_FLOAT(0.0F, r.activePowerW);
  TEST_ASSERT_TRUE(uploader.enqueue(r));
}
void test_slow_snapshot_rejects_possible_cache_refresh_and_wrap_is_safe() {
  PzemReader reader(serial, 14, 27);
  fakePzem.delayPerGetter = 40;
  auto r = reader.read(5000, "2026-11-03T00:00:00Z");
  TEST_ASSERT_EQUAL_STRING("read_error", r.qualityStatus);
  fakePzem.delayPerGetter = 0;
  reader.read(10000, "2026-11-03T00:00:05Z");
  fakeNow = UINT32_MAX - 10;
  fakePzem.delayPerGetter = 5;
  r = reader.read(fakeNow, "2026-11-03T00:00:05Z");
  TEST_ASSERT_EQUAL_STRING("valid", r.qualityStatus);
}
void test_genuine_mode_serializer_receipt_and_optional_fixture_export() {
  PzemReader reader(serial, 14, 27);
  auto r = reader.read(5000, "2026-11-03T00:00:00Z");
  r.sampleSequence = 1;
  TelemetryUpload<6> uploader("r1a-fixture", "tefal-kettle", "pzem-fw-0.2.5");
  TEST_ASSERT_TRUE(uploader.enqueue(r));
  uploader.requestFinalFlush();
  uploader.poll(5000, true,
    []() { return std::string("11111111-1111-4111-8111-111111111111"); },
    [](const std::string &id, const std::string &body) {
      JsonDocument doc;
      TEST_ASSERT_FALSE(deserializeJson(doc, body));
      TEST_ASSERT_EQUAL_STRING("pzem-fw-0.2.5", doc["readings"][0]["firmware_version"]);
      TEST_ASSERT_EQUAL_STRING("valid", doc["readings"][0]["quality_status"]);
      TEST_ASSERT_TRUE(doc["readings"][0]["battery_voltage_v"].isNull());
      if (const char *path = std::getenv("R1A_FIXTURE_PATH")) {
        std::ofstream out(path);
        out << "{\"fixture_kind\":\"synthetic_native_pzem_api_double\",\"batch\":" << body << "}";
        TEST_ASSERT_TRUE(out.good());
      }
      return UploadResponse{201, "{\"data\":{\"batch_id\":\"" + id +
        "\",\"accepted\":1,\"duplicates\":0,\"replayed\":false}}"};
    });
  TEST_ASSERT_TRUE(uploader.finalFlushComplete());
}
int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_complete_snapshot_preserves_units_and_quality);
  RUN_TEST(test_missing_voltage_aborts_without_cached_values_or_fake_zeroes);
  RUN_TEST(test_invalid_fields_reject_entire_snapshot);
  RUN_TEST(test_zero_load_is_valid_and_recovery_uses_new_measurement);
  RUN_TEST(test_slow_snapshot_rejects_possible_cache_refresh_and_wrap_is_safe);
  RUN_TEST(test_genuine_mode_serializer_receipt_and_optional_fixture_export);
  return UNITY_END();
}
