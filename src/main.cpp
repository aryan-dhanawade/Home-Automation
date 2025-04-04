#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <Arduino.h>
#include "LightController.h"
#include "LightManager.h"

// WiFi Credentials
const char* ssid = "Aryan's A55";
const char* password = "aryan123";

// Pins
const int livingRoomPin = D2;
const int bedRoomPin = D1;

// Server
ESP8266WebServer server(80);

// Lights
LightController bedRoomLight(bedRoomPin);
LightController livingRoomLight(livingRoomPin);

// Manager
LightManager lightManager(&bedRoomLight, &livingRoomLight, &server);

void setup() {
    Serial.begin(115200);
    
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(1000);
        Serial.println("Connecting to WiFi...");
    }

    Serial.println("Connected to WiFi");
    Serial.println(WiFi.localIP());

    // Setup API endpoint
    server.on("/command", HTTP_POST, []() {
        lightManager.handleCommand();
    });

    server.begin();
    Serial.println("HTTP server started");
}

void loop() {
    server.handleClient();
    lightManager.checkScheduledCommand();
    lightManager.handlePartyMode();
}
