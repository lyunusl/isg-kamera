"""The offline, signed model license of İSG Kamera. User-visible messages are translated with T().

The file names, the signature domain, the format strings and the JSON fields of the license, the device request and the
returned license dict (bitis, cihaz, sarili) are part of the issued license format and must not change.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from . import device_key
from .i18n import T

LICENSE_FILE = "model.lic"
SIGNER_FILE = "model-imzalayan.rsa"
MAX_SIZE = 16_384
SIGNATURE_DOMAIN = b"ISG-Kamera/model-lisans/v1\x00"
REQUEST_FORMAT = "isg-kamera-cihaz-v1"
LICENSE_FORMAT = "isg-kamera-model-v1"


class LicenseError(Exception):
    pass


def json_bytes(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(data: str) -> bytes:
    if not isinstance(data, str):
        raise ValueError(T("Base64 text expected."))
    return base64.b64decode(data, validate=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, limit: int = MAX_SIZE) -> bytes:
    with path.open("rb") as f:
        data = f.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError(T("Invalid file size."))
    return data


def check_not_expired(end: int) -> None:
    if time.time() >= end:
        raise LicenseError(T("The model license has expired. Upload a new model.lic file."))


def device_request(key_id: bytes, signer_digest: bytes) -> dict:
    try:
        public = device_key.public_key(create=True)
    except (device_key.DeviceKeyError, ValueError) as e:
        raise LicenseError(str(e)) from None
    return {"bicim": REQUEST_FORMAT, "cihaz": b64encode(public),
            "model": key_id.hex(), "imzalayan": signer_digest.hex()}


def verify(raw: bytes, folder: str, key_id: bytes, signer_digest: bytes) -> dict:
    """Verifies the signature, model package, device and validity period. The model key is not opened at this stage."""
    try:
        if len(raw) > MAX_SIZE:
            raise ValueError(T("Invalid file size."))
        public = _read(Path(folder) / SIGNER_FILE, 411)
        if hashlib.sha256(public).digest() != signer_digest:
            raise LicenseError(T("The signing key of the model package does not match."))
        device_key.public_components(public)
        envelope = json.loads(raw)
        body = b64decode(envelope["veri"])
        signature = b64decode(envelope["imza"])
        if not device_key.verify_signature(public, SIGNATURE_DOMAIN + body, signature):
            raise LicenseError(T("The model license signature is not valid."))
        lic = json.loads(body)
        if lic["bicim"] != LICENSE_FORMAT or lic["model"] != key_id.hex():
            raise LicenseError(T("The model license does not belong to this package."))
        start, end = lic["baslangic"], lic["bitis"]
        if type(start) is not int or type(end) is not int or not 0 < start < end:
            raise ValueError(T("The license dates are not valid."))
        check_not_expired(end)
        if time.time() < start:
            raise LicenseError(T("The model license has not started yet. Check the Windows date and time."))
        device = device_key.public_key()
        if lic["cihaz"] != sha256_hex(device):
            raise LicenseError(T("The model license does not belong to this Windows user or computer."))
        _, n = device_key.public_components(device)
        wrapped = b64decode(lic["sarili"])
        if len(wrapped) != n.bit_length() // 8:
            raise ValueError(T("The key size in the model license is not valid."))
        return {"bitis": end, "cihaz": lic["cihaz"], "sarili": wrapped}
    except device_key.DeviceKeyError as e:
        raise LicenseError(str(e)) from None
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise LicenseError(T("The model license or the package's verification file could not be read.")) from None


def local_license(data_dir: str | None, folder: str, key_id: bytes, signer_digest: bytes) -> dict:
    if not data_dir:
        raise LicenseError(T("Model license not found. Upload the model.lic file in the panel."))
    try:
        raw = _read(Path(data_dir) / LICENSE_FILE)
    except FileNotFoundError:
        raise LicenseError(T("Model license not found. Upload the model.lic file in the panel.")) from None
    except (OSError, ValueError):
        raise LicenseError(T("The model license file could not be read.")) from None
    return verify(raw, folder, key_id, signer_digest)


def save_license(data_dir: str, raw: bytes, folder: str, key_id: bytes, signer_digest: bytes) -> None:
    verify(raw, folder, key_id, signer_digest)
    target = Path(data_dir) / LICENSE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".model-lic-", delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def model_key(lic: dict) -> bytearray:
    check_not_expired(lic["bitis"])
    try:
        return device_key.unwrap_key(lic["sarili"], lic["cihaz"])
    except device_key.DeviceKeyError as e:
        raise LicenseError(str(e)) from None
