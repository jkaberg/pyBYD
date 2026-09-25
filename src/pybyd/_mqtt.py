"""Internal MQTT bootstrap, parsing, and runtime helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, cast

import paho.mqtt.client as mqtt
from pydantic import BaseModel, ConfigDict

from pybyd._api._common import build_inner_base, post_token_json
from pybyd._crypto.aes import aes_decrypt_utf8
from pybyd._crypto.hashing import md5_hex
from pybyd._redact import redact_for_log
from pybyd._transport import SecureTransport
from pybyd.config import BydConfig
from pybyd.exceptions import BydCryptoError, BydError
from pybyd.session import Session


class MqttBootstrap(BaseModel):
    """Broker/session data required to connect to BYD MQTT."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    broker_host: str
    broker_port: int
    topic: str
    client_id: str
    username: str
    password: str


class MqttEvent(BaseModel):
    """Normalized decrypted MQTT event envelope."""

    model_config = ConfigDict(frozen=True)

    event: str
    vin: str | None
    topic: str
    payload: dict[str, Any]


def _parse_broker(raw_broker: str) -> tuple[str, int]:
    value = raw_broker.strip()
    if not value:
        raise ValueError("Broker value is empty")

    if "://" in value:
        value = value.split("://", 1)[1]
    if "/" in value:
        value = value.split("/", 1)[0]

    host, _, maybe_port = value.rpartition(":")
    if host and maybe_port.isdigit():
        return host, int(maybe_port)
    return value, 8883


def _build_client_id(config: BydConfig) -> str:
    imei_md5 = (config.device.imei_md5 or "").strip().upper()
    if imei_md5 and set(imei_md5) != {"0"}:
        return f"oversea_{imei_md5}"
    return f"oversea_{md5_hex(config.device.imei)}"


def _build_mqtt_password(session: Session, client_id: str, ts_seconds: int) -> str:
    ts_text = str(ts_seconds)
    base = f"{session.sign_token}{client_id}{session.user_id}{ts_text}"
    return f"{ts_text}{md5_hex(base)}"


async def _fetch_emq_broker(
    config: BydConfig,
    session: Session,
    transport: SecureTransport,
) -> str:
    endpoint = "/app/emqAuth/getEmqBrokerIp"
    inner = build_inner_base(config)
    decoded = await post_token_json(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
    )
    if not isinstance(decoded, dict):
        raise BydError("Broker lookup response inner payload is not an object")

    broker = decoded.get("emqBorker") or decoded.get("emqBroker")
    if not isinstance(broker, str) or not broker.strip():
        raise BydError("Broker lookup response missing emqBorker/emqBroker")
    return broker.strip()


async def fetch_mqtt_bootstrap(
    config: BydConfig,
    session: Session,
    transport: SecureTransport,
) -> MqttBootstrap:
    """Build MQTT connection details from current authenticated session."""
    broker = await _fetch_emq_broker(config, session, transport)
    broker_host, broker_port = _parse_broker(broker)
    client_id = _build_client_id(config)
    now_seconds = int(time.time())
    return MqttBootstrap(
        user_id=session.user_id,
        broker_host=broker_host,
        broker_port=broker_port,
        topic=f"oversea/res/{session.user_id}",
        client_id=client_id,
        username=session.user_id,
        password=_build_mqtt_password(session, client_id, now_seconds),
    )


def decode_mqtt_payload(payload: bytes, decrypt_key_hex: str) -> tuple[dict[str, Any], str]:
    """Decrypt and parse MQTT payload bytes into a JSON object.

    Returns
    -------
    tuple[dict, str]
        The parsed dict **and** the decrypted plaintext string.
    """
    raw_text = payload.decode("ascii", errors="replace")
    raw_text = "".join(raw_text.split())
    plain = aes_decrypt_utf8(raw_text, decrypt_key_hex)
    parsed = json.loads(plain)
    if not isinstance(parsed, dict):
        raise BydError("MQTT payload decrypted to non-object JSON")
    return parsed, plain


