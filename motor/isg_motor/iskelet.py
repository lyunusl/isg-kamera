"""İskelet (duruş) tahmini: her kişi kutusu kırpılıp büyütülerek duruş modelinden geçirilir.

Tam karede çalışan duruş modelleri geniş açıda küçük görünen kişileri kaçırıyor; kişiyi önce tespit edip sonra kutusunu
büyüterek bakmak (yukarıdan aşağı yöntem) uzaktaki kişilerde de eklemleri verir. Yalnızca numpy, OpenCV ve onnxruntime kullanır.

17 nokta (COCO sırası): 0 burun, 1-2 gözler, 3-4 kulaklar, 5-6 omuzlar, 7-8 dirsekler, 9-10 bilekler, 11-12 kalçalar,
13-14 dizler, 15-16 ayak bilekleri. Her nokta [x, y, güven].
"""
from __future__ import annotations

import os

import cv2
import numpy as np

KEMIKLER = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
EN_AZ_BOY = 36   # bundan kısa (piksel) kişide iskelet aranmaz
PAY = 0.12       # kutunun çevresine eklenen pay (kol ve bacak dışarı taşabilir)


class Iskelet:
    def __init__(self, yol: str, veri: str | None = None):
        """`veri` paket etkinleştirmesinde kullanılan veri klasörüdür."""
        import onnxruntime as ort

        from .model_oturumu import oturum_ac

        secenek = ort.SessionOptions()
        secenek.log_severity_level = 3
        secenek.add_session_config_entry("session.intra_op.allow_spinning", "0")  # birden fazla model işlemciyi birbirinden çalmasın
        self.oturum = oturum_ac(yol, veri, secenek, ["CPUExecutionProvider"])
        g = self.oturum.get_inputs()[0]
        self.girdi = g.name
        self.h, self.w = (int(g.shape[2]), int(g.shape[3])) if isinstance(g.shape[2], int) else (384, 256)
        self.toplu = not isinstance(g.shape[0], int)  # toplu boyutu serbest mi
        self.ad = os.path.basename(yol)

    def _hazirla(self, bgr: np.ndarray, kutu):
        """Kişi kutusunu kırpıp model girdisine yerleştirir. Dönüş: (girdi, ölçek, dx, dy, xa, ya, yw, yh) ya da None."""
        H, W = bgr.shape[:2]
        x1, y1, x2, y2 = kutu
        bw, bh = x2 - x1, y2 - y1
        if bh < EN_AZ_BOY or bw < 8:
            return None
        xa, ya = int(max(0, x1 - PAY * bw)), int(max(0, y1 - PAY * bh))
        xb, yb = int(min(W, x2 + PAY * bw)), int(min(H, y2 + PAY * bh))
        kirpinti = bgr[ya:yb, xa:xb]
        kh, kw = kirpinti.shape[:2]
        if kh < 8 or kw < 8:
            return None
        o = min(self.w / kw, self.h / kh)
        yw, yh = max(1, int(round(kw * o))), max(1, int(round(kh * o)))
        tuval = np.full((self.h, self.w, 3), 114, np.uint8)
        dx, dy = (self.w - yw) // 2, (self.h - yh) // 2
        tuval[dy:dy + yh, dx:dx + yw] = cv2.resize(kirpinti, (yw, yh), interpolation=cv2.INTER_LINEAR)
        x = tuval[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return x, o, dx, dy, xa, ya, yw, yh

    def _coz(self, y: np.ndarray, o, dx, dy, xa, ya, yw, yh) -> list | None:
        """Model çıktısından (aday, 4 + 1 + 51) kırpıntının asıl kişisini seçer ve noktaları görüntüye taşır."""
        if not len(y):
            return None
        # kırpıntıda birden fazla kişi olabilir: kırpıntının ortasına en yakın ve yeterince güvenli olan seçilir
        guven = y[:, 4]
        uygun = np.flatnonzero(guven >= 0.25)
        if not len(uygun):
            return None
        merkez = np.hypot(y[uygun, 0] - self.w / 2, y[uygun, 1] - self.h / 2) / max(self.w, self.h)
        alan = (y[uygun, 2] * y[uygun, 3]) / (yw * yh)
        i = uygun[int(np.argmax(guven[uygun] + 0.5 * np.minimum(alan, 1.0) - merkez))]
        k = y[i, 5:].reshape(17, 3)
        return [[round(float((p[0] - dx) / o + xa), 1), round(float((p[1] - dy) / o + ya), 1), round(float(p[2]), 2)] for p in k]

    def kisi(self, bgr: np.ndarray, kutu) -> list | None:
        """Bir kişi kutusunun 17 noktası (görüntü pikseli) ya da bulunamazsa None."""
        h = self._hazirla(bgr, kutu)
        if h is None:
            return None
        y = self.oturum.run(None, {self.girdi: np.ascontiguousarray(h[0][None])})[0][0].T
        return self._coz(y, *h[1:])

    def calistir(self, bgr: np.ndarray, tespitler: list[dict], en_cok: int = 12) -> int:
        """Kişi tespitlerine "k" (17 nokta) alanını ekler; kişiler tek seferde (toplu) işlenir. Dönüş: iskeleti bulunan kişi sayısı."""
        kisiler = sorted((d for d in tespitler if d["s"] == "kisi"), key=lambda d: -(d["b"][3] - d["b"][1]))[:en_cok]
        hazir = [(d, self._hazirla(bgr, d["b"])) for d in kisiler]
        hazir = [(d, h) for d, h in hazir if h is not None]
        if not hazir:
            return 0
        if self.toplu:
            cikti = self.oturum.run(None, {self.girdi: np.ascontiguousarray(np.stack([h[0] for _, h in hazir]))})[0]
        else:
            cikti = [self.oturum.run(None, {self.girdi: np.ascontiguousarray(h[0][None])})[0][0] for _, h in hazir]
        n = 0
        for (d, h), y in zip(hazir, cikti):
            k = self._coz(y.T, *h[1:])
            if k is not None:
                d["k"] = k
                n += 1
        return n


def ciz(fr: np.ndarray, k: list, renk=(255, 200, 60), esik: float = 0.4) -> None:
    for a, b in KEMIKLER:
        if k[a][2] >= esik and k[b][2] >= esik:
            cv2.line(fr, (int(k[a][0]), int(k[a][1])), (int(k[b][0]), int(k[b][1])), renk, 2, cv2.LINE_AA)
    for p in k[5:]:
        if p[2] >= esik:
            cv2.circle(fr, (int(p[0]), int(p[1])), 3, renk, -1, cv2.LINE_AA)
