"""Canlı kural motoru: her karede tespitleri alır, kişileri ve araçları izler, kuralları uygular.

Bir olay, koşul `sure_sn` boyunca sürdüğü anda BAŞLAR (uyarı ve çıkışlar o anda tetiklenir) ve koşul bitince KAPANIR.
Yalnızca numpy ve OpenCV kullanır; AugeLab Studio'nun içinde (İSG Kurallar bloğu) çalışır.
"""
from __future__ import annotations

import datetime as dt
import math
import uuid
from collections import defaultdict, deque

import cv2
import numpy as np

from . import katalog, renk
from .algila import ayni_kutu

IZGARA = (32, 18)        # ısı haritası hücreleri (en, boy)
ARACLAR = {"forklift", "arac"}
NESNELER = {"palet", "koli", "varil", "merdiven", "telefon"}  # izlenen nesneler (ek model)
YUK = {"koli", "varil", "telefon"}  # eli dolduran nesneler: bunlardan birini tutan el merdiveni tutmuyor sayılır
ELDE = {"telefon"}       # elde tutulan nesneler: bölge, nesnenin değil tutan kişinin bastığı yere göre belirlenir
BOS_ISKELET = [[0.0, 0.0, 0.0]] * 17
OLUMSUZ_GUVEN = 0.5      # "takılmamış" tespiti en az bu güvenle sayılır
OZ_VAR, OZ_YOK = 0.6, 0.4  # öznitelik modelinin olasılığı: üstü "var", altı "yok", arası kararsız (değerlendirilmez)
KKD_EN_AZ_BOY = 60       # bundan kısa görünen (piksel) kişide koruyucu değerlendirilmez
# koruyucu hangi vücut bölgesinde aranır
PARCA = {"baret": "bas", "gozluk": "bas", "maske": "bas", "siper": "bas", "kulaklik": "bas", "solunum": "bas",
         "yelek": "govde", "onluk": "govde", "tulum": "govde", "eldiven": "el", "kolluk": "el", "bot": "ayak"}


KP = 0.4  # iskelet noktası güven eşiği

# REBA tabloları (Hignett ve McAtamney, 2000). A: gövde × boyun × bacak · B: üst kol × alt kol × bilek · C: A × B
REBA_A = [[[1, 2, 3, 4], [1, 2, 3, 4], [3, 3, 5, 6]], [[2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]], [[2, 4, 5, 6], [4, 5, 6, 7], [5, 6, 7, 8]],
          [[3, 5, 6, 7], [5, 6, 7, 8], [6, 7, 8, 9]], [[4, 6, 7, 8], [6, 7, 8, 9], [7, 8, 9, 9]]]
REBA_B = [[[1, 2, 2], [1, 2, 3]], [[1, 2, 3], [2, 3, 4]], [[3, 4, 5], [4, 5, 5]], [[4, 5, 5], [5, 6, 7]], [[6, 7, 8], [7, 8, 8]], [[7, 8, 8], [8, 9, 9]]]
REBA_C = [[1, 1, 1, 2, 3, 3, 4, 5, 6, 7, 7, 7], [1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 7, 8], [2, 3, 3, 3, 4, 5, 6, 7, 7, 8, 8, 8],
          [3, 4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9], [4, 4, 4, 5, 6, 7, 8, 8, 9, 9, 9, 9], [6, 6, 6, 7, 8, 8, 9, 9, 10, 10, 10, 10],
          [7, 7, 7, 8, 9, 9, 9, 10, 10, 11, 11, 11], [8, 8, 8, 9, 10, 10, 10, 10, 10, 11, 11, 11], [9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 12],
          [10, 10, 10, 11, 11, 11, 11, 12, 12, 12, 12, 12], [11, 11, 11, 11, 12, 12, 12, 12, 12, 12, 12, 12], [12] * 12]


def _aci(u, v) -> float:
    """İki yön arasındaki açı (derece)."""
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu < 1e-6 or nv < 1e-6:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))))


def _orta(k, sira) -> tuple[float, float] | None:
    n = [k[i] for i in sira if k[i][2] >= KP]
    return (sum(p[0] for p in n) / len(n), sum(p[1] for p in n) / len(n)) if n else None


def govde_acisi(k) -> float | None:
    """Gövdenin (kalça → omuz) düşeyden sapması, derece. 0 = dik, 90 = yatay."""
    om, ka = _orta(k, (5, 6)), _orta(k, (11, 12))
    if om is None or ka is None:
        return None
    return _aci((om[0] - ka[0], om[1] - ka[1]), (0.0, -1.0))