class BydMqttRuntime:
    """Threaded paho-mqtt runtime that emits parsed events onto an asyncio loop."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        decrypt_key_hex: str,
        on_event: Callable[[MqttEvent], None],
        on_decrypt_error: Callable[[], None] | None = None,
        on_connack_refused: Callable[[int], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        keepalive: int = 120,
        logger: logging.Logger | None = None,
    ) -> None:
        self._loop = loop
        self._decrypt_key_hex = decrypt_key_hex
        self._on_event = on_event
        self._on_decrypt_error = on_decrypt_error
        self._on_connack_refused = on_connack_refused
        self._on_connected = on_connected
        self._keepalive = keepalive
        self._logger = logger or logging.getLogger(__name__)
        self._client: mqtt.Client | None = None
        self._running = False
        self._topic: str | None = None
        # Count of consecutive refused CONNACKs (paho auto-reconnects reuse
        # the same stale credentials forever; this drives client recovery).
        self._consecutive_connack_refusals = 0

    @property
    def is_running(self) -> bool:
        """Whether the MQTT runtime is actively running."""
        return self._running

    def update_decrypt_key(self, key_hex: str) -> None:
        """Update the AES decryption key (thread-safe string assignment)."""
        self._decrypt_key_hex = key_hex

    def start(self, bootstrap: MqttBootstrap) -> None:
        """Connect and subscribe with provided broker details."""
        self.stop()
        self._logger.debug(
            "MQTT runtime start requested host=%s port=%s topic=%s client_id=%s",
            bootstrap.broker_host,
            bootstrap.broker_port,
            bootstrap.topic,
            bootstrap.client_id,
        )

        client = mqtt.Client(
            callback_api_version=cast(Any, mqtt).CallbackAPIVersion.VERSION2,
            client_id=bootstrap.client_id,
            protocol=mqtt.MQTTv5,
        )
        client.enable_logger(self._logger)
        client.username_pw_set(bootstrap.username, bootstrap.password)
        client.tls_set()

        self._topic = bootstrap.topic

        def on_connect(
            c: mqtt.Client,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            if reason_code.value != 0:
                self._consecutive_connack_refusals += 1
                n = self._consecutive_connack_refusals
                self._logger.warning(
                    "MQTT connect failed reason=%s (consecutive refusals=%d)",
                    reason_code,
                    n,
                )
                if self._on_connack_refused is not None:
                    with contextlib.suppress(RuntimeError):
                        self._loop.call_soon_threadsafe(self._on_connack_refused, n)
                return
            self._consecutive_connack_refusals = 0
            self._logger.debug("MQTT connected reason=%s", reason_code)
            if self._topic:
                self._logger.debug("MQTT subscribe topic=%s", self._topic)
                c.subscribe(self._topic, qos=0)
            # Notify (on the asyncio loop) that the push channel is up —
            # fires on first connect and on every paho auto-reconnect.
            if self._on_connected is not None:
                self._loop.call_soon_threadsafe(self._on_connected)

        def on_message(_c: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
            try:
                parsed, _plaintext = decode_mqtt_payload(msg.payload, self._decrypt_key_hex)

                event_name = str(parsed.get("event") or "")
                vin_value = parsed.get("vin")
                vin = vin_value if isinstance(vin_value, str) and vin_value else None
                self._logger.debug(
                    "MQTT decoded topic=%s event=%s vin=%s payload=%s",
                    msg.topic,
                    event_name,
                    vin,
                    redact_for_log(parsed),
                )
                event = MqttEvent(
                    event=event_name,
                    vin=vin,
                    topic=msg.topic,
                    payload=parsed,
                )
                self._loop.call_soon_threadsafe(self._on_event, event)
            except BydCryptoError:
                snippet = msg.payload[:64].hex() if msg.payload else "<empty>"
                self._logger.debug(
                    "MQTT decrypt failed (%d bytes, head=%s) — likely stale key, requesting re-auth",
                    len(msg.payload),
                    snippet,
                )
                if self._on_decrypt_error is not None:
                    with contextlib.suppress(RuntimeError):
                        self._loop.call_soon_threadsafe(self._on_decrypt_error)
            except Exception:
                snippet = msg.payload[:64].hex() if msg.payload else "<empty>"
                self._logger.warning(
                    "MQTT payload parse failed (%d bytes, head=%s)",
                    len(msg.payload),
                    snippet,
                    exc_info=True,
                )

        def on_disconnect(
            _client: mqtt.Client,
            _userdata: Any,
            _disconnect_flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            if self._running:
                self._logger.debug("MQTT disconnected reason=%s", reason_code)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect

        client.connect(bootstrap.broker_host, bootstrap.broker_port, keepalive=self._keepalive)
        client.loop_start()

        self._client = client
        self._running = True
        self._logger.debug("MQTT network loop started")

    def stop(self) -> None:
        """Stop and disconnect current MQTT client if running."""
        client = self._client
        self._client = None
        self._running = False
        self._topic = None

        if client is None:
            return
        try:
            # Request a graceful DISCONNECT unconditionally — even a refused /
            # never-acked connection can hold a half-open socket.
            self._logger.debug("MQTT disconnect requested")
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        finally:
            client.loop_stop()
            # paho's disconnect()/loop_stop() do NOT deterministically close
            # the TLS socket — it is otherwise only reclaimed at GC (__del__).
            # Under frequent restarts (re-auth on session expiry / token
            # invalidation, key-refresh) the leaked file descriptors accumulate
            # and can eventually exhaust the host's FD limit ([Errno 24]),
            # taking the process down. Force the TLS socket + the loop_start
            # self-pipe closed right now.
            with contextlib.suppress(Exception):
                client._reset_sockets()  # closes _sock (TLS) + socketpair
            with contextlib.suppress(Exception):
                sock = client.socket()
                if sock is not None:
                    sock.close()
            self._logger.debug("MQTT network loop stopped (sockets released)")
