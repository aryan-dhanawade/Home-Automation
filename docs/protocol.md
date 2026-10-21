# PresenceSense wire protocol

The contract between the hub (ESP8266 firmware, or `python -m presencesense sim`)
and the backend. The backend validates every inbound message against this and
rejects + logs anything else (`rejected_messages` table). The firmware must
produce exactly these shapes.

Topic root: `home/<hub_id>` (default `home/hub01`).

| Topic | Direction | QoS (documented) | Retained | Payload |
|---|---|---|---|---|
| `status` | hub → | 1 | yes | `online` / `offline` (plain text, Last Will = `offline`) |
| `telemetry` | hub → | 0 | no | snapshot every 5 s |
| `event` | hub → | 1 | no | one per state transition, sequence-numbered |
| `cmd` | → hub | 1 | no | command object |
| `ack` | hub → | 1 | no | result + the command's correlation id |

`PubSubClient` publishes at QoS 0 only, so the QoS column is the broker-side /
subscriber setting, not a delivery guarantee from the hub. The backend measures
actual delivery from sequence gaps instead.

Every payload must be ≤ 512 bytes (keep under `PubSubClient`'s 256 B default, or
call `setBufferSize()`).

## telemetry

```json
{"state":"OCCUPIED","lampA":255,"lampB":255,"ambient":312,"distance":84.5,"fault":false,"uptime":123456}
```

| Field | Type | Range | Notes |
|---|---|---|---|
| `state` | string | `VACANT` `OCCUPIED` `OCCUPIED_DAYLIGHT` `GRACE` `OVERRIDE` | `OVERRIDE` whenever the override flag is set |
| `lampA`, `lampB` | int | 0–255 | current PWM level |
| `ambient` | int | 0–1023 | raw A0 reading |
| `uptime` | int | 0–2³²−1 | `millis()` |
| `distance` | number, optional | −1–1000 | last HC-SR04 range in cm, −1 on echo timeout. Needed by the stuck-state rule; one 5 s snapshot, not the 10 Hz stream |
| `fault` | bool, optional | | `sensorFaulted()` |

## event

```json
{"seq":17,"state":"GRACE","uptime":123456}
```

`seq` starts at 1 after every boot and increments by exactly 1 per published
event. The backend treats a jump as lost events and `uptime` going backwards as
a reboot.

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
{"result":"OK","correlationId":"c1a2b3c4d5e6f7a8b"}
```

`result` ∈ `OK`, `AUTH_FAIL`, `BAD_REQUEST`. Echo the correlation id even on
`AUTH_FAIL`, so rejected attempts are visible on the dashboard.

## REST (LAN only, plain HTTP)

- `POST /command`: same JSON body as `cmd`. Responds `200` / `401` / `400` with
  the same body as `ack`, plus `Access-Control-Allow-Origin: <dashboard origin>`.
- `GET /status`: same body as `telemetry`.

## Behaviour notes for the firmware

- `GRACE` should **hold** the current lamp level for the 6 s grace period;
  `setLamp(ALL, state == OCCUPIED ? 255 : 0)` as drafted would switch the lamp off
  as soon as grace starts. The simulator holds.
- On reboot the override flag is lost; the backend's override audit resets on a
  detected reboot rather than reporting a violation.
