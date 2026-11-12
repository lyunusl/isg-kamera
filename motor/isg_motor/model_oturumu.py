"""ONNX Runtime model oturumlarının yaşam döngüsü."""
from __future__ import annotations

import threading
import time

from . import kasa, community_model, model_lisans

BOSTA_SURE = 5.0
COMMUNITY_DENETIM_ARALIGI = 60.0
COMMUNITY_TEKRAR_ARALIGI = 5.0
COMMUNITY_OKUMA_TOLERANSI = 15.0


def oturum_ac(yol, veri, secenek, saglayicilar, *, cpu_yedek=False):
    if community_model.model_mi(yol):
        kaynak, bilesen = community_model.ac(yol)
        oturum = _onnx(kaynak, secenek, saglayicilar, cpu_yedek)
        return _LisansliOturum(yol, veri, secenek, saglayicilar, cpu_yedek, oturum, None, bilesen)
    kaynak, bitis = kasa.model_ve_sure(yol, veri)
    oturum = _onnx(kaynak, secenek, saglayicilar, cpu_yedek)
    if bitis is None:
        return oturum
    return _LisansliOturum(yol, veri, secenek, saglayicilar, cpu_yedek, oturum, bitis)


def _onnx(kaynak, secenek, saglayicilar, cpu_yedek):
    import onnxruntime as ort

    try:
        return ort.InferenceSession(kaynak, sess_options=secenek, providers=saglayicilar)
    except Exception:
        if not cpu_yedek or saglayicilar == ["CPUExecutionProvider"]:
            raise
        return ort.InferenceSession(kaynak, sess_options=secenek, providers=["CPUExecutionProvider"])


class _LisansliOturum:
    """Studio blokları durduğunda bir daha çağrılmasa da ONNX oturumu boştayken kapanır."""

    def __init__(self, yol, veri, secenek, saglayicilar, cpu_yedek, oturum, bitis, community_dll=None):
        self._yol, self._veri = yol, veri
        self._secenek, self._saglayicilar, self._cpu_yedek = secenek, saglayicilar, cpu_yedek
        self._kilit = threading.Lock()
        self._oturum, self._bitis = oturum, bitis
        self._community_dll = community_dll
        self._son = time.monotonic()
        self._community_dogrulandi(self._son)
        self._zamanlayici = None
        self._zamanla(max(0, self._kalan()))

    def _kalan(self):
        kalan = BOSTA_SURE - (time.monotonic() - self._son)
        return kalan if self._bitis is None else min(kalan, self._bitis - time.time())

    def _zamanla(self, sure):
        zamanlayici = threading.Timer(sure, self._bosta_kapat)
        zamanlayici.daemon = True
        self._zamanlayici = zamanlayici
        zamanlayici.start()

    def _bosta_kapat(self):
        with self._kilit:
            if self._zamanlayici is not threading.current_thread():
                return  # Bu çağrıdan önce kapatılmış veya yeni bir zamanlayıcı kurulmuş.
            kalan = self._kalan()
            if kalan > 0:
                self._zamanla(kalan)
            else:
                self._oturum = self._zamanlayici = None

    def kapat(self):
        with self._kilit:
            if self._zamanlayici is not None:
                self._zamanlayici.cancel()
            self._oturum = self._zamanlayici = None

    def _community_dogrulandi(self, simdi):
        self._community_yenileme = simdi + COMMUNITY_DENETIM_ARALIGI
        self._community_sinir = self._community_yenileme + COMMUNITY_OKUMA_TOLERANSI

    def _community_yenile(self):
        if time.monotonic() < self._community_yenileme:
            return
        try:
            community_model.yetki(self._community_dll)
        except community_model.StudioLisansOkumaHatasi:
            simdi = time.monotonic()
            if simdi >= self._community_sinir:
                raise
            self._community_yenileme = min(simdi + COMMUNITY_TEKRAR_ARALIGI, self._community_sinir)
        else:
            self._community_dogrulandi(time.monotonic())

    def _cagir(self, ad, *args):
        with self._kilit:
            if self._community_dll is not None and self._oturum is not None:
                try:
                    self._community_yenile()
                except model_lisans.LisansHatasi:
                    self._oturum = None
                    raise
            if self._oturum is not None and self._bitis is not None and time.time() >= self._bitis:
                self._oturum = None
            if self._oturum is None:
                if self._community_dll is not None:
                    kaynak, bilesen = community_model.ac(self._yol)
                    if bilesen != self._community_dll:
                        raise model_lisans.LisansHatasi("Model paketi değişmiş. Analizi yeniden başlatın.")
                    self._community_dogrulandi(time.monotonic())
                    bitis = None
                else:
                    kaynak, bitis = kasa.model_ve_sure(self._yol, self._veri)
                    if bitis is None:
                        raise model_lisans.LisansHatasi("Lisanslı model paketi değişmiş.")
                self._oturum = _onnx(kaynak, self._secenek, self._saglayicilar, self._cpu_yedek)
                self._bitis = bitis
                if self._zamanlayici is not None:
                    self._zamanlayici.cancel()
                self._son = time.monotonic()
                self._zamanla(max(0, self._kalan()))
            if self._bitis is not None and time.time() >= self._bitis:
                self._oturum = None
                model_lisans.suresi_gecerli(self._bitis)
            try:
                return getattr(self._oturum, ad)(*args)
            finally:
                self._son = time.monotonic()

    def run(self, *args):
        return self._cagir("run", *args)

    def get_inputs(self):
        return self._cagir("get_inputs")

    def get_outputs(self):
        return self._cagir("get_outputs")

    def get_modelmeta(self):
        return self._cagir("get_modelmeta")

    def get_providers(self):
        return self._cagir("get_providers")

