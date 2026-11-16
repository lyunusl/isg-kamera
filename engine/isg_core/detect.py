"""ONNX object detection (YOLO-style output): uses only numpy, OpenCV and onnxruntime.

The same code runs inside AugeLab Studio (the ISG Detection block) and in tests outside Studio.
The models are the .onnx files in the `models/` folder; class names are read from the file's own metadata.
"""
from __future__ import annotations

import ast
import os

import cv2
import numpy as np

from .i18n import T

# model class name (lower case) → code in this program. "_missing": that protection is clearly seen not being worn.
# The keys are the class names in the model metadata and must stay as they are.
CLASS_CODE = {
    "person": "person",
    "hardhat": "hard_hat", "helmet": "hard_hat", "no-hardhat": "hard_hat_missing", "no_helmet": "hard_hat_missing",
    "safety vest": "vest", "vest": "vest", "no-safety vest": "vest_missing", "no_vest": "vest_missing",
    "gloves": "gloves", "no-gloves": "gloves_missing", "no_gloves": "gloves_missing",
    "goggles": "safety_glasses", "no-goggles": "safety_glasses_missing", "no_goggle": "safety_glasses_missing",
    "boots": "safety_shoes", "no_boots": "safety_shoes_missing",
    "forklift": "forklift",
    # add-on model (open vocabulary): more protection and objects
    "apron": "apron", "face shield": "face_shield", "ear muffs": "hearing_protection", "coverall": "coveralls", "arm sleeves": "sleeve_guards",
    "pallet": "pallet", "cardboard box": "box", "barrel": "barrel", "ladder": "ladder", "cell phone": "phone", "face mask": "face_mask",
}


def providers() -> list[str]:
    """The graphics card (DirectML or CUDA) if there is one, otherwise the CPU."""
    import onnxruntime as ort

    available = ort.get_available_providers()
    return [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in available] + ["CPUExecutionProvider"]


class Model:
    def __init__(self, path: str, classes: set[str] | None = None, data_dir: str | None = None):
        """`classes` gives the codes to keep, `data_dir` the data folder used for package activation."""
        import onnxruntime as ort

        from .model_session import open_session

        self.name = os.path.basename(path)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")  # several models should not steal the CPU from each other
        self.session = open_session(path, data_dir, options, providers(), cpu_fallback=True)
        self.provider = self.session.get_providers()[0]
        g = self.session.get_inputs()[0]
        self.input_name = g.name
        self.h, self.w = (int(g.shape[2]), int(g.shape[3])) if isinstance(g.shape[2], int) else (640, 640)
        meta = self.session.get_modelmeta().custom_metadata_map or {}
        names = ast.literal_eval(meta["names"]) if meta.get("names") else {}
        self.nc = len(names)  # in segmentation models the mask coefficients come after the class scores
        self.codes = {int(i): CLASS_CODE.get(str(a).lower()) for i, a in names.items()}
        if classes is not None:
            self.codes = {i: (k if k in classes else None) for i, k in self.codes.items()}
        self.license = meta.get("license", "")

    def run(self, bgr: np.ndarray, confidence: float = 0.25, iou: float = 0.5) -> list[dict]:
        """Returns: [{"s": code, "b": [x1, y1, x2, y2], "p": confidence}] (image pixels)."""
        H, W = bgr.shape[:2]
        o = min(self.w / W, self.h / H)
        yw, yh = int(round(W * o)), int(round(H * o))
        canvas = np.full((self.h, self.w, 3), 114, np.uint8)
        dx, dy = (self.w - yw) // 2, (self.h - yh) // 2
        canvas[dy:dy + yh, dx:dx + yw] = cv2.resize(bgr, (yw, yh), interpolation=cv2.INTER_LINEAR)
        x = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        y = self.session.run(None, {self.input_name: x})[0][0].T  # (candidate, 4 + class)
        scores = y[:, 4:4 + self.nc] if self.nc else y[:, 4:]
        cls = scores.argmax(1)
        p = scores[np.arange(len(cls)), cls]
        keep = p >= confidence
        if not keep.any():
            return []
        y, cls, p = y[keep], cls[keep], p[keep]
        boxes = np.empty((len(y), 4), np.float32)  # x, y, w, h (image pixels)
        boxes[:, 0] = (y[:, 0] - y[:, 2] / 2 - dx) / o
        boxes[:, 1] = (y[:, 1] - y[:, 3] / 2 - dy) / o
        boxes[:, 2] = y[:, 2] / o
        boxes[:, 3] = y[:, 3] / o
        offset = cls[:, None].astype(np.float32) * 4096.0  # suppress while keeping the classes apart
        kept = cv2.dnn.NMSBoxes((boxes + np.hstack([offset, offset, np.zeros_like(offset), np.zeros_like(offset)])).tolist(), p.tolist(), confidence, iou)
        out = []
        for i in np.array(kept).reshape(-1):
            code = self.codes.get(int(cls[i]))
            if code is None:
                continue
            x1, y1, w, h = boxes[i]
            out.append({"s": code, "b": [round(float(max(0.0, x1)), 1), round(float(max(0.0, y1)), 1),
                                         round(float(min(W, x1 + w)), 1), round(float(min(H, y1 + h)), 1)], "p": round(float(p[i]), 3)})
        return out


