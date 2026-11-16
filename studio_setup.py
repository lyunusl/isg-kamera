"""AugeLab Studio setup and license registration on Windows for İSG Kamera. The Studio window never opens.

The installer and the panel show this step by step:
1. downloads AugeLab's official installer and verifies its SHA-256 digest,
2. if the Microsoft Visual C++ 2015-2022 runtime (x64) is missing, downloads it from Microsoft, verifies the Microsoft
   signature and installs it,
3. installs Studio silently into the user folder; if that fails, opens the official setup window,
4. registers the license code the user got from their AugeLab account with Studio's own command, headless
   (studio verify; the code goes into an environment variable, not the command line). The code is not stored or logged.
The analysis is headless too (studio.py)."""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import certifi

import paths
from i18n import T

EXE = "AugeLab AI Studio.exe"
INSTALLER_URL = "https://studio-desktop-application.s3.eu-central-1.amazonaws.com/AugeLab%20Ai%20Studio.exe"
INSTALLER_SHA256 = "cec1e2613620c57fa51c853fc2b6148bc90f978eaf76e351b4b0b0f393681bd8"
VC_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
ACCOUNT_URL = "https://account.augelab.com/"  # the license code is shown in the AugeLab account; the panel only opens this page
_START_LOCK = threading.Lock()

# For the panel to show the setup steps (install() writes, status() reads); "what" and "error" are texts in the current
# language. stage: idle | downloading | vc | installing | window | error
STATE: dict = {"stage": "idle", "what": "", "downloaded": 0, "total": 0, "error": "", "started": 0.0}


def status() -> dict:
    d = dict(STATE)
    started = d.pop("started")
    d["elapsed"] = round(time.monotonic() - started) if started else 0
    d["studio_running"] = studio_running()
    return d


_RUNNING = {"at": 0.0, "value": False}


def studio_running() -> bool:
    """Is a Studio process open (from the task list; kept for 3 seconds because the panel polls often)?"""
    if os.name != "nt":
        return False
    if time.monotonic() - _RUNNING["at"] < 3:
        return _RUNNING["value"]
    _RUNNING["at"] = time.monotonic()
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    _RUNNING["value"] = EXE.casefold() in out.casefold()
    return _RUNNING["value"]


def vc_installed() -> bool:
    """Microsoft Visual C++ 2015-2022 runtime (x64): Studio does not start without it."""
    if os.name != "nt":
        return True
    import winreg

    for key_path in (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                     r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                if winreg.QueryValueEx(key, "Installed")[0] == 1:
                    return True
        except OSError:
            continue
    return False


def microsoft_signed(file: Path) -> bool:
    """Windows' own signature check (Authenticode): valid and signed by Microsoft Corporation?"""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:ISG_FILE; "
              "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Microsoft Corporation') { 'ok' }")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             env={**os.environ, "ISG_FILE": str(file)}).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == "ok"


def default_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "Programs/AugeLab Studio"


def _studio_exe(folder: Path) -> Path | None:
    exe = folder / EXE
    python = folder / "Python"
    package = python / "Lib" / "site-packages" / "studio" / "__init__.py"
    if exe.is_file() and (python / "pythonw.exe").is_file() and package.is_file():
        return exe
    return None


def installed_studio() -> Path | None:
    if os.name != "nt":
        return None
    import winreg

    default = _studio_exe(default_dir())
    if default is not None:
        return default
    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            try:
                                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                                if "augelab" in name.casefold() and "studio" in name.casefold():
                                    location = str(winreg.QueryValueEx(sub, "InstallLocation")[0]).strip('"')
                                    folder = Path(location)
                                    if location and folder.is_absolute():
                                        exe = _studio_exe(folder)
                                        if exe is not None:
                                            return exe
                            except OSError:
                                continue
            except OSError:
                continue
    return None


