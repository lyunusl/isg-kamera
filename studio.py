"""AugeLab Studio bridge: headless runs.

İSG Kamera does not contain Studio's code and never opens the Studio window. With the Python of the Studio the user
installed, it starts a hidden process for each camera:

    "<Studio>/Python/python.exe" -m studio run isg-kamera.pmod --json

isg-kamera.pmod is the ready-made scene shipped with the program (Studio saves scene files encrypted, so it was built
and saved once in Studio: installer/build_scene.py):

    ISG Camera Code ─────────────────────┬────────────────┐
    ISG Video ┬► ISG Detection ─► ISG Pose ─► ISG Rules ─► events, clips, measurements (to the panel's queue)
              └──────────────────────────────► (image, time, done)

The scene is the same for every camera; it learns which camera to process from environment variables (isg_core.blocks:
ISG_CAMERA "code#run", ISG_CAMERA_URL live stream address, ISG_STOP_FILE stop file, ISG_LANG language). The panel first
writes its blocks to Studio's custom block folder and puts its engine (isg_core, models, onnxruntime) next to it; no
package is installed into Studio's environment.

For development:  python studio.py status | probe | run <camera> [seconds]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import i18n
import paths
import studio_blocks
from i18n import T

BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ENGINE_SOURCE = BUNDLE / "engine"
SCENE_SOURCE = BUNDLE / "installer" / "scene" / "isg-kamera.pmod"
STATUS_FILE = paths.DATA / "studio.json"
LICENSE_MARKER = paths.DATA / "studio-license.txt"  # left by the installer once it saved the license code in Studio (not the code itself)
_LEGACY_LICENSE_MARKER = paths.DATA / "studio-lisans.txt"  # name written by the v0.1.0 installer
RUN_DIR = paths.DATA / "studio"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
LIVE_TYPES = ("rtsp", "onvif", "http")

ENGINE_PARTS = ("isg_core", "models", "deps")  # engine subfolders mirrored into marketplace/isg_engine
# Left in marketplace/isg_engine and custom_blocks by v0.1.0; removed during the normal mirroring.
_LEGACY_ENGINE_DIRS = ("isg_motor", "modeller")
_LEGACY_ENGINE_FILES = ("isg_motor.surum", "modeller.surum", "deps.surum", "surum.txt", "ayar.json")
_LEGACY_BLOCK_OP_CODES = ("IsgAlgila", "IsgIskelet", "IsgKurallar")


class StudioError(RuntimeError):
    """Studio rejected a request or the scene did not run."""


class StudioMissing(StudioError):
    """AugeLab Studio is not installed: expected, not an error."""


# ---------------------------------------------------------------- where Studio lives


def install_dir() -> Path | None:
    import studio_setup

    exe = studio_setup.installed_studio()
    return exe.parent if exe else None


def studio_python() -> Path:
    """Studio's own Python interpreter (headless runs use it)."""
    folder = install_dir()
    if folder is None:
        raise StudioMissing(T("AugeLab Studio is not installed. Install it with the setup wizard."))
    py = folder / "Python" / "python.exe"
    if not py.is_file():
        raise StudioError(T("AugeLab Studio's Python was not found ({path}). Repair Studio with the official installer.", path=py))
    return py


def marketplace() -> Path:
    """Where Studio's custom block folder is (Studio keeps it under %APPDATA% for each user)."""
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "AugeLab Studio" / "marketplace"


def version() -> str | None:
    """Version of the installed Studio (from the package metadata; Studio is not run)."""
    folder = install_dir()
    if folder is None:
        return None
    for info in sorted((folder / "Python" / "Lib" / "site-packages").glob("studio-*.dist-info")):
        return info.name[len("studio-"):-len(".dist-info")]
    return None


# ---------------------------------------------------------------- engine folder


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def _file_digest(p: Path) -> bytes:
    """SHA-256 digest of the whole file content."""
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").digest()


def _fingerprint(folder: Path) -> str:
    """Content signature of a folder: file names and content digests. File dates are ignored: the exe unpacks its files
    into a temporary folder with new dates on every start; with dates in the signature the engine would be copied again
    on every start."""
    h = hashlib.sha256()
    files = _files(folder)
    for p in files:
        h.update(p.relative_to(folder).as_posix().encode("utf-8") + b"\0" + _file_digest(p))
    return f"{len(files)}:{h.hexdigest()[:40]}"


