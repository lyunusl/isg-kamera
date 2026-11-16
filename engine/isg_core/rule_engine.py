"""Live rule engine: takes the detections of every frame, tracks people and vehicles, applies the rules.

An event STARTS as soon as its condition has lasted `duration_s` (alerts and outputs fire at that moment) and CLOSES when
the condition ends. Uses only numpy and OpenCV; runs inside AugeLab Studio (the ISG Rules block).
"""
from __future__ import annotations

import datetime as dt
import math
import uuid
from collections import defaultdict, deque

import cv2
import numpy as np

from . import catalog, color
from .detect import same_box
from .i18n import T

GRID = (32, 18)        # heat map cells (width, height)
VEHICLES = {"forklift", "vehicle"}
OBJECTS = {"pallet", "box", "barrel", "ladder", "phone"}  # tracked objects (add-on model)
LOADS = {"box", "barrel", "phone"}  # objects that fill a hand: a hand holding one of them does not count as holding the ladder
HELD = {"phone"}       # hand-held objects: the zone is decided by where the holder stands, not by the object
EMPTY_POSE = [[0.0, 0.0, 0.0]] * 17
NEGATIVE_CONFIDENCE = 0.5      # a "not worn" detection counts only with at least this confidence
ATTR_PRESENT, ATTR_ABSENT = 0.6, 0.4  # attribute model probability: above = "worn", below = "not worn", in between undecided (not evaluated)
PPE_MIN_HEIGHT = 60       # protection is not evaluated for a person shorter than this (pixels)
MISSING_SUFFIX = "_missing"  # class code suffix: that protection is clearly seen not being worn
# body region in which each protection is looked for
BODY_PART = {"hard_hat": "head", "safety_glasses": "head", "face_mask": "head", "face_shield": "head", "hearing_protection": "head",
             "respirator": "head", "vest": "torso", "apron": "torso", "coveralls": "torso", "gloves": "hand", "sleeve_guards": "hand",
             "safety_shoes": "foot"}


KP = 0.4  # keypoint confidence threshold

# REBA tables (Hignett and McAtamney, 2000). A: trunk × neck × legs · B: upper arm × lower arm × wrist · C: A × B
REBA_A = [[[1, 2, 3, 4], [1, 2, 3, 4], [3, 3, 5, 6]], [[2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]], [[2, 4, 5, 6], [4, 5, 6, 7], [5, 6, 7, 8]],
          [[3, 5, 6, 7], [5, 6, 7, 8], [6, 7, 8, 9]], [[4, 6, 7, 8], [6, 7, 8, 9], [7, 8, 9, 9]]]
REBA_B = [[[1, 2, 2], [1, 2, 3]], [[1, 2, 3], [2, 3, 4]], [[3, 4, 5], [4, 5, 5]], [[4, 5, 5], [5, 6, 7]], [[6, 7, 8], [7, 8, 8]], [[7, 8, 8], [8, 9, 9]]]
REBA_C = [[1, 1, 1, 2, 3, 3, 4, 5, 6, 7, 7, 7], [1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 7, 8], [2, 3, 3, 3, 4, 5, 6, 7, 7, 8, 8, 8],
          [3, 4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9], [4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9, 9], [6, 6, 6, 7, 8, 8, 9, 9, 10, 10, 10, 10],
          [7, 7, 7, 8, 9, 9, 9, 10, 10, 11, 11, 11], [8, 8, 8, 9, 10, 10, 10, 10, 10, 11, 11, 11], [9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 12],
          [10, 10, 10, 11, 11, 11, 11, 12, 12, 12, 12, 12], [11, 11, 11, 11, 12, 12, 12, 12, 12, 12, 12, 12], [12] * 12]


def _angle(u, v) -> float:
    """Angle between two directions (degrees)."""
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu < 1e-6 or nv < 1e-6:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))))


def _mid(k, indices) -> tuple[float, float] | None:
    n = [k[i] for i in indices if k[i][2] >= KP]
    return (sum(p[0] for p in n) / len(n), sum(p[1] for p in n) / len(n)) if n else None


def torso_angle(k) -> float | None:
    """Deviation of the trunk (hip → shoulder) from vertical, in degrees. 0 = upright, 90 = horizontal."""
    sh, hip = _mid(k, (5, 6)), _mid(k, (11, 12))
    if sh is None or hip is None:
        return None
    return _angle((sh[0] - hip[0], sh[1] - hip[1]), (0.0, -1.0))


