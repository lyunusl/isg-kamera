"""Windows CNG: bu Windows kullanıcısına ve bilgisayara ait model lisans anahtarı."""
from __future__ import annotations

import ctypes as c
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import os
import struct
import threading

V, U, W, B = c.c_void_p, c.c_ulong, c.c_wchar_p, c.c_char_p
SILENT = 0x40
OAEP = 0x04
PSS = 0x08
ANAHTAR_ADI = "IsgKamera.ModelLisans.RSA.v1"
SAGLAYICILAR = ("Microsoft Platform Crypto Provider", "Microsoft Software Key Storage Provider")
_YARATMA = threading.Lock()


class CihazHatasi(Exception):
    pass


class _OAEP(c.Structure):
    _fields_ = [("algoritma", W), ("etiket", V), ("etiket_boyu", U)]


class _PSS(c.Structure):
    _fields_ = [("algoritma", W), ("tuz_boyu", U)]


@lru_cache(maxsize=1)
def _kitapliklar():
    if os.name != "nt":
        raise CihazHatasi("Windows gerekli.")
    n, b = c.WinDLL("ncrypt"), c.WinDLL("bcrypt")
    p = c.POINTER(V)
    sayac = c.POINTER(U)
    tanimlar = (
        (n, "NCryptOpenStorageProvider", [p, W, U]),
        (n, "NCryptOpenKey", [V, p, W, U, U]),
        (n, "NCryptCreatePersistedKey", [V, p, W, W, U, U]),
        (n, "NCryptSetProperty", [V, W, V, U, U]),
        (n, "NCryptFinalizeKey", [V, U]),
        (n, "NCryptFreeObject", [V]),
        (n, "NCryptExportKey", [V, V, W, V, V, U, sayac, U]),
        (n, "NCryptDecrypt", [V, B, U, V, V, U, sayac, U]),
        (b, "BCryptOpenAlgorithmProvider", [p, W, W, U]),
        (b, "BCryptImportKeyPair", [V, V, W, p, B, U, U]),
        (b, "BCryptVerifySignature", [V, V, B, U, B, U, U]),
        (b, "BCryptDestroyKey", [V]),
        (b, "BCryptCloseAlgorithmProvider", [V, U]),
    )
    for kutuphane, ad, args in tanimlar:
        f = getattr(kutuphane, ad)
        f.argtypes, f.restype = args, c.c_long
    return n, b


def _kontrol(kod):
    if kod:
        raise CihazHatasi(f"Windows anahtar işlemi başarısız (0x{kod & 0xffffffff:08X}).")


