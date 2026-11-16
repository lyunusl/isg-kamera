"""Authorized clothing colors (people allowed into a machine zone).

Workers are told apart from visitors by clothing color (no identity recognition). In the camera settings file:

  "authorized_colors": ["orange"]                            # one color
  "authorized_colors": ["black", "white"]                    # waiters in black, kitchen in white
  "authorized_colors": [{"color": "green", "threshold": 0.15}] # change the threshold of a preset color
  "authorized_colors": [{"name": "Company green", "hue": [[60, 80]], "saturation": [100, 255], "value": [60, 255], "threshold": 0.12, "region": "torso"}]

Color ranges are on the OpenCV HSV scale: hue 0-179, saturation and value 0-255.
"threshold": if at least this share of the pixels in the body region matches the color, the frame counts as "worker color".
"region": "apron" (shoulders to knees, for aprons) or "torso" (shoulders to hips, for shirts/T-shirts).
Color names ("name") are English source texts marked with N_; they are translated with T() where they are shown.
"""
from __future__ import annotations

import hashlib
import json

import warnings

import cv2
import numpy as np

from .i18n import T, N_

KP = 0.35

# weak=True: a color visitors often wear too; not reliable on its own
PRESETS: dict[str, dict] = {
    "orange": {"name": N_("Orange"), "hue": [[0, 11], [176, 179]], "saturation": [150, 255], "value": [110, 255], "threshold": 0.07, "region": "apron", "hex": "#ff7a1a"},
    "red": {"name": N_("Red"), "hue": [[0, 7], [170, 179]], "saturation": [130, 255], "value": [80, 255], "threshold": 0.10, "region": "torso", "hex": "#e0262b"},
    "maroon": {"name": N_("Maroon"), "hue": [[0, 8], [165, 179]], "saturation": [90, 255], "value": [30, 115], "threshold": 0.15, "region": "torso", "hex": "#7a1525"},
    # hi-vis vest yellow: the hue shifts from yellow toward green and looks pale in dim light (hue 26-45, saturation 60-140 in sample images)
    "hi_vis_yellow": {"name": N_("Fluorescent yellow (vest)"), "hue": [[24, 46]], "saturation": [60, 255], "value": [110, 255], "threshold": 0.06, "region": "torso", "hex": "#d4e600"},
    "yellow": {"name": N_("Yellow"), "hue": [[20, 34]], "saturation": [110, 255], "value": [120, 255], "threshold": 0.10, "region": "torso", "hex": "#f5c518"},
    "green": {"name": N_("Green"), "hue": [[38, 85]], "saturation": [80, 255], "value": [45, 255], "threshold": 0.10, "region": "torso", "hex": "#2f9e44"},
    "turquoise": {"name": N_("Turquoise"), "hue": [[85, 100]], "saturation": [90, 255], "value": [80, 255], "threshold": 0.10, "region": "torso", "hex": "#1fb5b0"},
    "blue": {"name": N_("Blue"), "hue": [[100, 124]], "saturation": [140, 255], "value": [90, 255], "threshold": 0.12, "region": "torso", "hex": "#1f6fe0"},
    "navy": {"name": N_("Navy blue"), "hue": [[105, 130]], "saturation": [80, 255], "value": [25, 110], "threshold": 0.25, "region": "torso", "hex": "#1b2a5c", "weak": True},
    "purple": {"name": N_("Purple"), "hue": [[125, 150]], "saturation": [70, 255], "value": [50, 255], "threshold": 0.10, "region": "torso", "hex": "#7b3fbf"},
    "pink": {"name": N_("Pink"), "hue": [[150, 172]], "saturation": [70, 255], "value": [120, 255], "threshold": 0.10, "region": "torso", "hex": "#e84a9a"},
    "black": {"name": N_("Black"), "hue": [[0, 179]], "saturation": [0, 255], "value": [0, 50], "threshold": 0.45, "region": "torso", "hex": "#151515", "weak": True},
    "white": {"name": N_("White"), "hue": [[0, 179]], "saturation": [0, 35], "value": [185, 255], "threshold": 0.40, "region": "torso", "hex": "#f4f4f4", "weak": True},
}


def resolve(items) -> list[dict]:
    """Turns the 'authorized_colors' list of the camera settings file into full definitions."""
    if not items:
        items = ["orange"]
    if isinstance(items, (str, dict)):
        items = [items]
    out = []
    for r in items:
        if isinstance(r, str):
            if r not in PRESETS:
                raise ValueError(T("Unknown color: {color}. Options: {options}", color=r, options=", ".join(PRESETS)))
            out.append({"id": r, **PRESETS[r]})
        else:
            base = PRESETS.get(r.get("color", ""), {})
            t = {"id": r.get("color", "custom"), "region": "torso", "threshold": 0.12, "name": N_("Custom"), **base,
                 **{k: v for k, v in r.items() if k != "color"}}
            for field in ("hue", "saturation", "value"):
                if field not in t:
                    raise ValueError(T("Custom color is missing '{field}': {color}", field=field, color=r))
            if t["hue"] and not isinstance(t["hue"][0], (list, tuple)):
                t["hue"] = [t["hue"]]
            out.append(t)
    return out


