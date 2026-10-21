// Transports: MQTT (remote control + telemetry) and REST (local control).
// Both parse into the same Command and go through the same CommandRouter.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WebServer.h>
#include <WiFi.h>
#include "Config.h"
#include "Control.h"
#include "Secrets.h"
#include "Types.h"

namespace Protocol {

// JSON -> Command. Returns false when the body is not a JSON object.
inline bool parseCommand(const char* json, size_t length, Command& out) {
  JsonDocument doc;
  if (deserializeJson(doc, json, length) || !doc.is<JsonObject>()) return false;
  out.type = parseType(doc["type"] | "");
  out.target = out.type == CommandType::RESUME ? CommandTarget::ALL : parseTarget(doc["target"] | "");
  out.value = doc["value"].is<int>() ? (int16_t)doc["value"].as<int>() : -1;
  strlcpy(out.correlationId, doc["correlationId"] | "", sizeof out.correlationId);
  strlcpy(out.token, doc["token"] | "", sizeof out.token);
  return true;
}

// The ack names what was executed, so the backend can log and audit commands
// it did not send itself (the wall switch, a direct REST call).
inline size_t writeAck(char* buffer, size_t size, const Command& cmd, CommandResult result, CommandSource source) {
  JsonDocument doc;
  doc["result"] = resultName(result);
  doc["correlationId"] = cmd.correlationId;
  doc["source"] = sourceName(source);
  if (result == CommandResult::OK) {
    doc["type"] = typeName(cmd.type);
    doc["target"] = targetName(cmd.target);
    doc["value"] = resolvedLevel(cmd);
  }
  return serializeJson(doc, buffer, size);
}

inline size_t writeTelemetry(char* buffer, size_t size, const Snapshot& s) {
  JsonDocument doc;
  doc["state"] = stateName(s.state);
  doc["lampA"] = s.lampA;
  doc["lampB"] = s.lampB;
  doc["ambient"] = s.ambientRaw;
  doc["lux"] = roundf(s.lux * 10) / 10;
  doc["distance"] = roundf(s.distanceCm * 10) / 10;
  doc["fault"] = s.fault;
  doc["uptime"] = s.uptimeMs;
  return serializeJson(doc, buffer, size);
}

}  // namespace Protocol

class MqttControlPlane {
 public:
  explicit MqttControlPlane(CommandRouter& router) : _router(router), _client(_net) {}

  void begin() {
    snprintf(_topicStatus, sizeof _topicStatus, "home/%s/status", Secrets::HUB_ID);
    snprintf(_topicTelemetry, sizeof _topicTelemetry, "home/%s/telemetry", Secrets::HUB_ID);
    snprintf(_topicEvent, sizeof _topicEvent, "home/%s/event", Secrets::HUB_ID);
    snprintf(_topicCmd, sizeof _topicCmd, "home/%s/cmd", Secrets::HUB_ID);
    snprintf(_topicAck, sizeof _topicAck, "home/%s/ack", Secrets::HUB_ID);
    snprintf(_clientId, sizeof _clientId, "ps-hub-%s-%04x", Secrets::HUB_ID, (unsigned)(esp_random() & 0xffff));
    _client.setServer(Secrets::MQTT_HOST, Secrets::MQTT_PORT);
    _client.setBufferSize(Config::MQTT_BUFFER_BYTES);
    _client.setKeepAlive(Config::MQTT_KEEPALIVE_S);
    _client.setCallback([this](char* topic, uint8_t* payload, unsigned int length) {
      onMessage(topic, payload, length);
    });
  }

  // Non-blocking between attempts: reconnects with exponential backoff, 1 s to 30 s.
  void loop(unsigned long now) {
    if (_client.connected()) {
      _client.loop();
      return;
    }
    if (WiFi.status() != WL_CONNECTED || (long)(now - _nextRetryMs) < 0) return;
    connect(now);
  }

  bool connected() { return _client.connected(); }

  void publishTelemetry(const Snapshot& s) {
    char buffer[256];
    size_t n = Protocol::writeTelemetry(buffer, sizeof buffer, s);
    publish(_topicTelemetry, buffer, n);
  }

  void publishEvent(uint32_t seq, HubState state, uint32_t uptimeMs) {
    char buffer[96];
    int n = snprintf(buffer, sizeof buffer, "{\"seq\":%lu,\"state\":\"%s\",\"uptime\":%lu}",
                     (unsigned long)seq, stateName(state), (unsigned long)uptimeMs);
    if (!publish(_topicEvent, buffer, n)) {
      Serial.printf("[MQTT] event seq=%lu not sent (offline)\n", (unsigned long)seq);
    }
  }

