"""Model paketi biçimi ve yerel model anahtarı.

Model klasöründe bir model iki biçimde durabilir:

  isg-nesne-1280.onnx          düz dosya (kaynak koddan çalışırken, kendi modelinizi koyduğunuzda)
  isg-nesne-1280.onnx.aesgcm   dağıtım paketindeki dosya

Düz dosyalar geliştirme modunda kullanılabilir. Lisanslı paketler modeli bellekte çözer ve ONNX Runtime'a bayt olarak verir.
Sürüm 3, AES-GCM başlığına lisansı imzalayan açık anahtarın SHA-256 özetini ekler ve model.lic gerektirir.

Paket biçimi:  "AUGELAB-AESGCM" (14) | sürüm 0x02 (1) | anahtar kimliği (8) | nonce (12) | içerik | etiket (16)
Doğrulanan ek veri: baştaki 23 bayt + modelin dosya adı (bir model başka bir modelin adıyla kullanılamaz).
Anahtar kimliği anahtarın özetinin ilk 8 baytıdır; "yanlış anahtar" ile "bozuk dosya" bununla ayrılır.

Model anahtarı (32 bayt) veri klasöründe `model-anahtari.dpapi` dosyasında, Windows DPAPI ile bu Windows kullanıcısına
bağlı olarak durur. Panel de AugeLab Studio'daki motor da aynı dosyayı okur; ikisi aynı Windows kullanıcısıyla çalışmalıdır.

Anahtar bu bilgisayara etkinleştirme koduyla gelir: `anahtar-paketi.json` dosyasındaki her yuva, model anahtarının bir
etkinleştirme kodundan türetilen anahtarla (PBKDF2-HMAC-SHA256) sarılmış hâlidir. Kod yalnızca yuvayı açmak için
kullanılır; kaydedilmez.

Koruma sınırı: anahtar, modeli çalıştıran bilgisayarda bulunmak zorundadır. Etkinleştirilmiş bir kurulumun sahibi
anahtarı ve çözülmüş modeli (DPAPI ile ya da çalışan sürecin belleğinden) elde edebilir. Bu düzen dosyaların
kopyalanmasını ve değiştirilmesini zorlaştırır; kopya korumasının ya da lisansın yerini tutmaz.

Yalnızca standart kitaplık kullanılır: AES-GCM Windows CNG (bcrypt.dll), anahtar koruması DPAPI (crypt32.dll).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets

from . import community_model, model_lisans

SIHIR = b"AUGELAB-AESGCM"
SURUM = 2
SURUM_LISANS = 3
UZANTI = ".aesgcm"
ANAHTAR_DOSYASI = "model-anahtari.dpapi"
PAKET_DOSYASI = "anahtar-paketi.json"
ANAHTAR_BOYU = 32
KDF_TURU = 200_000
KOD_ALFABESI = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 simge; I, L, O, U yok (karıştırılmasın)
KOD_BOYU = 25                                        # 125 bit

_BASLIK = len(SIHIR) + 1 + 8
_BASLIK_LISANS = _BASLIK + 32
_NONCE = 12
_ETIKET = 16
_DPAPI_EK = b"IsgKamera model anahtari v1"            # gizli değil: korunan verinin başka bir amaçla açılmasını önler
_PAKET_EK = b"isg-kamera-anahtar-paketi\0"
_ETIKET_UYUSMADI = 0xC000A002

NEDENLER = {
    "anahtar_yok": "Modeller bu bilgisayarda henüz etkinleştirilmedi. Paneli açıp etkinleştirme kodunu girin.",
    "anahtar_okunamadi": "Bu bilgisayardaki model anahtarı okunamadı. Anahtar başka bir Windows kullanıcısına ait olabilir; "
                         "paneli ve AugeLab Studio'yu aynı kullanıcıyla açın ya da etkinleştirme kodunu yeniden girin.",
    "anahtar_uyumsuz": "Bu bilgisayardaki model anahtarı bu model dosyalarına ait değil. Etkinleştirme kodunu yeniden girin.",
    "bozuk": "Model dosyası bozulmuş ya da değiştirilmiş. Programı yeniden indirin.",
    "kod_yanlis": "Etkinleştirme kodu geçersiz.",
    "paket_yok": "Anahtar paketi bulunamadı (anahtar-paketi.json).",
    "desteklenmiyor": "Bu model paketi Windows gerektirir.",
}


class KasaHatasi(Exception):
    """`neden`: NEDENLER'deki kodlardan biri."""

    def __init__(self, neden: str, ayrinti: str = ""):
        self.neden = neden
        super().__init__(NEDENLER.get(neden, neden) + (f" ({ayrinti})" if ayrinti else ""))


