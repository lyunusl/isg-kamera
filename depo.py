"""Veritabanı (SQLite, tek dosya: veri/isg.db): olaylar, görevler, ölçüm parçaları.

Bütün zamanlar yerel saatle ISO biçiminde ("2026-11-11T09:30:00") tutulur.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading

import yollar

_KILIT = threading.RLock()

SEMA = """
CREATE TABLE IF NOT EXISTS olay (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kimlik TEXT,                    -- motorun verdiği kimlik (olay başlarken ve kapanırken aynı)
  suruyor INTEGER NOT NULL DEFAULT 0,  -- olay şu an sürüyor mu
  zaman TEXT NOT NULL,            -- olayın başladığı an
  kamera TEXT NOT NULL,           -- kamera kodu
  kamera_ad TEXT NOT NULL,
  kural TEXT NOT NULL,            -- kural kodu
  kural_ad TEXT NOT NULL,
  kategori TEXT NOT NULL,
  bolge TEXT,                     -- bölge adı (varsa)
  onem INTEGER NOT NULL,          -- 1-5
  olasilik INTEGER NOT NULL,      -- 1-5
  puan INTEGER NOT NULL,          -- onem × olasilik
  ciddi INTEGER NOT NULL,         -- ciddi yaralanma/ölüm potansiyeli
  sure_sn REAL NOT NULL,
  kisi INTEGER NOT NULL,          -- olaya karışan kişi sayısı
  x REAL, y REAL,                 -- görüntüdeki konum (0-1)
  klip TEXT, kare TEXT,           -- cikti/ altındaki göreli yollar (yüzler gizli)
  aciklama TEXT,
  ornek INTEGER NOT NULL DEFAULT 0,
  durum TEXT NOT NULL DEFAULT 'acik',   -- acik | ustlenildi | cozuldu | yanlis
  ustlenen TEXT, notlar TEXT, durum_zamani TEXT,
  parca INTEGER                   -- olcum.id
);
CREATE INDEX IF NOT EXISTS olay_zaman ON olay(zaman);
CREATE INDEX IF NOT EXISTS olay_kamera ON olay(kamera, zaman);
CREATE INDEX IF NOT EXISTS olay_kimlik ON olay(kimlik);