VERSION = 2  # when the color computation changes, cached scores are recomputed


def signature(colors: list[dict]) -> str:
    fields = [VERSION] + [{k: r[k] for k in ("hue", "saturation", "value", "threshold", "region")} for r in colors]
    return hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:12]


def mask(hsv: np.ndarray, r: dict) -> np.ndarray:
    """Pixels matching the color (whole frame or a part)."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = np.zeros(h.shape, bool)
    for a, b in r["hue"]:
        m |= (h >= a) & (h <= b)
    return m & (s >= r["saturation"][0]) & (s <= r["saturation"][1]) & (v >= r["value"][0]) & (v <= r["value"][1])


def background(frames: list, boxes: list) -> tuple[np.ndarray, np.ndarray]:
    """Median of the sample frames with the person boxes left out: tables, chairs, floor.
    Returns: (background HSV, mask of pixels never seen empty). Computed at half resolution."""
    H, W = frames[0].shape[:2]
    w2, h2 = W // 2, H // 2
    stack = np.full((len(frames), h2, w2, 3), np.nan, np.float32)
    for j, (fr, frame_boxes) in enumerate(zip(frames, boxes)):
        g = cv2.resize(fr, (w2, h2), interpolation=cv2.INTER_AREA).astype(np.float32)
        for x1, y1, x2, y2 in frame_boxes:
            g[max(0, int(y1 / 2)):int(y2 / 2) + 1, max(0, int(x1 / 2)):int(x2 / 2) + 1] = np.nan
        stack[j] = g
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        med = np.nanmedian(stack, 0)
    unseen = np.isnan(med).any(2)
    med = np.nan_to_num(med, nan=0).astype(np.uint8)
    med = cv2.resize(med, (W, H), interpolation=cv2.INTER_NEAREST)
    unseen = cv2.resize(unseen.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
    return cv2.cvtColor(med, cv2.COLOR_BGR2HSV), unseen


def background_masks(background_hsv: np.ndarray, unseen: np.ndarray, colors: list) -> list:
    """For each color, the background pixels that already have that color (wooden chair, orange wall...)."""
    return [mask(background_hsv, r) & ~unseen for r in colors]


def _roi(hsv: np.ndarray, box, kp, region: str):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    sh = [kp[i] for i in (5, 6) if kp[i][2] > KP]
    hips = [kp[i] for i in (11, 12) if kp[i][2] > KP]
    if len(sh) == 2 and len(hips) >= 1:
        xs = [p[0] for p in sh + hips]
        ya = min(p[1] for p in sh)
        yb = max(p[1] for p in hips)
        yb = yb + (0.8 if region == "apron" else 0.1) * (yb - ya)
        xa, xb = min(xs), max(xs)
    else:
        xa, xb = x1 + 0.25 * w, x2 - 0.25 * w
        ya, yb = (y1 + 0.22 * h, y1 + 0.82 * h) if region == "apron" else (y1 + 0.20 * h, y1 + 0.55 * h)
    H, W = hsv.shape[:2]
    xa, xb = int(max(0, xa)), int(min(W, xb))
    ya, yb = int(max(0, ya)), int(min(H, yb))
    if xb - xa < 4 or yb - ya < 4:
        return None
    return ya, yb, xa, xb


def ratio(hsv: np.ndarray, box, kp, r: dict, bg: np.ndarray | None = None) -> float:
    """Share of pixels in the body region that match this color. Scattered pixels (skin, wood edges) are removed with an
    opening; pixels that have this color in the background too (chair, table) are not counted."""
    k = _roi(hsv, box, kp, r["region"])
    if k is None:
        return 0.0
    ya, yb, xa, xb = k
    m = mask(hsv[ya:yb, xa:xb], r)
    if bg is not None:
        m &= ~bg[ya:yb, xa:xb]
    ks = max(3, (xb - xa) // 12) | 1
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))) > 0
    return float(m.mean())


def score(hsv: np.ndarray, box, kp, colors: list, backgrounds: list | None = None) -> float:
    """Above 1: in this frame the person wears one of the selected worker colors."""
    backgrounds = backgrounds or [None] * len(colors)
    return max(ratio(hsv, box, kp, r, a) / r["threshold"] for r, a in zip(colors, backgrounds))
