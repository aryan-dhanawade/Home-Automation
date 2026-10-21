# PresenceSense backend + dashboard

Python side of the PresenceSense hub: MQTT subscriber, SQLite store, rule
engine, energy report, 30-day retention job and a Flask dashboard. It talks to
the hub using the contract in [../docs/protocol.md](../docs/protocol.md).

```
hub (ESP8266 / simulator) ──MQTT──> broker ──> HubService.ingest ──> validate ──> SQLite
                                                       │                           │
                                                       └──> rules (gaps, stuck,    │
                                                            fault, override audit)  │
dashboard (browser) ──bearer token──> Flask /api ──> HubService.send_command ──MQTT/REST──> hub
                                                 └──> energy report, metrics <──────┘
```

## Setup

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows; .venv/bin/python on Linux/macOS
cp .env.example .env
```

Put a token in `.env` (`PS_API_TOKEN`), the same value as `API_TOKEN` in the
firmware's `Secrets.h`:

```bash
python -c "import secrets; print(secrets.token_hex(16))"
```

On the public broker anyone can subscribe to your topics, so also change
`PS_HUB_ID` to something non-obvious (it sets the topic root `home/<hub_id>`).

## Run

```bash
.venv/Scripts/python -m presencesense serve
```

Open http://localhost:5000 and sign in with the token.

No hub yet? Run the simulator in a second terminal. It implements the firmware
contract (LWT, telemetry, sequence-numbered events, token-checked commands,
override flag) and cycles through every state every 2 minutes:

```bash
.venv/Scripts/python -m presencesense sim --rest-port 8081
```

Set `PS_DEVICE_URL=http://127.0.0.1:8081` in `.env` to enable the REST
transport. Other simulator options: `--scenario box` (an object parked in the
beam, which trips the stuck-state rule after `PS_STUCK_WINDOW_S`) and
`--drop-events 0.1` (drops 10 % of events, to exercise gap detection).

### Broker and networks that block MQTT

By default the backend talks to shiftr.io's public broker
(`public.cloud.shiftr.io`) over MQTT-over-WebSocket with TLS on **port 443**,
which gets through networks that block MQTT's ports 1883/8883. The Wokwi hub
reaches the same broker on 1883 from Wokwi's cloud. Check any network first:

```bash
.venv/Scripts/python -m presencesense netcheck
```

It reports which ports are open, then does a real publish/subscribe round
trip, a retained message and a Last Will. On a public broker, `serve` and `sim`
refuse to start unless `PS_HUB_ID` is random (`ps-` + 16 hex characters).

Offline alternative (simulator only; the Wokwi hub cannot reach your PC on
Wokwi's free gateway):

```bash
.venv/Scripts/python -m pip install amqtt
.venv/Scripts/amqtt -c broker.yaml
```

and set `PS_MQTT_HOST=127.0.0.1` in `.env` (port 1883, plain TCP, no TLS follow
automatically).

### The real hub in Wokwi

See [../firmware/README.md](../firmware/README.md). Same `PS_HUB_ID` and
`PS_API_TOKEN` go into the firmware's `Secrets.h`.

## Reports and maintenance

```bash
.venv/Scripts/python -m presencesense report --minutes 60     # Markdown energy report
.venv/Scripts/python -m presencesense report --minutes 60 --json
.venv/Scripts/python -m presencesense metrics --minutes 1440  # evaluation metrics
.venv/Scripts/python -m presencesense retention               # also runs hourly inside `serve`
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

## What it does, mapped to the brief

| Brief item | Where |
|---|---|
| Validate payloads, reject + log malformed | `protocol.py`, `HubService.ingest` → `rejected_messages` |
| Persist with device uptime + server wall-clock | `db.py` (`events`, `telemetry`, `commands`, `device_status`) |
| Sequence-gap detection → delivery metric | `rules.SequenceTracker`, `seq_gaps`; reboot = uptime going backwards |
| Stuck state ("box in the beam") | `rules.StuckStateDetector`: occupied ≥ window with distance stdev ≤ ceiling |
| Liveness from retained LWT | `status` topic → `device_status`; `stale` if no telemetry for 3 periods |
| Objective 7, override integrity | `rules.OverrideAuditor`: after an acked manual command, telemetry must show `OVERRIDE` + commanded levels until `resume` |
| Commands with correlation id + measured round-trip | `HubService.send_command`; RTT = ack time − send time, both on the server clock |
| Schedule command (Objective 4) | `scheduled_commands`; the backend publishes at the due time |
| Energy report vs always-on | `energy.py`: Σ(brightness/255 × Δt) × rated W; gaps > 3 periods excluded from both sides; hub 0.25 W separate |
| Retention: > 30 days → daily totals | `retention.py` → `event_daily`, `telemetry_daily`; raw rows deleted |
| Bearer token, constant-time compare, rejections logged | `web/__init__.py` (`hmac.compare_digest`) → `auth_rejections`; all `/api` routes, reads included |
| CORS = exact dashboard origin | `flask-cors` on `/api/*` with `PS_DASHBOARD_ORIGIN` |
| Secrets out of git | `.env` gitignored, `.env.example` committed |

## Known limits

- Flask's built-in server is fine for a LAN demo; it is not a production server.
- The dashboard is plain HTTP. The token travels in clear on the LAN, the same
  documented gap as the hub's REST plane.
- Energy is modelled from logged brightness, not measured (INA219 is the next step).
  Resolution is one telemetry period (5 s) per transition.
- The override audit only sees commands sent through this backend. A `resume`
  sent straight to the hub's REST endpoint would look like a violation.
