// Decision logic: authentication, the occupancy state machine, and the one
// command router both transports and the wall switch go through.
#pragma once
#include <Arduino.h>
#include "Actuators.h"
#include "Config.h"
#include "Sensors.h"
#include "Types.h"

// Constant-time token check: the loop always runs over the full expected
// length, so response time does not reveal how many leading characters matched.
class SecurityGate {
 public:
  explicit SecurityGate(const char* expected) : _expected(expected), _len(strlen(expected)) {}

  bool authorize(const char* given) const {
    size_t givenLen = strnlen(given, 64);
    uint8_t diff = givenLen != _len ? 1 : 0;
    for (size_t i = 0; i < _len; i++) {
      char g = i < givenLen ? given[i] : 0;
      diff |= (uint8_t)(g ^ _expected[i]);
    }
    return diff == 0;
  }

 private:
  const char* _expected;
  size_t _len;
};

// The only class that decides lamp levels.
//
// Override is a flag, not a state the machine enters: any manual command sets
// it, only an explicit resume clears it, and update() returns before touching
// the lamps while it is set. That one early return is Objective 7.
class OccupancyEngine {
 public:
  OccupancyEngine(PresenceSensor& presence, LightSensor& light, LampChannel& lampA, LampChannel& lampB,
                  AlertBuzzer& buzzer)
      : _presence(presence), _light(light), _lampA(lampA), _lampB(lampB), _buzzer(buzzer) {}

  void update(unsigned long now) {
    bool sampled = _presence.poll(now);
    _light.poll(now);
    _buzzer.setAlarm(_presence.sensorFaulted());
    if (sampled) logFirstInRange(now);

    if (_overrideActive) return;

    HubState before = _autoState;
    bool present = _presence.confirmedPresent();
    HubState occupied = _light.isDaylight() ? HubState::OCCUPIED_DAYLIGHT : HubState::OCCUPIED;
    switch (_autoState) {
      case HubState::VACANT:
      case HubState::GRACE:
        if (present) {
          _autoState = occupied;
        } else if (_autoState == HubState::GRACE && now - _graceStartMs >= Config::GRACE_PERIOD_MS) {
          _autoState = HubState::VACANT;
        }
        break;
      case HubState::OCCUPIED:
      case HubState::OCCUPIED_DAYLIGHT:
        if (present) {
          _autoState = occupied;  // daylight can come and go while someone stays
        } else {
          _autoState = HubState::GRACE;
          _graceStartMs = now;
        }
        break;
      case HubState::OVERRIDE:
        break;  // never an automatic state
    }

    // GRACE holds whatever level the lamps had; every other state sets it.
    if (_autoState != HubState::GRACE) {
      uint8_t level = _autoState == HubState::OCCUPIED ? 255 : 0;
      setLamps(CommandTarget::ALL, level);
    }
    if (before != _autoState && (before == HubState::VACANT || before == HubState::GRACE) &&
        _autoState != HubState::VACANT && _autoState != HubState::GRACE) {
      logDetection(now);
    }
  }

  HubState state() const { return _overrideActive ? HubState::OVERRIDE : _autoState; }

  void applyManual(CommandTarget target, uint8_t level) {
    _overrideActive = true;
    setLamps(target, level);
  }

  void clearOverride() { _overrideActive = false; }  // next update() re-derives the lamps

  Snapshot snapshot(unsigned long now) const {
    return Snapshot{state(),
                    _lampA.brightness(),
                    _lampB.brightness(),
                    _light.lastRaw(),
                    _light.lux(),
                    _presence.lastDistanceCm(),
                    _presence.sensorFaulted(),
                    (uint32_t)now};
  }

 private:
  void setLamps(CommandTarget target, uint8_t level) {
    if (target == CommandTarget::LAMP_A || target == CommandTarget::ALL) _lampA.setBrightness(level);
    if (target == CommandTarget::LAMP_B || target == CommandTarget::ALL) _lampB.setBrightness(level);
  }

  // Objective 1 evidence. Detection latency = confirming sample - first in-range
  // sample, both on millis(). (The true entry happened up to one poll before.)
  void logFirstInRange(unsigned long now) {
    bool inRange = _presence.lastSampleInRange();
    if (inRange && !_prevInRange && !isOccupied()) {
      _firstInRangeMs = now;
      Serial.printf("[T] %lu first_in_range d=%.1fcm\n", now, _presence.lastDistanceCm());
    }
    _prevInRange = inRange;
  }

  void logDetection(unsigned long now) {
    Serial.printf("[T] %lu confirmed state=%s latency_ms=%lu lampA=%u lampB=%u\n", now,
                  stateName(_autoState), now - _firstInRangeMs, _lampA.brightness(), _lampB.brightness());
  }

  bool isOccupied() const {
    return _autoState == HubState::OCCUPIED || _autoState == HubState::OCCUPIED_DAYLIGHT;
  }

  PresenceSensor& _presence;
  LightSensor& _light;
  LampChannel& _lampA;
  LampChannel& _lampB;
  AlertBuzzer& _buzzer;
  HubState _autoState = HubState::VACANT;
  bool _overrideActive = false;
  unsigned long _graceStartMs = 0;
  unsigned long _firstInRangeMs = 0;
  bool _prevInRange = false;
};

// One place for authentication and override semantics, shared by MQTT, REST
// and the wall switch, so the same command behaves the same whichever way it came.
class CommandRouter {
 public:
  CommandRouter(SecurityGate& gate, OccupancyEngine& engine) : _gate(gate), _engine(engine) {}

  CommandResult handle(const Command& cmd, CommandSource source) {
    // The wall switch is physical access to the room, so it needs no token.
    if (source != CommandSource::BUTTON && !_gate.authorize(cmd.token)) {
      Serial.printf("[SEC] %lu rejected %s command %s: bad token\n", millis(), sourceName(source),
                    cmd.correlationId);
      return CommandResult::AUTH_FAIL;
    }
    CommandResult result = execute(cmd);
    Serial.printf("[CMD] %lu %s %s %s %d -> %s (%s)\n", millis(), sourceName(source), typeName(cmd.type),
                  targetName(cmd.target), cmd.value, resultName(result), cmd.correlationId);
    return result;
  }

 private:
  CommandResult execute(const Command& cmd) {
    if (cmd.type == CommandType::RESUME) {
      _engine.clearOverride();
      return CommandResult::OK;
    }
    if (cmd.type == CommandType::UNKNOWN || cmd.target == CommandTarget::UNKNOWN) {
      return CommandResult::BAD_REQUEST;
    }
    if ((cmd.type == CommandType::BRIGHTNESS || cmd.type == CommandType::SCENE) && cmd.value < 0) {
      return CommandResult::BAD_REQUEST;  // a level is required
    }
    _engine.applyManual(cmd.target, resolvedLevel(cmd));
    return CommandResult::OK;
  }

  SecurityGate& _gate;
  OccupancyEngine& _engine;
};
