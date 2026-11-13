"""AugeLab Studio bloklarının çağırdığı işlevler.

Bloklar incedir: görüntüyü ve kamera kodunu bu işlevlere verir. Motor klasörü (isg_engine) Studio'nun özel blok
klasörünün yanındadır ve şunları içerir:

  isg_motor/   bu paket
  modeller/    ONNX model dosyaları ve paket girdileri
  deps/        Studio'da olmayan kütüphaneler (onnxruntime)
  ayar.json    {"veri": "<panelin veri klasörü>"}  (kamera ayarları, kuyruk ve klipler orada)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import cv2
import numpy as np

from . import katalog, model_lisans
from .algila import Algilayici
from .kayit import Kayitci, ciz, kuyruga_birak
from .kural import Motor

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Arayüzsüz koşu (python -m studio run): panel her kamera için sahne şablonunu ayrı bir süreçte çalıştırır ve kameranın
# kimliğini bu ortam değişkenleriyle verir. Şablondaki "İSG Kamera Kodu" girişi o zaman yok sayılır.
ORTAM_KAMERA = "ISG_KAMERA"        # "kod#koşu"
ORTAM_ADRES = "ISG_KAMERA_ADRES"   # canlı kameranın akış adresi (şifre içerebilir: dosyaya yazılmaz)
ORTAM_DUR = "ISG_DUR"              # bu dosya oluşunca akış biter: açık olaylar kapanış kaydı bırakır
ORTAM_DENEME_SONUC = "ISG_DENEME_SONUC"  # deneme koşusunda modellerin açılıp açılmadığı bu dosyaya yazılır
DENEME_KOD = "_deneme"             # panelin "Studio'yu dene" koşusu: kamera yok, siyah kare ile modeller (ve lisans) sınanır
# Deneme koşusunda bu iki değişken verilirse siyah kare yerine bu kare kullanılır, yüzleri gizlenip ISG_YUZ_CIKTI'ya yazılır
# (bağlantı denemesinin bölge çizme karesi: şifreli modeller yalnızca Studio'da açılır). Kare diske yazılmaz: panel onu
# 127.0.0.1'deki tek seferlik bir adresten verir.
ORTAM_YUZ_KARE = "ISG_YUZ_KARE"
ORTAM_YUZ_CIKTI = "ISG_YUZ_CIKTI"


def _yerel_kare(adres: str):
    """Panelin verdiği kareyi bellekten alır (yalnızca 127.0.0.1; vekil sunucu kullanılmaz)."""
    import urllib.request

    if not adres.startswith("http://127.0.0.1:"):
        return None
    acici = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with acici.open(adres, timeout=15) as r:
        veri = r.read(64 * 1024 * 1024)
    return cv2.imdecode(np.frombuffer(veri, np.uint8), cv2.IMREAD_COLOR)
OLCUM_ARALIGI = 30.0  # saniye: ısı haritası ve sayılar bu aralıkla panele bırakılır


def _hata_json(neden: str) -> str:
    """Tespit çalışmadığında "tespitler" yerine verilen değer. Boş liste "kimse yok" demektir; bu ise "bilinmiyor" demektir:
    İSG Kurallar bu karede kural değerlendirmez, görüntü kaydetmez ve panele hata bırakır."""
    return json.dumps({"hata": neden}, ensure_ascii=False)


def kod_ve_kosu(kamera_kod) -> tuple[str, str]:
    """Kamera girişi "kod" ya da "kod#koşu" biçimindedir; koşu değişince bloklar baştan başlar.
    Arayüzsüz koşuda kamera ortam değişkeninden gelir ve girişteki değerin yerini alır."""
    kamera_kod = os.environ.get(ORTAM_KAMERA) or kamera_kod
    kod, _, kosu = str(kamera_kod or "").strip().partition("#")
    return kod.strip(), kosu


def veri_klasoru() -> str:
    with open(os.path.join(KOK, "ayar.json"), encoding="utf-8") as f:
        return json.load(f)["veri"]


def kamera_ayari(kod: str) -> tuple[dict, float]:
    yol = os.path.join(veri_klasoru(), "ayar", f"{kod}.json")
    with open(yol, encoding="utf-8") as f:
        return json.load(f), os.path.getmtime(yol)


def _goruntu(image) -> np.ndarray | None:
    if image is None:
        return None
    a = np.asarray(image)
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    elif a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    return np.ascontiguousarray(a) if a.ndim == 3 and a.size else None


def _bos_kare(state: dict, sinir: int = 10) -> bool:
    """Arka arkaya `sinir` kez görüntü gelmediyse True (bir kez): akış bitmiş sayılır. Açık kalan her Studio belgesi
    modellerini bellekte tuttuğu için (belge başına yaklaşık 0,6-0,9 GB) akışı biten blok modellerini bırakır."""
    state["bos"] = state.get("bos", 0) + 1
    return state["bos"] == sinir


class _CanliOkuyucu:
    """Canlı akışı arka planda okur ve yalnızca son kareyi tutar: analiz kare hızından yavaşken gecikme birikmesin.
    Bağlantı koparsa birkaç saniyede bir yeniden bağlanır."""

    def __init__(self, adres: str):
        import threading

        self.adres, self.kare, self.sira, self.durum = adres, None, 0, "bekliyor: kameraya bağlanılıyor"
        self._dur = threading.Event()
        self._kilit = threading.Lock()
        threading.Thread(target=self._oku, name="isg-canli", daemon=True).start()

    def _oku(self) -> None:
        bekle = 1.0
        while not self._dur.is_set():
            cap = cv2.VideoCapture(self.adres, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self.durum = "bekliyor: kameraya bağlanılamadı, yeniden deneniyor"
                self._dur.wait(bekle)
                bekle = min(10.0, bekle * 2)
                continue
            bekle = 1.0
            while not self._dur.is_set():
                ok, fr = cap.read()
                if not ok:
                    self.durum = "bekliyor: görüntü kesildi, yeniden bağlanılıyor"
                    break
                with self._kilit:
                    self.kare, self.sira = fr, self.sira + 1
            cap.release()

    def son(self):
        with self._kilit:
            return self.kare, self.sira

    def kapat(self) -> None:
        self._dur.set()


def _durduruldu() -> bool:
    """Panel arayüzsüz koşuyu durdururken önce bu dosyayı oluşturur: akış "bitti" olur, açık olaylar kapanır."""
    dur = os.environ.get(ORTAM_DUR)
    return bool(dur) and os.path.exists(dur)


def _video_ac(state: dict, kod: str, kosu: str) -> str | None:
    """Kaynağı açar. Dönüş: hata durumu ya da None."""
    state["kosu"] = kosu
    try:
        ayar, _ = kamera_ayari(kod)
    except Exception as e:
        return f"hata: kamera ayarı okunamadı ({kod}): {e}"
    kamera = ayar.get("kamera") or {}
    if kamera.get("tur") in ("rtsp", "onvif", "http"):
        adres = os.environ.get(ORTAM_ADRES, "")
        if not adres:
            return "hata: kameranın akış adresi verilmedi (sahneyi İSG Kamera paneli başlatır)"
        state.update(kod=kod, canli=_CanliOkuyucu(adres), sira=0, n=0, bitti=False)
        return None
    if kamera.get("tur") != "dosya":
        return "hata: bu kamera türü İSG Video ile okunamıyor"
    yol = kamera.get("yol", "")
    yol = yol if os.path.isabs(yol) else os.path.join(veri_klasoru(), yol)
    cap = cv2.VideoCapture(yol)
    if not cap.isOpened():
        return f"hata: video açılamadı: {os.path.basename(yol)}"
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    state.update(kod=kod, cap=cap, fps=fps if 1 <= fps <= 120 else 25.0, n=0, toplam=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), bitti=False)
    return None


def _video_kapat(state: dict) -> None:
    if state.get("cap") is not None:
        state["cap"].release()
    if state.get("canli") is not None:
        state["canli"].kapat()
    state.clear()


def video(state: dict, kamera_kod: str):
    """İSG Video bloğu: kameranın ayarındaki video dosyasını kare kare okur. Arayüzsüz koşuda canlı kameranın akışını da
    okur (adres ortam değişkeninden gelir).
    Dönüş: (görüntü | None, zaman (sn; canlı akışta -1: duvar saati kullanılır), bitti, durum). Dosya bitince ya da panel
    durdurunca bir kez bitti=True döner, sonra görüntü vermez."""
    kod, kosu = kod_ve_kosu(kamera_kod)
    if kod == DENEME_KOD:  # deneme koşusu: modelleri sınamak için siyah bir kare yeter
        ham = os.environ.get(ORTAM_YUZ_KARE)
        if ham:
            if "ham" not in state:
                try:
                    state["ham"] = _yerel_kare(ham)
                except Exception:
                    state["ham"] = None
            if state["ham"] is None:
                return None, 0.0, False, "hata: kare okunamadı"
            return state["ham"].copy(), -1.0, False, "ok bağlantı karesi"
        return np.zeros((360, 640, 3), np.uint8), -1.0, False, "ok deneme karesi"
    if state.get("kod") != kod or state.get("kosu") != kosu:
        _video_kapat(state)
        hata = _video_ac(state, kod, kosu)
        if hata:
            return None, 0.0, False, hata
    canli = state.get("canli")
    if state["bitti"]:
        return None, -1.0 if canli else state["n"] / state["fps"], False, f"bitti: {state['n']} kare"
    if _durduruldu():
        state["bitti"] = True
        if canli:
            canli.kapat()
        else:
            state["cap"].release()
        return None, -1.0 if canli else state["n"] / state["fps"], True, f"bitti: durduruldu, {state['n']} kare"
    if canli:
        kare, sira = canli.son()
        if kare is None or sira == state["sira"]:
            return None, -1.0, False, canli.durum if kare is None else "ok: yeni kare bekleniyor"
        state["sira"] = sira
        state["n"] += 1
        return kare, -1.0, False, f"ok {state['n']} kare"
    ok, fr = state["cap"].read()
    if not ok:
        state["bitti"] = True
        state["cap"].release()
        return None, state["n"] / state["fps"], True, f"bitti: {state['n']} kare"
    t = state["n"] / state["fps"]
    state["n"] += 1
    return fr, t, False, f"ok {state['n']}" + (f" / {state['toplam']}" if state["toplam"] > 0 else "") + " kare"


def algila(state: dict, image, kamera_kod: str) -> tuple[str, str]:
    """İSG Algılama bloğu. Dönüş: (tespitler JSON, durum)."""
    fr = _goruntu(image)
    if fr is None:
        if _bos_kare(state) and state.pop("algilayici", None) is not None:
            state.pop("imza", None)  # akış bitti: modeller bellekten bırakılır, görüntü yeniden gelirse baştan yüklenir
        return "[]", "bekliyor: görüntü yok"
    state["bos"] = 0
    if kod_ve_kosu(kamera_kod)[0] == DENEME_KOD:
        return _model_deneme(state, fr)
    try:
        ayar, degisim = kamera_ayari(kod_ve_kosu(kamera_kod)[0])
    except Exception as e:
        return _hata_json(f"kamera ayarı okunamadı ({kamera_kod})"), f"hata: kamera ayarı okunamadı ({kamera_kod}): {e}"
    plan = katalog.model_plani(ayar)
    imza = (tuple(m["dosya"] for m in plan), degisim)
    if state.get("imza") != imza:
        try:
            state["algilayici"] = Algilayici(os.path.join(KOK, "modeller"), plan, veri_klasoru())
        except Exception as e:
            return _hata_json(f"model yüklenemedi: {e}"), f"hata: model yüklenemedi: {type(e).__name__}: {e}"
        state["imza"] = imza
    t0 = time.perf_counter()
    try:
        tespitler = state["algilayici"].calistir(fr)
    except model_lisans.LisansHatasi as e:
        state.pop("algilayici", None)
        state.pop("imza", None)
        return _hata_json(str(e)), f"hata: {e}"
    except Exception as e:
        return _hata_json(f"tespit çalışmadı: {type(e).__name__}"), f"hata: tespit çalışmadı: {type(e).__name__}: {e}"
    return json.dumps(tespitler), f"ok {len(tespitler)} tespit · {1000 * (time.perf_counter() - t0):.0f} ms · {state['algilayici'].ozet()}"


def _model_deneme(state: dict, fr) -> tuple[str, str]:
    """"Studio'yu dene": varsayılan model planını açıp bir kare çalıştırır (şifreli paketlerde lisans kontrolü burada olur) ve
    sonucu ORTAM_DENEME_SONUC dosyasına bir kez yazar. Kamera ayarı gerekmez."""
    if state.get("deneme_yazildi"):
        return "[]", state.get("deneme_durum", "deneme bitti")
    sonuc = {"ok": False, "hata": ""}
    cikti = os.environ.get(ORTAM_YUZ_CIKTI) if os.environ.get(ORTAM_YUZ_KARE) else None
    try:
        plan = [katalog.MODEL_PLANI[0]] if cikti else katalog.model_plani({})
        algilayici = Algilayici(os.path.join(KOK, "modeller"), plan, veri_klasoru())
        tespitler = algilayici.calistir(fr)
        ek = {}
        if cikti:
            from .kayit import yuz_bolgeleri, yuzleri_gizle

            kare = fr.copy()
            yuzleri_gizle(kare, tespitler)
            if not cv2.imwrite(cikti, kare):
                raise OSError("gizlenmiş kare yazılamadı")
            ek["yuz"] = len(yuz_bolgeleri(tespitler))
        sonuc = {"ok": True, "ozet": algilayici.ozet(), **ek}
    except model_lisans.LisansHatasi as e:
        sonuc["hata"] = f"lisans: {e}"
    except Exception as e:  # model dosyası yok, paket açılamadı, onnxruntime yüklenemedi...
        sonuc["hata"] = f"{type(e).__name__}: {e}"
    yol = os.environ.get(ORTAM_DENEME_SONUC)
    if yol:
        try:
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(sonuc, f, ensure_ascii=False)
        except OSError:
            pass
    state["deneme_yazildi"] = True
    state["deneme_durum"] = "ok modeller açıldı" if sonuc["ok"] else f"hata: {sonuc['hata']}"
    return "[]", state["deneme_durum"]


def iskelet(state: dict, image, tespit_json: str) -> tuple[str, str]:
    """İSG İskelet bloğu: kişi tespitlerine 17 eklem noktasını ekler. Dönüş: (tespitler JSON, durum)."""
    fr = _goruntu(image)
    if fr is None:
        if _bos_kare(state):
            state.pop("model", None)
        return tespit_json or "[]", "bekliyor: görüntü yok"
    state["bos"] = 0
    try:
        tespitler = json.loads(tespit_json) if tespit_json else []
    except ValueError:
        return _hata_json("tespitler okunamadı"), "hata: tespitler okunamadı"
    if not isinstance(tespitler, list):  # İSG Algılama çalışmıyor: hata olduğu gibi İSG Kurallar'a iletilir
        return tespit_json, "hata: tespit yok (İSG Algılama'nın Durum çıkışına bakın)"
    if state.get("model") is None:
        from .iskelet import Iskelet

        try:
            state["model"] = Iskelet(os.path.join(KOK, "modeller", "isg-iskelet.onnx"), veri_klasoru())
        except model_lisans.LisansHatasi as e:
            return _hata_json(str(e)), f"hata: {e}"
        except Exception as e:
            return tespit_json, f"hata: iskelet modeli yüklenemedi: {type(e).__name__}: {e}"
    t0 = time.perf_counter()
    try:
        n = state["model"].calistir(fr, tespitler)
    except model_lisans.LisansHatasi as e:
        state.pop("model", None)
        return _hata_json(str(e)), f"hata: {e}"
    except Exception as e:
        return tespit_json, f"hata: iskelet çalışmadı: {type(e).__name__}: {e}"
    kisi = sum(1 for d in tespitler if d["s"] == "kisi")
    return json.dumps(tespitler), f"ok {n}/{kisi} kişide iskelet · {1000 * (time.perf_counter() - t0):.0f} ms"


def _kur(state: dict, kod: str, ayar: dict, degisim: float, W: int, H: int) -> None:
    kamera = ayar.get("kamera") or {}
    bas = dt.datetime.now().replace(microsecond=0)
    if kamera.get("tur") == "dosya" and kamera.get("baslangic"):
        try:
            bas = dt.datetime.fromisoformat(kamera["baslangic"])
        except ValueError:
            pass
    state.update(kod=kod, degisim=degisim, boyut=(W, H), n=0, t0=time.monotonic(), son_t=None, son_olcum=0.0, son_cizili=None,
                 motor=Motor(ayar, W, H, bas, 0.125), kayitci=None, ayar=ayar, yuz_hafiza={})


def _bitir(state: dict, isaret: bool = True) -> None:
    motor, kayitci = state.get("motor"), state.get("kayitci")
    if motor is None:
        return
    if kayitci is not None:
        kayitci.bitir(motor.kapat(), isaret)
        o = motor.olcum()
        if o is not None:
            kayitci.olcum(o, state.get("son_cizili"))
    state["motor"] = None


def kurallar(state: dict, image, tespit_json: str, kamera_kod: str, zaman=None, bitti: bool = False):
    """İSG Kurallar bloğu. `zaman`: görüntünün saniye cinsinden zamanı (video dosyasında İSG Video bloğundan gelir;
    boşsa duvar saati kullanılır). `bitti`: akış bitti, açık olayları kapat.
    Dönüş: (işaretli görüntü, alarm, risk puanı, başlayan olaylar JSON, durum, yeni olay var mı, alarm yazılsın mı).
    "alarm yazılsın mı": alarm durumu değiştiğinde ve beş saniyede bir True olur; PLC yazma bloklarının Enable girişine bağlanır,
    böylece PLC'ye her karede değil yalnızca gerektiğinde yazılır."""
    if bitti:  # akış bitti: giriş görüntüsü dolu gelse de, motor henüz kurulmamış olsa da ham kare çıkışa verilmez
        if state.get("motor") is None:
            return None, False, 0, "[]", "bitti: akış kapandı", False, False
        _bitir(state)
        state["son_alarm"] = False
        return None, False, 0, "[]", "bitti: akış kapandı", False, True  # alarm kapalı olarak bir kez yazılır
    fr = _goruntu(image)
    if fr is None:
        return image, False, 0, "[]", "bekliyor: görüntü yok", False, False
    kod, kosu = kod_ve_kosu(kamera_kod)
    if state.get("kosu") != kosu:  # yeni çalıştırma: önceki akıştan kalanı kapat, baştan başla
        _bitir(state, isaret=False)
        state["kosu"] = kosu
    try:
        ayar, degisim = kamera_ayari(kod)
    except Exception as e:  # ham kare çıkışa verilmez: yüzler gizlenmedi
        return np.zeros_like(fr), False, 0, "[]", f"hata: kamera ayarı okunamadı ({kod}): {e}", False, False
    H, W = fr.shape[:2]
    if state.get("motor") is None or state.get("kod") != kod or state.get("degisim") != degisim or state.get("boyut") != (W, H):
        _bitir(state, isaret=False)
        _kur(state, kod, ayar, degisim, W, H)
    motor = state["motor"]
    try:
        tespitler = json.loads(tespit_json) if tespit_json else []
    except ValueError:
        tespitler = {"hata": "tespitler okunamadı"}
    if not isinstance(tespitler, list):
        # Tespit çalışmıyor (model dosyası yok, yüklenemedi, hata verdi). Bu "kimse yok" sayılmaz: kişiler bilinmediği için
        # yüzler gizlenemez, bu yüzden kare kaydedilmez ve çıkışa da verilmez; panel işi hatayla durdurur.
        neden = str(tespitler.get("hata") or "bilinmiyor") if isinstance(tespitler, dict) else "tespitler okunamadı"
        if time.monotonic() - state.get("hata_kaydi", 0.0) >= 5.0:
            state["hata_kaydi"] = time.monotonic()
            try:
                kuyruga_birak(veri_klasoru(), kod, {"tur": "hata", "kamera": kod, "kosu": kosu, "metin": neden})
            except OSError:
                pass
        return np.zeros_like(fr), False, 0, "[]", f"hata: tespit çalışmıyor, görüntü kaydedilmiyor: {neden}", False, False
    try:
        t = float(zaman) if zaman is not None and str(zaman) != "" else None
    except (TypeError, ValueError):
        t = None
    if t is None or t < 0:  # canlı akış (İSG Video -1 verir) ya da zaman girişi bağlı değil: duvar saati
        t = time.monotonic() - state["t0"]
    if state["son_t"] is not None and t > state["son_t"]:  # gerçek kare aralığının yumuşatılmış ortalaması
        aralik = min(1.0, max(0.02, t - state["son_t"]))
        motor.aralik = aralik if state["n"] == 1 else 0.9 * motor.aralik + 0.1 * aralik
    state["son_t"] = t
    state["n"] += 1
    if state["kayitci"] is None:  # klip hızı ilk iki karenin aralığından
        if state["n"] < 2:  # ilk kare işaretlenmeden geçer: ham hâli çıkışa verilmez
            return np.zeros_like(fr), False, 0, "[]", "ok ilk kare", False, False
        state["kayitci"] = Kayitci(veri_klasoru(), kod, ayar.get("ad", kod), W, H, 1 / motor.aralik)
        state["kayitci"].kosu = kosu
    kayitci = state["kayitci"]
    if time.monotonic() - state.get("nabiz", 0.0) >= 5.0:  # panel, sahnenin çalıştığını bu kayıttan anlar
        state["nabiz"] = time.monotonic()
        kayitci._birak({"tur": "nabiz", "kamera": kod, "kare": state["n"]})
    sonuc = motor.adim(t, tespitler, fr)
    an = (motor.bas_zaman + dt.timedelta(seconds=t)).strftime("%d.%m.%Y %H:%M:%S")
    acik = sonuc["acik"]
    ust = " · ".join(dict.fromkeys(v.get("etiket", "") for v in sonuc["vurgu"])) if sonuc["vurgu"] else ""
    cizili = ciz(fr.copy(), sonuc, tespitler, motor.bolgeler, ust, f"{ayar.get('ad', kod)} · {an} · yüzler gizlendi", state.setdefault("yuz_hafiza", {}), t)
    kayitci.kare(t, cizili, sonuc)
    state["son_cizili"] = cizili
    if t - state["son_olcum"] >= OLCUM_ARALIGI:
        state["son_olcum"] = t
        o = motor.olcum()
        if o is not None:
            kayitci.olcum(o, cizili)
    # Alarm, önemi eşiğin üstünde bir olay sürdüğü sürece açıktır (olay başlar başlamaz); risk puanı süreyle büyür.
    esik = int(ayar.get("alarm_en_az_onem", 4))
    puanlar = [katalog.risk(katalog.KURAL[o["kural"]]["onem"], katalog.olasilik(t - o["bas"], o["sira"])) for o in acik]
    risk = max(puanlar, default=0)
    alarm = any(katalog.KURAL[o["kural"]]["onem"] >= esik for o in acik)
    durum = f"ok {len(sonuc['kisiler'])} kişi, {len(sonuc['araclar'])} araç, {len(acik)} açık olay · {an}"
    simdi = time.monotonic()
    yaz = bool(alarm) != bool(state.get("son_alarm")) or simdi - state.get("son_yazma", 0.0) >= 5.0
    if yaz:
        state["son_alarm"], state["son_yazma"] = bool(alarm), simdi
    return cizili, bool(alarm), int(risk), json.dumps(sonuc["baslayan"], ensure_ascii=False), durum, bool(sonuc["baslayan"]), yaz


def kapat(state: dict) -> None:
    """Sahne durdurulurken çağrılabilir: açık olayları ve sayıları kuyruğa bırakır."""
    _bitir(state)
