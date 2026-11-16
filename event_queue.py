"""Takes the records left by the engine in AugeLab Studio (<data>/queue/*.json) into the database.

Record types (field "type"):
  event        an event started (ended = false) or closed (ended = true); both come with the same `uid`
  measurement  the counts of a time slice (heat map, paths, PPE compliance times)
  done         the stream of a video file ended
  error        detection in Studio is not working (field "message")
  heartbeat    the scene is running (field "frames")
Clips are written raw (mp4v) by the engine; here they are converted to H.264, which plays in the browser.

Records left by a v0.1.0 engine (Turkish field names, written while the panel was closed) are still accepted for one
release: normalize_record() maps them to the current schema.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import db
import paths
from i18n import T

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
FOLDER = paths.DATA / "queue"
# Kept per run: a late record from an earlier run on the same camera does not overwrite the current run's record.
FINISHED: dict[str, dict[str, float]] = {}  # camera → {run id: moment its stream ended}
LAST_RECORD: dict[str, float] = {}          # camera → moment of the last record taken in
ERRORS: dict[str, dict[str, str]] = {}      # camera → {run id: reason}: detection in Studio is not working, no footage is saved
_LOCK = threading.Lock()

_RAW_SUFFIXES = (".raw.mp4", ".ham.mp4")  # ".ham.mp4": clips referenced by v0.1.0 records

# ---------------------------------------------------------------- v0.1.0 records
# Local copy of the queue part of the v0.1.0 -> v2 mapping (the data migration renames the folder but leaves record
# contents to this reader). Rule ids go through catalog.LEGACY_RULE_IDS so that table is defined once.
_V1_TYPES = {"olay": "event", "olcum": "measurement", "bitti": "done", "hata": "error", "nabiz": "heartbeat"}
_V1_COMMON = {"tur": "type", "kamera": "camera", "kamera_ad": "camera_name", "kosu": "run"}
_V1_FIELDS = {
    "event": {"kimlik": "uid", "bitti": "ended", "kural": "rule", "kural_ad": "rule_name", "kategori": "category",
              "bolge": "zone", "onem": "severity", "olasilik": "likelihood", "puan": "score", "ciddi": "serious",
              "zaman": "time", "bas": "start_s", "sure_sn": "duration_s", "kisi": "people", "aciklama": "description",
              "kare": "snapshot", "klip": "clip", "son": "last", "ek": "extra", "sira": "seq", "kapandi": "closed"},
    "measurement": {"bas": "start", "sure_sn": "duration_s", "kisi_sn": "person_s", "kisi_iz": "people_tracked",
                    "tepe_kisi": "peak_people", "arac_iz": "vehicles_tracked", "kkd_uyumlu_sn": "ppe_compliant_s",
                    "kkd_uyumsuz_sn": "ppe_noncompliant_s", "izgara": "grid", "izler": "tracks", "son_kare": "last_frame"},
    "error": {"metin": "message"},
    "heartbeat": {"kare": "frames"},
}
_V1_CATEGORIES = {"kkd": "ppe", "alan": "area", "davranis": "behavior", "arac": "vehicle", "duzen": "housekeeping",
                  "ergonomi": "ergonomics"}
_V1_GRID = {"gen": "cols", "yuk": "rows", "kisi": "person", "arac": "vehicle"}
_V1_SUBJECTS = {"kisi": "person", "arac": "vehicle"}


def _legacy_rule_ids() -> dict:
    try:
        import rules  # noqa: F401  (adds the engine folder to the path)
        from isg_core.catalog import LEGACY_RULE_IDS
        return LEGACY_RULE_IDS
    except Exception:
        return {}


def normalize_record(r: dict) -> dict:
    """Returns the record in the current schema; a v0.1.0 record (field "tur" instead of "type") is mapped."""
    if "type" in r or "tur" not in r:
        return r
    kind = _V1_TYPES.get(r.get("tur"), r.get("tur"))
    fields = {**_V1_COMMON, **_V1_FIELDS.get(kind, {})}
    out = {fields.get(k, k): v for k, v in r.items()}
    out["type"] = kind
    if kind == "event":
        out["rule"] = _legacy_rule_ids().get(out.get("rule"), out.get("rule"))
        out["category"] = _V1_CATEGORIES.get(out.get("category"), out.get("category"))
    elif kind == "measurement":
        grid = out.get("grid")
        if isinstance(grid, dict):
            out["grid"] = {_V1_GRID.get(k, k): v for k, v in grid.items()}
        tracks = out.get("tracks")
        if isinstance(tracks, list):
            out["tracks"] = [{**t, "s": _V1_SUBJECTS.get(t.get("s"), t.get("s"))} if isinstance(t, dict) else t for t in tracks]
        if isinstance(out.get("last_frame"), str) and out["last_frame"].endswith("/son_kare.jpg"):
            out["last_frame"] = out["last_frame"][:-len("son_kare.jpg")] + "last_frame.jpg"
    return out


# ---------------------------------------------------------------- intake


def _convert_clip(rel: str | None) -> str | None:
    """Converts a raw clip under output/ to H.264; returns the new relative path."""
    if not rel:
        return None
    raw = paths.OUTPUT / rel
    suffix = next((s for s in _RAW_SUFFIXES if rel.endswith(s)), None)
    if suffix is None:
        return rel if raw.exists() else None
    target = raw.with_name(raw.name[:-len(suffix)] + ".mp4")
    if not raw.exists():
        return None
    try:
        subprocess.run([paths.ffmpeg(), "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
                        "-movflags", "+faststart", str(target)], check=True, stdin=subprocess.DEVNULL, timeout=120, **_NO_WINDOW)
    except Exception:
        return None
    raw.unlink(missing_ok=True)
    return str(target.relative_to(paths.OUTPUT)).replace("\\", "/")


def _upsert_event(r: dict, sample: bool) -> int | None:
    """Inserts or updates the event. Returns the id of a newly inserted event."""
    clip = _convert_clip(r.get("clip")) if r.get("ended") else None
    existing = db.query("SELECT id, snapshot FROM event WHERE uid = ?", r["uid"])
    if existing:
        db.execute("UPDATE event SET duration_s = ?, likelihood = ?, score = ?, people = ?, x = ?, y = ?, description = ?, "
                   "clip = COALESCE(?, clip), snapshot = COALESCE(?, snapshot), ongoing = ? WHERE id = ?",
                   r["duration_s"], r["likelihood"], r["score"], r["people"], r["x"], r["y"], r["description"], clip,
                   r.get("snapshot"), 0 if r.get("ended") else 1, existing[0]["id"])
        return None
    return db.insert("event", {
        "uid": r["uid"], "time": r["time"], "camera": r["camera"], "camera_name": r.get("camera_name") or r["camera"], "rule": r["rule"],
        "rule_name": r["rule_name"], "category": r["category"], "zone": r.get("zone"), "severity": r["severity"], "likelihood": r["likelihood"],
        "score": r["score"], "serious": r["serious"], "duration_s": r["duration_s"], "people": r["people"], "x": r["x"], "y": r["y"],
        "clip": clip, "snapshot": r.get("snapshot"), "description": r["description"], "sample": int(sample), "ongoing": 0 if r.get("ended") else 1,
    })


def drain() -> list[int]:
    """Takes in the queued records in order. Returns the ids of newly started events (to be sent to the outputs)."""
    new: list[int] = []
    if not FOLDER.is_dir():
        return new
    samples = set(paths.sample_codes())
    with _LOCK:
        for file in sorted(FOLDER.glob("*.json")):
            try:
                r = normalize_record(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # may still be being written; next round
            try:
                camera = r.get("camera", "")
                LAST_RECORD[camera] = time.time()
                kind = r.get("type")
                if kind == "event":
                    event_id = _upsert_event(r, camera in samples)
                    if event_id is not None:
                        new.append(event_id)
                elif kind == "measurement":
                    db.insert("measurement", {
                        "camera": camera, "camera_name": r.get("camera_name") or camera, "start": r["start"], "duration_s": r["duration_s"],
                        "person_s": r["person_s"], "people_tracked": r["people_tracked"], "peak_people": r["peak_people"],
                        "vehicles_tracked": r["vehicles_tracked"], "ppe_compliant_s": r["ppe_compliant_s"],
                        "ppe_noncompliant_s": r["ppe_noncompliant_s"], "violation_s": 0, "grid": r["grid"], "tracks": r["tracks"],
                        "last_frame": r.get("last_frame"), "source": "studio", "sample": int(camera in samples),
                    })
                elif kind == "done":
                    FINISHED.setdefault(camera, {})[str(r.get("run") or "")] = time.time()
                elif kind == "error":
                    ERRORS.setdefault(camera, {})[str(r.get("run") or "")] = str(r.get("message") or T("unknown reason"))
            finally:
                file.unlink(missing_ok=True)
    return new


def _for_run(records: dict | None, run: str):
    """This run's record: only the record with a matching id. A record of another run, or one without an id, never
    counts for a run with an id. Without a run id (development commands) the record without an id counts, or else any
    record of the camera."""
    if not records:
        return None
    if run:
        return records.get(run)
    return records[""] if "" in records else next(iter(records.values()))


def run_finished(code: str, run: str = "") -> bool:
    """Has this run's stream ended (was its "done" record taken in)?"""
    return _for_run(FINISHED.get(code), run) is not None


