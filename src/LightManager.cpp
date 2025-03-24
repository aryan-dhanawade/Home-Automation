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

    if (schedule != -1) {
        // Schedule command
        scheduled = true;
        scheduleTime = millis() + (schedule * 1000);
        targetRoom = roomID;
        targetState = state;
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

void LightManager::handleMode(String mode) {
    if (mode == "party") {
        partyModeActive = true;
        partyModeStep = 0;
        lastFlashTime = millis();
        Serial.println("Party mode activated");
    } else {
        Serial.println("Mode not implemented");
    }
}

void LightManager::checkScheduledCommand() {
    if (scheduled && millis() >= scheduleTime) {
        Serial.println("Executing scheduled command");

        if (targetRoom == 1) {
            if (targetState == "ON") {
                bedRoomLight->turnOn();
            } else if (targetState == "OFF") {
                bedRoomLight->turnOff();
            }
        } else if (targetRoom == 2) {
            if (targetState == "ON") {
                livingRoomLight->turnOn();
            } else if (targetState == "OFF") {
                livingRoomLight->turnOff();
            }
        }

        scheduled = false; //  Reset the scheduled flag 
    }
}

void LightManager::handlePartyMode() {
    if (partyModeActive) {
        if (millis() - lastFlashTime >= flashInterval) {
            lastFlashTime = millis();

            if (partyModeStep % 2 == 0) {
                livingRoomLight->turnOn();
            } else {
                livingRoomLight->turnOff();
            }

            partyModeStep++;
            if (partyModeStep >= (maxFlashes * 2)) {
                partyModeActive = false;
                Serial.println("Party mode finished");
            }
        }
    }
}
