"""AugeLab Studio köprüsü: Studio'nun yerel MCP adresi üzerinden.

İSG Kamera, Studio'nun kodunu içermez. Kullanıcının kurup açtığı Studio'ya, Studio'nun herkese açık MCP araçlarıyla
(node.custom.create, graph.program, scene.run.start, ...) bağlanır. İlk kullanımda kendi bloklarını kaydeder
(studio_blok.py), motorunu Studio'nun özel blok klasörünün yanına koyar ve her kamera için bir sahne kurar:

    İSG Kamera Kodu ─────────────────────┬──────────────────┐
    İSG Video / Camera IP ┬► İSG Algılama ─► İSG İskelet ─► İSG Kurallar ─► işaretli görüntü, Alarm, Risk, Olaylar
                       └────────────────────► (görüntü)          (Alarm → Modbus / S7 / OPC UA / MQTT blokları)

Sahne Studio'da canlı çalışır; olaylar ve klipler panelin veri klasörüne (kuyruk/) düşer.

Geliştirme için:  python studio.py durum | kur <kamera> | calistir <kamera> [saniye] | durdur <kamera>
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import studio_blok
import yollar

VARSAYILAN_ADRES = "http://127.0.0.1:8765/mcp"
OP_METIN = "119"       # String Input
OP_VIDEO = "203"       # Video
OP_KAMERA_IP = "2024"  # Camera IP
OP_KAMERA_USB = "202"  # Camera USB
KOD_BASLIGI = "İSG Kamera Kodu"
MOTOR_KAYNAK = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "motor"
DURUM_DOSYASI = yollar.VERI / "studio.json"


class StudioHatasi(RuntimeError):
    """Studio bir isteği reddetti ya da sahne çalışmadı."""


class StudioYok(StudioHatasi):
    """Studio açık değil ya da MCP sunucusu kapalı: beklenebilir, hata değil."""


def _anahtar() -> str:
    return "isg-" + secrets.token_hex(12)


def _tirnak(deger: str) -> str:
    return "'" + str(deger).replace("\\", "/").replace("'", "\\'") + "'"


async def cagir(arac: str, arg: dict[str, Any], adres: str = VARSAYILAN_ADRES) -> dict[str, Any]:
    """Bir Studio aracını çağırır. Değiştiren çağrılar idempotencyKey taşıdığı için yeniden denemek güvenlidir."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    son: Exception | None = None
    for deneme in range(4):
        try:
            async with streamable_http_client(adres) as (oku, yaz, *_):
                async with ClientSession(oku, yaz) as oturum:
                    await oturum.initialize()
                    sonuc = await oturum.call_tool(arac, arg)
            break
        except Exception as e:  # bağlantı reddi, zaman aşımı, protokol hatası
            son = e
            if deneme < 3:
                await asyncio.sleep(1 + deneme)
    else:
        raise StudioYok(f"AugeLab Studio'ya bağlanılamadı ({adres}). Studio açık mı, MCP etkin mi?") from son
    yuk = getattr(sonuc, "structuredContent", None) or getattr(sonuc, "structured_content", None)
    if not yuk and sonuc.content:
        try:
            yuk = json.loads(getattr(sonuc.content[0], "text", "") or "{}")
        except ValueError:
            yuk = {}
    yuk = yuk or {}
    if getattr(sonuc, "isError", False) or getattr(sonuc, "is_error", False) or yuk.get("error"):
        hata = yuk.get("error") or {}
        raise StudioHatasi(f"{arac}: {hata.get('code', 'HATA')} {hata.get('message', '')}".strip())
    veri = dict(yuk.get("data") or {})
    if yuk.get("operation") and "operation" not in veri:  # uzun işler "kabul edildi" yanıtını verinin yanında döndürür
        veri["operation"] = yuk["operation"]
    return veri


async def bekle(veri: dict[str, Any], sinir_sn: float) -> dict[str, Any]:
    """Kabul edilen bir işi kısa sunucu beklemeleriyle sonuna kadar izler."""
    is_ = veri.get("operation") or veri
    bas = time.monotonic()
    while is_.get("state") in ("queued", "running") and time.monotonic() - bas < sinir_sn:
        dahasi = await cagir("operation.get", {"operationId": is_["id"], "waitTimeoutMs": 15000, "updateLimit": 1})
        is_ = dahasi.get("operation") or dahasi
    return is_


