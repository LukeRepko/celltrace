// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

#include <Arduino.h>
#include <Wire.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>
#include "CapacityTest.h"
#include "Ina226.h"

using Capacity::Reason;
using Capacity::State;

Ina226 sensor;
Capacity::Test test;
Capacity::Sample live = {};
bool sensorReady = false;
bool liveValid = false;
bool csv = false;
float zeroOffset = 0;
uint32_t lastSampleAttempt = 0;
uint32_t lastDisplay = 0;
uint32_t lastRetry = 0;
uint32_t testNumber = 0;
bool summaryPrinted = false;
const __FlashStringHelper *stopSensorError = nullptr;

const __FlashStringHelper *stateText() {
  switch (test.state) {
    case State::Idle: return F("IDLE");
    case State::Armed: return F("ARMED");
    case State::Running: return F("RUNNING");
    default: return F("FINISHED");
  }
}

const __FlashStringHelper *reasonText(Reason reason) {
  switch (reason) {
    case Reason::None: return F("none");
    case Reason::LowVoltage: return F("low_voltage_endpoint");
    case Reason::PossibleBms: return F("output_lost_possible_BMS_cutoff");
    case Reason::LoadLost: return F("load_disconnected_or_interrupted");
    case Reason::Manual: return F("manual_stop_partial");
    case Reason::SensorFault: return F("sensor_fault_incomplete");
    case Reason::InvalidReading: return F("invalid_voltage_incomplete");
    case Reason::ReverseCurrent: return F("reversed_current_incomplete");
    case Reason::ExcessCurrent: return F("excess_current_incomplete");
    default: return F("measurement_gap_incomplete");
  }
}

void printElapsed(uint64_t ms) {
  const uint32_t seconds = uint32_t(ms / 1000);
  const uint32_t hours = seconds / 3600;
  if (hours < 10) Serial.print('0');
  Serial.print(hours); Serial.print(':');
  if ((seconds / 60) % 60 < 10) Serial.print('0');
  Serial.print((seconds / 60) % 60); Serial.print(':');
  if (seconds % 60 < 10) Serial.print('0');
  Serial.print(seconds % 60);
}

void field(const __FlashStringHelper *label, double value, uint8_t places = 3) {
  Serial.print(F("# ")); Serial.print(label); Serial.println(value, places);
}

void printHelp() {
  Serial.println(F("# Commands (send with Newline or CR):"));
  Serial.println(F("# start   - clear totals; arm with a fully charged battery; then connect load"));
  Serial.println(F("# stop    - freeze result; YOU must disconnect the resistor"));
  Serial.println(F("# status  - live readings and test totals"));
  Serial.println(F("# summary - reprint frozen result"));
  Serial.println(F("# zero    - 32-sample offset calibration; resistor MUST be disconnected"));
  Serial.println(F("# csv     - CSV mode; lines beginning # are comments"));
  Serial.println(F("# human   - readable mode"));
  Serial.println(F("# settings - print effective next-test settings"));
  Serial.println(F("# configure AH WH ENDPOINT - set between tests; 0 rating means unknown"));
  Serial.println(F("# help    - these commands"));
}

void printSettings() {
  Serial.println(F("# CELLTRACE protocol 1"));
  Serial.print(F("# Settings: "));
  Serial.print(test.settings.ratedAh, 6); Serial.print(',');
  Serial.print(test.settings.ratedWh, 6); Serial.print(',');
  Serial.println(test.settings.endpointV, 3);
  field(F("Rated capacity Ah: "), test.settings.ratedAh, 6);
  field(F("Rated energy Wh: "), test.settings.ratedWh, 6);
  field(F("Voltage endpoint V: "), test.settings.endpointV);
}

void printPercent(double value, float rating, uint8_t places) {
  if (rating > 0) Serial.print(value / rating * 100, places);
}

void percentField(const __FlashStringHelper *label, double value, float rating) {
  Serial.print(F("# ")); Serial.print(label);
  if (rating > 0) printPercent(value, rating, 2);
  else Serial.print(F("unknown"));
  Serial.println();
}

void csvHeader() {
  Serial.println(F("test_id,uptime_ms,state,valid,voltage_v,current_a,power_w,elapsed_s,ah,mah,wh,kwh,remaining_est_pct,rated_ah_pct,rated_wh_pct,reason"));
}

