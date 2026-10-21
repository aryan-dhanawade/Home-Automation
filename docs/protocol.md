# PresenceSense wire protocol

The contract between the hub (ESP32 firmware in `firmware/`, or
`python -m presencesense sim`) and the backend. The backend validates every
inbound message against this and rejects + logs anything else
(`rejected_messages` table). The firmware must produce exactly these shapes.

Topic root: `home/<hub_id>`. On a public broker `hub_id` must be random
(`ps-` + 16 hex characters); the backend refuses to start with a guessable one.

| Topic | Direction | QoS (documented) | Retained | Payload |
|---|---|---|---|---|
| `status` | hub → | 1 | yes | `online` / `offline` (plain text, Last Will = `offline`) |
| `telemetry` | hub → | 0 | no | snapshot every 5 s |
| `event` | hub → | 1 | no | one per state transition, sequence-numbered |
| `cmd` | → hub | 1 | no | command object |
| `ack` | hub → | 1 | no | result + the command's correlation id + what was executed |

`PubSubClient` publishes at QoS 0 only, so the QoS column is the broker-side /
subscriber setting, not a delivery guarantee from the hub. The backend measures
actual delivery from sequence gaps instead.

Every payload must be ≤ 512 bytes. The firmware calls `setBufferSize(512)`
because `PubSubClient`'s 256 B default is too tight for a command with a token.

## Broker

Default: shiftr.io's public broker, `public.cloud.shiftr.io`, user `public`,
password `public`.

| Who | How | Why |
|---|---|---|
| Hub (Wokwi) | plain MQTT, port 1883 | Wokwi's public gateway allows outbound TCP |
| Backend | MQTT over WebSocket with TLS, port 443 | The college network blocks 1883 and 8883 but allows 443 |

Checked on 2026-10-21 from the development laptop: 1883/8883 blocked, 443 open;
retained messages and Last Will both delivered. Run
`python -m presencesense netcheck` on the presentation network to re-check.

A random topic root stops collisions and casual reading, but it is not
encryption: anyone subscribed to `#` on a public broker sees every message,
including the token in `cmd` (fixed by signed commands, Phase 2).

## telemetry

```json
{"state":"OCCUPIED","lampA":255,"lampB":255,"ambient":3712,"lux":81.4,"distance":84.5,"fault":false,"uptime":123456}
```

| Field | Type | Range | Notes |
|---|---|---|---|
| `state` | string | `VACANT` `OCCUPIED` `OCCUPIED_DAYLIGHT` `GRACE` `OVERRIDE` | `OVERRIDE` whenever the override flag is set |
| `lampA`, `lampB` | int | 0–255 | current PWM level |
| `ambient` | int | 0–4095 | raw 12-bit ADC reading. Falls as light rises (LDR module wiring) |
| `lux` | number, optional | 0–200 000 | illuminance computed on the hub; the daylight thresholds (500 on / 300 off) are in lux |
| `uptime` | int | 0–2³²−1 | `millis()` |
| `distance` | number, optional | −1–1000 | last HC-SR04 range in cm, −1 on echo timeout. Needed by the stuck-state rule; one 5 s snapshot, not the 12.5 Hz stream |
| `fault` | bool, optional | | `sensorFaulted()` |

## event

```json
{"seq":17,"state":"GRACE","uptime":123456}
```

`seq` starts at 1 after every boot and increments by exactly 1 per state
change, whether or not the publish succeeds. The backend treats a jump as lost
events and `uptime` going backwards as a reboot.

## cmd

```json
{"type":"brightness","target":"lampA","value":128,"token":"<API_TOKEN>","correlationId":"c1a2b3c4d5e6f7a8b"}
```

| Field | Values |
|---|---|
| `type` | `on` (→255), `off` (→0), `brightness` (value 0–255), `scene` (value 0–255; the scene name stays on the backend), `resume` (release override) |
| `target` | `lampA`, `lampB`, `all` (ignored for `resume`) |
| `value` | int 0–255; the backend always sends it, already resolved |
| `token` | shared bearer token, ≤ 64 chars (`Command.token[65]`) |
| `correlationId` | ≤ 23 chars (`Command.correlationId[24]`) |

Every command except `resume` sets the override flag; only `resume` clears it.
Schedules are executed by the backend: it publishes an ordinary command at the
due time, so the hub needs no schedule support.

## ack

```json
{"result":"OK","correlationId":"c1a2b3c4d5e6f7a8b","source":"MQTT","type":"brightness","target":"lampA","value":128}
```

| Field | Values |
|---|---|
| `result` | `OK`, `AUTH_FAIL`, `BAD_REQUEST` |
| `correlationId` | echoed, even on `AUTH_FAIL`, so rejected attempts are visible |
| `source` | optional: `MQTT`, `REST`, `BUTTON` (the hub's wall switch) |
| `type`, `target`, `value` | optional, on `OK`: what was executed, as resolved on the hub |

The hub publishes an ack for **every** command, whichever way it arrived. A
wall-switch press is acked with source `BUTTON` and a correlation id
`b<millis>`, so the backend logs it and its override audit knows a human is in
control. The wall switch needs no token: physical access to the room is the
authority.

## REST (LAN only, plain HTTP)

- `POST /command`: same JSON body as `cmd`. Responds `200` / `401` / `400` with
  the same body as `ack`, plus `Access-Control-Allow-Origin: <dashboard origin>`.
  The hub also publishes that ack on MQTT.
- `GET /status`: same body as `telemetry`.

In Wokwi the hub cannot accept incoming connections on the free public
gateway, so REST is demonstrated and measured against the Python simulator,
not the Wokwi hub. Say so wherever REST numbers are reported.

## Behaviour notes

- `GRACE` holds the current lamp level for the 6 s grace period.
- On reboot the override flag is lost; the backend's override audit resets on a
  detected reboot rather than reporting a violation.
- Serial log lines for Objective 1: `[T] <ms> first_in_range …` and
  `[T] <ms> confirmed state=… latency_ms=…`. Latency is measured from the first
  in-range sample to the confirming sample; the actual entry happened up to one
  poll (80 ms) before the first sample.
