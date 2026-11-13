"""Real-time appliance updates: MQTT 3.1.1 over WebSocket on AWS IoT, the same channel as the official app.

Listen only: this module subscribes and never publishes, so it cannot send commands.
Authentication is the AWS IoT custom authorizer used by the app ('candy-iot-authorizer'): the signature
comes from auth/v1/introspection and the token is the id token of the login.
The MQTT codec is written here on purpose (a subscriber needs six packet types): no native dependency and
everything runs inside the asyncio loop on an aiohttp WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import struct
import time
from typing import Any, Callable

import aiohttp

from .hon import HonAuthError, HonClient, HonError

_LOGGER = logging.getLogger(__name__)

MQTT_URL = os.environ.get("HON_HUB_MQTT_URL", "wss://a30f6tqw0oh1x0-ats.iot.eu-west-1.amazonaws.com/mqtt")
AUTHORIZER = "candy-iot-authorizer"
KEEPALIVE = 60                 # seconds, sent in CONNECT
PING_EVERY = 25                # a PINGREQ is sent after this many silent seconds
SILENCE_LIMIT = KEEPALIVE + 30  # nothing received for this long: the connection is dead
TOPICS_PER_SUBSCRIBE = 8       # AWS IoT limit
BACKOFF_MIN = 5
BACKOFF_MAX = 300

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14
PINGREQ_PACKET = b"\xc0\x00"
DISCONNECT_PACKET = b"\xe0\x00"


# ── MQTT 3.1.1 codec ─────────────────────────────────────────────────────────

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        n, byte = divmod(n, 128)
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _string(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("!H", len(raw)) + raw


def connect_packet(client_id: str, keepalive: int = KEEPALIVE) -> bytes:
    body = _string("MQTT") + bytes([4, 0x02]) + struct.pack("!H", keepalive) + _string(client_id)   # clean session
    return bytes([CONNECT << 4]) + _varint(len(body)) + body


def subscribe_packet(packet_id: int, topics: list[str]) -> bytes:
    body = struct.pack("!H", packet_id) + b"".join(_string(t) + b"\x00" for t in topics)   # QoS 0
    return bytes([SUBSCRIBE << 4 | 0x02]) + _varint(len(body)) + body


def puback_packet(packet_id: int) -> bytes:
    return bytes([PUBACK << 4, 2]) + struct.pack("!H", packet_id)


def split_packets(buf: bytearray) -> list[tuple[int, int, bytes]]:
    """Remove the complete packets from buf and return them as (type, flags, body)."""
    out = []
    while len(buf) >= 2:
        length, mult, i = 0, 1, 1
        while True:
            if i >= len(buf):
                return out          # the length itself is not complete yet
            byte = buf[i]
            length += (byte & 0x7F) * mult
            mult *= 128
            i += 1
            if not byte & 0x80:
                break
            if i > 4:
                raise ValueError("malformed MQTT length")
        if len(buf) < i + length:
            return out
        out.append((buf[0] >> 4, buf[0] & 0x0F, bytes(buf[i:i + length])))
        del buf[:i + length]
    return out


def parse_publish(flags: int, body: bytes) -> tuple[str, bytes, int, int]:
    """-> (topic, payload, qos, packet id)."""
    size = struct.unpack("!H", body[:2])[0]
    topic = body[2:2 + size].decode()
    rest = body[2 + size:]
    qos = (flags >> 1) & 3
    packet_id = 0
    if qos:
        packet_id = struct.unpack("!H", rest[:2])[0]
        rest = rest[2:]
    return topic, rest, qos, packet_id


def status_parameters(payload: Any) -> dict[str, str]:
    """Parameters of an appliancestatus message as {name: value}."""
    pars = payload.get("parameters") if isinstance(payload, dict) else None
    out: dict[str, str] = {}
    if isinstance(pars, list):
        for p in pars:
            if isinstance(p, dict) and "parName" in p:
                value = p.get("parNewVal", p.get("parValue"))
                if value is not None:
                    out[str(p["parName"])] = str(value)
    elif isinstance(pars, dict):
        for name, p in pars.items():
            value = p.get("parNewVal", p.get("parValue")) if isinstance(p, dict) else p
            if value is not None:
                out[str(name)] = str(value)
    return out


def default_topics(mac: str) -> list[str]:
    return [f"$aws/events/presence/disconnected/{mac}", f"$aws/events/presence/connected/{mac}",
            f"haier/things/{mac}/event/appliancestatus/update", f"haier/things/{mac}/event/discovery/update"]


# ── listener ─────────────────────────────────────────────────────────────────

class HonRealtime:
    """Keeps the channel open and reconnects by itself with fresh tokens."""

    def __init__(self, client: HonClient, appliances: list[dict], *,
                 on_status: Callable[[str, dict[str, str]], None],
                 on_presence: Callable[[str, bool], None],
                 on_discovery: Callable[[str], None] | None = None,
                 on_channel: Callable[[bool], None] | None = None,
                 session: aiohttp.ClientSession | None = None, url: str | None = None) -> None:
        self._client = client
        self._session = session
        self._url = url or MQTT_URL
        self._on_status = on_status
        self._on_presence = on_presence
        self._on_discovery = on_discovery
        self._on_channel = on_channel
        self._topics: dict[str, str] = {}      # topic -> mac
        for a in appliances:
            mac = a["macAddress"]
            for topic in (a.get("topics") or {}).get("subscribe") or default_topics(mac):
                self._topics[topic] = mac
        self._task: asyncio.Task | None = None
        self.connected = False
        self.connected_since = 0.0
        self.messages = 0
        self.last_message = 0.0
        self.last_error = ""

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run(), name="hon_hub realtime")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):   # noqa: BLE001 - we are shutting down
                pass
        self._channel(False)

    def _channel(self, up: bool) -> None:
        if up == self.connected:
            return
        self.connected = up
        self.connected_since = time.time() if up else 0.0
        self._call(self._on_channel, up)

    @staticmethod
    def _call(callback: Callable | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:   # noqa: BLE001 - a broken callback must not kill the listener
            _LOGGER.exception("hOn real-time callback failed")

    async def run(self) -> None:
        """Run until cancelled. Home Assistant runs this as a background task of the config entry."""
        try:
            await self._loop()
        finally:
            self._channel(False)

    async def _loop(self) -> None:
        backoff = BACKOFF_MIN
        while True:
            started = time.time()
            try:
                await self._connection()
                self.last_error = "closed by the server"
            except asyncio.CancelledError:
                raise
            except HonAuthError as err:
                # wrong password: the REST polling raises the re-authentication, here we just wait
                self.last_error = str(err)
                backoff = BACKOFF_MAX
            except (HonError, aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, ValueError, OSError) as err:
                self.last_error = f"{type(err).__name__}: {err}".rstrip(": ")
            except Exception as err:   # noqa: BLE001 - never give up on the channel
                self.last_error = f"{type(err).__name__}: {err}"
                _LOGGER.exception("hOn real-time channel: unexpected error")
            was_up = self.connected
            self._channel(False)
            if was_up and time.time() - started > 120:
                backoff = BACKOFF_MIN       # it worked for a while: reconnect quickly
            _LOGGER.debug("hOn real-time channel down (%s), retry in %s s", self.last_error, backoff)
            await asyncio.sleep(backoff + random.uniform(0, backoff / 4))
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _connection(self) -> None:
        await self._client.login()
        signature = await self._client.aws_token()
        if not signature:
            raise HonError("no signature for the real-time channel")
        headers = {"X-Amz-CustomAuthorizer-Name": AUTHORIZER, "X-Amz-CustomAuthorizer-Signature": signature,
                   "token": self._client.id_token}
        session = self._session or self._client.session
        async with asyncio.timeout(30):
            ws = await session.ws_connect(self._url, protocols=("mqtt",), headers=headers, heartbeat=None)
        try:
            await self._serve(ws)
        finally:
            if not ws.closed:
                try:
                    async with asyncio.timeout(5):
                        await ws.send_bytes(DISCONNECT_PACKET)
                        await ws.close()
                except (asyncio.TimeoutError, aiohttp.ClientError, ConnectionError, OSError, RuntimeError):
                    pass

    async def _serve(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        buf = bytearray()
        await ws.send_bytes(connect_packet(f"{self._client.mobile_id}_{secrets.token_hex(8)}"))
        topics = list(self._topics)
        pending: dict[int, list[str]] = {}
        granted = 0
        last_rx = last_tx = time.time()
        while True:
            # the keep-alive counts what WE send: ping even while the server keeps talking
            now = time.time()
            if now - last_tx >= PING_EVERY:
                if now - last_rx > SILENCE_LIMIT:
                    raise ConnectionError("no answer from the server")
                await ws.send_bytes(PINGREQ_PACKET)
                last_tx = now
            try:
                msg = await asyncio.wait_for(ws.receive(), max(1.0, PING_EVERY - (now - last_tx)))
            except asyncio.TimeoutError:
                continue
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                return
            if msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"websocket error: {ws.exception()}")
            if msg.type != aiohttp.WSMsgType.BINARY:
                continue
            last_rx = time.time()
            buf.extend(msg.data)
            for kind, flags, body in split_packets(buf):
                if kind == CONNACK:
                    if len(body) < 2 or body[1] != 0:
                        raise HonError(f"real-time channel refused (CONNACK {body[1] if len(body) > 1 else '?'})")
                    for n, start in enumerate(range(0, len(topics), TOPICS_PER_SUBSCRIBE), 1):
                        pending[n] = topics[start:start + TOPICS_PER_SUBSCRIBE]
                        await ws.send_bytes(subscribe_packet(n, pending[n]))
                    if not pending:
                        self._channel(True)
                elif kind == SUBACK:
                    asked = pending.pop(struct.unpack("!H", body[:2])[0], [])
                    refused = [t for t, code in zip(asked, body[2:]) if code & 0x80]
                    granted += len(asked) - len(refused)
                    if refused:
                        _LOGGER.debug("hOn real-time channel: %s subscriptions refused", len(refused))
                    if not pending:
                        if not granted:
                            # connected but deaf: better to say the channel is down, so the polling stays fast
                            raise HonError("every subscription was refused")
                        _LOGGER.debug("hOn real-time channel up, %s of %s topics", granted, len(topics))
                        self._channel(True)
                elif kind == PUBLISH:
                    topic, payload, qos, packet_id = parse_publish(flags, body)
                    if qos == 1:
                        await ws.send_bytes(puback_packet(packet_id))
                    self._message(topic, payload)

    def _message(self, topic: str, raw: bytes) -> None:
        mac = self._topics.get(topic)
        if mac is None:
            return
        self.messages += 1
        self.last_message = time.time()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        if "appliancestatus" in topic:
            params = status_parameters(payload)
            if params:
                self._call(self._on_status, mac, params)
        elif "/disconnected/" in topic:
            self._call(self._on_presence, mac, False)
        elif "/connected/" in topic:
            self._call(self._on_presence, mac, True)
        elif "discovery" in topic:
            self._call(self._on_discovery, mac)
