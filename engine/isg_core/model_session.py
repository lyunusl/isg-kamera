"""Life cycle of the ONNX Runtime model sessions. User-visible messages use T()."""
from __future__ import annotations

import threading
import time

from . import vault, community_model, model_license
from .i18n import T

IDLE_TIMEOUT = 5.0
COMMUNITY_CHECK_INTERVAL = 60.0
COMMUNITY_RETRY_INTERVAL = 5.0
COMMUNITY_READ_GRACE = 15.0


def open_session(path, data_dir, options, providers, *, cpu_fallback=False):
    if community_model.is_community_model(path):
        source, component = community_model.open_model(path)
        session = _onnx(source, options, providers, cpu_fallback)
        return _LicensedSession(path, data_dir, options, providers, cpu_fallback, session, None, component)
    source, expires = vault.model_and_expiry(path, data_dir)
    session = _onnx(source, options, providers, cpu_fallback)
    if expires is None:
        return session
    return _LicensedSession(path, data_dir, options, providers, cpu_fallback, session, expires)


def _onnx(source, options, providers, cpu_fallback):
    import onnxruntime as ort

    try:
        return ort.InferenceSession(source, sess_options=options, providers=providers)
    except Exception:
        if not cpu_fallback or providers == ["CPUExecutionProvider"]:
            raise
        return ort.InferenceSession(source, sess_options=options, providers=["CPUExecutionProvider"])


class _LicensedSession:
    """The ONNX session closes when idle, even if the Studio blocks stop and never call it again."""

    def __init__(self, path, data_dir, options, providers, cpu_fallback, session, expires, community_dll=None):
        self._path, self._data_dir = path, data_dir
        self._options, self._providers, self._cpu_fallback = options, providers, cpu_fallback
        self._lock = threading.Lock()
        self._session, self._expires = session, expires
        self._community_dll = community_dll
        self._last = time.monotonic()
        self._community_verified(self._last)
        self._timer = None
        self._schedule(max(0, self._remaining()))

    def _remaining(self):
        remaining = IDLE_TIMEOUT - (time.monotonic() - self._last)
        return remaining if self._expires is None else min(remaining, self._expires - time.time())

    def _schedule(self, delay):
        timer = threading.Timer(delay, self._close_when_idle)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _close_when_idle(self):
        with self._lock:
            if self._timer is not threading.current_thread():
                return  # Closed before this call, or a new timer has been set up.
            remaining = self._remaining()
            if remaining > 0:
                self._schedule(remaining)
            else:
                self._session = self._timer = None

    def close(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._session = self._timer = None

    def _community_verified(self, now):
        self._community_refresh_at = now + COMMUNITY_CHECK_INTERVAL
        self._community_deadline = self._community_refresh_at + COMMUNITY_READ_GRACE

    def _community_refresh(self):
        if time.monotonic() < self._community_refresh_at:
            return
        try:
            community_model.authorize(self._community_dll)
        except community_model.StudioLicenseReadError:
            now = time.monotonic()
            if now >= self._community_deadline:
                raise
            self._community_refresh_at = min(now + COMMUNITY_RETRY_INTERVAL, self._community_deadline)
        else:
            self._community_verified(time.monotonic())

    def _call(self, name, *args):
        with self._lock:
            if self._community_dll is not None and self._session is not None:
                try:
                    self._community_refresh()
                except model_license.LicenseError:
                    self._session = None
                    raise
            if self._session is not None and self._expires is not None and time.time() >= self._expires:
                self._session = None
            if self._session is None:
                if self._community_dll is not None:
                    source, component = community_model.open_model(self._path)
                    if component != self._community_dll:
                        raise model_license.LicenseError(T("The model package has changed. Restart the analysis."))
                    self._community_verified(time.monotonic())
                    expires = None
                else:
                    source, expires = vault.model_and_expiry(self._path, self._data_dir)
                    if expires is None:
                        raise model_license.LicenseError(T("The licensed model package has changed."))
                self._session = _onnx(source, self._options, self._providers, self._cpu_fallback)
                self._expires = expires
                if self._timer is not None:
                    self._timer.cancel()
                self._last = time.monotonic()
                self._schedule(max(0, self._remaining()))
            if self._expires is not None and time.time() >= self._expires:
                self._session = None
                model_license.check_not_expired(self._expires)
            try:
                return getattr(self._session, name)(*args)
            finally:
                self._last = time.monotonic()

    def run(self, *args):
        return self._call("run", *args)

    def get_inputs(self):
        return self._call("get_inputs")

    def get_outputs(self):
        return self._call("get_outputs")

    def get_modelmeta(self):
        return self._call("get_modelmeta")

    def get_providers(self):
        return self._call("get_providers")
