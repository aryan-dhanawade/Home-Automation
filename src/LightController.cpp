#include "LightController.h"

LightController::LightController(int pin) {
    this->pin = pin;
    this->state = false; // false = OFF, true = ON
    this->brightness = 100;
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
}

void LightController::turnOn() {
    state = true;
    digitalWrite(pin, HIGH);
    Serial.print("Light ");
    Serial.print(state ? "ON" : "OFF");
    Serial.print(" at pin ");
    Serial.println(pin);
}

void LightController::turnOff() {
    state = false;
    digitalWrite(pin, LOW);
    Serial.print("Light ");
    Serial.print(state ? "ON" : "OFF");
    Serial.print(" at pin ");
    Serial.println(pin);
}

void LightController::setBrightness(int value) {
    brightness = value;
    analogWrite(pin, map(value, 0, 100, 0, 255));
    Serial.printf("Brightness set to %d at pin %d\n", brightness, pin);
}

bool LightController::getState() {
    return state;
}

int LightController::getBrightness() {
    return brightness;
}
