// PresenceSense: occupancy-aware adaptive lighting hub (ESP32, runs in Wokwi).
// Wiring: ../diagram.json. Wire protocol: ../../docs/protocol.md.
#include "Hub.h"

PresenceSenseHub hub;

void setup() { hub.begin(); }

void loop() { hub.loop(); }
