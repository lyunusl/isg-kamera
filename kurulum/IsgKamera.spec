import json
import hashlib
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
data = [
    (str(root / "web"), "web"),
    (str(root / "ornekler"), "ornekler"),
    (str(root / "motor" / "isg_motor"), "motor/isg_motor"),
    (str(root / "motor" / "deps"), "motor/deps"),
    (str(root / "LICENSE"), "."),
    (str(root / "ATIFLAR.md"), "."),
    (str(root / "MODEL-LISANSLARI.md"), "."),
]
duz_modeller = sorted((root / "motor/modeller").glob("*.onnx"))
kip = os.environ.get("ISG_MODEL_PAKETI", "").strip().lower()
if kip == "community":
    paket = Path(os.environ.get("ISG_COMMUNITY_MODEL_KLASORU") or root / "kurulum" / "community-modeller")
    isaret = paket / "community-paket.json"
    try:
        bildirim = json.loads(isaret.read_text(encoding="ascii"))
        kimlik = bytes.fromhex(bildirim["id"])
        if len(kimlik) != 16 or bildirim["fixture"] is not False:
            raise ValueError("Test paketi dağıtımda kullanılamaz.")
        dll = paket / f"goruntu-{kimlik.hex()}.dll"
        if not dll.is_file():
            raise ValueError("Rust model bileşeni eksik.")
        bildirimler = paket / "RUST-NOTICES.txt"
        if not bildirimler.is_file() or not bildirimler.stat().st_size:
            raise ValueError("Rust bağımlılık bildirimleri eksik.")
        modeller = [paket / (ad + ".onnx.isgm") for ad in
                    ("isg-nesne-1280", "isg-ek-1280", "isg-iskelet", "isg-oznitelik")]
        for m in modeller:
            with m.open("rb") as f:
                if f.read(24) != b"ISGMOD\x01\x00" + kimlik or m.stat().st_size <= 68:
                    raise ValueError(f"Paket başlığı geçersiz: {m.name}")
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise SystemExit(f"Community model paketi eksik veya geçersiz: {e}") from e
    data += [(str(m), "motor/modeller") for m in [isaret, dll, *modeller]]
    data.append((str(bildirimler), "."))
elif kip == "lisansli":
    paket = Path(os.environ.get("ISG_LISANSLI_MODEL_KLASORU") or root / "kurulum" / "lisansli-modeller")
    imzalayan = paket / "model-imzalayan.rsa"
    modeller = sorted(paket.glob("*.onnx.aesgcm"))
    gerekli = {"isg-nesne-1280.onnx.aesgcm", "isg-iskelet.onnx.aesgcm"}
    if not imzalayan.is_file() or not gerekli.issubset({m.name for m in modeller}):
        raise SystemExit("Lisanslı model paketi eksik. Önce kurulum/model_lisans.py ile paketleyin.")
    ozet = hashlib.sha256(imzalayan.read_bytes()).digest()
    kimlikler = set()
    baslik = b"AUGELAB-AESGCM\x03"
    baslik_boyu = len(baslik) + 8 + len(ozet)
    for m in modeller:
        with m.open("rb") as f:
            bas = f.read(baslik_boyu)
        if len(bas) != baslik_boyu or not bas.startswith(baslik) or bas[-32:] != ozet:
            raise SystemExit(f"Modelin lisans başlığı geçersiz: {m.name}")
        kimlikler.add(bas[-40:-32])
    if len(kimlikler) != 1:
        raise SystemExit("Lisanslı modeller aynı pakete ait olmalı.")
    data += [(str(m), "motor/modeller") for m in [imzalayan, *modeller]]
elif kip == "sifreli":
    paket = root / "kurulum" / "sifreli-modeller"
    eksik = [m.name for m in duz_modeller if not (paket / (m.name + ".aesgcm")).is_file()]
    eski = [m.name for m in duz_modeller if m.name not in eksik and (paket / (m.name + ".aesgcm")).stat().st_mtime < m.stat().st_mtime]
    try:
        yuva = len(json.loads((paket / "anahtar-paketi.json").read_text(encoding="utf-8"))["yuvalar"])
    except (OSError, ValueError, KeyError):
        yuva = 0
    if eksik or eski:
        raise SystemExit(f"Model paketi eksik veya güncel değil ({', '.join(eksik + eski)}). Paketi yeniden oluşturun: python kurulum/model_paketi.py sifrele")
    if not yuva:
        raise SystemExit("Anahtar paketinde etkinleştirme kodu yok: python kurulum/model_paketi.py kod")
    data += [(str(paket / (m.name + ".aesgcm")), "motor/modeller") for m in duz_modeller]
    data += [(str(paket / "anahtar-paketi.json"), "motor/modeller")]
else:
    data += [(str(model), "motor/modeller") for model in duz_modeller]

a = Analysis(
    [str(root / "uygulama.py")],
    pathex=[str(root), str(root / "motor")],
    datas=data,
    hiddenimports=(
        collect_submodules("isg_motor")
        + collect_submodules("mcp.client")
        + collect_submodules("mcp.shared")
    ),
    excludes=["torch", "tensorflow", "matplotlib", "pytest"],
)
if kip == "community":
    # ONNX Runtime'ın örnek grafikleri Community kurulumunda kullanılmaz.
    a.datas = [(ad, yol, tur) for ad, yol, tur in a.datas if not ad.lower().endswith(".onnx")]
# Visual C++ çalışma kitaplıkları Windows'un kendi klasöründen alınır. PyInstaller bunları PATH'te ilk bulduğu yerden
# toplar; PATH'teki başka bir programın eski msvcp140.dll dosyası pakete girerse onnxruntime modeli yüklerken
# erişim ihlaliyle (0xC0000005) kapanır.
_sistem = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
_crt = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll"}
a.binaries = [(ad, str(_sistem / ad), tur) if ad.lower() in _crt and (_sistem / ad).is_file() else (ad, yol, tur) for ad, yol, tur in a.binaries]
for _ad, _yol, _tur in a.binaries:
    if _ad.lower() == "msvcp140.dll":
        import ctypes, ctypes.wintypes

        _boy = ctypes.windll.version.GetFileVersionInfoSizeW(_yol, None)
        _veri = ctypes.create_string_buffer(_boy)
        ctypes.windll.version.GetFileVersionInfoW(_yol, 0, _boy, _veri)
        _bilgi, _n = ctypes.c_void_p(), ctypes.wintypes.UINT()
        ctypes.windll.version.VerQueryValueW(_veri, "\\", ctypes.byref(_bilgi), ctypes.byref(_n))
        _ms = ctypes.cast(_bilgi, ctypes.POINTER(ctypes.wintypes.DWORD * 4)).contents[2]  # dwFileVersionMS
        if (_ms >> 16, _ms & 0xFFFF) < (14, 40):
            raise SystemExit(f"msvcp140.dll eski ({_ms >> 16}.{_ms & 0xFFFF}, kaynak: {_yol}); güncel Visual C++ 2015-2022 Redistributable (x64) kurun.")

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name={"sifreli": "IsgKamera-sifreli", "lisansli": "IsgKamera-lisansli",
          "community": "IsgKamera-community"}.get(kip, "IsgKamera"),
    console=True,
    upx=False,
    icon=str(root / "kurulum" / "isg-kamera.ico"),  # kurulum/simge.py çizer
)
