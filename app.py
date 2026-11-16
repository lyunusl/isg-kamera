"""İSG Kamera: on double-click it sets up the data folder, starts the local server and opens it in the browser.

By default the server listens only on 127.0.0.1. If network access is turned on in Settings it listens on all network
interfaces; requests from the network can only read the /api/v2/... endpoints (and the frozen v0.1.0 contract under
/api/v1/..., see legacy.py), with the API key (event clips and snapshots included).

The screens are a single page (web/index.html); data comes as JSON from the /api/... endpoints.
"""
from __future__ import annotations

import argparse
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

import i18n
import legacy
import migration
import paths
from i18n import N_, T

DEFAULT_PORT = 8780
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8", ".jpg": "image/jpeg",
    ".mp4": "video/mp4", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png", ".ico": "image/x-icon", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

# ---------------------------------------------------------------- background jobs

# Language of the panel: settings.json "language"; undecided on a fresh installation until the browser chooses one.
LANG_STATE = {"undecided": False}


def _is_progress_line(s: str) -> bool:
    """Is it a progress line of the engine ("  detection: ...", in either language)?"""
    return s.startswith((T("  detection:", lang="tr"), T("  detection:", lang="en")))


class Job:
    """A job running in the background (sample analysis, connection test, live monitoring) and what it prints."""

    counter = 0

    def __init__(self, kind: str, title: str):
        Job.counter += 1
        self.id = Job.counter
        self.kind, self.title = kind, title
        self.lines: list[str] = []
        self._partial = ""
        self.status = "running"
        self.result: dict = {}
        self.started, self.ended = time.time(), None
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        with self.lock:
            parts = (self._partial + text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
            self._partial = parts.pop()
            for s in parts:
                s = s.rstrip()
                if not s:
                    continue
                if self.lines and _is_progress_line(s) and _is_progress_line(self.lines[-1]):
                    self.lines[-1] = s  # progress lines overwrite each other
                else:
                    self.lines.append(s)
            del self.lines[:-400]

    def summary(self) -> dict:
        with self.lock:
            lines = self.lines + ([self._partial] if self._partial.strip() else [])
        return {"id": self.id, "type": self.kind, "title": self.title, "status": self.status, "lines": lines[-80:],
                "result": self.result, "elapsed_s": round((self.ended or time.time()) - self.started, 1),
                "stopping": self.stop.is_set()}


class OutputRedirector(io.TextIOBase):
    """Also writes print() output to the log of the job whose thread is printing."""

    def __init__(self, original):
        self.original = original
        self.local = threading.local()

    @property
    def encoding(self):
        return "utf-8"

    def write(self, s):
        job = getattr(self.local, "job", None)
        if job is not None:
            job.append(s)
        if self.original is not None:
            try:
                self.original.write(s)
            except UnicodeEncodeError:
                enc = getattr(self.original, "encoding", None) or "ascii"
                self.original.write(s.encode(enc, "replace").decode(enc))
            except Exception:
                pass
        return len(s)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass

    def isatty(self):
        return False


LISTENING = {"address": "127.0.0.1", "port": DEFAULT_PORT, "network": []}  # address the server listens on (filled at start)
REDIRECT_OUT = OutputRedirector(sys.stdout)
REDIRECT_ERR = OutputRedirector(sys.stderr)
ACTIVE_JOB: Job | None = None
JOB_LOCK = threading.Lock()


def start_job(kind: str, title: str, fn, *args) -> tuple[bool, str]:
    global ACTIVE_JOB
    with JOB_LOCK:
        if ACTIVE_JOB is not None and ACTIVE_JOB.status == "running":
            return False, T("\"{title}\" is running right now. Wait for it to finish or stop it.", title=ACTIVE_JOB.title)
        job = Job(kind, title)
        ACTIVE_JOB = job

    def work():
        REDIRECT_OUT.local.job = REDIRECT_ERR.local.job = job
        try:
            result = fn(job, *args)
            job.result = result or {}
            job.status = "error" if job.result.get("error") else ("stopped" if job.stop.is_set() else "done")
        except SystemExit as e:
            if e.code not in (None, 0):
                if not isinstance(e.code, int):
                    print(str(e.code), flush=True)
                job.status = "error"
            else:
                job.status = "done"
        except Exception as e:  # unexpected error: details to the console, a short message to the screen
            traceback.print_exc(file=sys.__stderr__)
            print(T("Error: {error}", error=e), flush=True)
            job.status = "error"
        finally:
            REDIRECT_OUT.local.job = REDIRECT_ERR.local.job = None
            job.ended = time.time()

    threading.Thread(target=work, name=f"job-{job.id}", daemon=True).start()
    return True, ""


def camera_path(code: str) -> Path:
    return paths.CAMERAS / f"{code}.json"


class CameraConfigError(Exception):
    """The camera settings could not be saved; the message is shown to the user."""


_CAMERA_LOCK = threading.Lock()  # every operation that writes camera files: two saves arriving together cannot interleave


def _write_camera_json(file, config: dict) -> None:
    file.write(json.dumps(config, ensure_ascii=False, indent=1))


def _delete_camera_file(code: str) -> None:
    camera_path(code).unlink(missing_ok=True)


def _prepare_camera(config) -> tuple[str, dict, list[str]]:
    """Checks incoming camera settings: (short code, settings with passwords removed, notes)."""
    if not isinstance(config, dict):
        raise CameraConfigError(T("Could not read the settings."))
    code = str(config.get("code", ""))
    if not CODE_RE.match(code):
        raise CameraConfigError(T("The short code may contain only lowercase letters (a-z), digits, - and _ (e.g. depo2)."))
    if code in paths.sample_codes():
        raise CameraConfigError(T("\"{code}\" belongs to the sample cameras. Enter another short code (e.g. {code}2).", code=code))
    config = json.loads(json.dumps(config))  # copy: the caller's data must not change
    config["code"] = code
    return code, config, strip_passwords(config)


def create_cameras(configs) -> dict:
    """Creates new cameras in one operation: either all are written or none (adding a recorder's channels at once).

    An existing camera is never overwritten. Checking and writing happen under one lock and every file is opened with
    "create if missing": of two simultaneous requests only one succeeds. If writing fails, the files this call created
    are deleted; any that cannot be deleted are named in the message."""
    if not isinstance(configs, list) or not 1 <= len(configs) <= 64:
        raise CameraConfigError(T("You can add between 1 and 64 cameras at a time."))
    prepared = [_prepare_camera(c) for c in configs]
    codes = [code for code, _, _ in prepared]
    repeated = sorted({c for c in codes if codes.count(c) > 1})
    if repeated:
        raise CameraConfigError(T("The same short code appears more than once: {codes}. None were added.", codes=", ".join(repeated)))
    with _CAMERA_LOCK:
        paths.CAMERAS.mkdir(parents=True, exist_ok=True)
        existing = [c for c in codes if camera_path(c).exists()]
        if existing:
            raise CameraConfigError(T("Cameras with these codes already exist: {codes}. They were not overwritten; none were added.",
                                      codes=", ".join(existing)))
        created: list[str] = []
        try:
            for code, config, _ in prepared:
                file = open(camera_path(code), "x", encoding="utf-8")  # "x": fails if the file exists, never overwrites it
                created.append(code)
                with file:
                    _write_camera_json(file, config)
        except OSError as e:
            left = []
            for code in created:  # only what this call created is rolled back
                try:
                    _delete_camera_file(code)
                except OSError:
                    left.append(code)
            reason = (T("a camera with the same code was created a moment ago") if isinstance(e, FileExistsError)
                      else T("the file could not be written ({error})", error=type(e).__name__))
            rest = (T("None were added.") if not left else
                    T("Some records could not be rolled back: {codes}. Delete them in Settings.", codes=", ".join(left)))
            raise CameraConfigError(T("Could not add the cameras: {reason}. {rest}", reason=reason, rest=rest)) from None
    return {"codes": codes, "notes": sorted({n for _, _, notes in prepared for n in notes})}


def channel_cameras(base, channels, profiles=None) -> list[dict]:
    """Builds camera settings for the channels of a recorder: `base` is the settings from the form (code, name, connection, rules).

    Each channel becomes a separate camera with the code `<code>-ch<n>`; zones start empty, rules are copied. If `profiles`
    is given (ONVIF: the profile list read from the device) the channel's profile and video source tokens are written
    too: the camera stays bound to the same physical channel even if the device later reports its profiles in another
    order. A channel without a profile in the list is rejected."""
    if not isinstance(base, dict) or not isinstance(base.get("source"), dict):
        raise CameraConfigError(T("Could not read the settings."))
    code = str(base.get("code", ""))
    if not CODE_RE.match(code):
        raise CameraConfigError(T("Enter the short code first (e.g. nvr1): channels are added as nvr1-ch1, nvr1-ch2…"))
    if not base["source"].get("ip"):
        raise CameraConfigError(T("Enter the recorder's IP address first."))
    try:
        channels = [int(n) for n in channels]
    except (TypeError, ValueError):
        raise CameraConfigError(T("Could not read the channel list.")) from None
    if not 1 <= len(channels) <= 64 or len(set(channels)) != len(channels) or min(channels) < 1:
        raise CameraConfigError(T("The number of channels must be between 1 and 64, and each channel may appear only once."))
    if base["source"].get("type") == "onvif" and not (isinstance(profiles, list) and profiles):
        # with ONVIF a channel is saved with its persistent profile and source tokens; without the list no token-less settings are written
        raise CameraConfigError(T("List the device's channels and profiles before adding ONVIF channels; none were added."))
    if base["source"].get("type") != "onvif":
        profiles = None  # brand-based RTSP: the channel is part of the address, there are no tokens
    out = []
    for n in channels:
        source = {a: v for a, v in base["source"].items() if a not in ("profile", "profile_name", "video_source")}
        source["channel"] = n
        if profiles is not None:
            candidates = sorted((p for p in profiles if isinstance(p, dict) and p.get("channel") == n and p.get("token") and p.get("source")),
                                key=lambda p: int(p.get("width") or 0) * int(p.get("height") or 0))
            if not candidates:
                raise CameraConfigError(T("Channel {n} is not among the profiles the device reported; none were added. List the channels again.", n=n))
            p = candidates[-1] if source.get("stream") == "main" else candidates[0]
            source.update(profile=str(p["token"]), video_source=str(p["source"]),
                          profile_name=T("Channel {n} · {name} · {codec} {width}×{height}", n=n, name=p.get("name") or p["token"],
                                         codec=p.get("encoding", ""), width=p.get("width", 0), height=p.get("height", 0)))
        out.append({**base, "code": f"{code}-ch{n}", "name": T("{name} · Channel {n}", name=base.get("name") or code, n=n),
                    "source": source, "zones": []})
    return out


def read_camera(code: str) -> dict:
    path = camera_path(code)
    if not path.exists():
        raise SystemExit(T("Settings not found: {code}", code=code))
    return json.loads(path.read_text(encoding="utf-8"))


def _camera_title(config: dict, code: str) -> str:
    """Name of a camera for job titles and logs (sample names in the current language)."""
    return paths.sample_name(config.get("name", code))


def _notify_outputs(event_ids: list[int]) -> None:
    """Sends newly started events to the configured outputs."""
    import outputs

    for line in outputs.dispatch(event_ids):
        print(line, flush=True)


def _run_async(coroutine):
    import asyncio

    return asyncio.run(coroutine)


def job_studio_test(job: Job) -> dict:
    """Checks the Studio license once: Studio runs briefly in the background and opens the İSG models (the license of
    an encrypted package is verified here). The result is stored; it is not repeated on later starts."""
    import studio

    print(T("Checking the license…"), flush=True)
    try:
        studio.probe()
    except studio.StudioError as e:
        raise SystemExit(str(e)) from None
    finally:
        _STUDIO_STATE["at"] = 0.0
    print(T("Ready: license verified."), flush=True)
    return {}


def _detection_error(reason: str | None) -> str:
    return T("Detection in AugeLab Studio is not working ({reason}). No footage was saved because no people could be found; "
             "check the model files and your AugeLab Studio license.", reason=reason)


QUEUE_NOT_DRAINED = N_("The last records from Studio could not be processed (the queue did not drain). Open events and partial "
                       "clips were left as they are; check the queue folder in the data folder.")


def _stop_scene(code: str, stop, wait_s: float) -> str | None:
    """Stops the scene (`stop()`: function returning the stop coroutine) and waits for the camera's queue to drain.

    Returns None if all went well; the reason if stopping could not be confirmed or the queue did not drain in time.
    If a reason is returned the caller does not close open events, does not delete partial clips and ends the job with
    an error: the scene may still be leaving records, or there are unprocessed records and unconverted clips."""
    import event_queue

    try:
        _run_async(stop())
    except Exception as e:
        return T("Could not confirm that the AugeLab Studio process stopped ({error}). Open events and partial clips were "
                 "left as they are. Close the program and open it again.", error=f"{type(e).__name__}: {e}")
    time.sleep(wait_s)  # let the scene's last records reach the queue
    if not event_queue.wait_drained(code):  # every record of this run must be taken in and its clips converted
        return T(QUEUE_NOT_DRAINED)
    return None


def analyze_in_studio(job: Job, code: str, max_s: float = 1800.0) -> None:
    """Runs the camera's video file through the scene in AugeLab Studio from start to end; events reach the panel via the queue."""
    import event_queue
    import studio

    config = read_camera(code)
    print(T("  Preparing AugeLab Studio (headless; no window opens)"), flush=True)
    info = _run_async(studio.prepare_scene(code, config))
    event_queue.FINISHED.pop(code, None)
    event_queue.ERRORS.pop(code, None)

    async def start():
        run = await studio.new_run(info, code)
        await studio.run(info, int(max_s * 1000))
        return run

    run = _run_async(start())  # queue records carry this number: a record left from an earlier run does not count for this job
    error: BaseException | None = None
    print(T("  scene running headless in AugeLab Studio (the first run takes a while to load Studio and the models)"), flush=True)
    started = time.time()
    try:
        while True:
            if event_queue.run_error(code, run):  # even if it arrives together with the "done" record, the error wins
                raise SystemExit(_detection_error(event_queue.run_error(code, run)))
            if event_queue.run_finished(code, run):
                break
            if studio.run_problem(info):
                raise SystemExit(studio.run_problem(info))
            if job.stop.is_set():
                print(T("  stopped"), flush=True)
                break
            if time.time() - started > max_s:
                raise SystemExit(T("The analysis in AugeLab Studio did not finish in the expected time."))
            last = event_queue.LAST_RECORD.get(code, 0.0)
            # wait longer before the first record: if the engine was updated, Studio reloads the blocks and models
            if time.time() - max(started, last) > (90 if last > started else 240):
                raise SystemExit(T("No records have come from AugeLab Studio for a long time. Check with the “Test Studio” step "
                                   "in the setup wizard."))
            time.sleep(0.5)
    except BaseException as e:  # job error: the scene is still closed, the error is re-raised below
        error = e
    # Cleanup (closing open events, deleting partial clips) happens only if the scene was confirmed stopped and every
    # queue record of this run was taken in and its clips converted; otherwise nothing is deleted and the job ends with an error.
    problem = _stop_scene(code, lambda: studio.stop(info), 1.2)
    if problem is not None:
        print(T("  warning: {message}", message=problem), flush=True)
    elif not event_queue.run_finished(code, run) or event_queue.run_error(code, run):  # error or stop: running events could not leave a closing record
        _close_open_events(code)
    if error is not None:
        raise error
    if event_queue.run_error(code, run):  # this run's error record may have been processed after "done", with the last records
        raise SystemExit(_detection_error(event_queue.run_error(code, run)))
    if problem is not None:
        raise SystemExit(problem)


def _close_open_events(code: str) -> None:
    """When the scene is stopped, running events cannot leave a closing record: they must not stay "ongoing" in the
    panel, and partial clips are deleted."""
    import db

    db.execute("UPDATE event SET ongoing = 0 WHERE camera = ? AND ongoing = 1", code)
    if (paths.OUTPUT / code).is_dir():
        for pattern in ("*.raw.mp4", "*.ham.mp4"):  # *.ham.mp4: raw clips of the v0.1.0 engine
            for f in (paths.OUTPUT / code).glob(pattern):
                try:
                    f.unlink()
                except OSError:
                    pass


def _clear_sample(code: str) -> None:
    import db

    db.delete_camera(code, samples_only=True)
    for f in (paths.OUTPUT / code).glob("*") if (paths.OUTPUT / code).is_dir() else []:
        f.unlink(missing_ok=True)


def job_sample(job: Job) -> dict:
    import event_queue

    codes = paths.sample_codes()
    if not codes:
        raise SystemExit(T("This version has no sample videos. Add a camera or a video file."))
    for n, code in enumerate(codes, 1):
        if job.stop.is_set():
            print(T("Stopped."), flush=True)
            return {}
        config = read_camera(code)
        print(T("[{n}/{total}] Analyzing {name} in AugeLab Studio…", n=n, total=len(codes), name=_camera_title(config, code)), flush=True)
        t0 = time.time()
        _clear_sample(code)
        event_queue.LAST_RECORD.pop(code, None)
        analyze_in_studio(job, code)
        print(T("[{n}/{total}] done ({secs:.0f} s).", n=n, total=len(codes), secs=time.time() - t0), flush=True)
    return {"goto": "#/"}


def job_test(job: Job, code: str, password: str | None) -> dict:
    import camera

    config = read_camera(code)
    if camera.test(config, camera_path(code), password) != 0:
        return {"error": True}
    return {"frame": f"/cameras/_frame_{camera.code(config, camera_path(code))}.jpg?t={int(time.time())}", "code": code}


def job_monitor(job: Job, code: str, password: str | None, minutes: float) -> dict:
    """Monitors a live camera through the scene in AugeLab Studio until stopped; events reach the panel as soon as they start."""
    import camera
    import event_queue
    import studio

    config = read_camera(code)
    if (config.get("source") or {}).get("type") not in ("rtsp", "onvif", "http"):
        raise SystemExit(T("Live monitoring is for IP cameras, recorders and HTTP streams. For a video file, use Analyze video."))
    url, shown = camera.source(config, password)
    print(f"{_camera_title(config, code)}: {shown}", flush=True)
    print(T("  Preparing AugeLab Studio (headless; no window opens)"), flush=True)
    info = _run_async(studio.prepare_scene(code, config))
    event_queue.LAST_RECORD.pop(code, None)
    event_queue.ERRORS.pop(code, None)

    async def start():
        await studio.set_camera_url(info, str(url))
        run = await studio.new_run(info, code)
        await studio.run(info)
        return run

    async def finish():
        try:
            await studio.stop(info)
        finally:
            await studio.set_camera_url(info, "")  # the address may contain a password: not left in memory

    run = _run_async(start())
    print(T("  scene running headless in AugeLab Studio; monitoring until stopped"), flush=True)
    started, warned = time.time(), False
    error: BaseException | None = None
    try:
        while not job.stop.is_set():
            if event_queue.run_error(code, run):
                raise SystemExit(_detection_error(event_queue.run_error(code, run)))
            if studio.run_problem(info):
                raise SystemExit(studio.run_problem(info))
            last = event_queue.LAST_RECORD.get(code, 0.0)
            if not warned and time.time() - max(started, last) > 45:
                warned = True
                print(T("  No records from Studio for 45 seconds: the camera may not be sending video (address, user, "
                        "password, network)."), flush=True)
            elif warned and time.time() - last < 10:
                warned = False
                print(T("  records are coming in again"), flush=True)
            time.sleep(0.5)
        print(T("  stopping"), flush=True)
    except BaseException as e:  # job error: the scene is still closed, the error is re-raised below
        error = e
    # Same rule as for a video file: if stopping cannot be confirmed or the queue does not drain, cleanup is skipped and the job ends with an error.
    problem = _stop_scene(code, finish, 1.5)
    if problem is None:
        _close_open_events(code)  # monitoring was stopped: running events could not leave a closing record
    else:
        print(T("  warning: {message}", message=problem), flush=True)
    if error is not None:
        raise error
    if event_queue.run_error(code, run):  # the error record may have been processed with the last records
        raise SystemExit(_detection_error(event_queue.run_error(code, run)))
    if problem is not None:
        raise SystemExit(problem)
    return {"goto": "#/"}


def job_video(job: Job, code: str) -> dict:
    """Analyzes a camera of the video-file type (e.g. footage exported from a recorder) in AugeLab Studio from start to end."""
    import event_queue

    config = read_camera(code)
    if (config.get("source") or {}).get("type") != "file":
        raise SystemExit(T("This camera is not a video file."))
    print(T("Analyzing {name} in AugeLab Studio…", name=_camera_title(config, code)), flush=True)
    event_queue.LAST_RECORD.pop(code, None)
    analyze_in_studio(job, code)
    return {"goto": "#/"}


# ---------------------------------------------------------------- settings


def strip_passwords(config: dict) -> list[str]:
    """Keeps passwords out of the camera file: deletes password fields and replaces a password in the address with {password}."""
    notes = []
    s = config.get("source")
    if isinstance(s, dict):
        for field in list(s):
            if field not in ("password_env", "sifre_degiskeni") and re.search(r"sifre|şifre|password|pass$|parola", field, re.I):
                del s[field]
                notes.append(T("The password field was not saved."))
        url = s.get("url")
        if isinstance(url, str):
            m = re.search(r"://([^:/@]+):([^@]*)@", url)
            if m and m.group(2) not in ("{password}", ""):
                s["url"] = url[:m.start(2)] + "{password}" + url[m.end(2):]
                notes.append(T("The password in the address was not saved; {password} was written in its place. Enter the "
                               "password in the password box when running."))
    config.pop("password", None)
    config.pop("sifre", None)
    return notes


def model_lock() -> dict:
    """Local activation status of the model package."""
    import rules

    return rules.model_lock(str(paths.DATA), str(paths.ENGINE / "models"))


def status() -> dict:
    import camera
    import db
    import report
    import rules
    import studio_setup

    cameras = report.cameras()
    for k in cameras:
        try:
            k["needs_password"] = camera.needs_password(read_camera(k["code"]))
        except SystemExit:
            k["needs_password"] = False
    return {
        "app": paths.APP_NAME, "version": paths.VERSION, "data_dir": str(paths.DATA),
        "models": {"object": rules.vault.model_exists(str(paths.MODEL_OBJECT)), "addon": rules.vault.model_exists(str(paths.MODEL_ADDON))},
        "model_lock": model_lock(),
        "has_samples": bool(paths.sample_codes()), "cameras": cameras, "settings": db.settings(), "listening": LISTENING,
        "job": ACTIVE_JOB.summary() if ACTIVE_JOB else None, "studio": studio_status(),
        "studio_installed": studio_setup.installed_studio() is not None,
        "studio_setup": studio_setup.status(), "account_url": studio_setup.ACCOUNT_URL,
    }


_STUDIO_STATE = {"at": 0.0, "status": None, "polling": False}
_STUDIO_LOCK = threading.Lock()


def _poll_studio() -> None:
    try:
        import studio

        d = _run_async(studio.status())
        _STUDIO_STATE["status"] = {"connected": d.get("ready") is True, "installed": True, **d}
    except Exception as e:
        _STUDIO_STATE["status"] = {"connected": False, "installed": False, "message": str(e)[:200]}
    finally:
        _STUDIO_STATE["at"] = time.time()
        _STUDIO_STATE["polling"] = False


def studio_status() -> dict:
    """Is AugeLab Studio ready to run headless (installed and the test passed)? Polling happens in the background and
    does not run Studio; this function never waits and returns the last known status."""
    with _STUDIO_LOCK:
        if not _STUDIO_STATE["polling"] and time.time() - _STUDIO_STATE["at"] > 5:
            _STUDIO_STATE["polling"] = True
            threading.Thread(target=_poll_studio, daemon=True, name="studio-poll").start()
    return _STUDIO_STATE["status"] or {"connected": False, "polling": True, "message": T("Checking AugeLab Studio…")}


def _tr(v):
    """Translates a human-readable text of a data table at output; other values pass through unchanged."""
    return T(v) if isinstance(v, str) else v


def catalog() -> dict:
    import camera
    import colors
    import outputs
    import rules

    missing = rules.missing_models(str(paths.ENGINE / "models"))  # rules whose model file is missing are flagged in the panel
    rule_list = []
    for k in rules.RULES:
        k = {**k, "name": _tr(k.get("name")), "description": _tr(k.get("description"))}
        if k["id"] in missing:
            k["missing_model"] = missing[k["id"]]
        rule_list.append(k)
    return {
        "rules": rule_list,
        "categories": {k: _tr(v) for k, v in rules.CATEGORIES.items()},
        "zone_types": {k: _tr(v) for k, v in rules.ZONE_TYPES.items()},
        "brands": {k: _tr(v["name"]) for k, v in camera.BRANDS.items()},
        "colors": {k: {"name": _tr(v["name"]), "hex": v["hex"], "weak": bool(v.get("weak"))} for k, v in colors.PRESETS.items()},
        "output_types": {k: _tr(v) for k, v in outputs.TYPES.items()}, "api": api_endpoints(),
    }


# ---------------------------------------------------------------- API for external systems (read-only)

# /api/v2: English names. /api/v1 (v0.1.0 contract, frozen) is answered by legacy.api_v1.
API_ENDPOINTS = {
    "/api/v2/summary": N_("Safety score, PPE compliance, event counts, daily series (days | from, to)"),
    "/api/v2/events": N_("Event list (days | from, to, camera, category, rule, status, level, shift, serious=1, limit)"),
    "/api/v2/events.csv": N_("The same list as CSV (Excel, Power BI)"),
    "/api/v2/event/<id>": N_("A single event"),
    "/api/v2/report": N_("Breakdowns by rule / camera / shift / zone, and the trend (days | from, to)"),
    "/api/v2/analytics": N_("A camera's heat map, paths and counts (camera, days | from, to)"),
    "/api/v2/cameras": N_("Cameras and their latest status"),
    "/api/v2/tasks": N_("Tasks and their verification status"),
    "/api/v2/media/<path>": N_("An event's clip or snapshot (the event's clip / snapshot field; faces of detected people are masked)"),
}


def api_endpoints() -> dict:
    """API_ENDPOINTS with the descriptions in the current language."""
    return {k: T(v) for k, v in API_ENDPOINTS.items()}


CSV_FIELDS = ("id", "time", "camera", "camera_name", "rule", "rule_name", "category", "zone", "shift", "severity", "likelihood", "score",
              "level", "serious", "duration_s", "people", "status", "assignee", "notes", "clip", "snapshot")
CSV_FILENAMES = {"/api/v1": legacy.V1_CSV_FILENAME, "/api/v2": "events.csv"}


def _v2_query(q: dict) -> dict:
    """Old rule and category ids in a v2 filter still work (catalog ids of v0.1.0)."""
    q = dict(q)
    if q.get("rule"):
        q["rule"] = legacy.RULE_IDS.get(q["rule"], q["rule"])
    if q.get("category"):
        q["category"] = legacy.CATEGORY_IDS.get(q["category"], q["category"])
    return q


def api_v1(path: str, q: dict):
    """v0.1.0 contract. Returns ("json", data) | ("csv", text) | ("file", Path) | None."""
    return legacy.api_v1(path, q)


def api_v2(path: str, q: dict):
    """Returns ("json", data) | ("csv", text) | ("file", Path) | None."""
    import db
    import report

    endpoint = path[len("/api/v2"):].rstrip("/")
    if endpoint == "":
        return "json", {"app": paths.APP_NAME, "version": paths.VERSION, "endpoints": api_endpoints()}
    q = _v2_query(q)
    if endpoint == "/summary":
        return "json", report.summary(q)
    if endpoint in ("/events", "/events.csv"):
        d = report.events({"limit": 1000, **q})
        if endpoint == "/events":
            return "json", d
        return "csv", legacy.events_csv(d["events"], CSV_FIELDS)
    if endpoint.startswith("/event/"):
        try:
            rows = db.query("SELECT * FROM event WHERE id = ?", int(endpoint[len("/event/"):]))
        except ValueError:
            rows = []
        return ("json", report.localize_event(rows[0])) if rows else None
    if endpoint == "/report":
        return "json", report.report(q)
    if endpoint == "/analytics":
        return "json", report.analytics(q)
    if endpoint == "/cameras":
        return "json", {"cameras": report.cameras()}
    if endpoint == "/tasks":
        return "json", {"tasks": report.tasks()}
    if endpoint.startswith("/media/"):
        target = (paths.OUTPUT / endpoint[len("/media/"):]).resolve()
        if paths.OUTPUT.resolve() in target.parents and target.suffix.lower() in (".mp4", ".jpg"):
            return "file", target
    return None


def external_api(path: str, q: dict):
    """/api/v1/... (v0.1.0) or /api/v2/...; None for any other path."""
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return api_v1(path, q)
    if path == "/api/v2" or path.startswith("/api/v2/"):
        return api_v2(path, q)
    return None


# ---------------------------------------------------------------- HTTP


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows, do not open the same port a second time
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _is_external(path: str) -> bool:
    return path in ("/api/v1", "/api/v2") or path.startswith(("/api/v1/", "/api/v2/"))


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "IsgKamera"
    sys_version = ""

    def log_message(self, *a):  # do not fill the console with request lines
        pass

    # --- helpers
    def _is_local(self) -> bool:
        """The interface and all operations: only from this computer."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin is not None and origin not in {f"http://{h}" for h in allowed}:
            return False
        return True

    def _has_api_key(self) -> bool:
        """Another system on the network: only if network access is on and it sends the correct API key."""
        import db

        g = db.settings()
        key = g.get("api_key") or ""
        if not g.get("network_access") or len(key) < 20:
            return False
        sent = self.headers.get("X-API-Key") or ""
        auth = self.headers.get("Authorization") or ""
        if not sent and auth.lower().startswith("bearer "):
            sent = auth[7:].strip()
        return hmac.compare_digest(sent.encode("utf-8"), key.encode("utf-8"))

    def _external(self, path: str, q: dict):
        r = external_api(path, q)
        v1 = path == "/api/v1" or path.startswith("/api/v1/")
        if r is None:
            return self._json({"hata": T("Not found.")} if v1 else {"error": T("Not found.")}, 404)
        if r[0] == "json":
            return self._json(r[1])
        if r[0] == "csv":
            name = CSV_FILENAMES["/api/v1" if v1 else "/api/v2"]
            return self._send(200, ("\ufeff" + r[1]).encode("utf-8"), "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self._file(r[1])

    def _send(self, code: int, body: bytes, kind: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), MIME_TYPES[".json"])

    def _not_found(self):
        self._send(404, T("Not found").encode("utf-8"), "text/plain; charset=utf-8")

    def _file(self, path: Path):
        if not path.is_file():
            return self._not_found()
        kind = MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        requested = self.headers.get("Range")
        first, last = 0, size - 1
        code = 200
        m = re.match(r"bytes=(\d*)-(\d*)$", requested or "")
        if m and size > 0 and (m.group(1) or m.group(2)):
            if m.group(1):
                first = int(m.group(1))
                last = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                first = max(0, size - int(m.group(2)))
            if first > last or first >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(last - first + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if code == 206:
            self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        if path.suffix.lower() == ".xlsx":
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(first)
                left = last - first + 1
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass

    def _under(self, root: Path, relative: str):
        target = (root / relative).resolve()
        if root.resolve() not in target.parents:
            return self._not_found()
        return self._file(target)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ValueError("request too large")
        data = self.rfile.read(n) if n else b"{}"
        return json.loads(data.decode("utf-8") or "{}")

    # --- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = dict(parse_qsl(parsed.query))
        if not self._is_local():
            if _is_external(path) and self._has_api_key():
                try:
                    return self._external(path, q)
                except Exception:
                    traceback.print_exc(file=sys.__stderr__)
                    v1 = path.startswith("/api/v1")
                    return self._json({"hata" if v1 else "error": T("Unexpected error.")}, 500)
            return self._send(403, b"Forbidden", "text/plain")
        try:
            return self._get(path, q)
        except SystemExit as e:
            return self._json({"error": str(e.code)}, 400)
        except Exception as e:
            traceback.print_exc(file=sys.__stderr__)
            return self._json({"error": T("Unexpected error: {error}", error=e)}, 500)

    def _get(self, path: str, q: dict):
        import db
        import report

        if path in ("/", "/index.html"):
            return self._file(paths.WEB / "index.html")
        if path.startswith("/web/"):
            return self._under(paths.WEB, path[len("/web/"):])
        if path == "/favicon.ico":
            return self._file(paths.WEB / "favicon.ico")
        if _is_external(path):
            return self._external(path, q)
        if path == "/api/status":
            return self._json(status())
        if path == "/api/i18n":  # lang: None while undecided (a fresh installation: the browser chooses)
            return self._json({"lang": None if LANG_STATE["undecided"] else i18n.language(), "catalog": i18n.catalog()})
        if path == "/api/catalog":
            return self._json(catalog())
        if path == "/api/summary":
            return self._json(report.summary(q))
        if path == "/api/events":
            return self._json(report.events(q))
        if path == "/api/analytics":
            return self._json(report.analytics(q))
        if path == "/api/report":
            return self._json(report.report(q))
        if path == "/api/tasks":
            return self._json({"tasks": report.tasks()})
        if path == "/api/report.xlsx":
            return self._file(report.excel())
        if path.startswith("/api/event/"):
            try:
                rows = db.query("SELECT * FROM event WHERE id = ?", int(path[len("/api/event/"):]))
            except ValueError:
                rows = []
            if not rows:
                return self._json({"error": T("Event not found.")}, 404)
            return self._json(report.localize_event(rows[0]))
        if path.startswith("/api/camera/"):
            code = path[len("/api/camera/"):]
            if not CODE_RE.match(code) or not camera_path(code).exists():
                return self._json({"error": T("Settings not found.")}, 404)
            return self._json(json.loads(camera_path(code).read_text(encoding="utf-8")))
        frame = re.match(r"^/(?:cameras/_frame_|ayar/_kare_)([a-z0-9_-]{1,40})\.jpg$", path)
        if frame:  # only snapshots with faces masked (/ayar/_kare_: URL form of v0.1.0, still in /api/v1 answers)
            return self._file(paths.CAMERAS / f"_frame_{frame.group(1)}.jpg")
        if path.startswith("/output/"):
            return self._under(paths.OUTPUT, path[len("/output/"):])
        if path.startswith("/cikti/"):  # URL form of v0.1.0, still in /api/v1 answers
            return self._under(paths.OUTPUT, path[len("/cikti/"):])
        return self._not_found()

    # --- POST
    def do_POST(self):
        if not self._is_local():
            return self._send(403, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        try:
            g = self._body()
        except Exception:
            return self._json({"error": T("Could not read the request.")}, 400)
        try:
            return self._post(path, g)
        except SystemExit as e:
            return self._json({"error": str(e.code)}, 400)
        except Exception as e:
            traceback.print_exc(file=sys.__stderr__)
            return self._json({"error": T("Unexpected error: {error}", error=e)}, 500)

    def _code(self, g: dict) -> str:
        code = str(g.get("code", ""))
        if not CODE_RE.match(code):
            raise SystemExit(T("The short code may contain only lowercase letters (a-z), digits, - and _ (e.g. depo2)."))
        return code

    def _busy(self) -> bool:
        return ACTIVE_JOB is not None and ACTIVE_JOB.status == "running"

    def _post(self, path: str, g: dict):
        import db

        if path == "/api/i18n":  # language of the panel: saved in settings.json, used from now on (engine runs get it as ISG_LANG)
            lang = i18n.valid(g.get("lang"))
            if lang is None:
                return self._json({"error": T("Unknown language.")}, 400)
            i18n.set_language(lang)
            # only the language is written: unsaved default texts (site, shift names) keep following the language
            db.save_settings({"language": lang}, with_defaults=False)
            LANG_STATE["undecided"] = False
            return self._json({"ok": True, "lang": lang})
        if path in ("/api/job/sample", "/api/job/test", "/api/job/monitor", "/api/job/video"):
            lock = model_lock()
            studio_verifies = lock.get("community") and lock["status"] == "verified_in_studio_run"
            if lock["encrypted"] and lock["status"] != "active" and not studio_verifies:
                message = (T("To start the analysis, enter the activation code in the field at the top of the page.")
                           if lock["status"] == "key_missing" else lock.get("message") or T("The models are not activated."))
                return self._json({"error": message}, 409)
        if path in ("/api/model/device-request", "/api/model/license"):
            from isg_core import model_license, vault
            from isg_core.catalog import MODEL_FILES

            if self._busy():
                return self._json({"error": T("You can manage the model license once the running analysis finishes.")}, 409)
            folder = str(paths.ENGINE / "models")
            try:
                info = vault.license_package(folder, MODEL_FILES)
                if info is None:
                    return self._json({"error": T("This installation does not use model.lic.")}, 409)
                if path.endswith("/device-request"):
                    return self._json(model_license.device_request(*info))
                raw = g.get("file")
                if not isinstance(raw, str) or len(raw) > model_license.MAX_SIZE:
                    return self._json({"error": T("Select a valid model.lic file.")}, 400)
                model_license.save_license(str(paths.DATA), raw.encode("utf-8"), folder, *info)
            except (vault.VaultError, model_license.LicenseError, OSError, UnicodeError) as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True})
        if path == "/api/model/activate":  # legacy packages with activation codes
            import rules

            if self._busy():
                return self._json({"error": T("Activate once the running job finishes.")}, 409)
            try:
                rules.activate_models(str(paths.DATA), str(g.get("code") or ""), str(paths.ENGINE / "models"))
            except rules.vault.VaultError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "model_lock": model_lock()})
        elif path == "/api/studio/install":
            import studio_setup

            ok, m = start_job("studio-install", T("AugeLab Studio setup"), studio_setup.install)
        elif path == "/api/studio/license":
            import studio
            import studio_setup

            if self._busy():
                return self._json({"error": T("Enter the license code once the running job finishes.")}, 409)
            try:
                studio_setup.save_license(str(g.get("code") or ""))  # the code is not stored and not logged
            except SystemExit as e:
                return self._json({"error": str(e.code or T("Could not save the license code."))}, 400)
            studio.save_status(license_saved_at=time.time(), ready=None)
            _STUDIO_STATE["at"] = 0.0
            ok, m = start_job("studio-test", T("AugeLab Studio license check"), job_studio_test)
        elif path == "/api/studio/test":
            ok, m = start_job("studio-test", T("AugeLab Studio license check"), job_studio_test)
        elif path == "/api/job/sample":
            ok, m = start_job("sample", T("Sample analysis"), job_sample)
        elif path in ("/api/job/test", "/api/job/monitor", "/api/job/video"):
            code = self._code(g)
            if not camera_path(code).exists():
                return self._json({"error": T("Settings not found: {code}", code=code)}, 404)
            password = g.get("password")
            password = str(password) if password is not None else None
            name = _camera_title(read_camera(code), code)
            if path == "/api/job/test":
                ok, m = start_job("test", T("Connection test · {name}", name=name), job_test, code, password)
            elif path == "/api/job/video":
                ok, m = start_job("video", T("Video analysis · {name}", name=name), job_video, code)
            else:
                try:
                    minutes = min(240.0, max(0.25, float(g.get("minutes", 5))))
                except (TypeError, ValueError):
                    minutes = 5.0
                ok, m = start_job("monitor", T("Live monitoring · {name}", name=name), job_monitor, code, password, minutes)
        elif path == "/api/job/stop":
            if self._busy():
                ACTIVE_JOB.stop.set()
                print(T("Stop requested: {title}", title=ACTIVE_JOB.title), flush=True)
            return self._json({"ok": True})
        elif path == "/api/event/status":
            new_status = str(g.get("status", ""))
            if new_status not in ("open", "assigned", "resolved", "false_alarm"):
                return self._json({"error": T("Unknown status.")}, 400)
            ids = [int(x) for x in (g.get("ids") or [g.get("id")]) if x is not None]
            for event_id in ids:
                db.execute("UPDATE event SET status = ?, assignee = COALESCE(?, assignee), notes = COALESCE(?, notes), status_time = ? WHERE id = ?",
                           new_status, (str(g["assignee"])[:80] if g.get("assignee") else None),
                           (str(g["note"])[:2000] if g.get("note") is not None else None), db.now(), event_id)
            return self._json({"ok": True})
        elif path == "/api/task":
            title = str(g.get("title", "")).strip()[:200]
            if not title:
                return self._json({"error": T("The task has no title.")}, 400)
            new_status = str(g.get("status", "open"))
            if new_status not in ("open", "in_progress", "closed"):
                return self._json({"error": T("Unknown status.")}, 400)
            rule = g.get("rule") or None
            fields = {"title": title, "description": str(g.get("description") or "")[:4000], "owner": str(g.get("owner") or "")[:80],
                      "due_date": (str(g["due_date"])[:10] if g.get("due_date") else None), "status": new_status,
                      "camera": g.get("camera") or None, "rule": legacy.RULE_IDS.get(rule, rule) if isinstance(rule, str) else rule,
                      "zone": g.get("zone") or None}
            if g.get("id"):
                task_id = int(g["id"])
                old = db.query("SELECT status, closed FROM task WHERE id = ?", task_id)
                if not old:
                    return self._json({"error": T("Task not found.")}, 404)
                closed = old[0]["closed"] if new_status == "closed" and old[0]["status"] == "closed" else (db.now() if new_status == "closed" else None)
                db.execute("UPDATE task SET title=?, description=?, owner=?, due_date=?, status=?, camera=?, rule=?, zone=?, closed=? WHERE id=?",
                           *fields.values(), closed, task_id)
            else:
                task_id = db.insert("task", {**fields, "created": db.now(), "closed": db.now() if new_status == "closed" else None})
            for o in g.get("events") or []:
                db.execute("INSERT OR IGNORE INTO task_event (task, event) VALUES (?, ?)", task_id, int(o))
                db.execute("UPDATE event SET status = 'assigned', assignee = COALESCE(NULLIF(?, ''), assignee), status_time = ? WHERE id = ? AND status = 'open'",
                           fields["owner"], db.now(), int(o))
            return self._json({"ok": True, "id": task_id})
        elif path == "/api/task/delete":
            db.execute("DELETE FROM task_event WHERE task = ?", int(g.get("id", 0)))
            db.execute("DELETE FROM task WHERE id = ?", int(g.get("id", 0)))
            return self._json({"ok": True})
        elif path == "/api/settings":
            new = {}
            if isinstance(g.get("site"), str):
                new["site"] = g["site"].strip()[:80] or T("Site")
            if isinstance(g.get("shifts"), list):
                shifts = []
                for s in g["shifts"][:6]:
                    if not isinstance(s, dict):
                        continue
                    if (re.match(r"^\d\d:\d\d$", str(s.get("start", ""))) and re.match(r"^\d\d:\d\d$", str(s.get("end", "")))
                            and str(s.get("name", "")).strip()):
                        shifts.append({"name": str(s["name"]).strip()[:30], "start": s["start"], "end": s["end"]})
                if shifts:
                    new["shifts"] = shifts
            if isinstance(g.get("outputs"), list):
                import outputs

                new["outputs"] = [outputs.sanitize(x) for x in g["outputs"][:20] if isinstance(x, dict)]
            if "network_access" in g:
                new["network_access"] = bool(g["network_access"])
                if new["network_access"] and not db.settings().get("api_key"):
                    new["api_key"] = secrets.token_urlsafe(32)
            if g.get("regenerate_key"):
                new["api_key"] = secrets.token_urlsafe(32)
            db.save_settings(new)
            return self._json({"ok": True, "settings": db.settings()})
        elif path == "/api/output/test":
            import outputs

            ok, message = outputs.test(outputs.sanitize(g.get("output") or {}))
            return self._json({"ok": ok, "message": message})
        elif path == "/api/onvif/discover":  # ONVIF cameras and recorders on the network (no password needed)
            import onvif_client

            return self._json({"devices": onvif_client.discover(3.0)})
        elif path == "/api/onvif/profiles":  # the device's channels and profiles; the password is used only for this request, not stored
            import onvif_client

            try:
                device = onvif_client.Device(str(g.get("ip") or "").strip(), int(g.get("port") or 80), str(g.get("username") or "admin"),
                                             str(g.get("password") or ""))
                profiles = device.profiles()
                return self._json({"device": device.info(), "profiles": profiles})
            except (onvif_client.OnvifError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
        elif path == "/api/camera/channels":  # a recorder's channels as separate cameras: all are added or none
            try:
                return self._json({"ok": True, **create_cameras(channel_cameras(g.get("base"), g.get("channels"), g.get("profiles")))})
            except CameraConfigError as e:
                return self._json({"error": str(e)}, 400)
        elif path == "/api/camera":
            try:
                if g.get("new"):  # new camera only: an existing one is not overwritten
                    result = create_cameras([g.get("camera")])
                    return self._json({"ok": True, "code": result["codes"][0], "notes": result["notes"]})
                code, config, notes = _prepare_camera(g.get("camera"))
            except CameraConfigError as e:
                return self._json({"error": str(e)}, 400)
            with _CAMERA_LOCK:
                paths.CAMERAS.mkdir(parents=True, exist_ok=True)
                camera_path(code).write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
            return self._json({"ok": True, "code": code, "notes": notes})
        elif path == "/api/camera/delete":
            code = self._code(g)
            if self._busy():
                return self._json({"error": T("Finish or stop the running job first.")}, 409)
            if code not in paths.sample_codes():
                camera_path(code).unlink(missing_ok=True)
                (paths.CAMERAS / f"_frame_{code}.jpg").unlink(missing_ok=True)
            db.delete_camera(code)
            shutil.rmtree(paths.OUTPUT / code, ignore_errors=True)
            return self._json({"ok": True})
        elif path == "/api/open-data-folder":
            if hasattr(os, "startfile"):
                os.startfile(str(paths.DATA))  # noqa: S606 - only our own data folder
            return self._json({"ok": True})
        elif path in ("/api/shutdown", "/api/kapat"):  # /api/kapat: path of v0.1.0, called by older installers (permanent alias)
            self._json({"ok": True, "tamam": True} if path == "/api/kapat" else {"ok": True})
            if ACTIVE_JOB is not None:
                ACTIVE_JOB.stop.set()
            threading.Thread(target=shutdown, args=(self.server,), daemon=True).start()
            return None
        else:
            return self._json({"error": T("Unknown request.")}, 404)
        if not ok:
            return self._json({"error": m}, 409)
        return self._json({"ok": True, "job": ACTIVE_JOB.summary() if ACTIVE_JOB else None})


def shutdown(server) -> None:
    time.sleep(0.4)
    print(T("Closing İSG Kamera."), flush=True)
    try:
        import studio

        studio.stop_all()  # headless Studio processes must not stay behind
    except Exception:
        pass
    server.shutdown()


# ---------------------------------------------------------------- start


def is_running(port: int) -> bool:
    """Is an İSG Kamera already running on this port? (A panel of v0.1.0 answers /api/durum with "uygulama".)"""
    for path, field in (("/api/status", "app"), ("/api/durum", "uygulama")):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                if json.loads(r.read().decode("utf-8")).get(field) == paths.APP_NAME:
                    return True
        except Exception:
            continue
    return False


def close_splash() -> None:
    try:
        import pyi_splash  # exists only in the exe

        pyi_splash.close()
    except Exception:
        pass


def warm_up() -> None:
    """Load heavy libraries in the background so the first job starts sooner."""
    try:
        import camera  # noqa: F401
        import event_queue  # noqa: F401
    except Exception:
        traceback.print_exc(file=sys.__stderr__)


def init_language(existing: bool) -> None:
    """Sets the panel language at start (right after the data-folder migration, before the data folder is prepared).

    Saved setting (settings.json "language") if valid; else an existing installation (`existing`: old or new data was
    there before this start) stays Turkish (nothing is written, its settings file stays as it is); else (fresh
    installation) the language is undecided: Turkish is the server default until the browser chooses (POST /api/i18n)."""
    import db

    saved = i18n.valid(db.settings().get("language"))
    i18n.set_language(saved or "tr")
    LANG_STATE["undecided"] = saved is None and not existing


def main() -> None:
    if migration.pending() and is_running(DEFAULT_PORT):
        # an older panel still uses the data folder: it is converted at the next start, after that panel is closed
        close_splash()
        print(T("An older İSG Kamera is still running. Close it and start the program again."))
        time.sleep(5)
        return
    migrated = migration.migrate()  # first: everything below reads the data folder in the new format
    init_language(migrated["existed"])
    ap = argparse.ArgumentParser(prog=paths.APP_NAME, description=T("İSG camera analysis: local server"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help=T("do not open the browser"))
    ap.add_argument("--tarayici-yok", dest="no_browser", action="store_true", help=argparse.SUPPRESS)  # option name of v0.1.0
    a = ap.parse_args()

    if is_running(a.port):
        close_splash()
        url = f"http://127.0.0.1:{a.port}/"
        print(T("İSG Kamera is already running: {url}", url=url))
        if not a.no_browser:
            webbrowser.open(url)
        time.sleep(2)
        return

    sys.stdout, sys.stderr = REDIRECT_OUT, REDIRECT_ERR
    print(f"İSG Kamera {paths.VERSION}")
    paths.prepare()
    if migrated["migrated"]:
        print(T("The data folder was converted to the new format; the original files are in {path}.", path=migrated["backup"]))
    if not migrated["existed"]:
        print(T("First setup: the data folder is ready."))
    # if the program closed during monitoring, delete leftover raw recording pieces (faces not masked)
    for f in paths.VIDEOS.glob("*.mp4"):
        if re.search(r"_\d{8}_\d{6}\.mp4$", f.name):
            f.unlink(missing_ok=True)
            (paths.CACHE / f"{f.stem}.json").unlink(missing_ok=True)
    import db

    db.conn()

    network = bool(db.settings().get("network_access")) and len(db.settings().get("api_key") or "") >= 20
    host = "0.0.0.0" if network else "127.0.0.1"
    server = None
    for port in [a.port] + list(range(a.port + 1, a.port + 25)):
        try:
            server = Server((host, port), RequestHandler)
            break
        except OSError:
            if is_running(port):
                close_splash()
                print(T("İSG Kamera is already running: {url}", url=f"http://127.0.0.1:{port}/"))
                if not a.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{port}/")
                time.sleep(2)
                return
    if server is None:
        close_splash()
        print(T("No free port found."))
        time.sleep(5)
        raise SystemExit(1)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    LISTENING.update(address=host, port=port)
    if network:
        try:
            LISTENING["network"] = sorted({b[4][0] for b in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                                           if not b[4][0].startswith("127.")})
        except OSError:
            pass
        print(T("  Network access ON: systems on the network can read the /api/v2/... endpoints with the API key (the "
                "interface is still available only on this computer)."))
    print(T("  Address : {url}", url=url))
    print(T("  Data    : {path}", path=paths.DATA))
    print(T("To quit, close this window (or press the Close button on the Settings page).") + "\n", flush=True)
    threading.Thread(target=warm_up, daemon=True).start()
    import event_queue

    queue_stop = threading.Event()
    threading.Thread(target=event_queue.run_forever, args=(queue_stop, _notify_outputs), name="queue", daemon=True).start()
    close_splash()
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    # if monitoring is running, give it a moment so the recording closes cleanly
    if ACTIVE_JOB is not None and ACTIVE_JOB.status == "running":
        ACTIVE_JOB.stop.set()
        for _ in range(10):
            if ACTIVE_JOB.status != "running":
                break
            time.sleep(0.3)


if __name__ == "__main__":
    main()
