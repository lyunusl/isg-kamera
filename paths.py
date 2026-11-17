"""Folders and the files shipped with the program.

BUNDLE: files shipped with the program (web pages, models, samples). In the exe, the temporary unpack folder.
DATA:   the user's data (settings, recordings, database, clips). In the exe %LOCALAPPDATA%\\IsgKamera, when running
        from source the data/ folder in the project. Can be changed with the ISG_DATA_DIR environment variable (the
        v0.1.0 name ISG_KAMERA_VERI is still read). Data folders of v0.1.0 are converted by migration.py at start.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from i18n import N_, T

VERSION = "0.1.0"
APP_NAME = "IsgKamera"
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_ENV_VARS = ("ISG_DATA_DIR", "ISG_KAMERA_VERI")  # new name first; the v0.1.0 name is a fallback


def _data_override() -> str | None:
    for name in DATA_ENV_VARS:
        if os.environ.get(name):
            return os.environ[name]
    return None


def _data_dir() -> Path:
    override = _data_override()
    if override:
        return Path(override).resolve()
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    return Path(__file__).resolve().parent / "data"


DATA = _data_dir()
# A source checkout of v0.1.0 kept its data in veri/; migration.py renames it to data/ (None in the exe or with an override).
LEGACY_SOURCE_DATA = None if FROZEN or _data_override() else Path(__file__).resolve().parent / "veri"
CAMERAS = DATA / "cameras"    # per camera <code>.json and the zone-drawing snapshot _frame_<code>.jpg
VIDEOS = DATA / "videos"      # raw recordings (deleted after analysis)
OUTPUT = DATA / "output"      # clips, last snapshots (faces masked)
CACHE = DATA / "cache"
QUEUE = DATA / "queue"        # records the engine in AugeLab Studio leaves for the panel (event_queue.py)
DB = DATA / "isg.db"
SETTINGS_FILE = DATA / "settings.json"
FORMAT_FILE = DATA / "format.json"  # data-folder format marker, written last by migration.py

WEB = BUNDLE / "web"
SAMPLES = BUNDLE / "samples"
ENGINE = BUNDLE / "engine"                              # engine placed in AugeLab Studio (isg_core, models, deps)
MODEL_OBJECT = ENGINE / "models" / "isg-nesne-1280.onnx"       # person, hard hat, vest, gloves, glasses, safety shoes, forklift
MODEL_ADDON = ENGINE / "models" / "isg-ek-1280.onnx"           # optional: more PPE (mask, face shield, hearing protection...) and objects

# Display and zone names of the sample cameras: one table shared with the engine (isg_core.i18n).
from isg_core.i18n import SAMPLE_NAMES, sample_name  # noqa: E402,F401  (i18n above puts engine/ on sys.path)


def sample_codes() -> list[str]:
    d = SAMPLES / "cameras"
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def hide(text) -> str:
    """Shortens the user folder in a text shown in the panel or written to a log (the Windows user name does not appear)."""
    text = str(text)
    for env, short in (("LOCALAPPDATA", "%LOCALAPPDATA%"), ("APPDATA", "%APPDATA%"), ("USERPROFILE", "%USERPROFILE%")):
        folder = os.environ.get(env)
        if folder:
            for form in {folder, folder.replace("\\", "/")}:
                text = text.replace(form, short)
    return text


def ffmpeg() -> str:
    """The ffmpeg shipped with the program (imageio-ffmpeg package); else the one on PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit(T("ffmpeg not found."))
    return path


_JOB_OBJECT = None


def bind_child_process(process) -> None:
    """When the program closes (even if its window is closed), this child process closes too: Windows job object."""
    global _JOB_OBJECT
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
        if _JOB_OBJECT is None:
            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            h = k32.CreateJobObjectW(None, None)
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not h or not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _JOB_OBJECT = h
        k32.AssignProcessToJobObject(_JOB_OBJECT, int(process._handle))
    except Exception:
        pass


def prepare() -> bool:
    """Sets up the data folder and copies the sample videos and settings (if missing). Returns True on first setup.

    A data folder of v0.1.0 (or a source checkout's veri/ folder) is converted first (migration.py)."""
    import migration

    first = not DATA.exists() and not (LEGACY_SOURCE_DATA is not None and LEGACY_SOURCE_DATA.exists())
    migration.migrate()
    for d in (CAMERAS, VIDEOS, OUTPUT, CACHE):
        d.mkdir(parents=True, exist_ok=True)
    for code in sample_codes():
        for source, target in ((SAMPLES / "videos" / f"{code}.mp4", VIDEOS / f"{code}.mp4"),
                               (SAMPLES / "cameras" / f"{code}.json", CAMERAS / f"{code}.json")):
            if source.exists() and not target.exists():
                shutil.copy2(source, target)
    return first
