// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <Arduino.h>
#include <Wire.h>
#include "CapacityTest.h"

// Register-level driver, following TI INA226 SBOS547. Triggered conversions keep
// voltage and current registers stable while we read one completed measurement.
class Ina226 {
 public:
  uint8_t address = 0;
  const __FlashStringHelper *error = nullptr;

  bool begin() {
    address = 0;
    for (uint8_t addr = 0x40; addr <= 0x4f; ++addr) {
      uint16_t manufacturer, die;
      if (readAt(addr, 0xfe, manufacturer) && readAt(addr, 0xff, die) &&
          manufacturer == 0x5449 && (die & 0xfff0) == 0x2260) {
        if (address) { error = F("Multiple INA226s found; connect only one"); return false; }
        address = addr;
      }
    }
    if (!address) { error = F("INA226 not found; check 5V/GND/SDA/SCL"); return false; }
    const float rawCal = 0.00512f / (Config::CURRENT_LSB_A * Config::SHUNT_OHMS);
    if (rawCal < 1 || rawCal > 32767) {
      error = F("Invalid shunt/current calibration settings"); return false;
    }
    calibration = uint16_t(rawCal + 0.5f);
    actualCurrentLsb = 0.00512f / (calibration * Config::SHUNT_OHMS);
    if (!write(0x00, 0x8000)) return fail();
    delay(2);
    if (!write(0x05, calibration) || !write(0x06, 0x0000) ||
        !write(0x00, CONFIG_POWERDOWN)) return fail();
    error = F("OK");
    return true;
  }

  bool read(Capacity::Sample &sample) {
    uint16_t cal, config, flags, bus, current, shunt;
    // A sensor power reset must not silently produce zero current/BMS detection.
    if (!readAt(address, 0x05, cal) || !readAt(address, 0x00, config)) return fail();
    if (cal != calibration || (config & 0xfff8) != (CONFIG_TRIGGER & 0xfff8)) {
      error = F("Sensor reset/configuration changed"); return false;
    }
    if (!write(0x00, CONFIG_TRIGGER)) return fail();
    const uint32_t started = millis();
    do {
      delay(1);
      if (!readAt(address, 0x06, flags)) return fail();
      if (flags & 0x0004) { error = F("INA226 math overflow"); return false; }
      if (flags & 0x0008) break;
      if (uint32_t(millis() - started) >= 90) {
        error = F("INA226 conversion timeout"); return false;
      }
    } while (true);
    if (!readAt(address, 0x02, bus) || !readAt(address, 0x04, current) ||
        !readAt(address, 0x01, shunt)) return fail();
    if (shunt == 0x7fff || shunt == 0x8000 || bus > 28800) {
      error = F("INA226 input out of range"); return false;
    }
    sample.ms = millis();
    sample.volts = bus * 0.00125f;
    sample.amps = int16_t(current) * actualCurrentLsb;
    error = F("OK");
    return true;
  }

 private:
  // Reserved bit 14, AVG=16, VBUSCT=1100us, VSHCT=1100us: 35.2ms/sample.
  static constexpr uint16_t CONFIG_POWERDOWN = 0x4520;
  static constexpr uint16_t CONFIG_TRIGGER = 0x4523;
  uint16_t calibration = 0;
  float actualCurrentLsb = Config::CURRENT_LSB_A;

  bool fail() { error = F("I2C communication failure"); return false; }

  static bool readAt(uint8_t addr, uint8_t reg, uint16_t &value) {
    Wire.clearWireTimeoutFlag();
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(addr, uint8_t(2)) != 2 || Wire.getWireTimeoutFlag()) return false;
    value = uint16_t(Wire.read()) << 8;
    value |= uint8_t(Wire.read());
    return true;
  }

  bool write(uint8_t reg, uint16_t value) {
    Wire.clearWireTimeoutFlag();
    Wire.beginTransmission(address);
    Wire.write(reg);
    Wire.write(uint8_t(value >> 8));
    Wire.write(uint8_t(value));
    return Wire.endTransmission() == 0 && !Wire.getWireTimeoutFlag();
  }
};
