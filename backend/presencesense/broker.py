"""Building MQTT clients from Settings, shared by the backend, the simulator and netcheck.

Networks that block the MQTT ports (1883/8883) usually still allow 443, so the
backend can reach a broker over MQTT-over-WebSocket with TLS on 443 while the
hub (in Wokwi's cloud) uses plain MQTT to the same broker.
"""

import secrets
import socket
import threading
import time

import paho.mqtt.client as mqtt

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def make_client(settings, client_id, will=None):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
        transport=settings.mqtt_transport,
    )
    if settings.mqtt_transport == "websockets":
        client.ws_set_options(path=settings.mqtt_ws_path)
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    if settings.mqtt_tls:
        client.tls_set(ca_certs=settings.mqtt_ca_file or None)
    if will:
        client.will_set(*will)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


def is_local(settings):
    return settings.mqtt_host in LOCAL_HOSTS


def random_hub_id():
    return "ps-" + secrets.token_hex(8)


def netcheck(settings, extra_ports=((1883, "MQTT"), (8883, "MQTT/TLS"), (443, "HTTPS/WSS"))):
    """Print which ports reach the broker, then do a real pub/sub round trip
    with the configured settings, a retained message and a Last Will."""
    host = settings.mqtt_host
    print(f"Broker {host}, configured as {settings.mqtt_transport} on port {settings.mqtt_port}"
          f" (TLS {'on' if settings.mqtt_tls else 'off'})\n")
    for port, label in extra_ports:
        try:
            socket.create_connection((host, port), timeout=5).close()
            print(f"  port {port:<5} {label:<10} open")
        except OSError as exc:
            print(f"  port {port:<5} {label:<10} BLOCKED ({type(exc).__name__})")

    root = f"ps-netcheck-{secrets.token_hex(6)}"
    received = {}
    arrived = threading.Event()

    def on_message(client, userdata, msg):
        received[msg.topic.rsplit("/", 1)[-1]] = msg.payload.decode(errors="replace")
        arrived.set()

    def connect(client):
        connected = threading.Event()
        client.on_connect = lambda c, u, f, rc, p: (not rc.is_failure) and connected.set()
        client.connect_async(host, settings.mqtt_port, keepalive=5)
        client.loop_start()
        return connected.wait(10)

    print(f"\nMQTT test under {root}/ ...")
    publisher = make_client(settings, f"{root}-pub")
    if not connect(publisher):
        print("  FAILED: could not connect with the configured settings.")
        publisher.loop_stop()
        return False
    publisher.publish(f"{root}/retained", "online", qos=1, retain=True).wait_for_publish(5)

    subscriber = make_client(settings, f"{root}-sub")
    subscriber.on_message = on_message
    connect(subscriber)
    subscriber.subscribe(f"{root}/#", qos=1)
    ok_retained = arrived.wait(8) and received.get("retained") == "online"

    arrived.clear()
    started = time.perf_counter()
    publisher.publish(f"{root}/ping", "x", qos=1)
    ok_ping = arrived.wait(5) and "ping" in received
    rtt_ms = (time.perf_counter() - started) * 1000

    arrived.clear()
    victim = make_client(settings, f"{root}-will", will=(f"{root}/will", "offline", 1, False))
    connect(victim)
    time.sleep(1)
    victim._sock_close()  # vanish without DISCONNECT, as a crashed hub would
    victim.loop_stop()
    ok_will = arrived.wait(20) and received.get("will") == "offline"

    publisher.publish(f"{root}/retained", b"", retain=True).wait_for_publish(5)
    for client in (publisher, subscriber):
        client.disconnect()
        client.loop_stop()

    print(f"  publish/subscribe : {'OK, ' + format(rtt_ms, '.0f') + ' ms round trip' if ok_ping else 'FAILED'}")
    print(f"  retained message  : {'OK' if ok_retained else 'FAILED'}")
    print(f"  Last Will         : {'OK' if ok_will else 'FAILED'}")
    return ok_ping and ok_retained and ok_will
