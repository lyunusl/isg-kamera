"""Outputs (integrations): forward new events to the systems the user has defined.

The "outputs" list in settings.json; each output has a type and filters:

  {"name": "...", "type": "...", "enabled": true, "min_score": 9, "serious_only": false,
   "cameras": ["warehouse1"], "categories": ["vehicle"], "rules": ["vehicle_pedestrian"], ...type-specific fields}

Types and their fields:
  webhook   url, headers? ({"Name": "value"})                    the whole event is POSTed as JSON
  slack     url (incoming webhook)                               short text
  teams     url (incoming webhook)                               short text
  mqtt      host, port (1883), topic, qos (0|1), retain?,        the whole event is published as JSON
            username?, password_env?
  modbus    host, port (502), unit_id (1), target ("coil"|"register"), address, value (1), pulse_s (0 = leave on)
            writes to the PLC over Modbus TCP: coil (function 5) or holding register (function 6)
  opcua     url (opc.tcp://...), node_id ("ns=2;s=Alarm"), value (true), pulse_s, username?, password_env?
  email     host, port (587), tls (true), sender, recipients [..], username?, password_env?

JSON payload (webhook body, MQTT message): {"event": {...English event fields...}}, default MQTT topic "isg/event".
Outputs migrated from v0.1.0 carry "legacy_payload": true and keep sending the v0.1.0 shape (built by
legacy.event_to_v1) and the old default MQTT topic, so the systems that consume them keep working.

Passwords are NOT written to the settings file: they are read from the environment variable named in "password_env".
No images are sent; only the event's text fields. Data goes only to the addresses the user entered.
Human texts (rule name, description, message text) are in the panel's current language; field names, rule ids and
JSON keys stay as they are.

IMPORTANT: analysis runs chunk by chunk, so outputs fire after the event, once that chunk's analysis has finished (delay =
chunk length + analysis time). These outputs are for warning lights, sirens, logging and notifications; they must NOT be
used in place of a machine safety stop (emergency stop).
"""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
import urllib.request

import db
import report
import rules
from i18n import N_, T

# output types: id -> label (N_-marked; shown with T())
TYPES = {
    "webhook": N_("Webhook (JSON)"), "slack": N_("Slack"), "teams": N_("Microsoft Teams"), "mqtt": N_("MQTT"), "modbus": N_("Modbus TCP (PLC)"),
    "opcua": N_("OPC UA (PLC / SCADA)"), "email": N_("Email (SMTP)"),
}
# In older versions the Siemens S7 output went through a block in the Studio scene. Studio now runs headless with the same
# ready-made scene for everyone, so S7 is not available in this version; saved S7 outputs are ignored.
FIELDS = ("id", "time", "camera", "camera_name", "rule", "rule_name", "category", "zone", "severity", "likelihood", "score", "serious",
          "duration_s", "people", "description", "clip", "snapshot")
MQTT_TOPIC = "isg/event"
LEGACY_MQTT_TOPIC = "isg/olay"  # default topic of v0.1.0 outputs (external contract, see legacy_payload)
# Sample event for the connection test; its human texts are English source texts, translated in sample_event().
SAMPLE_EVENT = {"id": 0, "time": "2026-01-01T12:00:00", "camera": "test", "camera_name": N_("Test camera"), "rule": "vehicle_pedestrian",
                "rule_name": N_("Vehicle–pedestrian proximity"), "category": "vehicle", "zone": N_("Loading area"), "severity": 5, "likelihood": 3,
                "score": 15, "serious": 1, "duration_s": 4.0, "people": 1, "description": N_("This is a test message."), "clip": None, "snapshot": None}
SAMPLE_TEXT_FIELDS = ("camera_name", "rule_name", "zone", "description")


def sample_event(**changes) -> dict:
    """The sample event with its texts in the current language."""
    return {**SAMPLE_EVENT, **{f: T(SAMPLE_EVENT[f]) for f in SAMPLE_TEXT_FIELDS}, **changes}


