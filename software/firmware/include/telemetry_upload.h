#pragma once

#include <ArduinoJson.h>

#include <cstddef>
#include <cstdint>
#include <string>

#include "telemetry_queue.h"

struct UploadResponse {
  int status;
  std::string body;
};

// RAM-only delivery state. The queue retains the pending prefix until a matching
// server receipt arrives; neither this queue nor its frozen request survives reset.
template <std::size_t Capacity>
class TelemetryUpload {
 public:
  TelemetryUpload(const char *deviceId, const char *profileId, const char *firmwareVersion)
      : deviceId_(deviceId), profileId_(profileId), firmwareVersion_(firmwareVersion) {
    static_assert(Capacity >= kBatchSize, "Queue must hold a complete batch");
  }

  bool enqueue(const TelemetryReading &reading) {
    return !finalFlushRequested_ && queue_.push(reading);
  }
  bool full() const { return queue_.full(); }
  std::size_t queued() const { return queue_.size(); }
  std::size_t pendingCount() const { return pendingCount_; }
  void requestFinalFlush() { finalFlushRequested_ = true; }
  bool finalFlushRequested() const { return finalFlushRequested_; }
  bool finalFlushComplete() const {
    return finalFlushRequested_ && queue_.size() == 0 && pendingCount_ == 0;
  }

  // Only the transport is injected: native tests exercise the same scheduler,
  // serializer, frozen request and receipt validation used by the ESP32.
  template <typename MakeId, typename Send>
  void poll(std::uint32_t nowMs, bool online, MakeId makeId, Send send) {
    if (!online || queue_.size() == 0) return;
    if (retryScheduled_ && nowMs - lastFailureMs_ < retryDelayMs_) return;
    if (pendingCount_ == 0) {
      TelemetryReading readings[kBatchSize]{};
      const std::size_t count = queue_.copyFront(readings, kBatchSize);
      if (count < kBatchSize && !finalFlushRequested_ &&
          nowMs - readings[0].deviceUptimeMs < kPartialAgeMs) return;
      const std::string id = makeId();
      if (id.empty()) return;
      JsonDocument document;
      document["batch_id"] = id;
      JsonArray values = document["readings"].to<JsonArray>();
      for (std::size_t i = 0; i < count; ++i) {
        const TelemetryReading &reading = readings[i];
        JsonObject value = values.add<JsonObject>();
        value["device_id"] = deviceId_;
        value["profile_id"] = profileId_;
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
        if (reading.hasBatteryVoltage) value["battery_voltage_v"] = reading.batteryVoltageV;
        else value["battery_voltage_v"] = nullptr;
        value["connection_state"] = reading.connectionState;
        value["anomaly_status"] = reading.anomalyStatus;
        value["quality_status"] = reading.qualityStatus;
        value["firmware_version"] = firmwareVersion_;
      }
      if (document.overflowed()) return;
      payload_.clear();
      serializeJson(document, payload_);
      if (payload_.size() != measureJson(document)) {
        payload_.clear();
        return;
      }
      batchId_ = id;
      pendingCount_ = count;
    }

    const UploadResponse response = send(batchId_, payload_);
    if (acknowledged(response)) {
      queue_.popFront(pendingCount_);
      pendingCount_ = 0;
      batchId_.clear();
      payload_.clear();
      retryScheduled_ = false;
      retryDelayMs_ = 1000;
    } else {
      // First retry after one second; subsequent failures double to sixty.
      if (retryScheduled_ && retryDelayMs_ < 60000) {
        retryDelayMs_ = retryDelayMs_ > 30000 ? 60000 : retryDelayMs_ * 2;
      }
      lastFailureMs_ = nowMs;
      retryScheduled_ = true;
    }
  }

 private:
  bool acknowledged(const UploadResponse &response) const {
    if ((response.status != 200 && response.status != 201) || response.body.size() > 2048) {
      return false;
    }
    JsonDocument receipt;
    if (deserializeJson(receipt, response.body)) return false;
    JsonVariantConst data = receipt["data"];
    if (!data.is<JsonObjectConst>() || !data["batch_id"].is<const char *>() ||
        batchId_ != data["batch_id"].as<const char *>() ||
        !data["accepted"].is<unsigned>() || !data["duplicates"].is<unsigned>() ||
        !data["replayed"].is<bool>()) return false;
    const unsigned accepted = data["accepted"].as<unsigned>();
    const unsigned duplicates = data["duplicates"].as<unsigned>();
    return accepted <= pendingCount_ && duplicates <= pendingCount_ &&
           accepted + duplicates == pendingCount_;
  }

  static constexpr std::size_t kBatchSize = 6;
  static constexpr std::uint32_t kPartialAgeMs = 30000;
  const char *deviceId_;
  const char *profileId_;
  const char *firmwareVersion_;
  TelemetryQueue<Capacity> queue_;
  std::string batchId_;
  std::string payload_;
  std::size_t pendingCount_ = 0;
  std::uint32_t lastFailureMs_ = 0;
  std::uint32_t retryDelayMs_ = 1000;
  bool retryScheduled_ = false;
  bool finalFlushRequested_ = false;
};
