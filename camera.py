"""Camera connection: test the connection, grab a snapshot for drawing zones, record (İSG Kamera).

The "source" field of the camera config:

  {"type": "rtsp", "brand": "hikvision", "ip": "<camera IP>", "port": 554,
   "username": "admin", "channel": 1, "stream": "sub"}
  {"type": "rtsp", "url": "rtsp://{user}:{password}@<camera IP>:554/stream1", "username": "admin"}
  {"type": "onvif", "ip": "<device IP>", "port": 80, "username": "admin", "channel": 1, "stream": "sub"}     # address learned from the device
  {"type": "onvif", "ip": "<device IP>", "port": 80, "username": "admin", "profile": "<profile token>", "video_source": "<video source token>"}
  {"type": "usb", "index": 0}
  {"type": "http", "url": "http://<camera IP>:8080/video"}
  {"type": "file", "path": "videos/hall.mp4"}

The password is NEVER written to the camera config. It is taken, in this order, from:
  1) the password typed on the program's screen (kept in memory only)
  2) the environment variable named in the "password_env" field (default CAMERA_PASSWORD, then KAMERA_SIFRE)
  3) a prompt when running on the command line (not echoed)

Use from source:
  python camera.py brands
  python camera.py test   data/cameras/hall.json            # connect, grab one frame: cameras/_frame_<camera>.jpg
  python camera.py record data/cameras/hall.json --minutes 15
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import paths
from i18n import N_, T

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}

# Default password variables when the config names none: the new name first, the v0.1.0 name as fallback.
DEFAULT_PASSWORD_ENVS = ("CAMERA_PASSWORD", "KAMERA_SIFRE")

# {user}, {password}, {ip}, {port}, {channel}; separate templates for the main and sub stream.
# "name" is shown with T() (brand names have no translation; only the EZVIZ hint does).
BRANDS = {
    "hikvision": {"name": "Hikvision / HiLook", "main": "rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}01", "sub": "rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}02"},
    "dahua": {"name": "Dahua / Imou / IMOU NVR", "main": "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype=0", "sub": "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype=1"},
    "uniview": {"name": "Uniview (UNV)", "main": "rtsp://{user}:{password}@{ip}:{port}/unicast/c{channel}/s0/live", "sub": "rtsp://{user}:{password}@{ip}:{port}/unicast/c{channel}/s1/live"},
    "reolink": {"name": "Reolink", "main": "rtsp://{user}:{password}@{ip}:{port}/h264Preview_{channel:02d}_main", "sub": "rtsp://{user}:{password}@{ip}:{port}/h264Preview_{channel:02d}_sub"},
    "tapo": {"name": "TP-Link Tapo / VIGI", "main": "rtsp://{user}:{password}@{ip}:{port}/stream1", "sub": "rtsp://{user}:{password}@{ip}:{port}/stream2"},
    "axis": {"name": "Axis", "main": "rtsp://{user}:{password}@{ip}:{port}/axis-media/media.amp", "sub": "rtsp://{user}:{password}@{ip}:{port}/axis-media/media.amp?resolution=640x360"},
    "ezviz": {"name": N_("EZVIZ (user admin, password: the verification code under the device)"), "main": "rtsp://{user}:{password}@{ip}:{port}/H.264", "sub": "rtsp://{user}:{password}@{ip}:{port}/H.264"},
}

# Address placeholders: current names first; the v0.1.0 names are still understood in a config that escaped migration.
_USER_PLACEHOLDERS = ("{user}", "{kullanici}")
_PASSWORD_PLACEHOLDERS = ("{password}", "{sifre}")


def mask_password(url: str) -> str:
    """Hides the password when the address is shown."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1****@", url)


def _has_password_placeholder(template: str) -> bool:
    return any(p in template for p in _PASSWORD_PLACEHOLDERS)


def _fill(template: str, user: str, password_of) -> str:
    """Fills the user and password placeholders of a full address; `password_of()` is only asked if one is needed."""
    url = template
    for p in _USER_PLACEHOLDERS:
        url = url.replace(p, quote(user, safe=""))
    if _has_password_placeholder(url):
        password = quote(password_of(), safe="")
        for p in _PASSWORD_PLACEHOLDERS:
            url = url.replace(p, password)
    return url


def needs_password(config: dict) -> bool:
    """Does this camera need a password (to show the password box on screen)?"""
    s = config.get("source") or {}
    kind = s.get("type", "rtsp")
    if kind == "onvif":
        return True
    if kind == "rtsp":
        return _has_password_placeholder(s["url"]) if s.get("url") else True
    if kind == "http":
        return _has_password_placeholder(s.get("url", ""))
    return False


