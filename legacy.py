"""The v0.1.0 data contract, kept for external systems and for migrating old data folders.

v0.1.0 stored and published everything with Turkish names (ASCII): settings keys, camera config keys, database columns,
rule ids, status values and the /api/v1 HTTP API. This version uses English names internally. This module holds the
one mapping between the two:

* old -> new tables (rule ids, categories, zone types, statuses, field names ...) used by migration.py to convert
  settings.json, camera configs, the database and leftover queue records once, and
* new -> old conversions used to keep answering /api/v1 and the v0.1.0 output payloads exactly as v0.1.0 did
  (same paths, query parameters, field names, enum values and CSV columns) from the new internal data.

The Turkish strings in the tables below are data of the old format, not user-visible text.
"""
from __future__ import annotations

import csv
import io
import re

from i18n import N_, T

# ---------------------------------------------------------------- value tables (old -> new)

RULE_IDS = {
    "baret": "hard_hat", "yelek": "vest", "eldiven": "gloves", "gozluk": "safety_glasses", "bot": "safety_shoes", "onluk": "apron",
    "maske": "face_mask", "siper": "face_shield", "kulaklik": "hearing_protection", "kemer": "harness", "tulum": "coveralls",
    "kolluk": "sleeve_guards", "carpisma_bolgesi": "collision_zone", "makine_yetkisiz": "unauthorized_in_machine_zone",
    "en_cok_kisi": "too_many_people", "en_az_kisi": "too_few_people", "yasak_nesne": "prohibited_object",
    "yasak_bolge": "restricted_zone", "saatli_bolge": "after_hours_entry", "bos_kalmamali": "unattended_area",
    "tirmanma": "climbing", "kalabalik": "crowding", "elektrik_bolgesi": "electrical_zone", "uc_nokta": "three_point_contact",
    "kosma": "running", "yaya_yolu_disi": "off_walkway", "kisi_yakinligi": "people_proximity", "yuksekte_calisma": "work_at_height",
    "dusme": "person_down", "arac_yol_disi": "vehicle_off_lane", "arac_yasak_bolge": "vehicle_in_restricted_zone",
    "arac_yaya": "vehicle_pedestrian", "arac_arac": "vehicle_vehicle", "ters_yon": "wrong_way", "arac_yolu_engel": "lane_obstruction",
    "gecit_engel": "blocked_aisle", "sahipsiz_nesne": "abandoned_object", "reba": "reba", "rula": "rula",
}
CATEGORY_IDS = {"kkd": "ppe", "alan": "area", "davranis": "behavior", "arac": "vehicle", "duzen": "housekeeping", "ergonomi": "ergonomics"}
MECHANISMS = {"kkd": "ppe", "durus": "posture", "ergonomi": "ergonomics", "nesne": "object", "uc_nokta": "three_point", "bolge": "zone",
              "sayi": "count", "yakinlik": "proximity", "hiz": "speed", "yol_disi": "off_lane", "ters_yon": "wrong_way",
              "birlikte": "co_presence", "yok": "none"}
RULE_STATUSES = {"hazir": "ready", "sinirli": "limited", "zayif": "weak", "yok": "off"}
EVIDENCE = {"olumsuz": "negative", "yokluk": "absence", "oznitelik": "attribute"}
SUBJECTS = {"kisi": "person", "arac": "vehicle"}
POSTURES = {"yatma": "lying"}
ZONE_TYPES = {"kkd": "ppe", "yasak": "restricted", "makine": "machine", "elektrik": "electrical", "carpisma": "collision",
              "sayim": "headcount", "yaya_yolu": "walkway", "arac_yolu": "vehicle_lane", "arac_yasak": "no_vehicle", "yuksek": "height",
              "tirmanma": "no_climb", "gecit": "aisle", "merdiven": "ladder"}