def reba(k) -> int | None:
    """Approximate REBA score (1-12) from the joint angles in the image. Load, coupling and repetition scores are not added;
    the wrist counts as 1. Angles are in the image plane: bending toward or away from the camera is under-measured."""
    sh, hip = _mid(k, (5, 6)), _mid(k, (11, 12))
    if sh is None or hip is None:
        return None
    trunk = (sh[0] - hip[0], sh[1] - hip[1])
    # With a camera looking down from high up, an upright person also looks tilted in the image; so the trunk tilt is measured
    # against the person's own leg line (ankle → hip), not the image vertical. If the legs are not visible, the image vertical is used.
    ankle = _mid(k, (15, 16))
    upright = ((hip[0] - ankle[0], hip[1] - ankle[1]) if ankle is not None and math.hypot(hip[0] - ankle[0], hip[1] - ankle[1]) > 0.3 * math.hypot(*trunk)
               else (0.0, -1.0))
    a = _angle(trunk, upright)
    g = 1 if a < 5 else 2 if a < 20 else 3 if a < 60 else 4
    head = _mid(k, (0, 1, 2, 3, 4))
    neck = 1 if head is None or _angle((head[0] - sh[0], head[1] - sh[1]), trunk) < 20 else 2
    bend = 0.0
    for hip_i, knee_i, ankle_i in ((11, 13, 15), (12, 14, 16)):
        if min(k[hip_i][2], k[knee_i][2], k[ankle_i][2]) >= KP:
            bend = max(bend, 180 - _angle((k[hip_i][0] - k[knee_i][0], k[hip_i][1] - k[knee_i][1]),
                                          (k[ankle_i][0] - k[knee_i][0], k[ankle_i][1] - k[knee_i][1])))
    legs = 1 + (1 if 30 <= bend < 60 else 2 if bend >= 60 else 0)
    upper, lower = 1, 1
    for shoulder_i, elbow_i, wrist_i in ((5, 7, 9), (6, 8, 10)):
        if min(k[shoulder_i][2], k[elbow_i][2]) < KP:
            continue
        arm = _angle((k[elbow_i][0] - k[shoulder_i][0], k[elbow_i][1] - k[shoulder_i][1]), (-trunk[0], -trunk[1]))
        upper = max(upper, 1 if arm < 20 else 2 if arm < 45 else 3 if arm < 90 else 4)
        if k[wrist_i][2] >= KP:
            elbow_bend = 180 - _angle((k[shoulder_i][0] - k[elbow_i][0], k[shoulder_i][1] - k[elbow_i][1]),
                                      (k[wrist_i][0] - k[elbow_i][0], k[wrist_i][1] - k[elbow_i][1]))
            lower = max(lower, 1 if 60 <= elbow_bend <= 100 else 2)
    return REBA_C[REBA_A[g - 1][neck - 1][legs - 1] - 1][REBA_B[upper - 1][lower - 1][0] - 1]


def foot_point(b, k=None) -> tuple[float, float]:
    """The person's point on the ground: the ankles (with a pose), otherwise the bottom center of the box."""
    if k:
        n = [k[i] for i in (15, 16) if k[i][2] >= KP]
        if n:
            return (sum(p[0] for p in n) / len(n), max(p[1] for p in n))
    return ((b[0] + b[2]) / 2, float(b[3]))


def inside(poly: np.ndarray, p) -> bool:
    return cv2.pointPolygonTest(poly, (float(p[0]), float(p[1])), False) >= 0


def head_region(b, k=None) -> tuple[float, float, float, float]:
    """Ellipse covering the face (cx, cy, rx, ry): from the face keypoints with a pose, otherwise the top center of the person box."""
    x1, y1, x2, y2 = b
    w, h = x2 - x1, y2 - y1
    if k:
        face = [k[i] for i in range(5) if k[i][2] >= KP]
        if len(face) >= 2:
            cx, cy = sum(p[0] for p in face) / len(face), sum(p[1] for p in face) / len(face)
            r = max(max(math.hypot(p[0] - cx, p[1] - cy) for p in face) * 2.1, 0.09 * h, 8.0)
            r = min(r, max(0.27 * h, 12.0)) * 1.15
            return cx, cy, r, r * 1.2
    r = min(max(0.11 * h, 0.2 * w, 9.0), max(0.27 * h, 12.0)) * 1.15
    return (x1 + x2) / 2, y1 + 0.11 * h, r, r * 1.2


def body_parts(b, k=None) -> dict:
    """Body regions in which protection is looked for: hands and feet from the keypoints with a pose, otherwise from the person box."""
    x1, y1, x2, y2 = b
    w, h = x2 - x1, y2 - y1
    if k:
        r = max(0.12 * h, 10.0)
        hands = [p for p in (k[9], k[10]) if p[2] >= KP]
        feet = [p for p in (k[15], k[16]) if p[2] >= KP]
        if hands or feet:
            box = lambda ns, up, down: [min(p[0] for p in ns) - r, min(p[1] for p in ns) - up * r, max(p[0] for p in ns) + r, max(p[1] for p in ns) + down * r]  # noqa: E731
            return {
                "head": [x1 - 0.1 * w, y1 - 0.06 * h, x2 + 0.1 * w, y1 + 0.3 * h],
                "torso": [x1, y1 + 0.12 * h, x2, y1 + 0.68 * h],
                "hand": box(hands, 1.0, 1.0) if hands else [x1 - 0.15 * w, y1 + 0.25 * h, x2 + 0.15 * w, y1 + 0.8 * h],
                "foot": box(feet, 0.8, 1.3) if feet else [x1 - 0.1 * w, y2 - 0.2 * h, x2 + 0.1 * w, y2 + 0.04 * h],
            }
    return {
        "head": [x1 - 0.1 * w, y1 - 0.06 * h, x2 + 0.1 * w, y1 + 0.3 * h],
        "torso": [x1, y1 + 0.12 * h, x2, y1 + 0.68 * h],
        "hand": [x1 - 0.15 * w, y1 + 0.25 * h, x2 + 0.15 * w, y1 + 0.8 * h],
        "foot": [x1 - 0.1 * w, y2 - 0.2 * h, x2 + 0.1 * w, y2 + 0.04 * h],
    }