@contextlib.contextmanager
def _clean_dll_dir():
    """Studio and the installers must not inherit the packaged Python's DLL directory."""
    if not paths.FROZEN:
        yield
        return
    import ctypes
    from ctypes import wintypes

    set_dll_dir = ctypes.WinDLL("kernel32", use_last_error=True).SetDllDirectoryW
    set_dll_dir.argtypes = [wintypes.LPCWSTR]
    set_dll_dir.restype = wintypes.BOOL
    with _START_LOCK:
        if not set_dll_dir(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            set_dll_dir(sys._MEIPASS)


def _external_program(program: Path, *args: str, **options) -> subprocess.Popen:
    with _clean_dll_dir():
        return subprocess.Popen([str(program), *args], **options)


def _run_elevated(program: Path, params: str = "", show: bool = True) -> int:
    """Starts the program the way Explorer does (Windows may ask for permission) and waits for it. Returns the exit code.
    OSError (winerror 1223) if permission is denied."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):  # SHELLEXECUTEINFOW
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS: process handle to wait on
    info.lpVerb, info.lpFile, info.lpParameters = "open", str(program), params or None
    info.lpDirectory, info.nShow = str(program.parent), 1 if show else 0
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    with _clean_dll_dir():
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    if not info.hProcess:
        return 0
    k32 = ctypes.WinDLL("kernel32")
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        k32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        k32.CloseHandle(info.hProcess)


def _download(url: str, file: Path, what: str, job, sha256: str | None = None) -> bool:
    """Downloads `url` to `file` (TLS verified with certifi). If `sha256` is given the digest must match. False if stopped.
    `what` names the download in the current language."""
    partial = file.with_suffix(".part")
    tls = ssl.create_default_context()
    tls.load_verify_locations(cafile=certifi.where())
    STATE.update(stage="vc" if file.name.startswith("vc_") else "downloading", what=what, downloaded=0, total=0)
    request = urllib.request.Request(url, headers={"User-Agent": "IsgKamera"})
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=30, context=tls) as response, partial.open("wb") as out:
            STATE["total"] = int(response.headers.get("Content-Length") or 0)
            for chunk in iter(lambda: response.read(256 * 1024), b""):
                if job.stop.is_set():
                    return False
                digest.update(chunk)
                out.write(chunk)
                STATE["downloaded"] += len(chunk)
        with partial.open("rb") as f:
            corrupt = f.read(2) != b"MZ"
        if corrupt or (STATE["total"] and partial.stat().st_size != STATE["total"]):
            raise SystemExit(T("The downloaded {what} file is incomplete or corrupt. Try again.", what=what))
        if sha256 and digest.hexdigest() != sha256:
            raise SystemExit(T("Verification of the AugeLab installer failed. Download the current installer from the official "
                               "AugeLab account."))
        partial.replace(file)
        return True
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise SystemExit(T("The TLS certificate of the {what} download address could not be verified.", what=what)) from exc
        raise SystemExit(T("Could not download the {what}: check your internet connection.", what=what)) from exc
    finally:
        partial.unlink(missing_ok=True)


def _install_vc(folder: Path, job) -> bool:
    """Downloads the Visual C++ runtime from Microsoft, verifies its signature and installs it. False if stopped."""
    vc = folder / "vc_redist.x64.exe"
    print(T("Downloading the Visual C++ runtime (Microsoft)."), flush=True)
    if not _download(VC_URL, vc, T("Visual C++ component"), job):
        return False
    if not microsoft_signed(vc):
        vc.unlink(missing_ok=True)
        raise SystemExit(T("The Microsoft signature of the Visual C++ component could not be verified; it was not installed."))
    STATE.update(stage="vc", what=T("Installing the Visual C++ component"), downloaded=0, total=0)
    print(T("Installing the Visual C++ runtime. If Windows asks for permission, click Yes."), flush=True)
    try:
        code = _run_elevated(vc, "/install /passive /norestart")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1223:
            raise SystemExit(T("Windows permission was not given. Try again and answer Yes to the permission request.")) from exc
        raise
    if code not in (0, 1638, 3010) or not vc_installed():
        raise SystemExit(T("Could not install the Visual C++ component (code {code}).", code=code))
    return True


def _wait_for_install(limit_min: float, job) -> Path | None:
    """Waits until Studio's program files are in place."""
    deadline = time.monotonic() + limit_min * 60
    while time.monotonic() < deadline and not job.stop.is_set():
        studio = installed_studio()
        if studio is not None:
            return studio
        time.sleep(2)
    return installed_studio()


LICENSE_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/=-]{5,1023}")
# Same command as `studio verify <code>`; the code is passed in an environment variable so it does not appear on the
# command line (the installer does the same, with the same variable name).
VERIFY = ("import os,runpy,sys; sys.argv=['studio','verify',os.environ.pop('ISG_LICENSE_CODE','')]; "
          "runpy.run_module('studio',run_name='__main__',alter_sys=True)")


