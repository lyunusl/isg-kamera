"""Model package format and the local model key.

A model can be in the model folder in two forms:

  isg-nesne-1280.onnx          plain file (when running from source, when you put in your own model)
  isg-nesne-1280.onnx.aesgcm   file from the distribution package

Plain files can be used in development mode. Licensed packages decrypt the model in memory and hand it to ONNX Runtime
as bytes. Version 3 adds the SHA-256 digest of the public key that signs the license to the AES-GCM header and requires
model.lic.

Package format:  "AUGELAB-AESGCM" (14) | version 0x02 (1) | key id (8) | nonce (12) | content | tag (16)
Authenticated additional data: the first 23 bytes + the model's file name (a model cannot be used under another model's name).
The key id is the first 8 bytes of the key's digest; it tells "wrong key" apart from "damaged file".

The model key (32 bytes) is kept in the data folder in the file `model-anahtari.dpapi`, bound to this Windows user with
Windows DPAPI. The panel and the engine in AugeLab Studio read the same file; both must run as the same Windows user.

The key reaches this computer with an activation code: each slot in `anahtar-paketi.json` is the model key wrapped with a
key derived from an activation code (PBKDF2-HMAC-SHA256). The code is used only to open the slot; it is not saved.
The byte strings, file names and JSON fields of the package format are already distributed and must not change.

Protection boundary: the key has to be on the computer that runs the model. The owner of an activated installation can
get the key and the decrypted model (with DPAPI or from the running process's memory). This scheme makes copying and
modifying the files harder; it does not replace copy protection or a license.

User-visible messages are English source texts translated with T() (REASONS holds N_-marked texts).
Only the standard library is used: AES-GCM via Windows CNG (bcrypt.dll), key protection via DPAPI (crypt32.dll).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets

from . import community_model, model_license
from .i18n import T, N_

MAGIC = b"AUGELAB-AESGCM"
FORMAT_VERSION = 2
FORMAT_VERSION_LICENSED = 3
EXTENSION = ".aesgcm"
KEY_FILE = "model-anahtari.dpapi"
KEY_PACKAGE_FILE = "anahtar-paketi.json"
KEY_SIZE = 32
KDF_ITERATIONS = 200_000
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32 symbols; no I, L, O, U (so they are not confused)
CODE_LENGTH = 25                                     # 125 bits

_HEADER = len(MAGIC) + 1 + 8
_HEADER_LICENSED = _HEADER + 32
_NONCE = 12
_TAG = 16
_DPAPI_ENTROPY = b"IsgKamera model anahtari v1"     # not secret: keeps the protected data from being opened for another purpose
_PACKAGE_AAD = b"isg-kamera-anahtar-paketi\0"
_TAG_MISMATCH = 0xC000A002

REASONS = {
    "key_missing": N_("The models have not been activated on this computer yet. Open the panel and enter the activation code."),
    "key_unreadable": N_("The model key on this computer could not be read. The key may belong to another Windows user; "
                         "open the panel and AugeLab Studio as the same user or enter the activation code again."),
    "key_mismatch": N_("The model key on this computer does not belong to these model files. Enter the activation code again."),
    "corrupt": N_("A model file is damaged or has been modified. Download the program again."),
    "invalid_code": N_("The activation code is not valid."),
    "package_missing": N_("Key package not found (anahtar-paketi.json)."),
    "unsupported": N_("This model package requires Windows."),
}


class VaultError(Exception):
    """`reason`: one of the codes in REASONS."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(T(REASONS.get(reason, reason)) + (f" ({detail})" if detail else ""))


# ---------------------------------------------------------------- AES-GCM (Windows CNG)

_CNG = None