def reba(k) -> int | None:
    """Görüntüdeki eklem açılarından yaklaşık REBA puanı (1-12). Yük, kavrama ve tekrar puanları eklenmez; bilek 1 sayılır.
    Açılar görüntü düzlemindedir: kameraya doğru ya da kameradan uzağa eğilme olduğundan az ölçülür."""
    om, ka = _orta(k, (5, 6)), _orta(k, (11, 12))
    if om is None or ka is None:
        return None
    govde = (om[0] - ka[0], om[1] - ka[1])
    # Yüksekten bakan kamerada dik duran kişi de görüntüde eğik görünür; bu yüzden gövdenin eğimi görüntünün düşeyine göre değil,
    # kişinin kendi bacak doğrultusuna (ayak bileği → kalça) göre ölçülür. Bacaklar görünmüyorsa görüntünün düşeyi kullanılır.
    bilek = _orta(k, (15, 16))
    dik = (ka[0] - bilek[0], ka[1] - bilek[1]) if bilek is not None and math.hypot(ka[0] - bilek[0], ka[1] - bilek[1]) > 0.3 * math.hypot(*govde) else (0.0, -1.0)
    a = _aci(govde, dik)
    g = 1 if a < 5 else 2 if a < 20 else 3 if a < 60 else 4
    bas = _orta(k, (0, 1, 2, 3, 4))
    boyun = 1 if bas is None or _aci((bas[0] - om[0], bas[1] - om[1]), govde) < 20 else 2
    bukum = 0.0
    for kalca, diz, bilek in ((11, 13, 15), (12, 14, 16)):
        if min(k[kalca][2], k[diz][2], k[bilek][2]) >= KP:
            bukum = max(bukum, 180 - _aci((k[kalca][0] - k[diz][0], k[kalca][1] - k[diz][1]), (k[bilek][0] - k[diz][0], k[bilek][1] - k[diz][1])))
    bacak = 1 + (1 if 30 <= bukum < 60 else 2 if bukum >= 60 else 0)
    ust, alt = 1, 1
    for omuz, dirsek, bilek in ((5, 7, 9), (6, 8, 10)):
        if min(k[omuz][2], k[dirsek][2]) < KP:
            continue
        kol = _aci((k[dirsek][0] - k[omuz][0], k[dirsek][1] - k[omuz][1]), (-govde[0], -govde[1]))
        ust = max(ust, 1 if kol < 20 else 2 if kol < 45 else 3 if kol < 90 else 4)
        if k[bilek][2] >= KP:
            dirsek_bukumu = 180 - _aci((k[omuz][0] - k[dirsek][0], k[omuz][1] - k[dirsek][1]), (k[bilek][0] - k[dirsek][0], k[bilek][1] - k[dirsek][1]))
            alt = max(alt, 1 if 60 <= dirsek_bukumu <= 100 else 2)
    return REBA_C[REBA_A[g - 1][boyun - 1][bacak - 1] - 1][REBA_B[ust - 1][alt - 1][0] - 1]


def ayak(b, k=None) -> tuple[float, float]:
    """Kişinin zemindeki noktası: ayak bilekleri (iskelet varsa), yoksa kutunun alt ortası."""
    if k:
        n = [k[i] for i in (15, 16) if k[i][2] >= KP]
        if n:
            return (sum(p[0] for p in n) / len(n), max(p[1] for p in n))
    return ((b[0] + b[2]) / 2, float(b[3]))


def icinde(poly: np.ndarray, p) -> bool:
    return cv2.pointPolygonTest(poly, (float(p[0]), float(p[1])), False) >= 0


def bas_bolgesi(b, k=None) -> tuple[float, float, float, float]:
    """Yüzü örtecek elips (cx, cy, rx, ry): iskelet varsa yüz noktalarından, yoksa kişi kutusunun üst ortası."""
    x1, y1, x2, y2 = b
    w, h = x2 - x1, y2 - y1
    if k:
        yuz = [k[i] for i in range(5) if k[i][2] >= KP]
        if len(yuz) >= 2:
            cx, cy = sum(p[0] for p in yuz) / len(yuz), sum(p[1] for p in yuz) / len(yuz)
            r = max(max(math.hypot(p[0] - cx, p[1] - cy) for p in yuz) * 2.1, 0.09 * h, 8.0)
            r = min(r, max(0.27 * h, 12.0)) * 1.15
            return cx, cy, r, r * 1.2
    r = min(max(0.11 * h, 0.2 * w, 9.0), max(0.27 * h, 12.0)) * 1.15
    return (x1 + x2) / 2, y1 + 0.11 * h, r, r * 1.2


def parcalar(b, k=None) -> dict:
    """Koruyucuların aranacağı vücut bölgeleri: iskelet varsa eller ve ayaklar eklem noktalarından, yoksa kişi kutusundan."""
    x1, y1, x2, y2 = b
    w, h = x2 - x1, y2 - y1
    if k:
        r = max(0.12 * h, 10.0)
        eller = [p for p in (k[9], k[10]) if p[2] >= KP]
        ayaklar = [p for p in (k[15], k[16]) if p[2] >= KP]
        if eller or ayaklar:
            kutu = lambda ns, ust, alt: [min(p[0] for p in ns) - r, min(p[1] for p in ns) - ust * r, max(p[0] for p in ns) + r, max(p[1] for p in ns) + alt * r]  # noqa: E731
            return {
                "bas": [x1 - 0.1 * w, y1 - 0.06 * h, x2 + 0.1 * w, y1 + 0.3 * h],
                "govde": [x1, y1 + 0.12 * h, x2, y1 + 0.68 * h],
                "el": kutu(eller, 1.0, 1.0) if eller else [x1 - 0.15 * w, y1 + 0.25 * h, x2 + 0.15 * w, y1 + 0.8 * h],
                "ayak": kutu(ayaklar, 0.8, 1.3) if ayaklar else [x1 - 0.1 * w, y2 - 0.2 * h, x2 + 0.1 * w, y2 + 0.04 * h],
            }
    return {
        "bas": [x1 - 0.1 * w, y1 - 0.06 * h, x2 + 0.1 * w, y1 + 0.3 * h],
        "govde": [x1, y1 + 0.12 * h, x2, y1 + 0.68 * h],
        "el": [x1 - 0.15 * w, y1 + 0.25 * h, x2 + 0.15 * w, y1 + 0.8 * h],
        "ayak": [x1 - 0.1 * w, y2 - 0.2 * h, x2 + 0.1 * w, y2 + 0.04 * h],
    }


