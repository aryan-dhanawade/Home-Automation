#ifndef LIGHT_MANAGER_H
#define LIGHT_MANAGER_H

#include <Arduino.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include "LightController.h"

class LightManager {
private:
    LightController* bedRoomLight;
    LightController* livingRoomLight;
    ESP8266WebServer* webServer;

    // Light Scheduling
    bool scheduled;
    unsigned long scheduleTime;
    int targetRoom;
    String targetState;

    // Party Mode
    bool partyModeActive;
    unsigned long lastFlashTime;
    int partyModeStep;
    const int maxFlashes = 5;
    const int flashInterval = 500;

public:
    LightManager(LightController* bedRoom, LightController* livingRoom, ESP8266WebServer* webServer);
    
    // Handles incoming HTTP command
    void handleCommand();

    // Handles the light settings for a specific room
    void handleRoom(LightController* light, String state, int brightness, String mode);

    // Handles different light modes (e.g., party mode)
    void handleMode(String mode);

    // Checks for scheduled commands and executes them
    void checkScheduledCommand();

    // Runs party mode sequence
    void handlePartyMode();
};

#endif