def _password(b: dict) -> str | None:
    name = b.get("password_env")
    return os.environ.get(name) if name else None


def text(o: dict) -> str:
    """Short text of an event (Slack, Teams, e-mail body)."""
    place = f"{o['camera_name']}" + (f" · {o['zone']}" if o.get("zone") else "")
    return (f"{T('SERIOUS RISK · ') if o['serious'] else ''}{o['rule_name']} — {place}\n"
            + T("{time} · risk score {score}/25 · {secs:g} s", time=o["time"].replace("T", " "), score=o["score"], secs=o["duration_s"])
            + f"\n{o.get('description') or ''}")


def payload(o: dict, legacy_payload: bool = False) -> dict:
    """JSON body of an event for webhook/MQTT. `legacy_payload`: the v0.1.0 shape (old field names and values)."""
    event = {f: o.get(f) for f in FIELDS}
    if legacy_payload:
        import legacy  # loaded only for outputs migrated from v0.1.0

        return {"olay": legacy.event_to_v1(event)}
    return {"event": event}


# ---------------------------------------------------------------- HTTP


def _http(url: str, body: dict, headers: dict | None = None) -> None:
    if not str(url).startswith(("http://", "https://")):
        raise ValueError(T("the address must start with http:// or https://"))
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "IsgKamera", **(headers or {})})
    with urllib.request.urlopen(request, timeout=8) as r:
        r.read()


# ---------------------------------------------------------------- MQTT 3.1.1 (publish only)


def _mqtt_str(s: bytes) -> bytes:
    return struct.pack("!H", len(s)) + s


def _mqtt_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError(T("connection closed"))
        data += chunk
    return data


def mqtt_publish(host: str, port: int, topic: str, message: bytes, qos: int = 0, retain: bool = False,
                 username: str | None = None, password: str | None = None, client_id: str = "isg-kamera") -> None:
    flags = 0x02  # clean session
    body = _mqtt_str(client_id.encode())
    if username:
        flags |= 0x80
        body += _mqtt_str(username.encode())
        if password is not None:
            flags |= 0x40
            body += _mqtt_str(password.encode())
    variable = _mqtt_str(b"MQTT") + bytes([0x04, flags]) + struct.pack("!H", 30)
    with socket.create_connection((host, int(port)), timeout=8) as s:
        s.settimeout(8)
        s.sendall(bytes([0x10]) + _mqtt_len(len(variable) + len(body)) + variable + body)
        reply = _read(s, 4)
        if reply[0] != 0x20 or reply[3] != 0:
            raise ConnectionError(T("the MQTT broker refused the connection (code {code})", code=reply[3]))
        qos = 1 if int(qos) == 1 else 0
        publish = _mqtt_str(topic.encode()) + (struct.pack("!H", 1) if qos else b"") + message
        s.sendall(bytes([0x30 | (qos << 1) | (1 if retain else 0)]) + _mqtt_len(len(publish)) + publish)
        if qos:
            ack = _read(s, 4)
            if ack[0] != 0x40:
                raise ConnectionError(T("the MQTT broker did not acknowledge the message"))
        s.sendall(bytes([0xE0, 0x00]))


# ---------------------------------------------------------------- Modbus TCP


MODBUS_ERRORS = {1: N_("function not supported"), 2: N_("invalid address"), 3: N_("invalid value"), 4: N_("device failure")}


