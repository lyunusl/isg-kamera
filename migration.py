"""One-time conversion of a v0.1.0 data folder to the current format (format 2).

v0.1.0 used Turkish names on disk: subfolders ayar/ videolar/ cikti/ onbellek/ kuyruk/, genel.json, Turkish keys in the
settings and camera configs, a Turkish database schema and Turkish stored values (rule ids, statuses, categories ...).
migrate() converts all of it in place. It runs at start before anything else touches the data folder and is safe to run
any number of times:

1. A source checkout's veri/ folder is renamed to data/ (the same data).
2. Backup first: genel.json, the camera configs and frames, isg.db, leftover queue records and studio-lisans.txt are
   copied to <data>/backup-0.1.0/ (only files not backed up yet; a ".complete" file closes the backup).
3. Subfolders and files are renamed (cameras/, videos/, output/, cache/, queue/, setup/, studio-license.txt). If an old and a
   new folder both exist, files are moved one by one; nothing is overwritten.
4. genel.json -> settings.json (keys, output configs; migrated outputs get "legacy_payload": true and keep sending the
   v0.1.0 payload), camera configs and their _kare_<code>.jpg frames, leftover queue records.
5. The database: tables are rebuilt with the English schema inside one transaction (rows, ids and the AUTOINCREMENT
   counters are kept; stored values are mapped).
6. <data>/format.json {"format": 2} is written last. With the marker present only late queue leftovers are checked.

Nothing is deleted: files that cannot be moved without overwriting stay where they are, unknown files and keys are kept
and the original files remain in the backup. The mapping tables live in legacy.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import legacy
import paths

FORMAT = 2
BACKUP_DIR = "backup-0.1.0"
# kurulum/ is the download cache of the Studio and Visual C++ installers (studio_setup.py now uses setup/)
OLD_FOLDERS = {"ayar": "cameras", "videolar": "videos", "cikti": "output", "onbellek": "cache", "kuyruk": "queue", "kurulum": "setup"}
OLD_FILES = {"genel.json": "settings.json", "studio-lisans.txt": "studio-license.txt"}
OLD_ARTIFACTS = tuple(OLD_FOLDERS) + tuple(OLD_FILES)
NEW_ARTIFACTS = tuple(OLD_FOLDERS.values()) + ("settings.json", "studio-license.txt", "isg.db", "studio.json", "studio", "format.json")


class MigrationError(Exception):
    """The data folder could not be converted; it is left as it was (the next start tries again)."""


# ---------------------------------------------------------------- converters (pure functions, also used for samples)


def _map(v, table: dict):
    return table.get(v, v) if isinstance(v, str) else v


def _map_list(v, table: dict):
    return [_map(x, table) for x in v] if isinstance(v, list) else _map(v, table)


def convert_output(o: dict, mark_legacy: bool = True) -> dict:
    """An output config of genel.json "baglantilar" in the new form. Values that are user data (names, addresses, env
    var names, camera codes) are kept; type, Modbus target, category and rule ids are mapped."""
    if not isinstance(o, dict):
        return o
    kind = _map(o.get("tur", o.get("type")), legacy.OUTPUT_TYPES)
    out = {}
    for k, v in o.items():
        if k == "adres":
            k = "address" if kind == "modbus" else "url"
        else:
            k = legacy.OUTPUT_KEYS.get(k, k)
        if k == "type":
            v = _map(v, legacy.OUTPUT_TYPES)
        elif k == "target":
            v = _map(v, legacy.MODBUS_TARGETS)
        elif k == "categories":
            v = _map_list(v, legacy.CATEGORY_IDS)
        elif k == "rules":
            v = _map_list(v, legacy.RULE_IDS)
        out[k] = v
    if kind == "mqtt" and not out.get("topic"):
        out["topic"] = legacy.V1_MQTT_TOPIC  # v0.1.0 default topic: subscribers keep receiving
    if mark_legacy and any(k in o for k in ("tur", "ad", "adres", "acik", "sunucu")):
        out.setdefault("legacy_payload", True)
    return out


def convert_settings(g: dict) -> dict:
    """genel.json content (v0.1.0) as settings.json content. Already converted content stays the same."""
    if not isinstance(g, dict):
        return {}
    out = {}
    for k, v in g.items():
        new = legacy.SETTINGS_KEYS.get(k, k)
        if new != k and new in g:
            continue  # both names present (hand-edited file): the new key wins
        if new == "shifts" and isinstance(v, list):
            v = [{legacy.SHIFT_KEYS.get(a, a): b for a, b in s.items()} if isinstance(s, dict) else s for s in v]
        elif new == "outputs" and isinstance(v, list):
            v = [convert_output(b) for b in v]
        out[new] = v
    return out


def _convert_url(url):
    if not isinstance(url, str):
        return url
    return url.replace("{kullanici}", "{user}").replace("{sifre}", "{password}")


def convert_source(s: dict) -> dict:
    """The "kamera" (connection) object of a camera config in the new form."""
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "sifre":
            continue  # passwords are never stored (v0.1.0 did not store them either)
        k = legacy.SOURCE_KEYS.get(k, k)
        if k == "type":
            v = _map(v, legacy.SOURCE_TYPES)
        elif k == "stream":
            v = _map(v, legacy.STREAMS)
        elif k == "url":
            v = _convert_url(v)
        elif k == "path" and isinstance(v, str):
            for old, new in (("videolar/", "videos/"), ("videolar\\", "videos\\")):
                if v.startswith(old):
                    v = new + v[len(old):]
        out[k] = v
    return out


def convert_zone(z: dict) -> dict:
    if not isinstance(z, dict):
        return z
    return {legacy.ZONE_KEYS.get(k, k): (_map(v, legacy.ZONE_TYPES) if legacy.ZONE_KEYS.get(k, k) == "type" else v)
            for k, v in z.items()}


_RULE_VALUES = {"id": legacy.RULE_IDS, "category": legacy.CATEGORY_IDS, "mechanism": legacy.MECHANISMS, "class": legacy.CLASS_CODES,
                "classes": legacy.CLASS_CODES, "evidence": legacy.EVIDENCE, "status": legacy.RULE_STATUSES,
                "zone_type": legacy.ZONE_TYPES, "subject": legacy.SUBJECTS, "a": legacy.SUBJECTS, "b": legacy.SUBJECTS,
                "posture": legacy.POSTURES}


def convert_rule(r: dict) -> dict:
    """A per-camera rule entry ({"kod": "baret", "sure_sn": 3, "bolge": "Press line"}) in the new form."""
    if not isinstance(r, dict):
        return r
    out = {}
    for k, v in r.items():
        k = legacy.RULE_FIELDS.get(k, k)
        if k in _RULE_VALUES:
            v = _map_list(v, _RULE_VALUES[k])
        elif k == "validation" and isinstance(v, dict):
            v = {legacy.VALIDATION_KEYS.get(a, a): b for a, b in v.items()}
        out[k] = v
    return out


def convert_color(c):
    """An authorized clothing color: a preset id or a custom color dict."""
    if isinstance(c, str):
        return _map(c, legacy.COLOR_IDS)
    if not isinstance(c, dict):
        return c
    out = {}
    for k, v in c.items():
        k = legacy.COLOR_KEYS.get(k, k)
        if k in ("color", "id"):
            v = _map(v, legacy.COLOR_IDS)
        elif k == "region":
            v = _map(v, legacy.COLOR_REGIONS)
        out[k] = v
    return out


def convert_camera_config(a: dict) -> dict:
    """A camera config (ayar/<kod>.json of v0.1.0) in the new form. Already converted configs stay the same; camera codes,
    names, zone names and password variable names are user data and are kept."""
    if not isinstance(a, dict):
        return a
    out = {}
    for k, v in a.items():
        if k == "sifre":
            continue
        k = legacy.CAMERA_KEYS.get(k, k)
        if k == "source":
            v = convert_source(v)
        elif k == "zones" and isinstance(v, list):
            v = [convert_zone(z) for z in v]
        elif k == "rules" and isinstance(v, list):
            v = [convert_rule(r) for r in v]
        elif k == "authorized_colors" and isinstance(v, list):
            v = [convert_color(c) for c in v]
        out[k] = v
    return out


def convert_grid(g):
    """measurement.grid JSON: {"gen", "yuk", "kisi", "arac"} -> {"cols", "rows", "person", "vehicle"}."""
    if not isinstance(g, dict):
        return g
    return {legacy.GRID_KEYS.get(k, k): v for k, v in g.items()}


def convert_tracks(t):
    """measurement.tracks JSON: [{"s": "kisi" | "arac", "p": [...]}] -> "person" | "vehicle"."""
    if not isinstance(t, list):
        return t
    return [{**x, "s": _map(x["s"], legacy.SUBJECTS)} if isinstance(x, dict) and "s" in x else x for x in t]


def convert_queue_record(r: dict) -> dict:
    """A record the v0.1.0 engine left in kuyruk/ in the form the current queue reader expects."""
    if not isinstance(r, dict) or "tur" not in r:
        return r
    kind = _map(r.get("tur"), legacy.QUEUE_TYPES)
    specific = {"event": legacy.QUEUE_EVENT, "measurement": legacy.QUEUE_MEASUREMENT, "error": legacy.QUEUE_ERROR,
                "heartbeat": legacy.QUEUE_HEARTBEAT}.get(kind, {})
    out = {}
    for k, v in r.items():
        k = legacy.QUEUE_COMMON.get(k) or specific.get(k, k)
        if k == "type":
            v = kind
        elif k == "rule":
            v = _map(v, legacy.RULE_IDS)
        elif k == "category":
            v = _map(v, legacy.CATEGORY_IDS)
        elif k == "grid":
            v = convert_grid(v)
        elif k == "tracks":
            v = convert_tracks(v)
        out[k] = v
    return out


# ---------------------------------------------------------------- file helpers


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _move_tree(old: Path, new: Path, notes: list) -> None:
    """Renames folder `old` to `new`; if `new` exists, moves the files one by one without overwriting."""
    if not old.is_dir():
        return
    if not new.exists():
        os.replace(old, new)
        return
    for item in sorted(old.iterdir()):
        target = new / item.name
        if item.is_dir() and target.is_dir():
            _move_tree(item, target, notes)
        elif not target.exists():
            os.replace(item, target)
        else:
            notes.append(f"kept {item} (the new folder already has {target.name})")
    try:
        old.rmdir()
    except OSError:
        pass


def _copy_new(src: Path, dst: Path) -> None:
    """Copies a file to the backup unless it is already there (copy to .tmp, then rename: no half files)."""
    if not src.is_file() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _checkpoint(db_path: Path) -> None:
    """Writes the WAL into the database file so a file copy is complete."""
    if not db_path.is_file():
        return
    c = sqlite3.connect(str(db_path), timeout=30)
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()


def _old_tables(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    c = sqlite3.connect(str(db_path), timeout=30)
    try:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        c.close()
    return names & set(legacy.TABLES)


# ---------------------------------------------------------------- state


def has_artifacts(data: Path) -> bool:
    """Does the folder hold anything of an installation (old or new layout)?"""
    return data.is_dir() and any((data / n).exists() for n in OLD_ARTIFACTS + NEW_ARTIFACTS)


def is_current(data: Path | None = None) -> bool:
    data = Path(data) if data is not None else paths.DATA
    m = _read_json(data / "format.json")
    return isinstance(m, dict) and isinstance(m.get("format"), int) and m["format"] >= FORMAT


def pending(data: Path | None = None, legacy_dir: Path | None = None) -> bool:
    """Is there old data to convert?"""
    data = Path(data) if data is not None else paths.DATA
    if legacy_dir is None and data == paths.DATA:
        legacy_dir = paths.LEGACY_SOURCE_DATA
    if legacy_dir is not None and Path(legacy_dir).is_dir() and not data.exists():
        return True
    if (data / "kuyruk").is_dir():
        return True
    return not is_current(data) and (any((data / n).exists() for n in OLD_ARTIFACTS) or bool(_old_tables(data / "isg.db")))


# ---------------------------------------------------------------- steps


def _backup(data: Path) -> Path:
    backup = data / BACKUP_DIR
    if (backup / ".complete").exists():
        return backup
    _copy_new(data / "genel.json", backup / "genel.json")
    _copy_new(data / "studio-lisans.txt", backup / "studio-lisans.txt")
    if (data / "ayar").is_dir():
        for f in sorted((data / "ayar").iterdir()):
            _copy_new(f, backup / "ayar" / f.name)
    if (data / "kuyruk").is_dir():
        for f in sorted((data / "kuyruk").glob("*.json")):
            _copy_new(f, backup / "kuyruk" / f.name)
    if _old_tables(data / "isg.db"):
        _checkpoint(data / "isg.db")
        _copy_new(data / "isg.db", backup / "isg.db")
    backup.mkdir(parents=True, exist_ok=True)
    (backup / ".complete").write_text("v0.1.0 files as they were before the conversion\n", encoding="utf-8")
    return backup


def _migrate_settings(data: Path, notes: list) -> None:
    old, new = data / "genel.json", data / "settings.json"
    if not old.exists():
        current = _read_json(new)
        if isinstance(current, dict) and any(k in current for k in legacy.SETTINGS_KEYS):
            _write_json(new, convert_settings(current))
        return
    g = _read_json(old)
    if g is None:
        notes.append("genel.json could not be read; it was left in place")
        return
    current = _read_json(new) if new.exists() else {}
    merged = {**convert_settings(g), **convert_settings(current if isinstance(current, dict) else {})}
    _write_json(new, merged)
    done = data / "genel.json.migrated"
    if not done.exists():
        os.replace(old, done)


def _shipped_sample(config: dict, code: str):
    """The shipped sample config if `config` is an unedited copy of the v0.1.0 sample with the same code (only the
    Turkish display and zone names differ); else None. Edited samples stay as the user left them."""
    shipped = _read_json(paths.SAMPLES / "cameras" / f"{code}.json")
    if not isinstance(shipped, dict):
        return None
    names = legacy.SAMPLE_NAMES
    renamed = {**config, "name": names.get(config.get("name"), config.get("name"))}
    if isinstance(config.get("zones"), list):
        renamed["zones"] = [{**z, "name": names.get(z.get("name"), z.get("name"))} if isinstance(z, dict) else z for z in config["zones"]]
    return shipped if renamed == shipped and renamed != config else None


def _migrate_cameras(data: Path) -> None:
    folder = data / "cameras"
    if not folder.is_dir():
        return
    samples = set(paths.sample_codes())
    for f in sorted(folder.glob("*.json")):
        a = _read_json(f)
        if not isinstance(a, dict):
            continue
        b = convert_camera_config(a)
        if f.stem in samples:
            b = _shipped_sample(b, f.stem) or b
        if b != a:
            _write_json(f, b)
    for f in sorted(folder.glob("_kare_*.jpg")):
        target = f.with_name("_frame_" + f.name[len("_kare_"):])
        if not target.exists():
            os.replace(f, target)


def _migrate_queue(data: Path, notes: list) -> None:
    _move_tree(data / "kuyruk", data / "queue", notes)
    folder = data / "queue"
    if not folder.is_dir():
        return
    for f in sorted(folder.glob("*.json")):
        r = _read_json(f)
        if not isinstance(r, dict) or "tur" not in r:
            continue
        n = convert_queue_record(r)
        clip = n.get("clip")
        if isinstance(clip, str) and clip.endswith(".ham.mp4"):  # raw clip of the old engine: the reader converts *.raw.mp4
            src = data / "output" / clip
            raw = clip[:-len(".ham.mp4")] + ".raw.mp4"
            if src.exists() and not (data / "output" / raw).exists():
                os.replace(src, data / "output" / raw)
            if (data / "output" / raw).exists():
                n["clip"] = raw
        _write_json(f, n)


def _schema_statements() -> list[str]:
    import db

    return [s.strip() for s in db.SCHEMA.split(";") if s.strip()]


def migrate_db(db_path: Path) -> bool:
    """Rebuilds the v0.1.0 tables with the English schema in one transaction. Returns True if anything was converted.

    Each old table is copied into a new table created from db.SCHEMA (so column defaults are the new values), ids and the
    AUTOINCREMENT counter are kept, stored values (event / task status, category and rule ids, grid and track JSON)
    are mapped, then the old table is dropped. On any error the transaction is rolled back and the file is unchanged."""
    if not _old_tables(db_path):
        return False
    statements = _schema_statements()
    c = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("BEGIN IMMEDIATE")
        try:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for old, new in legacy.TABLES.items():
                if old not in tables:
                    continue
                if new in tables:
                    if c.execute(f"SELECT COUNT(*) FROM {new}").fetchone()[0]:
                        raise MigrationError(f"both tables {old} and {new} hold rows")
                    c.execute(f"DROP TABLE {new}")
                create = next(s for s in statements if s.upper().startswith("CREATE TABLE") and
                              s.split("(")[0].split()[-1] == new)
                c.execute(create)
                old_cols = [r[1] for r in c.execute(f"PRAGMA table_info({old})")]
                new_cols = {r[1] for r in c.execute(f"PRAGMA table_info({new})")}
                pairs = [(o, legacy.COLUMNS[new].get(o, o)) for o in old_cols if legacy.COLUMNS[new].get(o, o) in new_cols]
                c.execute(f"INSERT INTO {new} ({', '.join(n for _, n in pairs)}) SELECT {', '.join(o for o, _ in pairs)} FROM {old}")
                if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'").fetchone():
                    seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (old,)).fetchone()
                    if seq is not None:  # keep the counter: ids of deleted rows are never given again
                        c.execute("DELETE FROM sqlite_sequence WHERE name = ?", (new,))
                        c.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (new, seq[0]))
                        c.execute("DELETE FROM sqlite_sequence WHERE name = ?", (old,))
                c.execute(f"DROP TABLE {old}")
            for s in statements:
                if s.upper().startswith("CREATE"):
                    c.execute(s)  # missing tables and the indexes (IF NOT EXISTS)

            def remap(table: str, column: str, mapping: dict) -> None:
                pairs = [(o, n) for o, n in mapping.items() if o != n]
                if pairs:
                    case = " ".join("WHEN ? THEN ?" for _ in pairs)
                    c.execute(f"UPDATE {table} SET {column} = CASE {column} {case} ELSE {column} END",
                              [x for p in pairs for x in p])

            remap("event", "status", legacy.EVENT_STATUS)
            remap("event", "category", legacy.CATEGORY_IDS)
            remap("event", "rule", legacy.RULE_IDS)
            remap("task", "status", legacy.TASK_STATUS)
            remap("task", "rule", legacy.RULE_IDS)
            for row_id, grid, tracks in c.execute("SELECT id, grid, tracks FROM measurement").fetchall():
                try:
                    g2 = json.dumps(convert_grid(json.loads(grid)), ensure_ascii=False) if grid else grid
                    t2 = json.dumps(convert_tracks(json.loads(tracks)), ensure_ascii=False) if tracks else tracks
                except ValueError:
                    continue
                if (g2, t2) != (grid, tracks):
                    c.execute("UPDATE measurement SET grid = ?, tracks = ? WHERE id = ?", (g2, t2, row_id))
            c.execute(f"PRAGMA user_version = {FORMAT}")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()
    return True


# ---------------------------------------------------------------- entry point


def migrate(data: Path | None = None, legacy_dir: Path | None = None) -> dict:
    """Converts the data folder if needed. Idempotent; call it before anything else uses the data folder.

    `data` defaults to paths.DATA, `legacy_dir` to the source checkout's veri/ folder (paths.LEGACY_SOURCE_DATA).
    Returns {"existed": bool (old or new artifacts were there before), "migrated": bool, "backup": str | None,
    "notes": [str]}."""
    data = Path(data) if data is not None else paths.DATA
    if legacy_dir is None and data == paths.DATA:
        legacy_dir = paths.LEGACY_SOURCE_DATA
    notes: list[str] = []
    result = {"existed": False, "migrated": False, "backup": None, "notes": notes}
    if legacy_dir is not None and Path(legacy_dir).is_dir():
        if not data.exists():
            data.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy_dir, data)
            notes.append(f"renamed {Path(legacy_dir).name}/ to {data.name}/")
        elif Path(legacy_dir).resolve() != data.resolve():
            notes.append(f"both {Path(legacy_dir).name}/ and {data.name}/ exist; {Path(legacy_dir).name}/ was left as it is")
    result["existed"] = has_artifacts(data)
    if is_current(data):
        if (data / "kuyruk").is_dir():  # records an old engine wrote after the conversion
            _backup_late_queue(data)
            _migrate_queue(data, notes)
        return result
    data.mkdir(parents=True, exist_ok=True)
    old = any((data / n).exists() for n in OLD_ARTIFACTS) or bool(_old_tables(data / "isg.db"))
    if old:
        result["backup"] = str(_backup(data))
        for o, n in OLD_FOLDERS.items():
            if o != "kuyruk":
                _move_tree(data / o, data / n, notes)
        if (data / "studio-lisans.txt").exists() and not (data / "studio-license.txt").exists():
            os.replace(data / "studio-lisans.txt", data / "studio-license.txt")
        _migrate_settings(data, notes)
        _migrate_cameras(data)
        _migrate_queue(data, notes)
        migrate_db(data / "isg.db")
        result["migrated"] = True
    _write_json(data / "format.json", {"format": FORMAT})
    return result


def _backup_late_queue(data: Path) -> None:
    for f in sorted((data / "kuyruk").glob("*.json")):
        _copy_new(f, data / BACKUP_DIR / "kuyruk" / f.name)


if __name__ == "__main__":
    import sys

    r = migrate(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(json.dumps(r, ensure_ascii=False, indent=1))
