"""ONNX nesne tespiti (YOLO biçimli çıktı): yalnızca numpy, OpenCV ve onnxruntime kullanır.

AugeLab Studio'nun içinde (İSG Algılama bloğu) ve Studio dışındaki sınamalarda aynı kod çalışır.
Modeller `modeller/` klasöründeki .onnx dosyalarıdır; sınıf adları dosyanın kendi üstverisinden okunur.
"""
from __future__ import annotations

import ast
import os

import cv2
import numpy as np

# model sınıf adı (küçük harf) → bu programdaki kod. "_yok": o koruyucunun takılmadığı açıkça görülüyor.
SINIF_KODU = {
    "person": "kisi",
    "hardhat": "baret", "helmet": "baret", "no-hardhat": "baret_yok", "no_helmet": "baret_yok",
    "safety vest": "yelek", "vest": "yelek", "no-safety vest": "yelek_yok", "no_vest": "yelek_yok",
    "gloves": "eldiven", "no-gloves": "eldiven_yok", "no_gloves": "eldiven_yok",
    "goggles": "gozluk", "no-goggles": "gozluk_yok", "no_goggle": "gozluk_yok",
    "boots": "bot", "no_boots": "bot_yok",
    "forklift": "forklift",
    # ek model (açık sözlüklü): ek koruyucular ve nesneler
    "apron": "onluk", "face shield": "siper", "ear muffs": "kulaklik", "coverall": "tulum", "arm sleeves": "kolluk",
    "pallet": "palet", "cardboard box": "koli", "barrel": "varil", "ladder": "merdiven", "cell phone": "telefon", "face mask": "maske",
}


def saglayicilar() -> list[str]:
    """Varsa ekran kartı (DirectML ya da CUDA), yoksa işlemci."""
    import onnxruntime as ort

    var = ort.get_available_providers()
    return [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in var] + ["CPUExecutionProvider"]