# ---------------------------------------------------------------- AES-GCM (Windows CNG)

_CNG = None


def _cng():
    global _CNG
    if _CNG is not None:
        return _CNG
    import ctypes

    V, U = ctypes.c_void_p, ctypes.c_ulong

    class Bilgi(ctypes.Structure):  # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO
        _fields_ = [("cbSize", U), ("dwInfoVersion", U), ("pbNonce", V), ("cbNonce", U), ("pbAuthData", V), ("cbAuthData", U),
                    ("pbTag", V), ("cbTag", U), ("pbMacContext", V), ("cbMacContext", U), ("cbAAD", U),
                    ("cbData", ctypes.c_ulonglong), ("dwFlags", U)]

    b = ctypes.WinDLL("bcrypt")
    b.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(V), ctypes.c_wchar_p, ctypes.c_wchar_p, U]
    b.BCryptSetProperty.argtypes = [V, ctypes.c_wchar_p, V, U, U]
    b.BCryptGenerateSymmetricKey.argtypes = [V, ctypes.POINTER(V), V, U, V, U, U]
    b.BCryptDestroyKey.argtypes = [V]
    for f in (b.BCryptEncrypt, b.BCryptDecrypt):
        f.argtypes = [V, ctypes.c_char_p, U, ctypes.POINTER(Bilgi), V, U, V, U, ctypes.POINTER(U), U]
    for f in (b.BCryptOpenAlgorithmProvider, b.BCryptSetProperty, b.BCryptGenerateSymmetricKey, b.BCryptDestroyKey, b.BCryptEncrypt, b.BCryptDecrypt):
        f.restype = ctypes.c_long
    saglayici = V()
    if b.BCryptOpenAlgorithmProvider(ctypes.byref(saglayici), "AES", None, 0):
        raise KasaHatasi("desteklenmiyor", "AES sağlayıcısı açılamadı")
    kip = ctypes.create_unicode_buffer("ChainingModeGCM")
    if b.BCryptSetProperty(saglayici, "ChainingMode", ctypes.cast(kip, V), ctypes.sizeof(kip), 0):
        raise KasaHatasi("desteklenmiyor", "Model paketi açılamadı")
    _CNG = (ctypes, b, Bilgi, saglayici)
    return _CNG


def _aes_gcm(anahtar: bytes | bytearray, nonce: bytes, veri: bytes, ek: bytes, etiket: bytes | None = None):
    """`etiket` verilmezse şifreler ve (şifreli veri, etiket) döner; verilirse çözer ve düz veriyi döner.
    Etiket doğrulanamazsa (yanlış anahtar, değişmiş veri) KasaHatasi("bozuk")."""
    if os.name != "nt":
        return _aes_gcm_yedek(anahtar, nonce, veri, ek, etiket)
    ctypes, b, Bilgi, saglayici = _cng()
    V = ctypes.c_void_p
    tutamak = V()
    anahtar_t = ((ctypes.c_ubyte * len(anahtar)).from_buffer(anahtar) if isinstance(anahtar, bytearray)
                 else ctypes.c_char_p(anahtar))
    if b.BCryptGenerateSymmetricKey(saglayici, ctypes.byref(tutamak), None, 0, anahtar_t, len(anahtar), 0):
        raise KasaHatasi("desteklenmiyor", "AES anahtarı kurulamadı")
    cikti = None
    try:
        nonce_t = ctypes.create_string_buffer(nonce, len(nonce))
        etiket_t = ctypes.create_string_buffer(etiket, _ETIKET) if etiket is not None else ctypes.create_string_buffer(_ETIKET)
        bilgi = Bilgi()
        bilgi.cbSize, bilgi.dwInfoVersion = ctypes.sizeof(Bilgi), 1
        bilgi.pbNonce, bilgi.cbNonce = ctypes.addressof(nonce_t), len(nonce)
        if ek:
            ek_t = ctypes.create_string_buffer(ek, len(ek))
            bilgi.pbAuthData, bilgi.cbAuthData = ctypes.addressof(ek_t), len(ek)
        bilgi.pbTag, bilgi.cbTag = ctypes.addressof(etiket_t), _ETIKET
        cikti = ctypes.create_string_buffer(max(1, len(veri)))
        yazilan = ctypes.c_ulong()
        islem = b.BCryptEncrypt if etiket is None else b.BCryptDecrypt
        sonuc = islem(tutamak, veri, len(veri), ctypes.byref(bilgi), None, 0, ctypes.cast(cikti, V), len(veri), ctypes.byref(yazilan), 0) & 0xFFFFFFFF
        if sonuc == _ETIKET_UYUSMADI:
            raise KasaHatasi("bozuk")
        if sonuc or yazilan.value != len(veri):
            raise KasaHatasi("desteklenmiyor", f"Model paketi işlenemedi (0x{sonuc:08X})")
        govde = ctypes.string_at(cikti, len(veri))
        return (govde, etiket_t.raw) if etiket is None else govde
    finally:
        if cikti is not None:
            ctypes.memset(cikti, 0, ctypes.sizeof(cikti))
        b.BCryptDestroyKey(tutamak)