CLASS_CODES = {
    "kisi": "person", "baret": "hard_hat", "baret_yok": "hard_hat_missing", "yelek": "vest", "yelek_yok": "vest_missing",
    "eldiven": "gloves", "eldiven_yok": "gloves_missing", "gozluk": "safety_glasses", "gozluk_yok": "safety_glasses_missing",
    "bot": "safety_shoes", "bot_yok": "safety_shoes_missing", "forklift": "forklift", "onluk": "apron", "siper": "face_shield",
    "kulaklik": "hearing_protection", "tulum": "coveralls", "kolluk": "sleeve_guards", "kemer": "harness", "maske": "face_mask",
    "maske_yok": "face_mask_missing", "solunum": "respirator", "palet": "pallet", "koli": "box", "varil": "barrel",
    "merdiven": "ladder", "telefon": "phone",
}
COLOR_IDS = {"turuncu": "orange", "kirmizi": "red", "bordo": "maroon", "fosforlu": "hi_vis_yellow", "sari": "yellow", "yesil": "green",
             "turkuaz": "turquoise", "mavi": "blue", "lacivert": "navy", "mor": "purple", "pembe": "pink", "siyah": "black", "beyaz": "white"}
COLOR_REGIONS = {"onluk": "apron", "govde": "torso"}
EVENT_STATUS = {"acik": "open", "ustlenildi": "assigned", "cozuldu": "resolved", "yanlis": "false_alarm"}
TASK_STATUS = {"acik": "open", "suruyor": "in_progress", "kapandi": "closed"}
VERIFICATION_STATUS = {"dogrulandi": "verified", "tekrarladi": "recurred", "bekliyor": "pending"}
LEVELS = {"kritik": "critical", "yuksek": "high", "orta": "medium", "dusuk": "low"}
OUTPUT_TYPES = {"eposta": "email"}
MODBUS_TARGETS = {"bobin": "coil", "yazmac": "register"}
SOURCE_TYPES = {"dosya": "file"}
STREAMS = {"ana": "main", "alt": "sub"}
QUEUE_TYPES = {"olay": "event", "olcum": "measurement", "bitti": "done", "hata": "error", "nabiz": "heartbeat"}
# Display and zone names of the sample cameras of v0.1.0 -> names in samples/cameras/*.json (paths.SAMPLE_NAMES).
SAMPLE_NAMES = {"Depo · Koridor": "Warehouse · Aisle", "Pres atölyesi": "Press shop", "Raf alanı": "Rack area",
                "Yükleme alanı": "Loading area", "Forklift koridoru": "Forklift aisle", "Pres çevresi": "Around the press",
                "Pres operatör alanı": "Press operator area", "Raf yüzeyi": "Rack face", "Raf önü geçidi": "Aisle in front of the rack",
                "Yaya yolu": "Pedestrian walkway", "Forklift yolu": "Forklift lane"}

# ---------------------------------------------------------------- key tables (old -> new)

SETTINGS_KEYS = {"tesis": "site", "vardiyalar": "shifts", "baglantilar": "outputs", "ag_erisimi": "network_access", "api_anahtari": "api_key"}
SHIFT_KEYS = {"ad": "name", "bas": "start", "bit": "end"}
OUTPUT_KEYS = {
    "ad": "name", "tur": "type", "acik": "enabled", "en_az_puan": "min_score", "yalniz_ciddi": "serious_only", "kameralar": "cameras",
    "kategoriler": "categories", "kurallar": "rules", "basliklar": "headers", "sunucu": "host", "konu": "topic", "kalici": "retain",
    "kullanici": "username", "sifre_degiskeni": "password_env", "birim": "unit_id", "hedef": "target", "deger": "value",
    "darbe_sn": "pulse_s", "dugum": "node_id", "tip": "data_type", "gonderen": "sender", "alicilar": "recipients",
}  # "adres" depends on the type: "address" (Modbus register) or "url"
CAMERA_KEYS = {"kod": "code", "ad": "name", "kamera": "source", "kkd_her_yerde": "ppe_everywhere", "bolgeler": "zones", "kurallar": "rules",
               "yetkili_renkleri": "authorized_colors", "calisan_renkleri": "authorized_colors", "alarm_en_az_onem": "alarm_min_severity"}
