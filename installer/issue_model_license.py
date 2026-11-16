"""İSG Kamera package issuer. Run only by the licensing team."""
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from isg_core import device_key, vault, model_license as ml
from isg_core.catalog import MODEL_FILES

PACKAGE_DIR = ROOT / "installer" / "licensed-models"
DPAPI_ENTROPY = b"IsgKamera offline license issuer v1"


def _key_path(file: str) -> Path:
    path = Path(file).resolve()
    if path.is_relative_to(ROOT) or path.suffix.lower() != ".dpapi":
        raise ValueError("Keep the license issuer key in a .dpapi file outside the repository.")
    return path


def create_key(a):
    path = _key_path(a.key)
    if path.exists():
        raise ValueError("The license issuer key already exists.")
    signer = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pkcs8 = signer.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    protected = vault._dpapi(vault.generate_key() + pkcs8, True, DPAPI_ENTROPY)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(protected)
    print("License issuer key saved with Windows protection.")


def _read_keys(file):
    protected = ml._read(_key_path(file), 32_768)
    raw = vault._dpapi(protected, False, DPAPI_ENTROPY)
    signer = serialization.load_der_private_key(raw[32:], password=None)
    if not isinstance(signer, rsa.RSAPrivateKey) or signer.key_size != 3072:
        raise ValueError("The license issuer key is invalid.")
    numbers = signer.public_key().public_numbers()
    return raw[:32], signer, device_key.public_blob(numbers.e, numbers.n)


def build_package(a):
    key, _, public = _read_keys(a.key)
    target = Path(a.out).resolve()
    if target.exists():
        raise ValueError("The package folder already exists. Choose a new output folder.")
    source = Path(a.source)
    models = [source / name for name in MODEL_FILES if (source / name).is_file()]
    if not {"isg-nesne-1280.onnx", "isg-iskelet.onnx"}.issubset({m.name for m in models}):
        raise ValueError("The main object and pose models are required.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="isg-package-", dir=target.parent) as tmp:
        folder = Path(tmp) / "package"
        folder.mkdir()
        (folder / ml.SIGNER_FILE).write_bytes(public)
        for model in models:
            content = vault.encrypt(model.read_bytes(), key, model.name, signer=hashlib.sha256(public).digest())
            (folder / (model.name + vault.EXTENSION)).write_bytes(content)
        os.rename(folder, target)
    print(f"Model package ready ({len(models)} files): {target}")


def issue_license(a):
    key, signer, public = _read_keys(a.key)
    key_id = vault.key_id(key).hex()
    request = json.loads(ml._read(Path(a.request)))
    if (request["bicim"] != ml.REQUEST_FORMAT or request["model"] != key_id
            or request["imzalayan"] != ml.sha256_hex(public)):
        raise ValueError("The device request does not belong to this model or license issuer.")
    device = ml.b64decode(request["cihaz"])
    e, n = device_key.public_components(device)
    expires = datetime.fromisoformat(a.expires)
    if expires.tzinfo is None or expires.timestamp() <= time.time():
        raise ValueError("Enter a future end time with a time zone (e.g. 2027-01-01T00:00:00+03:00).")
    wrapped = rsa.RSAPublicNumbers(e, n).public_key().encrypt(
        key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    body = ml.json_bytes({"bicim": ml.LICENSE_FORMAT, "model": key_id, "cihaz": ml.sha256_hex(device),
                          "baslangic": int(time.time()) - 120, "bitis": int(expires.timestamp()), "sarili": ml.b64encode(wrapped)})
    signature = signer.sign(ml.SIGNATURE_DOMAIN + body, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    target = Path(a.out)
    if target.suffix.lower() != ".lic":
        raise ValueError("The license output must have the .lic extension.")
    with target.open("xb") as f:
        f.write(ml.json_bytes({"veri": ml.b64encode(body), "imza": ml.b64encode(signature)}))
    print(f"Device-specific license ready: {target}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", required=True, help="License issuer .dpapi file outside the repository")
    commands = ap.add_subparsers(required=True)
    commands.add_parser("create-key").set_defaults(action=create_key)
    p = commands.add_parser("package")
    p.set_defaults(action=build_package)
    p.add_argument("--source", default=ROOT / "engine" / "models")
    p.add_argument("--out", default=PACKAGE_DIR)
    p = commands.add_parser("issue", help="Run after verifying the existing AugeLab license")
    p.set_defaults(action=issue_license)
    p.add_argument("--request", required=True)
    p.add_argument("--expires", required=True, help="ISO 8601 end time that does not exceed the verified AugeLab license")
    p.add_argument("--out", default="model.lic")
    a = ap.parse_args()
    try:
        a.action(a)
    except (ValueError, TypeError, KeyError, OSError, vault.VaultError) as e:
        ap.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()