async def durum() -> dict[str, Any]:
    v = await cagir("studio.status.get", {})
    lisans = v.get("license")
    community_siniri = None
    if isinstance(lisans, dict):
        plan = lisans.get("tier")
        suresi_doldu = lisans.get("expired")
        fps_siniri = lisans.get("fpsLimitActive")
        if isinstance(plan, str) and isinstance(suresi_doldu, bool) and isinstance(fps_siniri, bool):
            community_siniri = plan.casefold() == "community" and suresi_doldu and fps_siniri
    return {
        "surum": v.get("studioVersion"),
        "mcp": (v.get("mcp") or {}).get("state"),
        "motor_calisiyor": v.get("engineRunning"),
        "community_fps_siniri": community_siniri,
    }


# ---------------------------------------------------------------- motor klasörü


def _dosyalar(klasor: Path) -> list[Path]:
    return sorted(p for p in klasor.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def _dosya_ozeti(p: Path) -> bytes:
    """Dosyanın tüm içeriğinin SHA-256 özeti."""
    with p.open("rb") as dosya:
        return hashlib.file_digest(dosya, "sha256").digest()


def _parmak_izi(klasor: Path) -> str:
    """Klasörün içerik imzası: dosya adları ve içerik özetleri. Dosya tarihine bakılmaz: exe her açılışta dosyalarını
    geçici bir klasöre yeni tarihle açar; tarih imzaya girseydi motor her açılışta yeniden kopyalanırdı."""
    h = hashlib.sha256()
    dosyalar = _dosyalar(klasor)
    for p in dosyalar:
        h.update(p.relative_to(klasor).as_posix().encode("utf-8") + b"\0" + _dosya_ozeti(p))
    return f"{len(dosyalar)}:{h.hexdigest()[:40]}"


def _esitle(kaynak: Path, hedef: Path) -> int:
    """Hedef klasörü kaynağın aynısı yapar; yalnızca içeriği farklı olan dosyalara dokunur. Dönüş: kopyalanan dosya sayısı.

    Aynı kalan dosyalar yeniden yazılmaz: Studio açıkken yüklediği kitaplıklar (onnxruntime) kilitlidir ve üstlerine
    yazılamaz. Gerçekten değişmiş bir dosya kilitliyse anlaşılır bir hata verilir. Kaynakta artık olmayan dosyalar silinir;
    silinemeyen (kullanımda olan) eski dosya yerinde kalır."""
    hedef.mkdir(parents=True, exist_ok=True)
    kaynaklar = {p.relative_to(kaynak): p for p in _dosyalar(kaynak)}
    kopya = 0
    for goreli, p in kaynaklar.items():
        h = hedef / goreli
        try:
            ayni = h.is_file() and h.stat().st_size == p.stat().st_size and _dosya_ozeti(h) == _dosya_ozeti(p)
        except OSError:
            ayni = False  # hedef okunamadı: yeniden yazmayı dene
        if ayni:
            continue
        h.parent.mkdir(parents=True, exist_ok=True)
        gecici = h.with_name(h.name + ".yeni")
        try:
            shutil.copyfile(p, gecici)
            os.replace(gecici, h)
        except OSError:
            try:
                gecici.unlink(missing_ok=True)
            except OSError:
                pass
            raise StudioHatasi(f"Motor dosyası güncellenemedi ({goreli.as_posix()}): dosya kullanımda olabilir. "
                               "AugeLab Studio'yu kapatıp yeniden açın, sonra tekrar deneyin.") from None
        kopya += 1
    for h in _dosyalar(hedef):
        if h.relative_to(hedef) not in kaynaklar:
            try:
                h.unlink()
            except OSError:
                pass
    return kopya


def motoru_yerlestir(hedef: Path) -> Path:
    """Motoru (isg_motor, modeller, deps) Studio'nun özel blok klasörünün yanına kopyalar; değişmediyse dokunmaz."""
    if not (MOTOR_KAYNAK / "isg_motor").is_dir():
        raise StudioHatasi(f"Motor kaynağı bulunamadı: {MOTOR_KAYNAK}")
    hedef.mkdir(parents=True, exist_ok=True)
    imzalar = []
    for ad in ("isg_motor", "modeller", "deps"):
        kaynak = MOTOR_KAYNAK / ad
        if not kaynak.is_dir():
            continue
        imza = _parmak_izi(kaynak)
        imzalar.append(imza)
        isaret = hedef / f"{ad}.surum"
        if (hedef / ad).is_dir() and isaret.exists() and isaret.read_text(encoding="ascii") == imza:
            continue
        _esitle(kaynak, hedef / ad)
        isaret.write_text(imza, encoding="ascii")
    surum = hedef / "surum.txt"  # yalnızca değiştiyse yazılır: bloklar bu dosyanın tarihine bakarak motoru yeniden yükler
    if not surum.exists() or surum.read_text(encoding="ascii") != "|".join(imzalar):
        surum.write_text("|".join(imzalar), encoding="ascii")
    (hedef / "ayar.json").write_text(json.dumps({"veri": str(yollar.VERI)}), encoding="utf-8")
    return hedef


async def bloklari_kur() -> Path:
    """İSG bloklarını Studio'ya kaydeder (ya da yeniler) ve motoru yerleştirir. Dönüş: motor klasörü."""
    veri = await cagir("node.custom.create", {"specifications": studio_blok.TANIMLAR, "replaceExisting": True, "idempotencyKey": _anahtar()})
    is_ = await bekle(veri, 120)
    if is_.get("state") != "succeeded":
        hata = is_.get("error") or {}
        raise StudioHatasi(f"İSG blokları Studio'ya eklenemedi: {hata.get('message') or is_.get('state')}")
    dosyalar = [n.get("file_path") for n in (is_.get("result") or {}).get("nodes", []) if n.get("file_path")]
    if not dosyalar:
        raise StudioHatasi("Studio, blokların nereye kaydedildiğini bildirmedi.")
    return motoru_yerlestir(Path(dosyalar[0]).parent.parent / studio_blok.MOTOR_KLASORU)


# ---------------------------------------------------------------- sahne


def _kayitli() -> dict:
    try:
        return json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _kaydet(kod: str, sahne: str | None, tur: str = "") -> None:
    d = _kayitli()
    if sahne is None:
        d.pop(kod, None)
    else:
        d[kod] = {"sahne": sahne, "blok": studio_blok.imza(), "cikis": cikis_imzasi(kod), "tur": tur}
    DURUM_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
    DURUM_DOSYASI.write_text(json.dumps(d), encoding="utf-8")


KURAL_CIKIS = {"alarm": 1, "risk": 2, "olaylar": 3, "yeni": 5, "yaz": 6}  # İSG Kurallar bloğunun çıkış sıraları
OP_MODBUS_BAGLAN, OP_MODBUS_YAZ = "435", "437"
OP_MQTT_YAYIMLA = "4332"
OP_OPCUA_ISTEMCI, OP_OPCUA_YAZ = "412", "414"
OP_S7_BAGLAN, OP_S7_YAZ = "429", "4312"
KAMERA_BASLIGI = "İSG Kamera"


def studio_cikislari(kod: str) -> list[dict]:
    """Bu kameranın sahnesine Studio bloğu olarak eklenecek çıkışlar (Ayarlar → Çıkışlar'da "Studio bloğuyla" işaretli olanlar)."""
    import depo

    return [c for c in depo.genel().get("baglantilar") or []
            if c.get("studio") and c.get("acik", True) and c.get("tur") in ("modbus", "mqtt", "opcua", "s7")
            and (not c.get("kameralar") or kod in c["kameralar"])]


def cikis_imzasi(kod: str) -> str:
    import hashlib

    return hashlib.sha1(json.dumps(studio_cikislari(kod), sort_keys=True).encode("utf-8")).hexdigest()[:12]


def sahne_kaynagi(kod: str, tur: str, cikislar: list[dict] | None = None) -> str:
    if tur == "usb":
        raise StudioHatasi("USB kamera Studio sahnesine henüz bağlanmıyor (Camera USB bloğunun kamerası Studio'da elle seçilir).")
    dosya = tur == "dosya"
    satirlar = [
        f"kod = node('{OP_METIN}', title='{KOD_BASLIGI}')",
        f"kaynak = node('{studio_blok.VIDEO_OP}', title='{studio_blok.VIDEO_BASLIK}')" if dosya else f"kaynak = node('{OP_KAMERA_IP}', title='{KAMERA_BASLIGI}')",
        f"algi = node('{studio_blok.ALGILA_OP}', title='{studio_blok.ALGILA_BASLIK}')",
        f"iskelet = node('{studio_blok.ISKELET_OP}', title='{studio_blok.ISKELET_BASLIK}')",
        f"kural = node('{studio_blok.KURALLAR_OP}', title='{studio_blok.KURALLAR_BASLIK}')",
        f"kod.params['threshold'] = {_tirnak(kod)}",
        "connect(kaynak.out[0], algi.in_[0])",
        "connect(kod.out[0], algi.in_[1])",
        "connect(kaynak.out[0], kural.in_[0])",
        "connect(kaynak.out[0], iskelet.in_[0])",
        "connect(algi.out[0], iskelet.in_[1])",
        "connect(iskelet.out[0], kural.in_[1])",
        "connect(kod.out[0], kural.in_[2])",
    ]
    if dosya:
        satirlar += ["connect(kod.out[0], kaynak.in_[0])", "connect(kaynak.out[1], kural.in_[3])", "connect(kaynak.out[2], kural.in_[4])"]
    C = KURAL_CIKIS
    for i, c in enumerate(cikislar or []):
        ad = _tirnak(f"İSG çıkış · {c.get('ad') or c['tur']}"[:60])
        if c["tur"] == "modbus":
            yazmac = c.get("hedef") == "yazmac"
            satirlar += [
                f"mb{i} = node('{OP_MODBUS_BAGLAN}')", f"mb{i}.params['Ip Address'] = {_tirnak(c['sunucu'])}", f"mb{i}.params['TCP Port'] = {_tirnak(c.get('port') or 502)}",
                f"my{i} = node('{OP_MODBUS_YAZ}', title={ad})", f"my{i}.params['Address'] = {_tirnak(c.get('adres') or 0)}",
                f"my{i}.params['Data Type'] = {_tirnak('Holding Register' if yazmac else 'Coil')}",
                f"connect(kural.out[{C['yaz']}], my{i}.in_[0])", f"connect(mb{i}.out[0], my{i}.in_[1])",
                f"connect(kural.out[{C['risk'] if yazmac else C['alarm']}], my{i}.in_[2])",  # bobine Alarm, yazmaca Risk (0-25) yazılır
            ]
        elif c["tur"] == "mqtt":
            satirlar += [
                f"mq{i} = node('{OP_MQTT_YAYIMLA}', title={ad})", f"mq{i}.params['Address'] = {_tirnak(c['sunucu'])}", f"mq{i}.params['Port'] = {_tirnak(c.get('port') or 1883)}",
                f"mq{i}.params['Topic'] = {_tirnak(c.get('konu') or 'isg/olay')}",  # QoS bloğun varsayılanında (0) kalır: ayar adı tek başına çözülmüyor
                f"connect(kural.out[{C['yeni']}], mq{i}.in_[0])", f"connect(kural.out[{C['olaylar']}], mq{i}.in_[1])",  # yalnızca olay başladığı karede yayımlanır
            ]
        elif c["tur"] == "opcua":
            satirlar += [
                # OPC UA Client bloğu adresi "IP:port" biçiminde ister (opc.tcp:// ve yol olmadan)
                f"oi{i} = node('{OP_OPCUA_ISTEMCI}')", f"oi{i}.params['le1'] = {_tirnak(re.sub(r'^opc\.tcp://', '', c['adres']).split('/')[0])}",
                f"od{i} = node('{OP_METIN}', title={_tirnak(('OPC UA düğümü · ' + (c.get('ad') or ''))[:60])})", f"od{i}.params['threshold'] = {_tirnak(c['dugum'])}",
                f"oy{i} = node('{OP_OPCUA_YAZ}', title={ad})", f"oy{i}.params['pshbttn1'] = {_tirnak(c.get('tip') or 'Float')}",
                f"connect(oi{i}.out[0], oy{i}.in_[0])", f"connect(od{i}.out[0], oy{i}.in_[1])", f"connect(kural.out[{C['risk']}], oy{i}.in_[2])",  # düğüme Risk (0-25, ondalık) yazılır
            ]
        elif c["tur"] == "s7":
            satirlar += [
                f"sb{i} = node('{OP_S7_BAGLAN}')", f"sb{i}.params['Ip Address'] = {_tirnak(c['sunucu'])}", f"sb{i}.params['Rack Address'] = {_tirnak(c.get('raf') or 0)}",
                f"sb{i}.params['Slot'] = {_tirnak(c.get('yuva') or 1)}", f"sb{i}.params['TCP Port'] = {_tirnak(c.get('port') or 102)}",
                f"sy{i} = node('{OP_S7_YAZ}', title={ad})", f"sy{i}.params['Db Number'] = {_tirnak(c.get('db') or 1)}", f"sy{i}.params['Byte Address'] = {_tirnak(c.get('bayt') or 0)}",
                f"sy{i}.params['Bit Position:'] = {_tirnak(c.get('bit') or 0)}", f"sy{i}.params['Data Type'] = 'Boolean'",
                f"connect(kural.out[{C['yaz']}], sy{i}.in_[0])", f"connect(kural.out[{C['alarm']}], sy{i}.in_[1])", f"connect(sb{i}.out[0], sy{i}.in_[3])",
            ]
    return "\n".join(satirlar) + "\n"


async def sahneyi_oku(sahne: str) -> dict | None:
    """Sahnedeki İSG bloklarını başlıklarından bulur. Sahne yoksa ya da bizim değilse None."""
    try:
        g = await cagir("graph.read", {"sceneId": sahne, "view": "graph"})
    except StudioHatasi:
        return None
    satirlar = re.findall(r'^ (n\w+):"([^"]*)" @(\S+)(?: title="([^"]*)")?', g.get("graph", ""), flags=re.M)
    hedefler = g.get("navigationTargets") or [{}]
    ham = dict(zip((s[0] for s in satirlar), hedefler[0].get("nodeIds", [])))
    basliga = {s[3] or s[1]: s[0] for s in satirlar}
    opa = {s[2]: s[0] for s in satirlar}
    if KOD_BASLIGI not in basliga or studio_blok.KURALLAR_BASLIK not in basliga or OP_VIDEO in opa:
        return None  # yok, bizim değil ya da eski düzen (Studio'nun Video bloğuyla kurulmuş)
    return {"sahne": sahne, "sahne_ham": hedefler[0].get("sceneId"), "revizyon": g.get("revision"), "dugumler": basliga, "ham": ham, "op": opa, "metin": g.get("graph", "")}


async def sahne_kur(kod: str, ayar: dict) -> dict:
    """Kameranın sahnesini bulur ya da kurar. Dönüş: sahne bilgisi."""
    await bloklari_kur()
    tur = (ayar.get("kamera") or {}).get("tur", "rtsp")
    eski = _kayitli().get(kod)
    # blok tanımı, kaynak türü ya da Studio çıkışları değiştiyse sahne yeniden kurulur
    if isinstance(eski, dict) and eski.get("blok") == studio_blok.imza() and eski.get("cikis") == cikis_imzasi(kod) and eski.get("tur") == tur:
        bilgi = await sahneyi_oku(eski["sahne"])
        if bilgi:
            return bilgi
    veri = await cagir("graph.program", {"target": "create_new", "expectedRevision": "none", "idempotencyKey": _anahtar(),
                                         "source": sahne_kaynagi(kod, tur, studio_cikislari(kod))})
    bilgi = await sahneyi_oku(veri["scene"])
    if not bilgi:
        raise StudioHatasi("İSG sahnesi Studio'da kurulamadı.")
    _kaydet(kod, veri["scene"], tur)
    return bilgi


async def yeni_kosu(bilgi: dict, kod: str) -> str:
    """Kamera kodu girişine yeni bir koşu numarası yazar: bloklar önceki çalıştırmadan kalan durumu bırakır.
    Dönüş: koşu numarası (motorun kuyruğa bıraktığı kayıtlar bu numarayı taşır)."""
    dugum = bilgi["dugumler"][KOD_BASLIGI]
    kosu = secrets.token_hex(4)
    kaynak = f"ref('{dugum}').params['threshold'] = {_tirnak(kod + '#' + kosu)}"
    for deneme in (1, 2):
        try:
            veri = await cagir("graph.program", {"sceneId": bilgi["sahne"], "expectedRevision": bilgi["revizyon"], "idempotencyKey": _anahtar(), "source": kaynak})
            bilgi["revizyon"] = veri.get("revision", bilgi["revizyon"])
            return kosu
        except StudioHatasi as e:
            if deneme == 2 or ("REVISION" not in str(e).upper() and "CONFLICT" not in str(e).upper()):
                raise
            g = await cagir("graph.read", {"sceneId": bilgi["sahne"], "view": "overview"})
            bilgi["revizyon"] = g["revision"]
    return kosu


async def _duzenle(bilgi: dict, kaynak: str) -> None:
    for deneme in (1, 2):
        try:
            veri = await cagir("graph.program", {"sceneId": bilgi["sahne"], "expectedRevision": bilgi["revizyon"], "idempotencyKey": _anahtar(), "source": kaynak})
            bilgi["revizyon"] = veri.get("revision", bilgi["revizyon"])
            return
        except StudioHatasi as e:
            if deneme == 2 or ("REVISION" not in str(e).upper() and "CONFLICT" not in str(e).upper()):
                raise
            g = await cagir("graph.read", {"sceneId": bilgi["sahne"], "view": "overview"})
            bilgi["revizyon"] = g["revision"]


async def kamera_adresi(bilgi: dict, adres: str) -> None:
    """Camera IP bloğunun adresini yazar. Şifre içerebildiği için sahne durunca boşaltılır (adres = '')."""
    dugum = bilgi["dugumler"].get(KAMERA_BASLIGI)
    if not dugum:
        raise StudioHatasi("Sahnede kamera bloğu yok.")
    await _duzenle(bilgi, f"ref('{dugum}').params['address'] = {_tirnak(adres)}")


async def calistir(bilgi: dict, sure_ms: int | None = None) -> dict:
    """Sahneyi sürekli çalıştırır. `sure_ms` verilirse o kadar sonra kendiliğinden durur."""
    kural = bilgi["ham"][bilgi["dugumler"][studio_blok.KURALLAR_BASLIK]]
    arg = {"sceneId": bilgi["sahne_ham"] or bilgi["sahne"], "mode": "indefinite", "idempotencyKey": _anahtar(), "waitTimeoutMs": 0,
           "observations": [{"key": "durum", "view": "node_output", "nodeId": kural, "outputIndex": 4, "required": False}]}
    if sure_ms:
        arg["stopAfterMs"] = int(sure_ms)
    return await cagir("scene.run.start", arg)


async def durdur(bilgi: dict, dogrula_sn: float = 20.0) -> None:
    """Sahneyi durdurur ve durduğunu doğrular: Studio motorun çalışmadığını bildirene kadar bekler.
    Doğrulanamazsa (durdurma isteği hata verdi, Studio durumu bildirmedi ya da motor hâlâ çalışıyor) StudioHatasi."""
    await cagir("scene.stop", {"sceneId": bilgi["sahne_ham"] or bilgi["sahne"], "idempotencyKey": _anahtar()})
    son = time.monotonic() + dogrula_sn
    while True:
        calisiyor = (await cagir("studio.status.get", {})).get("engineRunning")
        if calisiyor is False:
            return
        if time.monotonic() >= son:
            raise StudioHatasi("Studio sahnenin durduğunu bildirmedi" + (" (motor hâlâ çalışıyor)." if calisiyor else " (motor durumu okunamadı)."))
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------- geliştirme komutları


async def _komut(arg: list[str]) -> None:
    yollar.hazirla()
    if arg[0] == "durum":
        print(await durum())
        return
    kod = arg[1]
    ayar = json.loads((yollar.AYAR / f"{kod}.json").read_text(encoding="utf-8"))
    bilgi = await sahne_kur(kod, ayar)
    if arg[0] == "kur":
        print(bilgi["metin"])
        print({k: v for k, v in bilgi.items() if k not in ("metin",)})
    elif arg[0] == "calistir":
        await yeni_kosu(bilgi, kod)
        veri = await calistir(bilgi, int(float(arg[2]) * 1000) if len(arg) > 2 else None)
        print(json.dumps(veri, ensure_ascii=False)[:1500])
    elif arg[0] == "durdur":
        await durdur(bilgi)
        print("durduruldu")


if __name__ == "__main__":
    asyncio.run(_komut(sys.argv[1:]))
