"""Marks up the image (faces masked), writes event clips and drops the events into the queue folder the panel reads.

Queue: <data>/queue/*.json  ·  clips and snapshots: <data>/output/<camera>/
Also works while the panel is closed; when the panel opens it takes in the queued files in order.
Texts drawn into frames arrive already translated (rule names from rule_engine.py, the top and bottom lines from blocks.py);
the Segoe UI / Arial fonts used below render both English and Turkish.

Queue record: {"type": "event" | "measurement" | "done" | "error" | "heartbeat", "camera": code, "run": run, ...}
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque

import cv2
import numpy as np

from . import pose
from .i18n import sample_name
from .rule_engine import head_region

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # without Pillow in Studio, texts are drawn with OpenCV and Turkish letters are simplified
    Image = None

COLOR_EVENT = (15, 68, 240)      # BGR
COLOR_OTHER = (175, 160, 150)
COLOR_ZONE = (60, 190, 255)
_ASCII_FOLD = str.maketrans("çğıöşüÇĞİÖŞÜ–·", "cgiosuCGIOSU-.")
_FONT: dict = {}


def _font(size: int):
    if size not in _FONT:
        for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
            try:
                _FONT[size] = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name), size)
                break
            except OSError:
                continue
        else:
            _FONT[size] = ImageFont.load_default()
    return _FONT[size]


def pixelate(fr: np.ndarray, cx: float, cy: float, rx: float, ry: float) -> None:
    H, W = fr.shape[:2]
    xa, xb = int(max(0, cx - rx)), int(min(W, cx + rx))
    ya, yb = int(max(0, cy - ry)), int(min(H, cy + ry))
    if xb - xa < 4 or yb - ya < 4:
        return
    roi = fr[ya:yb, xa:xb]
    block = max(5, int(rx / 2.2))
    small = cv2.resize(roi, (max(2, (xb - xa) // block), max(2, (yb - ya) // block)), interpolation=cv2.INTER_AREA)
    large = cv2.resize(small, (xb - xa, yb - ya), interpolation=cv2.INTER_NEAREST)
    m = np.zeros(roi.shape[:2], np.uint8)
    cv2.ellipse(m, (int(cx - xa), int(cy - ya)), (int(rx), int(ry)), 0, 0, 360, 255, -1)
    roi[m > 0] = large[m > 0]


FACE_HOLD_S = 1.0  # if detection misses, the last seen head region stays masked for this long
FACE_GROWTH = 1.0  # the kept region grows to (1 + FACE_GROWTH) times its size over that time: the person may have moved a little


def enqueue(data_dir: str, code: str, record: dict) -> None:
    """Drops a single record into the queue the panel reads (usable before the camera's writer is set up)."""
    folder = os.path.join(data_dir, "queue")
    os.makedirs(folder, exist_ok=True)
    name = f"{time.time_ns():020d}_{code}_h"
    tmp = os.path.join(folder, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, name + ".json"))


FACE_COVER = 0.8     # a kept region is released once at least this share of its area is covered by the current masks
_SAMPLE_POINTS = [(math.sqrt((i + 0.5) / 48) * math.cos(i * 2.399963), math.sqrt((i + 0.5) / 48) * math.sin(i * 2.399963))
                  for i in range(48)]  # points spread evenly over the unit disk


def _covered(old, current) -> bool:
    """Whether at least FACE_COVER of the old mask's area is covered by the union of the current masks (measured with 48 sample points)."""
    n = 0
    for ux, uy in _SAMPLE_POINTS:
        x, y = old[0] + ux * old[2], old[1] + uy * old[3]
        if any(((x - s[0]) / max(s[2], 1e-6)) ** 2 + ((y - s[1]) / max(s[3], 1e-6)) ** 2 <= 1.0 for s in current):
            n += 1
    return n >= FACE_COVER * len(_SAMPLE_POINTS)


def face_regions(detections: list[dict]) -> list[tuple[float, float, float, float]]:
    """Ellipses to mask (cx, cy, rx, ry): the head region of person boxes and the face below head/hard-hat boxes found by the model."""
    out = []
    for d in detections:
        b = d["b"]
        w, h = b[2] - b[0], b[3] - b[1]
        if d["s"] == "person":
            out.append(tuple(head_region(b, d.get("k"))))
        elif d["s"] in ("hard_hat_missing", "safety_glasses", "safety_glasses_missing", "face_mask", "face_mask_missing"):  # the box is the head itself
            out.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(0.6 * w, 7), max(0.65 * h, 8)))
        elif d["s"] == "hard_hat":  # the face is right below the hard hat
            out.append(((b[0] + b[2]) / 2, b[3] + 0.35 * h, max(0.55 * w, 7), max(0.75 * h, 8)))
    return out