def modbus_write(host: str, port: int, unit_id: int, target: str, address: int, value: int) -> None:
    """Writes a single coil (function 5) or a single holding register (function 6). `address` is zero-based (40001 → 0)."""
    if target == "coil":
        function, data = 5, 0xFF00 if value else 0x0000
    else:
        function, data = 6, int(value) & 0xFFFF
    pdu = struct.pack("!BHH", function, int(address) & 0xFFFF, data)
    frame = struct.pack("!HHHB", 1, 0, len(pdu) + 1, int(unit_id) & 0xFF) + pdu
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.settimeout(5)
        s.sendall(frame)
        head = _read(s, 7)
        length = struct.unpack("!H", head[4:6])[0]
        reply = _read(s, length - 1)
        if reply[0] & 0x80:
            code = reply[1] if len(reply) > 1 else 0
            raise ConnectionError(T("the PLC refused the write (Modbus exception code {code}: {reason})", code=code,
                                    reason=T(MODBUS_ERRORS.get(code, N_("unknown")))))
        if reply[0] != function:
            raise ConnectionError(T("unexpected response from the PLC"))


# ---------------------------------------------------------------- OPC UA


def opcua_write(url: str, node_id: str, value, username: str | None = None, password: str | None = None) -> None:
    try:
        from asyncua import ua
        from asyncua.sync import Client
    except ImportError as e:
        raise RuntimeError(T("the asyncua package needed for OPC UA is not installed")) from e
    client = Client(url, timeout=6)
    if username:
        client.set_user(username)
        if password is not None:
            client.set_password(password)
    client.connect()
    try:
        node = client.get_node(node_id)
        vtype = node.read_data_type_as_variant_type()
        if vtype == ua.VariantType.Boolean:
            # text values typed by the user: English and Turkish words for "on" are accepted
            value = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "evet", "açık", "acik")
        elif vtype in (ua.VariantType.Float, ua.VariantType.Double):
            value = float(value)
        elif vtype != ua.VariantType.String:
            value = int(value)
        else:
            value = str(value)
        node.write_value(ua.DataValue(ua.Variant(value, vtype)))
    finally:
        client.disconnect()


# ---------------------------------------------------------------- e-mail


def send_email(b: dict, subject: str, body: str) -> None:
    import smtplib
    from email.message import EmailMessage

    recipients = [a for a in b.get("recipients") or [] if a]
    if not recipients:
        raise ValueError(T("no recipients entered"))
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, b.get("sender") or b.get("username") or "isg-kamera@localhost", ", ".join(recipients)
    message.set_content(body)
    with smtplib.SMTP(b["host"], int(b.get("port") or 587), timeout=10) as s:
        if b.get("tls", True):
            s.starttls()
        if b.get("username"):
            password = _password(b)
            if password is None:
                if b.get("password_env"):
                    raise ValueError(T("email password not found: set the {variable} environment variable", variable=b["password_env"]))
                raise ValueError(T("email password not found: password_env is not set; set the environment variable"))
            s.login(b["username"], password)
        s.send_message(message)


# ---------------------------------------------------------------- sending


