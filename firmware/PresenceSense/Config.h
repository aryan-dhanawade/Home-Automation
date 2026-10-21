// PresenceSense hub configuration. Values the backend also relies on are marked.
#pragma once
#include <Arduino.h>

namespace Config {

// --- Pins (ESP32 DevKit C) ---------------------------------------------------
constexpr uint8_t PIN_TRIG = 5;
constexpr uint8_t PIN_ECHO = 18;
constexpr uint8_t PIN_LAMP_A = 25;  // living room
constexpr uint8_t PIN_LAMP_B = 26;  // bedroom
constexpr uint8_t PIN_LDR = 34;     // ADC1: ADC2 pins stop working while Wi-Fi is on
constexpr uint8_t PIN_SDA = 21;
constexpr uint8_t PIN_SCL = 22;
constexpr uint8_t PIN_BUZZER = 27;
constexpr uint8_t PIN_BUTTON = 4;   // wall switch to GND, internal pull-up

// --- Presence (HC-SR04) ------------------------------------------------------
// 25 ms covers the sensor's 4 m maximum (58 us per cm, there and back) and
// bounds how long one missing echo can block the loop.
constexpr unsigned long ECHO_TIMEOUT_US = 25000;
constexpr float US_PER_CM = 58.0f;
constexpr float PRESENCE_RANGE_CM = 150.0f;
constexpr unsigned long RANGE_POLL_MS = 80;
constexpr uint8_t CONFIRM_SAMPLES = 3;  // 3 x 80 ms: a single jolt never switches the lamp
constexpr unsigned long GRACE_PERIOD_MS = 6000;

// --- Daylight (photoresistor module) -----------------------------------------
// Hysteresis in lux. Backend mirror: protocol.LUX_DAYLIGHT_ON / _OFF.
constexpr float LUX_DAYLIGHT_ON = 500.0f;
constexpr float LUX_DAYLIGHT_OFF = 300.0f;
constexpr unsigned long LDR_POLL_MS = 200;
// Module model (Wokwi photoresistor-sensor defaults): LDR in series with 10 kOhm.
constexpr float LDR_GAMMA = 0.7f;
constexpr float LDR_RL10_KOHM = 50.0f;  // LDR resistance at 10 lux
constexpr float LDR_SERIES_OHM = 10000.0f;
constexpr float ADC_VREF = 3.3f;
constexpr int ADC_MAX = 4095;  // 12-bit
constexpr float LUX_MAX = 100000.0f;

// --- Wall switch ---------------------------------------------------------------
constexpr unsigned long DEBOUNCE_MS = 30;
constexpr unsigned long LONG_PRESS_MS = 1000;  // long press = resume automation

// --- Buzzer --------------------------------------------------------------------
constexpr uint16_t FAULT_TONE_HZ = 2000;
constexpr uint16_t CLICK_TONE_HZ = 3000;
constexpr uint16_t CLICK_MS = 60;

// --- Network -------------------------------------------------------------------
constexpr unsigned long TELEMETRY_PERIOD_MS = 5000;  // backend: PS_TELEMETRY_PERIOD_S
constexpr unsigned long WIFI_TIMEOUT_MS = 20000;
constexpr uint8_t WIFI_CHANNEL = 6;  // Wokwi-GUEST listens on channel 6; skips the scan
constexpr uint16_t REST_PORT = 80;
constexpr const char* DASHBOARD_ORIGIN = "http://localhost:5000";
constexpr uint16_t MQTT_KEEPALIVE_S = 15;
constexpr uint16_t MQTT_BUFFER_BYTES = 512;  // PubSubClient's 256 B default is too tight
constexpr unsigned long BACKOFF_MIN_MS = 1000;
constexpr unsigned long BACKOFF_MAX_MS = 30000;

// --- Display -------------------------------------------------------------------
constexpr uint8_t LCD_ADDRESS = 0x27;
constexpr unsigned long LCD_REFRESH_MS = 250;

}  // namespace Config
