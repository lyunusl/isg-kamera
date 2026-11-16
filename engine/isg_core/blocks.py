"""Functions called by the AugeLab Studio blocks.

The blocks are thin: they pass the image and the camera code to these functions. The engine folder (isg_engine) sits
next to Studio's custom block folder and contains:

  isg_core/     this package
  models/       ONNX model files and package entries
  deps/         libraries Studio does not have (onnxruntime)
  engine.json   {"data_dir": "<the panel's data folder>"}  (camera settings, the queue and clips are there)

Status texts and the texts drawn into frames are in the language given by ISG_LANG (i18n.language()).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import cv2
import numpy as np

from . import catalog, model_license
from .detect import Detector
from .i18n import T, language, sample_name
from .recorder import Recorder, draw, enqueue
from .rule_engine import RuleEngine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Headless run (python -m studio run): the panel runs the scene template in a separate process for each camera and gives
# the camera's identity in these environment variables. The template's "ISG Camera Code" input is then ignored.
ENV_CAMERA = "ISG_CAMERA"                # "code#run"
ENV_CAMERA_URL = "ISG_CAMERA_URL"        # stream address of the live camera (may contain a password: never written to a file)
ENV_STOP_FILE = "ISG_STOP_FILE"          # when this file appears the stream ends: open events leave a closing record
ENV_PROBE_RESULT = "ISG_PROBE_RESULT"    # in a test run, whether the models opened is written to this file
PROBE_CODE = "_probe"                    # the panel's "try Studio" run: no camera, the models (and license) are tested on a black frame
# If these two variables are given in a test run, this frame is used instead of the black one; its faces are masked and it is
# written to ISG_FACE_OUTPUT (the zone drawing frame of a connection test: encrypted models open only in Studio). The frame is
# not written to disk: the panel serves it from a one-time address on 127.0.0.1.
ENV_FACE_FRAME_URL = "ISG_FACE_FRAME_URL"
ENV_FACE_OUTPUT = "ISG_FACE_OUTPUT"


def _local_frame(url: str):
    """Fetches the frame the panel serves from memory (127.0.0.1 only; no proxy is used)."""
    import urllib.request

    if not url.startswith("http://127.0.0.1:"):
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=15) as r:
        data = r.read(64 * 1024 * 1024)
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
MEASUREMENT_INTERVAL = 30.0  # seconds: heat map and counts are handed to the panel at this interval


def _error_json(reason: str) -> str:
    """Value given instead of the detections when detection is not working. An empty list means "nobody there"; this
    means "unknown": ISG Rules evaluates no rules on this frame, records no image and leaves an error for the panel."""
    return json.dumps({"error": reason}, ensure_ascii=False)


def code_and_run(camera_code) -> tuple[str, str]:
    """The camera input is "code" or "code#run"; when the run changes the blocks start over.
    In a headless run the camera comes from the environment variable and replaces the input value."""
    camera_code = os.environ.get(ENV_CAMERA) or camera_code
    code, _, run = str(camera_code or "").strip().partition("#")
    return code.strip(), run


def data_dir() -> str:
    with open(os.path.join(ROOT, "engine.json"), encoding="utf-8") as f:
        return json.load(f)["data_dir"]


def camera_settings(code: str) -> tuple[dict, float]:
    path = os.path.join(data_dir(), "cameras", f"{code}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f), os.path.getmtime(path)


def _as_image(image) -> np.ndarray | None:
    if image is None:
        return None
    a = np.asarray(image)
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    elif a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    return np.ascontiguousarray(a) if a.ndim == 3 and a.size else None


def _empty_frame(state: dict, limit: int = 10) -> bool:
    """True (once) if no image came `limit` times in a row: the stream is considered finished. Every open Studio document
    keeps its models in memory (about 0.6-0.9 GB per document), so a block whose stream ended releases its models."""
    state["empty"] = state.get("empty", 0) + 1
    return state["empty"] == limit


class _LiveReader:
    """Reads the live stream in the background and keeps only the latest frame, so no delay builds up while analysis is
    slower than the frame rate. Reconnects every few seconds if the connection drops."""

    def __init__(self, url: str):
        import threading

        self.url, self.frame, self.seq, self.status = url, None, 0, T("waiting: connecting to the camera")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._read, name="isg-live", daemon=True).start()

    def _read(self) -> None:
        wait = 1.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self.status = T("waiting: could not connect to the camera, retrying")
                self._stop.wait(wait)
                wait = min(10.0, wait * 2)
                continue
            wait = 1.0
            while not self._stop.is_set():
                ok, fr = cap.read()
                if not ok:
                    self.status = T("waiting: video lost, reconnecting")
                    break
                with self._lock:
                    self.frame, self.seq = fr, self.seq + 1
            cap.release()

    def latest(self):
        with self._lock:
            return self.frame, self.seq

    def close(self) -> None:
        self._stop.set()


def _stopped() -> bool:
    """When stopping a headless run, the panel first creates this file: the stream becomes "done" and open events close."""
    stop_file = os.environ.get(ENV_STOP_FILE)
    return bool(stop_file) and os.path.exists(stop_file)


def _open_video(state: dict, code: str, run: str) -> str | None:
    """Opens the source. Returns: an error status or None."""
    state["run"] = run
    try:
        camera, _ = camera_settings(code)
    except Exception as e:
        return T("error: could not read camera settings ({camera}): {error}", camera=code, error=e)
    source = camera.get("source") or {}
    if source.get("type") in ("rtsp", "onvif", "http"):
        url = os.environ.get(ENV_CAMERA_URL, "")
        if not url:
            return T("error: no stream address was given for the camera (the İSG Kamera panel starts the scene)")
        state.update(code=code, live=_LiveReader(url), seq=0, n=0, done=False)
        return None
    if source.get("type") != "file":
        return T("error: the ISG Video block cannot read this camera type")
    path = source.get("path", "")
    path = path if os.path.isabs(path) else os.path.join(data_dir(), path)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return T("error: could not open the video: {file}", file=os.path.basename(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    state.update(code=code, cap=cap, fps=fps if 1 <= fps <= 120 else 25.0, n=0, total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), done=False)
    return None


def _close_video(state: dict) -> None:
    if state.get("cap") is not None:
        state["cap"].release()
    if state.get("live") is not None:
        state["live"].close()
    state.clear()


def video(state: dict, camera_code: str):
    """ISG Video block: reads the video file in the camera settings frame by frame. In a headless run it also reads the
    live camera's stream (the address comes from the environment variable).
    Returns: (image | None, time (s; -1 for a live stream: the wall clock is used), done, status). When the file ends or the
    panel stops it, returns done=True once and then gives no images."""
    code, run = code_and_run(camera_code)
    if code == PROBE_CODE:  # test run: a black frame is enough to test the models
        raw_url = os.environ.get(ENV_FACE_FRAME_URL)
        if raw_url:
            if "raw" not in state:
                try:
                    state["raw"] = _local_frame(raw_url)
                except Exception:
                    state["raw"] = None
            if state["raw"] is None:
                return None, 0.0, False, T("error: could not read the frame")
            return state["raw"].copy(), -1.0, False, T("ok connection test frame")
        return np.zeros((360, 640, 3), np.uint8), -1.0, False, T("ok test frame")
    if state.get("code") != code or state.get("run") != run:
        _close_video(state)
        error = _open_video(state, code, run)
        if error:
            return None, 0.0, False, error
    live = state.get("live")
    if state["done"]:
        return None, -1.0 if live else state["n"] / state["fps"], False, T("done: {n} frames", n=state["n"])
    if _stopped():
        state["done"] = True
        if live:
            live.close()
        else:
            state["cap"].release()
        return None, -1.0 if live else state["n"] / state["fps"], True, T("done: stopped, {n} frames", n=state["n"])
    if live:
        frame, seq = live.latest()
        if frame is None or seq == state["seq"]:
            return None, -1.0, False, live.status if frame is None else T("ok: waiting for a new frame")
        state["seq"] = seq
        state["n"] += 1
        return frame, -1.0, False, T("ok frame {n}", n=state["n"])
    ok, fr = state["cap"].read()
    if not ok:
        state["done"] = True
        state["cap"].release()
        return None, state["n"] / state["fps"], True, T("done: {n} frames", n=state["n"])
    t = state["n"] / state["fps"]
    state["n"] += 1
    if state["total"] > 0:
        return fr, t, False, T("ok frame {n} / {total}", n=state["n"], total=state["total"])
    return fr, t, False, T("ok frame {n}", n=state["n"])


def detect(state: dict, image, camera_code: str) -> tuple[str, str]:
    """ISG Detection block. Returns: (detections JSON, status)."""
    fr = _as_image(image)
    if fr is None:
        if _empty_frame(state) and state.pop("detector", None) is not None:
            state.pop("signature", None)  # the stream ended: models are released; they are loaded again if images come back
        return "[]", T("waiting: no image")
    state["empty"] = 0
    if code_and_run(camera_code)[0] == PROBE_CODE:
        return _model_probe(state, fr)
    try:
        camera, mtime = camera_settings(code_and_run(camera_code)[0])
    except Exception as e:
        return (_error_json(T("could not read camera settings ({camera})", camera=camera_code)),
                T("error: could not read camera settings ({camera}): {error}", camera=camera_code, error=e))
    plan = catalog.model_plan(camera)
    signature = (tuple(m["file"] for m in plan), mtime)
    if state.get("signature") != signature:
        try:
            state["detector"] = Detector(os.path.join(ROOT, "models"), plan, data_dir())
        except Exception as e:
            return (_error_json(T("could not load the model: {error}", error=e)),
                    T("error: could not load the model: {kind}: {error}", kind=type(e).__name__, error=e))
        state["signature"] = signature
    t0 = time.perf_counter()
    try:
        detections = state["detector"].run(fr)
    except model_license.LicenseError as e:
        state.pop("detector", None)
        state.pop("signature", None)
        return _error_json(str(e)), T("error: {error}", error=e)
    except Exception as e:
        return (_error_json(T("detection failed: {kind}", kind=type(e).__name__)),
                T("error: detection failed: {kind}: {error}", kind=type(e).__name__, error=e))
    return json.dumps(detections), T("ok {n} detections · {ms:.0f} ms · {models}", n=len(detections), ms=1000 * (time.perf_counter() - t0),
                                     models=state["detector"].summary())


def _model_probe(state: dict, fr) -> tuple[str, str]:
    """"Try Studio": opens the default model plan and runs one frame (for encrypted packages the license check happens here)
    and writes the result once to the ENV_PROBE_RESULT file. No camera settings are needed."""
    if state.get("probe_written"):
        return "[]", state.get("probe_status", T("test finished"))
    result = {"ok": False, "error": ""}
    output = os.environ.get(ENV_FACE_OUTPUT) if os.environ.get(ENV_FACE_FRAME_URL) else None
    try:
        plan = [catalog.MODEL_PLAN[0]] if output else catalog.model_plan({})
        detector = Detector(os.path.join(ROOT, "models"), plan, data_dir())
        detections = detector.run(fr)
        extra = {}
        if output:
            from .recorder import face_regions, mask_faces

            frame = fr.copy()
            mask_faces(frame, detections)
            if not cv2.imwrite(output, frame):
                raise OSError(T("could not write the masked frame"))
            extra["faces"] = len(face_regions(detections))
        result = {"ok": True, "summary": detector.summary(), **extra}
    except model_license.LicenseError as e:
        result["error"] = T("license: {error}", error=e)
    except Exception as e:  # model file missing, package could not be opened, onnxruntime could not be loaded...
        result["error"] = f"{type(e).__name__}: {e}"
    path = os.environ.get(ENV_PROBE_RESULT)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        except OSError:
            pass
    state["probe_written"] = True
    state["probe_status"] = T("ok models loaded") if result["ok"] else T("error: {error}", error=result["error"])
    return "[]", state["probe_status"]


def pose(state: dict, image, detections_json: str) -> tuple[str, str]:
    """ISG Pose block: adds the 17 keypoints to the person detections. Returns: (detections JSON, status)."""
    fr = _as_image(image)
    if fr is None:
        if _empty_frame(state):
            state.pop("model", None)
        return detections_json or "[]", T("waiting: no image")
    state["empty"] = 0
    try:
        detections = json.loads(detections_json) if detections_json else []
    except ValueError:
        return _error_json(T("could not read the detections")), T("error: could not read the detections")
    if not isinstance(detections, list):  # ISG Detection is not working: the error is passed on to ISG Rules as it is
        return detections_json, T("error: no detections (see the Status output of the ISG Detection block)")
    if state.get("model") is None:
        from .pose import PoseEstimator

        try:
            state["model"] = PoseEstimator(os.path.join(ROOT, "models", "isg-iskelet.onnx"), data_dir())
        except model_license.LicenseError as e:
            return _error_json(str(e)), T("error: {error}", error=e)
        except Exception as e:
            return detections_json, T("error: could not load the pose model: {kind}: {error}", kind=type(e).__name__, error=e)
    t0 = time.perf_counter()
    try:
        n = state["model"].run(fr, detections)
    except model_license.LicenseError as e:
        state.pop("model", None)
        return _error_json(str(e)), T("error: {error}", error=e)
    except Exception as e:
        return detections_json, T("error: pose estimation failed: {kind}: {error}", kind=type(e).__name__, error=e)
    people = sum(1 for d in detections if d["s"] == "person")
    return json.dumps(detections), T("ok pose found for {n}/{people} people · {ms:.0f} ms", n=n, people=people, ms=1000 * (time.perf_counter() - t0))


def _setup(state: dict, code: str, camera: dict, mtime: float, W: int, H: int) -> None:
    source = camera.get("source") or {}
    start = dt.datetime.now().replace(microsecond=0)
    if source.get("type") == "file" and source.get("start_time"):
        try:
            start = dt.datetime.fromisoformat(source["start_time"])
        except ValueError:
            pass
    state.update(code=code, mtime=mtime, size=(W, H), n=0, t0=time.monotonic(), last_t=None, last_measurement=0.0, last_drawn=None,
                 engine=RuleEngine(camera, W, H, start, 0.125), recorder=None, settings=camera, face_memory={})


def _finish(state: dict, marker: bool = True) -> None:
    engine, recorder = state.get("engine"), state.get("recorder")
    if engine is None:
        return
    if recorder is not None:
        recorder.finish(engine.close(), marker)
        o = engine.measurement()
        if o is not None:
            recorder.measurement(o, state.get("last_drawn"))
    state["engine"] = None


def rules(state: dict, image, detections_json: str, camera_code: str, time_s=None, done: bool = False):
    """ISG Rules block. `time_s`: time of the image in seconds (for a video file it comes from the ISG Video block;
    if empty the wall clock is used). `done`: the stream ended, close the open events.
    Returns: (marked-up image, alarm, risk score, started events JSON, status, any new event, write the alarm).
    "write the alarm": True when the alarm state changes and every five seconds; it is wired to the Enable input of PLC
    write blocks, so the PLC is written only when needed, not on every frame."""
    if done:  # the stream ended: even if an input image comes and even if the engine is not set up, no raw frame is output
        if state.get("engine") is None:
            return None, False, 0, "[]", T("done: stream closed"), False, False
        _finish(state)
        state["last_alarm"] = False
        return None, False, 0, "[]", T("done: stream closed"), False, True  # the alarm is written once as off
    fr = _as_image(image)
    if fr is None:
        return image, False, 0, "[]", T("waiting: no image"), False, False
    code, run = code_and_run(camera_code)
    if state.get("run") != run:  # new run: close what is left from the previous stream, start over
        _finish(state, marker=False)
        state["run"] = run
    try:
        camera, mtime = camera_settings(code)
    except Exception as e:  # no raw frame is output: faces were not masked
        return np.zeros_like(fr), False, 0, "[]", T("error: could not read camera settings ({camera}): {error}", camera=code, error=e), False, False
    H, W = fr.shape[:2]
    if state.get("engine") is None or state.get("code") != code or state.get("mtime") != mtime or state.get("size") != (W, H):
        _finish(state, marker=False)
        _setup(state, code, camera, mtime, W, H)
    engine = state["engine"]
    try:
        detections = json.loads(detections_json) if detections_json else []
    except ValueError:
        detections = {"error": T("could not read the detections")}
    if not isinstance(detections, list):
        # Detection is not working (model file missing, could not be loaded, failed). This does not count as "nobody there":
        # with the people unknown the faces cannot be masked, so the frame is neither recorded nor output; the panel stops the job with an error.
        reason = str(detections.get("error") or T("reason unknown")) if isinstance(detections, dict) else T("could not read the detections")
        if time.monotonic() - state.get("error_logged", 0.0) >= 5.0:
            state["error_logged"] = time.monotonic()
            try:
                enqueue(data_dir(), code, {"type": "error", "camera": code, "run": run, "message": reason})
            except OSError:
                pass
        return np.zeros_like(fr), False, 0, "[]", T("error: detection is not working, images are not being recorded: {reason}", reason=reason), False, False
    try:
        t = float(time_s) if time_s is not None and str(time_s) != "" else None
    except (TypeError, ValueError):
        t = None
    if t is None or t < 0:  # live stream (ISG Video gives -1) or the time input is not connected: wall clock
        t = time.monotonic() - state["t0"]
    if state["last_t"] is not None and t > state["last_t"]:  # smoothed average of the real frame interval
        interval = min(1.0, max(0.02, t - state["last_t"]))
        engine.interval = interval if state["n"] == 1 else 0.9 * engine.interval + 0.1 * interval
    state["last_t"] = t
    state["n"] += 1
    if state["recorder"] is None:  # clip frame rate from the interval of the first two frames
        if state["n"] < 2:  # the first frame passes unmarked: its raw form is not output
            return np.zeros_like(fr), False, 0, "[]", T("ok first frame"), False, False
        state["recorder"] = Recorder(data_dir(), code, camera.get("name", code), W, H, 1 / engine.interval)
        state["recorder"].run = run
    recorder = state["recorder"]
    if time.monotonic() - state.get("heartbeat", 0.0) >= 5.0:  # the panel tells from this record that the scene is running
        state["heartbeat"] = time.monotonic()
        recorder._enqueue({"type": "heartbeat", "camera": code, "frames": state["n"]})
    result = engine.step(t, detections, fr)
    now = (engine.start_time + dt.timedelta(seconds=t)).strftime("%d.%m.%Y %H:%M:%S" if language() == "tr" else "%Y-%m-%d %H:%M:%S")
    open_events = result["open"]
    top = " · ".join(dict.fromkeys(v.get("label", "") for v in result["highlights"])) if result["highlights"] else ""
    bottom = T("{camera} · {time} · faces masked", camera=sample_name(camera.get("name", code)), time=now)
    drawn = draw(fr.copy(), result, detections, engine.zones, top, bottom, state.setdefault("face_memory", {}), t)
    recorder.frame(t, drawn, result)
    state["last_drawn"] = drawn
    if t - state["last_measurement"] >= MEASUREMENT_INTERVAL:
        state["last_measurement"] = t
        o = engine.measurement()
        if o is not None:
            recorder.measurement(o, drawn)
    # The alarm is on while an event with severity at or above the threshold is going on (from the moment it starts); the risk score grows with time.
    threshold = int(camera.get("alarm_min_severity", 4))
    scores = [catalog.risk(catalog.RULE[o["rule"]]["severity"], catalog.likelihood(t - o["start"], o["seq"])) for o in open_events]
    risk = max(scores, default=0)
    alarm = any(catalog.RULE[o["rule"]]["severity"] >= threshold for o in open_events)
    status = T("ok {people} people, {vehicles} vehicles, {events} open events · {time}", people=len(result["people"]),
               vehicles=len(result["vehicles"]), events=len(open_events), time=now)
    mono = time.monotonic()
    write = bool(alarm) != bool(state.get("last_alarm")) or mono - state.get("last_write", 0.0) >= 5.0
    if write:
        state["last_alarm"], state["last_write"] = bool(alarm), mono
    return drawn, bool(alarm), int(risk), json.dumps(result["started"], ensure_ascii=False), status, bool(result["started"]), write


def close(state: dict) -> None:
    """May be called when the scene stops: drops the open events and the counts into the queue."""
    _finish(state)
