// Composition root: owns every part, wires them by reference, runs the loop.
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include "Actuators.h"
#include "Config.h"
#include "Control.h"
#include "Transport.h"
#include "Secrets.h"
#include "Sensors.h"
#include "Types.h"

class PresenceSenseHub {
 public:
  void begin() {
    Serial.begin(115200);
    Serial.println("\n[BOOT] PresenceSense hub");
    _lampA.begin();
    _lampB.begin();
    _buzzer.begin();
    _button.begin();
    _presence.begin();
    _light.begin();
    _display.begin();
    connectWifi();
    _rest.begin();
    _mqtt.begin();
  }

  void loop() {
    unsigned long now = millis();
    _rest.loop();
    _mqtt.loop(now);
    handleButton(now);
    _engine.update(now);

    HubState state = _engine.state();
    if (state != _lastState) {
      _lastState = state;
      _eventSeq++;
      Serial.printf("[S] %lu state=%s seq=%lu\n", now, stateName(state), (unsigned long)_eventSeq);
      _mqtt.publishEvent(_eventSeq, state, now);
    }

    Snapshot snapshot = _engine.snapshot(now);
    _display.show(snapshot, _mqtt.connected(), now);
    if (now - _lastTelemetryMs >= Config::TELEMETRY_PERIOD_MS) {
      _lastTelemetryMs = now;
      _mqtt.publishTelemetry(snapshot);
    }
  }

 private:
  // The only blocking wait in the firmware, once at boot.
  void connectWifi() {
    _display.message("PresenceSense", "Wi-Fi...");
    WiFi.mode(WIFI_STA);
    WiFi.begin(Secrets::WIFI_SSID, Secrets::WIFI_PASSWORD, Config::WIFI_CHANNEL);
    unsigned long started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < Config::WIFI_TIMEOUT_MS) {
      delay(100);
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("[WIFI] connected, IP %s\n", WiFi.localIP().toString().c_str());
    } else {
      // Automation still works offline; the ESP32 keeps retrying in the background.
      Serial.println("[WIFI] not connected yet, running offline");
    }
  }

  // Wall switch: short press toggles both lamps as a manual command (engaging
  // the override), long press resumes automation. Acked over MQTT like any command.
  void handleButton(unsigned long now) {
    ButtonEvent event = _button.poll(now);
    if (event == ButtonEvent::NONE) return;
    Command cmd;
    cmd.target = CommandTarget::ALL;
    if (event == ButtonEvent::LONG_PRESS) {
      cmd.type = CommandType::RESUME;
    } else {
      bool anyOn = _lampA.brightness() > 0 || _lampB.brightness() > 0;
      cmd.type = anyOn ? CommandType::OFF : CommandType::ON;
    }
    snprintf(cmd.correlationId, sizeof cmd.correlationId, "b%lu", now);
    _buzzer.click();
    CommandResult result = _router.handle(cmd, CommandSource::BUTTON);
    _mqtt.publishAck(cmd, result, CommandSource::BUTTON);
  }

  // Declaration order is construction order: parts before what uses them.
  PresenceSensor _presence{Config::PIN_TRIG, Config::PIN_ECHO};
  LightSensor _light{Config::PIN_LDR};
  OverrideButton _button{Config::PIN_BUTTON};
  LampChannel _lampA{Config::PIN_LAMP_A};
  LampChannel _lampB{Config::PIN_LAMP_B};
  AlertBuzzer _buzzer{Config::PIN_BUZZER};
  StatusDisplay _display;
  SecurityGate _gate{Secrets::API_TOKEN};
  OccupancyEngine _engine{_presence, _light, _lampA, _lampB, _buzzer};
  CommandRouter _router{_gate, _engine};
  MqttControlPlane _mqtt{_router};
  RestControlPlane _rest{_router, _engine, _mqtt};

  HubState _lastState = HubState::OVERRIDE;  // forces an event for the first real state
  uint32_t _eventSeq = 0;
  unsigned long _lastTelemetryMs = 0;
};