class Model:
    def __init__(self, yol: str, siniflar: set[str] | None = None, veri: str | None = None):
        """`siniflar` alınacak kodları, `veri` paket etkinleştirmesinde kullanılan veri klasörünü belirtir."""
        import onnxruntime as ort

        from .model_oturumu import oturum_ac

        self.ad = os.path.basename(yol)
        secenek = ort.SessionOptions()
        secenek.log_severity_level = 3
        secenek.add_session_config_entry("session.intra_op.allow_spinning", "0")  # birden fazla model işlemciyi birbirinden çalmasın
        self.oturum = oturum_ac(yol, veri, secenek, saglayicilar(), cpu_yedek=True)
        self.saglayici = self.oturum.get_providers()[0]
        g = self.oturum.get_inputs()[0]
        self.girdi = g.name
        self.h, self.w = (int(g.shape[2]), int(g.shape[3])) if isinstance(g.shape[2], int) else (640, 640)
        ust = self.oturum.get_modelmeta().custom_metadata_map or {}
        adlar = ast.literal_eval(ust["names"]) if ust.get("names") else {}
        self.nc = len(adlar)  # bölütleme modellerinde sınıf puanlarından sonra maske katsayıları gelir
        self.kodlar = {int(i): SINIF_KODU.get(str(a).lower()) for i, a in adlar.items()}
        if siniflar is not None:
            self.kodlar = {i: (k if k in siniflar else None) for i, k in self.kodlar.items()}
        self.lisans = ust.get("license", "")

    def calistir(self, bgr: np.ndarray, guven: float = 0.25, iou: float = 0.5) -> list[dict]:
        """Dönüş: [{"s": kod, "b": [x1, y1, x2, y2], "p": güven}] (görüntü pikseli)."""
        H, W = bgr.shape[:2]
        o = min(self.w / W, self.h / H)
        yw, yh = int(round(W * o)), int(round(H * o))
        tuval = np.full((self.h, self.w, 3), 114, np.uint8)
        dx, dy = (self.w - yw) // 2, (self.h - yh) // 2
        tuval[dy:dy + yh, dx:dx + yw] = cv2.resize(bgr, (yw, yh), interpolation=cv2.INTER_LINEAR)
        x = np.ascontiguousarray(tuval[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        y = self.oturum.run(None, {self.girdi: x})[0][0].T  # (aday, 4 + sınıf)
        puan = y[:, 4:4 + self.nc] if self.nc else y[:, 4:]
        sinif = puan.argmax(1)
        p = puan[np.arange(len(sinif)), sinif]
        sec = p >= guven
        if not sec.any():
            return []
        y, sinif, p = y[sec], sinif[sec], p[sec]
        kutu = np.empty((len(y), 4), np.float32)  # x, y, w, h (görüntü pikseli)
        kutu[:, 0] = (y[:, 0] - y[:, 2] / 2 - dx) / o
        kutu[:, 1] = (y[:, 1] - y[:, 3] / 2 - dy) / o
        kutu[:, 2] = y[:, 2] / o
        kutu[:, 3] = y[:, 3] / o
        kaydirma = sinif[:, None].astype(np.float32) * 4096.0  # sınıfları ayrı tutarak bastır
        kalan = cv2.dnn.NMSBoxes((kutu + np.hstack([kaydirma, kaydirma, np.zeros_like(kaydirma), np.zeros_like(kaydirma)])).tolist(), p.tolist(), guven, iou)
        out = []
        for i in np.array(kalan).reshape(-1):
            kod = self.kodlar.get(int(sinif[i]))
            if kod is None:
                continue
            x1, y1, w, h = kutu[i]
            out.append({"s": kod, "b": [round(float(max(0.0, x1)), 1), round(float(max(0.0, y1)), 1),
                                        round(float(min(W, x1 + w)), 1), round(float(min(H, y1 + h)), 1)], "p": round(float(p[i]), 3)})
        return out


def _kesisim(a, b) -> tuple[float, float, float]:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    k = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return k, (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])


def ayni_kutu(a, b, iou: float = 0.5, icerme: float = 0.75) -> bool:
    """İki kutu aynı nesneye mi ait: yeterince örtüşüyor ya da biri ötekinin içinde."""
    k, aa, ab = _kesisim(a, b)
    return k > 0 and (k / (aa + ab - k + 1e-6) >= iou or k / (min(aa, ab) + 1e-6) >= icerme)


def birlestir(tespitler: list[dict]) -> list[dict]:
    """Birden fazla modelin aynı nesne için verdiği kutuları teke indirir (güveni yüksek olan kalır)."""
    out: list[dict] = []
    for d in sorted(tespitler, key=lambda d: -d["p"]):
        if not any(e["s"] == d["s"] and ayni_kutu(d["b"], e["b"]) for e in out):
            out.append(d)
    return out


class Algilayici:
    """`modeller/` klasöründeki modelleri yükler; her model yalnızca kendisine ayrılan sınıfları verir."""

    def __init__(self, klasor: str, plan: list[dict] | None = None, veri: str | None = None):
        """plan: [{"dosya": "x.onnx", "siniflar": [...] | None, "guven": 0.25, "sinif_guven": {"merdiven": 0.6}}]; verilmezse klasördeki bütün modeller.
        `sinif_guven` sınıf için daha yüksek güven eşiğidir. `veri` paket etkinleştirmesinde kullanılan veri klasörüdür."""
        from . import kasa

        if plan is None:
            uzantilar = (kasa.UZANTI, kasa.community_model.UZANTI)
            adlar = {next((f[:-len(u)] for u in uzantilar if f.lower().endswith(u)), f)
                     for f in os.listdir(klasor)}
            plan = [{"dosya": f} for f in sorted(adlar) if f.lower().endswith(".onnx")]
        self.modeller = []
        self.oznitelik = None  # kişi kırpıntısından koruyucu öznitelikleri (önlük, tulum, kolluk, kemer, kulaklık)
        self.eksik: list[str] = []  # planda olup klasörde bulunmayan isteğe bağlı modeller (durum satırında görünür)
        for p in plan:
            yol = os.path.join(klasor, p["dosya"])
            if not kasa.model_var(yol):
                if not p.get("kurallar") or kasa.community_model.isaretli(klasor):
                    raise RuntimeError(f"model dosyası yok: {p['dosya']}")
                self.eksik.append(p["dosya"])
                continue
            if p.get("tur") == "oznitelik":
                from .oznitelik import Oznitelik

                self.oznitelik = Oznitelik(yol, veri)
                continue
            self.modeller.append((Model(yol, set(p["siniflar"]) if p.get("siniflar") else None, veri), float(p.get("guven", 0.25)), p.get("sinif_guven") or {}))
        if not self.modeller:
            raise RuntimeError(f"model bulunamadı: {klasor}")

    def calistir(self, bgr: np.ndarray) -> list[dict]:
        hepsi = []
        for m, guven, ozel in self.modeller:
            hepsi += [d for d in m.calistir(bgr, guven) if d["p"] >= ozel.get(d["s"], 0.0)]
        hepsi = birlestir(hepsi)
        if self.oznitelik is not None:
            self.oznitelik.calistir(bgr, hepsi)
        return hepsi

    def ozet(self) -> str:
        return (", ".join(f"{m.ad} ({m.saglayici.replace('ExecutionProvider', '')})" for m, _, _ in self.modeller) + (f", {self.oznitelik.ad}" if self.oznitelik else "")
                + (f" · uyarı: model dosyası yok: {', '.join(self.eksik)}" if self.eksik else ""))
