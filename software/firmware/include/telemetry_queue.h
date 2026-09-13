#pragma once

#include <cstddef>

#include "telemetry.h"

template <std::size_t Capacity>
class TelemetryQueue {
 public:
  bool push(const TelemetryReading &reading) {
    if (size_ == Capacity) return false;
    readings_[(head_ + size_) % Capacity] = reading;
    ++size_;
    return true;
  }

  std::size_t copyFront(TelemetryReading *output, std::size_t maximum) const {
    const std::size_t count = size_ < maximum ? size_ : maximum;
    for (std::size_t index = 0; index < count; ++index) {
      output[index] = readings_[(head_ + index) % Capacity];
    }
    return count;
  }

  void popFront(std::size_t count) {
    const std::size_t removed = count < size_ ? count : size_;
    head_ = (head_ + removed) % Capacity;
    size_ -= removed;
  }

  std::size_t size() const { return size_; }
  bool full() const { return size_ == Capacity; }

 private:
  TelemetryReading readings_[Capacity]{};
  std::size_t head_ = 0;
  std::size_t size_ = 0;
};
