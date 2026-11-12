"""İSG Kamera paket üreticisi. Yalnızca lisans veren ekip çalıştırır."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "motor"))

from isg_motor import cihaz_anahtari, kasa, model_lisans as ml
from isg_motor.katalog import MODEL_DOSYALARI

PAKET = KOK / "kurulum" / "lisansli-modeller"
KORUMA = b"IsgKamera offline license issuer v1"


def _anahtar_yolu(dosya: str) -> Path:
    yol = Path(dosya).resolve()
    if yol.is_relative_to(KOK) or yol.suffix.lower() != ".dpapi":
        raise ValueError("Lisans üretici anahtarını deponun dışındaki bir .dpapi dosyasında tutun.")
    return yol


def anahtar_uret(a):
    yol = _anahtar_yolu(a.anahtar)
    if yol.exists():
        raise ValueError("Lisans üretici anahtarı zaten var.")
    imza = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pkcs8 = imza.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    korunan = kasa._dpapi(kasa.anahtar_uret() + pkcs8, True, KORUMA)
    yol.parent.mkdir(parents=True, exist_ok=True)
    with yol.open("xb") as f:
        f.write(korunan)
    print("Lisans üretici anahtarı Windows korumasıyla kaydedildi.")


def _anahtarlari_oku(dosya):
    korunan = ml._oku(_anahtar_yolu(dosya), 32_768)
    ham = kasa._dpapi(korunan, False, KORUMA)
    imza = serialization.load_der_private_key(ham[32:], password=None)
    if not isinstance(imza, rsa.RSAPrivateKey) or imza.key_size != 3072:
        raise ValueError("Lisans üretici anahtarı geçersiz.")
    sayilar = imza.public_key().public_numbers()
    return ham[:32], imza, cihaz_anahtari.acik_blob(sayilar.e, sayilar.n)


def paket_uret(a):
    anahtar, _, acik = _anahtarlari_oku(a.anahtar)
    hedef = Path(a.cikti).resolve()
    if hedef.exists():
        raise ValueError("Paket klasörü zaten var. Yeni bir çıktı klasörü seçin.")
    kaynak = Path(a.kaynak)
    modeller = [kaynak / ad for ad in MODEL_DOSYALARI if (kaynak / ad).is_file()]
    if not {"isg-nesne-1280.onnx", "isg-iskelet.onnx"}.issubset({m.name for m in modeller}):
        raise ValueError("Ana nesne ve iskelet modelleri gerekli.")
    hedef.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="isg-paket-", dir=hedef.parent) as gecici:
        dizin = Path(gecici) / "paket"
        dizin.mkdir()
        (dizin / ml.IMZALAYAN).write_bytes(acik)
        for model in modeller:
            icerik = kasa.sifrele(model.read_bytes(), anahtar, model.name, imzalayan=hashlib.sha256(acik).digest())
            (dizin / (model.name + kasa.UZANTI)).write_bytes(icerik)
        os.rename(dizin, hedef)
    print(f"Model paketi hazır ({len(modeller)} dosya): {hedef}")


def lisans_uret(a):
    anahtar, imza, acik = _anahtarlari_oku(a.anahtar)
    kimlik = kasa.anahtar_kimligi(anahtar).hex()
    istek = json.loads(ml._oku(Path(a.istek)))
    if (istek["bicim"] != ml.ISTEK_BICIMI or istek["model"] != kimlik
            or istek["imzalayan"] != ml.ozet(acik)):
        raise ValueError("Cihaz isteği bu modele veya lisans üreticisine ait değil.")
    cihaz = ml.coz64(istek["cihaz"])
    e, n = cihaz_anahtari.acik_bilesenler(cihaz)
    bitis = datetime.fromisoformat(a.bitis)
    if bitis.tzinfo is None or bitis.timestamp() <= time.time():
        raise ValueError("İleri bir bitiş zamanı ve saat dilimi girin (ör. 2027-01-01T00:00:00+03:00).")
    sarili = rsa.RSAPublicNumbers(e, n).public_key().encrypt(
        anahtar, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    govde = ml.json_baytlari({"bicim": ml.LISANS_BICIMI, "model": kimlik, "cihaz": ml.ozet(cihaz),
                            "baslangic": int(time.time()) - 120, "bitis": int(bitis.timestamp()), "sarili": ml.kodla(sarili)})
    imza_verisi = imza.sign(ml.IMZA_ALANI + govde, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    hedef = Path(a.cikti)
    if hedef.suffix.lower() != ".lic":
        raise ValueError("Lisans çıktısı .lic uzantılı olmalı.")
    with hedef.open("xb") as f:
        f.write(ml.json_baytlari({"veri": ml.kodla(govde), "imza": ml.kodla(imza_verisi)}))
    print(f"Cihaza özel lisans hazır: {hedef}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--anahtar", required=True, help="Depo dışındaki lisans üretici .dpapi dosyası")
    komutlar = ap.add_subparsers(required=True)
    komutlar.add_parser("anahtar-uret").set_defaults(islem=anahtar_uret)
    p = komutlar.add_parser("paketle")
    p.set_defaults(islem=paket_uret)
    p.add_argument("--kaynak", default=KOK / "motor" / "modeller")
    p.add_argument("--cikti", default=PAKET)
    p = komutlar.add_parser("lisans-ver", help="Mevcut AugeLab lisansını doğruladıktan sonra çalıştırın")
    p.set_defaults(islem=lisans_uret)
    p.add_argument("--istek", required=True)
    p.add_argument("--bitis", required=True, help="Doğrulanmış AugeLab lisansını aşmayan ISO 8601 bitiş zamanı")
    p.add_argument("--cikti", default="model.lic")
    a = ap.parse_args()
    try:
        a.islem(a)
    except (ValueError, TypeError, KeyError, OSError, kasa.KasaHatasi) as e:
        ap.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()

