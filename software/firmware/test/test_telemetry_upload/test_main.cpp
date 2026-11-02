#include <unity.h>

#include <cstring>
#include <string>
#include <vector>

#include "telemetry_upload.h"

void setUp() {}
void tearDown() {}

struct Attempt {
  std::string id;
  std::string payload;
};

struct Server {
  std::vector<Attempt> attempts;
  UploadResponse response{201, ""};
  unsigned ids = 0;

  std::string nextId() { return "batch-" + std::to_string(++ids); }
  UploadResponse send(const std::string &id, const std::string &payload) {
    attempts.push_back({id, payload});
    return response;
  }
};

TelemetryReading reading(std::uint64_t sequence, std::uint32_t uptime) {
  TelemetryReading value{};
  std::strcpy(value.timestamp, "2026-11-02T00:00:00Z");
  value.sampleSequence = sequence;
  value.deviceUptimeMs = uptime;
  value.voltageV = 230;
  value.currentA = 8;
  value.activePowerW = 1840;
  value.cumulativeEnergyKwh = 1.25F;
  value.frequencyHz = 50;
  value.powerFactor = 1;
  value.applianceState = "on";
  value.connectionState = "online";
  value.anomalyStatus = "not_evaluated";
  value.qualityStatus = "valid";
  return value;
}

void poll(TelemetryUpload<12> &upload, Server &server, std::uint32_t now,
          bool online = true) {
  upload.poll(now, online, [&server]() { return server.nextId(); },
              [&server](const std::string &id, const std::string &payload) {
                return server.send(id, payload);
              });
}

void test_full_batch_sends_before_partial_deadline() {
  TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
  Server server;
  server.response.body = R"({"data":{"batch_id":"batch-1","accepted":6,"duplicates":0,"replayed":false}})";
  for (unsigned i = 0; i < 6; ++i) upload.enqueue(reading(i + 1, i * 5000));
  poll(upload, server, 25000);
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  TEST_ASSERT_EQUAL_UINT32(0, upload.queued());
  JsonDocument payload;
  TEST_ASSERT_FALSE(deserializeJson(payload, server.attempts[0].payload));
  TEST_ASSERT_EQUAL_UINT32(6, payload["readings"].size());
  TEST_ASSERT_EQUAL_STRING("device", payload["readings"][0]["device_id"]);
  TEST_ASSERT_EQUAL_STRING("sim-fw-test", payload["readings"][0]["firmware_version"]);
  TEST_ASSERT_EQUAL_FLOAT(1.25F, payload["readings"][0]["cumulative_energy_kwh"].as<float>());
  TEST_ASSERT_TRUE(payload["readings"][0]["battery_voltage_v"].isNull());
}

void test_each_partial_size_waits_for_oldest_reading_deadline() {
  for (unsigned count = 1; count <= 5; ++count) {
    TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
    Server server;
    server.response.body = "{\"data\":{\"batch_id\":\"batch-1\",\"accepted\":" +
                           std::to_string(count) + ",\"duplicates\":0,\"replayed\":false}}";
    for (unsigned i = 0; i < count; ++i) upload.enqueue(reading(i + 1, 5000 + i * 5000));
    poll(upload, server, 34999);
    TEST_ASSERT_EQUAL_UINT32(0, server.attempts.size());
    poll(upload, server, 35000);
    TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
    TEST_ASSERT_EQUAL_UINT32(0, upload.queued());
    JsonDocument payload;
    deserializeJson(payload, server.attempts[0].payload);
    TEST_ASSERT_EQUAL_UINT32(count, payload["readings"].size());
  }
}

void test_offline_lost_ack_retry_freezes_exact_request_with_new_samples() {
  TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
  Server server;
  upload.enqueue(reading(1, 5000));
  poll(upload, server, 35000, false);
  TEST_ASSERT_EQUAL_UINT32(0, server.attempts.size());
  // Server may have committed, but the response was lost in transit.
  server.response = {-1, ""};
  poll(upload, server, 35000);
  TEST_ASSERT_EQUAL_UINT32(1, upload.queued());
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  const Attempt first = server.attempts[0];
  for (unsigned i = 2; i <= 7; ++i) upload.enqueue(reading(i, 35000));
  poll(upload, server, 35500);
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  poll(upload, server, 36000, false);
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  server.response = {200, R"({"data":{"batch_id":"batch-1","accepted":1,"duplicates":0,"replayed":true}})"};
  poll(upload, server, 36000);
  TEST_ASSERT_EQUAL_STRING(first.id.c_str(), server.attempts[1].id.c_str());
  TEST_ASSERT_EQUAL_STRING(first.payload.c_str(), server.attempts[1].payload.c_str());
  TEST_ASSERT_EQUAL_UINT32(1, server.ids);
  TEST_ASSERT_EQUAL_UINT32(6, upload.queued());
  server.response = {201, R"({"data":{"batch_id":"batch-2","accepted":4,"duplicates":2,"replayed":false}})"};
  poll(upload, server, 36001);
  TEST_ASSERT_EQUAL_UINT32(0, upload.queued());
  JsonDocument payload;
  deserializeJson(payload, server.attempts[2].payload);
  TEST_ASSERT_EQUAL_UINT64(2, payload["readings"][0]["sample_sequence"].as<std::uint64_t>());
}

