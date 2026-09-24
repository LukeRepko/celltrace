// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <math.h>
#include <stdint.h>
#include "TesterConfig.h"

namespace Capacity {
enum class State : uint8_t { Idle, Armed, Running, Finished };
enum class Reason : uint8_t {
  None, LowVoltage, PossibleBms, LoadLost, Manual, SensorFault,
  InvalidReading, ReverseCurrent, ExcessCurrent, SampleGap
};

struct Sample {
  uint32_t ms;
  float volts;
  float amps;  // Signed, zero-offset corrected; positive means discharge.
};

// AVR double is only 32 bits. Compensation preserves small increments in long tests.
struct Sum {
  double value = 0;
  double correction = 0;
  void add(double increment) {
    const double y = increment - correction;
    const double next = value + y;
    correction = (next - value) - y;
    value = next;
  }
};

struct Result {
  Sum ah;
  Sum wh;
  uint64_t elapsedMs = 0;
  uint32_t samples = 0;
  float startVolts = 0;
  float minLoadedVolts = 0;
  float peakAmps = 0;
  Sample last = {};
  Sample lastLoaded = {};
  bool hasData = false;
};

struct Settings {
  float ratedAh = Config::RATED_AH;  // Zero means unknown.
  float ratedWh = Config::RATED_WH;
  float endpointV = Config::ENDPOINT_V;
  bool valid() const {
    return isfinite(ratedAh) && isfinite(ratedWh) && isfinite(endpointV) &&
      ratedAh >= 0 && ratedAh <= 100000 && ratedWh >= 0 && ratedWh <= 1000000 &&
      endpointV > 0 && endpointV < Config::MAX_BATTERY_V;
  }
};

class Test {
 public:
  State state = State::Idle;
  Reason reason = Reason::None;
  Result result;
  Settings settings;
  Settings resultSettings;
  bool configure(const Settings &next) {
    if (active() || !next.valid()) return false;
    settings = next;
    return true;
  }
  const Settings &reportSettings() const { return state == State::Idle ? settings : resultSettings; }

  static Reason check(const Sample &s) {
    if (!isfinite(s.volts) || !isfinite(s.amps) || s.volts < 0 ||
        s.volts > Config::MAX_BATTERY_V) return Reason::InvalidReading;
    if (s.amps < Config::REVERSE_CURRENT_A) return Reason::ReverseCurrent;
    if (s.amps > Config::MAX_DISCHARGE_A) return Reason::ExcessCurrent;
    return Reason::None;
  }

  bool active() const { return state == State::Armed || state == State::Running; }

  bool arm(const Sample &s) {
    if (active() || check(s) != Reason::None || s.volts <= settings.endpointV)
      return false;
    resultSettings = settings;
    result = Result{};
    reason = Reason::None;
    candidate = lowPending = lossPending = false;
    state = State::Armed;
    return true;
  }

  void finish(Reason why) {
    if (!active()) return;
    state = State::Finished;
    reason = why;
  }

  void sample(const Sample &s) {
    if (!active()) return;
    const Reason fault = check(s);
    if (fault != Reason::None) { finish(fault); return; }

    if (state == State::Armed) {
      if (s.volts <= settings.endpointV) { finish(Reason::LowVoltage); return; }
      if (s.amps < Config::START_CURRENT_A) {
        candidate = false;
        result = Result{};
        return;
      }
      if (!candidate) {
        candidate = true;
        candidateSince = s.ms;
        beginData(s);
        return;
      }
      if (!integrate(s)) return;
      if (uint32_t(s.ms - candidateSince) >= Config::START_CONFIRM_MS)
        state = State::Running;
      return;
    }

    if (!integrate(s)) return;

    // A collapse must be classified before checking the low-voltage endpoint.
    if (s.amps <= Config::LOST_CURRENT_A) {
      lowPending = false;
      if (!lossPending) { lossPending = true; lossSince = s.ms; }
      if (uint32_t(s.ms - lossSince) >= Config::LOSS_CONFIRM_MS)
        finish(s.volts <= (settings.endpointV * 0.2f < Config::COLLAPSED_V ? settings.endpointV * 0.2f : Config::COLLAPSED_V) ? Reason::PossibleBms : Reason::LoadLost);
    } else {
      lossPending = false;
      if (s.volts <= settings.endpointV) {
        if (!lowPending) { lowPending = true; lowSince = s.ms; }
        if (uint32_t(s.ms - lowSince) >= Config::ENDPOINT_CONFIRM_MS)
          finish(Reason::LowVoltage);
      } else lowPending = false;
    }
  }

  void checkFreshness(uint32_t now) {
    if (active() && result.hasData &&
        uint32_t(now - result.last.ms) > Config::MAX_SAMPLE_GAP_MS)
      finish(Reason::SampleGap);
  }

  double remainingPercent() const {
    if (reportSettings().ratedAh <= 0) return NAN;
    const double percent = 100.0 * (1.0 - result.ah.value / reportSettings().ratedAh);
    return percent < 0 ? 0 : (percent > 100 ? 100 : percent);
  }

  double averageAmps() const {
    return result.elapsedMs ? result.ah.value * 3600000.0 / result.elapsedMs : 0;
  }

 private:
  bool candidate = false;
  bool lowPending = false;
  bool lossPending = false;
  uint32_t candidateSince = 0;
  uint32_t lowSince = 0;
  uint32_t lossSince = 0;

  void beginData(const Sample &s) {
    result = Result{};
    result.hasData = true;
    result.samples = 1;
    result.startVolts = result.minLoadedVolts = s.volts;
    result.peakAmps = s.amps;
    result.last = result.lastLoaded = s;
  }

  bool integrate(const Sample &s) {
    const uint32_t dt = s.ms - result.last.ms; // Unsigned subtraction handles millis wrap.
    if (dt > Config::MAX_SAMPLE_GAP_MS) { finish(Reason::SampleGap); return false; }
    if (!dt) return true;
    // Small negative values within the noise tolerance do not subtract discharge.
    const double a0 = result.last.amps > 0 ? result.last.amps : 0;
    const double a1 = s.amps > 0 ? s.amps : 0;
    const double hours = dt / 3600000.0;
    result.ah.add((a0 + a1) * 0.5 * hours);
    result.wh.add((result.last.volts * a0 + s.volts * a1) * 0.5 * hours);
    result.elapsedMs += dt;
    ++result.samples;
    result.last = s;
    if (s.amps > result.peakAmps) result.peakAmps = s.amps;
    if (s.amps >= Config::START_CURRENT_A) {
      result.lastLoaded = s;
      if (s.volts < result.minLoadedVolts) result.minLoadedVolts = s.volts;
    }
    return true;
  }
};
}  // namespace Capacity