SOURCE_KEYS = {"tur": "type", "marka": "brand", "kullanici": "username", "kanal": "channel", "akis": "stream", "adres": "url",
               "sifre_degiskeni": "password_env", "profil": "profile", "profil_ad": "profile_name", "kaynak": "video_source",
               "no": "index", "yol": "path", "baslangic": "start_time"}
ZONE_KEYS = {"ad": "name", "tur": "type", "yon_derece": "direction_deg"}
RULE_FIELDS = {
    "kod": "id", "ad": "name", "kategori": "category", "mekanizma": "mechanism", "sinif": "class", "kanit": "evidence", "onem": "severity",
    "ciddi": "serious", "sure_sn": "duration_s", "durum": "status", "aciklama": "description", "bolge_turu": "zone_type",
    "ozne": "subject", "esik": "threshold", "siniflar": "classes", "sahipsiz": "unattended", "saatler": "hours",
    "yetkili_renk": "authorized_color", "en_cok": "max", "en_az": "min", "varsayilan_kapali": "off_by_default",
    "en_az_boy": "min_height", "durus": "posture", "sinama": "validation", "model_eksik": "missing_model", "kapali": "disabled",
    "bolge": "zone",
}
VALIDATION_KEYS = {"yakalanan": "caught", "beklenen": "expected", "yanlis": "false_alarms", "denetim": "checked"}
COLOR_KEYS = {"renk": "color", "esik": "threshold", "ad": "name", "ton": "hue", "doygunluk": "saturation", "parlaklik": "value",
              "bolge": "region", "kod": "id"}

TABLES = {"olay": "event", "olcum": "measurement", "gorev": "task", "gorev_olay": "task_event"}
EVENT_COLUMNS = {
    "id": "id", "kimlik": "uid", "suruyor": "ongoing", "zaman": "time", "kamera": "camera", "kamera_ad": "camera_name", "kural": "rule",
    "kural_ad": "rule_name", "kategori": "category", "bolge": "zone", "onem": "severity", "olasilik": "likelihood", "puan": "score",
    "ciddi": "serious", "sure_sn": "duration_s", "kisi": "people", "x": "x", "y": "y", "klip": "clip", "kare": "snapshot",
    "aciklama": "description", "ornek": "sample", "durum": "status", "ustlenen": "assignee", "notlar": "notes",
    "durum_zamani": "status_time", "parca": "measurement_id",
}
MEASUREMENT_COLUMNS = {
    "id": "id", "kamera": "camera", "kamera_ad": "camera_name", "bas": "start", "sure_sn": "duration_s", "kisi_sn": "person_s",
    "kisi_iz": "people_tracked", "tepe_kisi": "peak_people", "arac_iz": "vehicles_tracked", "kkd_uyumlu_sn": "ppe_compliant_s",
    "kkd_uyumsuz_sn": "ppe_noncompliant_s", "ihlal_sn": "violation_s", "izgara": "grid", "izler": "tracks", "son_kare": "last_frame",
    "kaynak": "source", "ornek": "sample",
}
TASK_COLUMNS = {"id": "id", "baslik": "title", "aciklama": "description", "sorumlu": "owner", "son_tarih": "due_date", "durum": "status",
                "kamera": "camera", "kural": "rule", "bolge": "zone", "olusturma": "created", "kapanma": "closed"}
TASK_EVENT_COLUMNS = {"gorev": "task", "olay": "event"}
COLUMNS = {"event": EVENT_COLUMNS, "measurement": MEASUREMENT_COLUMNS, "task": TASK_COLUMNS, "task_event": TASK_EVENT_COLUMNS}
GRID_KEYS = {"gen": "cols", "yuk": "rows", "kisi": "person", "arac": "vehicle", "olay": "event"}

QUEUE_COMMON = {"tur": "type", "kamera": "camera", "kamera_ad": "camera_name", "kosu": "run"}
QUEUE_EVENT = {"kimlik": "uid", "bitti": "ended", "kural": "rule", "kural_ad": "rule_name", "kategori": "category", "bolge": "zone",
               "onem": "severity", "olasilik": "likelihood", "puan": "score", "ciddi": "serious", "zaman": "time", "bas": "start_s",
               "sure_sn": "duration_s", "kisi": "people", "aciklama": "description", "kare": "snapshot", "klip": "clip",
               "son": "last", "ek": "extra", "sira": "seq", "kapandi": "closed"}
