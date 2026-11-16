"""Creates the model package and activation codes for distribution.

  python installer/model_package.py key --create     creates the master key (once); shows its status
  python installer/model_package.py encrypt          engine/models/*.onnx -> installer/encrypted-models/*.onnx.aesgcm
  python installer/model_package.py codes --count 3 --file C:/safe/codes.txt
                                                     creates activation codes and adds their slots to the key package
  python installer/model_package.py verify           compares the package contents with the source models

The master key (32 bytes) lives outside this repository, bound to this Windows user with Windows DPAPI:
%LOCALAPPDATA%\\AugeLab\\ISG-Kamera\\ (the ISG_MODEL_KEY environment variable can point to another file). This
script never writes the key in clear text to the screen or to a file; it only shows its id (first 8 bytes of its hash).
If the key is lost, no new codes can be created for old packages; keep your own backup.

Activation codes are only visible at the moment they are created: written to the --file file if given, otherwise to
the screen. The package (anahtar-paketi.json) contains no codes, only slots that a code opens. The package can be
distributed; the codes are not.

Distribution build:  with the environment variable ISG_MODEL_PACKAGE=encrypted  pyinstaller installer/IsgKamera.spec
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from isg_core import vault  # noqa: E402
from isg_core.catalog import MODEL_FILES  # noqa: E402

PLAIN_DIR = ROOT / "engine" / "models"
OUT_DIR = ROOT / "installer" / "encrypted-models"
KEY_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AugeLab" / "ISG-Kamera"
KEY_FILE_NAMES = ("model-anahtari.dpapi", "model-pilot-v1.dpapi")  # the second one is the key created in the first trial


def key_file() -> Path:
    env = os.environ.get("ISG_MODEL_KEY") or os.environ.get("ISG_MODEL_ANAHTARI")  # old name: developer fallback
    if env:
        return Path(env)
    for name in KEY_FILE_NAMES:
        if (KEY_DIR / name).is_file():
            return KEY_DIR / name
    return KEY_DIR / KEY_FILE_NAMES[0]


def master_key() -> bytes:
    path = key_file()
    if not path.is_file():
        raise SystemExit(f"Master key missing: {path}\nFirst run: python installer/model_package.py key --create")
    protected = path.read_bytes()
    for entropy in (vault._DPAPI_ENTROPY, None):  # None: a key protected without entropy, from before this script
        try:
            key = vault._dpapi(protected, protect=False, entropy=entropy)
        except vault.VaultError:
            continue
        if len(key) == vault.KEY_SIZE:
            return key
    raise SystemExit(f"Could not read the master key: {path} (it may belong to another Windows user)")


def read_package() -> dict | None:
    try:
        return json.loads((OUT_DIR / vault.KEY_PACKAGE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_package(package: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / (vault.KEY_PACKAGE_FILE + ".new")
    tmp.write_text(json.dumps(package, indent=1), encoding="utf-8")
    os.replace(tmp, OUT_DIR / vault.KEY_PACKAGE_FILE)


def current_package(key: bytes) -> dict:
    """The package of this key: with its slots if it exists, otherwise (or if it belongs to another key) empty."""
    package = read_package()
    if not package or package.get("anahtar_kimligi") != vault.key_id(key).hex():
        package = vault.build_package(key)
    return package


def cmd_key(a) -> int:
    path = key_file()
    if a.create:
        if path.is_file():
            raise SystemExit(f"Master key already exists: {path} (it is not overwritten)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(vault._dpapi(vault.generate_key(), protect=True))
        print("Master key created:", path)
    key = master_key()
    print(f"Master key: {path} · id {vault.key_id(key).hex()}")
    package = read_package()
    if package:
        same = package.get("anahtar_kimligi") == vault.key_id(key).hex()
        print(f"Key package: {len(package.get('yuvalar') or [])} slots" + ("" if same else " · BELONGS TO ANOTHER KEY"))
    return 0


def cmd_encrypt(a) -> int:
    key = master_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [f for f in MODEL_FILES if not (PLAIN_DIR / f).is_file()]
    if missing:
        raise SystemExit(f"Plain model file missing: {', '.join(missing)}")
    for f in MODEL_FILES:
        plain = (PLAIN_DIR / f).read_bytes()
        encrypted = vault.encrypt(plain, key, f)
        if vault.decrypt(encrypted, key, f) != plain:
            raise SystemExit(f"{f}: model package could not be verified")
        tmp = OUT_DIR / (f + vault.EXTENSION + ".new")
        tmp.write_bytes(encrypted)
        os.replace(tmp, OUT_DIR / (f + vault.EXTENSION))
        print(f"  {f}{vault.EXTENSION}  {len(encrypted):>11,} bytes  (plain: {len(plain):,})")
    for p in OUT_DIR.glob("*" + vault.EXTENSION):  # keep old files that are no longer in the list out of the package
        if p.name[:-len(vault.EXTENSION)] not in MODEL_FILES:
            p.unlink()
    package = current_package(key)
    write_package(package)
    print(f"Model package ready ({len(MODEL_FILES)} files): {OUT_DIR} · key id {vault.key_id(key).hex()} · {len(package['yuvalar'])} slots in the package")
    if not package["yuvalar"]:
        print("The package has no activation code yet: python installer/model_package.py codes")
    return 0


def cmd_codes(a) -> int:
    key = master_key()
    package = current_package(key)
    codes = [vault.generate_code() for _ in range(max(1, a.count))]
    package["yuvalar"] += [vault.package_slot(key, c) for c in codes]
    write_package(package)
    if a.file:
        target = Path(a.file).resolve()
        if ROOT in target.parents:
            raise SystemExit("Write the code file somewhere outside the repository.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("".join(c + "\n" for c in codes))
        print(f"{len(codes)} activation code(s) added to the file: {target}")
    else:
        print("Activation codes (visible only now; if you do not keep them, you will need to create new ones):")
        for c in codes:
            print("  " + c)
    print(f"{len(package['yuvalar'])} slots in the package: {OUT_DIR / vault.KEY_PACKAGE_FILE}")
    return 0


def cmd_verify(a) -> int:
    key = master_key()
    failed = 0
    for f in MODEL_FILES:
        try:
            opened = vault.decrypt((OUT_DIR / (f + vault.EXTENSION)).read_bytes(), key, f)
            same = hashlib.sha256(opened).digest() == hashlib.sha256((PLAIN_DIR / f).read_bytes()).digest()
            print(("  same       " if same else "  DIFFERENT  ") + f)
            failed += not same
        except (OSError, vault.VaultError) as e:
            print(f"  NOT OPENED  {f}: {e}")
            failed += 1
    package = read_package() or {}
    print(f"Key package: {len(package.get('yuvalar') or [])} slots · belongs to the key: {package.get('anahtar_kimligi') == vault.key_id(key).hex()}")
    return 1 if failed else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="İSG Kamera model package and activation codes")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("key", help="master key status; creates it with --create")
    p.add_argument("--create", action="store_true")
    sub.add_parser("encrypt", help="creates the distribution package from the four models")
    p = sub.add_parser("codes", help="creates an activation code and adds it to the package")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--file", help="file the codes are appended to (outside the repository); if omitted, they are printed")
    sub.add_parser("verify", help="compares the package contents with the source models")
    a = ap.parse_args()
    return {"key": cmd_key, "encrypt": cmd_encrypt, "codes": cmd_codes, "verify": cmd_verify}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
