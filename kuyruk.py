"""AugeLab Studio'daki motorun bıraktığı kayıtları (veri/kuyruk/*.json) veritabanına alır.

Kayıt türleri:
  olay    bir olay başladı (bitti = false) ya da kapandı (bitti = true); aynı `kimlik` ile gelir
  olcum   bir zaman diliminin sayıları (ısı haritası, güzergâh, KKD uyum süreleri)
  bitti   bir video dosyasının akışı bitti
Klipler motor tarafından ham (mp4v) yazılır; burada tarayıcıda oynayan H.264'e çevrilir.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import depo
import yollar

_GIZLI = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
KLASOR = yollar.VERI / "kuyruk"
# Koşuya göre ayrı tutulur: aynı kamerada önceki koşudan geç gelen bir kayıt, güncel koşunun kaydının üstüne yazılmaz.
BITENLER: dict[str, dict[str, float]] = {}  # kamera → {koşu numarası: akışının bittiği an}
SON_KAYIT: dict[str, float] = {}            # kamera → son kayıt alınan an
HATALAR: dict[str, dict[str, str]] = {}     # kamera → {koşu numarası: neden}: Studio'daki tespit çalışmıyor, görüntü kaydedilmiyor
_KILIT = threading.Lock()


def _klibi_cevir(goreli: str | None) -> str | None:
    """cikti/ altındaki ham klibi H.264'e çevirir; yeni göreli yolu döndürür."""
    if not goreli:
        return None
    ham = yollar.CIKTI / goreli
    if not goreli.endswith(".ham.mp4"):
        return goreli if ham.exists() else None
    hedef = ham.with_name(ham.name[:-len(".ham.mp4")] + ".mp4")
    if not ham.exists():
        return None
    try:
        subprocess.run([yollar.ffmpeg(), "-y", "-loglevel", "error", "-i", str(ham), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
                        "-movflags", "+faststart", str(hedef)], check=True, stdin=subprocess.DEVNULL, timeout=120, **_GIZLI)
    except Exception:
        return None
    ham.unlink(missing_ok=True)
    return str(hedef.relative_to(yollar.CIKTI)).replace("\\", "/")


def _olay(k: dict, ornek: bool) -> int | None:
    """Olayı ekler ya da günceller. Yeni eklenen olayın numarasını döndürür."""
    klip = _klibi_cevir(k.get("klip")) if k.get("bitti") else None
    var = depo.sor("SELECT id, kare FROM olay WHERE kimlik = ?", k["kimlik"])
    if var:
        depo.yaz("UPDATE olay SET sure_sn = ?, olasilik = ?, puan = ?, kisi = ?, x = ?, y = ?, aciklama = ?, klip = COALESCE(?, klip), "
                 "kare = COALESCE(?, kare), suruyor = ? WHERE id = ?",
                 k["sure_sn"], k["olasilik"], k["puan"], k["kisi"], k["x"], k["y"], k["aciklama"], klip, k.get("kare"), 0 if k.get("bitti") else 1, var[0]["id"])
        return None
    return depo.ekle("olay", {
        "kimlik": k["kimlik"], "zaman": k["zaman"], "kamera": k["kamera"], "kamera_ad": k.get("kamera_ad") or k["kamera"], "kural": k["kural"],
        "kural_ad": k["kural_ad"], "kategori": k["kategori"], "bolge": k.get("bolge"), "onem": k["onem"], "olasilik": k["olasilik"], "puan": k["puan"],
        "ciddi": k["ciddi"], "sure_sn": k["sure_sn"], "kisi": k["kisi"], "x": k["x"], "y": k["y"], "klip": klip, "kare": k.get("kare"),
        "aciklama": k["aciklama"], "ornek": int(ornek), "suruyor": 0 if k.get("bitti") else 1,
    })


