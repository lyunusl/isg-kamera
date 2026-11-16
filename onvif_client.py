"""ONVIF client (standard library only): device discovery on the network, channel and profile list, stream address.

ONVIF is the common management interface of IP cameras and recorders (NVR/DVR). This module only reads:

  discover()                         finds ONVIF devices on the network (WS-Discovery; no password needed)
  Device(ip, port, user, password)   .info()  .profiles()  .select_profile(profile, channel, stream)  .stream_url(token)

On a recorder each channel is a separate video source; profiles are grouped into channels by source (numbered from 1).
The password is never written anywhere; it is sent with each request as a WS-Security "UsernameToken" (digest), and HTTP
Digest is tried too if the device asks for it.

Security: the host name in the service and stream addresses the device reports is not used; requests and the stream always
go to the IP address the user entered (only the path and port are taken). So the user name and password cannot be sent to
another address. Redirects are not followed.

Command line:
  python onvif_client.py discover
  python onvif_client.py profiles <ip> [port] [user]      (asks for the password, not shown on screen)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.client
import os
import re
import select
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from i18n import N_, T

DISCOVERY_ADDR = ("239.255.255.250", 3702)
COMMON_PORTS = N_("80, 8000, 8080 or 8899")
MAX_RESPONSES = 2 * 1024 * 1024  # largest response read from a device (bytes): a channel list is far below this
_TIMEOUT_TEXT = N_("The device did not complete its response in time.")


def __getattr__(name):
    """TIMEOUT_MESSAGE: the timeout message in the current language (it is what OnvifError carries on a timeout)."""
    if name == "TIMEOUT_MESSAGE":
        return T(_TIMEOUT_TEXT)
    raise AttributeError(name)

_ENVELOPE = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
             'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
             'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>')
_SECURITY = ('<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
             'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken>'
             '<wsse:Username>{user}</wsse:Username>'
             '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
             '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce}</wsse:Nonce>'
             '<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>')
_PROBE = ('<?xml version="1.0" encoding="UTF-8"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
          'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
          'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{message_id}</w:MessageID>'
          '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
          '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>'
          '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>')


class OnvifError(Exception):
    """Carries a message to show to the user. `kind` tells the kind of failure without reading the (translated) message:
    "unreachable" (device not reachable), "auth" (user name or password rejected), "no_response" (no ONVIF response)."""

    def __init__(self, message: str = "", kind: str = ""):
        super().__init__(message)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # the user name and password must not be carried to another address
        return None


def _parse(data: bytes) -> ET.Element:
    """Parses XML from the device. A response with a document type or entity declaration is rejected (memory blow-up through
    nested entities)."""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ET.ParseError("document type declarations are not accepted")
    return ET.fromstring(data)


def _socket(resp):
    """The socket under a response (resp.fp.raw._sock for a successful response, one level deeper for an HTTP error); None
    if not found."""
    for e in (resp, getattr(resp, "fp", None)):
        s = getattr(getattr(getattr(e, "fp", None), "raw", None), "_sock", None)
        if s is not None:
            return s
    return None


def _read(resp, deadline: float) -> bytes:
    """Reads the response body; OnvifError if it exceeds MAX_RESPONSES bytes or the `deadline` moment (time.monotonic).

    Each read is a single socket read (read1) and the socket timeout is lowered to the time left: a device that sends the
    body slowly cannot stretch the read beyond the total time."""
    chunks, total = [], 0
    sock = _socket(resp)
    read = getattr(resp, "read1", None) or resp.read
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise OnvifError(T(_TIMEOUT_TEXT))
        if sock is not None:
            try:
                sock.settimeout(left)
            except OSError:
                pass
        try:
            chunk = read(65536)
        except TimeoutError:
            raise OnvifError(T(_TIMEOUT_TEXT)) from None
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_RESPONSES:
            raise OnvifError(T("The device's response is much larger than expected; it was not read."))
        chunks.append(chunk)


def _natural_key(text: str) -> list:
    """Natural sort key: "VideoSource_10" comes after "VideoSource_2"."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def _clean(text: str) -> str:
    """Text written by the device is not trusted: only letters, digits and plain punctuation are shown, at most 120 characters."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w .,:;()/\-]", " ", text or "")).strip()[:120]


def _close_socket(s) -> None:
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass


class _Handler(urllib.request.HTTPHandler):
    """Records the socket of every connection the request opens in `sockets`: when time runs out the caller cancels the
    request by closing these sockets. No new connection is opened once `cancelled` is set."""

    def __init__(self, sockets: list, cancelled: threading.Event):
        super().__init__()
        self._sockets, self._cancelled = sockets, cancelled

    def http_open(self, req):
        sockets, cancelled = self._sockets, self._cancelled

        class Connection(http.client.HTTPConnection):
            def connect(self):
                if cancelled.is_set():
                    raise OSError(T("request cancelled"))
                super().connect()
                sockets.append(self.sock)
                if cancelled.is_set():  # time ran out while connecting
                    _close_socket(self.sock)
                    raise OSError(T("request cancelled"))

        return self.do_open(Connection, req)


class _Digest(urllib.request.HTTPDigestAuthHandler):
    def http_error_401(self, req, fp, code, msg, headers):
        fp.close()  # the connection of the first (credentials-requesting) response is closed before the authenticated retry
        return super().http_error_401(req, fp, code, msg, headers)


def _local_name(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _find(root: ET.Element, *names: str) -> ET.Element | None:
    """First element following the given chain of names, ignoring namespaces (each step is searched in the subtree)."""
    e = root
    for name in names:
        e = next((x for x in e.iter() if x is not e and _local_name(x) == name), None)
        if e is None:
            return None
    return e


def _text(root: ET.Element, *names: str) -> str:
    e = _find(root, *names)
    return (e.text or "").strip() if e is not None else ""


def _local_addresses() -> list[str]:
    addresses = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass
    return addresses or [""]


def discover(timeout: float = 3.0, interfaces: list[str] | None = None) -> list[dict]:
    """ONVIF devices on the network: [{"ip", "port", "name", "model", "url"}]. Searches only the network(s) this computer is
    connected to; devices behind a router are not found (their IP address is entered by hand)."""
    probe = _PROBE.format(message_id=uuid.uuid4()).encode("utf-8")
    sockets = []
    for ip in interfaces if interfaces is not None else _local_addresses():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.sendto(probe, DISCOVERY_ADDR)
            sockets.append(s)
        except OSError:
            continue
    found: dict[tuple[str, int], dict] = {}
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
    try:
        while sockets:
            left = (deadline - dt.datetime.now()).total_seconds()
            if left <= 0:
                break
            ready, _, _ = select.select(sockets, [], [], min(left, 0.5))
            for s in ready:
                try:
                    data, (sender, _) = s.recvfrom(65535)
                    root = _parse(data)
                except (OSError, ET.ParseError):
                    continue
                for match in (x for x in root.iter() if _local_name(x) == "ProbeMatch"):
                    try:  # a broken response (invalid address or port) is just skipped, the search goes on
                        url = next((a for a in _text(match, "XAddrs").split()
                                    if urlsplit(a).scheme == "http" and re.fullmatch(r"[\d.]+", urlsplit(a).hostname or "") and (urlsplit(a).port or 80)), None)
                        if url is None:
                            continue
                        scopes = _text(match, "Scopes").split()
                        scope = lambda key: _clean(next((urllib.request.unquote(k.rsplit("/", 1)[-1]) for k in scopes if f"/{key}/" in k), ""))[:60]  # noqa: E731
                        port = urlsplit(url).port or 80
                        found.setdefault((sender, port), {"ip": sender, "port": port, "name": scope("name"), "model": scope("hardware"), "url": url})
                    except ValueError:
                        continue
    finally:
        for s in sockets:
            s.close()
    return sorted(found.values(), key=lambda c: tuple(int(p) for p in c["ip"].split(".")))


class Device:
    """An ONVIF device (camera or recorder). All requests go to the `ip` address."""

    def __init__(self, ip: str, port: int = 80, username: str = "admin", password: str = "", timeout: float = 6.0):
        if not re.fullmatch(r"[A-Za-z0-9.\-]+", ip or ""):
            raise OnvifError(T("The device's IP address is missing or invalid."))
        self.ip, self.port, self.username, self.password, self.timeout = ip, int(port or 80), username or "", password or "", timeout
        self._clock_offset: float | None = None  # device clock - this computer's clock (s): the digest is built for the device's clock
        self._media: str | None = None

    # --- addresses
    def _pin(self, url: str, default_path: str) -> str:
        """Takes only the path (and the port, if it is the device's own address) from an address the device reports; the
        host is always self.ip."""
        p = urlsplit(url or "")
        port = p.port if p.hostname == self.ip and p.port else self.port
        return f"http://{self.ip}:{port}{p.path or default_path}"

    @property
    def _device_service(self) -> str:
        return f"http://{self.ip}:{self.port}/onvif/device_service"

    # --- request
    def _security_header(self) -> str:
        if not self.username:
            return ""
        nonce = os.urandom(16)
        created = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self._clock_offset or 0.0)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode("ascii") + self.password.encode("utf-8")).digest()).decode("ascii")
        return _SECURITY.format(user=escape(self.username), digest=digest, nonce=base64.b64encode(nonce).decode("ascii"), created=created)

    def _request(self, url: str, body: str, authenticated: bool = True) -> ET.Element:
        """A single SOAP request. Connecting, headers, reading the body and an HTTP Digest retry if needed TOGETHER take at
        most self.timeout seconds (monotonic clock). The request runs in a separate thread; when time runs out it is
        CANCELLED: the sockets of its connections are closed, it may not open new ones, and the thread is waited for. So
        after an error no open connection to the device and no running worker are left."""
        deadline = time.monotonic() + self.timeout
        result: list = []
        sockets: list = []
        cancelled = threading.Event()

        def work():
            try:
                result.append((True, self._request_inner(url, body, authenticated, deadline, sockets, cancelled)))
            except BaseException as e:  # noqa: BLE001  (passed on to the caller)
                result.append((False, e))

        worker = threading.Thread(target=work, daemon=True, name="onvif-request")
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            cancelled.set()
            for s in list(sockets):
                _close_socket(s)
            worker.join(1.0)  # a read whose socket was closed fails at once
            raise OnvifError(T(_TIMEOUT_TEXT))
        if not result:
            raise OnvifError(T(_TIMEOUT_TEXT))
        ok, value = result[0]
        if ok:
            return value
        if isinstance(value, OnvifError):
            raise value
        if isinstance(value, UnicodeError):  # urllib's HTTP Digest code handles the password as ASCII
            raise OnvifError(T("The device asks for HTTP Digest authentication; this method cannot be used with a user name or "
                               "password that contains non-ASCII characters.")) from None
        raise OnvifError(T("The ONVIF request could not be completed ({error}).", error=type(value).__name__)) from None

    def _request_inner(self, url: str, body: str, authenticated: bool, deadline: float, sockets: list,
                       cancelled: threading.Event) -> ET.Element:
        data = _ENVELOPE.format(header=self._security_header() if authenticated else "", body=body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), _Handler(sockets, cancelled))
        for attempt in (1, 2):
            left = deadline - time.monotonic()
            if left <= 0:
                raise OnvifError(T(_TIMEOUT_TEXT))
            try:
                with opener.open(request, timeout=left) as r:
                    return _parse(_read(r, deadline))
            except urllib.error.HTTPError as e:
                try:
                    content = _read(e, deadline)
                finally:
                    e.close()  # the error response's connection is closed on every exit (timeout included)
                if e.code == 401 and attempt == 1 and authenticated and "digest" in (e.headers.get("WWW-Authenticate") or "").lower():
                    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()  # the device asks for HTTP Digest
                    passwords.add_password(None, url, self.username, self.password)
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), _Handler(sockets, cancelled), _Digest(passwords))
                    continue
                # The free error text written by the device is read only to tell the kind of error; it is not carried into
                # the message, the panel or the job log (it is not trusted). The user sees only the HTTP code and our message.
                try:
                    fault = _parse(content)
                    fault_text = " ".join((x.text or "") for x in fault.iter() if _local_name(x) in ("Value", "Text", "faultstring"))
                except ET.ParseError:
                    fault_text = ""
                if e.code in (401, 403) or re.search(r"NotAuthorized|Unauthorized|FailedAuthentication|not authorized", fault_text, re.I):
                    raise OnvifError(T("The device did not accept the user name or password. ONVIF access must be enabled for this "
                                       "user on the device."), "auth") from None
                if 300 <= e.code < 400:
                    raise OnvifError(T("The device redirected to another address; redirects are not followed.")) from None
                raise OnvifError(T("The device rejected the request (HTTP {code}).", code=e.code)) from None
            except (urllib.error.URLError, OSError) as e:
                raise OnvifError(T("Could not reach the device ({ip}:{port}): {reason}. The ONVIF port is usually {ports}; ONVIF must be "
                                   "enabled on the device.", ip=self.ip, port=self.port, reason=getattr(e, "reason", e), ports=T(COMMON_PORTS)),
                                 "unreachable") from None
            except ET.ParseError:
                raise OnvifError(T("The device at {ip}:{port} did not give an ONVIF response. Check the ONVIF port ({ports}).",
                                   ip=self.ip, port=self.port, ports=T(COMMON_PORTS)), "no_response") from None
        raise OnvifError(T("The device did not accept the user name or password."), "auth")

    def _sync_clock(self) -> None:
        """Reads the device clock (no credentials needed): the digest is accepted only with a timestamp close to the device's clock."""
        if self._clock_offset is not None:
            return
        self._clock_offset = 0.0
        try:
            response = self._request(self._device_service, "<tds:GetSystemDateAndTime/>", authenticated=False)
            u = _find(response, "UTCDateTime")
            if u is not None:
                num = lambda *path: int(_text(u, *path))  # noqa: E731
                device_time = dt.datetime(num("Date", "Year"), num("Date", "Month"), num("Date", "Day"), num("Time", "Hour"), num("Time", "Minute"),
                                          num("Time", "Second"), tzinfo=dt.timezone.utc)
                self._clock_offset = (device_time - dt.datetime.now(dt.timezone.utc)).total_seconds()
        except OnvifError as e:
            if e.kind == "unreachable":
                raise  # no device: the next requests should not wait until the timeout as well
        except ValueError:
            pass  # clock could not be read: this computer's clock is used

    def _media_url(self) -> str:
        if self._media is None:
            self._sync_clock()
            try:
                response = self._request(self._device_service, "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>")
                self._media = self._pin(_text(response, "Media", "XAddr"), "/onvif/Media")
            except OnvifError as e:
                if e.kind in ("auth", "unreachable", "no_response"):
                    raise
                self._media = f"http://{self.ip}:{self.port}/onvif/Media"  # device without a capability list: the common path
        return self._media

    # --- reading
    def info(self) -> dict:
        """{"manufacturer", "model", "firmware"}; an empty dict if it cannot be read."""
        self._sync_clock()
        try:
            y = self._request(self._device_service, "<tds:GetDeviceInformation/>")
        except OnvifError:
            return {}
        return {"manufacturer": _clean(_text(y, "Manufacturer"))[:60], "model": _clean(_text(y, "Model"))[:60],
                "firmware": _clean(_text(y, "FirmwareVersion"))[:60]}

    def profiles(self) -> list[dict]:
        """Video profiles: [{"token", "name", "channel", "source", "encoding", "width", "height"}], sorted by channel.

        `source` is the token of the video source (physical channel). The `channel` number is the natural order of the source
        tokens (from 1): whatever order the device reports the profiles in, the same source gets the same number."""
        response = self._request(self._media_url(), "<trt:GetProfiles/>")
        out = []
        for p in (x for x in response.iter() if _local_name(x) == "Profiles"):
            if _find(p, "VideoEncoderConfiguration") is None:
                continue  # audio or metadata profile
            num = lambda *path: int(_text(p, *path) or 0)  # noqa: E731
            out.append({"token": p.get("token") or "", "name": _clean(_text(p, "Name"))[:60], "channel": 0,
                        "source": _text(p, "VideoSourceConfiguration", "SourceToken") or "-",
                        "encoding": _clean(_text(p, "VideoEncoderConfiguration", "Encoding"))[:20],
                        "width": num("VideoEncoderConfiguration", "Resolution", "Width"),
                        "height": num("VideoEncoderConfiguration", "Resolution", "Height")})
        if not out:
            raise OnvifError(T("The device reported no video profile."))
        sources = sorted({p["source"] for p in out}, key=_natural_key)
        for p in out:
            p["channel"] = sources.index(p["source"]) + 1
        return sorted(out, key=lambda p: (p["channel"], -p["width"] * p["height"], _natural_key(p["token"])))

    def select_profile(self, profile: str | None = None, channel: int = 1, stream: str = "sub", profiles: list[dict] | None = None,
                       source: str | None = None) -> dict:
        """The profile to use.

        Saved tokens take precedence over the channel number and tie the camera to a physical channel:
          - if the `profile` token exists on the device (and belongs to the same source when `source` is saved): that profile;
          - if the profile token is gone but `source` still exists on the device: the sub or main stream profile of the SAME source;
          - if neither is found: an error; it never falls back to another channel.
        With no saved token: if the device has a single channel (one video source), the sub or main stream profile of that source.
        On a multi-channel device a camera saved only with a channel number is AMBIGUOUS (the number may map to another
        physical channel depending on the order the device reports profiles in or on the sort rule): it does not connect and
        asks for the profile to be selected again."""
        every = profiles if profiles is not None else self.profiles()
        pick = max if stream == "main" else min
        if profile or source:
            p = next((x for x in every if x["token"] == profile), None) if profile else None
            if p is not None and (not source or p["source"] == source):
                return p
            same_source = [x for x in every if x["source"] == source] if source else []
            if same_source:
                return pick(same_source, key=lambda x: x["width"] * x["height"])
            raise OnvifError(T("The saved profile or video source was not found on the device; it did not switch to another channel. "
                               "List the channels again in the camera settings and select the profile."))
        if len({x["source"] for x in every}) > 1:
            raise OnvifError(T("This camera is on a multi-channel device and was saved only with a channel number; it is not certain "
                               "which physical channel it is. In the camera settings, click \"List channels and profiles\", select the "
                               "profile and save."))
        candidates = [x for x in every if x["channel"] == int(channel or 1)]
        if not candidates:
            raise OnvifError(T("The device has no channel {channel} (it reported {count} channels).", channel=channel,
                               count=max(x["channel"] for x in every)))
        return pick(candidates, key=lambda x: x["width"] * x["height"])

    def stream_url(self, token: str) -> str:
        """RTSP address of the profile (without user name and password). The host is always self.ip."""
        response = self._request(self._media_url(), "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>"
                                 f"<tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{escape(token)}</trt:ProfileToken></trt:GetStreamUri>")
        uri = _text(response, "MediaUri", "Uri") or _text(response, "Uri")
        p = urlsplit(uri)
        if p.scheme != "rtsp":
            raise OnvifError(T("The device gave no RTSP address for this profile."))
        return f"rtsp://{self.ip}:{p.port or 554}{p.path}{'?' + p.query if p.query else ''}"


def resolve_stream(ip: str, port: int, username: str, password: str, profile: str | None = None, channel: int = 1, stream: str = "sub",
                   source: str | None = None) -> tuple[str, dict]:
    """(RTSP address, selected profile). The address contains no user name or password."""
    d = Device(ip, port, username, password)
    p = d.select_profile(profile, channel, stream, source=source)
    return d.stream_url(p["token"]), p


if __name__ == "__main__":
    import getpass
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "discover":
        for c in discover() or [{"ip": T("no device found"), "port": "", "name": "", "model": ""}]:
            print(c["ip"], c["port"], c["name"], c["model"])
    elif len(sys.argv) >= 3 and sys.argv[1] == "profiles":
        user = sys.argv[4] if len(sys.argv) > 4 else "admin"
        try:
            d = Device(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 80, user, getpass.getpass(T("Password ({user}): ", user=user)))
            print(d.info())
            for p in d.profiles():
                print(T("channel {channel}", channel=p["channel"]) + f"  {p['token']:<18} {p['name']:<24} {p['encoding']} {p['width']}x{p['height']}")
        except OnvifError as e:
            raise SystemExit(str(e))
    else:
        raise SystemExit(__doc__)
