"""Local model package of İSG Kamera that opens automatically with the Studio license. User-visible messages use T().

The marker file name, the file extension, the magic bytes, the DLL name pattern and the DLL functions are part of the
shipped package format and must not change.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import json
import os
from pathlib import Path
import re

from .i18n import T, N_
from .model_license import LicenseError

MARKER_FILE = "community-paket.json"
EXTENSION = ".isgm"
MAGIC = b"ISGMOD\x01\x00"
HEADER_SIZE, TAG_SIZE = 52, 16
MAX_SIZE = 128 * 1024 * 1024 + HEADER_SIZE + TAG_SIZE

_MESSAGES = {
    1: N_("The model file format is not valid. Download the İSG Kamera installer again."),
    2: N_("The model file could not be verified. Download the İSG Kamera installer again."),
    3: N_("Could not verify the AugeLab Studio license status. Restart the analysis through Studio."),
    4: N_("No active AugeLab Studio license that allows headless analysis was found."),
}


class StudioLicenseReadError(LicenseError):
    """Studio could not return the license status."""


def is_community_folder(folder) -> bool:
    return (Path(folder) / MARKER_FILE).is_file()


def is_community_model(path) -> bool:
    return is_community_folder(Path(path).parent) or Path(str(path) + EXTENSION).is_file()


def _package_id(folder: Path) -> bytes:
    try:
        with (folder / MARKER_FILE).open("rb") as f:
            data = json.loads(f.read(512))
        package_id = data["id"]
        if not isinstance(package_id, str) or not re.fullmatch("[a-f0-9]{32}", package_id):
            raise ValueError
        return bytes.fromhex(package_id)
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise LicenseError(T(_MESSAGES[1])) from e


@lru_cache(maxsize=8)
def _library(path: Path):
    if os.name != "nt":
        raise LicenseError(T("This model package requires Windows x64."))
    try:
        # Search only the module folder and System32 for dependencies.
        dll = ctypes.CDLL(str(path), winmode=0x100 | 0x800)
        dll.layout_stride.argtypes = ()
        dll.layout_stride.restype = ctypes.c_int
        dll.frame_color.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t)
        dll.frame_color.restype = ctypes.c_int
    except (OSError, AttributeError) as e:
        raise LicenseError(T("Could not open the model component. Download the İSG Kamera installer again.")) from e
    return dll


def _dll_path(folder: Path, package_id: bytes) -> Path:
    return (folder / f"goruntu-{package_id.hex()}.dll").absolute()


def _check(code: int) -> None:
    if code == 3:
        raise StudioLicenseReadError(T(_MESSAGES[code]))
    if code:
        raise LicenseError(T(_MESSAGES.get(code, _MESSAGES[1])))


def authorize(dll_path: Path) -> None:
    _check(_library(dll_path).layout_stride())


def status(folder, files) -> dict:
    result = {"encrypted": True, "community": True}
    folder = Path(folder)
    try:
        package_id = _package_id(folder)
        for name in files:
            with (folder / (name + EXTENSION)).open("rb") as f:
                if f.read(24) != MAGIC + package_id:
                    raise LicenseError(T(_MESSAGES[1]))
        if not _dll_path(folder, package_id).is_file():
            raise LicenseError(T(_MESSAGES[1]))
    except (OSError, LicenseError) as e:
        return dict(result, status="corrupt",
                    message=str(e) if isinstance(e, LicenseError) else T(_MESSAGES[1]))
    # The panel process is not Studio. The license is verified by the DLL when the model is opened.
    return dict(result, status="verified_in_studio_run")


def open_model(path) -> tuple[bytes, Path]:
    """The DLL checks the authorization itself; decryption has no target other than writable memory."""
    path = Path(path)
    package_id = _package_id(path.parent)
    dll_path = _dll_path(path.parent, package_id)
    try:
        name = path.name.encode("ascii")
        with Path(str(path) + EXTENSION).open("rb") as f:
            size = os.fstat(f.fileno()).st_size
            if not HEADER_SIZE + TAG_SIZE < size <= MAX_SIZE:
                raise LicenseError(T(_MESSAGES[1]))
            data = bytearray(size)
            if f.readinto(data) != size or f.read(1) or data[:24] != MAGIC + package_id:
                raise LicenseError(T(_MESSAGES[1]))
    except (OSError, UnicodeError) as e:
        raise LicenseError(T(_MESSAGES[1])) from e
    buf = (ctypes.c_ubyte * len(data)).from_buffer(data)
    address = ctypes.addressof(buf)
    try:
        _check(_library(dll_path).frame_color(address, size, name, len(name)))
        # ORT's Python API requires bytes. Its in-memory copy lives only with the inference session.
        return ctypes.string_at(address + HEADER_SIZE, size - HEADER_SIZE - TAG_SIZE), dll_path
    finally:
        ctypes.memset(address, 0, size)
