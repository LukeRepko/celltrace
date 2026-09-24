// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

// Host-only tests: see README.md. No battery or Arduino required.
#include "CapacityTest.h"
#include <assert.h>
#include <cmath>
#include <cstdio>
#include <limits>
#include <initializer_list>

using namespace Capacity;

void near(double actual, double expected, double tolerance = 0.00001) {
  if (std::fabs(actual - expected) > tolerance) {
    std::fprintf(stderr, "actual %.9f != expected %.9f\n", actual, expected);
    assert(false);
  }
}

Test running(uint32_t origin = 0) {
  Test t;
  assert(t.arm({origin, 13.0f, 0}));
  for (uint32_t dt = 0; dt <= 500; dt += 100)
    t.sample({uint32_t(origin + dt), 13.0f, 2.0f});
  assert(t.state == State::Running);
  return t;
}

void constantAndAboveRating() {
  Test t = running();
  for (uint32_t ms = 600; ms <= 3600000; ms += 100)
    t.sample({ms, 13, 2});
  near(t.result.ah.value, 2);
  near(t.result.wh.value, 26);
  near(t.averageAmps(), 2);
  near(t.remainingPercent(), 90);
  assert(t.result.elapsedMs == 3600000);
  for (uint32_t ms = 3600100; ms <= 39600000; ms += 100)
    t.sample({ms, 13, 2});
  near(t.result.ah.value, 22);
  near(t.result.wh.value, 286);
  near(t.remainingPercent(), 0);
  assert(t.state == State::Running); // Never stop at estimated 0%.
}

void variableAndIrregular() {
  Test t = running();
  const double initialAh = t.result.ah.value;
  const double initialWh = t.result.wh.value;
  t.sample({750, 12, 4}); // 0.25s, avg 3A and avg (26+48)/2 = 37W
  t.sample({1250, 11, 6}); // 0.5s, avg 5A and avg 57W
  near(t.result.ah.value - initialAh, (0.25 * 3 + 0.5 * 5) / 3600);
  near(t.result.wh.value - initialWh, (0.25 * 37 + 0.5 * 57) / 3600);
  near(t.result.minLoadedVolts, 11);
  near(t.result.peakAmps, 6);
}

void armingAndManual() {
  Test t;
  assert(!t.arm({0, 10, 0}));
  assert(!t.arm({0, 13, -2}));
  assert(!t.arm({0, 16, 0}));
  assert(t.arm({0, 13, 0}));
  assert(!t.arm({0, 13, 0}));
  t.sample({5000, 13, 0});
  assert(!t.result.hasData);
  t.sample({5100, 13, 2});
  t.sample({5200, 13, 0}); // Spurious current doesn't start timer.
  assert(!t.result.hasData);
  for (uint32_t ms = 6000; ms <= 6500; ms += 100) t.sample({ms, 13, 2});
  assert(t.state == State::Running);
  assert(t.result.elapsedMs == 500);
  t.finish(Reason::Manual);
  const double total = t.result.ah.value;
  t.sample({6600, 13, 2});
  near(t.result.ah.value, total);
  assert(t.reason == Reason::Manual);
  assert(t.arm({7000, 13, 0}));
  assert(!t.result.hasData);
  near(t.result.ah.value, 0);
}

void lowVoltage() {
  Test t = running();
  t.sample({600, 9.9f, 4.95f});
  t.sample({700, 10.1f, 5.05f}); // A brief dip does not stop the test.
  assert(t.state == State::Running);
  t.sample({800, 9.9f, 4.95f});
  t.sample({900, 9.9f, 4.95f});
  t.sample({1000, 9.9f, 4.95f});
  assert(t.state == State::Running);
  t.sample({1100, 9.9f, 4.95f});
  assert(t.state == State::Finished && t.reason == Reason::LowVoltage);
  const double total = t.result.wh.value;
  t.sample({1200, 13, 2});
  near(t.result.wh.value, total); // Recovery cannot restart the test.
}

void lossAndBms() {
  for (float voltage : {0.0f, 13.0f}) {
    Test t = running();
    for (uint32_t ms = 600; ms <= 1100; ms += 100) t.sample({ms, voltage, 0});
    assert(t.state == State::Finished);
    assert(t.reason == (voltage == 0 ? Reason::PossibleBms : Reason::LoadLost));
    near(t.result.lastLoaded.volts, 13);
    near(t.result.lastLoaded.amps, 2);
    near(t.result.minLoadedVolts, 13); // Exclude post-disconnection zero voltage.
  }
  Test t = running();
  t.sample({600, 0, 0});
  t.sample({700, 13, 2});
  assert(t.state == State::Running);
}

void failuresAndRollover() {
  Test t = running();
  const double total = t.result.ah.value;
  t.sample({1600, 13, 2});
  assert(t.reason == Reason::SampleGap);
  near(t.result.ah.value, total); // Never interpolate over unknown data.
  t = running();
  t.checkFreshness(1601);
  assert(t.reason == Reason::SampleGap);
  t = running();
  t.finish(Reason::SensorFault);
  t.sample({600, 0, 0});
  assert(t.reason == Reason::SensorFault);
  near(t.result.ah.value, total);
  t = running();
  t.sample({600, 13, -1});
  assert(t.reason == Reason::ReverseCurrent);
  t = running();
  t.sample({600, 13, 11});
  assert(t.reason == Reason::ExcessCurrent);
  t = running();
  t.sample({600, std::numeric_limits<float>::quiet_NaN(), 2});
  assert(t.reason == Reason::InvalidReading);
  const uint32_t origin = UINT32_MAX - 300;
  t = running(origin);
  t.sample({uint32_t(origin + 600), 13, 2});
  assert(t.state == State::Running);
  assert(t.result.elapsedMs == 600);
  near(t.result.ah.value, 2.0 * 0.6 / 3600);
  // Check confirmation timers across wrap as well.
  t = running(UINT32_MAX - 700);
  for (uint32_t dt = 600; dt <= 900; dt += 100)
    t.sample({uint32_t(UINT32_MAX - 700 + dt), 9.9f, 4.95f});
  assert(t.reason == Reason::LowVoltage);
}

void configurableProfiles() {
  Test t;
  Capacity::Settings settings;
  settings.ratedAh = 5;
  settings.ratedWh = 0;
  settings.endpointV = 11;
  assert(t.configure(settings));
  assert(t.arm({0, 13, 2}));
  assert(!t.configure(settings));
  for (uint32_t ms = 0; ms <= 1000; ms += 100) t.sample({ms, 13, 2});
  for (uint32_t ms = 1100; ms <= 1400; ms += 100) t.sample({ms, 10.9f, 2});
  assert(t.reason == Reason::LowVoltage);
  settings.ratedAh = 10;
  assert(t.configure(settings));
  assert(t.reportSettings().ratedAh == 5); // Frozen report keeps the old rating.
  settings.ratedAh = 0;
  assert(t.configure(settings));
  assert(t.arm({2000, 13, 2}));
  assert(std::isnan(t.remainingPercent()));
  t.finish(Reason::Manual);
  settings.endpointV = 15;
  assert(!t.configure(settings));
  settings.endpointV = 10;
  settings.ratedAh = -1;
  assert(!t.configure(settings));
  settings.ratedAh = std::numeric_limits<float>::infinity();
  assert(!t.configure(settings));
}

int main() {
  constantAndAboveRating();
  variableAndIrregular();
  armingAndManual();
  lowVoltage();
  lossAndBms();
  failuresAndRollover();
  configurableProfiles();
  std::puts("PASS: integration, rated-capacity overrun, arming, manual stop, endpoints, losses, faults, millis rollover");
}