QUEUE_MEASUREMENT = {"bas": "start", "sure_sn": "duration_s", "kisi_sn": "person_s", "kisi_iz": "people_tracked", "tepe_kisi": "peak_people",
                     "arac_iz": "vehicles_tracked", "kkd_uyumlu_sn": "ppe_compliant_s", "kkd_uyumsuz_sn": "ppe_noncompliant_s",
                     "izgara": "grid", "izler": "tracks", "son_kare": "last_frame"}
QUEUE_ERROR = {"metin": "message"}
QUEUE_HEARTBEAT = {"kare": "frames"}

# ---------------------------------------------------------------- v1 (new -> old)


def _rev(m: dict) -> dict:
    out = {}
    for old, new in m.items():
        out.setdefault(new, old)
    return out


V1_RULE_IDS, V1_CATEGORY_IDS, V1_EVENT_STATUS = _rev(RULE_IDS), _rev(CATEGORY_IDS), _rev(EVENT_STATUS)
V1_TASK_STATUS, V1_VERIFICATION_STATUS, V1_LEVELS = _rev(TASK_STATUS), _rev(VERIFICATION_STATUS), _rev(LEVELS)
V1_SOURCE_TYPES, V1_ZONE_TYPES, V1_SUBJECTS = _rev(SOURCE_TYPES), _rev(ZONE_TYPES), _rev(SUBJECTS)
V1_EVENT_FIELDS = {**_rev(EVENT_COLUMNS), "shift": "vardiya", "level": "duzey", "tasks": "gorevler"}
V1_TASK_FIELDS = {**_rev(TASK_COLUMNS), "events": "olaylar", "overdue": "gecikti", "weeks": "haftalar", "verification": "dogrulama"}
V1_CAMERA_FIELDS = {"code": "kod", "name": "ad", "type": "tur", "zones": "bolge", "rules": "kural", "last_analysis": "son_analiz",
                    "last_frame": "son_kare", "setup_frame": "ayar_karesi", "open": "acik", "open_serious": "acik_ciddi",
                    "max_score": "en_yuksek_puan", "sample": "ornek", "needs_password": "sifre"}
V1_SUMMARY_FIELDS = {"from": "bas", "to": "bit", "site": "tesis", "score": "puan", "compliance": "uyum", "monitored_s": "izlenen_sn",
                     "events": "olay", "serious": "ciddi", "open": "acik", "series": "seri", "cameras": "kameralar", "live": "canli"}
V1_SERIES_FIELDS = {"day": "gun", "events": "olay", "serious": "ciddi", "score": "puan", "compliance": "uyum", "monitored_s": "izlenen_sn"}
V1_ANALYTICS_FIELDS = {"from": "bas", "to": "bit", "camera": "kamera", "slices": "parca", "last_frame": "son_kare", "grid": "izgara",
                       "tracks": "izler", "zones": "bolgeler", "points": "noktalar", "counts": "sayilar", "hourly": "saatlik",
                       "rules": "kurallar"}
V1_POINT_FIELDS = {"score": "puan", "rule_name": "kural_ad"}
V1_COUNT_FIELDS = {"people_per_day": "kisi_gun", "peak_people": "tepe_kisi", "vehicles_per_day": "arac_gun", "busiest_hour": "yogun_saat",
                   "compliance": "uyum", "events": "olay", "monitored_s": "izlenen_sn", "riskiest_share": "en_riskli_pay"}
V1_HOURLY_FIELDS = {"hour": "saat", "person_s": "kisi_sn", "events": "olay"}
V1_REPORT_FIELDS = {"from": "bas", "to": "bit", "site": "tesis", "weekly": "haftalik", "score": "puan", "previous_score": "onceki_puan",
                    "events": "olay", "serious": "ciddi", "risk": "risk", "open": "acik", "resolved": "cozulen", "monitored_s": "izlenen_sn",
                    "person_hours": "kisi_saat", "avg_response_min": "ort_yanit_dk", "trend": "egilim", "rules": "kurallar",
                    "cameras": "kameralar", "shifts": "vardiyalar", "categories": "kategoriler", "zones": "bolgeler", "tasks": "gorevler"}
