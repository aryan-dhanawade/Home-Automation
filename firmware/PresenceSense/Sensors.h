// Inputs: ultrasonic presence, daylight, and the wall switch.
#pragma once
#include <Arduino.h>
#include "Config.h"

// HC-SR04 ranging. Detects objects, not people: a still person keeps
// returning an in-range echo, which is why a seated occupant never times out.
class PresenceSensor {
 public:
  PresenceSensor(uint8_t trigPin, uint8_t echoPin) : _trig(trigPin), _echo(echoPin) {}

  void begin() {
    pinMode(_trig, OUTPUT);
    pinMode(_echo, INPUT);
    digitalWrite(_trig, LOW);
  }

  // Takes a sample every RANGE_POLL_MS. Returns true when it did.
  bool poll(unsigned long now) {
    if (now - _lastPollMs < Config::RANGE_POLL_MS) return false;
    _lastPollMs = now;
    _lastDistanceCm = readDistanceCm();
    _lastInRange = _lastDistanceCm > 0 && _lastDistanceCm <= Config::PRESENCE_RANGE_CM;
    _window = (uint8_t)(((_window << 1) | (_lastInRange ? 1 : 0)) & kWindowMask);
    if (_samples < Config::CONFIRM_SAMPLES) _samples++;
    return true;
  }

  // Present only when the last CONFIRM_SAMPLES samples were all in range.
  bool confirmedPresent() const {
    return _samples >= Config::CONFIRM_SAMPLES && _window == kWindowMask;
  }
  bool lastSampleInRange() const { return _lastInRange; }
  bool sensorFaulted() const { return _lastDistanceCm < 0; }
  float lastDistanceCm() const { return _lastDistanceCm; }

 private:
  static constexpr uint8_t kWindowMask = (1u << Config::CONFIRM_SAMPLES) - 1;

  float readDistanceCm() {
    digitalWrite(_trig, LOW);
    delayMicroseconds(2);
    digitalWrite(_trig, HIGH);
    delayMicroseconds(10);
    digitalWrite(_trig, LOW);
    // Without the explicit timeout, pulseIn waits a full second for a missing echo.
    unsigned long echoUs = pulseIn(_echo, HIGH, Config::ECHO_TIMEOUT_US);
    if (echoUs == 0) return -1.0f;
    return echoUs / Config::US_PER_CM;
  }

  uint8_t _trig;
  uint8_t _echo;
  unsigned long _lastPollMs = 0;
  float _lastDistanceCm = 0.0f;  // 0, not -1: no fault buzzer before the first reading
  bool _lastInRange = false;
  uint8_t _window = 0;
  uint8_t _samples = 0;
};

// Photoresistor module on an ADC1 pin, converted to lux so the daylight
// thresholds mean something physical. Brighter light gives a LOWER reading.
class LightSensor {
 public:
  explicit LightSensor(uint8_t pin) : _pin(pin) {}

  void begin() { analogReadResolution(12); }

  void poll(unsigned long now) {
    if (_polled && now - _lastPollMs < Config::LDR_POLL_MS) return;
    _polled = true;
    _lastPollMs = now;
    _raw = analogRead(_pin);
    _lux = toLux(_raw);
    // Hysteresis: a reading hovering near one threshold cannot make the lamp flicker.
    if (_lux >= Config::LUX_DAYLIGHT_ON) {
      _daylight = true;
    } else if (_lux < Config::LUX_DAYLIGHT_OFF) {
      _daylight = false;
    }
  }

  bool isDaylight() const { return _daylight; }
  int lastRaw() const { return _raw; }
  float lux() const { return _lux; }

 private:
  // AO is the LDR's share of the supply: R_ldr = R_series * V / (Vref - V),
  // then lux from the LDR's log-log curve (gamma, resistance at 10 lux).
  static float toLux(int raw) {
    float volts = raw * Config::ADC_VREF / Config::ADC_MAX;
    if (volts >= Config::ADC_VREF - 0.001f) return 0.0f;      // no light at all
    if (volts <= 0.001f) return Config::LUX_MAX;              // saturated
    float resistance = Config::LDR_SERIES_OHM * volts / (Config::ADC_VREF - volts);
    float lux = powf(Config::LDR_RL10_KOHM * 1000.0f * powf(10.0f, Config::LDR_GAMMA) / resistance,
                     1.0f / Config::LDR_GAMMA);
    return lux > Config::LUX_MAX ? Config::LUX_MAX : lux;
  }

  uint8_t _pin;
  unsigned long _lastPollMs = 0;
  bool _polled = false;
  int _raw = 0;
  float _lux = 0.0f;
  bool _daylight = false;
};

enum class ButtonEvent : uint8_t { NONE, SHORT_PRESS, LONG_PRESS };

// Debounced wall switch: a short press toggles the lamps (a manual command),
// a long press hands control back to automation.
class OverrideButton {
 public:
  explicit OverrideButton(uint8_t pin) : _pin(pin) {}

  void begin() { pinMode(_pin, INPUT_PULLUP); }

  ButtonEvent poll(unsigned long now) {
    bool raw = digitalRead(_pin);
    if (raw != _lastRaw) {
      _lastRaw = raw;
      _lastChangeMs = now;
    }
    if (now - _lastChangeMs >= Config::DEBOUNCE_MS && raw != _stable) {
      _stable = raw;
      if (_stable == LOW) {
        _pressedAtMs = now;
        _longFired = false;
      } else if (!_longFired) {
        return ButtonEvent::SHORT_PRESS;
      }
    }
    if (_stable == LOW && !_longFired && now - _pressedAtMs >= Config::LONG_PRESS_MS) {
      _longFired = true;
      return ButtonEvent::LONG_PRESS;
    }
    return ButtonEvent::NONE;
  }

 private:
  uint8_t _pin;
  bool _lastRaw = HIGH;
  bool _stable = HIGH;
  unsigned long _lastChangeMs = 0;
  unsigned long _pressedAtMs = 0;
  bool _longFired = false;
};
