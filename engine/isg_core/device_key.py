"""Windows CNG: the model license key that belongs to this Windows user and computer. User-visible messages use T()."""
from __future__ import annotations

import ctypes as c
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import os
import struct
import threading

from .i18n import T

V, U, W, B = c.c_void_p, c.c_ulong, c.c_wchar_p, c.c_char_p
SILENT = 0x40
OAEP = 0x04
PSS = 0x08
KEY_NAME = "IsgKamera.ModelLisans.RSA.v1"  # persisted CNG key name: must not change (issued licenses are bound to this key)
PROVIDERS = ("Microsoft Platform Crypto Provider", "Microsoft Software Key Storage Provider")
_CREATE_LOCK = threading.Lock()


class DeviceKeyError(Exception):
    pass


class _OAEP(c.Structure):
    _fields_ = [("algorithm", W), ("label", V), ("label_size", U)]


class _PSS(c.Structure):
    _fields_ = [("algorithm", W), ("salt_size", U)]


@lru_cache(maxsize=1)
def _libraries():
    if os.name != "nt":
        raise DeviceKeyError(T("Windows is required."))
    n, b = c.WinDLL("ncrypt"), c.WinDLL("bcrypt")
    p = c.POINTER(V)
    counter = c.POINTER(U)
    definitions = (
        (n, "NCryptOpenStorageProvider", [p, W, U]),
        (n, "NCryptOpenKey", [V, p, W, U, U]),
        (n, "NCryptCreatePersistedKey", [V, p, W, W, U, U]),
        (n, "NCryptSetProperty", [V, W, V, U, U]),
        (n, "NCryptFinalizeKey", [V, U]),
        (n, "NCryptFreeObject", [V]),
        (n, "NCryptExportKey", [V, V, W, V, V, U, counter, U]),
        (n, "NCryptDecrypt", [V, B, U, V, V, U, counter, U]),
        (b, "BCryptOpenAlgorithmProvider", [p, W, W, U]),
        (b, "BCryptImportKeyPair", [V, V, W, p, B, U, U]),
        (b, "BCryptVerifySignature", [V, V, B, U, B, U, U]),
        (b, "BCryptDestroyKey", [V]),
        (b, "BCryptCloseAlgorithmProvider", [V, U]),
    )
    for library, name, args in definitions:
        f = getattr(library, name)
        f.argtypes, f.restype = args, c.c_long
    return n, b


def _check(code):
    if code:
        raise DeviceKeyError(T("Windows key operation failed (0x{code:08X}).", code=code & 0xffffffff))


def public_components(blob: bytes) -> tuple[int, int]:
    """(e, n) from a CNG RSAPUBLICBLOB. Only the expected RSA public keys."""
    if len(blob) < 24:
        raise ValueError(T("The RSA key is incomplete."))
    magic, bits, e_len, n_len, p, q = struct.unpack_from("<6I", blob)
    if (magic != 0x31415352 or bits not in (2048, 3072) or p or q
            or e_len != 3 or n_len != bits // 8 or len(blob) != 24 + e_len + n_len):
        raise ValueError(T("The RSA key is not valid."))
    e = int.from_bytes(blob[24:27], "big")
    n = int.from_bytes(blob[27:], "big")
    if e != 65537 or n.bit_length() != bits or not n % 2:
        raise ValueError(T("The RSA key is not valid."))
    return e, n


def public_blob(e: int, n: int) -> bytes:
    bits = n.bit_length()
    blob = struct.pack("<6I", 0x31415352, bits, 3, bits // 8, 0, 0) + e.to_bytes(3, "big") + n.to_bytes(bits // 8, "big")
    public_components(blob)
    return blob


def _existing(n, provider, key):
    for name in PROVIDERS:
        if n.NCryptOpenStorageProvider(c.byref(provider), name, 0):
            continue
        if not n.NCryptOpenKey(provider, c.byref(key), KEY_NAME, 0, SILENT):
            return True
        n.NCryptFreeObject(provider)
        provider.value = None
    return False


def _create(n, provider, key):
    for name in PROVIDERS:
        if n.NCryptOpenStorageProvider(c.byref(provider), name, 0):
            continue
        code = n.NCryptCreatePersistedKey(provider, c.byref(key), "RSA", KEY_NAME, 0, 0)
        if code & 0xffffffff == 0x8009000F:  # Another process created the same name.
            code = n.NCryptOpenKey(provider, c.byref(key), KEY_NAME, 0, SILENT)
            if not code:
                return
        elif not code:
            try:
                for prop, value in (("Length", 2048), ("Key Usage", 1), ("Export Policy", 0)):
                    number = U(value)
                    _check(n.NCryptSetProperty(key, prop, c.byref(number), c.sizeof(number), 0))
                _check(n.NCryptFinalizeKey(key, SILENT))
                return
            except DeviceKeyError:
                n.NCryptFreeObject(key)
                key.value = None
        n.NCryptFreeObject(provider)
        provider.value = None
    raise DeviceKeyError(T("Could not create a license key on this computer."))


@contextmanager
def _device(create=False):
    n, _ = _libraries()
    provider, key = V(), V()
    try:
        with _CREATE_LOCK:
            if not _existing(n, provider, key):
                if not create:
                    raise DeviceKeyError(T("The device key of this Windows user was not found."))
                _create(n, provider, key)
        yield n, key
    finally:
        if key.value:
            n.NCryptFreeObject(key)
        if provider.value:
            n.NCryptFreeObject(provider)


def _public(n, key):
    size = U()
    _check(n.NCryptExportKey(key, None, "RSAPUBLICBLOB", None, None, 0, c.byref(size), 0))
    if not 24 <= size.value <= 411:
        raise DeviceKeyError(T("The device key is not the expected size."))
    output = c.create_string_buffer(size.value)
    _check(n.NCryptExportKey(key, None, "RSAPUBLICBLOB", None, output, len(output), c.byref(size), 0))
    blob = output.raw[:size.value]
    public_components(blob)
    return blob


def public_key(*, create=False) -> bytes:
    with _device(create) as (n, key):
        return _public(n, key)


def unwrap_key(wrapped: bytes, device_digest: str) -> bytearray:
    with _device() as (n, key):
        if hashlib.sha256(_public(n, key)).hexdigest() != device_digest:
            raise DeviceKeyError(T("The license does not belong to this computer."))
        padding = _OAEP("SHA256", None, 0)
        size = U()
        output = (c.c_ubyte * len(wrapped))()
        try:
            _check(n.NCryptDecrypt(key, wrapped, len(wrapped), c.byref(padding),
                                   output, len(output), c.byref(size), OAEP | SILENT))
            if size.value != 32:
                raise DeviceKeyError(T("The model key is not valid."))
            return bytearray(memoryview(output).cast("B")[:size.value])
        finally:
            c.memset(output, 0, c.sizeof(output))


def verify_signature(public: bytes, data: bytes, signature: bytes) -> bool:
    _, b = _libraries()
    provider, key = V(), V()
    _check(b.BCryptOpenAlgorithmProvider(c.byref(provider), "RSA", None, 0))
    try:
        _check(b.BCryptImportKeyPair(provider, None, "RSAPUBLICBLOB", c.byref(key), public, len(public), 0))
        padding = _PSS("SHA256", 32)
        digest = hashlib.sha256(data).digest()
        return b.BCryptVerifySignature(key, c.byref(padding), digest, len(digest), signature, len(signature), PSS) == 0
    finally:
        if key.value:
            b.BCryptDestroyKey(key)
        b.BCryptCloseAlgorithmProvider(provider, 0)
