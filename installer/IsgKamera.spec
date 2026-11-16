import json
import hashlib
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
# Ready-made ISG scene: Studio saves scenes encrypted, so it is built and saved once in Studio (installer/build_scene.py).
# It is not kept in the repository (.gitignore: *.pmod); it goes into the exe and the panel runs it headless.
scene = root / "installer" / "scene" / "isg-kamera.pmod"
if not scene.is_file():
    raise SystemExit(f"Ready-made ISG scene missing: {scene}. Produce it with installer/build_scene.py and save it in Studio with Save As.")
data = [
    (str(scene), "installer/scene"),
    (str(root / "web"), "web"),
    (str(root / "samples"), "samples"),
    (str(root / "engine" / "isg_core"), "engine/isg_core"),
    (str(root / "engine" / "deps"), "engine/deps"),
    (str(root / "LICENSE"), "."),
    (str(root / "ATTRIBUTIONS.md"), "."),
    (str(root / "MODEL-LICENSES.md"), "."),
]
plain_models = sorted((root / "engine/models").glob("*.onnx"))
# Old values and variable names are still accepted (developer builds only).
mode = (os.environ.get("ISG_MODEL_PACKAGE") or os.environ.get("ISG_MODEL_PAKETI") or "").strip().lower()
mode = {"duz": "plain", "sifreli": "encrypted", "lisansli": "licensed"}.get(mode, mode)
if mode == "community":
    package = Path(os.environ.get("ISG_COMMUNITY_MODEL_DIR") or os.environ.get("ISG_COMMUNITY_MODEL_KLASORU")
                   or root / "installer" / "community-models")
    marker = package / "community-paket.json"
    try:
        manifest = json.loads(marker.read_text(encoding="ascii"))
        package_id = bytes.fromhex(manifest["id"])
        if len(package_id) != 16 or manifest["fixture"] is not False:
            raise ValueError("A test package cannot be used for distribution.")
        dll = package / f"goruntu-{package_id.hex()}.dll"
        if not dll.is_file():
            raise ValueError("Rust model component missing.")
        notices = package / "RUST-NOTICES.txt"
        if not notices.is_file() or not notices.stat().st_size:
            raise ValueError("Rust dependency notices missing.")
        models = [package / (name + ".onnx.isgm") for name in
                  ("isg-nesne-1280", "isg-ek-1280", "isg-iskelet", "isg-oznitelik")]
        for m in models:
            with m.open("rb") as f:
                if f.read(24) != b"ISGMOD\x01\x00" + package_id or m.stat().st_size <= 68:
                    raise ValueError(f"Invalid package header: {m.name}")
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise SystemExit(f"Community model package missing or invalid: {e}") from e
    data += [(str(m), "engine/models") for m in [marker, dll, *models]]
    data.append((str(notices), "."))
elif mode == "licensed":
    package = Path(os.environ.get("ISG_LICENSED_MODEL_DIR") or os.environ.get("ISG_LISANSLI_MODEL_KLASORU")
                   or root / "installer" / "licensed-models")
    signer = package / "model-imzalayan.rsa"
    models = sorted(package.glob("*.onnx.aesgcm"))
    required = {"isg-nesne-1280.onnx.aesgcm", "isg-iskelet.onnx.aesgcm"}
    if not signer.is_file() or not required.issubset({m.name for m in models}):
        raise SystemExit("Licensed model package missing. Package it first with installer/issue_model_license.py.")
    digest = hashlib.sha256(signer.read_bytes()).digest()
    key_ids = set()
    header = b"AUGELAB-AESGCM\x03"
    header_size = len(header) + 8 + len(digest)
    for m in models:
        with m.open("rb") as f:
            head = f.read(header_size)
        if len(head) != header_size or not head.startswith(header) or head[-32:] != digest:
            raise SystemExit(f"Invalid license header in model: {m.name}")
        key_ids.add(head[-40:-32])
    if len(key_ids) != 1:
        raise SystemExit("Licensed models must belong to the same package.")
    data += [(str(m), "engine/models") for m in [signer, *models]]
elif mode == "encrypted":
    package = root / "installer" / "encrypted-models"
    missing = [m.name for m in plain_models if not (package / (m.name + ".aesgcm")).is_file()]
    stale = [m.name for m in plain_models if m.name not in missing and (package / (m.name + ".aesgcm")).stat().st_mtime < m.stat().st_mtime]
    try:
        slots = len(json.loads((package / "anahtar-paketi.json").read_text(encoding="utf-8"))["yuvalar"])
    except (OSError, ValueError, KeyError):
        slots = 0
    if missing or stale:
        raise SystemExit(f"Model package missing or out of date ({', '.join(missing + stale)}). Rebuild the package: python installer/model_package.py encrypt")
    if not slots:
        raise SystemExit("The key package has no activation code: python installer/model_package.py codes")
    data += [(str(package / (m.name + ".aesgcm")), "engine/models") for m in plain_models]
    data += [(str(package / "anahtar-paketi.json"), "engine/models")]
else:
    data += [(str(model), "engine/models") for model in plain_models]

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root), str(root / "engine")],
    datas=data,
    hiddenimports=(
        collect_submodules("isg_core")
    ),
    excludes=["torch", "tensorflow", "matplotlib", "pytest"],
)
if mode == "community":
    # ONNX Runtime's sample graphs are not used in the Community installation.
    a.datas = [(name, path, kind) for name, path, kind in a.datas if not name.lower().endswith(".onnx")]
# The Visual C++ runtime libraries are taken from Windows' own folder. PyInstaller collects them from the first place it
# finds them on PATH; if an old msvcp140.dll from another program on PATH gets into the package, onnxruntime crashes
# with an access violation (0xC0000005) while loading a model.
_system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
_crt = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll"}
a.binaries = [(name, str(_system / name), kind) if name.lower() in _crt and (_system / name).is_file() else (name, path, kind) for name, path, kind in a.binaries]
for _name, _path, _kind in a.binaries:
    if _name.lower() == "msvcp140.dll":
        import ctypes, ctypes.wintypes

        _size = ctypes.windll.version.GetFileVersionInfoSizeW(_path, None)
        _buf = ctypes.create_string_buffer(_size)
        ctypes.windll.version.GetFileVersionInfoW(_path, 0, _size, _buf)
        _info, _n = ctypes.c_void_p(), ctypes.wintypes.UINT()
        ctypes.windll.version.VerQueryValueW(_buf, "\\", ctypes.byref(_info), ctypes.byref(_n))
        _ms = ctypes.cast(_info, ctypes.POINTER(ctypes.wintypes.DWORD * 4)).contents[2]  # dwFileVersionMS
        if (_ms >> 16, _ms & 0xFFFF) < (14, 40):
            raise SystemExit(f"msvcp140.dll is too old ({_ms >> 16}.{_ms & 0xFFFF}, source: {_path}); install the current Visual C++ 2015-2022 Redistributable (x64).")

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name={"encrypted": "IsgKamera-encrypted", "licensed": "IsgKamera-licensed",
          "community": "IsgKamera-community"}.get(mode, "IsgKamera"),
    console=True,
    upx=False,
    icon=str(root / "installer" / "isg-kamera.ico"),  # drawn by installer/make_icon.py
)
