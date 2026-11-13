"""İSG Kamera'nın çevrimdışı, imzalı model lisansı."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from . import cihaz_anahtari

LISANS = "model.lic"
IMZALAYAN = "model-imzalayan.rsa"
SINIR = 16_384
IMZA_ALANI = b"ISG-Kamera/model-lisans/v1\x00"
ISTEK_BICIMI = "isg-kamera-cihaz-v1"
LISANS_BICIMI = "isg-kamera-model-v1"


class LisansHatasi(Exception):
    pass


def json_baytlari(veri: dict) -> bytes:
    return json.dumps(veri, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def kodla(veri: bytes) -> str:
    return base64.b64encode(veri).decode("ascii")


def coz64(veri: str) -> bytes:
    if not isinstance(veri, str):
        raise ValueError("Base64 metni bekleniyor.")
    return base64.b64decode(veri, validate=True)


def ozet(veri: bytes) -> str:
    return hashlib.sha256(veri).hexdigest()


def _oku(yol: Path, sinir: int = SINIR) -> bytes:
    with yol.open("rb") as f:
        veri = f.read(sinir + 1)
    if not 0 < len(veri) <= sinir:
        raise ValueError("Dosya boyutu geçersiz.")
    return veri


def suresi_gecerli(son: int) -> None:
    if time.time() >= son:
        raise LisansHatasi("Model lisansının süresi doldu. Yeni model.lic dosyasını yükleyin.")


def cihaz_istegi(anahtar_kimligi: bytes, imzalayan_ozeti: bytes) -> dict:
    try:
        acik = cihaz_anahtari.acik_anahtar(uret=True)
    except (cihaz_anahtari.CihazHatasi, ValueError) as e:
        raise LisansHatasi(str(e)) from None
    return {"bicim": ISTEK_BICIMI, "cihaz": kodla(acik),
            "model": anahtar_kimligi.hex(), "imzalayan": imzalayan_ozeti.hex()}


def dogrula(ham: bytes, klasor: str, anahtar_kimligi: bytes, imzalayan_ozeti: bytes) -> dict:
    """İmza, model paketi, cihaz ve süre doğrulanır. Model anahtarı bu aşamada açılmaz."""
    try:
        if len(ham) > SINIR:
            raise ValueError("Dosya boyutu geçersiz.")
        acik = _oku(Path(klasor) / IMZALAYAN, 411)
        if hashlib.sha256(acik).digest() != imzalayan_ozeti:
            raise LisansHatasi("Model paketinin imzalayan anahtarı uyuşmuyor.")
        cihaz_anahtari.acik_bilesenler(acik)
        zarf = json.loads(ham)
        govde = coz64(zarf["veri"])
        imza = coz64(zarf["imza"])
        if not cihaz_anahtari.imza_dogrula(acik, IMZA_ALANI + govde, imza):
            raise LisansHatasi("Model lisansının imzası geçersiz.")
        lisans = json.loads(govde)
        if lisans["bicim"] != LISANS_BICIMI or lisans["model"] != anahtar_kimligi.hex():
            raise LisansHatasi("Model lisansı bu pakete ait değil.")
        bas, son = lisans["baslangic"], lisans["bitis"]
        if type(bas) is not int or type(son) is not int or not 0 < bas < son:
            raise ValueError("Lisans tarihleri geçersiz.")
        suresi_gecerli(son)
        if time.time() < bas:
            raise LisansHatasi("Model lisansı henüz başlamadı. Windows tarih ve saatini kontrol edin.")
        cihaz = cihaz_anahtari.acik_anahtar()
        if lisans["cihaz"] != ozet(cihaz):
            raise LisansHatasi("Model lisansı bu Windows kullanıcısına veya bilgisayara ait değil.")
        _, n = cihaz_anahtari.acik_bilesenler(cihaz)
        sarili = coz64(lisans["sarili"])
        if len(sarili) != n.bit_length() // 8:
            raise ValueError("Model lisansı anahtar boyutu geçersiz.")
        return {"bitis": son, "cihaz": lisans["cihaz"], "sarili": sarili}
    except cihaz_anahtari.CihazHatasi as e:
        raise LisansHatasi(str(e)) from None
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise LisansHatasi("Model lisansı veya paketin doğrulama dosyası okunamadı.") from None


def yerel_lisans(veri: str | None, klasor: str, kimlik: bytes, imzalayan: bytes) -> dict:
    if not veri:
        raise LisansHatasi("Model lisansı bulunamadı. Panelden model.lic dosyasını yükleyin.")
    try:
        ham = _oku(Path(veri) / LISANS)
    except FileNotFoundError:
        raise LisansHatasi("Model lisansı bulunamadı. Panelden model.lic dosyasını yükleyin.") from None
    except (OSError, ValueError):
        raise LisansHatasi("Model lisans dosyası okunamadı.") from None
    return dogrula(ham, klasor, kimlik, imzalayan)


def lisansi_kaydet(veri: str, ham: bytes, klasor: str, kimlik: bytes, imzalayan: bytes) -> None:
    dogrula(ham, klasor, kimlik, imzalayan)
    hedef = Path(veri) / LISANS
    hedef.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=hedef.parent, prefix=".model-lic-", delete=False) as f:
        gecici = Path(f.name)
    try:
        gecici.write_bytes(ham)
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)


def model_anahtari(lisans: dict) -> bytearray:
    suresi_gecerli(lisans["bitis"])
    try:
        return cihaz_anahtari.anahtari_ac(lisans["sarili"], lisans["cihaz"])
    except cihaz_anahtari.CihazHatasi as e:
        raise LisansHatasi(str(e)) from None

