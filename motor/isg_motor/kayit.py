"""Görüntüyü işaretler (yüzler gizli), olay kliplerini yazar ve olayları panelin okuyacağı kuyruk klasörüne bırakır.

Kuyruk: <veri>/kuyruk/*.json  ·  klipler ve kareler: <veri>/cikti/<kamera>/
Panel kapalıyken de çalışır; panel açılınca kuyruktaki dosyaları sırayla içeri alır.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque

import cv2
import numpy as np

from . import iskelet
from .kural import bas_bolgesi

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # Studio'da Pillow yoksa yazılar OpenCV ile, Türkçe harfler sadeleştirilerek çizilir
    Image = None

RENK_OLAY = (15, 68, 240)      # BGR
RENK_DIGER = (175, 160, 150)
RENK_BOLGE = (60, 190, 255)
_SADE = str.maketrans("çğıöşüÇĞİÖŞÜ–·", "cgiosuCGIOSU-.")
_FONT: dict = {}


def _font(boyut: int):
    if boyut not in _FONT:
        for ad in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
            try:
                _FONT[boyut] = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", ad), boyut)
                break
            except OSError:
                continue
        else:
            _FONT[boyut] = ImageFont.load_default()
    return _FONT[boyut]


def piksel(fr: np.ndarray, cx: float, cy: float, rx: float, ry: float) -> None:
    H, W = fr.shape[:2]
    xa, xb = int(max(0, cx - rx)), int(min(W, cx + rx))
    ya, yb = int(max(0, cy - ry)), int(min(H, cy + ry))
    if xb - xa < 4 or yb - ya < 4:
        return
    roi = fr[ya:yb, xa:xb]
    blok = max(5, int(rx / 2.2))
    kucuk = cv2.resize(roi, (max(2, (xb - xa) // blok), max(2, (yb - ya) // blok)), interpolation=cv2.INTER_AREA)
    buyuk = cv2.resize(kucuk, (xb - xa, yb - ya), interpolation=cv2.INTER_NEAREST)
    maske = np.zeros(roi.shape[:2], np.uint8)
    cv2.ellipse(maske, (int(cx - xa), int(cy - ya)), (int(rx), int(ry)), 0, 0, 360, 255, -1)
    roi[maske > 0] = buyuk[maske > 0]


YUZ_TUTMA_SN = 1.0  # tespit kaçarsa son görülen baş bölgesi bu kadar süre gizli kalır
YUZ_BUYUME = 1.0    # tutulan bölge bu sürede (1 + YUZ_BUYUME) katına genişler: kişi o sırada biraz yer değiştirmiş olabilir


def kuyruga_birak(veri: str, kod: str, kayit: dict) -> None:
    """Panelin okuduğu kuyruğa tek kayıt bırakır (kameranın yazıcısı kurulmadan önce de kullanılabilir)."""
    klasor = os.path.join(veri, "kuyruk")
    os.makedirs(klasor, exist_ok=True)
    ad = f"{time.time_ns():020d}_{kod}_h"
    gecici = os.path.join(klasor, ad + ".tmp")
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False)
    os.replace(gecici, os.path.join(klasor, ad + ".json"))


YUZ_ORTME = 0.8     # tutulan bölge, alanının en az bu kadarı şimdiki maskelerle örtülünce bırakılır
_ORNEK = [(math.sqrt((i + 0.5) / 48) * math.cos(i * 2.399963), math.sqrt((i + 0.5) / 48) * math.sin(i * 2.399963)) for i in range(48)]  # birim dairede eşit dağılmış noktalar


def _ortulu(eski, simdiki) -> bool:
    """Eski maskenin alanının en az YUZ_ORTME kadarı şimdiki maskelerin birleşimiyle örtülü mü (48 örnek noktayla ölçülür)."""
    n = 0
    for ux, uy in _ORNEK:
        x, y = eski[0] + ux * eski[2], eski[1] + uy * eski[3]
        if any(((x - s[0]) / max(s[2], 1e-6)) ** 2 + ((y - s[1]) / max(s[3], 1e-6)) ** 2 <= 1.0 for s in simdiki):
            n += 1
    return n >= YUZ_ORTME * len(_ORNEK)


def yuz_bolgeleri(tespitler: list[dict]) -> list[tuple[float, float, float, float]]:
    """Gizlenecek elipsler (cx, cy, rx, ry): kişi kutularının baş bölgesi ve modelin bulduğu baş/baret kutularının altındaki yüz."""
    out = []
    for d in tespitler:
        b = d["b"]
        w, h = b[2] - b[0], b[3] - b[1]
        if d["s"] == "kisi":
            out.append(tuple(bas_bolgesi(b, d.get("k"))))
        elif d["s"] in ("baret_yok", "gozluk", "gozluk_yok", "maske", "maske_yok"):  # kutu başın kendisi
            out.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(0.6 * w, 7), max(0.65 * h, 8)))
        elif d["s"] == "baret":  # yüz baretin hemen altında
            out.append(((b[0] + b[2]) / 2, b[3] + 0.35 * h, max(0.55 * w, 7), max(0.75 * h, 8)))
    return out


def yuzleri_gizle(fr: np.ndarray, tespitler: list[dict], hafiza: dict | None = None, t: float | None = None) -> None:
    """Baş bölgelerini piksellere çevirir.

    `hafiza` (akış boyunca aynı sözlük) ve `t` (saniye) verilirse, son YUZ_TUTMA_SN içinde gizlenmiş baş bölgeleri de,
    alanlarının en az YUZ_ORTME kadarı bu karenin maskeleriyle örtülmediği sürece gizlenmeye devam eder: kişi bir-iki
    karede algılanmazsa ya da yeni maske eski bölgeyi yeterince örtmüyorsa yüz açıkta kalmaz (yürüyen kişinin ardında
    bir saniyelik iz kalır). Tutulan bölge kişiyi izlemez; yalnızca zamanla genişler. Hiç algılanmamış kişinin yüzü gizlenemez."""
    simdiki = yuz_bolgeleri(tespitler)
    eski = []
    if hafiza is not None and t is not None:
        eski = [(t0, e) for t0, e in hafiza.get("yuzler", []) if 0 <= t - t0 <= YUZ_TUTMA_SN and not _ortulu(e, simdiki)]
        hafiza["yuzler"] = eski + [(t, s) for s in simdiki]
    for e in simdiki:
        piksel(fr, *e)
    for t0, e in eski:
        k = 1.0 + YUZ_BUYUME * (t - t0) / YUZ_TUTMA_SN
        piksel(fr, e[0], e[1], e[2] * k, e[3] * k)


def ciz(fr: np.ndarray, sonuc: dict, tespitler: list[dict], bolgeler: list[dict], ust: str, alt: str,
        hafiza: dict | None = None, t: float | None = None) -> np.ndarray:
    """Yüzleri gizler; bölgeleri, kişileri ve o an süren ihlalleri çizer. `fr` yerinde değişir. `hafiza`, `t`: yuzleri_gizle."""
    yuzleri_gizle(fr, tespitler, hafiza, t)
    vurgulu = {tuple(v["b"]) for v in sonuc["vurgu"]}
    for b in bolgeler:
        cv2.polylines(fr, [b["poly"].astype(np.int32)], True, RENK_BOLGE, 2, cv2.LINE_AA)
    for p in sonuc["kisiler"]:
        if p.get("k"):
            iskelet.ciz(fr, p["k"], (235, 200, 120))
    for p in sonuc["kisiler"] + sonuc["araclar"]:
        if tuple(p["b"]) not in vurgulu:
            x1, y1, x2, y2 = (int(v) for v in p["b"])
            cv2.rectangle(fr, (x1, y1), (x2, y2), RENK_DIGER, 1, cv2.LINE_AA)
    etiketler = []
    for b in bolgeler:
        etiketler.append((int(b["poly"][:, 0].min()) + 6, int(b["poly"][:, 1].min()) + 6, b["ad"], RENK_BOLGE, (20, 20, 20), 15))
    kutu_etiketleri: dict = {}  # aynı kutudaki ihlaller tek satırda
    for v in sonuc["vurgu"]:
        x1, y1, x2, y2 = (int(q) for q in v["b"])
        cv2.rectangle(fr, (x1, y1), (x2, y2), RENK_OLAY, 3, cv2.LINE_AA)
        adlar = kutu_etiketleri.setdefault((x1, y1), [])
        if v.get("etiket") and v["etiket"] not in adlar:
            adlar.append(v["etiket"])
    for (x1, y1), adlar in kutu_etiketleri.items():
        etiketler.append((x1, max(2, y1 - 26), " · ".join(adlar), RENK_OLAY, (255, 255, 255), 16))
    if ust:
        etiketler.append((12, 10, ust, RENK_OLAY if sonuc["acik"] else (40, 30, 24), (255, 255, 255), 20))
    etiketler.append((12, fr.shape[0] - 34, alt, (20, 14, 12), (240, 230, 225), 15))
    return _yazilar(fr, etiketler)


def _yazilar(fr: np.ndarray, etiketler: list) -> np.ndarray:
    gorulen = set()
    if Image is None:
        for x, y, metin, zemin, yazi, boyut in etiketler:
            if not metin or (x // 8, y // 8, metin) in gorulen:
                continue
            gorulen.add((x // 8, y // 8, metin))
            m = metin.translate(_SADE)
            (w, h), _ = cv2.getTextSize(m, cv2.FONT_HERSHEY_SIMPLEX, boyut / 30, 1)
            cv2.rectangle(fr, (x, y), (x + w + 12, y + h + 12), zemin, -1)
            cv2.putText(fr, m, (x + 6, y + h + 5), cv2.FONT_HERSHEY_SIMPLEX, boyut / 30, yazi, 1, cv2.LINE_AA)
        return fr
    im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    cz = ImageDraw.Draw(im)
    for x, y, metin, zemin, yazi, boyut in etiketler:
        if not metin or (x // 8, y // 8, metin) in gorulen:
            continue
        gorulen.add((x // 8, y // 8, metin))
        f = _font(boyut)
        l, t, r, b = cz.textbbox((0, 0), metin, font=f)
        w, h = r - l, b - t
        x = min(max(4, x), max(4, im.size[0] - w - 18))
        cz.rounded_rectangle([x, y, x + w + 14, y + h + 10], radius=6, fill=zemin[::-1])
        cz.text((x + 7, y + 4 - t), metin, font=f, fill=yazi[::-1])
    fr[:] = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    return fr


class Kayitci:
    """Bir kameranın klip ve kuyruk yazıcısı."""

    def __init__(self, veri: str, kod: str, kamera_ad: str, W: int, H: int, hiz: float):
        self.kod, self.kamera_ad, self.W, self.H = kod, kamera_ad, W, H
        self.kosu = ""  # çalıştırmanın numarası (blok yazar): panel kayıtları koşuya göre ayırır
        self.hiz = max(1.0, min(30.0, hiz))
        self.cikti = os.path.join(veri, "cikti", kod)
        self.kuyruk = os.path.join(veri, "kuyruk")
        os.makedirs(self.cikti, exist_ok=True)
        os.makedirs(self.kuyruk, exist_ok=True)
        self.tampon: deque = deque(maxlen=max(2, int(1.5 * self.hiz)))  # olaydan önceki son 1,5 sn (JPEG)
        self.yazicilar: dict[str, dict] = {}
        self.sayac = 0

    def _birak(self, kayit: dict) -> None:
        self.sayac += 1
        if self.kosu:
            kayit = {**kayit, "kosu": self.kosu}
        ad = f"{time.time_ns():020d}_{self.kod}_{self.sayac:05d}"
        gecici = os.path.join(self.kuyruk, ad + ".tmp")
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(kayit, f, ensure_ascii=False)
        os.replace(gecici, os.path.join(self.kuyruk, ad + ".json"))

    def kare(self, t: float, cizili: np.ndarray, sonuc: dict) -> None:
        """Her işaretli karede çağrılır: başlayan olayların klibini açar, sürenleri yazar, bitenleri kapatır."""
        for o in sonuc["baslayan"]:
            taban = f"{o['zaman'].replace('-', '').replace(':', '').replace('T', '_')}_{o['kimlik'][:8]}_{o['kural']}"
            kare_yolu = os.path.join(self.cikti, taban + ".jpg")
            cv2.imwrite(kare_yolu, cizili, [cv2.IMWRITE_JPEG_QUALITY, 82])
            y = {"yaz": None, "bas": t, "bitis": None, "ham": os.path.join(self.cikti, taban + ".ham.mp4"), "kare": f"{self.kod}/{taban}.jpg", "n": 0}
            if len(self.yazicilar) < 6:  # aynı anda en çok altı klip
                y["yaz"] = cv2.VideoWriter(y["ham"], cv2.VideoWriter_fourcc(*"mp4v"), self.hiz, (self.W, self.H))
                for _, jpg in self.tampon:
                    y["yaz"].write(cv2.imdecode(jpg, cv2.IMREAD_COLOR))
                    y["n"] += 1
            self.yazicilar[o["kimlik"]] = y
            self._birak({"tur": "olay", "kamera": self.kod, "kamera_ad": self.kamera_ad, **o, "kare": y["kare"], "klip": None})
        ok, jpg = cv2.imencode(".jpg", cizili, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.tampon.append((t, jpg))
        for o in sonuc["biten"]:
            y = self.yazicilar.get(o["kimlik"])
            if y is not None:
                y["bitis"], y["olay"] = t, o
            else:
                self._birak({"tur": "olay", "kamera": self.kod, "kamera_ad": self.kamera_ad, **o, "kare": None, "klip": None})
        for kimlik, y in list(self.yazicilar.items()):
            if y["yaz"] is not None and t - y["bas"] <= 15.0:
                y["yaz"].write(cizili)
                y["n"] += 1
            if y["bitis"] is not None and (t - y["bitis"] >= 1.5 or t - y["bas"] > 15.0):
                self._kapat(kimlik)

    def _kapat(self, kimlik: str) -> None:
        y = self.yazicilar.pop(kimlik)
        klip = None
        if y["yaz"] is not None:
            y["yaz"].release()
            if y["n"] >= 2 and os.path.exists(y["ham"]):
                klip = f"{self.kod}/{os.path.basename(y['ham'])}"
        if y.get("olay") is not None:
            self._birak({"tur": "olay", "kamera": self.kod, "kamera_ad": self.kamera_ad, **y["olay"], "kare": y["kare"], "klip": klip})

    def olcum(self, o: dict, son_kare: np.ndarray | None) -> None:
        if son_kare is not None:
            cv2.imwrite(os.path.join(self.cikti, "son_kare.jpg"), son_kare, [cv2.IMWRITE_JPEG_QUALITY, 82])
            o["son_kare"] = f"{self.kod}/son_kare.jpg"
        self._birak({"tur": "olcum", "kamera": self.kod, "kamera_ad": self.kamera_ad, **o})

    def bitir(self, kalan_olaylar: list[dict], bitti_isareti: bool = True) -> None:
        """Akış bitti: açık klipleri kapatır, kapanan olayları ve 'bitti' işaretini kuyruğa bırakır."""
        for o in kalan_olaylar:
            y = self.yazicilar.get(o["kimlik"])
            if y is not None:
                y["olay"] = o
            else:
                self._birak({"tur": "olay", "kamera": self.kod, "kamera_ad": self.kamera_ad, **o, "kare": None, "klip": None})
        for kimlik in list(self.yazicilar):
            if self.yazicilar[kimlik].get("olay") is None:  # bitişi bildirilmemiş: klibi bırak, olay açık kalır
                self.yazicilar[kimlik]["olay"] = None
            self._kapat(kimlik)
        self.tampon.clear()
        if bitti_isareti:
            self._birak({"tur": "bitti", "kamera": self.kod})