def _cng():
    global _CNG
    if _CNG is not None:
        return _CNG
    import ctypes

    V, U = ctypes.c_void_p, ctypes.c_ulong

    class Info(ctypes.Structure):  # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO
        _fields_ = [("cbSize", U), ("dwInfoVersion", U), ("pbNonce", V), ("cbNonce", U), ("pbAuthData", V), ("cbAuthData", U),
                    ("pbTag", V), ("cbTag", U), ("pbMacContext", V), ("cbMacContext", U), ("cbAAD", U),
                    ("cbData", ctypes.c_ulonglong), ("dwFlags", U)]

    b = ctypes.WinDLL("bcrypt")
    b.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(V), ctypes.c_wchar_p, ctypes.c_wchar_p, U]
    b.BCryptSetProperty.argtypes = [V, ctypes.c_wchar_p, V, U, U]
    b.BCryptGenerateSymmetricKey.argtypes = [V, ctypes.POINTER(V), V, U, V, U, U]
    b.BCryptDestroyKey.argtypes = [V]
    for f in (b.BCryptEncrypt, b.BCryptDecrypt):
        f.argtypes = [V, ctypes.c_char_p, U, ctypes.POINTER(Info), V, U, V, U, ctypes.POINTER(U), U]
    for f in (b.BCryptOpenAlgorithmProvider, b.BCryptSetProperty, b.BCryptGenerateSymmetricKey, b.BCryptDestroyKey, b.BCryptEncrypt, b.BCryptDecrypt):
        f.restype = ctypes.c_long
    provider = V()
    if b.BCryptOpenAlgorithmProvider(ctypes.byref(provider), "AES", None, 0):
        raise VaultError("unsupported", T("could not open the AES provider"))
    mode = ctypes.create_unicode_buffer("ChainingModeGCM")
    if b.BCryptSetProperty(provider, "ChainingMode", ctypes.cast(mode, V), ctypes.sizeof(mode), 0):
        raise VaultError("unsupported", T("Could not open the model package"))
    _CNG = (ctypes, b, Info, provider)
    return _CNG


def _aes_gcm(key: bytes | bytearray, nonce: bytes, data: bytes, aad: bytes, tag: bytes | None = None):
    """Without `tag` it encrypts and returns (ciphertext, tag); with it, it decrypts and returns the plaintext.
    If the tag cannot be verified (wrong key, modified data): VaultError("corrupt")."""
    if os.name != "nt":
        return _aes_gcm_fallback(key, nonce, data, aad, tag)
    ctypes, b, Info, provider = _cng()
    V = ctypes.c_void_p
    handle = V()
    key_buf = ((ctypes.c_ubyte * len(key)).from_buffer(key) if isinstance(key, bytearray)
               else ctypes.c_char_p(key))
    if b.BCryptGenerateSymmetricKey(provider, ctypes.byref(handle), None, 0, key_buf, len(key), 0):
        raise VaultError("unsupported", T("could not set up the AES key"))
    output = None
    try:
        nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
        tag_buf = ctypes.create_string_buffer(tag, _TAG) if tag is not None else ctypes.create_string_buffer(_TAG)
        info = Info()
        info.cbSize, info.dwInfoVersion = ctypes.sizeof(Info), 1
        info.pbNonce, info.cbNonce = ctypes.addressof(nonce_buf), len(nonce)
        if aad:
            aad_buf = ctypes.create_string_buffer(aad, len(aad))
            info.pbAuthData, info.cbAuthData = ctypes.addressof(aad_buf), len(aad)
        info.pbTag, info.cbTag = ctypes.addressof(tag_buf), _TAG
        output = ctypes.create_string_buffer(max(1, len(data)))
        written = ctypes.c_ulong()
        operation = b.BCryptEncrypt if tag is None else b.BCryptDecrypt
        result = operation(handle, data, len(data), ctypes.byref(info), None, 0, ctypes.cast(output, V), len(data), ctypes.byref(written), 0) & 0xFFFFFFFF
        if result == _TAG_MISMATCH:
            raise VaultError("corrupt")
        if result or written.value != len(data):
            raise VaultError("unsupported", T("Could not process the model package (0x{code:08X})", code=result))
        body = ctypes.string_at(output, len(data))
        return (body, tag_buf.raw) if tag is None else body
    finally:
        if output is not None:
            ctypes.memset(output, 0, ctypes.sizeof(output))
        b.BCryptDestroyKey(handle)


