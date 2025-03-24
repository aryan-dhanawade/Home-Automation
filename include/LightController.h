#ifndef LIGHT_CONTROLLER_H
#define LIGHT_CONTROLLER_H

#include <Arduino.h>

class LightController {
private:
    int pin;
    bool state;
    int brightness;

public:
    // Constructor
    LightController(int pin);

    // Turns the light on
    void turnOn();

    // Turns the light off
    void turnOff();

    // Sets brightness (0 - 100)
    void setBrightness(int value);

    // Returns the current state (true = ON, false = OFF)
    bool getState();

    // Returns the current brightness level
    int getBrightness();
};

#endif
