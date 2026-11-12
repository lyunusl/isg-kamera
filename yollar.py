"""Klasörler ve programla gelen dosyalar.

KAYNAK: programla gelen dosyalar (web sayfaları, modeller, örnekler). Exe'de geçici açılma klasörü.
VERI:   kullanıcının verisi (ayarlar, kayıtlar, veritabanı, klipler). Exe'de %LOCALAPPDATA%\\IsgKamera,
        kaynak koddan çalışırken proje içindeki veri/ klasörü. ISG_KAMERA_VERI ortam değişkeniyle değiştirilebilir.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SURUM = "0.1.0"
UYGULAMA = "IsgKamera"
DONDURULMUS = bool(getattr(sys, "frozen", False))
KAYNAK = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _veri() -> Path:
    ortam = os.environ.get("ISG_KAMERA_VERI")
    if ortam:
        return Path(ortam).resolve()
    if DONDURULMUS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / UYGULAMA
    return Path(__file__).resolve().parent / "veri"


VERI = _veri()
AYAR = VERI / "ayar"          # kamera başına <kod>.json ve bölge çizme karesi _kare_<kod>.jpg
VIDEOLAR = VERI / "videolar"  # ham kayıtlar (analizden sonra silinir)
CIKTI = VERI / "cikti"        # klipler, son kareler (yüzler gizli)
ONBELLEK = VERI / "onbellek"
VT = VERI / "isg.db"
GENEL_AYAR = VERI / "genel.json"

WEB = KAYNAK / "web"
ORNEKLER = KAYNAK / "ornekler"
MOTOR = KAYNAK / "motor"                              # AugeLab Studio'ya yerleştirilen motor (isg_motor, modeller, deps)
MODEL_NESNE = MOTOR / "modeller" / "isg-nesne-1280.onnx"       # kişi, baret, yelek, eldiven, gözlük, iş ayakkabısı, forklift
MODEL_EK = MOTOR / "modeller" / "isg-ek-1280.onnx"             # isteğe bağlı: ek koruyucular (maske, siper, kulaklık...) ve nesneler


def ornek_kodlar() -> list[str]:
    d = ORNEKLER / "ayar"
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def ffmpeg() -> str:
    """Programla gelen ffmpeg (imageio-ffmpeg paketi); yoksa PATH'teki."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    yol = shutil.which("ffmpeg")
    if not yol:
        raise SystemExit("ffmpeg bulunamadı.")
    return yol


_IS_NESNESI = None


def cocuk_bagla(surec) -> None:
    """Program kapanınca (pencere kapatılsa bile) bu alt süreç de kapansın: Windows iş nesnesi (job object)."""
    global _IS_NESNESI
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _IS_NESNESI is None:
            class Temel(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class GirdiCikti(ctypes.Structure):
                _fields_ = [(ad, ctypes.c_uint64) for ad in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class Genis(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Temel), ("IoInfo", GirdiCikti), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            h = k32.CreateJobObjectW(None, None)
            bilgi = Genis()
            bilgi.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not h or not k32.SetInformationJobObject(h, 9, ctypes.byref(bilgi), ctypes.sizeof(bilgi)):
                return
            _IS_NESNESI = h
        k32.AssignProcessToJobObject(_IS_NESNESI, int(surec._handle))
    except Exception:
        pass


def hazirla() -> bool:
    """Veri klasörünü kurar, örnek videoları ve ayarları (yoksa) kopyalar. İlk kurulumsa True döner."""
    ilk = not VERI.exists()
    for d in (AYAR, VIDEOLAR, CIKTI, ONBELLEK):
        d.mkdir(parents=True, exist_ok=True)
    for kod in ornek_kodlar():
        for kaynak, hedef in ((ORNEKLER / "videolar" / f"{kod}.mp4", VIDEOLAR / f"{kod}.mp4"),
                              (ORNEKLER / "ayar" / f"{kod}.json", AYAR / f"{kod}.json")):
            if kaynak.exists() and not hedef.exists():
                shutil.copy2(kaynak, hedef)
    return ilk
