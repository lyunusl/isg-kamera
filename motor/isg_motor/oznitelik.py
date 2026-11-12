"""Kişi öznitelikleri: her kişi kutusu kırpılıp küçük bir sınıflandırıcıdan geçirilir; kişinin üstünde hangi koruyucuların
olduğu (önlük, tek parça koruyucu giysi, kolluk, tam vücut emniyet kemeri, kulak koruyucu) olasılık olarak döner.

Nesne tespiti bu koruyucuları güvenilir bulamadığı için (önlük ve tulum bedenin büyük kısmını kaplar, kemer ince şeritlerdir)
"bu kişide var mı" sorusu kişi kırpıntısı üstünde sorulur. Yalnızca numpy, OpenCV ve onnxruntime kullanır.
Öznitelik modelinin eğitiminde yapay zekâ ile üretilmiş görüntüler de kullanıldı.
"""
from __future__ import annotations

import ast
import os

import cv2
import numpy as np

SINIFLAR = ["onluk", "tulum", "kolluk", "kemer", "kulaklik"]
GIRDI = (384, 192)   # yükseklik, genişlik
PAY = 0.08           # kutunun çevresine eklenen pay
EN_AZ_BOY = 110      # bundan kısa (piksel) kişi değerlendirilmez


def kirp(bgr: np.ndarray, kutu) -> np.ndarray | None:
    """Kişi kutusunu payıyla kırpar ve oranını bozmadan GIRDI boyutuna yerleştirir (BGR, uint8)."""
    H, W = bgr.shape[:2]
    x1, y1, x2, y2 = kutu
    bw, bh = x2 - x1, y2 - y1
    if bh < EN_AZ_BOY or bw < 12:
        return None
    xa, ya = int(max(0, x1 - PAY * bw)), int(max(0, y1 - PAY * bh))
    xb, yb = int(min(W, x2 + PAY * bw)), int(min(H, y2 + PAY * bh))
    k = bgr[ya:yb, xa:xb]
    kh, kw = k.shape[:2]
    if kh < 8 or kw < 8:
        return None
    o = min(GIRDI[1] / kw, GIRDI[0] / kh)
    yw, yh = max(1, int(round(kw * o))), max(1, int(round(kh * o)))
    tuval = np.full((GIRDI[0], GIRDI[1], 3), 114, np.uint8)
    dx, dy = (GIRDI[1] - yw) // 2, (GIRDI[0] - yh) // 2
    tuval[dy:dy + yh, dx:dx + yw] = cv2.resize(k, (yw, yh), interpolation=cv2.INTER_AREA if o < 1 else cv2.INTER_LINEAR)
    return tuval


class Oznitelik:
    def __init__(self, yol: str, veri: str | None = None):
        """`veri` paket etkinleştirmesinde kullanılan veri klasörüdür."""
        import onnxruntime as ort

        from .model_oturumu import oturum_ac

        secenek = ort.SessionOptions()
        secenek.log_severity_level = 3
        secenek.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.oturum = oturum_ac(yol, veri, secenek, ["CPUExecutionProvider"])
        self.girdi = self.oturum.get_inputs()[0].name
        ust = self.oturum.get_modelmeta().custom_metadata_map or {}
        self.siniflar = ast.literal_eval(ust["names"]) if ust.get("names") else SINIFLAR
        self.ad = os.path.basename(yol)

    def calistir(self, bgr: np.ndarray, tespitler: list[dict], en_cok: int = 8) -> int:
        """Kişi tespitlerine "oz" alanını ekler: {"onluk": 0.93, ...}. En büyük `en_cok` kişi tek seferde işlenir. Dönüş: işlenen kişi sayısı."""
        kisiler = sorted((d for d in tespitler if d["s"] == "kisi"), key=lambda d: -(d["b"][3] - d["b"][1]))[:en_cok]
        hazir = [(d, kirp(bgr, d["b"])) for d in kisiler]
        hazir = [(d, k) for d, k in hazir if k is not None]
        if not hazir:
            return 0
        x = np.stack([k[:, :, ::-1].transpose(2, 0, 1) for _, k in hazir]).astype(np.float32) / 255.0
        y = self.oturum.run(None, {self.girdi: np.ascontiguousarray(x)})[0]
        for (d, _), satir in zip(hazir, y):
            d["oz"] = {s: round(float(p), 2) for s, p in zip(self.siniflar, satir)}
        return len(hazir)
