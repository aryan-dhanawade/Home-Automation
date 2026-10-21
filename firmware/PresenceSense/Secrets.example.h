// Copy to Secrets.h (gitignored) and fill in. In the Wokwi web editor, add a
// tab named Secrets.h with this content instead.
//
// HUB_ID and API_TOKEN must equal PS_HUB_ID and PS_API_TOKEN in backend/.env.
// Generate both on the backend side:
//   python -c "import secrets; print('ps-' + secrets.token_hex(8))"
//   python -c "import secrets; print(secrets.token_hex(16))"
#pragma once
#include <Arduino.h>

namespace Secrets {

constexpr const char* WIFI_SSID = "Wokwi-GUEST";
constexpr const char* WIFI_PASSWORD = "";

// The hub runs in Wokwi's cloud, so it uses plain MQTT on 1883. The backend
// reaches the same broker over a secure WebSocket on 443.
constexpr const char* MQTT_HOST = "public.cloud.shiftr.io";
constexpr uint16_t MQTT_PORT = 1883;
constexpr const char* MQTT_USER = "public";
constexpr const char* MQTT_PASSWORD = "public";

// Topic root is home/<HUB_ID>. Random, so other users of the public broker
// cannot guess it or collide with it.
constexpr const char* HUB_ID = "ps-CHANGE-ME";
constexpr const char* API_TOKEN = "CHANGE-ME";  // at most 64 characters

}  // namespace Secrets
