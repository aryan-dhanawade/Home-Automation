"""Runtime settings, read from environment variables (optionally via a .env file).

The .env file plays the same role as the firmware's Secrets.h: it is gitignored,
and .env.example is the committed template.
"""

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path):
    """Load KEY=VALUE lines into os.environ without overriding real env vars."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name, default=""):
    return os.environ.get(name, default)


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    hub_id: str = "hub01"

    # Default: shiftr.io's public broker over WebSocket + TLS on 443, which gets
    # through networks that block 1883/8883. The hub uses plain MQTT to the same host.
    mqtt_host: str = "public.cloud.shiftr.io"
    mqtt_port: int = 443
    mqtt_transport: str = "websockets"  # or "tcp"
    mqtt_ws_path: str = "/"
    mqtt_tls: bool = True
    mqtt_ca_file: str = ""
    mqtt_username: str = "public"
    mqtt_password: str = "public"
    mqtt_client_id: str = "presencesense-backend"

    # Shared bearer token: the dashboard must present it, and it is embedded in
    # every command so the hub's SecurityGate accepts it. Max 64 chars (Command.token[65]).
    api_token: str = ""
    # Base URL of the hub's REST control plane, e.g. http://192.168.1.50. Empty disables REST.
    device_url: str = ""

    db_path: str = "presencesense.db"
    dashboard_origin: str = "http://localhost:5000"
    web_host: str = "127.0.0.1"
    web_port: int = 5000

    lamp_a_watts: float = 9.0
    lamp_b_watts: float = 9.0
    hub_watts: float = 0.25

    telemetry_period_s: float = 5.0
    command_timeout_s: float = 2.5

    stuck_window_s: float = 600.0
    stuck_distance_ceiling_cm: float = 1.0
    stuck_duration_only_s: float = 4 * 3600.0

    retention_days: int = 30

    @property
    def topic_root(self):
        return f"home/{self.hub_id}"

    def topic(self, leaf):
        return f"{self.topic_root}/{leaf}"

    @property
    def max_gap_s(self):
        """Telemetry gaps longer than this count as 'unknown' time in the energy report."""
        return 3 * self.telemetry_period_s

    @classmethod
    def from_env(cls):
        # Unset broker options follow from the host and port, so a .env that only
        # says PS_MQTT_HOST=127.0.0.1 still means plain MQTT on 1883.
        host = _env("PS_MQTT_HOST") or cls.mqtt_host
        shiftr = host == cls.mqtt_host
        port = int(_env("PS_MQTT_PORT") or (443 if shiftr else 1883))
        transport = _env("PS_MQTT_TRANSPORT") or ("websockets" if port == 443 else "tcp")
        if transport not in ("tcp", "websockets"):
            raise ValueError("PS_MQTT_TRANSPORT must be 'tcp' or 'websockets'")
        return cls(
            hub_id=_env("PS_HUB_ID", cls.hub_id),
            mqtt_host=host,
            mqtt_port=port,
            mqtt_transport=transport,
            mqtt_ws_path=_env("PS_MQTT_WS_PATH") or cls.mqtt_ws_path,
            mqtt_tls=_env_bool("PS_MQTT_TLS", default=port in (443, 8883)),
            mqtt_ca_file=_env("PS_MQTT_CA_FILE"),
            mqtt_username=_env("PS_MQTT_USERNAME") or ("public" if shiftr else ""),
            mqtt_password=_env("PS_MQTT_PASSWORD") or ("public" if shiftr else ""),
            mqtt_client_id=_env("PS_MQTT_CLIENT_ID", cls.mqtt_client_id),
            api_token=_env("PS_API_TOKEN"),
            device_url=_env("PS_DEVICE_URL").rstrip("/"),
            db_path=_env("PS_DB_PATH", cls.db_path),
            dashboard_origin=_env("PS_DASHBOARD_ORIGIN", cls.dashboard_origin),
            web_host=_env("PS_WEB_HOST", cls.web_host),
            web_port=int(_env("PS_WEB_PORT", str(cls.web_port))),
            lamp_a_watts=float(_env("PS_LAMP_A_WATTS", str(cls.lamp_a_watts))),
            lamp_b_watts=float(_env("PS_LAMP_B_WATTS", str(cls.lamp_b_watts))),
            hub_watts=float(_env("PS_HUB_WATTS", str(cls.hub_watts))),
            telemetry_period_s=float(_env("PS_TELEMETRY_PERIOD_S", str(cls.telemetry_period_s))),
            command_timeout_s=float(_env("PS_COMMAND_TIMEOUT_S", str(cls.command_timeout_s))),
            stuck_window_s=float(_env("PS_STUCK_WINDOW_S", str(cls.stuck_window_s))),
            stuck_distance_ceiling_cm=float(
                _env("PS_STUCK_DISTANCE_CEILING_CM", str(cls.stuck_distance_ceiling_cm))
            ),
            stuck_duration_only_s=float(
                _env("PS_STUCK_DURATION_ONLY_S", str(cls.stuck_duration_only_s))
            ),
            retention_days=int(_env("PS_RETENTION_DAYS", str(cls.retention_days))),
        )