def save_license(code: str) -> None:
    """Registers the license (verification) code from the AugeLab account on this computer with Studio's own command,
    without opening the Studio window. The code is given only to Studio: not stored, not written to logs or error texts."""
    code = (code or "").strip()
    if not LICENSE_CODE_RE.fullmatch(code):
        raise SystemExit(T("The license code looks invalid. Paste the code from your AugeLab account exactly as it is, without spaces."))
    studio = installed_studio()
    if studio is None:
        raise SystemExit(T("AugeLab Studio is not installed. Install Studio first."))
    py = studio.parent / "Python" / "python.exe"
    try:
        with _clean_dll_dir():
            r = subprocess.run([str(py), "-c", VERIFY], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, cwd=str(studio.parent), stdin=subprocess.DEVNULL,
                               env=dict(os.environ, ISG_LICENSE_CODE=code), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise SystemExit(T("AugeLab Studio did not verify the license code in time. Check your internet connection and try again.")) from None
    except OSError as e:
        raise SystemExit(T("Could not run AugeLab Studio: {error}", error=e)) from None
    if r.returncode != 0:  # Studio's output is not shown: it may contain part of the code
        raise SystemExit(T("Could not save the license code. Get the code again from your AugeLab account and try again."))


def install(job) -> dict:
    try:
        return _install(job)
    except SystemExit as e:
        STATE.update(stage="error", error=str(e.code or ""), started=0.0)
        raise
    except Exception as e:
        STATE.update(stage="error", error=T("Setup stopped: {error}", error=e), started=0.0)
        raise


def _install(job) -> dict:
    STATE.update(stage="idle", what="", downloaded=0, total=0, error="", started=0.0)
    arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
    if os.name != "nt" or arch.upper() != "AMD64":
        raise SystemExit(T("This setup is for Windows x64."))
    existing = installed_studio()
    if existing is not None:
        print(T("AugeLab Studio is already installed."), flush=True)
        return {"installed": True}

    target = default_dir()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(T("The AugeLab Studio folder is not empty, but the Studio installation is incomplete. Repair the "
                           "existing installation with the official installer."))
    folder = paths.DATA / "setup"
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"AugeLab-Studio-{INSTALLER_SHA256[:12]}.exe"
    verified = False
    if file.is_file():
        with file.open("rb") as stream:
            verified = hashlib.file_digest(stream, "sha256").hexdigest() == INSTALLER_SHA256
    if not verified:
        print(T("Downloading the official AugeLab Studio installer."), flush=True)
        if not _download(INSTALLER_URL, file, T("AugeLab Studio installer"), job, INSTALLER_SHA256):
            return {}
    if job.stop.is_set():
        return {}
    if not vc_installed() and not _install_vc(folder, job):
        return {}
    if job.stop.is_set():
        return {}
    # Silent setup first (Visual C++ is in place now); if that fails, the official setup window.
    STATE.update(stage="installing", what=T("Installing AugeLab Studio"), downloaded=0, total=0, started=time.monotonic())
    print(T("Installing AugeLab Studio. This can take a few minutes."), flush=True)
    try:
        code = _run_elevated(file, f'--silent --install-dir "{target}" --start-menu-shortcut', show=False)
    except OSError as exc:
        code = getattr(exc, "winerror", None) or -1
    studio = _wait_for_install(20, job) if code == 0 else installed_studio()
    if studio is None and not job.stop.is_set():
        STATE.update(stage="window", what=T("Installing AugeLab Studio in the setup window"), started=time.monotonic())
        print(T("Silent setup did not complete (code {code}). The official setup window has opened; finish the steps in that window.",
                code=code), flush=True)
        with _external_program(file, cwd=folder) as installer:
            code = installer.wait()
        studio = _wait_for_install(1, job)
    if job.stop.is_set():
        return {}
    if studio is None:
        raise SystemExit(T("Studio setup did not complete or Studio was not found (code {code}). Check the installation and "
                           "refresh the panel.", code=code))
    time.sleep(3)  # let the installer release its files
    _close_studio_window()
    STATE.update(stage="idle", what="", started=0.0)
    print(T("AugeLab Studio is installed. Next step: enter the license code from your AugeLab account."), flush=True)
    return {"installed": True}


def _close_studio_window() -> None:
    """If the official installer opens Studio by itself when it finishes, closes it: İSG Kamera uses Studio headless."""
    for _ in range(10):
        if studio_running():
            subprocess.run(["taskkill", "/IM", EXE, "/T"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        time.sleep(1)
        _RUNNING["at"] = 0.0