V1_TREND_FIELDS = {"score": "puan", "events": "olay", "serious": "ciddi"}
V1_BREAKDOWN_FIELDS = {"id": "kod", "name": "ad", "serious": "ciddi", "duration_s": "sure_sn", "violation_s": "ihlal_sn", "score": "puan"}
V1_WEEK_FIELDS = {"week": "hafta"}
V1_VERIFICATION_FIELDS = {"events_after": "sonra_olay", "slices_after": "sonra_parca", "status": "durum"}
V1_PARAMS = {"gun": "days", "bas": "from", "bit": "to", "kamera": "camera", "kategori": "category", "kural": "rule", "durum": "status",
             "duzey": "level", "vardiya": "shift", "ciddi": "serious", "sinir": "limit", "sirala": "sort"}

# Fields and CSV columns of v0.1.0, in their order.
V1_CSV_FIELDS = ("id", "zaman", "kamera", "kamera_ad", "kural", "kural_ad", "kategori", "bolge", "vardiya", "onem", "olasilik", "puan",
                 "duzey", "ciddi", "sure_sn", "kisi", "durum", "ustlenen", "notlar", "klip", "kare")
V1_CSV_FILENAME = "olaylar.csv"
V1_OUTPUT_FIELDS = ("id", "zaman", "kamera", "kamera_ad", "kural", "kural_ad", "kategori", "bolge", "onem", "olasilik", "puan", "ciddi",
                    "sure_sn", "kisi", "aciklama", "klip", "kare")
V1_MQTT_TOPIC = "isg/olay"

# /api/v1 endpoint list with the v0.1.0 parameter names (descriptions are translated when shown).
V1_ENDPOINTS = {
    "/api/v1/ozet": N_("Safety score, PPE compliance, event counts, daily series (gun | bas, bit)"),
    "/api/v1/olaylar": N_("Event list (gun | bas, bit, kamera, kategori, kural, durum, duzey, vardiya, ciddi=1, sinir)"),
    "/api/v1/olaylar.csv": N_("The same list as CSV (Excel, Power BI)"),
    "/api/v1/olay/<no>": N_("A single event"),
    "/api/v1/rapor": N_("Breakdowns by rule / camera / shift / zone, and the trend (gun | bas, bit)"),
    "/api/v1/analitik": N_("A camera's heat map, paths and counts (kamera, gun | bas, bit)"),
    "/api/v1/kameralar": N_("Cameras and their latest status"),
    "/api/v1/gorevler": N_("Tasks and their verification status"),
    "/api/v1/medya/<yol>": N_("An event's clip or snapshot (the event's klip / kare field; faces of detected people are masked)"),
}


def v1_endpoints() -> dict:
    """V1_ENDPOINTS with the descriptions in the current language."""
    return {k: T(v) for k, v in V1_ENDPOINTS.items()}


def _keys(d: dict, m: dict) -> dict:
    """Copy of `d` with its keys renamed through `m` (order kept; unknown keys unchanged)."""
    return {m.get(k, k): v for k, v in d.items()}


def _url_v1(url):
    """Panel URLs of v0.1.0: /cikti/... for the output folder and /ayar/_kare_<code>.jpg for the camera setup frame."""
    if not isinstance(url, str):
        return url
    if url.startswith("/output/"):
        return "/cikti/" + url[len("/output/"):]
    m = re.match(r"^/cameras/_frame_(.+)$", url)
    return f"/ayar/_kare_{m.group(1)}" if m else url


def event_to_v1(event: dict) -> dict:
    """An event (database row, optionally with the computed shift / level / tasks) with the v0.1.0 field names and values."""
    out = {}
    for k, v in event.items():
        if k == "rule":
            v = V1_RULE_IDS.get(v, v)
        elif k == "category":
            v = V1_CATEGORY_IDS.get(v, v)
        elif k == "status":
            v = V1_EVENT_STATUS.get(v, v)
        elif k == "level":
            v = V1_LEVELS.get(v, v)
        out[V1_EVENT_FIELDS.get(k, k)] = v
    return out