CREATE TABLE IF NOT EXISTS olcum (            -- analiz edilen her kayıt parçası
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kamera TEXT NOT NULL, kamera_ad TEXT NOT NULL,
  bas TEXT NOT NULL, sure_sn REAL NOT NULL,
  kisi_sn REAL NOT NULL,          -- görüntüdeki toplam kişi-saniye
  kisi_iz INTEGER NOT NULL,       -- izlenen kişi sayısı (yaklaşık)
  tepe_kisi INTEGER NOT NULL,
  arac_iz INTEGER NOT NULL,
  kkd_uyumlu_sn REAL NOT NULL, kkd_uyumsuz_sn REAL NOT NULL,
  ihlal_sn REAL NOT NULL,         -- önemle ağırlıklı ihlal kişi-saniyesi
  izgara TEXT NOT NULL,           -- JSON: {"gen": 32, "yuk": 18, "kisi": [...], "arac": [...]}
  izler TEXT NOT NULL,            -- JSON: [{"s": "kisi"|"arac", "p": [[x, y], ...]}]
  son_kare TEXT,
  kaynak TEXT,                    -- analiz edilen dosyanın imzası (aynı dosya yeniden analiz edilirse eskisi silinir)
  ornek INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS olcum_kamera ON olcum(kamera, bas);

CREATE TABLE IF NOT EXISTS gorev (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  baslik TEXT NOT NULL, aciklama TEXT,
  sorumlu TEXT, son_tarih TEXT,
  durum TEXT NOT NULL DEFAULT 'acik',   -- acik | suruyor | kapandi
  kamera TEXT, kural TEXT, bolge TEXT,
  olusturma TEXT NOT NULL, kapanma TEXT
);
CREATE TABLE IF NOT EXISTS gorev_olay (gorev INTEGER NOT NULL, olay INTEGER NOT NULL, PRIMARY KEY (gorev, olay));
"""


def baglan() -> sqlite3.Connection:
    yollar.VERI.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(yollar.VT), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SEMA)
    return c


_VT: sqlite3.Connection | None = None


def vt() -> sqlite3.Connection:
    global _VT
    with _KILIT:
        if _VT is None:
            _VT = baglan()
        return _VT


def kapat() -> None:
    global _VT
    with _KILIT:
        if _VT is not None:
            _VT.close()
            _VT = None


def simdi() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def sor(sql: str, *p) -> list[dict]:
    with _KILIT:
        return [dict(r) for r in vt().execute(sql, p).fetchall()]


def yaz(sql: str, *p) -> int:
    with _KILIT:
        c = vt().execute(sql, p)
        vt().commit()
        return c.lastrowid


def ekle(tablo: str, kayit: dict) -> int:
    alanlar = list(kayit)
    return yaz(f"INSERT INTO {tablo} ({', '.join(alanlar)}) VALUES ({', '.join('?' * len(alanlar))})",
               *[json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in kayit.values()])


def kamera_sil(kod: str, yalniz_ornek: bool = False) -> None:
    ek = " AND ornek = 1" if yalniz_ornek else ""
    with _KILIT:
        vt().execute(f"DELETE FROM gorev_olay WHERE olay IN (SELECT id FROM olay WHERE kamera = ?{ek})", (kod,))
        vt().execute(f"DELETE FROM olay WHERE kamera = ?{ek}", (kod,))
        vt().execute(f"DELETE FROM olcum WHERE kamera = ?{ek}", (kod,))
        vt().commit()


def kaynak_sil(kamera: str, kaynak: str) -> int:
    """Aynı dosyanın önceki analiz sonuçlarını siler (olaylar ve görev bağları dahil). Dönüş: silinen parça sayısı."""
    with _KILIT:
        parcalar = [r["id"] for r in vt().execute("SELECT id FROM olcum WHERE kamera = ? AND kaynak = ?", (kamera, kaynak)).fetchall()]
        for p in parcalar:
            vt().execute("DELETE FROM gorev_olay WHERE olay IN (SELECT id FROM olay WHERE parca = ?)", (p,))
            vt().execute("DELETE FROM olay WHERE parca = ?", (p,))
            vt().execute("DELETE FROM olcum WHERE id = ?", (p,))
        vt().commit()
        return len(parcalar)


# ---------------------------------------------------------------- genel ayarlar

VARSAYILAN_GENEL = {
    "tesis": "Tesis",
    "vardiyalar": [{"ad": "Gündüz", "bas": "08:00", "bit": "16:00"}, {"ad": "Akşam", "bas": "16:00", "bit": "00:00"},
                   {"ad": "Gece", "bas": "00:00", "bit": "08:00"}],
    "baglantilar": [],   # [{"ad": "...", "tur": "webhook", "adres": "https://...", "en_az_puan": 9}]
}


def genel() -> dict:
    try:
        g = json.loads(yollar.GENEL_AYAR.read_text(encoding="utf-8"))
    except Exception:
        g = {}
    return {**VARSAYILAN_GENEL, **g}


def genel_yaz(g: dict) -> None:
    yollar.VERI.mkdir(parents=True, exist_ok=True)
    yollar.GENEL_AYAR.write_text(json.dumps({**genel(), **g}, ensure_ascii=False, indent=1), encoding="utf-8")


def vardiya(zaman: str, vardiyalar: list[dict] | None = None) -> str:
    """Verilen anın düştüğü vardiyanın adı."""
    vardiyalar = vardiyalar or genel()["vardiyalar"]
    dk = int(zaman[11:13]) * 60 + int(zaman[14:16])
    for v in vardiyalar:
        a = int(v["bas"][:2]) * 60 + int(v["bas"][3:5])
        b = int(v["bit"][:2]) * 60 + int(v["bit"][3:5])
        if (a <= dk < b) if a < b else (dk >= a or dk < b):
            return v["ad"]
    return "–"
