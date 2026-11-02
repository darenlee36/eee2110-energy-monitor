#pragma once
#include <cstdint>

// Advance fixed deadlines, never invent measurements for missed deadlines.
class SamplingCadence {
 public:
  struct Tick { bool due; std::uint32_t lateMs; std::uint32_t missedSlots; };
  SamplingCadence(std::uint32_t intervalMs, std::uint32_t startMs)
      : intervalMs_(intervalMs), deadlineBaseMs_(startMs) {}
  Tick poll(std::uint32_t nowMs) {
    const std::uint32_t elapsed = nowMs - deadlineBaseMs_;
    if (elapsed < intervalMs_) return {false, 0, 0};
    const std::uint32_t slots = elapsed / intervalMs_;
    deadlineBaseMs_ += slots * intervalMs_;
    return {true, elapsed - intervalMs_, slots - 1};
  }
 private:
  const std::uint32_t intervalMs_;
  std::uint32_t deadlineBaseMs_;
};