void test_unverified_or_failed_responses_never_remove_samples() {
  const UploadResponse failures[] = {
      {500, R"({"data":{"batch_id":"batch-1","accepted":1,"duplicates":0,"replayed":false}})"},
      {401, ""}, {422, ""}, {201, ""}, {201, "not json"},
      {201, R"({"data":{"batch_id":"wrong","accepted":1,"duplicates":0,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":0,"duplicates":0,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":2,"duplicates":0,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":-1,"duplicates":2,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":"1","duplicates":0,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":1.5,"duplicates":0,"replayed":false}})"},
      {201, R"({"data":{"batch_id":"batch-1","accepted":1,"duplicates":0}})"},
      {204, ""}};
  for (const auto &response : failures) {
    TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
    Server server;
    server.response = response;
    upload.enqueue(reading(1, 5000));
    poll(upload, server, 35000);
    TEST_ASSERT_EQUAL_UINT32(1, upload.queued());
    TEST_ASSERT_EQUAL_UINT32(1, upload.pendingCount());
  }
}

void test_final_flush_stops_sampling_drains_full_and_partial_batches() {
  TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
  Server server;
  for (unsigned i = 1; i <= 8; ++i) upload.enqueue(reading(i, 5000));
  upload.requestFinalFlush();
  upload.requestFinalFlush();
  TEST_ASSERT_FALSE(upload.enqueue(reading(9, 5000)));
  TEST_ASSERT_FALSE(upload.finalFlushComplete());
  poll(upload, server, 5001, false);
  TEST_ASSERT_FALSE(upload.finalFlushComplete());
  server.response = {201, R"({"data":{"batch_id":"batch-1","accepted":6,"duplicates":0,"replayed":false}})"};
  poll(upload, server, 5002);
  TEST_ASSERT_FALSE(upload.finalFlushComplete());
  server.response = {500, ""};
  poll(upload, server, 5003);
  TEST_ASSERT_FALSE(upload.finalFlushComplete());
  server.response = {201, R"({"data":{"batch_id":"batch-2","accepted":0,"duplicates":2,"replayed":false}})"};
  poll(upload, server, 6003);
  TEST_ASSERT_TRUE(upload.finalFlushComplete());
  TEST_ASSERT_EQUAL_STRING(server.attempts[1].payload.c_str(), server.attempts[2].payload.c_str());
  poll(upload, server, 90000);
  TEST_ASSERT_EQUAL_UINT32(3, server.attempts.size());
}

void test_empty_flush_and_queue_overflow_do_not_claim_unsaved_data() {
  TelemetryUpload<12> empty("device", "profile", "sim-fw-test");
  Server server;
  TEST_ASSERT_FALSE(empty.finalFlushComplete());
  empty.requestFinalFlush();
  TEST_ASSERT_TRUE(empty.finalFlushComplete());
  poll(empty, server, 0);
  TEST_ASSERT_EQUAL_UINT32(0, server.attempts.size());
  TelemetryUpload<12> full("device", "profile", "sim-fw-test");
  for (unsigned i = 1; i <= 12; ++i) TEST_ASSERT_TRUE(full.enqueue(reading(i, 5000)));
  TEST_ASSERT_TRUE(full.full());
  TEST_ASSERT_FALSE(full.enqueue(reading(13, 5000)));
  TEST_ASSERT_EQUAL_UINT32(12, full.queued());
}

void test_deadline_and_backoff_survive_millis_wrap() {
  TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
  Server server;
  upload.enqueue(reading(1, UINT32_MAX - 29999));
  poll(upload, server, UINT32_MAX);
  TEST_ASSERT_EQUAL_UINT32(0, server.attempts.size());
  poll(upload, server, 0);
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  poll(upload, server, 999);
  TEST_ASSERT_EQUAL_UINT32(1, server.attempts.size());
  poll(upload, server, 1000);
  TEST_ASSERT_EQUAL_UINT32(2, server.attempts.size());
  poll(upload, server, 2999);
  TEST_ASSERT_EQUAL_UINT32(2, server.attempts.size());
  poll(upload, server, 3000);
  TEST_ASSERT_EQUAL_UINT32(3, server.attempts.size());
}

void test_retry_backoff_caps_at_sixty_seconds() {
  TelemetryUpload<12> upload("device", "profile", "sim-fw-test");
  Server server;
  server.response = {503, ""};
  upload.enqueue(reading(1, 0));
  upload.requestFinalFlush();
  const std::uint32_t attemptsAt[] = {0, 1000, 3000, 7000, 15000, 31000,
                                     63000, 123000, 183000};
  for (unsigned i = 0; i < 9; ++i) {
    if (i > 0) {
      poll(upload, server, attemptsAt[i] - 1);
      TEST_ASSERT_EQUAL_UINT32(i, server.attempts.size());
    }
    poll(upload, server, attemptsAt[i]);
    TEST_ASSERT_EQUAL_UINT32(i + 1, server.attempts.size());
    TEST_ASSERT_EQUAL_UINT32(1, upload.queued());
    TEST_ASSERT_FALSE(upload.finalFlushComplete());
  }
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_full_batch_sends_before_partial_deadline);
  RUN_TEST(test_each_partial_size_waits_for_oldest_reading_deadline);
  RUN_TEST(test_offline_lost_ack_retry_freezes_exact_request_with_new_samples);
  RUN_TEST(test_unverified_or_failed_responses_never_remove_samples);
  RUN_TEST(test_final_flush_stops_sampling_drains_full_and_partial_batches);
  RUN_TEST(test_empty_flush_and_queue_overflow_do_not_claim_unsaved_data);
  RUN_TEST(test_deadline_and_backoff_survive_millis_wrap);
  RUN_TEST(test_retry_backoff_caps_at_sixty_seconds);
  return UNITY_END();
}