def al() -> list[int]:
    """Kuyruktaki kayıtları sırayla içeri alır. Dönüş: yeni başlayan olayların numaraları (çıkışlara iletilmek üzere)."""
    yeni: list[int] = []
    if not KLASOR.is_dir():
        return yeni
    ornekler = set(yollar.ornek_kodlar())
    with _KILIT:
        for dosya in sorted(KLASOR.glob("*.json")):
            try:
                k = json.loads(dosya.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # yazımı sürüyor olabilir; bir sonraki turda
            try:
                kamera = k.get("kamera", "")
                SON_KAYIT[kamera] = time.time()
                if k.get("tur") == "olay":
                    kimlik = _olay(k, kamera in ornekler)
                    if kimlik is not None:
                        yeni.append(kimlik)
                elif k.get("tur") == "olcum":
                    depo.ekle("olcum", {
                        "kamera": kamera, "kamera_ad": k.get("kamera_ad") or kamera, "bas": k["bas"], "sure_sn": k["sure_sn"], "kisi_sn": k["kisi_sn"],
                        "kisi_iz": k["kisi_iz"], "tepe_kisi": k["tepe_kisi"], "arac_iz": k["arac_iz"], "kkd_uyumlu_sn": k["kkd_uyumlu_sn"],
                        "kkd_uyumsuz_sn": k["kkd_uyumsuz_sn"], "ihlal_sn": 0, "izgara": k["izgara"], "izler": k["izler"], "son_kare": k.get("son_kare"),
                        "kaynak": "studio", "ornek": int(kamera in ornekler),
                    })
                elif k.get("tur") == "bitti":
                    BITENLER.setdefault(kamera, {})[str(k.get("kosu") or "")] = time.time()
                elif k.get("tur") == "hata":
                    HATALAR.setdefault(kamera, {})[str(k.get("kosu") or "")] = str(k.get("metin") or "bilinmiyor")
            finally:
                dosya.unlink(missing_ok=True)
    return yeni


def _kosunun(kayitlar: dict | None, kosu: str):
    """Bu koşunun kaydı: yalnızca numarası tutan kayıt. Başka koşunun ya da numarasız bir kaydın numaralı koşuya sayılması
    engellenir. Koşu numarası verilmediyse (geliştirme komutları) numarasız kayıt, o da yoksa kameranın herhangi bir kaydı sayılır."""
    if not kayitlar:
        return None
    if kosu:
        return kayitlar.get(kosu)
    return kayitlar[""] if "" in kayitlar else next(iter(kayitlar.values()))


def kosu_bitti(kod: str, kosu: str = "") -> bool:
    """Bu koşunun akışı bitti mi ("bitti" kaydı içeri alındı mı)."""
    return _kosunun(BITENLER.get(kod), kosu) is not None


def kosu_hatasi(kod: str, kosu: str = "") -> str | None:
    """Bu koşuda Studio'daki tespitin çalışmadığını bildiren kayıt geldiyse nedeni; önceki koşudan kalan kayıt sayılmaz."""
    return _kosunun(HATALAR.get(kod), kosu)


BOSALMA_SN = 300.0  # bosalsin'in varsayılan bekleme süresi


def bosalsin(kod: str, en_cok: float | None = None, sakin: float = 1.5) -> bool:
    """Bu kameranın kuyruktaki bütün kayıtları içeri alınana kadar bekler. Sahne durdurulduktan sonra çağrılır.

    Boş sayılması için kuyrukta bu kameraya ait dosya (yazımı süren .tmp dahil) `sakin` saniye boyunca hiç bulunmamalı ve
    içeri alma kilidi boşta olmalıdır: içeri alma, klip dönüştürme dahil, kilit altında yapılır. `en_cok` saniye (verilmezse
    BOSALMA_SN) dolarsa False döner; çağıran bu durumda temizlik yapmamalıdır (işlenmemiş kayıtlar ve dönüştürülmemiş klipler
    olabilir). Süre monotonik saatle ölçülür ve kilit, kalan süre kadar beklenerek edinilir: uzun süren bir klip dönüştürme
    kilidi tutarken de işlev süresini aşmaz."""
    son = time.monotonic() + (BOSALMA_SN if en_cok is None else en_cok)
    bos_basi = None
    while True:
        kalan = son - time.monotonic()
        if kalan <= 0 or not _KILIT.acquire(timeout=kalan):
            return False
        try:
            bos = not KLASOR.is_dir() or not any(KLASOR.glob(f"*_{kod}_*"))
        finally:
            _KILIT.release()
        simdi = time.monotonic()
        if not bos:
            bos_basi = None
        elif bos_basi is None:
            bos_basi = simdi
        elif simdi - bos_basi >= sakin:
            return True
        time.sleep(max(0.0, min(0.2, son - time.monotonic())))


def surekli(dur: threading.Event, yeni_olay=None) -> None:
    """Arka plan döngüsü: kuyruğu saniyede bir boşaltır; `yeni_olay(numaralar)` çıkışları tetikler."""
    while not dur.is_set():
        try:
            yeni = al()
            if yeni and yeni_olay is not None:
                yeni_olay(yeni)
        except Exception:
            import sys
            import traceback

            traceback.print_exc(file=sys.__stderr__)
        dur.wait(1.0)