void printSummary() {
  if (test.state != State::Finished) {
    Serial.println(F("# No finished test. Use status for current totals.")); return;
  }
  const Capacity::Result &r = test.result;
  Serial.println(F("# ===== CELLTRACE CAPACITY TEST SUMMARY ====="));
  Serial.print(F("# Test number: ")); Serial.println(testNumber);
  Serial.print(F("# End reason: ")); Serial.println(reasonText(test.reason));
  if (test.reason == Reason::LowVoltage && r.hasData)
    Serial.println(F("# Result: measured to configured voltage endpoint; not a manufacturer pass/fail test"));
  else if (test.reason == Reason::PossibleBms)
    Serial.println(F("# Result: output lost; BMS operation NOT verified; check fuse and wiring"));
  else Serial.println(F("# Result: partial/interrupted; full battery capacity NOT established"));
  if (!r.hasData) Serial.println(F("# No discharge readings accumulated."));
  else {
    Serial.print(F("# Discharge elapsed: ")); printElapsed(r.elapsedMs); Serial.println();
    field(F("Start loaded voltage V: "), r.startVolts);
    field(F("Last loaded voltage V: "), r.lastLoaded.volts);
    field(F("Minimum loaded voltage V: "), r.minLoadedVolts);
    field(F("Last valid voltage V: "), r.last.volts);
    field(F("Last loaded current A: "), r.lastLoaded.amps);
    field(F("Last valid current A: "), r.last.amps);
    field(F("Average current A: "), test.averageAmps());
    field(F("Peak current A: "), r.peakAmps);
    field(F("Discharged Ah: "), r.ah.value, 4);
    field(F("Discharged mAh: "), r.ah.value * 1000, 1);
    field(F("Delivered Wh: "), r.wh.value, 4);
    field(F("Delivered kWh: "), r.wh.value / 1000, 6);
    percentField(F("Rated Ah delivered %: "), r.ah.value, test.reportSettings().ratedAh);
    percentField(F("Rated Wh delivered %: "), r.wh.value, test.reportSettings().ratedWh);
    Serial.print(F("# Valid integrated samples: ")); Serial.println(r.samples);
  }
  if (test.reason == Reason::SensorFault && stopSensorError) {
    Serial.print(F("# Sensor error at stop: ")); Serial.println(stopSensorError);
  }
  Serial.println(F("# DISCONNECT LOAD. Software cannot switch off the resistor."));
  Serial.println(F("# Result is frozen in RAM until start/reset. Recharge after disconnecting."));
}