def get_password(s: dict, password: str | None = None) -> str:
    if password is not None:  # came from the program's screen (may be empty: camera without a password)
        return password
    named = s.get("password_env") or s.get("sifre_degiskeni")  # the variable name is user data: used as written
    names = (named,) if named else DEFAULT_PASSWORD_ENVS
    value = next((os.environ[n] for n in names if n in os.environ), None)
    if value is None:
        if sys.stdin is None or not sys.stdin.isatty():
            raise SystemExit(T("No camera password was entered. Type it in the password box (or set the {variable} "
                               "environment variable).", variable=names[0]))
        value = getpass.getpass(T("Camera password ({user}): ", user=s.get("username", "admin")))
    return value


def source(config: dict, password: str | None = None) -> tuple[str | int, str]:
    """(source for OpenCV/ffmpeg, the form shown on screen)."""
    s = config.get("source")
    if not s:
        raise SystemExit(T('The settings file has no "source" field. Choose the connection type on the settings page and save.'))
    kind = s.get("type", "rtsp")
    if kind == "file":
        if not s.get("path"):
            raise SystemExit(T("The video file path is missing."))
        path = str((paths.DATA / s["path"]).resolve()) if not Path(s["path"]).is_absolute() else s["path"]
        if not Path(path).exists():
            raise SystemExit(T("Video file not found: {path}", path=s["path"]))
        return path, Path(path).name
    if kind == "usb":
        return int(s.get("index", 0)), T("USB camera {no}", no=s.get("index", 0))
    user = s.get("username", "admin")
    if kind == "http":
        template = s.get("url", "")
        if not template:
            raise SystemExit(T("The HTTP stream address is missing."))
        url = _fill(template, user, lambda: get_password(s, password))
        return url, mask_password(url)
    if kind == "onvif":  # ONVIF camera or recorder: the stream address is asked from the device on every connect
        import onvif_client

        pw = get_password(s, password)
        try:
            url, profile = onvif_client.resolve_stream(s.get("ip", ""), int(s.get("port") or 80), user, pw,
                                                       profile=s.get("profile") or None, channel=int(s.get("channel") or 1),
                                                       stream=s.get("stream", "sub"), source=s.get("video_source") or None)
        except onvif_client.OnvifError as e:
            raise SystemExit(f"ONVIF: {e}")
        print(T("ONVIF profile: channel {channel} · {name} · {codec} {width}x{height}", channel=profile["channel"],
                name=profile["name"] or profile["token"], codec=profile["encoding"], width=profile["width"],
                height=profile["height"]), flush=True)
        url = url.replace("rtsp://", f"rtsp://{quote(user, safe='')}:{quote(pw, safe='')}@", 1) if user else url
        return url, mask_password(url)
    if kind != "rtsp":
        raise SystemExit(T("Unknown camera type: {type} (rtsp, onvif, http, usb, file)", type=kind))
    if s.get("url"):
        url = _fill(s["url"], user, lambda: get_password(s, password))
        return url, mask_password(url)
    brand = s.get("brand", "hikvision").lower()
    if brand not in BRANDS:
        raise SystemExit(T("Unknown brand: {brand}. Enter the full address in the \"url\" field or choose one of: {brands}",
                           brand=brand, brands=", ".join(BRANDS)))
    if not s.get("ip"):
        raise SystemExit(T("The camera's IP address is missing. Enter the IP address on the settings page and save."))
    template = BRANDS[brand]["sub" if s.get("stream", "sub") == "sub" else "main"]
    url = template.format(user=quote(user, safe=""), password=quote(get_password(s, password), safe=""), ip=s["ip"],
                          port=s.get("port", 554), channel=int(s.get("channel", 1)))
    return url, mask_password(url)


def code(config: dict, config_path: Path) -> str:
    return config.get("code") or config_path.stem


def open_capture(src):
    import cv2

    if isinstance(src, int):
        return cv2.VideoCapture(src)
    if src.startswith("rtsp"):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # TCP is more stable
    # give up if there is no answer within 8 s (OpenCV default: 30 s)
    return cv2.VideoCapture(src, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000])