def _aes_gcm_yedek(anahtar, nonce, veri, ek, etiket):
    """Windows dışında (geliştirme ve sınama): `cryptography` paketi kuruluysa onunla."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise KasaHatasi("desteklenmiyor") from None
    if etiket is None:
        s = AESGCM(anahtar).encrypt(nonce, veri, ek or None)
        return s[:-_ETIKET], s[-_ETIKET:]
    try:
        return AESGCM(anahtar).decrypt(nonce, veri + etiket, ek or None)
    except InvalidTag:
        raise KasaHatasi("bozuk") from None


# ---------------------------------------------------------------- DPAPI (bu Windows kullanıcısına bağlı koruma)


def _dpapi(veri: bytes, koru: bool, ek: bytes | None = _DPAPI_EK) -> bytes:
    """`ek`: korumaya katılan ek veri; None ise eksiz (bu programın dışında korunmuş bir anahtarı okumak için)."""
    if os.name != "nt":
        raise KasaHatasi("desteklenmiyor")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    islem = crypt32.CryptProtectData if koru else crypt32.CryptUnprotectData
    islem.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    islem.restype = wintypes.BOOL
    girdi_t = ctypes.create_string_buffer(veri, len(veri))
    girdi, cikti = Blob(len(veri), ctypes.addressof(girdi_t)), Blob()
    ek_b = None
    if ek:
        ek_t = ctypes.create_string_buffer(ek, len(ek))
        ek_b = ctypes.byref(Blob(len(ek), ctypes.addressof(ek_t)))
    if not islem(ctypes.byref(girdi), None, ek_b, None, None, 0x1, ctypes.byref(cikti)):  # 0x1: pencere açma
        raise KasaHatasi("anahtar_okunamadi")
    try:
        return ctypes.string_at(cikti.pbData, cikti.cbData)
    finally:
        kernel32.LocalFree(cikti.pbData)


# ---------------------------------------------------------------- model dosyaları


def anahtar_uret() -> bytes:
    return secrets.token_bytes(ANAHTAR_BOYU)


def anahtar_kimligi(anahtar: bytes) -> bytes:
    ozet = hashlib.sha256(b"isg-kamera-model-anahtari\0")
    ozet.update(anahtar)
    return ozet.digest()[:8]


def _anahtar_denetle(anahtar) -> None:
    if not isinstance(anahtar, (bytes, bytearray)) or len(anahtar) != ANAHTAR_BOYU:
        raise KasaHatasi("anahtar_okunamadi", "anahtar 32 bayt olmalı")


def sifrele(duz: bytes, anahtar: bytes, ad: str, *, imzalayan: bytes | None = None) -> bytes:
    """`ad`: modelin düz dosya adı (isg-nesne-1280.onnx); çözerken aynı ad verilmelidir."""
    _anahtar_denetle(anahtar)
    surum = SURUM_LISANS if imzalayan is not None else SURUM
    baslik = SIHIR + bytes([surum]) + anahtar_kimligi(anahtar)
    if imzalayan is not None:
        if len(imzalayan) != 32:
            raise ValueError("İmzalayan özeti 32 bayt olmalı.")
        baslik += imzalayan
    nonce = secrets.token_bytes(_NONCE)
    govde, etiket = _aes_gcm(bytes(anahtar), nonce, duz, baslik + ad.encode("utf-8"))
    return baslik + nonce + govde + etiket


def _baslik_bilgisi(bas: bytes) -> tuple[bytes, bytes | None]:
    if len(bas) < _BASLIK or not bas.startswith(SIHIR):
        raise KasaHatasi("bozuk")
    kimlik = bas[len(SIHIR) + 1:_BASLIK]
    if bas[len(SIHIR)] == SURUM:
        return kimlik, None
    if bas[len(SIHIR)] == SURUM_LISANS and len(bas) >= _BASLIK_LISANS:
        return kimlik, bas[_BASLIK:_BASLIK_LISANS]
    raise KasaHatasi("bozuk")


def dosya_kimligi(bas: bytes) -> bytes:
    return _baslik_bilgisi(bas)[0]


def coz(veri: bytes, anahtar: bytes | bytearray, ad: str) -> bytes:
    _anahtar_denetle(anahtar)
    kimlik, imzalayan = _baslik_bilgisi(veri[:_BASLIK_LISANS])
    boy = _BASLIK_LISANS if imzalayan is not None else _BASLIK
    if len(veri) < boy + _NONCE + _ETIKET:
        raise KasaHatasi("bozuk")
    if kimlik != anahtar_kimligi(anahtar):
        raise KasaHatasi("anahtar_uyumsuz")
    nonce = veri[boy:boy + _NONCE]
    return _aes_gcm(anahtar, nonce, veri[boy + _NONCE:-_ETIKET], veri[:boy] + ad.encode("utf-8"), veri[-_ETIKET:])


def model_var(yol: str) -> bool:
    """Model dosyasının ya da paket girdisinin yerinde olup olmadığını döndürür."""
    if community_model.model_mi(yol):
        return os.path.isfile(yol + community_model.UZANTI)
    return os.path.isfile(yol + UZANTI) or (not _lisansli_klasor(yol) and os.path.isfile(yol))


def _lisansli_klasor(yol: str) -> bool:
    return os.path.isfile(os.path.join(os.path.dirname(yol), model_lisans.IMZALAYAN))


def sifreli_mi(yol: str) -> bool:
    return (_lisansli_klasor(yol) or not os.path.isfile(yol)) and os.path.isfile(yol + UZANTI)


def model_kaynagi(yol: str, veri: str | None = None):
    """ONNX Runtime'a verilecek kaynak dosya yolu ya da paketten alınan model baytlarıdır.
    `veri`: model anahtarının durduğu veri klasörü."""
    return model_ve_sure(yol, veri)[0]


def model_ve_sure(yol: str, veri: str | None = None) -> tuple[str | bytes, int | None]:
    if community_model.model_mi(yol):
        return community_model.ac(yol)[0], None
    if not sifreli_mi(yol):
        if _lisansli_klasor(yol):
            raise model_lisans.LisansHatasi("Lisanslı pakette model dosyası eksik.")
        return yol, None
    with open(yol + UZANTI, "rb") as f:
        kimlik, imzalayan = _baslik_bilgisi(f.read(_BASLIK_LISANS))
        if imzalayan is not None:
            lisans = model_lisans.yerel_lisans(veri, os.path.dirname(yol), kimlik, imzalayan)
            f.seek(0)
            icerik = f.read()
            anahtar = model_lisans.model_anahtari(lisans)
            try:
                return coz(icerik, anahtar, os.path.basename(yol)), lisans["bitis"]
            finally:
                anahtar[:] = bytes(len(anahtar))
        if _lisansli_klasor(yol):
            raise model_lisans.LisansHatasi("Lisanslı pakette eski biçimde model bulunuyor.")
        f.seek(0)
        return coz(f.read(), anahtar_oku(veri), os.path.basename(yol)), None


def lisans_paketi(klasor: str, dosyalar) -> tuple[bytes, bytes] | None:
    if community_model.isaretli(klasor):
        return None  # Bu paket model.lic kabul etmez.
    basliklar = set()
    for ad in dosyalar:
        yol = os.path.join(klasor, ad)
        if sifreli_mi(yol):
            with open(yol + UZANTI, "rb") as f:
                basliklar.add(_baslik_bilgisi(f.read(_BASLIK_LISANS)))
    lisansli = [b for b in basliklar if b[1] is not None]
    if not lisansli and not os.path.isfile(os.path.join(klasor, model_lisans.IMZALAYAN)):
        return None
    if len(lisansli) != 1 or len(basliklar) != 1:
        raise model_lisans.LisansHatasi("Bu klasörde farklı paketlere ait modeller var.")
    return lisansli[0]


# ---------------------------------------------------------------- bu bilgisayardaki anahtar

_ONBELLEK: dict = {}


def anahtar_yolu(veri: str) -> str:
    return os.path.join(veri, ANAHTAR_DOSYASI)


def anahtar_oku(veri: str | None) -> bytes:
    if not veri:
        raise KasaHatasi("anahtar_yok")
    yol = anahtar_yolu(veri)
    try:
        d = os.stat(yol)
    except OSError:
        raise KasaHatasi("anahtar_yok") from None
    imza = (yol, d.st_mtime_ns, d.st_size)
    if _ONBELLEK.get("imza") == imza:
        return _ONBELLEK["anahtar"]
    try:
        with open(yol, "rb") as f:
            korunan = f.read()
    except OSError:
        raise KasaHatasi("anahtar_okunamadi") from None
    anahtar = _dpapi(korunan, koru=False) if korunan else b""
    if len(anahtar) != ANAHTAR_BOYU:
        raise KasaHatasi("anahtar_okunamadi")
    _ONBELLEK.update(imza=imza, anahtar=anahtar)
    return anahtar


def anahtar_yaz(veri: str, anahtar: bytes) -> str:
    _anahtar_denetle(anahtar)
    os.makedirs(veri, exist_ok=True)
    yol = anahtar_yolu(veri)
    gecici = yol + ".yeni"
    with open(gecici, "wb") as f:
        f.write(_dpapi(bytes(anahtar), koru=True))
    os.replace(gecici, yol)
    _ONBELLEK.clear()
    return yol


def anahtar_sil(veri: str) -> None:
    _ONBELLEK.clear()
    try:
        os.remove(anahtar_yolu(veri))
    except FileNotFoundError:
        pass


def durum(klasor: str, dosyalar, veri: str | None) -> dict:
    """Panel için model paketinin yerel etkinleştirme durumunu döndürür.
    durum: "duz" | "etkin" | "anahtar_yok" | "anahtar_okunamadi" | "anahtar_uyumsuz" | "bozuk" """
    dosyalar = tuple(dosyalar)
    if community_model.isaretli(klasor) or any(community_model.model_mi(os.path.join(klasor, d)) for d in dosyalar):
        return community_model.durum(klasor, dosyalar)
    sifreli = [d for d in dosyalar if sifreli_mi(os.path.join(klasor, d))]
    paket = any(os.path.isfile(os.path.join(k, PAKET_DOSYASI)) for k in (veri, klasor) if k)
    if not sifreli and not os.path.isfile(os.path.join(klasor, model_lisans.IMZALAYAN)):
        return {"sifreli": False, "durum": "duz", "paket": paket}
    try:
        bilgi = lisans_paketi(klasor, sifreli)
        if bilgi is not None:
            lisans = model_lisans.yerel_lisans(veri, klasor, *bilgi)
            return {"sifreli": True, "durum": "etkin", "lisansli": True, "bitis": lisans["bitis"]}
    except model_lisans.LisansHatasi as e:
        return {"sifreli": True, "durum": "lisans_gerekli", "lisansli": True, "mesaj": str(e)}
    except (KasaHatasi, OSError):
        return {"sifreli": True, "durum": "bozuk", "mesaj": NEDENLER["bozuk"],
                "lisansli": os.path.isfile(os.path.join(klasor, model_lisans.IMZALAYAN))}
    try:
        kimlik = anahtar_kimligi(anahtar_oku(veri))
        for d in sifreli:
            with open(os.path.join(klasor, d) + UZANTI, "rb") as f:
                if dosya_kimligi(f.read(_BASLIK)) != kimlik:
                    raise KasaHatasi("anahtar_uyumsuz")
    except KasaHatasi as e:
        return {"sifreli": True, "durum": e.neden, "mesaj": str(e), "paket": paket}
    except OSError:
        return {"sifreli": True, "durum": "bozuk", "mesaj": NEDENLER["bozuk"], "paket": paket}
    return {"sifreli": True, "durum": "etkin", "paket": paket}


# ---------------------------------------------------------------- etkinleştirme kodu ve anahtar paketi


def kod_uret() -> str:
    ham = "".join(secrets.choice(KOD_ALFABESI) for _ in range(KOD_BOYU))
    return "-".join(ham[i:i + 5] for i in range(0, KOD_BOYU, 5))


def kod_duzelt(kod) -> str:
    """Yazım farklarını giderir (küçük harf, boşluk, tire; I/L -> 1, O -> 0). Geçersizse KasaHatasi("kod_yanlis")."""
    ham = str(kod or "").upper().translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))
    ham = "".join(c for c in ham if c in KOD_ALFABESI)
    if len(ham) != KOD_BOYU:
        raise KasaHatasi("kod_yanlis")
    return ham


def _yuva_kimligi(ham: str) -> str:
    return hashlib.sha256(b"isg-kamera-yuva\0" + ham.encode("ascii")).hexdigest()[:8]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def paket_yuvasi(anahtar: bytes, kod: str) -> dict:
    """Model anahtarını bu etkinleştirme koduyla sarar. Kod yuvada yer almaz."""
    _anahtar_denetle(anahtar)
    ham = kod_duzelt(kod)
    tuz, nonce = secrets.token_bytes(16), secrets.token_bytes(_NONCE)
    sarma = hashlib.pbkdf2_hmac("sha256", ham.encode("ascii"), tuz, KDF_TURU, ANAHTAR_BOYU)
    govde, etiket = _aes_gcm(sarma, nonce, bytes(anahtar), _PAKET_EK + anahtar_kimligi(anahtar))
    return {"kimlik": _yuva_kimligi(ham), "tuz": _b64(tuz), "tur": KDF_TURU, "nonce": _b64(nonce), "sarili": _b64(govde + etiket)}


def paket_olustur(anahtar: bytes, kodlar=()) -> dict:
    return {"bicim": 1, "anahtar_kimligi": anahtar_kimligi(anahtar).hex(), "yuvalar": [paket_yuvasi(anahtar, k) for k in kodlar]}


def paket_ac(paket: dict, kod: str) -> bytes:
    """Etkinleştirme koduyla paketten model anahtarını çıkarır. Kod bu pakete ait değilse KasaHatasi("kod_yanlis")."""
    ham = kod_duzelt(kod)
    try:
        kimlik = bytes.fromhex(paket["anahtar_kimligi"])
        yuvalar = [y for y in paket["yuvalar"] if y.get("kimlik") == _yuva_kimligi(ham)]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KasaHatasi("paket_yok", "dosya okunamadı") from None
    for y in yuvalar:
        try:
            tur = int(y["tur"])
            if not 1 <= tur <= 10_000_000:
                continue
            sarma = hashlib.pbkdf2_hmac("sha256", ham.encode("ascii"), base64.b64decode(y["tuz"]), tur, ANAHTAR_BOYU)
            sarili = base64.b64decode(y["sarili"])
            anahtar = _aes_gcm(sarma, base64.b64decode(y["nonce"]), sarili[:-_ETIKET], _PAKET_EK + kimlik, sarili[-_ETIKET:])
        except (KasaHatasi, KeyError, TypeError, ValueError):
            continue
        if len(anahtar) == ANAHTAR_BOYU and anahtar_kimligi(anahtar) == kimlik:
            return anahtar
    raise KasaHatasi("kod_yanlis")


def etkinlestir(klasor: str, veri: str, kod: str, dosyalar=()) -> None:
    """Etkinleştirme koduyla model anahtarını bu bilgisayara kaydeder. Paket önce veri klasöründe (sonradan verilen
    güncel paket), sonra model klasöründe (programla gelen) aranır."""
    if community_model.isaretli(klasor):
        raise KasaHatasi("bozuk", "bu paket Studio lisansı ile otomatik açılır")
    kod_duzelt(kod)
    paketler = [p for p in (os.path.join(k, PAKET_DOSYASI) for k in (veri, klasor)) if os.path.isfile(p)]
    if not paketler:
        raise KasaHatasi("paket_yok")
    anahtar = None
    for p in paketler:
        try:
            with open(p, encoding="utf-8") as f:
                anahtar = paket_ac(json.load(f), kod)
            break
        except (KasaHatasi, OSError, ValueError):
            continue
    if anahtar is None:
        raise KasaHatasi("kod_yanlis")
    kimlik = anahtar_kimligi(anahtar)
    for d in dosyalar:
        yol = os.path.join(klasor, d)
        if sifreli_mi(yol):
            with open(yol + UZANTI, "rb") as f:
                if dosya_kimligi(f.read(_BASLIK)) != kimlik:
                    raise KasaHatasi("anahtar_uyumsuz", "kod başka bir sürümün modellerine ait")
    anahtar_yaz(veri, anahtar)
