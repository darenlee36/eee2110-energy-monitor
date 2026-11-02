#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
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

#include "telemetry_upload.h"
#include "sampling_cadence.h"

namespace {
// GTS Root R4 from https://pki.goog/repo/certs/gtsr4.pem; expires 2036-06-22.
// Recheck the endpoint chain when its CA changes; never fall back to insecure TLS.
constexpr char kIngestRootCa[] = R"PEM(-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
)PEM";
constexpr std::uint32_t kSampleIntervalMs = 5000;
constexpr std::uint32_t kReconnectIntervalMs = 10000;
constexpr std::uint64_t kSequenceBlockSize = 1000000;
constexpr std::size_t kQueueCapacity = 120;
#if TELEMETRY_SIMULATED
constexpr char kFirmwareVersion[] = "sim-fw-0.2.4";
#else
constexpr char kFirmwareVersion[] = "pzem-fw-0.2.4";
#endif

TelemetryUpload<kQueueCapacity> uploader(DEVICE_ID, PROFILE_ID, kFirmwareVersion);
Preferences sequencePreferences;
std::uint64_t nextSequence = 0;
std::uint64_t sequenceLimit = 0;
SamplingCadence *cadence = nullptr;
std::uint32_t previousSampleMs = 0;
bool havePreviousSample = false;
bool acquisitionReady = false;
std::uint32_t lastReconnectMs = 0;
bool ntpConfigured = false;
bool flushReceiptPrinted = false;

bool configurationIsReady() {
  return String(WIFI_SSID) != "CHANGE_ME" && String(DEVICE_INGEST_TOKEN) != "CHANGE_ME" &&
         String(WIFI_SSID).length() > 0 && String(WIFI_PASSWORD) != "CHANGE_ME" &&
         String(DEVICE_INGEST_TOKEN).length() >= 32 &&
         String(INGEST_URL).startsWith("https://") &&
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

UploadResponse uploadBatch(const std::string &batchId, const std::string &body) {
  if (WiFi.status() != WL_CONNECTED) return {-1, ""};
  const std::uint32_t startedMs = millis();
  WiFiClientSecure transport;
  transport.setCACert(kIngestRootCa);
  HTTPClient client;
  client.setTimeout(8000);
  if (!client.begin(transport, INGEST_URL)) return {-1, ""};
  client.addHeader("Content-Type", "application/json");
  client.addHeader("Idempotency-Key", batchId.c_str());
  client.addHeader("x-device-token", DEVICE_INGEST_TOKEN);
  const int status = client.POST(String(body.c_str()));
  // Read the receipt before closing the connection. Status alone is not an ack.
  const String receipt = (status == 200 || status == 201) ? client.getString() : String();
  client.end();
  Serial.printf("Upload HTTP status=%d samples=%u duration_ms=%u\n", status,
                static_cast<unsigned>(uploader.pendingCount()), millis() - startedMs);
  return {status, std::string(receipt.c_str(), receipt.length())};
}

void readControlCommand() {
  static char command[6]{};
  static std::size_t length = 0;
  static bool overflow = false;
  // Bound work per loop so continuous serial input cannot starve uploads.
  for (unsigned i = 0; i < 32 && Serial.available(); ++i) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\r') continue;
    if (value == '\n') {
      command[length] = '\0';
      if (!overflow && strcmp(command, "flush") == 0) {
        uploader.requestFinalFlush();
        Serial.println("Final flush requested; sampling stopped. Waiting for server receipts.");
      }
      length = 0;
      overflow = false;
    } else if (length < sizeof(command) - 1) command[length++] = value;
    else overflow = true;
  }
}
void networkTask(void *) {
  for (;;) {
    const std::uint32_t nowMs = millis();
    if (WiFi.status() == WL_CONNECTED && !ntpConfigured) {
      configTime(0, 0, "pool.ntp.org", "time.google.com");
      ntpConfigured = true;
    } else if (WiFi.status() != WL_CONNECTED &&
               nowMs - lastReconnectMs >= kReconnectIntervalMs) {
      lastReconnectMs = nowMs;
      connectWifi();
    }
    uploader.poll(nowMs, WiFi.status() == WL_CONNECTED,
                  []() { return std::string(createBatchId().c_str()); }, uploadBatch);
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
}  // namespace

void setup() {
  Serial.begin(115200);
  const bool sequenceReady = reserveSequenceBlock();
  const bool readerReady = reader.begin();
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.printf("WiFi disconnect reason=%u\n", info.wifi_sta_disconnected.reason);
    } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.println("WiFi connected; IP assigned.");
    }
  });
  if (configurationIsReady()) {
    WiFi.mode(WIFI_STA);
    const int networks = WiFi.scanNetworks();
    bool found = false;
    for (int index = 0; index < networks; ++index) {
      if (WiFi.SSID(index) == WIFI_SSID) found = true;
    }
    Serial.printf("Hotspot scan: networks=%d saved network=%s\n", networks, found ? "found" : "not found");
    WiFi.scanDelete();
  }
  lastReconnectMs = millis() - kReconnectIntervalMs;
  Serial.println(TELEMETRY_SIMULATED ? "Simulated telemetry reader" : "PZEM telemetry reader");
  Serial.println("Send flush followed by newline to stop sampling and upload queued readings.");
  if (!configurationIsReady()) {
    Serial.println("Copy local_secrets.example.h to local_secrets.h before network upload.");
  }
  if (!sequenceReady) Serial.println("Sequence storage unavailable; sampling paused.");
  if (!readerReady) Serial.println("Telemetry reader unavailable; sampling paused.");
  static SamplingCadence fixedCadence(kSampleIntervalMs, millis());
  cadence = &fixedCadence;
  // Core 1 owns acquisition/sequence allocation/serial commands. Core 0 owns
  // Wi-Fi/TLS/HTTP and the frozen retry request. No network call runs in loop().
  const bool networkReady = xTaskCreatePinnedToCore(
      networkTask, "telemetry-network", 12288, nullptr, 1, nullptr, 0) == pdPASS;
  acquisitionReady = sequenceReady && readerReady && networkReady;
  if (!networkReady) Serial.println("Network worker unavailable; sampling paused.");
}