def mask_faces(frame, stop: threading.Event | None = None):
    """Masks the faces in the zone-drawing snapshot (same model and same masking code as the engine in Studio).
    Returns (frame, number of masked regions). `stop`: if set while Studio works, the run is closed (studio.Stopped)."""
    import rules  # noqa: F401  (adds the engine folder to the path)
    from isg_core import community_model

    if community_model.is_community_folder(paths.ENGINE / "models"):  # an encrypted package opens only in Studio
        return _mask_in_studio(frame, stop)
    from isg_core.detect import Detector
    from isg_core.recorder import face_regions, mask_faces as mask

    detections = Detector(str(paths.ENGINE / "models"), [rules.MODEL_PLAN[0]], str(paths.DATA)).run(frame)
    mask(frame, detections)
    return frame, len(face_regions(detections))


class OneTimeFrame:
    """Hands one frame to Studio without writing it to disk: a small HTTP server on 127.0.0.1 only, at a random address,
    that answers once. Every connection is handled in its own thread with a short timeout: a client that stalls or sends
    nothing cannot hold up the server or its closing. close() waits for no client."""

    TIMEOUT = 3.0  # seconds a client has to send its request and take the answer

    def __init__(self, data: bytes, content_type: str = "image/png"):
        import secrets
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.served = threading.Event()
        self._lock = threading.Lock()
        self._data = data
        key = secrets.token_urlsafe(24)
        outer = self

        class Request(BaseHTTPRequestHandler):
            timeout = self.TIMEOUT  # socket timeout: a half-sent request is dropped after this long

            def log_message(self, *a):
                pass

            def do_GET(self):
                with outer._lock:
                    wanted = self.path == "/" + key and not outer.served.is_set()
                    if wanted:
                        outer.served.set()
                        body, outer._data = outer._data, b""
                if not wanted:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False  # server_close() does not wait for open connections

        self._server = Server(("127.0.0.1", 0), Request)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/{key}"
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="one-frame").start()

    def close(self, max_s: float = 2.0) -> None:
        """Closes the server; waits for no client and takes at most `max_s`."""
        with self._lock:
            self._data = b""
            self.served.set()
        closer = threading.Thread(target=self._server.shutdown, daemon=True, name="one-frame-close")
        closer.start()
        closer.join(max_s)
        try:
            self._server.server_close()
        except OSError:
            pass


def _mask_in_studio(frame, stop: threading.Event | None = None):
    """A frame with unmasked faces is never written to disk: the Studio run fetches it from memory at a one-time local
    address served by this process."""
    import cv2
    import secrets

    import studio

    ok, png = cv2.imencode(".png", frame)
    if not ok:
        raise OSError(T("could not encode the frame"))
    server = OneTimeFrame(png.tobytes())
    studio.RUN_DIR.mkdir(parents=True, exist_ok=True)
    output = studio.RUN_DIR / f"face-{secrets.token_hex(4)}.png"
    try:
        # studio.mask_faces returns only after the Studio process has ended (or was closed): the server is closed after it
        count = studio.mask_faces(server.url, output, stop=stop)
        masked = cv2.imread(str(output))
        if masked is None:
            raise OSError(T("could not read the masked frame"))
        return masked, count
    finally:
        server.close()
        output.unlink(missing_ok=True)


def test(config: dict, config_path: Path, password: str | None = None, stop: threading.Event | None = None) -> int:
    """Connects, grabs one frame and saves it with the faces masked. Returns 0 if a snapshot was saved, 1 if not, 2 if
    `stop` was set meanwhile (nothing is saved)."""
    import cv2

    src, shown = source(config, password)
    print(T("Connecting:"), shown, flush=True)
    t0 = time.time()
    cap = open_capture(src)
    if not cap.isOpened():
        print(T("COULD NOT CONNECT ({secs:.1f} s). Check: are the IP and port correct, is the computer on the same network as "
                "the camera,", secs=time.time() - t0), flush=True)
        print(T("are the user/password correct, is RTSP enabled on the camera, does the brand template fit this model."), flush=True)
        return 1
    frame = None
    for _ in range(30):  # the first frames may be corrupt
        ok, fr = cap.read()
        if ok and fr is not None:
            frame = fr
            break
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if frame is None:
        print(T("Connected, but no video arrived. Try the substream (\"stream\": \"sub\") or another channel."), flush=True)
        return 1
    H, W = frame.shape[:2]
    print(T("CONNECTED: {width}x{height}, {fps:.0f} fps, {secs:.1f} s. Masking faces…", width=W, height=H, fps=fps,
            secs=time.time() - t0), flush=True)
    path = paths.CAMERAS / f"_frame_{code(config, config_path)}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    import studio

    try:
        if stop is not None and stop.is_set():
            raise studio.Stopped(T("Stopped."))
        masked, count = mask_faces(frame, stop)
        if not count:  # the model may have missed a person: the whole frame is blurred so no face stays visible
            masked = cv2.GaussianBlur(masked, (0, 0), max(4.0, W / 160))
        # last look right before the snapshot is written: a stop up to here leaves the old snapshot as it is. A stop that
        # arrives later finds the test finished: the caller reports it as done, not as stopped.
        if stop is not None and stop.is_set():
            raise studio.Stopped(T("Stopped."))
    except studio.Stopped:  # the user stopped the test (Studio may have been masking the faces): nothing is saved
        print(T("Stopped."), flush=True)
        return 2
    if not cv2.imwrite(str(path), masked):  # for drawing zones; faces masked
        print(T("Could not save the snapshot (the data folder may be full or not writable)."), flush=True)
        return 1  # the old snapshot (if any) is still there: the test must not be shown as successful
    if count == 1:
        print(T("Snapshot saved: {n} face masked.", n=count), flush=True)
    elif count:
        print(T("Snapshot saved: {n} faces masked.", n=count), flush=True)
    else:
        print(T("Snapshot saved: no person found, the snapshot was blurred."), flush=True)
    if W > 1920:
        print(T("Note: the resolution is high; the substream (720p) is enough for analysis and much faster."), flush=True)
    return 0