def _aes_gcm_fallback(key, nonce, data, aad, tag):
    """Outside Windows (development and tests): with the `cryptography` package if it is installed."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise VaultError("unsupported") from None
    if tag is None:
        s = AESGCM(key).encrypt(nonce, data, aad or None)
        return s[:-_TAG], s[-_TAG:]
    try:
        return AESGCM(key).decrypt(nonce, data + tag, aad or None)
    except InvalidTag:
        raise VaultError("corrupt") from None


# ---------------------------------------------------------------- DPAPI (protection bound to this Windows user)


def _dpapi(data: bytes, protect: bool, entropy: bytes | None = _DPAPI_ENTROPY) -> bytes:
    """`entropy`: additional data included in the protection; None for none (to read a key protected outside this program)."""
    if os.name != "nt":
        raise VaultError("unsupported")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    operation = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    input_buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), ctypes.addressof(input_buf)), Blob()
    entropy_blob = None
    if entropy:
        entropy_buf = ctypes.create_string_buffer(entropy, len(entropy))
        entropy_blob = ctypes.byref(Blob(len(entropy), ctypes.addressof(entropy_buf)))
    if not operation(ctypes.byref(blob_in), None, entropy_blob, None, None, 0x1, ctypes.byref(blob_out)):  # 0x1: do not show a window
        raise VaultError("key_unreadable")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# ---------------------------------------------------------------- model files


def generate_key() -> bytes:
    return secrets.token_bytes(KEY_SIZE)


def key_id(key: bytes) -> bytes:
    digest = hashlib.sha256(b"isg-kamera-model-anahtari\0")
    digest.update(key)
    return digest.digest()[:8]


def _check_key(key) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_SIZE:
        raise VaultError("key_unreadable", T("the key must be 32 bytes"))


def encrypt(plain: bytes, key: bytes, name: str, *, signer: bytes | None = None) -> bytes:
    """`name`: the model's plain file name (isg-nesne-1280.onnx); the same name must be given when decrypting."""
    _check_key(key)
    version = FORMAT_VERSION_LICENSED if signer is not None else FORMAT_VERSION
    header = MAGIC + bytes([version]) + key_id(key)
    if signer is not None:
        if len(signer) != 32:
            raise ValueError("The signer digest must be 32 bytes.")
        header += signer
    nonce = secrets.token_bytes(_NONCE)
    body, tag = _aes_gcm(bytes(key), nonce, plain, header + name.encode("utf-8"))
    return header + nonce + body + tag


def _header_info(head: bytes) -> tuple[bytes, bytes | None]:
    if len(head) < _HEADER or not head.startswith(MAGIC):
        raise VaultError("corrupt")
    kid = head[len(MAGIC) + 1:_HEADER]
    if head[len(MAGIC)] == FORMAT_VERSION:
        return kid, None
    if head[len(MAGIC)] == FORMAT_VERSION_LICENSED and len(head) >= _HEADER_LICENSED:
        return kid, head[_HEADER:_HEADER_LICENSED]
    raise VaultError("corrupt")


def file_key_id(head: bytes) -> bytes:
    return _header_info(head)[0]


def decrypt(data: bytes, key: bytes | bytearray, name: str) -> bytes:
    _check_key(key)
    kid, signer = _header_info(data[:_HEADER_LICENSED])
    size = _HEADER_LICENSED if signer is not None else _HEADER
    if len(data) < size + _NONCE + _TAG:
        raise VaultError("corrupt")
    if kid != key_id(key):
        raise VaultError("key_mismatch")
    nonce = data[size:size + _NONCE]
    return _aes_gcm(key, nonce, data[size + _NONCE:-_TAG], data[:size] + name.encode("utf-8"), data[-_TAG:])


def model_exists(path: str) -> bool:
    """Returns whether the model file or its package entry is in place."""
    if community_model.is_community_model(path):
        return os.path.isfile(path + community_model.EXTENSION)
    return os.path.isfile(path + EXTENSION) or (not _is_licensed_folder(path) and os.path.isfile(path))


def _is_licensed_folder(path: str) -> bool:
    return os.path.isfile(os.path.join(os.path.dirname(path), model_license.SIGNER_FILE))


def is_encrypted(path: str) -> bool:
    return (_is_licensed_folder(path) or not os.path.isfile(path)) and os.path.isfile(path + EXTENSION)


def model_source(path: str, data_dir: str | None = None):
    """The source for ONNX Runtime: a file path, or the model bytes taken from the package.
    `data_dir`: the data folder that holds the model key."""
    return model_and_expiry(path, data_dir)[0]


