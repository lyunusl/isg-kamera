"""Pose (skeleton) estimation: every person box is cropped, enlarged and passed through the pose model.

Pose models that run on the full frame miss people who look small in a wide view; detecting the person first and then
looking at the enlarged box (top-down method) gives the joints of distant people too. Uses only numpy, OpenCV and onnxruntime.

17 points (COCO order): 0 nose, 1-2 eyes, 3-4 ears, 5-6 shoulders, 7-8 elbows, 9-10 wrists, 11-12 hips,
13-14 knees, 15-16 ankles. Each point is [x, y, confidence].
"""
from __future__ import annotations

import os

import cv2
import numpy as np

BONES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
MIN_HEIGHT = 36   # no pose is looked for in a person shorter than this (pixels)
MARGIN = 0.12     # margin added around the box (arms and legs may stick out)


class PoseEstimator:
    def __init__(self, path: str, data_dir: str | None = None):
        """`data_dir` is the data folder used for package activation."""
        import onnxruntime as ort

        from .model_session import open_session

        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")  # several models should not steal the CPU from each other
        self.session = open_session(path, data_dir, options, ["CPUExecutionProvider"])
        g = self.session.get_inputs()[0]
        self.input_name = g.name
        self.h, self.w = (int(g.shape[2]), int(g.shape[3])) if isinstance(g.shape[2], int) else (384, 256)
        self.batched = not isinstance(g.shape[0], int)  # whether the batch size is free
        self.name = os.path.basename(path)

    def _prepare(self, bgr: np.ndarray, box):
        """Crops the person box and fits it into the model input. Returns: (input, scale, dx, dy, xa, ya, yw, yh) or None."""
        H, W = bgr.shape[:2]
        x1, y1, x2, y2 = box
        bw, bh = x2 - x1, y2 - y1
        if bh < MIN_HEIGHT or bw < 8:
            return None
        xa, ya = int(max(0, x1 - MARGIN * bw)), int(max(0, y1 - MARGIN * bh))
        xb, yb = int(min(W, x2 + MARGIN * bw)), int(min(H, y2 + MARGIN * bh))
        crop = bgr[ya:yb, xa:xb]
        kh, kw = crop.shape[:2]
        if kh < 8 or kw < 8:
            return None
        o = min(self.w / kw, self.h / kh)
        yw, yh = max(1, int(round(kw * o))), max(1, int(round(kh * o)))
        canvas = np.full((self.h, self.w, 3), 114, np.uint8)
        dx, dy = (self.w - yw) // 2, (self.h - yh) // 2
        canvas[dy:dy + yh, dx:dx + yw] = cv2.resize(crop, (yw, yh), interpolation=cv2.INTER_LINEAR)
        x = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return x, o, dx, dy, xa, ya, yw, yh

    def _decode(self, y: np.ndarray, o, dx, dy, xa, ya, yw, yh) -> list | None:
        """Picks the crop's main person from the model output (candidate, 4 + 1 + 51) and maps the points back to the image."""
        if not len(y):
            return None
        # the crop may contain several people: the one closest to the center of the crop and confident enough is chosen
        confidence = y[:, 4]
        ok = np.flatnonzero(confidence >= 0.25)
        if not len(ok):
            return None
        center = np.hypot(y[ok, 0] - self.w / 2, y[ok, 1] - self.h / 2) / max(self.w, self.h)
        area = (y[ok, 2] * y[ok, 3]) / (yw * yh)
        i = ok[int(np.argmax(confidence[ok] + 0.5 * np.minimum(area, 1.0) - center))]
        k = y[i, 5:].reshape(17, 3)
        return [[round(float((p[0] - dx) / o + xa), 1), round(float((p[1] - dy) / o + ya), 1), round(float(p[2]), 2)] for p in k]

    def person(self, bgr: np.ndarray, box) -> list | None:
        """The 17 points of a person box (image pixels), or None if not found."""
        h = self._prepare(bgr, box)
        if h is None:
            return None
        y = self.session.run(None, {self.input_name: np.ascontiguousarray(h[0][None])})[0][0].T
        return self._decode(y, *h[1:])

    def run(self, bgr: np.ndarray, detections: list[dict], max_people: int = 12) -> int:
        """Adds the "k" field (17 points) to the person detections; people are processed in one batch. Returns: the number of people with a pose."""
        people = sorted((d for d in detections if d["s"] == "person"), key=lambda d: -(d["b"][3] - d["b"][1]))[:max_people]
        ready = [(d, self._prepare(bgr, d["b"])) for d in people]
        ready = [(d, h) for d, h in ready if h is not None]
        if not ready:
            return 0
        if self.batched:
            output = self.session.run(None, {self.input_name: np.ascontiguousarray(np.stack([h[0] for _, h in ready]))})[0]
        else:
            output = [self.session.run(None, {self.input_name: np.ascontiguousarray(h[0][None])})[0][0] for _, h in ready]
        n = 0
        for (d, h), y in zip(ready, output):
            k = self._decode(y.T, *h[1:])
            if k is not None:
                d["k"] = k
                n += 1
        return n


def draw(fr: np.ndarray, k: list, color=(255, 200, 60), threshold: float = 0.4) -> None:
    for a, b in BONES:
        if k[a][2] >= threshold and k[b][2] >= threshold:
            cv2.line(fr, (int(k[a][0]), int(k[a][1])), (int(k[b][0]), int(k[b][1])), color, 2, cv2.LINE_AA)
    for p in k[5:]:
        if p[2] >= threshold:
            cv2.circle(fr, (int(p[0]), int(p[1])), 3, color, -1, cv2.LINE_AA)
