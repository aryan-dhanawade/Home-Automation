#ifndef ULTRASONIC_SENSOR_H
#define ULTRASONIC_SENSOR_H

#include <Arduino.h>

class UltrasonicSensor {
private:
    int trigPin;
    int echoPin;
    float distanceThreshold;
    unsigned long lastReadTime;
    const unsigned long readInterval = 100; // Read every 100ms

public:
    UltrasonicSensor(int trigPin, int echoPin, float threshold = 15.0);
    
    // Returns distance in cm
    float getDistance();
    
    // Returns true if object is detected within threshold distance
    bool isObjectDetected();
    
    // Check if it's time to read the sensor
    bool shouldRead();
};

#endif