def _sync(source: Path, target: Path) -> int:
    """Makes the target folder identical to the source; touches only files whose content differs. Returns the number of
    files copied.

    Unchanged files are not rewritten: libraries Studio has loaded (onnxruntime) are locked while it runs and cannot be
    overwritten. If a file that really changed is locked, a clear error is raised. Files no longer in the source are
    deleted; an old file that cannot be deleted (in use) stays."""
    target.mkdir(parents=True, exist_ok=True)
    sources = {p.relative_to(source): p for p in _files(source)}
    copied = 0
    for rel, p in sources.items():
        t = target / rel
        try:
            same = t.is_file() and t.stat().st_size == p.stat().st_size and _file_digest(t) == _file_digest(p)
        except OSError:
            same = False  # target could not be read: try rewriting it
        if same:
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_name(t.name + ".new")
        try:
            shutil.copyfile(p, tmp)
            os.replace(tmp, t)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StudioError(T("Could not update an engine file ({file}): the file may be in use. Stop running analyses "
                                "and try again.", file=rel.as_posix())) from None
        copied += 1
    for t in _files(target):
        if t.relative_to(target) not in sources:
            try:
                t.unlink()
            except OSError:
                pass
    return copied


def _remove_legacy_engine(target: Path) -> None:
    """Deletes what the v0.1.0 panel left in the engine folder (old package and model folders, marker files). A file
    Studio still holds stays and is tried again on the next start."""
    for name in _LEGACY_ENGINE_DIRS:
        folder = target / name
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    for name in _LEGACY_ENGINE_FILES:
        try:
            (target / name).unlink(missing_ok=True)
        except OSError:
            pass


def deploy_engine(target: Path) -> Path:
    """Copies the engine (isg_core, models, deps) next to Studio's custom block folder; leaves it alone if unchanged."""
    if not (ENGINE_SOURCE / "isg_core").is_dir():
        raise StudioError(T("Engine source not found: {path}", path=ENGINE_SOURCE))
    target.mkdir(parents=True, exist_ok=True)
    signatures = []
    for name in ENGINE_PARTS:
        source = ENGINE_SOURCE / name
        if not source.is_dir():
            continue
        signature = _fingerprint(source)
        signatures.append(signature)
        marker = target / f"{name}.version"
        if (target / name).is_dir() and marker.exists() and marker.read_text(encoding="ascii") == signature:
            continue
        _sync(source, target / name)
        marker.write_text(signature, encoding="ascii")
    version_file = target / "version.txt"  # written only when changed: the blocks reload the engine by this file's date
    if not version_file.exists() or version_file.read_text(encoding="ascii") != "|".join(signatures):
        version_file.write_text("|".join(signatures), encoding="ascii")
    (target / "engine.json").write_text(json.dumps({"data_dir": str(paths.DATA)}), encoding="utf-8")
    _remove_legacy_engine(target)
    return target


def install_blocks() -> Path:
    """Writes the İSG blocks to Studio's custom block folder (only if changed) and places the engine next to it.
    The files are identical to what Studio's node.custom.create produces (studio_blocks.block_source). Block files of
    v0.1.0 whose opCode was renamed are deleted (never other blocks). Returns the engine folder."""
    blocks = marketplace() / "custom_blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    current = set()
    for definition in studio_blocks.DEFINITIONS:
        current.add(definition["opCode"])
        target = blocks / f"{definition['opCode']}.py"
        text = studio_blocks.block_source(definition).replace("\n", "\r\n").encode("utf-8")
        try:
            if target.is_file() and target.read_bytes() == text:
                continue
        except OSError:
            pass
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(text)
        os.replace(tmp, target)
    for op_code in _LEGACY_BLOCK_OP_CODES:
        if op_code not in current:
            try:
                (blocks / f"{op_code}.py").unlink(missing_ok=True)
            except OSError:
                pass
    return deploy_engine(marketplace() / studio_blocks.ENGINE_FOLDER)


def scene_template() -> Path:
    """Copy of the ready-made scene in the data folder (the exe unpacks its files into a different temporary folder on every start)."""
    if not SCENE_SOURCE.is_file():
        raise StudioError(T("The ready-made İSG scene was not found: {path}", path=SCENE_SOURCE))
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    target = RUN_DIR / SCENE_SOURCE.name
    if not target.is_file() or _file_digest(target) != _file_digest(SCENE_SOURCE):
        shutil.copyfile(SCENE_SOURCE, target)
    return target


# ---------------------------------------------------------------- headless runs