def mask_faces(fr: np.ndarray, detections: list[dict], memory: dict | None = None, t: float | None = None) -> None:
    """Pixelates head regions.

    If `memory` (the same dict for the whole stream) and `t` (seconds) are given, head regions masked within the last
    FACE_HOLD_S stay masked as long as at least FACE_COVER of their area is not covered by this frame's masks: if a person
    is not detected for a frame or two, or the new mask does not cover the old region well enough, the face is not exposed
    (a walking person leaves a one-second trail). A kept region does not follow the person; it only grows over time.
    The face of a person who was never detected cannot be masked."""
    current = face_regions(detections)
    old = []
    if memory is not None and t is not None:
        old = [(t0, e) for t0, e in memory.get("faces", []) if 0 <= t - t0 <= FACE_HOLD_S and not _covered(e, current)]
        memory["faces"] = old + [(t, s) for s in current]
    for e in current:
        pixelate(fr, *e)
    for t0, e in old:
        k = 1.0 + FACE_GROWTH * (t - t0) / FACE_HOLD_S
        pixelate(fr, e[0], e[1], e[2] * k, e[3] * k)


def draw(fr: np.ndarray, result: dict, detections: list[dict], zones: list[dict], top: str, bottom: str,
         memory: dict | None = None, t: float | None = None) -> np.ndarray:
    """Masks faces; draws zones, people and the violations going on at the moment. `fr` is changed in place. `memory`, `t`: mask_faces."""
    mask_faces(fr, detections, memory, t)
    highlighted = {tuple(v["b"]) for v in result["highlights"]}
    for b in zones:
        cv2.polylines(fr, [b["poly"].astype(np.int32)], True, COLOR_ZONE, 2, cv2.LINE_AA)
    for p in result["people"]:
        if p.get("k"):
            pose.draw(fr, p["k"], (235, 200, 120))
    for p in result["people"] + result["vehicles"]:
        if tuple(p["b"]) not in highlighted:
            x1, y1, x2, y2 = (int(v) for v in p["b"])
            cv2.rectangle(fr, (x1, y1), (x2, y2), COLOR_OTHER, 1, cv2.LINE_AA)
    labels = []
    for b in zones:
        labels.append((int(b["poly"][:, 0].min()) + 6, int(b["poly"][:, 1].min()) + 6, sample_name(b["name"]), COLOR_ZONE, (20, 20, 20), 15))
    box_labels: dict = {}  # violations on the same box go on one line
    for v in result["highlights"]:
        x1, y1, x2, y2 = (int(q) for q in v["b"])
        cv2.rectangle(fr, (x1, y1), (x2, y2), COLOR_EVENT, 3, cv2.LINE_AA)
        names = box_labels.setdefault((x1, y1), [])
        if v.get("label") and v["label"] not in names:
            names.append(v["label"])
    for (x1, y1), names in box_labels.items():
        labels.append((x1, max(2, y1 - 26), " · ".join(names), COLOR_EVENT, (255, 255, 255), 16))
    if top:
        labels.append((12, 10, top, COLOR_EVENT if result["open"] else (40, 30, 24), (255, 255, 255), 20))
    labels.append((12, fr.shape[0] - 34, bottom, (20, 14, 12), (240, 230, 225), 15))
    return _draw_labels(fr, labels)