def record(config: dict, config_path: Path, minutes: float, target: Path | None = None, password: str | None = None,
           stop: threading.Event | None = None) -> Path:
    """Records the camera at 720p. If `stop` is set the recording ends early (what was recorded so far is kept)."""
    src, shown = source(config, password)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target or paths.VIDEOS / f"{code(config, config_path)}_{stamp}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    secs = max(5, int(minutes * 60))
    print(T("Recording: {source} → {file} ({secs} s)", source=shown, file=target.name, secs=secs), flush=True)
    if isinstance(src, int):
        return _record_opencv(src, target, secs, stop)
    if str(src).startswith("rtsp"):
        input_args = ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    elif (config.get("source") or {}).get("type") == "file":
        input_args = ["-re"]  # read the video file at real speed, like a live camera
    else:
        input_args = []
    # scale down to 720p, drop audio; re-encode so every camera gives the same format
    cmd = [paths.ffmpeg(), "-y", "-loglevel", "error", *input_args, "-i", str(src), "-t", str(secs), "-an",
           "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(target)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **_NO_WINDOW)
    paths.bind_child_process(p)  # if the program closes, the recording stops too
    errors = []
    reader = threading.Thread(target=lambda: errors.extend(p.stderr.read().decode("utf-8", "replace").splitlines()), daemon=True)
    reader.start()
    while p.poll() is None:
        if stop is not None and stop.is_set():
            try:  # "q" to ffmpeg: it closes the file cleanly and exits
                p.stdin.write(b"q")
                p.stdin.flush()
            except OSError:
                pass
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
            break
        time.sleep(0.3)
    reader.join(timeout=5)
    stopped = stop is not None and stop.is_set()
    if not target.exists() or target.stat().st_size < 10_000 or (p.returncode not in (0, None) and not stopped):
        last = mask_password(errors[-1]) if errors else T("unknown error")
        raise SystemExit(T("Could not record: {error}", error=last))
    return target


def _record_opencv(src: int, target: Path, secs: int, stop: threading.Event | None = None) -> Path:
    import cv2

    cap = open_capture(src)
    if not cap.isOpened():
        raise SystemExit(T("Could not open the USB camera."))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    W, H = int(cap.get(3)), int(cap.get(4))
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    t0 = time.time()
    while time.time() - t0 < secs and not (stop is not None and stop.is_set()):
        ok, fr = cap.read()
        if not ok:
            break
        writer.write(fr)
    cap.release()
    writer.release()
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=T("Camera connection"))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("brands", help=T("list the built-in brand templates"))
    for name in ("test", "record"):
        p = sub.add_parser(name)
        p.add_argument("config")
        if name != "test":
            p.add_argument("--minutes", type=float, default=15)
    a = ap.parse_args()
    if a.command == "brands":
        for k, v in BRANDS.items():
            print(f"{k:<10} {T(v['name'])}\n           {mask_password(v['sub'].replace('{user}', 'admin').replace('{password}', 'x'))}")
        print(T("Brand not in the list: enter the full address in the \"url\" field and leave {password} in place of the password."))
        return
    paths.prepare()
    config_path = Path(a.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if a.command == "test":
        raise SystemExit(test(config, config_path))
    print(T("Saved:"), record(config, config_path, a.minutes))


if __name__ == "__main__":
    main()