def model_and_expiry(path: str, data_dir: str | None = None) -> tuple[str | bytes, int | None]:
    if community_model.is_community_model(path):
        return community_model.open_model(path)[0], None
    if not is_encrypted(path):
        if _is_licensed_folder(path):
            raise model_license.LicenseError(T("A model file is missing from the licensed package."))
        return path, None
    with open(path + EXTENSION, "rb") as f:
        kid, signer = _header_info(f.read(_HEADER_LICENSED))
        if signer is not None:
            lic = model_license.local_license(data_dir, os.path.dirname(path), kid, signer)
            f.seek(0)
            content = f.read()
            key = model_license.model_key(lic)
            try:
                return decrypt(content, key, os.path.basename(path)), lic["bitis"]
            finally:
                key[:] = bytes(len(key))
        if _is_licensed_folder(path):
            raise model_license.LicenseError(T("The licensed package contains a model in the old format."))
        f.seek(0)
        return decrypt(f.read(), read_key(data_dir), os.path.basename(path)), None


def license_package(folder: str, files) -> tuple[bytes, bytes] | None:
    if community_model.is_community_folder(folder):
        return None  # This package does not accept model.lic.
    headers = set()
    for name in files:
        path = os.path.join(folder, name)
        if is_encrypted(path):
            with open(path + EXTENSION, "rb") as f:
                headers.add(_header_info(f.read(_HEADER_LICENSED)))
    licensed = [h for h in headers if h[1] is not None]
    if not licensed and not os.path.isfile(os.path.join(folder, model_license.SIGNER_FILE)):
        return None
    if len(licensed) != 1 or len(headers) != 1:
        raise model_license.LicenseError(T("This folder contains models from different packages."))
    return licensed[0]


# ---------------------------------------------------------------- the key on this computer

_CACHE: dict = {}


def key_path(data_dir: str) -> str:
    return os.path.join(data_dir, KEY_FILE)


def read_key(data_dir: str | None) -> bytes:
    if not data_dir:
        raise VaultError("key_missing")
    path = key_path(data_dir)
    try:
        st = os.stat(path)
    except OSError:
        raise VaultError("key_missing") from None
    stamp = (path, st.st_mtime_ns, st.st_size)
    if _CACHE.get("stamp") == stamp:
        return _CACHE["key"]
    try:
        with open(path, "rb") as f:
            protected = f.read()
    except OSError:
        raise VaultError("key_unreadable") from None
    key = _dpapi(protected, protect=False) if protected else b""
    if len(key) != KEY_SIZE:
        raise VaultError("key_unreadable")
    _CACHE.update(stamp=stamp, key=key)
    return key


def write_key(data_dir: str, key: bytes) -> str:
    _check_key(key)
    os.makedirs(data_dir, exist_ok=True)
    path = key_path(data_dir)
    tmp = path + ".yeni"
    with open(tmp, "wb") as f:
        f.write(_dpapi(bytes(key), protect=True))
    os.replace(tmp, path)
    _CACHE.clear()
    return path


def delete_key(data_dir: str) -> None:
    _CACHE.clear()
    try:
        os.remove(key_path(data_dir))
    except FileNotFoundError:
        pass


def status(folder: str, files, data_dir: str | None) -> dict:
    """Returns the local activation state of the model package for the panel.
    status: "plain" | "active" | "license_required" | "key_missing" | "key_unreadable" | "key_mismatch" | "corrupt"
    (community packages: community_model.status)"""
    files = tuple(files)
    if community_model.is_community_folder(folder) or any(community_model.is_community_model(os.path.join(folder, d)) for d in files):
        return community_model.status(folder, files)
    encrypted = [d for d in files if is_encrypted(os.path.join(folder, d))]
    package = any(os.path.isfile(os.path.join(k, KEY_PACKAGE_FILE)) for k in (data_dir, folder) if k)
    if not encrypted and not os.path.isfile(os.path.join(folder, model_license.SIGNER_FILE)):
        return {"encrypted": False, "status": "plain", "package": package}
    try:
        info = license_package(folder, encrypted)
        if info is not None:
            lic = model_license.local_license(data_dir, folder, *info)
            return {"encrypted": True, "status": "active", "licensed": True, "expires": lic["bitis"]}
    except model_license.LicenseError as e:
        return {"encrypted": True, "status": "license_required", "licensed": True, "message": str(e)}
    except (VaultError, OSError):
        return {"encrypted": True, "status": "corrupt", "message": T(REASONS["corrupt"]),
                "licensed": os.path.isfile(os.path.join(folder, model_license.SIGNER_FILE))}
    try:
        kid = key_id(read_key(data_dir))
        for d in encrypted:
            with open(os.path.join(folder, d) + EXTENSION, "rb") as f:
                if file_key_id(f.read(_HEADER)) != kid:
                    raise VaultError("key_mismatch")
    except VaultError as e:
        return {"encrypted": True, "status": e.reason, "message": str(e), "package": package}
    except OSError:
        return {"encrypted": True, "status": "corrupt", "message": T(REASONS["corrupt"]), "package": package}
    return {"encrypted": True, "status": "active", "package": package}