class Run:
    """The hidden `studio run` process of one camera."""

    def __init__(self, code: str, run_id: str, process: subprocess.Popen, stop_file: Path, log: Path):
        self.code, self.run_id, self.process, self.stop_file, self.log = code, run_id, process, stop_file, log
        self.state = "starting"
        self.result: dict | None = None
        self._read_done = threading.Event()
        threading.Thread(target=self._read_events, name=f"studio-{code}", daemon=True).start()

    def _read_events(self) -> None:
        """Reads Studio's --json events (lifecycle / result)."""
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    # With no local license Studio's console prompt can precede
                    # the JSON result on the same line, even with stdin closed.
                    start = line.find('{"type":')
                    if start < 0:
                        continue
                    try:
                        event = json.loads(line[start:])
                    except ValueError:
                        continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "lifecycle":
                    self.state = event.get("state") or self.state
                elif event.get("type") == "result":
                    self.result = event
        finally:
            self._read_done.set()

    def is_running(self) -> bool:
        return self.process.poll() is None

    def problem(self) -> str | None:
        """If the process ended on its own, the reason (license, missing block, crash...); None while it runs."""
        if self.is_running():
            return None
        self._read_done.wait(2)
        r = self.result or {}
        if r.get("success"):
            return None
        reason = r.get("error") or r.get("failure_type") or T("exit code {code}", code=self.process.returncode)
        return T("AugeLab Studio could not run the scene ({reason}). Check your Studio license in the setup wizard; "
                 "details: {log}", reason=reason, log=self.log)

    def finish(self, wait_s: float = 3.0) -> None:
        """First ends the stream (open events leave a closing record), then closes the process."""
        try:
            self.stop_file.touch()
        except OSError:
            pass
        deadline = time.monotonic() + wait_s
        while self.is_running() and time.monotonic() < deadline:
            time.sleep(0.2)
        if self.is_running():
            # in continuous mode Studio does not end on its own: the process tree (supervisor and worker) is closed
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError:
            pass


RUNS: dict[str, Run] = {}
_LOCK = threading.Lock()


def start(code: str, run_id: str, url: str = "", live: bool = False, step: int | None = None, extra_env: dict | None = None) -> Run:
    """Starts the camera's scene headless. `live`: if the process crashes, Studio restarts it."""
    py = studio_python()
    scene = scene_template()
    install_blocks()
    with _LOCK:
        old = RUNS.pop(code, None)
    if old is not None:
        old.finish(0)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stop_file = RUN_DIR / f"{code}.stop"
    stop_file.unlink(missing_ok=True)
    log = RUN_DIR / f"{code}.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ISG_", "PYTHON", "_MEI", "TCL_", "TK_"))}
    env.update(ISG_CAMERA=f"{code}#{run_id}", ISG_STOP_FILE=str(stop_file), ISG_LANG=i18n.language(), PYTHONIOENCODING="utf-8")
    if url:
        env["ISG_CAMERA_URL"] = url  # may contain a password: only in the process environment, never written to a file
    env.update(extra_env or {})
    cmd = [str(py), "-m", "studio", "run", str(scene), "--json", "--verbosity", "30"]
    if live:
        cmd += ["--on-fail", "restart", "--max-restarts", "1000", "--restart-delay", "5"]
    if step:
        cmd += ["--step", str(int(step))]
    import studio_setup

    with log.open("ab") as stderr, studio_setup._clean_dll_dir():  # Studio must not inherit the packaged Python's DLL directory
        process = subprocess.Popen(cmd, cwd=str(RUN_DIR), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    r = Run(code, run_id, process, stop_file, log)
    with _LOCK:
        RUNS[code] = r
    return r


def stop_all() -> None:
    """When the program closes: ends all running runs."""
    with _LOCK:
        runs = list(RUNS.values())
        RUNS.clear()
    for r in runs:
        r.finish()


# ---------------------------------------------------------------- status and test


CHECK_VERSION = 3  # bumped when the check changes: a "ready" record from older versions does not count (3: English status keys)
PROBE_CODE = "_probe"


def _saved_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_status(**values) -> None:
    d = _saved_status()
    d.update(values)
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(d), encoding="utf-8")