def run_error(code: str, run: str = "") -> str | None:
    """If a record arrived in this run saying detection in Studio is not working, the reason; a record left from an
    earlier run does not count."""
    return _for_run(ERRORS.get(code), run)


DRAIN_TIMEOUT_S = 300.0  # default wait of wait_drained


def wait_drained(code: str, timeout_s: float | None = None, quiet_s: float = 1.5) -> bool:
    """Waits until every queued record of this camera has been taken in. Called after the scene is stopped.

    The queue counts as empty when no file of this camera (including a .tmp still being written) is found for `quiet_s`
    seconds and the intake lock is free: intake, including clip conversion, happens under the lock. When `timeout_s`
    seconds (DRAIN_TIMEOUT_S if not given) pass, it returns False; the caller must then skip cleanup (there may be
    unprocessed records and unconverted clips). Time is measured with a monotonic clock and the lock is acquired waiting
    at most the remaining time: the function does not overrun even while a long clip conversion holds the lock."""
    deadline = time.monotonic() + (DRAIN_TIMEOUT_S if timeout_s is None else timeout_s)
    empty_since = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not _LOCK.acquire(timeout=remaining):
            return False
        try:
            empty = not FOLDER.is_dir() or not any(FOLDER.glob(f"*_{code}_*"))
        finally:
            _LOCK.release()
        now = time.monotonic()
        if not empty:
            empty_since = None
        elif empty_since is None:
            empty_since = now
        elif now - empty_since >= quiet_s:
            return True
        time.sleep(max(0.0, min(0.2, deadline - time.monotonic())))


def run_forever(stop: threading.Event, on_new_events=None) -> None:
    """Background loop: drains the queue once a second; `on_new_events(ids)` triggers the outputs."""
    while not stop.is_set():
        try:
            new = drain()
            if new and on_new_events is not None:
                on_new_events(new)
        except Exception:
            import sys
            import traceback

            traceback.print_exc(file=sys.__stderr__)
        stop.wait(1.0)
