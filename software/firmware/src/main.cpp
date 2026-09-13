#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_system.h>
#include <time.h>

#if __has_include("local_secrets.h")
#include "local_secrets.h"
#else
#include "local_secrets.example.h"
#endif

#ifndef TELEMETRY_SIMULATED
#define TELEMETRY_SIMULATED 1
#endif

#if TELEMETRY_SIMULATED
#include "simulated_reader.h"
using MeterReader = SimulatedReader;
MeterReader reader;
#else
#if !__has_include("local_hardware.h")
#error "Copy local_hardware.example.h to local_hardware.h and set supervisor-confirmed pins"
#endif
#include "local_hardware.h"
#include "pzem_reader.h"
PzemReader reader(Serial2, PZEM_RX_PIN, PZEM_TX_PIN);
#endif

#include "telemetry_queue.h"

namespace {
constexpr std::uint32_t kSampleIntervalMs = 5000;
constexpr std::uint32_t kReconnectIntervalMs = 10000;
constexpr std::uint32_t kMaximumRetryMs = 60000;
constexpr std::uint64_t kSequenceBlockSize = 1000000;
constexpr std::size_t kBatchSize = 6;
constexpr std::size_t kQueueCapacity = 120;
#if TELEMETRY_SIMULATED
constexpr char kFirmwareVersion[] = "sim-fw-0.2.0";
#else
constexpr char kFirmwareVersion[] = "pzem-fw-0.2.0";
#endif

TelemetryQueue<kQueueCapacity> queue;
Preferences sequencePreferences;
std::uint64_t nextSequence = 0;
std::uint64_t sequenceLimit = 0;
std::uint32_t lastSampleMs = 0;
std::uint32_t lastReconnectMs = 0;
std::uint32_t nextUploadAttemptMs = 0;
std::uint32_t retryDelayMs = 1000;
String pendingBatchId;
bool ntpConfigured = false;

bool configurationIsReady() {
  return String(WIFI_SSID) != "CHANGE_ME" && String(DEVICE_INGEST_TOKEN) != "CHANGE_ME" &&
         !String(INGEST_URL).startsWith("https://YOUR_PROJECT");
}

bool clockIsReady() { return time(nullptr) >= 1704067200; }

void connectWifi() {
  if (!configurationIsReady() || WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool reserveSequenceBlock() {
  if (!sequencePreferences.begin("energy-meter", false)) return false;
  const std::uint64_t blockStart = sequencePreferences.getULong64("nextSeq", 1);
  const std::uint64_t blockEnd = blockStart + kSequenceBlockSize;
  const std::size_t written = sequencePreferences.putULong64("nextSeq", blockEnd);
  sequencePreferences.end();
  if (written != sizeof(blockEnd)) return false;
  nextSequence = blockStart;
  sequenceLimit = blockEnd;
  return true;
}

bool assignSequence(TelemetryReading &reading) {
  if (nextSequence >= sequenceLimit && !reserveSequenceBlock()) return false;
  reading.sampleSequence = nextSequence++;
  return true;
}

bool isoTimestamp(char *output, std::size_t length) {
  if (!clockIsReady()) return false;
  const time_t now = time(nullptr);
  tm utc{};
  gmtime_r(&now, &utc);
  return strftime(output, length, "%Y-%m-%dT%H:%M:%SZ", &utc) > 0;
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

bool uploadBatch(
    const TelemetryReading *readings, std::size_t count, const String &batchId) {
  if (WiFi.status() != WL_CONNECTED) return false;

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
    if (reading.hasBatteryVoltage) {
      value["battery_voltage_v"] = reading.batteryVoltageV;
    } else {
      value["battery_voltage_v"] = nullptr;
    }
    value["connection_state"] = reading.connectionState;
    value["anomaly_status"] = reading.anomalyStatus;
    value["quality_status"] = reading.qualityStatus;
    value["firmware_version"] = kFirmwareVersion;
  }

  String body;
  serializeJson(document, body);
  HTTPClient client;
  client.setTimeout(8000);
  client.begin(INGEST_URL);
  client.addHeader("Content-Type", "application/json");
  client.addHeader("Idempotency-Key", batchId);
  client.addHeader("x-device-token", DEVICE_INGEST_TOKEN);
  const int status = client.POST(body);
  client.end();
  return status == 200 || status == 201;
}

bool uploadIsDue(std::uint32_t nowMs) {
  return static_cast<std::int32_t>(nowMs - nextUploadAttemptMs) >= 0;
}

void sendQueuedBatch(std::uint32_t nowMs) {
  if (queue.size() < kBatchSize || WiFi.status() != WL_CONNECTED || !uploadIsDue(nowMs)) {
    return;
  }

  TelemetryReading readings[kBatchSize]{};
  const std::size_t count = queue.copyFront(readings, kBatchSize);
  if (pendingBatchId.isEmpty()) pendingBatchId = createBatchId();
  if (uploadBatch(readings, count, pendingBatchId)) {
    queue.popFront(count);
    pendingBatchId = "";
    retryDelayMs = 1000;
    nextUploadAttemptMs = nowMs;
    return;
  }

  nextUploadAttemptMs = nowMs + retryDelayMs;
  retryDelayMs = min(retryDelayMs * 2, kMaximumRetryMs);
  Serial.printf("Upload deferred; %u readings queued.\n", static_cast<unsigned>(queue.size()));
}
}  // namespace

void setup() {
  Serial.begin(115200);
  const bool sequenceReady = reserveSequenceBlock();
  const bool readerReady = reader.begin();
  connectWifi();
  lastReconnectMs = millis();
  Serial.println(TELEMETRY_SIMULATED ? "Simulated telemetry reader" : "PZEM telemetry reader");
  if (!configurationIsReady()) {
    Serial.println("Copy local_secrets.example.h to local_secrets.h before network upload.");
  }
  if (!sequenceReady) Serial.println("Sequence storage unavailable; sampling paused.");
  if (!readerReady) Serial.println("Telemetry reader unavailable; sampling paused.");
}

void loop() {
  const std::uint32_t nowMs = millis();
  if (WiFi.status() == WL_CONNECTED && !ntpConfigured) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpConfigured = true;
  } else if (
      WiFi.status() != WL_CONNECTED && nowMs - lastReconnectMs >= kReconnectIntervalMs) {
    lastReconnectMs = nowMs;
    connectWifi();
  }

  if (nowMs - lastSampleMs >= kSampleIntervalMs) {
    lastSampleMs = nowMs;
    char timestamp[25];
    if (!isoTimestamp(timestamp, sizeof(timestamp))) {
      Serial.println("Clock not synchronized; sample deferred.");
    } else if (queue.full()) {
      Serial.println("Telemetry queue full; sample deferred until upload succeeds.");
    } else {
      TelemetryReading reading = reader.read(nowMs, timestamp);
      reading.connectionState = WiFi.status() == WL_CONNECTED ? "online" : "offline";
      if (assignSequence(reading) && queue.push(reading)) {
        Serial.printf(
            "sample=%llu power=%.1fW queued=%u\n",
            static_cast<unsigned long long>(reading.sampleSequence), reading.activePowerW,
            static_cast<unsigned>(queue.size()));
      } else {
        Serial.println("Persistent sequence unavailable; sample rejected.");
      }
    }
  }

  sendQueuedBatch(nowMs);
}