def _probe_run(timeout_s: float, extra_env: dict | None = None) -> tuple["Run", str | None, dict]:
    """Runs the test scene briefly: the engine opens the models, processes one frame and writes the result to a file.
    Returns (run, problem or None, the engine's result)."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RUN_DIR / "probe-result.json"
    result_path.unlink(missing_ok=True)
    r = start(PROBE_CODE, secrets.token_hex(4), step=3, extra_env={"ISG_PROBE_RESULT": str(result_path), **(extra_env or {})})
    try:
        r.process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        r.finish(0)
        raise StudioError(T("AugeLab Studio did not finish the test scene in time.")) from None
    finally:
        with _LOCK:
            RUNS.pop(PROBE_CODE, None)
    problem, model = r.problem(), {}
    if not problem:
        try:
            model = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            model = {"ok": False, "error": T("the engine left no result (the İSG blocks did not run in Studio)")}
        if not model.get("ok"):
            error = str(model.get("error") or model.get("hata") or T("unknown reason"))
            # the engine marks license errors with "lisans: " (older engines) or "license: "
            problem = (T("Enter the license code in the setup wizard.") if error.startswith(("lisans", "license"))
                       else T("Could not open the models ({error}).", error=error))
    return r, problem, model


def mask_faces(url: str, output: Path, timeout_s: float = 240.0) -> int:
    """Masks the faces in Studio on the frame the panel serves at `url` (127.0.0.1, one-time) and writes it to `output`;
    encrypted models are not opened in the panel. Returns the number of masked regions."""
    output.unlink(missing_ok=True)
    _, problem, model = _probe_run(timeout_s, {"ISG_FACE_FRAME_URL": url, "ISG_FACE_OUTPUT": str(output)})
    if problem:
        raise StudioError(problem)
    if not output.is_file():
        raise StudioError(T("Could not mask the faces."))
    return int(model.get("faces") or model.get("yuz") or 0)


def probe(timeout_s: float = 240.0) -> dict:
    """Briefly tests that Studio can run headless (installed, blocks load) and that the models open (including the
    Studio license for encrypted packages): the engine runs the models on a black frame and writes the result to a file."""
    r, problem, _ = _probe_run(timeout_s)
    if problem:
        save_status(ready=False, tested_at=time.time(), error=problem[:300], check_version=CHECK_VERSION)
        raise StudioError(problem)
    save_status(ready=True, tested_at=time.time(), version=version(), error=None, check_version=CHECK_VERSION)
    return {"ready": True, "duration_s": round(((r.result or {}).get("duration_ms") or 0) / 1000, 1)}


async def status() -> dict:
    """Studio status shown by the panel. Does not connect to Studio: is it installed, was the scene tested, is a camera running."""
    if install_dir() is None:
        raise StudioMissing(T("AugeLab Studio is not installed."))
    studio_python()
    s = _saved_status()
    ready = s.get("ready") if s.get("check_version") == CHECK_VERSION else None  # old record: checked again
    return {"version": version(), "headless": True, "ready": ready, "tested_at": s.get("tested_at"), "error": s.get("error"),
            "license_saved": bool(s.get("license_saved_at") or LICENSE_MARKER.is_file() or _LEGACY_LICENSE_MARKER.is_file()),
            "engine_running": any(r.is_running() for r in list(RUNS.values()))}


# ---------------------------------------------------------------- operations used by the panel (same flow as the old MCP bridge)


async def prepare_scene(code: str, config: dict) -> dict:
    """Prepares the camera's run: are Studio and the ready-made scene in place, are the blocks and engine up to date."""
    kind = (config.get("source") or {}).get("type", "rtsp")
    if kind == "usb":
        raise StudioError(T("USB cameras are not supported in headless mode yet. Add the camera as an IP camera or a video file."))
    studio_python()
    scene_template()
    install_blocks()
    return {"code": code, "type": kind, "url": "", "run": None}


async def new_run(info: dict, code: str) -> str:
    """Gives a new run id: the records the engine leaves in the queue carry this id."""
    info["run"] = secrets.token_hex(4)
    return info["run"]


async def set_camera_url(info: dict, url: str) -> None:
    """The live stream address is passed in the run's environment (not written to a file or the scene)."""
    info["url"] = url


async def run(info: dict, duration_ms: int | None = None) -> dict:
    """Starts the scene headless. If `duration_ms` is given, ends the stream after that long."""
    live = info["type"] in LIVE_TYPES
    r = start(info["code"], info["run"] or secrets.token_hex(4), info.get("url", ""), live=live)
    info["process"] = r
    if duration_ms:
        def stop_later():
            time.sleep(duration_ms / 1000)
            if r.is_running():
                r.stop_file.touch()
        threading.Thread(target=stop_later, daemon=True, name=f"studio-timer-{info['code']}").start()
    return {"pid": r.process.pid}


async def stop(info: dict, verify_s: float = 20.0) -> None:
    """Ends the stream and closes the process; StudioError if closing cannot be confirmed."""
    r = info.get("process") or RUNS.get(info["code"])
    if r is None:
        return
    r.finish()
    with _LOCK:
        if RUNS.get(info["code"]) is r:
            RUNS.pop(info["code"])
    if r.is_running():
        raise StudioError(T("Could not close the Studio process."))


def run_problem(info: dict) -> str | None:
    """If the run ended on its own (license, missing file, crash), the reason."""
    r = info.get("process")
    return r.problem() if r is not None else None


# ---------------------------------------------------------------- development commands


def _cli(args: list[str]) -> None:
    paths.prepare()
    if args[0] == "status":
        print(asyncio.run(status()))
    elif args[0] == "probe":
        print(probe())
    elif args[0] == "run":
        code = args[1]
        config = json.loads((paths.CAMERAS / f"{code}.json").read_text(encoding="utf-8"))
        info = asyncio.run(prepare_scene(code, config))
        asyncio.run(new_run(info, code))
        asyncio.run(run(info))
        time.sleep(float(args[2]) if len(args) > 2 else 30)
        print("problem:", run_problem(info))
        asyncio.run(stop(info))
        print("stopped")


if __name__ == "__main__":
    _cli(sys.argv[1:])