void printStatus() {
  const Capacity::Result &r = test.result;
  if (csv) {
    Serial.print(testNumber); Serial.print(',');
    Serial.print(liveValid ? live.ms : millis()); Serial.print(',');
    Serial.print(stateText()); Serial.print(',');
    Serial.print(liveValid ? 1 : 0); Serial.print(',');
    if (liveValid) {
      Serial.print(live.volts, 4); Serial.print(',');
      Serial.print(live.amps, 4); Serial.print(',');
      Serial.print(live.volts * live.amps, 4); Serial.print(',');
    } else Serial.print(F(",,,"));
    Serial.print(double(r.elapsedMs) / 1000, 3); Serial.print(',');
    Serial.print(r.ah.value, 6); Serial.print(',');
    Serial.print(r.ah.value * 1000, 3); Serial.print(',');
    Serial.print(r.wh.value, 6); Serial.print(',');
    Serial.print(r.wh.value / 1000, 8); Serial.print(',');
    if (test.state != State::Idle && test.reportSettings().ratedAh > 0) Serial.print(test.remainingPercent(), 2);
    Serial.print(',');
    printPercent(r.ah.value, test.reportSettings().ratedAh, 2); Serial.print(',');
    printPercent(r.wh.value, test.reportSettings().ratedWh, 2); Serial.print(',');
    Serial.println(reasonText(test.reason));
  } else {
    Serial.print(F("# CELLTRACE #")); Serial.print(testNumber);
    Serial.print(F("  ")); Serial.print(stateText());
    Serial.print(F("  elapsed ")); printElapsed(r.elapsedMs); Serial.println();
    if (liveValid) {
      Serial.print(F("# Live: ")); Serial.print(live.volts, 3); Serial.print(F(" V  "));
      Serial.print(live.amps, 3); Serial.print(F(" A  "));
      Serial.print(live.volts * live.amps, 2); Serial.println(F(" W"));
      const Reason check = Capacity::Test::check(live);
      if (check != Reason::None) {
        Serial.print(F("# CHECK WIRING/LOAD: ")); Serial.println(reasonText(check));
      }
    } else {
      Serial.print(F("# Live readings unavailable: ")); Serial.println(sensor.error);
    }
    Serial.print(F("# Discharged: ")); Serial.print(r.ah.value, 4); Serial.print(F(" Ah / "));
    Serial.print(r.ah.value * 1000, 1); Serial.println(F(" mAh"));
    Serial.print(F("# Energy: ")); Serial.print(r.wh.value, 3); Serial.print(F(" Wh / "));
    Serial.print(r.wh.value / 1000, 6); Serial.println(F(" kWh"));
    if (test.state != State::Idle && test.reportSettings().ratedAh > 0) {
      Serial.print(F("# Remaining estimate (rated Ah; full at start): "));
      Serial.print(test.remainingPercent(), 1); Serial.println('%');
    } else Serial.println(F("# Remaining estimate: -- (start a fully charged battery test)"));
    Serial.print(F("# Rated delivered: ")); printPercent(r.ah.value, test.reportSettings().ratedAh, 1);
    Serial.print(F("% Ah / ")); printPercent(r.wh.value, test.reportSettings().ratedWh, 1);
    Serial.println(F("% Wh"));
  }
  if (test.state == State::Armed)
    Serial.println(F("# Armed: connect load; timer starts at sustained discharge."));
  if (test.state == State::Finished)
    Serial.println(F("# TEST ENDED - DISCONNECT LOAD. Live monitoring continues; totals are frozen."));
}

void readMeasurement() {
  Capacity::Sample s;
  if (!sensor.read(s)) {
    liveValid = false;
    sensorReady = false;
    if (test.active()) stopSensorError = sensor.error;
    test.finish(Reason::SensorFault);
    return;
  }
  s.amps -= zeroOffset;
  live = s;
  liveValid = true;
  test.sample(s);
}

void calibrateZero() {
  if (test.active()) {
    Serial.println(F("# Zero refused: stop test and disconnect load first.")); return;
  }
  if (!sensorReady) { Serial.println(F("# Zero refused: sensor unavailable.")); return; }
  Serial.println(F("# Zeroing: resistor must be DISCONNECTED. Sampling for about 3 seconds..."));
  float sum = 0;
  float minimum = 100;
  float maximum = -100;
  for (uint8_t n = 0; n < Config::ZERO_SAMPLES; ++n) {
    Capacity::Sample s;
    if (!sensor.read(s)) {
      sensorReady = liveValid = false;
      Serial.println(F("# Zero failed: sensor error; previous offset retained.")); return;
    }
    if (fabs(s.amps) > Config::MAX_ZERO_OFFSET_A || Capacity::Test::check(s) != Reason::None) {
      Serial.println(F("# Zero refused: current/voltage too large or invalid; disconnect resistor.")); return;
    }
    sum += s.amps;
    if (s.amps < minimum) minimum = s.amps;
    if (s.amps > maximum) maximum = s.amps;
    delay(60);
  }
  if (maximum - minimum > 0.02f) {
    Serial.println(F("# Zero refused: unstable current; previous offset retained.")); return;
  }
  zeroOffset = sum / Config::ZERO_SAMPLES;
  field(F("Current offset A (RAM only): "), zeroOffset, 6);
  liveValid = false;
}

bool parseConfiguration(char *text, Capacity::Settings &settings) {
  float *values[] = {&settings.ratedAh, &settings.ratedWh, &settings.endpointV};
  for (uint8_t i = 0; i < 3; ++i) {
    while (*text == ' ' || *text == '\t') ++text;
    char *end = nullptr;
    const double value = strtod(text, &end);
    if (end == text || !isfinite(value)) return false;
    *values[i] = value;
    text = end;
    if (i < 2 && *text != ' ' && *text != '\t') return false;
  }
  while (*text == ' ' || *text == '\t') ++text;
  return *text == 0 && settings.valid();
}