def _draw_labels(fr: np.ndarray, labels: list) -> np.ndarray:
    seen = set()
    if Image is None:
        for x, y, text, bg, fg, size in labels:
            if not text or (x // 8, y // 8, text) in seen:
                continue
            seen.add((x // 8, y // 8, text))
            m = text.translate(_ASCII_FOLD)
            (w, h), _ = cv2.getTextSize(m, cv2.FONT_HERSHEY_SIMPLEX, size / 30, 1)
            cv2.rectangle(fr, (x, y), (x + w + 12, y + h + 12), bg, -1)
            cv2.putText(fr, m, (x + 6, y + h + 5), cv2.FONT_HERSHEY_SIMPLEX, size / 30, fg, 1, cv2.LINE_AA)
        return fr
    im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    canvas = ImageDraw.Draw(im)
    for x, y, text, bg, fg, size in labels:
        if not text or (x // 8, y // 8, text) in seen:
            continue
        seen.add((x // 8, y // 8, text))
        f = _font(size)
        l, t, r, b = canvas.textbbox((0, 0), text, font=f)
        w, h = r - l, b - t
        x = min(max(4, x), max(4, im.size[0] - w - 18))
        canvas.rounded_rectangle([x, y, x + w + 14, y + h + 10], radius=6, fill=bg[::-1])
        canvas.text((x + 7, y + 4 - t), text, font=f, fill=fg[::-1])
    fr[:] = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    return fr


class Recorder:
    """Clip and queue writer of one camera."""

    def __init__(self, data_dir: str, code: str, camera_name: str, W: int, H: int, fps: float):
        self.code, self.camera_name, self.W, self.H = code, camera_name, W, H
        self.run = ""  # number of the run (set by the block): the panel separates records by run
        self.fps = max(1.0, min(30.0, fps))
        self.output_dir = os.path.join(data_dir, "output", code)
        self.queue_dir = os.path.join(data_dir, "queue")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.queue_dir, exist_ok=True)
        self.buffer: deque = deque(maxlen=max(2, int(1.5 * self.fps)))  # the last 1.5 s before the event (JPEG)
        self.writers: dict[str, dict] = {}
        self.counter = 0

    def _enqueue(self, record: dict) -> None:
        self.counter += 1
        if self.run:
            record = {**record, "run": self.run}
        name = f"{time.time_ns():020d}_{self.code}_{self.counter:05d}"
        tmp = os.path.join(self.queue_dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.queue_dir, name + ".json"))

    def frame(self, t: float, drawn: np.ndarray, result: dict) -> None:
        """Called for every marked-up frame: opens clips for starting events, writes ongoing ones, closes finished ones."""
        for o in result["started"]:
            stem = f"{o['time'].replace('-', '').replace(':', '').replace('T', '_')}_{o['uid'][:8]}_{o['rule']}"
            snapshot_path = os.path.join(self.output_dir, stem + ".jpg")
            cv2.imwrite(snapshot_path, drawn, [cv2.IMWRITE_JPEG_QUALITY, 82])
            w = {"writer": None, "start": t, "end": None, "raw": os.path.join(self.output_dir, stem + ".raw.mp4"),
                 "snapshot": f"{self.code}/{stem}.jpg", "n": 0}
            if len(self.writers) < 6:  # at most six clips at a time
                w["writer"] = cv2.VideoWriter(w["raw"], cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (self.W, self.H))
                for _, jpg in self.buffer:
                    w["writer"].write(cv2.imdecode(jpg, cv2.IMREAD_COLOR))
                    w["n"] += 1
            self.writers[o["uid"]] = w
            self._enqueue({"type": "event", "camera": self.code, "camera_name": self.camera_name, **o, "snapshot": w["snapshot"], "clip": None})
        ok, jpg = cv2.imencode(".jpg", drawn, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.buffer.append((t, jpg))
        for o in result["ended"]:
            w = self.writers.get(o["uid"])
            if w is not None:
                w["end"], w["event"] = t, o
            else:
                self._enqueue({"type": "event", "camera": self.code, "camera_name": self.camera_name, **o, "snapshot": None, "clip": None})
        for uid, w in list(self.writers.items()):
            if w["writer"] is not None and t - w["start"] <= 15.0:
                w["writer"].write(drawn)
                w["n"] += 1
            if w["end"] is not None and (t - w["end"] >= 1.5 or t - w["start"] > 15.0):
                self._close(uid)

    def _close(self, uid: str) -> None:
        w = self.writers.pop(uid)
        clip = None
        if w["writer"] is not None:
            w["writer"].release()
            if w["n"] >= 2 and os.path.exists(w["raw"]):
                clip = f"{self.code}/{os.path.basename(w['raw'])}"
        if w.get("event") is not None:
            self._enqueue({"type": "event", "camera": self.code, "camera_name": self.camera_name, **w["event"], "snapshot": w["snapshot"], "clip": clip})

    def measurement(self, o: dict, last_frame: np.ndarray | None) -> None:
        if last_frame is not None:
            cv2.imwrite(os.path.join(self.output_dir, "last_frame.jpg"), last_frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            o["last_frame"] = f"{self.code}/last_frame.jpg"
        self._enqueue({"type": "measurement", "camera": self.code, "camera_name": self.camera_name, **o})

    def finish(self, remaining_events: list[dict], done_marker: bool = True) -> None:
        """The stream ended: closes open clips, drops the closed events and the 'done' marker into the queue."""
        for o in remaining_events:
            w = self.writers.get(o["uid"])
            if w is not None:
                w["event"] = o
            else:
                self._enqueue({"type": "event", "camera": self.code, "camera_name": self.camera_name, **o, "snapshot": None, "clip": None})
        for uid in list(self.writers):
            if self.writers[uid].get("event") is None:  # end not reported: release the clip, the event stays open
                self.writers[uid]["event"] = None
            self._close(uid)
        self.buffer.clear()
        if done_marker:
            self._enqueue({"type": "done", "camera": self.code})
