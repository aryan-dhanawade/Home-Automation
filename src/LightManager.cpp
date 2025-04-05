#include "LightManager.h"

LightManager::LightManager(LightController* bedRoom, LightController* livingRoom, ESP8266WebServer* webServer) {
    this->bedRoomLight = bedRoom;
    this->livingRoomLight = livingRoom;
    this->webServer = webServer;
    scheduled = false;
    partyModeActive = false;
}

void LightManager::handleCommand() {
    String command = webServer->arg(0);
    if (command.length() == 0) {
        webServer->send(400, "text/plain", "Empty command");
        return;
    }
    Serial.println("Received command: " + command);

    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, command);

    if (error) {
        Serial.println("Error parsing JSON Document!");
        webServer->send(400, "text/plain", "Invalid JSON format");
        return;
    }

    int roomID = doc["roomID"];
    String state = doc["status"]["state"];
    int brightness = doc["status"]["brightness"].isNull() ? -1 : doc["status"]["brightness"].as<int>();
    String mode = doc["status"]["mode"].isNull() ? "" : doc["status"]["mode"].as<String>();
    int schedule = doc["status"]["schedule"].isNull() ? -1 : doc["status"]["schedule"].as<int>();

    if (schedule != 0) {
        scheduled = true;
        scheduleTime = millis() + (schedule * 1000);
        targetRoom = roomID;
        targetState = state;
        targetBrightness = brightness;  // Store brightness for scheduled command
        webServer->send(200, "text/plain", "Command scheduled");
        return;
    }

    if (roomID == 1) {
        handleRoom(bedRoomLight, state, brightness, mode);
        webServer->send(200, "text/plain", "Bedroom Command received");
    } else if (roomID == 2) {
        handleRoom(livingRoomLight, state, brightness, mode);
        webServer->send(200, "text/plain", "Living Room Command received");
    } else {
        webServer->send(400, "text/plain", "Invalid Room ID");
    }
}

void LightManager::handleRoom(LightController* light, String state, int brightness, String mode) {
    if (state == "ON") {
        light->turnOn();
        if (brightness != -1) {
            light->setBrightness(brightness);
        }
        if (mode != "") {
            handleMode(mode);
        }
    } else if (state == "OFF") {
        light->turnOff();
    }
}

void LightManager::handleMode(LightController* light, String mode) {

    if (mode == "party") {

        partyModeActive = true;
        partyModeStep = 0;
        lastFlashTime = millis();
        Serial.println("Party mode activated");
    
    } else if(mode == "movie") {
        light->setBrightness(25);
        Serial.println("Movie Mode Activated");
        
    }
    
    else {
        Serial.println("Mode not implemented");
    }
}

void LightManager::checkScheduledCommand() {
    if (scheduled && millis() >= scheduleTime) {
        Serial.println("Executing scheduled command");

        if (targetRoom == 1) {
            if (targetState == "ON") {
                bedRoomLight->turnOn();
                if (targetBrightness != -1) { // Apply brightness if provided
                    bedRoomLight->setBrightness(targetBrightness);
                }
            } else {
                bedRoomLight->turnOff();
            }
        } else if (targetRoom == 2) {
            if (targetState == "ON") {
                livingRoomLight->turnOn();
                if (targetBrightness != -1) { // Apply brightness if provided
                    livingRoomLight->setBrightness(targetBrightness);
                }
            } else {
                livingRoomLight->turnOff();
            }
        }

        scheduled = false; // Reset the scheduled flag 
    }
}


void LightManager::handlePartyMode() {
    if (partyModeActive) {
        if (millis() - lastFlashTime >= flashInterval) {
            lastFlashTime = millis();

            // Toggle both lights alternatively
            if (partyModeStep % 2 == 0) {
                bedRoomLight->turnOn();
                livingRoomLight->turnOff();
            } else {
                bedRoomLight->turnOff();
                livingRoomLight->turnOn();
            }

            partyModeStep++;

            if (partyModeStep >= (maxFlashes * 2)) {
                partyModeActive = false;
                Serial.println("Party mode finished");
                // Turn off both lights at the end of party mode
                bedRoomLight->turnOff();
                livingRoomLight->turnOff();
            }
        }
    }
}
