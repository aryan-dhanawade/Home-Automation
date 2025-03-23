#ifndef LIGHT_CONTROLLER_H
#define LIGHT_CONTROLLER_H

#include <Arduino.h>

class LightController {
private:
    int pin;
    bool state;
    int brightness;

public:
    LightController(int pin) {
        this->pin = pin;
        this->state = false; // false = OFF, true = ON
        this->brightness = 100;
        pinMode(pin, OUTPUT);
        digitalWrite(pin, LOW);
    }

    void turnOn() {
        state = true;
        digitalWrite(pin, HIGH);
        Serial.print("Light ");
        Serial.print(state ? "ON" : "OFF");
        Serial.print(" at pin ");
        Serial.println(pin);
    }

    void turnOff() {
        state = false;
        digitalWrite(pin, LOW);
        Serial.print("Light ");
        Serial.print(state ? "ON" : "OFF");
        Serial.print(" at pin ");
        Serial.println(pin);
    }

    void setBrightness(int value) {
        brightness = value;
        analogWrite(pin, map(value, 0, 100, 0, 255));
        Serial.printf("Brightness set to %d at pin %d\n", brightness, pin);
    }

    bool getState() {
        return state;
    }

    int getBrightness() {
        return brightness;
    }
};

#endif
