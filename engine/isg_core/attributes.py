"""Person attributes: every person box is cropped and passed through a small classifier; which protection the person
wears (apron, one-piece protective coveralls, sleeve guards, full-body safety harness, hearing protection) comes back as
probabilities.

Since object detection cannot find this protection reliably (aprons and coveralls cover most of the body, harnesses are
thin straps), the question "does this person wear it" is asked on the person crop. Uses only numpy, OpenCV and onnxruntime.
AI-generated images were also used to train the attribute model.

The shipped model's metadata carries Turkish class names (the model is encrypted and is not changed); they are mapped to
the class codes of this program with MODEL_CLASS_MAP. Models with English class names are accepted as they are.
"""
from __future__ import annotations

import ast
import os

import cv2
import numpy as np

CLASSES = ["apron", "coveralls", "sleeve_guards", "harness", "hearing_protection"]
# class names in the metadata of the shipped model -> class codes
MODEL_CLASS_MAP = {"onluk": "apron", "tulum": "coveralls", "kolluk": "sleeve_guards", "kemer": "harness", "kulaklik": "hearing_protection"}
INPUT_SIZE = (384, 192)   # height, width
MARGIN = 0.08             # margin added around the box
MIN_HEIGHT = 110          # a person shorter than this (pixels) is not evaluated


def crop(bgr: np.ndarray, box) -> np.ndarray | None:
    """Crops the person box with its margin and fits it into the INPUT_SIZE size without changing its aspect ratio (BGR, uint8)."""
    H, W = bgr.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if bh < MIN_HEIGHT or bw < 12:
        return None
    xa, ya = int(max(0, x1 - MARGIN * bw)), int(max(0, y1 - MARGIN * bh))
    xb, yb = int(min(W, x2 + MARGIN * bw)), int(min(H, y2 + MARGIN * bh))
    k = bgr[ya:yb, xa:xb]
    kh, kw = k.shape[:2]
    if kh < 8 or kw < 8:
        return None
    o = min(INPUT_SIZE[1] / kw, INPUT_SIZE[0] / kh)
    yw, yh = max(1, int(round(kw * o))), max(1, int(round(kh * o)))
    canvas = np.full((INPUT_SIZE[0], INPUT_SIZE[1], 3), 114, np.uint8)
    dx, dy = (INPUT_SIZE[1] - yw) // 2, (INPUT_SIZE[0] - yh) // 2
    canvas[dy:dy + yh, dx:dx + yw] = cv2.resize(k, (yw, yh), interpolation=cv2.INTER_AREA if o < 1 else cv2.INTER_LINEAR)
    return canvas


class AttributeClassifier:
    def __init__(self, path: str, data_dir: str | None = None):
        """`data_dir` is the data folder used for package activation."""
        import onnxruntime as ort

        from .model_session import open_session

        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = open_session(path, data_dir, options, ["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        meta = self.session.get_modelmeta().custom_metadata_map or {}
        names = ast.literal_eval(meta["names"]) if meta.get("names") else CLASSES
        self.classes = [MODEL_CLASS_MAP.get(n, n) for n in names]
        self.name = os.path.basename(path)

    def run(self, bgr: np.ndarray, detections: list[dict], max_people: int = 8) -> int:
        """Adds the "attrs" field to the person detections: {"apron": 0.93, ...}. The `max_people` largest people are processed in one batch.
        Returns: the number of people processed."""
        people = sorted((d for d in detections if d["s"] == "person"), key=lambda d: -(d["b"][3] - d["b"][1]))[:max_people]
        ready = [(d, crop(bgr, d["b"])) for d in people]
        ready = [(d, k) for d, k in ready if k is not None]
        if not ready:
            return 0
        x = np.stack([k[:, :, ::-1].transpose(2, 0, 1) for _, k in ready]).astype(np.float32) / 255.0
        y = self.session.run(None, {self.input_name: np.ascontiguousarray(x)})[0]
        for (d, _), row in zip(ready, y):
            d["attrs"] = {s: round(float(p), 2) for s, p in zip(self.classes, row)}
        return len(ready)