void loop() {
  const std::uint32_t nowMs = millis();
  readControlCommand();

  const auto tick = cadence->poll(nowMs);
  if (acquisitionReady && !uploader.finalFlushRequested() && tick.due) {
    if (tick.missedSlots) {
      Serial.printf("Cadence missed_slots=%u late_ms=%u\n", tick.missedSlots, tick.lateMs);
    }
    char timestamp[25];
    if (!isoTimestamp(timestamp, sizeof(timestamp))) {
      Serial.println("Clock not synchronized; sample deferred.");
    } else if (uploader.full()) {
      Serial.println("Telemetry queue full; sample deferred until upload succeeds.");
    } else {
      TelemetryReading reading = reader.read(nowMs, timestamp);
      reading.connectionState = WiFi.status() == WL_CONNECTED ? "online" : "offline";
      if (assignSequence(reading) && uploader.enqueue(reading)) {
        Serial.printf(
            "sample=%llu power=%.1fW queued=%u uptime_ms=%u interval_ms=%u late_ms=%u missed_slots=%u\n",
            static_cast<unsigned long long>(reading.sampleSequence), reading.activePowerW,
            static_cast<unsigned>(uploader.queued()), nowMs,
            havePreviousSample ? nowMs - previousSampleMs : 0, tick.lateMs, tick.missedSlots);
        previousSampleMs = nowMs;
        havePreviousSample = true;
      } else {
        Serial.println("Persistent sequence unavailable; sample rejected.");
      }
    }
  }

  if (uploader.finalFlushComplete() && !flushReceiptPrinted) {
    Serial.println("Final flush complete: all queued readings acknowledged by server.");
    Serial.println("This confirms upload only; check cycle completion separately in the dashboard.");
    flushReceiptPrinted = true;
  }
  delay(1);
}
