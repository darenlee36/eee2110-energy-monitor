#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include <time.h>

#if __has_include("local_secrets.h")
#include "local_secrets.h"
#else
#include "local_secrets.example.h"
#endif

#include "simulated_reader.h"

namespace {
constexpr std::uint32_t kSampleIntervalMs = 5000;
constexpr std::size_t kBatchSize = 6;
constexpr char kFirmwareVersion[] = "sim-fw-0.1.0";

SimulatedReader reader;
TelemetryReading buffer[kBatchSize];
std::size_t buffered = 0;
std::uint32_t lastSampleMs = 0;

bool configurationIsReady() {
  return String(WIFI_SSID) != "CHANGE_ME" && String(DEVICE_INGEST_TOKEN) != "CHANGE_ME";
}

void connectWifi() {
  if (!configurationIsReady()) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const std::uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000U) {
    delay(250);
  }
  if (WiFi.status() == WL_CONNECTED) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
  }
}

void isoTimestamp(char *output, std::size_t length) {
  const time_t now = time(nullptr);
  tm utc{};
  gmtime_r(&now, &utc);
  strftime(output, length, "%Y-%m-%dT%H:%M:%SZ", &utc);
}

String createBatchId() {
  std::uint8_t bytes[16];
  for (std::uint8_t &byte : bytes) byte = static_cast<std::uint8_t>(esp_random());
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  char value[37];
  snprintf(
      value, sizeof(value),
      "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
      bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
      bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
  return String(value);
}

bool uploadBatch(const TelemetryReading *readings, std::size_t count) {
  if (WiFi.status() != WL_CONNECTED) return false;

  const String batchId = createBatchId();
  JsonDocument document;
  document["batch_id"] = batchId;
  JsonArray jsonReadings = document["readings"].to<JsonArray>();
  for (std::size_t index = 0; index < count; ++index) {
    const TelemetryReading &reading = readings[index];
    JsonObject value = jsonReadings.add<JsonObject>();
    value["device_id"] = DEVICE_ID;
    value["profile_id"] = PROFILE_ID;
    value["timestamp"] = reading.timestamp;
    value["sample_sequence"] = reading.sampleSequence;
    value["device_uptime_ms"] = reading.deviceUptimeMs;
    value["voltage_v"] = reading.voltageV;
    value["current_a"] = reading.currentA;
    value["active_power_w"] = reading.activePowerW;
    value["cumulative_energy_kwh"] = reading.cumulativeEnergyKwh;
    value["frequency_hz"] = reading.frequencyHz;
    value["power_factor"] = reading.powerFactor;
    value["appliance_state"] = reading.applianceState;
    value["battery_voltage_v"] = reading.batteryVoltageV;
    value["connection_state"] = reading.connectionState;
    value["anomaly_status"] = reading.anomalyStatus;
    value["quality_status"] = reading.qualityStatus;
    value["firmware_version"] = kFirmwareVersion;
  }

  String body;
  serializeJson(document, body);
  HTTPClient client;
  client.begin(INGEST_URL);
  client.addHeader("Content-Type", "application/json");
  client.addHeader("Idempotency-Key", batchId);
  client.addHeader("x-device-token", DEVICE_INGEST_TOKEN);
  const int status = client.POST(body);
  client.end();
  return status == 200 || status == 201;
}
}  // namespace

void setup() {
  Serial.begin(115200);
  connectWifi();
  Serial.println("EEE2110 software-only simulated telemetry firmware");
  if (!configurationIsReady()) {
    Serial.println(
        "Copy local_secrets.example.h to local_secrets.h before enabling network upload.");
  }
}

void loop() {
  const std::uint32_t nowMs = millis();
  if (nowMs - lastSampleMs < kSampleIntervalMs) return;
  lastSampleMs = nowMs;

  char timestamp[25];
  isoTimestamp(timestamp, sizeof(timestamp));
  buffer[buffered++] = reader.read(nowMs, timestamp);
  Serial.printf("sample=%lu power=%.1fW buffered=%u\n", buffer[buffered - 1].sampleSequence,
                buffer[buffered - 1].activePowerW, static_cast<unsigned>(buffered));

  if (buffered == kBatchSize) {
    if (uploadBatch(buffer, buffered)) {
      buffered = 0;
      return;
    }
    Serial.println("Upload deferred; buffered readings retained.");
    if (WiFi.status() != WL_CONNECTED) connectWifi();
  }
}
