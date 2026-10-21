// Outputs: two dimmable lamps, the alert buzzer and the 16x2 status display.
#pragma once
#include <Arduino.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>
#include "Config.h"
#include "Types.h"

class LampChannel {
 public:
  explicit LampChannel(uint8_t pin) : _pin(pin) {}

  void begin() {
    pinMode(_pin, OUTPUT);
    setBrightness(0);
  }

  // 8-bit PWM through the ESP32's LEDC peripheral (analogWrite's default resolution).
  void setBrightness(uint8_t level) {
    _level = level;
    analogWrite(_pin, level);
  }

  uint8_t brightness() const { return _level; }

 private:
  uint8_t _pin;
  uint8_t _level = 0;
};

// Piezo buzzer: a continuous tone while the ranging sensor is faulted, and a
// short click to confirm a wall-switch press.
class AlertBuzzer {
 public:
  explicit AlertBuzzer(uint8_t pin) : _pin(pin) {}

  void begin() { pinMode(_pin, OUTPUT); }

  void setAlarm(bool on) {
    if (on == _alarm) return;
    _alarm = on;
    if (on) {
      tone(_pin, Config::FAULT_TONE_HZ);
    } else {
      noTone(_pin);
    }
  }

  void click() {
    if (!_alarm) tone(_pin, Config::CLICK_TONE_HZ, Config::CLICK_MS);
  }

 private:
  uint8_t _pin;
  bool _alarm = false;
};

class StatusDisplay {
 public:
  StatusDisplay() : _lcd(Config::LCD_ADDRESS, 16, 2) {}

  void begin() {
    Wire.begin(Config::PIN_SDA, Config::PIN_SCL);
    _lcd.init();
    _lcd.backlight();
    message("PresenceSense", "starting...");
  }

  void message(const char* line1, const char* line2) {
    char a[17], b[17];
    snprintf(a, sizeof a, "%-16s", line1);
    snprintf(b, sizeof b, "%-16s", line2);
    draw(a, b);
  }

  // Redraws only when the text changes, at most every LCD_REFRESH_MS: I2C writes are slow.
  void show(const Snapshot& s, bool online, unsigned long now) {
    if (now - _lastDrawMs < Config::LCD_REFRESH_MS) return;
    _lastDrawMs = now;
    char a[17], b[17], light[7];
    if (s.lux < 10000) {
      snprintf(light, sizeof light, "%4ulx", (unsigned)s.lux);
    } else {
      snprintf(light, sizeof light, "%3uklx", (unsigned)(s.lux / 1000));
    }
    // "OCCUPIED       *" / "A100 B100 9999lx": '*' = connected to the broker
    snprintf(a, sizeof a, "%-15s%c", lcdLabel(s.state), online ? '*' : ' ');
    snprintf(b, sizeof b, "A%3d B%3d %6s", percent(s.lampA), percent(s.lampB), light);
    draw(a, b);
  }

 private:
  static int percent(uint8_t level) { return (level * 100 + 127) / 255; }

  static const char* lcdLabel(HubState s) {
    switch (s) {
      case HubState::VACANT: return "VACANT";
      case HubState::OCCUPIED: return "OCCUPIED";
      case HubState::OCCUPIED_DAYLIGHT: return "OCC + DAYLIGHT";
      case HubState::GRACE: return "GRACE";
      case HubState::OVERRIDE: return "OVERRIDE";
    }
    return "?";
  }

  void draw(const char* a, const char* b) {
    if (strcmp(a, _line1) != 0) {
      _lcd.setCursor(0, 0);
      _lcd.print(a);
      strlcpy(_line1, a, sizeof _line1);
    }
    if (strcmp(b, _line2) != 0) {
      _lcd.setCursor(0, 1);
      _lcd.print(b);
      strlcpy(_line2, b, sizeof _line2);
    }
  }

  LiquidCrystal_I2C _lcd;
  char _line1[17] = "";
  char _line2[17] = "";
  unsigned long _lastDrawMs = 0;
};
