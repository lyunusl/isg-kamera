"""Dağıtım için model paketi ve etkinleştirme kodları oluşturur.

  python kurulum/model_paketi.py anahtar --uret     ana anahtarı üretir (bir kez); durumunu gösterir
  python kurulum/model_paketi.py sifrele            motor/modeller/*.onnx -> kurulum/sifreli-modeller/*.onnx.aesgcm
  python kurulum/model_paketi.py kod --adet 3 --dosya C:/guvenli/kodlar.txt
                                                    etkinleştirme kodları üretir, yuvalarını anahtar paketine ekler
  python kurulum/model_paketi.py dogrula            paket içeriğini kaynak modellerle karşılaştırır

Ana anahtar (32 bayt) bu deponun dışında, Windows DPAPI ile bu Windows kullanıcısına bağlı olarak durur:
%LOCALAPPDATA%\\AugeLab\\ISG-Kamera\\ (ISG_MODEL_ANAHTARI ortam değişkeni başka bir dosya gösterebilir). Bu betik
anahtarı hiçbir zaman ekrana ya da bir dosyaya açık olarak yazmaz; yalnızca kimliğini (özetinin ilk 8 baytı) gösterir.
Anahtar kaybolursa eski paketler için yeni kod üretilemez; yedeğini siz alın.

Etkinleştirme kodları yalnızca üretildikleri anda görünür: --dosya verilirse o dosyaya, verilmezse ekrana yazılır.
Pakette (anahtar-paketi.json) kod yoktur; yalnızca kodla açılan yuvalar vardır. Paket dağıtılabilir, kodlar dağıtılmaz.

Dağıtım derlemesi:  ISG_MODEL_PAKETI=sifreli ortam değişkeniyle  pyinstaller kurulum/IsgKamera.spec
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

kok = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(kok / "motor"))

from isg_motor import kasa  # noqa: E402
from isg_motor.katalog import MODEL_DOSYALARI  # noqa: E402

DUZ = kok / "motor" / "modeller"
CIKTI = kok / "kurulum" / "sifreli-modeller"
ANAHTAR_KLASORU = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AugeLab" / "ISG-Kamera"
ANAHTAR_ADLARI = ("model-anahtari.dpapi", "model-pilot-v1.dpapi")  # ikincisi ilk denemede üretilen anahtar


def anahtar_dosyasi() -> Path:
    ortam = os.environ.get("ISG_MODEL_ANAHTARI")
    if ortam:
        return Path(ortam)
    for ad in ANAHTAR_ADLARI:
        if (ANAHTAR_KLASORU / ad).is_file():
            return ANAHTAR_KLASORU / ad
    return ANAHTAR_KLASORU / ANAHTAR_ADLARI[0]


def ana_anahtar() -> bytes:
    yol = anahtar_dosyasi()
    if not yol.is_file():
        raise SystemExit(f"Ana anahtar yok: {yol}\nÖnce: python kurulum/model_paketi.py anahtar --uret")
    korunan = yol.read_bytes()
    for ek in (kasa._DPAPI_EK, None):  # None: bu betikten önce, eksiz korunmuş anahtar
        try:
            anahtar = kasa._dpapi(korunan, koru=False, ek=ek)
        except kasa.KasaHatasi:
            continue
        if len(anahtar) == kasa.ANAHTAR_BOYU:
            return anahtar
    raise SystemExit(f"Ana anahtar okunamadı: {yol} (başka bir Windows kullanıcısına ait olabilir)")


def paket_oku() -> dict | None:
    try:
        return json.loads((CIKTI / kasa.PAKET_DOSYASI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def paket_yaz(paket: dict) -> None:
    CIKTI.mkdir(parents=True, exist_ok=True)
    gecici = CIKTI / (kasa.PAKET_DOSYASI + ".yeni")
    gecici.write_text(json.dumps(paket, indent=1), encoding="utf-8")
    os.replace(gecici, CIKTI / kasa.PAKET_DOSYASI)


def guncel_paket(anahtar: bytes) -> dict:
    """Bu anahtarın paketi: varsa yuvalarıyla birlikte, yoksa (ya da başka bir anahtara aitse) boş."""
    paket = paket_oku()
    if not paket or paket.get("anahtar_kimligi") != kasa.anahtar_kimligi(anahtar).hex():
        paket = kasa.paket_olustur(anahtar)
    return paket


def komut_anahtar(a) -> int:
    yol = anahtar_dosyasi()
    if a.uret:
        if yol.is_file():
            raise SystemExit(f"Ana anahtar zaten var: {yol} (üstüne yazılmaz)")
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_bytes(kasa._dpapi(kasa.anahtar_uret(), koru=True))
        print("Ana anahtar üretildi:", yol)
    anahtar = ana_anahtar()
    print(f"Ana anahtar: {yol} · kimlik {kasa.anahtar_kimligi(anahtar).hex()}")
    paket = paket_oku()
    if paket:
        ayni = paket.get("anahtar_kimligi") == kasa.anahtar_kimligi(anahtar).hex()
        print(f"Anahtar paketi: {len(paket.get('yuvalar') or [])} yuva" + ("" if ayni else " · BAŞKA BİR ANAHTARA AİT"))
    return 0


def komut_sifrele(a) -> int:
    anahtar = ana_anahtar()
    CIKTI.mkdir(parents=True, exist_ok=True)
    eksik = [d for d in MODEL_DOSYALARI if not (DUZ / d).is_file()]
    if eksik:
        raise SystemExit(f"Düz model dosyası yok: {', '.join(eksik)}")
    for d in MODEL_DOSYALARI:
        duz = (DUZ / d).read_bytes()
        sifreli = kasa.sifrele(duz, anahtar, d)
        if kasa.coz(sifreli, anahtar, d) != duz:
            raise SystemExit(f"{d}: model paketi doğrulanamadı")
        gecici = CIKTI / (d + kasa.UZANTI + ".yeni")
        gecici.write_bytes(sifreli)
        os.replace(gecici, CIKTI / (d + kasa.UZANTI))
        print(f"  {d}{kasa.UZANTI}  {len(sifreli):>11,} bayt  (düz: {len(duz):,})")
    for p in CIKTI.glob("*" + kasa.UZANTI):  # listede artık olmayan eski dosyalar pakete girmesin
        if p.name[:-len(kasa.UZANTI)] not in MODEL_DOSYALARI:
            p.unlink()
    paket = guncel_paket(anahtar)
    paket_yaz(paket)
    print(f"Model paketi hazır ({len(MODEL_DOSYALARI)} dosya): {CIKTI} · anahtar kimliği {kasa.anahtar_kimligi(anahtar).hex()} · pakette {len(paket['yuvalar'])} yuva")
    if not paket["yuvalar"]:
        print("Pakette henüz etkinleştirme kodu yok: python kurulum/model_paketi.py kod")
    return 0


def komut_kod(a) -> int:
    anahtar = ana_anahtar()
    paket = guncel_paket(anahtar)
    kodlar = [kasa.kod_uret() for _ in range(max(1, a.adet))]
    paket["yuvalar"] += [kasa.paket_yuvasi(anahtar, k) for k in kodlar]
    paket_yaz(paket)
    if a.dosya:
        hedef = Path(a.dosya).resolve()
        if kok in hedef.parents:
            raise SystemExit("Kod dosyasını deponun dışında bir yere yazın.")
        hedef.parent.mkdir(parents=True, exist_ok=True)
        with open(hedef, "a", encoding="utf-8") as f:
            f.write("".join(k + "\n" for k in kodlar))
        print(f"{len(kodlar)} etkinleştirme kodu dosyaya eklendi: {hedef}")
    else:
        print("Etkinleştirme kodları (yalnızca şimdi görünür; saklamazsanız yenisini üretmeniz gerekir):")
        for k in kodlar:
            print("  " + k)
    print(f"Pakette {len(paket['yuvalar'])} yuva: {CIKTI / kasa.PAKET_DOSYASI}")
    return 0


def komut_dogrula(a) -> int:
    anahtar = ana_anahtar()
    kalan = 0
    for d in MODEL_DOSYALARI:
        try:
            acik = kasa.coz((CIKTI / (d + kasa.UZANTI)).read_bytes(), anahtar, d)
            ayni = hashlib.sha256(acik).digest() == hashlib.sha256((DUZ / d).read_bytes()).digest()
            print(("  aynı    " if ayni else "  FARKLI  ") + d)
            kalan += not ayni
        except (OSError, kasa.KasaHatasi) as e:
            print(f"  AÇILMADI  {d}: {e}")
            kalan += 1
    paket = paket_oku() or {}
    print(f"Anahtar paketi: {len(paket.get('yuvalar') or [])} yuva · anahtara ait: {paket.get('anahtar_kimligi') == kasa.anahtar_kimligi(anahtar).hex()}")
    return 1 if kalan else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="İSG Kamera model paketi ve etkinleştirme kodları")
    alt = ap.add_subparsers(dest="komut", required=True)
    p = alt.add_parser("anahtar", help="ana anahtarın durumu; --uret ile üretir")
    p.add_argument("--uret", action="store_true")
    alt.add_parser("sifrele", help="dört modelden dağıtım paketi oluşturur")
    p = alt.add_parser("kod", help="etkinleştirme kodu üretir ve pakete ekler")
    p.add_argument("--adet", type=int, default=1)
    p.add_argument("--dosya", help="kodların ekleneceği dosya (deponun dışında); verilmezse ekrana yazılır")
    alt.add_parser("dogrula", help="paket içeriğini kaynak modellerle karşılaştırır")
    a = ap.parse_args()
    return {"anahtar": komut_anahtar, "sifrele": komut_sifrele, "kod": komut_kod, "dogrula": komut_dogrula}[a.komut](a)


if __name__ == "__main__":
    sys.exit(main())