class Tracker:
    """Links boxes across frames to the same identity: overlap first, then proximity."""

    def __init__(self, lifetime_s: float = 1.5, new_confidence: float = 0.4):
        self.tracks: dict[int, dict] = {}
        self.counter = 0
        self.lifetime = lifetime_s
        self.new_confidence = new_confidence

    def update(self, t: float, boxes: list[dict]) -> list[dict]:
        """Writes d["id"] into every box; a new track is opened only for a confident enough detection. Returns: the boxes with an id."""
        for z in self.tracks.values():  # estimated position in this frame from the velocity
            d = t - z["t"]
            z["pred"] = [z["b"][0] + z["v"][0] * d, z["b"][1] + z["v"][1] * d, z["b"][2] + z["v"][0] * d, z["b"][3] + z["v"][1] * d]
        candidates = []
        for i, d in enumerate(boxes):
            for track_id, z in self.tracks.items():
                if z["s"] != d["s"]:
                    continue
                x1, y1, x2, y2 = max(d["b"][0], z["pred"][0]), max(d["b"][1], z["pred"][1]), min(d["b"][2], z["pred"][2]), min(d["b"][3], z["pred"][3])
                k = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                a = (d["b"][2] - d["b"][0]) * (d["b"][3] - d["b"][1]) + (z["pred"][2] - z["pred"][0]) * (z["pred"][3] - z["pred"][1]) - k
                iou = k / (a + 1e-6)
                height = max(d["b"][3] - d["b"][1], z["b"][3] - z["b"][1], 1.0)
                dist = math.hypot((d["b"][0] + d["b"][2] - z["pred"][0] - z["pred"][2]) / 2, d["b"][3] - z["pred"][3]) / height
                if iou >= 0.15:
                    candidates.append((1.0 + iou, i, track_id))
                elif dist <= 0.6:
                    candidates.append((1.0 - dist, i, track_id))
        box_track, used_tracks = {}, set()
        for _, i, track_id in sorted(candidates, reverse=True):
            if i in box_track or track_id in used_tracks:
                continue
            box_track[i] = track_id
            used_tracks.add(track_id)
        out = []
        for i, d in enumerate(boxes):
            track_id = box_track.get(i)
            if track_id is None:
                if d["p"] < self.new_confidence:
                    continue
                self.counter += 1
                track_id = self.counter
                self.tracks[track_id] = {"b": d["b"], "t": t, "s": d["s"], "v": (0.0, 0.0), "first": t, "n": 0}
            z = self.tracks[track_id]
            elapsed = t - z["t"]
            if elapsed > 0 and z["n"] > 0:
                vx = ((d["b"][0] + d["b"][2]) - (z["b"][0] + z["b"][2])) / 2 / elapsed
                vy = (d["b"][3] - z["b"][3]) / elapsed
                z["v"] = (0.6 * z["v"][0] + 0.4 * vx, 0.6 * z["v"][1] + 0.4 * vy)
            z["b"], z["t"], z["n"] = d["b"], t, z["n"] + 1
            d["id"] = track_id
            out.append(d)
        for track_id in [k for k, z in self.tracks.items() if t - z["t"] > self.lifetime]:
            del self.tracks[track_id]
        return out


class EventManager:
    """Tracks the states of one rule: if the condition lasts `duration_s` an event starts; the same event continues even if the track id changes."""

    def __init__(self, rule: dict, frame_interval: float):
        self.k = rule
        self.duration = float(rule.get("duration_s", 1.0))
        self.gap = max(1.0, 3 * frame_interval, 0.5 * self.duration)
        self.pending: dict = {}
        self.open: list[dict] = []
        self.count = 0  # how many events this rule has started

    def update(self, t: float, active: dict) -> tuple[list[dict], list[dict], list[dict]]:
        started, ended, highlights = [], [], []
        for key, b in active.items():
            a = self.pending.get(key)
            if a is None:
                a = self.pending[key] = {"start": t, "event": None}
            a["last"] = t
            if t - a["start"] < self.duration:
                continue
            o = a["event"]
            if o is None or o.get("closed"):
                o = next((x for x in self.open if x["zone"] == b.get("zone") and t - x["last"] <= 2.0
                          and math.hypot(x["m"][0] - b["xy"][0], x["m"][1] - b["xy"][1]) < 0.15), None)
                if o is None:
                    self.count += 1
                    o = {"uid": uuid.uuid4().hex, "rule": self.k["id"], "start": a["start"], "last": t, "people": 0, "xy": [], "m": b["xy"],
                         "zone": b.get("zone"), "extra": b.get("extra"), "seq": self.count}
                    self.open.append(o)
                    started.append(o)
                a["event"] = o
            o["last"] = t
            o["people"] = max(o["people"], b.get("people", 1))
            o["xy"].append(b["xy"])
            o["m"] = tuple(np.mean(o["xy"][-40:], axis=0))
            if b.get("extra") is not None:
                o["extra"] = b["extra"]
            highlights += b.get("boxes", [])
        for key in [a for a, d in self.pending.items() if a not in active and t - d["last"] > self.gap]:
            del self.pending[key]
        for o in [o for o in self.open if t - o["last"] > max(self.gap, 2.0)]:
            self.open.remove(o)
            o["closed"] = True
            ended.append(o)
        return started, ended, highlights

    def finish(self) -> list[dict]:
        ended, self.open, self.pending = self.open, [], {}
        for o in ended:
            o["closed"] = True
        return ended