  void publishAck(const Command& cmd, CommandResult result, CommandSource source) {
    char buffer[192];
    size_t n = Protocol::writeAck(buffer, sizeof buffer, cmd, result, source);
    publish(_topicAck, buffer, n);
  }

 private:
  void connect(unsigned long now) {
    Serial.printf("[MQTT] connecting to %s:%u as %s\n", Secrets::MQTT_HOST, Secrets::MQTT_PORT, _clientId);
    // Last Will: if the hub vanishes, the broker publishes a retained "offline".
    if (_client.connect(_clientId, Secrets::MQTT_USER, Secrets::MQTT_PASSWORD, _topicStatus, 1, true,
                        "offline")) {
      _client.publish(_topicStatus, "online", true);
      _client.subscribe(_topicCmd, 1);
      _backoffMs = Config::BACKOFF_MIN_MS;
      Serial.printf("[MQTT] connected, topics under home/%s/\n", Secrets::HUB_ID);
      return;
    }
    Serial.printf("[MQTT] connect failed (state %d), retry in %lu ms\n", _client.state(), _backoffMs);
    _nextRetryMs = now + _backoffMs;
    _backoffMs = _backoffMs * 2 > Config::BACKOFF_MAX_MS ? Config::BACKOFF_MAX_MS : _backoffMs * 2;
  }

  void onMessage(char* topic, uint8_t* payload, unsigned int length) {
    Command cmd;
    CommandResult result = CommandResult::BAD_REQUEST;
    if (Protocol::parseCommand(reinterpret_cast<const char*>(payload), length, cmd)) {
      result = _router.handle(cmd, CommandSource::MQTT);
    }
    publishAck(cmd, result, CommandSource::MQTT);
  }

  bool publish(const char* topic, const char* payload, size_t length) {
    // PubSubClient only publishes at QoS 0; delivery is measured by the backend's sequence check.
    return _client.connected() &&
           _client.publish(topic, reinterpret_cast<const uint8_t*>(payload), length, false);
  }

  CommandRouter& _router;
  WiFiClient _net;
  PubSubClient _client;
  char _clientId[48];
  char _topicStatus[64], _topicTelemetry[64], _topicEvent[64], _topicCmd[64], _topicAck[64];
  unsigned long _nextRetryMs = 0;
  unsigned long _backoffMs = Config::BACKOFF_MIN_MS;
};

// Local control plane. Plain HTTP on the LAN; in Wokwi it is only reachable
// through the paid private gateway, so it is demonstrated with the Python simulator.
class RestControlPlane {
 public:
  RestControlPlane(CommandRouter& router, OccupancyEngine& engine, MqttControlPlane& mqtt)
      : _router(router), _engine(engine), _mqtt(mqtt), _server(Config::REST_PORT) {}

  void begin() {
    _server.on("/command", HTTP_OPTIONS, [this]() {
      corsHeaders();
      _server.sendHeader("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
      _server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
      _server.send(204);
    });
    _server.on("/command", HTTP_POST, [this]() { onCommand(); });
    _server.on("/status", HTTP_GET, [this]() {
      char buffer[256];
      Protocol::writeTelemetry(buffer, sizeof buffer, _engine.snapshot(millis()));
      corsHeaders();
      _server.send(200, "application/json", buffer);
    });
    _server.begin();
  }

  void loop() { _server.handleClient(); }

 private:
  void corsHeaders() {
    // The exact dashboard origin, never "*". A browser rule only; the token is the real control.
    _server.sendHeader("Access-Control-Allow-Origin", Config::DASHBOARD_ORIGIN);
  }

  void onCommand() {
    const String& body = _server.arg("plain");
    Command cmd;
    CommandResult result = CommandResult::BAD_REQUEST;
    if (body.length() > 0 && body.length() <= 512 && Protocol::parseCommand(body.c_str(), body.length(), cmd)) {
      result = _router.handle(cmd, CommandSource::REST);
    }
    char buffer[192];
    Protocol::writeAck(buffer, sizeof buffer, cmd, result, CommandSource::REST);
    corsHeaders();
    int code = result == CommandResult::OK ? 200 : result == CommandResult::AUTH_FAIL ? 401 : 400;
    _server.send(code, "application/json", buffer);
    _mqtt.publishAck(cmd, result, CommandSource::REST);  // the backend sees every command
  }

  CommandRouter& _router;
  OccupancyEngine& _engine;
  MqttControlPlane& _mqtt;
  WebServer _server;
};