def events_to_v1(events: list) -> list:
    return [event_to_v1(e) for e in events]


def output_payload_v1(event: dict) -> dict:
    """Body of a webhook / MQTT message in the v0.1.0 shape: {"olay": {...}} (outputs with "legacy_payload": true)."""
    o = event_to_v1(event)
    return {"olay": {a: o.get(a) for a in V1_OUTPUT_FIELDS}}


def camera_to_v1(c: dict) -> dict:
    """An item of the camera list (report.cameras) in the v0.1.0 form."""
    import paths

    out = {}
    for k, v in c.items():
        if k == "type":
            v = V1_SOURCE_TYPES.get(v, v)
        elif k == "name":
            v = paths.sample_name(v)
        elif k in ("last_frame", "setup_frame"):
            v = _url_v1(v)
        out[V1_CAMERA_FIELDS.get(k, k)] = v
    return out


def zone_to_v1(z: dict) -> dict:
    """A zone of a camera config in the v0.1.0 form."""
    import paths

    if not isinstance(z, dict):
        return z
    out = {}
    for k, v in z.items():
        if k == "type":
            v = V1_ZONE_TYPES.get(v, v)
        elif k == "name":
            v = paths.sample_name(v)
        out[_rev(ZONE_KEYS).get(k, k)] = v
    return out


def summary_to_v1(d: dict) -> dict:
    out = _keys(d, V1_SUMMARY_FIELDS)
    out["seri"] = [_keys(s, V1_SERIES_FIELDS) for s in out.get("seri") or []]
    out["kameralar"] = [camera_to_v1(c) for c in out.get("kameralar") or []]
    out["canli"] = events_to_v1(out.get("canli") or [])
    return out


def events_payload_to_v1(d: dict) -> dict:
    return {{"total": "toplam", "events": "olaylar"}.get(k, k): (events_to_v1(v) if k == "events" else v) for k, v in d.items()}


def analytics_to_v1(d: dict) -> dict:
    out = _keys(d, V1_ANALYTICS_FIELDS)
    out["son_kare"] = _url_v1(out.get("son_kare"))
    if isinstance(out.get("izgara"), dict):
        out["izgara"] = _keys(out["izgara"], _rev(GRID_KEYS))
    out["izler"] = [{**t, "s": _rev(SUBJECTS).get(t.get("s"), t.get("s"))} if isinstance(t, dict) and "s" in t else t
                    for t in out.get("izler") or []]
    out["bolgeler"] = [zone_to_v1(z) for z in out.get("bolgeler") or []]
    out["noktalar"] = [_keys(p, V1_POINT_FIELDS) for p in out.get("noktalar") or []]
    if isinstance(out.get("sayilar"), dict):
        out["sayilar"] = _keys(out["sayilar"], V1_COUNT_FIELDS)
    out["saatlik"] = [_keys(h, V1_HOURLY_FIELDS) for h in out.get("saatlik") or []]
    out["kurallar"] = [{("kod" if k == "id" else "ad" if k == "name" else k): (V1_RULE_IDS.get(v, v) if k == "id" else v)
                        for k, v in r.items()} for r in out.get("kurallar") or []]
    return out


def _breakdown_v1(rows: list, ids: dict | None = None) -> list:
    out = []
    for r in rows or []:
        x = _keys(r, V1_BREAKDOWN_FIELDS)
        if ids is not None:
            x["kod"] = ids.get(x.get("kod"), x.get("kod"))
        out.append(x)
    return out


def report_to_v1(d: dict) -> dict:
    out = _keys(d, V1_REPORT_FIELDS)
    out["egilim"] = [_keys(e, V1_TREND_FIELDS) for e in out.get("egilim") or []]
    out["kurallar"] = _breakdown_v1(out.get("kurallar"), V1_RULE_IDS)
    out["kameralar"] = _breakdown_v1(out.get("kameralar"))
    out["vardiyalar"] = _breakdown_v1(out.get("vardiyalar"))
    out["kategoriler"] = _breakdown_v1(out.get("kategoriler"), V1_CATEGORY_IDS)
    out["bolgeler"] = _breakdown_v1(out.get("bolgeler"))
    out["gorevler"] = {V1_TASK_STATUS.get(k, k): n for k, n in (out.get("gorevler") or {}).items()}
    return out