void command(char *line) {
  while (*line == ' ' || *line == '\t') ++line;
  size_t len = strlen(line);
  while (len && (line[len - 1] == ' ' || line[len - 1] == '\t')) line[--len] = 0;
  if (!len) return;
  if (!strcmp(line, "help")) printHelp();
  else if (!strcmp(line, "status")) printStatus();
  else if (!strcmp(line, "settings")) printSettings();
  else if (!strncmp(line, "configure ", 10)) {
    Capacity::Settings next;
    if (!parseConfiguration(line + 10, next) || !test.configure(next)) {
      Serial.println(F("# Configure refused: invalid settings or active test"));
    } else {
      Serial.println(F("# Settings applied; effective for next test"));
      printSettings();
    }
  }
  else if (!strcmp(line, "summary")) printSummary();
  else if (!strcmp(line, "csv")) { csv = true; csvHeader(); }
  else if (!strcmp(line, "human")) { csv = false; printStatus(); }
  else if (!strcmp(line, "zero")) calibrateZero();
  else if (!strcmp(line, "stop")) {
    if (!test.active()) Serial.println(F("# No active test; disconnect load if connected."));
    else test.finish(Reason::Manual);
  } else if (!strcmp(line, "start")) {
    if (!liveValid || uint32_t(millis() - live.ms) > Config::MAX_SAMPLE_GAP_MS || !test.arm(live)) {
      Serial.println(F("# Start refused: need fresh valid readings, voltage > endpoint, and no active test."));
    } else {
      ++testNumber;
      summaryPrinted = false;
      stopSensorError = nullptr;
      Serial.println(F("# New test armed. Assumes FULLY CHARGED battery. Connect load now."));
      if (live.amps >= Config::START_CURRENT_A)
        Serial.println(F("# Load already drawing: prior discharge is NOT included."));
    }
  } else Serial.println(F("# Unknown command. Type help."));
}

void readCommands() {
  static char line[96];
  static uint8_t length = 0;
  static bool overflow = false;
  for (uint8_t count = 0; count < 64 && Serial.available(); ++count) {
    const char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (overflow) Serial.println(F("# Command too long; discarded."));
      else { line[length] = 0; command(line); }
      length = 0; overflow = false;
      return; // Service the measurement loop between commands.
    } else if (c == '\b' || c == 127) {
      if (length && !overflow) --length;
    } else if (c >= 32 && c <= 126) {
      if (length < sizeof(line) - 1 && !overflow) line[length++] = tolower(c);
      else overflow = true;
    }
  }
}

void setup() {
  Serial.begin(Config::SERIAL_BAUD);
  Wire.begin();
  Wire.setClock(100000);
  Wire.setWireTimeout(25000, true);
  Serial.println(F("# CELLTRACE CAPACITY CHECKER - Mega 2560 + INA226"));
  Serial.println(F("# RESET: prior results/zero offset are lost; no automatic test resume."));
  Serial.println(F("# MANUAL DISCONNECT ONLY. No relay/MOSFET is controlled."));
  printSettings();
  field(F("Shunt ohms: "), Config::SHUNT_OHMS, 6);

  sensorReady = sensor.begin();
  if (sensorReady) {
    Serial.print(F("# INA226 verified at I2C 0x")); Serial.println(sensor.address, HEX);
  } else { Serial.print(F("# Sensor error: ")); Serial.println(sensor.error); }
  printHelp();
}

void loop() {
  const uint32_t now = millis();
  test.checkFreshness(now);
  if (sensorReady && uint32_t(now - lastSampleAttempt) >= Config::SAMPLE_MS) {
    lastSampleAttempt = now;
    readMeasurement();
  }
  if (test.state == State::Finished && !summaryPrinted) {
    printSummary();
    summaryPrinted = true;
  }
  if (!sensorReady && !test.active() && uint32_t(now - lastRetry) >= 2000) {
    lastRetry = now;
    sensorReady = sensor.begin();
    if (sensorReady) Serial.println(F("# Sensor recovered; any finished test remains frozen."));
  }
  readCommands();
  if (uint32_t(millis() - lastDisplay) >= Config::DISPLAY_MS) {
    lastDisplay = millis();
    printStatus();
  }
}