def acik_bilesenler(blob: bytes) -> tuple[int, int]:
    """CNG RSAPUBLICBLOB içinden (e, n). Yalnızca beklenen RSA açık anahtarları."""
    if len(blob) < 24:
        raise ValueError("RSA anahtarı eksik.")
    sihir, bit, e_boy, n_boy, p, q = struct.unpack_from("<6I", blob)
    if (sihir != 0x31415352 or bit not in (2048, 3072) or p or q
            or e_boy != 3 or n_boy != bit // 8 or len(blob) != 24 + e_boy + n_boy):
        raise ValueError("RSA anahtarı geçersiz.")
    e = int.from_bytes(blob[24:27], "big")
    n = int.from_bytes(blob[27:], "big")
    if e != 65537 or n.bit_length() != bit or not n % 2:
        raise ValueError("RSA anahtarı geçersiz.")
    return e, n


def acik_blob(e: int, n: int) -> bytes:
    bit = n.bit_length()
    blob = struct.pack("<6I", 0x31415352, bit, 3, bit // 8, 0, 0) + e.to_bytes(3, "big") + n.to_bytes(bit // 8, "big")
    acik_bilesenler(blob)
    return blob


def _mevcut(n, saglayici, anahtar):
    for ad in SAGLAYICILAR:
        if n.NCryptOpenStorageProvider(c.byref(saglayici), ad, 0):
            continue
        if not n.NCryptOpenKey(saglayici, c.byref(anahtar), ANAHTAR_ADI, 0, SILENT):
            return True
        n.NCryptFreeObject(saglayici)
        saglayici.value = None
    return False


def _uret(n, saglayici, anahtar):
    for ad in SAGLAYICILAR:
        if n.NCryptOpenStorageProvider(c.byref(saglayici), ad, 0):
            continue
        kod = n.NCryptCreatePersistedKey(saglayici, c.byref(anahtar), "RSA", ANAHTAR_ADI, 0, 0)
        if kod & 0xffffffff == 0x8009000F:  # Başka bir süreç aynı adı oluşturdu.
            kod = n.NCryptOpenKey(saglayici, c.byref(anahtar), ANAHTAR_ADI, 0, SILENT)
            if not kod:
                return
        elif not kod:
            try:
                for ozellik, deger in (("Length", 2048), ("Key Usage", 1), ("Export Policy", 0)):
                    sayi = U(deger)
                    _kontrol(n.NCryptSetProperty(anahtar, ozellik, c.byref(sayi), c.sizeof(sayi), 0))
                _kontrol(n.NCryptFinalizeKey(anahtar, SILENT))
                return
            except CihazHatasi:
                n.NCryptFreeObject(anahtar)
                anahtar.value = None
        n.NCryptFreeObject(saglayici)
        saglayici.value = None
    raise CihazHatasi("Bu bilgisayarda lisans anahtarı oluşturulamadı.")


@contextmanager
def _cihaz(uret=False):
    n, _ = _kitapliklar()
    saglayici, anahtar = V(), V()
    try:
        with _YARATMA:
            if not _mevcut(n, saglayici, anahtar):
                if not uret:
                    raise CihazHatasi("Bu Windows kullanıcısının cihaz anahtarı bulunamadı.")
                _uret(n, saglayici, anahtar)
        yield n, anahtar
    finally:
        if anahtar.value:
            n.NCryptFreeObject(anahtar)
        if saglayici.value:
            n.NCryptFreeObject(saglayici)


def _acik(n, anahtar):
    boy = U()
    _kontrol(n.NCryptExportKey(anahtar, None, "RSAPUBLICBLOB", None, None, 0, c.byref(boy), 0))
    if not 24 <= boy.value <= 411:
        raise CihazHatasi("Cihaz anahtarı beklenen boyutta değil.")
    cikti = c.create_string_buffer(boy.value)
    _kontrol(n.NCryptExportKey(anahtar, None, "RSAPUBLICBLOB", None, cikti, len(cikti), c.byref(boy), 0))
    blob = cikti.raw[:boy.value]
    acik_bilesenler(blob)
    return blob


def acik_anahtar(*, uret=False) -> bytes:
    with _cihaz(uret) as (n, anahtar):
        return _acik(n, anahtar)


def anahtari_ac(sarili: bytes, cihaz_ozeti: str) -> bytearray:
    with _cihaz() as (n, anahtar):
        if hashlib.sha256(_acik(n, anahtar)).hexdigest() != cihaz_ozeti:
            raise CihazHatasi("Lisans bu bilgisayara ait değil.")
        padding = _OAEP("SHA256", None, 0)
        boy = U()
        cikti = (c.c_ubyte * len(sarili))()
        try:
            _kontrol(n.NCryptDecrypt(anahtar, sarili, len(sarili), c.byref(padding),
                                     cikti, len(cikti), c.byref(boy), OAEP | SILENT))
            if boy.value != 32:
                raise CihazHatasi("Model anahtarı geçersiz.")
            return bytearray(memoryview(cikti).cast("B")[:boy.value])
        finally:
            c.memset(cikti, 0, c.sizeof(cikti))


def imza_dogrula(acik: bytes, veri: bytes, imza: bytes) -> bool:
    _, b = _kitapliklar()
    saglayici, anahtar = V(), V()
    _kontrol(b.BCryptOpenAlgorithmProvider(c.byref(saglayici), "RSA", None, 0))
    try:
        _kontrol(b.BCryptImportKeyPair(saglayici, None, "RSAPUBLICBLOB", c.byref(anahtar), acik, len(acik), 0))
        padding = _PSS("SHA256", 32)
        ozet = hashlib.sha256(veri).digest()
        return b.BCryptVerifySignature(anahtar, c.byref(padding), ozet, len(ozet), imza, len(imza), PSS) == 0
    finally:
        if anahtar.value:
            b.BCryptDestroyKey(anahtar)
        b.BCryptCloseAlgorithmProvider(saglayici, 0)