def _intersection(a, b) -> tuple[float, float, float]:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    k = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return k, (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])


def same_box(a, b, iou: float = 0.5, containment: float = 0.75) -> bool:
    """Whether two boxes belong to the same object: they overlap enough or one is inside the other."""
    k, aa, ab = _intersection(a, b)
    return k > 0 and (k / (aa + ab - k + 1e-6) >= iou or k / (min(aa, ab) + 1e-6) >= containment)


def merge(detections: list[dict]) -> list[dict]:
    """Reduces the boxes several models give for the same object to one (the more confident one stays)."""
    out: list[dict] = []
    for d in sorted(detections, key=lambda d: -d["p"]):
        if not any(e["s"] == d["s"] and same_box(d["b"], e["b"]) for e in out):
            out.append(d)
    return out


class Detector:
    """Loads the models in the `models/` folder; each model gives only the classes assigned to it."""

    def __init__(self, model_dir: str, plan: list[dict] | None = None, data_dir: str | None = None):
        """plan: [{"file": "x.onnx", "classes": [...] | None, "confidence": 0.25, "class_confidence": {"ladder": 0.6}}]; without it,
        every model in the folder. `class_confidence` is a higher confidence threshold for a class. `data_dir` is the data folder
        used for package activation."""
        from . import vault

        if plan is None:
            extensions = (vault.EXTENSION, vault.community_model.EXTENSION)
            names = {next((f[:-len(u)] for u in extensions if f.lower().endswith(u)), f)
                     for f in os.listdir(model_dir)}
            plan = [{"file": f} for f in sorted(names) if f.lower().endswith(".onnx")]
        self.models = []
        self.attributes = None  # protection attributes from the person crop (apron, coveralls, sleeve guards, harness, hearing protection)
        self.missing: list[str] = []  # optional models in the plan but not in the folder (shown in the status line)
        for p in plan:
            path = os.path.join(model_dir, p["file"])
            if not vault.model_exists(path):
                if not p.get("rules") or vault.community_model.is_community_folder(model_dir):
                    raise RuntimeError(T("model file missing: {file}", file=p["file"]))
                self.missing.append(p["file"])
                continue
            if p.get("kind") == "attributes":
                from .attributes import AttributeClassifier

                self.attributes = AttributeClassifier(path, data_dir)
                continue
            self.models.append((Model(path, set(p["classes"]) if p.get("classes") else None, data_dir), float(p.get("confidence", 0.25)),
                                p.get("class_confidence") or {}))
        if not self.models:
            raise RuntimeError(T("no model found: {folder}", folder=model_dir))

    def run(self, bgr: np.ndarray) -> list[dict]:
        found = []
        for m, confidence, per_class in self.models:
            found += [d for d in m.run(bgr, confidence) if d["p"] >= per_class.get(d["s"], 0.0)]
        found = merge(found)
        if self.attributes is not None:
            self.attributes.run(bgr, found)
        return found

    def summary(self) -> str:
        return (", ".join(f"{m.name} ({m.provider.replace('ExecutionProvider', '')})" for m, _, _ in self.models)
                + (f", {self.attributes.name}" if self.attributes else "")
                + (T(" · warning: model file missing: {files}", files=", ".join(self.missing)) if self.missing else ""))