def _in_hours(now: dt.datetime, ranges: list[str]) -> bool:
    minutes = now.hour * 60 + now.minute
    for a in ranges:
        try:
            x, y = a.split("-")
            b1, b2 = int(x[:2]) * 60 + int(x[3:5]), int(y[:2]) * 60 + int(y[3:5])
        except Exception:
            continue
        if (b1 <= minutes < b2) if b1 < b2 else (minutes >= b1 or minutes < b2):
            return True
    return False


class RuleEngine:
    """The rule engine of one camera. `step()` is called for every frame."""

    def __init__(self, camera: dict, W: int, H: int, start_time: dt.datetime, frame_interval: float = 0.125):
        self.camera, self.W, self.H = camera, W, H
        self.start_time = start_time
        self.interval = frame_interval
        scale = np.array([W, H], dtype=np.float32)
        self.zones = [{"name": b.get("name", T("Zone")), "type": b.get("type", ""), "poly": np.array(b["poly"], dtype=np.float32) * scale,
                       "direction": b.get("direction_deg")} for b in camera.get("zones") or [] if len(b.get("poly") or []) >= 3]
        self.zones_by_type = defaultdict(list)
        for b in self.zones:
            self.zones_by_type[b["type"]].append(b)
        self.rules = catalog.resolve(camera)
        self.managers = {k["id"]: EventManager(k, frame_interval) for k in self.rules}
        self.person_tracker, self.vehicle_tracker, self.object_tracker = Tracker(), Tracker(2.0), Tracker(4.0, 0.3)
        self.object_first: dict = {}  # object track → where it was first seen (if it moves, the timer restarts)
        self.authorized_colors = color.resolve(camera["authorized_colors"]) if camera.get("authorized_colors") else []
        self.color_history = defaultdict(lambda: deque(maxlen=40))  # person → whether the authorized color was seen in recent frames
        self.history = defaultdict(lambda: deque(maxlen=64))        # (kind, id) → (t, x, y, height)
        self.ppe_history = defaultdict(lambda: deque(maxlen=64))    # (rule, id) → (t, state)
        self.last_t = None
        self._reset_measurement()

    # ---------------------------------------------------------------- measurement
    def _reset_measurement(self) -> None:
        self.m_start = None
        self.m_grid = {"person": np.zeros((GRID[1], GRID[0]), np.float32), "vehicle": np.zeros((GRID[1], GRID[0]), np.float32)}
        self.m_track_points, self.m_track_last, self.m_first, self.m_last = defaultdict(list), {}, {}, {}
        self.m_people_series: list[int] = []
        self.m_compliant = self.m_noncompliant = 0.0
        self.m_duration = 0.0

    def measurement(self) -> dict | None:
        """Counts gathered since the last call (heat map, paths, compliance times); the counters are reset."""
        if self.m_start is None or not self.m_people_series:
            return None
        tracks = [{"s": kind, "p": n} for (kind, track_id), n in self.m_track_points.items()
                  if len(n) >= 4 and self.m_last[(kind, track_id)] - self.m_first[(kind, track_id)] >= 1.5]
        tracks = sorted(tracks, key=lambda z: -len(z["p"]))[:300]
        lived = lambda kind, at_least: sum(1 for a in self.m_first if a[0] == kind and self.m_last[a] - self.m_first[a] >= at_least)  # noqa: E731
        out = {
            "start": (self.start_time + dt.timedelta(seconds=self.m_start)).replace(microsecond=0).isoformat(),
            "duration_s": round(self.m_duration, 2),
            "person_s": round(float(np.sum(self.m_people_series)) * self.interval, 1), "people_tracked": lived("person", 1.5),
            "peak_people": int(np.percentile(self.m_people_series, 98)), "vehicles_tracked": lived("vehicle", 1.0),
            "ppe_compliant_s": round(self.m_compliant, 1), "ppe_noncompliant_s": round(self.m_noncompliant, 1),
            "grid": {"cols": GRID[0], "rows": GRID[1], "person": [[round(float(v), 1) for v in s] for s in self.m_grid["person"]],
                     "vehicle": [[round(float(v), 1) for v in s] for s in self.m_grid["vehicle"]]},
            "tracks": tracks,
        }
        self._reset_measurement()
        return out

    # ---------------------------------------------------------------- helpers
    def _speed(self, key, window=0.6):
        g = self.history[key]
        if len(g) < 2:
            return 0.0, (0.0, 0.0), g[-1][3] if g else 1.0
        last = g[-1]
        first = next((x for x in g if last[0] - x[0] <= window), g[0])
        if last[0] - first[0] < 0.25:
            first = g[max(0, len(g) - 4)]
        d = last[0] - first[0]
        if d <= 0:
            return 0.0, (0.0, 0.0), last[3]
        vx, vy = (last[1] - first[1]) / d, (last[2] - first[2]) / d
        return math.hypot(vx, vy), (vx, vy), float(np.median([x[3] for x in g if last[0] - x[0] <= window + 0.01]) or last[3])

    def _rule_zones(self, k: dict) -> list[dict]:
        bs = self.zones_by_type.get(k.get("zone_type", ""), [])
        return [b for b in bs if b["name"] == k["zone"]] if k.get("zone") else bs

    def _ppe(self, people: list[dict], objects: list[dict]) -> None:
        """Writes the protection seen on each person in this frame: p["ppe"] = {"hard_hat": True (worn) | False (seen not worn)}."""
        for p in people:
            p["ppe"] = {}
            p["parts"] = body_parts(p["b"], p.get("k"))
        for n in objects:
            negative = n["s"].endswith(MISSING_SUFFIX)
            base = n["s"][:-len(MISSING_SUFFIX)] if negative else n["s"]
            part = BODY_PART.get(base)
            if part is None:
                continue
            mx, my = (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2
            best, best_d = None, 1e9
            for p in people:
                x1, y1, x2, y2 = p["parts"][part]
                if x1 <= mx <= x2 and y1 <= my <= y2:
                    d = math.hypot(mx - (x1 + x2) / 2, my - (y1 + y2) / 2)
                    if d < best_d:
                        best, best_d = p, d
            if best is None:
                continue
            if negative:
                if n["p"] >= NEGATIVE_CONFIDENCE:
                    best["ppe"].setdefault(base, False)
            else:
                best["ppe"][base] = True  # "worn" evidence wins over "not worn" evidence
        for p in people:  # person attribute model (apron, coveralls, sleeve guards, harness, hearing protection)
            for s, probability in (p.get("attrs") or {}).items():
                if probability >= ATTR_PRESENT:
                    p["ppe"][s] = True
                elif probability <= ATTR_ABSENT:
                    p["ppe"].setdefault(s, False)

    # ---------------------------------------------------------------- frame
    def step(self, t: float, detections: list[dict], bgr: np.ndarray | None = None) -> dict:
        """Returns: {"people", "vehicles", "highlights": [...], "started": [event], "ended": [event], "open": [event]}."""
        W, H = self.W, self.H
        now = self.start_time + dt.timedelta(seconds=t)
        dtk = self.interval if self.last_t is None else min(1.0, max(0.0, t - self.last_t))
        self.last_t = t
        people = self.person_tracker.update(t, [dict(d) for d in detections if d["s"] == "person"])
        vehicles = self.vehicle_tracker.update(t, [dict(d) for d in detections if d["s"] in VEHICLES])
        objects = [d for d in detections if d["s"] != "person" and d["s"] not in VEHICLES]
        standing = self.object_tracker.update(t, [dict(d) for d in detections if d["s"] in OBJECTS])
        hsv = None
        self._ppe(people, objects)
        for p in people:
            p["a"] = foot_point(p["b"], p.get("k"))
            p["h"] = max(1.0, p["b"][3] - p["b"][1])
        for a in vehicles:
            a["a"] = ((a["b"][0] + a["b"][2]) / 2, float(a["b"][3]))
            a["h"] = max(1.0, a["b"][3] - a["b"][1])
        for p in people:  # a person inside a vehicle box is the driver: not counted as a pedestrian
            p["driver"] = any(same_box(p["b"], a["b"], iou=2.0, containment=0.6) and p["a"][1] <= a["b"][3] - 0.08 * a["h"]
                              and (p["b"][2] - p["b"][0]) * p["h"] < (a["b"][2] - a["b"][0]) * a["h"] for a in vehicles)
        pedestrians = [p for p in people if not p["driver"]]

        # --- counters, heat map, paths
        if self.m_start is None:
            self.m_start = t
        self.m_duration = t - self.m_start + self.interval
        self.m_people_series.append(len(people))
        for kind, items in (("person", people), ("vehicle", vehicles)):
            for o in items:
                key = (kind, o["id"])
                self.history[key].append((t, o["a"][0], o["a"][1], o["h"]))
                self.m_first.setdefault(key, t)
                self.m_last[key] = t
                gx = min(GRID[0] - 1, max(0, int(o["a"][0] / W * GRID[0])))
                gy = min(GRID[1] - 1, max(0, int(o["a"][1] / H * GRID[1])))
                self.m_grid[kind][gy, gx] += dtk
                if t - self.m_track_last.get(key, -9) >= 0.5:
                    self.m_track_last[key] = t
                    self.m_track_points[key].append([round(o["a"][0] / W, 4), round(o["a"][1] / H, 4)])

        box = lambda o, kind="person": {"b": o["b"], "kind": kind}  # noqa: E731
        position = lambda o: (o["a"][0] / W, o["a"][1] / H)  # noqa: E731
        ppe_zones = self.zones_by_type.get("ppe", [])
        started, ended, highlights = [], [], []
        for k in self.rules:
            mech, active = k["mechanism"], {}
            if mech == "ppe":
                cls = k["class"]
                # if the rule has its own zone type (safety harness: work-at-height area), only in those zones; otherwise in PPE-required areas
                areas = self.zones_by_type.get(k["zone_type"], []) if k.get("zone_type") else ppe_zones
                for p in pedestrians:
                    if p["h"] < PPE_MIN_HEIGHT or ((areas or k.get("zone_type")) and not any(inside(b["poly"], p["a"]) for b in areas)):
                        continue
                    state = p["ppe"].get(cls)
                    if state is None and k.get("evidence") == "absence" and p["h"] >= k.get("min_height", 90) and p.get("k"):
                        state = False  # the person is clearly visible but no protection was found
                    if state is None:
                        continue
                    if state:
                        self.m_compliant += dtk
                    else:
                        self.m_noncompliant += dtk
                    g = self.ppe_history[(k["id"], p["id"])]
                    g.append((t, state))
                    recent = [d for tt, d in g if t - tt <= 2.0]
                    if len(recent) >= 3 and np.mean([not d for d in recent]) >= 0.7:
                        zone = next((b["name"] for b in areas if inside(b["poly"], p["a"])), None)
                        active[p["id"]] = {"xy": position(p), "boxes": [box(p)], "zone": zone}
            elif mech == "posture":  # from the pose: person lying on the ground (trunk near horizontal, box wider than tall)
                for p in pedestrians:
                    if not p.get("k") or p["h"] < 30:
                        continue
                    angle = torso_angle(p["k"])
                    if angle is not None and angle >= 60 and (p["b"][2] - p["b"][0]) >= 1.1 * p["h"]:
                        active[p["id"]] = {"xy": position(p), "boxes": [box(p)]}
            elif mech == "ergonomics":  # REBA score from the pose: if the median of the last 3 seconds is above the threshold
                for p in pedestrians:
                    if not p.get("k") or p["h"] < PPE_MIN_HEIGHT:
                        continue
                    score = reba(p["k"])
                    if score is None:
                        continue
                    g = self.ppe_history[("reba", p["id"])]
                    g.append((t, score))
                    recent = [d for tt, d in g if t - tt <= 3.0]
                    if len(recent) >= 6 and float(np.median(recent)) >= k["threshold"]:
                        active[p["id"]] = {"xy": position(p), "boxes": [box(p)], "extra": int(np.median(recent))}
            elif mech == "zone":
                if not k.get("hours") or _in_hours(now, k["hours"]):
                    subjects = pedestrians if k.get("subject", "person") == "person" else vehicles
                    by_color = bool(k.get("authorized_color"))
                    if by_color and (not self.authorized_colors or bgr is None):
                        subjects = []  # without an authorized color nobody can be called unauthorized
                    for b in self._rule_zones(k):
                        for o in subjects:
                            if not inside(b["poly"], o["a"]):
                                continue
                            if by_color:  # a person whose clothing shows the authorized color raises no alert
                                if hsv is None:
                                    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                                g = self.color_history[o["id"]]
                                g.append(color.score(hsv, o["b"], o.get("k") or EMPTY_POSE, self.authorized_colors) > 1.0)
                                if len(g) < 4 or np.mean(g) >= 0.35:
                                    continue
                            active[(b["name"], o["id"])] = {"xy": position(o), "boxes": [box(o, k.get("subject", "person"))], "zone": b["name"]}
            elif mech == "count":
                for b in self._rule_zones(k):
                    inside_zone = [p for p in people if inside(b["poly"], p["a"])]
                    n = len(inside_zone)
                    if ("max" in k and n > k["max"]) or ("min" in k and n < k["min"]):
                        m = b["poly"].mean(0)
                        active[b["name"]] = {"xy": (float(m[0]) / W, float(m[1]) / H), "people": max(1, n), "boxes": [box(p) for p in inside_zone],
                                             "zone": b["name"], "extra": n}
            elif mech == "co_presence":
                for b in self._rule_zones(k):
                    ps = [p for p in pedestrians if inside(b["poly"], p["a"])]
                    vs = [a for a in vehicles if inside(b["poly"], a["a"])]
                    if ps and vs:
                        active[b["name"]] = {"xy": position(ps[0]), "people": len(ps), "zone": b["name"],
                                             "boxes": [box(p) for p in ps] + [box(a, "vehicle") for a in vs]}
            elif mech == "proximity":
                A = vehicles if k["a"] == "vehicle" else pedestrians
                B = vehicles if k["b"] == "vehicle" else pedestrians
                for x in A:
                    for y in B:
                        if x is y or (k["a"] == k["b"] and x["id"] >= y["id"]):
                            continue
                        if k["a"] == "vehicle" and k["b"] == "person":
                            v, _, _ = self._speed(("vehicle", x["id"]))
                            if v < 0.15 * x["h"]:  # in this rule a stationary vehicle is not a hazard to pedestrians
                                continue
                            dx = max(x["b"][0] - y["a"][0], 0.0, y["a"][0] - x["b"][2])
                            d = math.hypot(dx, y["a"][1] - x["b"][3])
                            unit = y["h"]
                        elif k["a"] == "vehicle":  # two vehicles: side gap between the boxes and depth difference on the ground
                            dx = max(0.0, x["b"][0] - y["b"][2], y["b"][0] - x["b"][2])
                            d = math.hypot(dx, x["b"][3] - y["b"][3])
                            unit = (x["h"] + y["h"]) / 2
                        else:
                            d = math.hypot(x["a"][0] - y["a"][0], x["a"][1] - y["a"][1])
                            unit = (x["h"] + y["h"]) / 2
                            if max(x["h"], y["h"]) > 1.6 * min(x["h"], y["h"]):  # one in front, one behind
                                continue
                        if d <= k["threshold"] * unit:
                            active[(x["id"], y["id"])] = {"xy": position(y), "people": 2 if k["a"] == "person" else 1,
                                                          "boxes": [box(x, k["a"]), box(y, k["b"])]}
            elif mech == "object":  # object standing in place: in a zone (obstruction, prohibited object) or with nobody near it (abandoned)
                zones = self._rule_zones(k) if k.get("zone_type") else None
                for n in standing:
                    if n["s"] not in k.get("classes", ()):
                        continue
                    cx, bottom = (n["b"][0] + n["b"][2]) / 2, n["b"][3]
                    size = max(n["b"][2] - n["b"][0], n["b"][3] - n["b"][1], 1.0)
                    if n["s"] in HELD:  # hand-held object: its place is where the holder stands; since it is held, "standing in place" is not required
                        holder = next((p for p in people if p["b"][0] <= cx <= p["b"][2] and p["b"][1] <= (n["b"][1] + bottom) / 2 <= p["b"][3]), None)
                        if holder is None:
                            continue
                        zone = next((b for b in zones or [] if inside(b["poly"], holder["a"])), None)
                        if zones is not None and zone is None:
                            continue
                        active[(k["id"], "person", holder["id"])] = {"xy": position(holder), "people": 1, "zone": zone["name"] if zone else None,
                                                                     "boxes": [{"b": n["b"], "kind": "object"}, box(holder)]}
                        continue
                    first = self.object_first.get(n["id"])
                    if first is None or math.hypot(cx - first[0], bottom - first[1]) > 0.25 * size:
                        self.object_first[n["id"]] = (cx, bottom)  # new, or moved
                        continue
                    zone = None
                    if zones is not None:
                        zone = next((b for b in zones if inside(b["poly"], (cx, bottom))), None)
                        if zone is None:
                            continue
                    if k.get("unattended") and any(math.hypot(p["a"][0] - cx, p["a"][1] - bottom) < 1.5 * max(p["h"], size) for p in people):
                        continue
                    active[(k["id"], n["id"])] = {"xy": (cx / W, bottom / H), "people": 1, "boxes": [{"b": n["b"], "kind": "object"}],
                                                  "zone": zone["name"] if zone else None}
            elif mech == "three_point":  # fewer than three of the hands and feet of the person on the ladder are over the ladder
                # ladders: drawn "ladder" zones (fixed) and ladders found by the add-on model (portable; extended upward to cover the person on it)
                areas = [(b["name"], b["poly"], None) for b in self.zones_by_type.get("ladder", []) if not k.get("zone") or b["name"] == k["zone"]]
                for n in objects:
                    if n["s"] == "ladder":
                        x1, y1, x2, y2 = n["b"]
                        w, h = x2 - x1, y2 - y1
                        areas.append((None, np.array([[x1 - 0.1 * w, y1 - 0.35 * h], [x2 + 0.1 * w, y1 - 0.35 * h], [x2 + 0.1 * w, y2], [x1 - 0.1 * w, y2]],
                                                     dtype=np.float32), y2 - 0.15 * h))
                loads = [n["b"] for n in objects if n["s"] in LOADS]
                full = lambda u: any(b[0] - 0.1 * (b[2] - b[0]) <= u[0] <= b[2] + 0.1 * (b[2] - b[0])  # noqa: E731
                                     and b[1] - 0.1 * (b[3] - b[1]) <= u[1] <= b[3] + 0.1 * (b[3] - b[1]) for b in loads)
                for name, poly, base_y in areas:
                    for p in pedestrians:
                        kp = p.get("k")
                        if not kp:
                            continue
                        hip = _mid(kp, (11, 12))
                        if hip is None or not inside(poly, hip):
                            continue
                        feet = [kp[i] for i in (15, 16) if kp[i][2] >= KP]
                        if base_y is not None and (not feet or max(u[1] for u in feet) > base_y):
                            continue  # feet at the foot of the ladder: standing on the ground
                        hands = [kp[i] for i in (9, 10) if kp[i][2] >= KP]
                        if len(hands) + len(feet) < 3:
                            continue
                        contact = sum(1 for u in feet if inside(poly, (u[0], u[1]))) + sum(1 for u in hands if inside(poly, (u[0], u[1])) and not full(u))
                        if contact < 3:
                            active[(name or "ladder", p["id"])] = {"xy": position(p), "boxes": [box(p)], "zone": name}
            elif mech == "speed":
                for p in pedestrians:
                    v, _, height = self._speed(("person", p["id"]))
                    if v / height >= k["threshold"] and len(self.history[("person", p["id"])]) >= 4:
                        active[p["id"]] = {"xy": position(p), "boxes": [box(p)]}
            elif mech == "off_lane":
                lane = self._rule_zones(k)
                if lane and k.get("subject", "person") == "person":
                    vehicle_lanes = self.zones_by_type.get("vehicle_lane", [])
                    for p in pedestrians:
                        if any(inside(b["poly"], p["a"]) for b in lane):
                            continue
                        b = next((b for b in vehicle_lanes if inside(b["poly"], p["a"])), None)
                        if vehicle_lanes and b is None:
                            continue  # if vehicle lanes are drawn, only a pedestrian walking in a vehicle lane raises an alert
                        active[p["id"]] = {"xy": position(p), "boxes": [box(p)], "zone": b["name"] if b else None}
                elif lane:
                    for a in vehicles:
                        if not any(inside(b["poly"], a["a"]) for b in lane):
                            active[a["id"]] = {"xy": position(a), "boxes": [box(a, "vehicle")]}
            elif mech == "wrong_way":
                subject = k.get("subject", "vehicle")
                for b in self._rule_zones(k):
                    if b["direction"] is None:
                        continue
                    yx, yy = math.cos(math.radians(b["direction"])), math.sin(math.radians(b["direction"]))
                    for o in (vehicles if subject == "vehicle" else pedestrians):
                        if not inside(b["poly"], o["a"]):
                            continue
                        v, (vx, vy), height = self._speed((subject, o["id"]), 1.0)
                        if v >= 0.25 * height and (vx * yx + vy * yy) / v <= -0.5:
                            active[(b["name"], o["id"])] = {"xy": position(o), "boxes": [box(o, subject)], "zone": b["name"]}
            st, en, hl = self.managers[k["id"]].update(t, active)
            for v in hl:
                v["label"] = T(k["name"])
            highlights += hl
            started += [self._event(k, o, t) for o in st]
            ended += [self._event(k, o, o["last"], ended=True) for o in en]
        open_events = [o for m in self.managers.values() for o in m.open]
        return {"people": people, "vehicles": vehicles, "highlights": highlights, "started": started, "ended": ended, "open": open_events}

    def _event(self, k: dict, o: dict, t: float, ended: bool = False) -> dict:
        duration = max(self.interval, t - o["start"] + self.interval)
        likelihood = catalog.likelihood(duration, o["seq"])
        text = T(k["description"])
        if k["mechanism"] == "ergonomics" and o.get("extra") is not None:
            text = T("REBA posture score {score} (threshold {threshold}). ", score=o["extra"], threshold=k["threshold"]) + T(k["description"])
        elif k["mechanism"] == "count" and o.get("extra") is not None:
            limit = T("at most {n}", n=k["max"]) if "max" in k else T("at least {n}", n=k["min"])
            if o["extra"] == 1:
                text = T("{zone}: {n} person seen (limit: {limit}).", zone=o["zone"], n=o["extra"], limit=limit)
            else:
                text = T("{zone}: {n} people seen (limit: {limit}).", zone=o["zone"], n=o["extra"], limit=limit)
        return {
            "uid": o["uid"], "ended": ended, "rule": k["id"], "rule_name": T(k["name"]), "category": k["category"], "zone": o["zone"],
            "severity": int(k["severity"]), "likelihood": likelihood, "score": catalog.risk(k["severity"], likelihood),
            "serious": int(bool(k.get("serious"))),
            "time": (self.start_time + dt.timedelta(seconds=o["start"])).replace(microsecond=0).isoformat(), "start_s": round(o["start"], 2),
            "duration_s": round(duration, 1), "people": int(max(1, o["people"])), "x": round(float(o["m"][0]), 4), "y": round(float(o["m"][1]), 4),
            "description": text,
        }

    def close(self) -> list[dict]:
        """Closes the events still open (when the stream ends)."""
        ended = []
        for k in self.rules:
            ended += [self._event(k, o, o["last"], ended=True) for o in self.managers[k["id"]].finish()]
        return ended