def task_to_v1(t: dict) -> dict:
    out = {}
    for k, v in t.items():
        if k == "status":
            v = V1_TASK_STATUS.get(v, v)
        elif k == "rule":
            v = V1_RULE_IDS.get(v, v)
        elif k == "events":
            v = events_to_v1(v or [])
        elif k == "weeks":
            v = [_keys(w, V1_WEEK_FIELDS) for w in v or []]
        elif k == "verification" and isinstance(v, dict):
            v = {V1_VERIFICATION_FIELDS.get(a, a): (V1_VERIFICATION_STATUS.get(b, b) if a == "status" else b) for a, b in v.items()}
        out[V1_TASK_FIELDS.get(k, k)] = v
    return out


def tasks_to_v1(tasks: list) -> list:
    return [task_to_v1(t) for t in tasks]


def v1_query(q: dict) -> dict:
    """Query parameters of a /api/v1 request (old names and values) as parameters of the new report functions.

    Old values are mapped (kategori=kkd -> category=ppe, durum=acik -> status=open, duzey=kritik -> level=critical,
    kural=baret -> rule=hard_hat, sirala=puan -> sort=score); unknown parameters pass through unchanged."""
    tables = {"kategori": CATEGORY_IDS, "kural": RULE_IDS, "durum": EVENT_STATUS, "duzey": LEVELS, "sirala": {"puan": "score"}}
    out = {}
    for k, v in q.items():
        if k in tables:
            v = tables[k].get(v, v)
        out[V1_PARAMS.get(k, k)] = v
    return out


def events_csv(events: list, fields: tuple) -> str:
    """Events as ';'-separated CSV with the given columns (v0.1.0 and v2 use the same format)."""
    buffer = io.StringIO()
    w = csv.writer(buffer, delimiter=";", lineterminator="\n")
    w.writerow(fields)
    for o in events:
        w.writerow(["" if o.get(a) is None else o[a] for a in fields])
    return buffer.getvalue()


def api_v1(path: str, q: dict):
    """Answers a /api/v1/... request exactly as v0.1.0 did, from the new internal data.

    Returns ("json", data) | ("csv", text) | ("file", Path) | None (not found)."""
    import db
    import paths
    import report

    endpoint = path[len("/api/v1"):].rstrip("/")
    if endpoint == "":
        return "json", {"uygulama": paths.APP_NAME, "surum": paths.VERSION, "uclar": v1_endpoints()}
    q = v1_query(q)
    if endpoint == "/ozet":
        return "json", summary_to_v1(report.summary(q))
    if endpoint in ("/olaylar", "/olaylar.csv"):
        d = events_payload_to_v1(report.events({"limit": 1000, **q}))
        if endpoint == "/olaylar":
            return "json", d
        return "csv", events_csv(d["olaylar"], V1_CSV_FIELDS)
    if endpoint.startswith("/olay/"):
        try:
            rows = db.query("SELECT * FROM event WHERE id = ?", int(endpoint[len("/olay/"):]))
        except ValueError:
            rows = []
        return ("json", event_to_v1(report.localize_event(rows[0]))) if rows else None
    if endpoint == "/rapor":
        return "json", report_to_v1(report.report(q))
    if endpoint == "/analitik":
        return "json", analytics_to_v1(report.analytics(q))
    if endpoint == "/kameralar":
        return "json", {"kameralar": [camera_to_v1(c) for c in report.cameras()]}
    if endpoint == "/gorevler":
        return "json", {"gorevler": tasks_to_v1(report.tasks())}
    if endpoint.startswith("/medya/"):
        target = (paths.OUTPUT / endpoint[len("/medya/"):]).resolve()
        if paths.OUTPUT.resolve() in target.parents and target.suffix.lower() in (".mp4", ".jpg"):
            return "file", target
    return None