class IzSurucu:
    """Kutuları kareler arasında aynı kimliğe bağlar: önce örtüşme, sonra yakınlık."""

    def __init__(self, omur_sn: float = 1.5, yeni_guven: float = 0.4):
        self.izler: dict[int, dict] = {}
        self.sayac = 0
        self.omur = omur_sn
        self.yeni_guven = yeni_guven

    def guncelle(self, t: float, kutular: list[dict]) -> list[dict]:
        """Her kutuya d["id"] yazar; yeni iz yalnızca yeterince güvenli tespitle açılır. Dönüş: kimliği olan kutular."""
        for z in self.izler.values():  # hıza göre bu karedeki tahmini konum
            d = t - z["t"]
            z["tahmin"] = [z["b"][0] + z["v"][0] * d, z["b"][1] + z["v"][1] * d, z["b"][2] + z["v"][0] * d, z["b"][3] + z["v"][1] * d]
        adaylar = []
        for i, d in enumerate(kutular):
            for kimlik, z in self.izler.items():
                if z["s"] != d["s"]:
                    continue
                x1, y1, x2, y2 = max(d["b"][0], z["tahmin"][0]), max(d["b"][1], z["tahmin"][1]), min(d["b"][2], z["tahmin"][2]), min(d["b"][3], z["tahmin"][3])
                k = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                a = (d["b"][2] - d["b"][0]) * (d["b"][3] - d["b"][1]) + (z["tahmin"][2] - z["tahmin"][0]) * (z["tahmin"][3] - z["tahmin"][1]) - k
                iou = k / (a + 1e-6)
                boy = max(d["b"][3] - d["b"][1], z["b"][3] - z["b"][1], 1.0)
                uzak = math.hypot((d["b"][0] + d["b"][2] - z["tahmin"][0] - z["tahmin"][2]) / 2, d["b"][3] - z["tahmin"][3]) / boy
                if iou >= 0.15:
                    adaylar.append((1.0 + iou, i, kimlik))
                elif uzak <= 0.6:
                    adaylar.append((1.0 - uzak, i, kimlik))
        atanan_kutu, atanan_iz = {}, set()
        for _, i, kimlik in sorted(adaylar, reverse=True):
            if i in atanan_kutu or kimlik in atanan_iz:
                continue
            atanan_kutu[i] = kimlik
            atanan_iz.add(kimlik)
        out = []
        for i, d in enumerate(kutular):
            kimlik = atanan_kutu.get(i)
            if kimlik is None:
                if d["p"] < self.yeni_guven:
                    continue
                self.sayac += 1
                kimlik = self.sayac
                self.izler[kimlik] = {"b": d["b"], "t": t, "s": d["s"], "v": (0.0, 0.0), "ilk": t, "n": 0}
            z = self.izler[kimlik]
            gecen = t - z["t"]
            if gecen > 0 and z["n"] > 0:
                vx = ((d["b"][0] + d["b"][2]) - (z["b"][0] + z["b"][2])) / 2 / gecen
                vy = (d["b"][3] - z["b"][3]) / gecen
                z["v"] = (0.6 * z["v"][0] + 0.4 * vx, 0.6 * z["v"][1] + 0.4 * vy)
            z["b"], z["t"], z["n"] = d["b"], t, z["n"] + 1
            d["id"] = kimlik
            out.append(d)
        for kimlik in [k for k, z in self.izler.items() if t - z["t"] > self.omur]:
            del self.izler[kimlik]
        return out


class OlayYonetici:
    """Bir kuralın durumlarını izler: koşul `sure_sn` sürerse olay başlar, iz kimliği değişse de aynı olay sürer."""

    def __init__(self, kural: dict, kare_araligi: float):
        self.k = kural
        self.sure = float(kural.get("sure_sn", 1.0))
        self.bosluk = max(1.0, 3 * kare_araligi, 0.5 * self.sure)
        self.aday: dict = {}
        self.acik: list[dict] = []
        self.sayi = 0  # bu kuraldan kaç olay başladı

    def guncelle(self, t: float, aktif: dict) -> tuple[list[dict], list[dict], list[dict]]:
        baslayan, biten, vurgu = [], [], []
        for anahtar, b in aktif.items():
            a = self.aday.get(anahtar)
            if a is None:
                a = self.aday[anahtar] = {"bas": t, "olay": None}
            a["son"] = t
            if t - a["bas"] < self.sure:
                continue
            o = a["olay"]
            if o is None or o.get("kapandi"):
                o = next((x for x in self.acik if x["bolge"] == b.get("bolge") and t - x["son"] <= 2.0
                          and math.hypot(x["m"][0] - b["xy"][0], x["m"][1] - b["xy"][1]) < 0.15), None)
                if o is None:
                    self.sayi += 1
                    o = {"kimlik": uuid.uuid4().hex, "kural": self.k["kod"], "bas": a["bas"], "son": t, "kisi": 0, "xy": [], "m": b["xy"],
                         "bolge": b.get("bolge"), "ek": b.get("ek"), "sira": self.sayi}
                    self.acik.append(o)
                    baslayan.append(o)
                a["olay"] = o
            o["son"] = t
            o["kisi"] = max(o["kisi"], b.get("kisi", 1))
            o["xy"].append(b["xy"])
            o["m"] = tuple(np.mean(o["xy"][-40:], axis=0))
            if b.get("ek") is not None:
                o["ek"] = b["ek"]
            vurgu += b.get("kutular", [])
        for anahtar in [a for a, d in self.aday.items() if a not in aktif and t - d["son"] > self.bosluk]:
            del self.aday[anahtar]
        for o in [o for o in self.acik if t - o["son"] > max(self.bosluk, 2.0)]:
            self.acik.remove(o)
            o["kapandi"] = True
            biten.append(o)
        return baslayan, biten, vurgu

    def bitir(self) -> list[dict]:
        biten, self.acik, self.aday = self.acik, [], {}
        for o in biten:
            o["kapandi"] = True
        return biten


