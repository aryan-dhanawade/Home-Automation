# PresenceSense hub firmware (ESP32, Wokwi)

The hub: HC-SR04 presence, photoresistor daylight sensing in lux, two dimmable
lamps, a 16×2 I²C LCD, a buzzer and a wall switch, talking MQTT to the backend
(and REST on a LAN). Wire format: [../docs/protocol.md](../docs/protocol.md).

## Why ESP32 and not the NodeMCU ESP8266 in the brief

Wokwi does not simulate the ESP8266:

- The ESP8266 is not on Wokwi's [supported hardware list](https://docs.wokwi.com/getting-started/supported-hardware)
  (ESP32 family, AVR, STM32 and RP2040 only).
- Wokwi's creator, Uri Shaked, on [wokwi-features #987](https://github.com/wokwi/wokwi-features/issues/987)
  (March 2025): "we do not simulate the esp8266"; and on
  [#314](https://github.com/wokwi/wokwi-features/issues/314) (June 2025): "there is
  no simulation model for the ESP8266 WIFI".

The ESP32 also removes three ESP8266 workarounds from the design: it has
hardware PWM (no software PWM, no `analogWriteRange`), more than one ADC pin, and
no boot-strap trap on the lamp pins.

## Circuit

| Signal | ESP32 pin | Part |
|---|---|---|
| HC-SR04 TRIG / ECHO | GPIO 5 / 18 | `sonar` |
| Lamp A / Lamp B | GPIO 25 / 26, 220 Ω each | `lampA` (yellow), `lampB` (white) |
| LDR module AO | GPIO 34 (ADC1: ADC2 is unusable with Wi-Fi on) | `ldr`, powered from 3V3 |
| LCD SDA / SCL | GPIO 21 / 22, address 0x27 | `lcd` |
| Buzzer | GPIO 27 | `buzzer` |
| Wall switch | GPIO 4 to GND, internal pull-up | `wallSwitch` (keyboard: B) |

## Run it in the Wokwi web editor (free)

1. Generate a hub id and a token on the backend side, and put them in `backend/.env`
   (`PS_HUB_ID`, `PS_API_TOKEN`):
   ```bash
   python -c "import secrets; print('ps-' + secrets.token_hex(8)); print(secrets.token_hex(16))"
   ```
2. Open https://wokwi.com/projects/new/esp32.
3. Replace `diagram.json` with [diagram.json](diagram.json), and the sketch with
   [PresenceSense/PresenceSense.ino](PresenceSense/PresenceSense.ino).
4. Add a tab for each of `Config.h`, `Types.h`, `Sensors.h`, `Actuators.h`,
   `Control.h`, `Transport.h`, `Hub.h` and paste each file in.
5. Add a tab `Secrets.h`: paste [Secrets.example.h](PresenceSense/Secrets.example.h)
   and fill in `HUB_ID` and `API_TOKEN` with the same values as `backend/.env`.
6. Add a tab `libraries.txt` with the contents of [libraries.txt](libraries.txt).
7. Start the backend (`python -m presencesense serve`), then press play in Wokwi.

A saved Wokwi project can be opened by anyone with its link, and `Secrets.h` is
part of it. Don't share the link, and use a token you don't use anywhere else.

During the simulation, click the HC-SR04 to move the "person" (2–400 cm), click
the photoresistor to change the light level, and press the blue button (or
the B key) for the wall switch: a short press toggles the lamps, a long press
(1 s) resumes automation.

## What the serial monitor shows

Captured from a Wokwi run on 2026-10-21 (hub id shortened):

```
[BOOT] PresenceSense hub
[WIFI] connected, IP 10.10.0.2
[MQTT] connecting to public.cloud.shiftr.io:1883 as ps-hub-ps-…-7bf8
[MQTT] connected, topics under home/ps-…/
[S] 5248 state=VACANT seq=1
[T] 27164 first_in_range d=86.1cm
[T] 27324 confirmed state=OCCUPIED latency_ms=160 lampA=255 lampB=255
[S] 27324 state=OCCUPIED seq=2
[CMD] 46627 MQTT brightness lampA 100 -> OK (c761185f7afeba67c)
[S] 46606 state=OVERRIDE seq=3
[CMD] 70635 BUTTON off all -1 -> OK (b70634)
[CMD] 78593 BUTTON resume all -1 -> OK (b78592)
[S] 78592 state=GRACE seq=4
[S] 84592 state=VACANT seq=5
[T] 111475 first_in_range d=86.1cm
[T] 111635 confirmed state=OCCUPIED_DAYLIGHT latency_ms=160 lampA=0 lampB=0
[S] 111635 state=OCCUPIED_DAYLIGHT seq=6
```

`[T]` lines are the Objective 1 evidence. Latency runs from the first in-range
sample to the third confirming one: 2 × 80 ms. (A `[S]` line can carry a
slightly earlier time than the `[CMD]` that caused it: the loop reads `millis()`
once at the top, and the command is handled inside that iteration.)

Between OVERRIDE (seq 3) and the button resume, the person left (distance
400 cm) for over 20 s and the state did not move: Objective 7.

## Demo tips

- Put Wokwi and the dashboard in **two windows side by side**, not two tabs.
  Browsers throttle background tabs, which slows the simulation and makes
  telemetry look late.
- A wall-switch press must be held longer than the 30 ms debounce. Clicking the
  button or holding B works; an instantaneous synthetic key event is (correctly)
  ignored.
- Wokwi's free build queue can take a minute or two. Compile once before the
  presentation and keep the tab open.

## Compile locally (PlatformIO)

```bash
pip install platformio
cp PresenceSense/Secrets.example.h PresenceSense/Secrets.h
pio run -d firmware
```

## Limits

- The HC-SR04 echo timeout (sensor fault) cannot be produced in Wokwi: the part
  always answers within 2–400 cm. The fault path is exercised by the simulator.
- Wokwi's sensors give clean values, so debounce and hysteresis are tested
  against simulated noise only. The wall switch is the exception: Wokwi
  simulates contact bounce, which the 30 ms debounce handles.
- REST cannot be reached on Wokwi's free public gateway (no incoming
  connections). REST is demonstrated with the Python simulator, and REST
  timings in the report come from the simulator, not this hub.