def _pulse(b: dict, switch_off) -> None:
    """The output is switched off after `pulse_s` seconds (0: it stays on and is reset on the PLC side)."""
    try:
        secs = float(b.get("pulse_s") or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0:
        t = threading.Timer(secs, lambda: _quiet(switch_off))
        t.daemon = True
        t.start()


def _quiet(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def deliver(b: dict, o: dict) -> None:
    """Sends one event to one output; raises an error if it fails. The event's texts are sent as given."""
    kind = b.get("type", "webhook")
    legacy_payload = bool(b.get("legacy_payload"))
    if kind in ("slack", "teams"):
        _http(b.get("url", ""), {"text": text(o)})
    elif kind == "webhook":
        _http(b.get("url", ""), payload(o, legacy_payload), b.get("headers") if isinstance(b.get("headers"), dict) else None)
    elif kind == "mqtt":
        topic = b.get("topic") or (LEGACY_MQTT_TOPIC if legacy_payload else MQTT_TOPIC)
        mqtt_publish(b["host"], b.get("port") or 1883, topic, json.dumps(payload(o, legacy_payload), ensure_ascii=False).encode("utf-8"),
                     b.get("qos") or 0, bool(b.get("retain")), b.get("username") or None, _password(b))
    elif kind == "modbus":
        target = b.get("target", "coil")
        args = (b["host"], b.get("port") or 502, b.get("unit_id") or 1, target, b.get("address") or 0)
        modbus_write(*args, b.get("value") if b.get("value") is not None else 1)
        _pulse(b, lambda: modbus_write(*args, 0))
    elif kind == "opcua":
        value = b.get("value") if b.get("value") is not None else True
        args = (b.get("url", ""), b.get("node_id", ""))
        opcua_write(*args, value, b.get("username") or None, _password(b))
        _pulse(b, lambda: opcua_write(*args, False if isinstance(value, bool) or str(value).lower() in ("true", "false") else 0,
                                      b.get("username") or None, _password(b)))
    elif kind == "email":
        send_email(b, f"[İSG Kamera] {o['rule_name']} · {o['camera_name']}", text(o))
    elif kind == "s7":
        raise ValueError(T("the Siemens S7 output is not supported in this version"))
    else:
        raise ValueError(T("unknown output type: {type}", type=kind))


def _rule_ids(ids) -> set:
    """Rule ids of a filter; old (v0.1.0) ids that escaped the migration are mapped to the new ones."""
    old = getattr(rules, "LEGACY_RULE_IDS", {})
    return {old.get(x, x) for x in ids}


def matches(b: dict, o: dict) -> bool:
    if not b.get("enabled", True):
        return False
    if o["score"] < int(b.get("min_score") or 0) or (b.get("serious_only") and not o["serious"]):
        return False
    if b.get("cameras") and o["camera"] not in b["cameras"]:
        return False
    if b.get("categories") and o["category"] not in b["categories"]:
        return False
    if b.get("rules") and o["rule"] not in _rule_ids(b["rules"]):
        return False
    return True


def _error_text(e: Exception) -> str:
    if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
        return T("the other side did not respond (timeout); check the address, port and network connection")
    if isinstance(e, ConnectionRefusedError):
        return T("connection refused; check the address and port and that the service on the other side is running")
    if isinstance(e, socket.gaierror):
        return T("could not resolve the server name; check the address")
    return (str(e) or e.__class__.__name__)[:160]


def dispatch(event_ids: list[int]) -> list[str]:
    """Sends new events to the enabled outputs. Returns short notes to show (errors included)."""
    # The panel sends all outputs (Studio runs headless with the ready-made scene; the scene has no output block)
    targets = [b for b in db.settings().get("outputs") or [] if b.get("enabled", True) and b.get("type") != "s7"]
    if not targets or not event_ids:
        return []
    events = db.query(f"SELECT * FROM event WHERE id IN ({', '.join('?' * len(event_ids))}) ORDER BY id", *event_ids)
    for o in events:
        report.localize_event(o)  # rule name and description in the current language
    notes = []
    for b in targets:
        n, errors = 0, []
        selected = [o for o in events if matches(b, o)]
        if b.get("type") in ("modbus", "opcua") and selected:
            selected = [max(selected, key=lambda o: o["score"])]  # a PLC output fires once per chunk
        for o in selected:
            try:
                deliver(b, o)
                n += 1
            except Exception as e:
                errors.append(_error_text(e))
        if n or errors:
            name = b.get("name") or T(TYPES.get(b.get("type"), N_("output")))
            notes.append((T('Output "{name}": {n} event sent', name=name, n=n) if n == 1 else T('Output "{name}": {n} events sent', name=name, n=n))
                         + (T("; {n} could not be sent ({error})", n=len(errors), error=errors[0]) if errors else "") + ".")
    return notes


def test(b: dict) -> tuple[bool, str]:
    """Sends a test message to an output. Returns (success, short explanation)."""
    try:
        deliver(b, sample_event(time=db.now()))
        return True, T("Test message sent.")
    except Exception as e:
        return False, _error_text(e)


def sanitize(b: dict) -> dict:
    """Validates an output coming from the panel and keeps only the known fields (no password field is kept)."""
    kind = b.get("type") if b.get("type") in TYPES else "webhook"

    def number(field, default, low, high):
        try:
            return max(low, min(high, int(float(b.get(field, default)))))
        except (TypeError, ValueError):
            return default

    def string(field, length=300):
        return str(b.get(field) or "").strip()[:length]

    def items(field):
        return [str(x)[:60] for x in (b.get(field) or []) if x][:40] if isinstance(b.get(field), list) else []

    out = {"name": string("name", 60) or T(TYPES[kind]), "type": kind, "enabled": bool(b.get("enabled", True)),
           "min_score": number("min_score", 0, 0, 25), "serious_only": bool(b.get("serious_only")), "cameras": items("cameras"),
           "categories": items("categories"), "rules": items("rules")}
    if b.get("legacy_payload"):
        out["legacy_payload"] = True  # migrated from v0.1.0: keeps sending the old payload shape
    if kind in ("webhook", "slack", "teams"):
        out["url"] = string("url", 500)
        if not out["url"].startswith(("http://", "https://")):
            raise SystemExit(T("The output address must start with http:// or https://."))
        if kind == "webhook" and isinstance(b.get("headers"), dict):
            out["headers"] = {str(k)[:60]: str(v)[:300] for k, v in list(b["headers"].items())[:8]}
    elif kind == "mqtt":
        out.update(host=string("host", 200), port=number("port", 1883, 1, 65535),
                   topic=string("topic", 200) or (LEGACY_MQTT_TOPIC if out.get("legacy_payload") else MQTT_TOPIC), qos=number("qos", 0, 0, 1),
                   retain=bool(b.get("retain")), username=string("username", 80), password_env=string("password_env", 60))
    elif kind == "modbus":
        out.update(host=string("host", 200), port=number("port", 502, 1, 65535), unit_id=number("unit_id", 1, 0, 255),
                   target="register" if b.get("target") == "register" else "coil", address=number("address", 0, 0, 65535),
                   value=number("value", 1, 0, 65535), pulse_s=number("pulse_s", 5, 0, 3600))
    elif kind == "opcua":
        out.update(url=string("url", 300), node_id=string("node_id", 200),
                   value=b.get("value") if isinstance(b.get("value"), (bool, int, float)) else (string("value", 60) or True),
                   pulse_s=number("pulse_s", 5, 0, 3600), username=string("username", 80), password_env=string("password_env", 60),
                   data_type=b.get("data_type") if b.get("data_type") in ("Float", "Double", "Int16", "Int32", "UInt16", "UInt32", "Boolean") else "Float")
        if not out["url"].startswith("opc.tcp://") or not out["node_id"]:
            raise SystemExit(T("The OPC UA address must start with opc.tcp:// and a node must be entered (e.g. ns=2;s=Alarm)."))
    elif kind == "email":
        out.update(host=string("host", 200), port=number("port", 587, 1, 65535), tls=bool(b.get("tls", True)), sender=string("sender", 120),
                   recipients=[str(a).strip()[:120] for a in (b.get("recipients") or []) if str(a).strip()][:20] if isinstance(b.get("recipients"), list) else [],
                   username=string("username", 120), password_env=string("password_env", 60))
    elif kind == "s7":
        out.update(host=string("host", 200), port=number("port", 102, 1, 65535), rack=number("rack", 0, 0, 7), slot=number("slot", 1, 0, 31),
                   db=number("db", 1, 1, 65535), byte=number("byte", 0, 0, 65535), bit=number("bit", 0, 0, 7))
    out["studio"] = False  # old "send with the Studio block" option: the panel now sends all outputs
    if kind in ("mqtt", "modbus", "email", "s7") and not out.get("host"):
        raise SystemExit(T("Server address is missing."))
    return out
