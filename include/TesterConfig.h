// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <stdint.h>

namespace Config {
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr float SHUNT_OHMS = 0.002f;  // R002. Verify the marking on YOUR module.
constexpr float CURRENT_LSB_A = 0.001f;
constexpr float RATED_AH = 20.0f;
constexpr float RATED_WH = 256.0f;
constexpr float ENDPOINT_V = 10.0f;
constexpr float MAX_BATTERY_V = 15.0f;
constexpr float MAX_DISCHARGE_A = 10.0f;  // Expected load is about 5-7.3 A.
constexpr float START_CURRENT_A = 0.10f;
constexpr float LOST_CURRENT_A = 0.05f;
constexpr float REVERSE_CURRENT_A = -0.05f;
constexpr float COLLAPSED_V = 2.0f;
constexpr float MAX_ZERO_OFFSET_A = 0.05f;
constexpr uint32_t SAMPLE_MS = 100;
constexpr uint32_t DISPLAY_MS = 1000;
constexpr uint32_t START_CONFIRM_MS = 500;
constexpr uint32_t ENDPOINT_CONFIRM_MS = 300;
constexpr uint32_t LOSS_CONFIRM_MS = 500;
constexpr uint32_t MAX_SAMPLE_GAP_MS = 1000;
constexpr uint8_t ZERO_SAMPLES = 32;
}  // namespace Config