def _saat_araliginda(an: dt.datetime, araliklar: list[str]) -> bool:
    dk = an.hour * 60 + an.minute
    for a in araliklar:
        try:
            x, y = a.split("-")
            b1, b2 = int(x[:2]) * 60 + int(x[3:5]), int(y[:2]) * 60 + int(y[3:5])
        except Exception:
            continue
        if (b1 <= dk < b2) if b1 < b2 else (dk >= b1 or dk < b2):
            return True
    return False


class Motor:
    """Bir kameranın kural motoru. `adim()` her kare için çağrılır."""

    def __init__(self, ayar: dict, W: int, H: int, bas_zaman: dt.datetime, kare_araligi: float = 0.125):
        self.ayar, self.W, self.H = ayar, W, H
        self.bas_zaman = bas_zaman
        self.aralik = kare_araligi
        olcek = np.array([W, H], dtype=np.float32)
        self.bolgeler = [{"ad": b.get("ad", "Bölge"), "tur": b.get("tur", ""), "poly": np.array(b["poly"], dtype=np.float32) * olcek,
                          "yon": b.get("yon_derece")} for b in ayar.get("bolgeler") or [] if len(b.get("poly") or []) >= 3]
        self.tur_bolgeleri = defaultdict(list)
        for b in self.bolgeler:
            self.tur_bolgeleri[b["tur"]].append(b)
        self.kurallar = katalog.coz(ayar)
        self.yoneticiler = {k["kod"]: OlayYonetici(k, kare_araligi) for k in self.kurallar}
        self.kisi_iz, self.arac_iz, self.nesne_iz = IzSurucu(), IzSurucu(2.0), IzSurucu(4.0, 0.3)
        self.nesne_ilk: dict = {}  # nesne izi → ilk görüldüğü yer (yerinden oynarsa süre baştan başlar)
        self.yetkili_renkler = renk.coz(ayar["yetkili_renkleri"]) if ayar.get("yetkili_renkleri") else []
        self.renk_gecmis = defaultdict(lambda: deque(maxlen=40))  # kişi → son karelerde yetkili renk görüldü mü
        self.gecmis = defaultdict(lambda: deque(maxlen=64))      # (tur, id) → (t, x, y, boy)
        self.kkd_gecmis = defaultdict(lambda: deque(maxlen=64))  # (kural, id) → (t, durum)
        self.son_t = None
        self._sifirla_olcum()

    # ---------------------------------------------------------------- ölçüm
    def _sifirla_olcum(self) -> None:
        self.o_bas = None
        self.o_izgara = {"kisi": np.zeros((IZGARA[1], IZGARA[0]), np.float32), "arac": np.zeros((IZGARA[1], IZGARA[0]), np.float32)}
        self.o_iz_nokta, self.o_iz_son, self.o_ilk, self.o_son = defaultdict(list), {}, {}, {}
        self.o_kisi_seri: list[int] = []
        self.o_uyumlu = self.o_uyumsuz = 0.0
        self.o_sure = 0.0

    def olcum(self) -> dict | None:
        """Son çağrıdan bu yana biriken sayılar (ısı haritası, güzergâh, uyum süreleri); sayaçlar sıfırlanır."""
        if self.o_bas is None or not self.o_kisi_seri:
            return None
        izler = [{"s": tur, "p": n} for (tur, kimlik), n in self.o_iz_nokta.items()
                 if len(n) >= 4 and self.o_son[(tur, kimlik)] - self.o_ilk[(tur, kimlik)] >= 1.5]
        izler = sorted(izler, key=lambda z: -len(z["p"]))[:300]
        yasayan = lambda tur, en_az: sum(1 for a in self.o_ilk if a[0] == tur and self.o_son[a] - self.o_ilk[a] >= en_az)  # noqa: E731
        out = {
            "bas": (self.bas_zaman + dt.timedelta(seconds=self.o_bas)).replace(microsecond=0).isoformat(), "sure_sn": round(self.o_sure, 2),
            "kisi_sn": round(float(np.sum(self.o_kisi_seri)) * self.aralik, 1), "kisi_iz": yasayan("kisi", 1.5),
            "tepe_kisi": int(np.percentile(self.o_kisi_seri, 98)), "arac_iz": yasayan("arac", 1.0),
            "kkd_uyumlu_sn": round(self.o_uyumlu, 1), "kkd_uyumsuz_sn": round(self.o_uyumsuz, 1),
            "izgara": {"gen": IZGARA[0], "yuk": IZGARA[1], "kisi": [[round(float(v), 1) for v in s] for s in self.o_izgara["kisi"]],
                       "arac": [[round(float(v), 1) for v in s] for s in self.o_izgara["arac"]]},
            "izler": izler,
        }
        self._sifirla_olcum()
        return out

    # ---------------------------------------------------------------- yardımcılar
    def _hiz(self, anahtar, pencere=0.6):
        g = self.gecmis[anahtar]
        if len(g) < 2:
            return 0.0, (0.0, 0.0), g[-1][3] if g else 1.0
        son = g[-1]
        ilk = next((x for x in g if son[0] - x[0] <= pencere), g[0])
        if son[0] - ilk[0] < 0.25:
            ilk = g[max(0, len(g) - 4)]
        d = son[0] - ilk[0]
        if d <= 0:
            return 0.0, (0.0, 0.0), son[3]
        vx, vy = (son[1] - ilk[1]) / d, (son[2] - ilk[2]) / d
        return math.hypot(vx, vy), (vx, vy), float(np.median([x[3] for x in g if son[0] - x[0] <= pencere + 0.01]) or son[3])

    def _kuralin_bolgeleri(self, k: dict) -> list[dict]:
        bs = self.tur_bolgeleri.get(k.get("bolge_turu", ""), [])
        return [b for b in bs if b["ad"] == k["bolge"]] if k.get("bolge") else bs

    def _kkd(self, kisiler: list[dict], nesneler: list[dict]) -> None:
        """Her kişiye o karede görülen koruyucuları yazar: p["kkd"] = {"baret": True (var) | False (takılmadığı görüldü)}."""
        for p in kisiler:
            p["kkd"] = {}
            p["parca"] = parcalar(p["b"], p.get("k"))
        for n in nesneler:
            olumsuz = n["s"].endswith("_yok")
            ana = n["s"][:-4] if olumsuz else n["s"]
            parca = PARCA.get(ana)
            if parca is None:
                continue
            mx, my = (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2
            en_iyi, en = None, 1e9
            for p in kisiler:
                x1, y1, x2, y2 = p["parca"][parca]
                if x1 <= mx <= x2 and y1 <= my <= y2:
                    d = math.hypot(mx - (x1 + x2) / 2, my - (y1 + y2) / 2)
                    if d < en:
                        en_iyi, en = p, d
            if en_iyi is None:
                continue
            if olumsuz:
                if n["p"] >= OLUMSUZ_GUVEN:
                    en_iyi["kkd"].setdefault(ana, False)
            else:
                en_iyi["kkd"][ana] = True  # "var" kanıtı "yok" kanıtına üstün gelir
        for p in kisiler:  # kişi öznitelik modeli (önlük, tulum, kolluk, kemer, kulaklık)
            for s, olasilik in (p.get("oz") or {}).items():
                if olasilik >= OZ_VAR:
                    p["kkd"][s] = True
                elif olasilik <= OZ_YOK:
                    p["kkd"].setdefault(s, False)

    # ---------------------------------------------------------------- kare
    def adim(self, t: float, tespitler: list[dict], bgr: np.ndarray | None = None) -> dict:
        """Dönüş: {"kisiler", "araclar", "vurgu": [...], "baslayan": [olay], "biten": [olay], "acik": [olay]}."""
        W, H = self.W, self.H
        an = self.bas_zaman + dt.timedelta(seconds=t)
        dtk = self.aralik if self.son_t is None else min(1.0, max(0.0, t - self.son_t))
        self.son_t = t
        kisiler = self.kisi_iz.guncelle(t, [dict(d) for d in tespitler if d["s"] == "kisi"])
        araclar = self.arac_iz.guncelle(t, [dict(d) for d in tespitler if d["s"] in ARACLAR])
        nesneler = [d for d in tespitler if d["s"] != "kisi" and d["s"] not in ARACLAR]
        duranlar = self.nesne_iz.guncelle(t, [dict(d) for d in tespitler if d["s"] in NESNELER])
        hsv = None
        self._kkd(kisiler, nesneler)
        for p in kisiler:
            p["a"] = ayak(p["b"], p.get("k"))
            p["h"] = max(1.0, p["b"][3] - p["b"][1])
        for a in araclar:
            a["a"] = ((a["b"][0] + a["b"][2]) / 2, float(a["b"][3]))
            a["h"] = max(1.0, a["b"][3] - a["b"][1])
        for p in kisiler:  # araç kutusunun içindeki kişi sürücüdür: yaya sayılmaz
            p["surucu"] = any(ayni_kutu(p["b"], a["b"], iou=2.0, icerme=0.6) and p["a"][1] <= a["b"][3] - 0.08 * a["h"]
                              and (p["b"][2] - p["b"][0]) * p["h"] < (a["b"][2] - a["b"][0]) * a["h"] for a in araclar)
        yayalar = [p for p in kisiler if not p["surucu"]]

        # --- sayaçlar, ısı haritası, güzergâh
        if self.o_bas is None:
            self.o_bas = t
        self.o_sure = t - self.o_bas + self.aralik
        self.o_kisi_seri.append(len(kisiler))
        for tur, liste in (("kisi", kisiler), ("arac", araclar)):
            for o in liste:
                anahtar = (tur, o["id"])
                self.gecmis[anahtar].append((t, o["a"][0], o["a"][1], o["h"]))
                self.o_ilk.setdefault(anahtar, t)
                self.o_son[anahtar] = t
                gx = min(IZGARA[0] - 1, max(0, int(o["a"][0] / W * IZGARA[0])))
                gy = min(IZGARA[1] - 1, max(0, int(o["a"][1] / H * IZGARA[1])))
                self.o_izgara[tur][gy, gx] += dtk
                if t - self.o_iz_son.get(anahtar, -9) >= 0.5:
                    self.o_iz_son[anahtar] = t
                    self.o_iz_nokta[anahtar].append([round(o["a"][0] / W, 4), round(o["a"][1] / H, 4)])

        kutu = lambda o, tur="kisi": {"b": o["b"], "tur": tur}  # noqa: E731
        konum = lambda o: (o["a"][0] / W, o["a"][1] / H)  # noqa: E731
        kkd_bolgeleri = self.tur_bolgeleri.get("kkd", [])
        baslayan, biten, vurgu = [], [], []
        for k in self.kurallar:
            mek, aktif = k["mekanizma"], {}
            if mek == "kkd":
                sinif = k["sinif"]
                # kuralın kendi bölge türü varsa (emniyet kemeri: yüksekte çalışma alanı) yalnızca o bölgelerde, yoksa KKD zorunlu alanlarda aranır
                alanlar = self.tur_bolgeleri.get(k["bolge_turu"], []) if k.get("bolge_turu") else kkd_bolgeleri
                for p in yayalar:
                    if p["h"] < KKD_EN_AZ_BOY or ((alanlar or k.get("bolge_turu")) and not any(icinde(b["poly"], p["a"]) for b in alanlar)):
                        continue
                    durum = p["kkd"].get(sinif)
                    if durum is None and k.get("kanit") == "yokluk" and p["h"] >= k.get("en_az_boy", 90) and p.get("k"):
                        durum = False  # kişi net görünüyor ama koruyucu bulunamadı
                    if durum is None:
                        continue
                    if durum:
                        self.o_uyumlu += dtk
                    else:
                        self.o_uyumsuz += dtk
                    g = self.kkd_gecmis[(k["kod"], p["id"])]
                    g.append((t, durum))
                    son = [d for tt, d in g if t - tt <= 2.0]
                    if len(son) >= 3 and np.mean([not d for d in son]) >= 0.7:
                        bolge = next((b["ad"] for b in alanlar if icinde(b["poly"], p["a"])), None)
                        aktif[p["id"]] = {"xy": konum(p), "kutular": [kutu(p)], "bolge": bolge}
            elif mek == "durus":  # iskeletten: yerde yatan kişi (gövde yataya yakın, kutu enine)
                for p in yayalar:
                    if not p.get("k") or p["h"] < 30:
                        continue
                    aci = govde_acisi(p["k"])
                    if aci is not None and aci >= 60 and (p["b"][2] - p["b"][0]) >= 1.1 * p["h"]:
                        aktif[p["id"]] = {"xy": konum(p), "kutular": [kutu(p)]}
            elif mek == "ergonomi":  # iskeletten REBA puanı: son 3 saniyenin ortancası eşiğin üstündeyse
                for p in yayalar:
                    if not p.get("k") or p["h"] < KKD_EN_AZ_BOY:
                        continue
                    puan = reba(p["k"])
                    if puan is None:
                        continue
                    g = self.kkd_gecmis[("reba", p["id"])]
                    g.append((t, puan))
                    son = [d for tt, d in g if t - tt <= 3.0]
                    if len(son) >= 6 and float(np.median(son)) >= k["esik"]:
                        aktif[p["id"]] = {"xy": konum(p), "kutular": [kutu(p)], "ek": int(np.median(son))}
            elif mek == "bolge":
                if not k.get("saatler") or _saat_araliginda(an, k["saatler"]):
                    ozneler = yayalar if k.get("ozne", "kisi") == "kisi" else araclar
                    renkli = bool(k.get("yetkili_renk"))
                    if renkli and (not self.yetkili_renkler or bgr is None):
                        ozneler = []  # yetkili renk seçilmemişse kimin yetkisiz olduğu söylenemez
                    for b in self._kuralin_bolgeleri(k):
                        for o in ozneler:
                            if not icinde(b["poly"], o["a"]):
                                continue
                            if renkli:  # kıyafetinde yetkili renk görülen kişi uyarı vermez
                                if hsv is None:
                                    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                                g = self.renk_gecmis[o["id"]]
                                g.append(renk.puan(hsv, o["b"], o.get("k") or BOS_ISKELET, self.yetkili_renkler) > 1.0)
                                if len(g) < 4 or np.mean(g) >= 0.35:
                                    continue
                            aktif[(b["ad"], o["id"])] = {"xy": konum(o), "kutular": [kutu(o, k.get("ozne", "kisi"))], "bolge": b["ad"]}
            elif mek == "sayi":
                for b in self._kuralin_bolgeleri(k):
                    icindekiler = [p for p in kisiler if icinde(b["poly"], p["a"])]
                    n = len(icindekiler)
                    if ("en_cok" in k and n > k["en_cok"]) or ("en_az" in k and n < k["en_az"]):
                        m = b["poly"].mean(0)
                        aktif[b["ad"]] = {"xy": (float(m[0]) / W, float(m[1]) / H), "kisi": max(1, n), "kutular": [kutu(p) for p in icindekiler],
                                          "bolge": b["ad"], "ek": n}
            elif mek == "birlikte":
                for b in self._kuralin_bolgeleri(k):
                    ps = [p for p in yayalar if icinde(b["poly"], p["a"])]
                    as_ = [a for a in araclar if icinde(b["poly"], a["a"])]
                    if ps and as_:
                        aktif[b["ad"]] = {"xy": konum(ps[0]), "kisi": len(ps), "bolge": b["ad"],
                                          "kutular": [kutu(p) for p in ps] + [kutu(a, "arac") for a in as_]}
            elif mek == "yakinlik":
                A = araclar if k["a"] == "arac" else yayalar
                B = araclar if k["b"] == "arac" else yayalar
                for x in A:
                    for y in B:
                        if x is y or (k["a"] == k["b"] and x["id"] >= y["id"]):
                            continue
                        if k["a"] == "arac" and k["b"] == "kisi":
                            v, _, _ = self._hiz(("arac", x["id"]))
                            if v < 0.15 * x["h"]:  # duran araç yaya için bu kuralda tehlike sayılmaz
                                continue
                            dx = max(x["b"][0] - y["a"][0], 0.0, y["a"][0] - x["b"][2])
                            d = math.hypot(dx, y["a"][1] - x["b"][3])
                            olcu = y["h"]
                        elif k["a"] == "arac":  # iki araç: kutuların yan aralığı ve zemindeki derinlik farkı
                            dx = max(0.0, x["b"][0] - y["b"][2], y["b"][0] - x["b"][2])
                            d = math.hypot(dx, x["b"][3] - y["b"][3])
                            olcu = (x["h"] + y["h"]) / 2
                        else:
                            d = math.hypot(x["a"][0] - y["a"][0], x["a"][1] - y["a"][1])
                            olcu = (x["h"] + y["h"]) / 2
                            if max(x["h"], y["h"]) > 1.6 * min(x["h"], y["h"]):  # biri önde biri arkada
                                continue
                        if d <= k["esik"] * olcu:
                            aktif[(x["id"], y["id"])] = {"xy": konum(y), "kisi": 2 if k["a"] == "kisi" else 1,
                                                         "kutular": [kutu(x, k["a"]), kutu(y, k["b"])]}
            elif mek == "nesne":  # yerinde duran nesne: bölgede (engel, yasak nesne) ya da yanında kimse yokken (sahipsiz)
                bolgeler = self._kuralin_bolgeleri(k) if k.get("bolge_turu") else None
                for n in duranlar:
                    if n["s"] not in k.get("siniflar", ()):
                        continue
                    cx, alt = (n["b"][0] + n["b"][2]) / 2, n["b"][3]
                    boyut = max(n["b"][2] - n["b"][0], n["b"][3] - n["b"][1], 1.0)
                    if n["s"] in ELDE:  # elde tutulan nesne: yeri, tutan kişinin bastığı yerdir; elde olduğu için "yerinde durma" aranmaz
                        tutan = next((p for p in kisiler if p["b"][0] <= cx <= p["b"][2] and p["b"][1] <= (n["b"][1] + alt) / 2 <= p["b"][3]), None)
                        if tutan is None:
                            continue
                        bolge = next((b for b in bolgeler or [] if icinde(b["poly"], tutan["a"])), None)
                        if bolgeler is not None and bolge is None:
                            continue
                        aktif[(k["kod"], "kisi", tutan["id"])] = {"xy": konum(tutan), "kisi": 1, "bolge": bolge["ad"] if bolge else None,
                                                                 "kutular": [{"b": n["b"], "tur": "nesne"}, kutu(tutan)]}
                        continue
                    ilk = self.nesne_ilk.get(n["id"])
                    if ilk is None or math.hypot(cx - ilk[0], alt - ilk[1]) > 0.25 * boyut:
                        self.nesne_ilk[n["id"]] = (cx, alt)  # yeni geldi ya da yerinden oynadı
                        continue
                    bolge = None
                    if bolgeler is not None:
                        bolge = next((b for b in bolgeler if icinde(b["poly"], (cx, alt))), None)
                        if bolge is None:
                            continue
                    if k.get("sahipsiz") and any(math.hypot(p["a"][0] - cx, p["a"][1] - alt) < 1.5 * max(p["h"], boyut) for p in kisiler):
                        continue
                    aktif[(k["kod"], n["id"])] = {"xy": (cx / W, alt / H), "kisi": 1, "kutular": [{"b": n["b"], "tur": "nesne"}],
                                                  "bolge": bolge["ad"] if bolge else None}
            elif mek == "uc_nokta":  # merdivendeki kişinin el ve ayaklarından üçten azı merdivenin üstünde
                # merdivenler: çizilen "merdiven" bölgeleri (sabit) ve ek modelin bulduğu merdivenler (seyyar; üstündeki kişiyi de kapsayacak kadar yukarı uzatılır)
                alanlar = [(b["ad"], b["poly"], None) for b in self.tur_bolgeleri.get("merdiven", []) if not k.get("bolge") or b["ad"] == k["bolge"]]
                for n in nesneler:
                    if n["s"] == "merdiven":
                        x1, y1, x2, y2 = n["b"]
                        w, h = x2 - x1, y2 - y1
                        alanlar.append((None, np.array([[x1 - 0.1 * w, y1 - 0.35 * h], [x2 + 0.1 * w, y1 - 0.35 * h], [x2 + 0.1 * w, y2], [x1 - 0.1 * w, y2]],
                                                       dtype=np.float32), y2 - 0.15 * h))
                yukler = [n["b"] for n in nesneler if n["s"] in YUK]
                dolu = lambda u: any(b[0] - 0.1 * (b[2] - b[0]) <= u[0] <= b[2] + 0.1 * (b[2] - b[0])  # noqa: E731
                                     and b[1] - 0.1 * (b[3] - b[1]) <= u[1] <= b[3] + 0.1 * (b[3] - b[1]) for b in yukler)
                for ad, poly, taban in alanlar:
                    for p in yayalar:
                        ik = p.get("k")
                        if not ik:
                            continue
                        kalca = _orta(ik, (11, 12))
                        if kalca is None or not icinde(poly, kalca):
                            continue
                        ayaklar = [ik[i] for i in (15, 16) if ik[i][2] >= KP]
                        if taban is not None and (not ayaklar or max(u[1] for u in ayaklar) > taban):
                            continue  # ayakları merdivenin dibinde: yerde duruyor
                        eller = [ik[i] for i in (9, 10) if ik[i][2] >= KP]
                        if len(eller) + len(ayaklar) < 3:
                            continue
                        temas = sum(1 for u in ayaklar if icinde(poly, (u[0], u[1]))) + sum(1 for u in eller if icinde(poly, (u[0], u[1])) and not dolu(u))
                        if temas < 3:
                            aktif[(ad or "merdiven", p["id"])] = {"xy": konum(p), "kutular": [kutu(p)], "bolge": ad}
            elif mek == "hiz":
                for p in yayalar:
                    v, _, boy = self._hiz(("kisi", p["id"]))
                    if v / boy >= k["esik"] and len(self.gecmis[("kisi", p["id"])]) >= 4:
                        aktif[p["id"]] = {"xy": konum(p), "kutular": [kutu(p)]}
            elif mek == "yol_disi":
                yol = self._kuralin_bolgeleri(k)
                if yol and k.get("ozne", "kisi") == "kisi":
                    arac_yollari = self.tur_bolgeleri.get("arac_yolu", [])
                    for p in yayalar:
                        if any(icinde(b["poly"], p["a"]) for b in yol):
                            continue
                        b = next((b for b in arac_yollari if icinde(b["poly"], p["a"])), None)
                        if arac_yollari and b is None:
                            continue  # araç yolları çiziliyse yalnızca araç yolunda yürüyen yaya uyarı verir
                        aktif[p["id"]] = {"xy": konum(p), "kutular": [kutu(p)], "bolge": b["ad"] if b else None}
                elif yol:
                    for a in araclar:
                        if not any(icinde(b["poly"], a["a"]) for b in yol):
                            aktif[a["id"]] = {"xy": konum(a), "kutular": [kutu(a, "arac")]}
            elif mek == "ters_yon":
                ozne = k.get("ozne", "arac")
                for b in self._kuralin_bolgeleri(k):
                    if b["yon"] is None:
                        continue
                    yx, yy = math.cos(math.radians(b["yon"])), math.sin(math.radians(b["yon"]))
                    for o in (araclar if ozne == "arac" else yayalar):
                        if not icinde(b["poly"], o["a"]):
                            continue
                        v, (vx, vy), boy = self._hiz((ozne, o["id"]), 1.0)
                        if v >= 0.25 * boy and (vx * yx + vy * yy) / v <= -0.5:
                            aktif[(b["ad"], o["id"])] = {"xy": konum(o), "kutular": [kutu(o, ozne)], "bolge": b["ad"]}
            bas, bit, vur = self.yoneticiler[k["kod"]].guncelle(t, aktif)
            for v in vur:
                v["etiket"] = k["ad"]
            vurgu += vur
            baslayan += [self._olay(k, o, t) for o in bas]
            biten += [self._olay(k, o, o["son"], bitti=True) for o in bit]
        acik = [o for y in self.yoneticiler.values() for o in y.acik]
        return {"kisiler": kisiler, "araclar": araclar, "vurgu": vurgu, "baslayan": baslayan, "biten": biten, "acik": acik}

    def _olay(self, k: dict, o: dict, t: float, bitti: bool = False) -> dict:
        sure = max(self.aralik, t - o["bas"] + self.aralik)
        olas = katalog.olasilik(sure, o["sira"])
        metin = k["aciklama"]
        if k["mekanizma"] == "ergonomi" and o.get("ek") is not None:
            metin = f"REBA duruş puanı {o['ek']} (eşik {k['esik']}). " + k["aciklama"]
        elif k["mekanizma"] == "sayi" and o.get("ek") is not None:
            sinir = f"en çok {k['en_cok']}" if "en_cok" in k else f"en az {k['en_az']}"
            metin = f"{o['bolge']}: {o['ek']} kişi görüldü (sınır: {sinir})."
        return {
            "kimlik": o["kimlik"], "bitti": bitti, "kural": k["kod"], "kural_ad": k["ad"], "kategori": k["kategori"], "bolge": o["bolge"],
            "onem": int(k["onem"]), "olasilik": olas, "puan": katalog.risk(k["onem"], olas), "ciddi": int(bool(k.get("ciddi"))),
            "zaman": (self.bas_zaman + dt.timedelta(seconds=o["bas"])).replace(microsecond=0).isoformat(), "bas": round(o["bas"], 2),
            "sure_sn": round(sure, 1), "kisi": int(max(1, o["kisi"])), "x": round(float(o["m"][0]), 4), "y": round(float(o["m"][1]), 4), "aciklama": metin,
        }

    def kapat(self) -> list[dict]:
        """Açık kalan olayları kapatır (akış bittiğinde)."""
        biten = []
        for k in self.kurallar:
            biten += [self._olay(k, o, o["son"], bitti=True) for o in self.yoneticiler[k["kod"]].bitir()]
        return biten