# ---------------------------------------------------------------- activation code and key package


def generate_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 5] for i in range(0, CODE_LENGTH, 5))


def normalize_code(code) -> str:
    """Evens out typing differences (lower case, spaces, dashes; I/L -> 1, O -> 0). If invalid: VaultError("invalid_code")."""
    raw = str(code or "").upper().translate(str.maketrans({"I": "1", "L": "1", "O": "0"}))
    raw = "".join(c for c in raw if c in CODE_ALPHABET)
    if len(raw) != CODE_LENGTH:
        raise VaultError("invalid_code")
    return raw


def _slot_id(raw: str) -> str:
    return hashlib.sha256(b"isg-kamera-yuva\0" + raw.encode("ascii")).hexdigest()[:8]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def package_slot(key: bytes, code: str) -> dict:
    """Wraps the model key with this activation code. The code is not stored in the slot."""
    _check_key(key)
    raw = normalize_code(code)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(_NONCE)
    wrap = hashlib.pbkdf2_hmac("sha256", raw.encode("ascii"), salt, KDF_ITERATIONS, KEY_SIZE)
    body, tag = _aes_gcm(wrap, nonce, bytes(key), _PACKAGE_AAD + key_id(key))
    return {"kimlik": _slot_id(raw), "tuz": _b64(salt), "tur": KDF_ITERATIONS, "nonce": _b64(nonce), "sarili": _b64(body + tag)}


def build_package(key: bytes, codes=()) -> dict:
    return {"bicim": 1, "anahtar_kimligi": key_id(key).hex(), "yuvalar": [package_slot(key, k) for k in codes]}


def open_package(package: dict, code: str) -> bytes:
    """Takes the model key out of the package with the activation code. If the code does not belong to this package: VaultError("invalid_code")."""
    raw = normalize_code(code)
    try:
        kid = bytes.fromhex(package["anahtar_kimligi"])
        slots = [y for y in package["yuvalar"] if y.get("kimlik") == _slot_id(raw)]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise VaultError("package_missing", T("the file could not be read")) from None
    for y in slots:
        try:
            iterations = int(y["tur"])
            if not 1 <= iterations <= 10_000_000:
                continue
            wrap = hashlib.pbkdf2_hmac("sha256", raw.encode("ascii"), base64.b64decode(y["tuz"]), iterations, KEY_SIZE)
            wrapped = base64.b64decode(y["sarili"])
            key = _aes_gcm(wrap, base64.b64decode(y["nonce"]), wrapped[:-_TAG], _PACKAGE_AAD + kid, wrapped[-_TAG:])
        except (VaultError, KeyError, TypeError, ValueError):
            continue
        if len(key) == KEY_SIZE and key_id(key) == kid:
            return key
    raise VaultError("invalid_code")


def activate(folder: str, data_dir: str, code: str, files=()) -> None:
    """Saves the model key on this computer with the activation code. The package is looked for first in the data folder
    (an updated package supplied later), then in the model folder (the one shipped with the program)."""
    if community_model.is_community_folder(folder):
        raise VaultError("corrupt", T("this package opens automatically with the Studio license"))
    normalize_code(code)
    packages = [p for p in (os.path.join(k, KEY_PACKAGE_FILE) for k in (data_dir, folder)) if os.path.isfile(p)]
    if not packages:
        raise VaultError("package_missing")
    key = None
    for p in packages:
        try:
            with open(p, encoding="utf-8") as f:
                key = open_package(json.load(f), code)
            break
        except (VaultError, OSError, ValueError):
            continue
    if key is None:
        raise VaultError("invalid_code")
    kid = key_id(key)
    for d in files:
        path = os.path.join(folder, d)
        if is_encrypted(path):
            with open(path + EXTENSION, "rb") as f:
                if file_key_id(f.read(_HEADER)) != kid:
                    raise VaultError("key_mismatch", T("the code belongs to the models of another version"))
    write_key(data_dir, key)
