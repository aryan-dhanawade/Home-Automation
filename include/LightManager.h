#ifndef LIGHT_MANAGER_H
#define LIGHT_MANAGER_H

#include <Arduino.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>
#include "LightController.h"
#include "UltrasonicSensor.h"

class LightManager {
private:
    LightController* bedRoomLight;
    LightController* livingRoomLight;
    ESP8266WebServer* webServer;
    UltrasonicSensor* ultrasonicSensor;
    bool motionDetectionEnabled;

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
    int targetBrightness;

public:
    LightManager(LightController* bedRoom, LightController* livingRoom, ESP8266WebServer* webServer, UltrasonicSensor* sensor);
    
    // Handles incoming HTTP command
    void handleCommand();

    // Handles the light settings for a specific room
    void handleRoom(LightController* light, String state, int brightness, String mode);

    // Handles different light modes (e.g., party mode)
    void handleMode(LightController* light, String mode);

    // Checks for scheduled commands and executes them
    void checkScheduledCommand();

    // Runs party mode sequence
    void handlePartyMode();

    // Enable/disable motion detection
    void setMotionDetection(bool enabled);
    
    // Check ultrasonic sensor and turn on lights if object detected
    void checkUltrasonicSensor();
};

#endif
