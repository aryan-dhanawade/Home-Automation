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
    analogWrite(pin, map(brightness, 0, 100, 0, 255)); // Use stored brightness
    Serial.printf("Light ON with brightness %d at pin %d\n", brightness, pin);
}

void LightController::turnOff() {
    state = false;
    analogWrite(pin, 0); // Turn off by setting PWM to 0
    Serial.printf("Light OFF at pin %d\n", pin);
}


void LightController::setBrightness(int value) {
    brightness = value;
    analogWrite(pin, map(value, 0, 100, 0, 255) );
    Serial.printf("Brightness set to %d at pin %d\n", brightness, pin);
}

bool LightController::getState() {
    return state;
}

int LightController::getBrightness() {
    return brightness;
}
