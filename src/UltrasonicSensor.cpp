#include "UltrasonicSensor.h"

UltrasonicSensor::UltrasonicSensor(int trigPin, int echoPin, float threshold) {
    this->trigPin = trigPin;
    this->echoPin = echoPin;
    this->distanceThreshold = threshold;
    this->lastReadTime = 0;
    
    pinMode(trigPin, OUTPUT);
    pinMode(echoPin, INPUT);
}

float UltrasonicSensor::getDistance() {
    // Clear the trigger pin
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    
    // Set trigger pin high for 10 microseconds
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);
    
    // Read the echo pin, returns the sound wave travel time in microseconds
    long duration = pulseIn(echoPin, HIGH);
    
    // Calculate the distance
    float distanceCm = duration * 0.034 / 2;
    
    return distanceCm;
}

bool UltrasonicSensor::isObjectDetected() {
    float distance = getDistance();
    return distance <= distanceThreshold && distance > 0;
}

bool UltrasonicSensor::shouldRead() {
    unsigned long currentTime = millis();
    if (currentTime - lastReadTime >= readInterval) {
        lastReadTime = currentTime;
        return true;
    }
    return false;
}
