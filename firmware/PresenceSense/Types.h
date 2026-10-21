// Shared vocabulary: states, commands and their wire names (docs/protocol.md).
#pragma once
#include <Arduino.h>

enum class HubState : uint8_t { VACANT, OCCUPIED, OCCUPIED_DAYLIGHT, GRACE, OVERRIDE };
enum class CommandType : uint8_t { ON, OFF, BRIGHTNESS, SCENE, RESUME, UNKNOWN };
enum class CommandTarget : uint8_t { LAMP_A, LAMP_B, ALL, UNKNOWN };
enum class CommandSource : uint8_t { MQTT, REST, BUTTON };
enum class CommandResult : uint8_t { OK, AUTH_FAIL, BAD_REQUEST };

struct Command {
  CommandType type = CommandType::UNKNOWN;
  CommandTarget target = CommandTarget::UNKNOWN;
  int16_t value = -1;
  char correlationId[24] = "";
  char token[65] = "";
};

// What the hub reports in telemetry and on GET /status.
struct Snapshot {
  HubState state;
  uint8_t lampA;
  uint8_t lampB;
  int ambientRaw;
  float lux;
  float distanceCm;  // -1 when the last echo timed out
  bool fault;
  uint32_t uptimeMs;
};

inline const char* stateName(HubState s) {
  switch (s) {
    case HubState::VACANT: return "VACANT";
    case HubState::OCCUPIED: return "OCCUPIED";
    case HubState::OCCUPIED_DAYLIGHT: return "OCCUPIED_DAYLIGHT";
    case HubState::GRACE: return "GRACE";
    case HubState::OVERRIDE: return "OVERRIDE";
  }
  return "UNKNOWN";
}

inline const char* typeName(CommandType t) {
  switch (t) {
    case CommandType::ON: return "on";
    case CommandType::OFF: return "off";
    case CommandType::BRIGHTNESS: return "brightness";
    case CommandType::SCENE: return "scene";
    case CommandType::RESUME: return "resume";
    default: return "unknown";
  }
}

inline const char* targetName(CommandTarget t) {
  switch (t) {
    case CommandTarget::LAMP_A: return "lampA";
    case CommandTarget::LAMP_B: return "lampB";
    case CommandTarget::ALL: return "all";
    default: return "unknown";
  }
}

inline const char* sourceName(CommandSource s) {
  switch (s) {
    case CommandSource::MQTT: return "MQTT";
    case CommandSource::REST: return "REST";
    case CommandSource::BUTTON: return "BUTTON";
  }
  return "UNKNOWN";
}

inline const char* resultName(CommandResult r) {
  switch (r) {
    case CommandResult::OK: return "OK";
    case CommandResult::AUTH_FAIL: return "AUTH_FAIL";
    case CommandResult::BAD_REQUEST: return "BAD_REQUEST";
  }
  return "BAD_REQUEST";
}

inline CommandType parseType(const char* s) {
  if (!strcmp(s, "on")) return CommandType::ON;
  if (!strcmp(s, "off")) return CommandType::OFF;
  if (!strcmp(s, "brightness")) return CommandType::BRIGHTNESS;
  if (!strcmp(s, "scene")) return CommandType::SCENE;
  if (!strcmp(s, "resume")) return CommandType::RESUME;
  return CommandType::UNKNOWN;
}

inline CommandTarget parseTarget(const char* s) {
  if (!strcmp(s, "lampA")) return CommandTarget::LAMP_A;
  if (!strcmp(s, "lampB")) return CommandTarget::LAMP_B;
  if (!strcmp(s, "all")) return CommandTarget::ALL;
  return CommandTarget::UNKNOWN;
}

// The lamp level a command results in (0 for resume, which sets no level).
inline uint8_t resolvedLevel(const Command& c) {
  switch (c.type) {
    case CommandType::ON: return 255;
    case CommandType::BRIGHTNESS:
    case CommandType::SCENE: return (uint8_t)constrain(c.value, 0, 255);
    default: return 0;
  }
}
