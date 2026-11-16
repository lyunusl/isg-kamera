"""Summaries from the database for the screens: Control Room, Alerts, Visual Analytics, Reports, Excel.

Safety score (0-100) = 100 × (1 − weighted violation time / monitored person-time)
  monitored person-time: total time people were seen in the image (people × seconds)
  weighted violation time: for each event, duration × people × severity/5 (events marked "false alarm" are not counted)

Events are stored with the rule name and description in the language the engine ran in. When they are returned, the
human texts are rendered again in the current language from the rule id (localize_event); unknown or changed texts are
returned as stored.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import string
from collections import defaultdict
from pathlib import Path

import db
import paths
import rules
from i18n import N_, T, catalog, language

# Texts the engine builds around a rule description (engine/isg_core/rule_engine.py). They are parsed back from the stored
# text in English or Turkish and rendered again in the current language.
REBA_TEXT = N_("REBA posture score {score} (threshold {threshold}). ")
COUNT_TEXT = N_("{zone}: {n} people seen (limit: {limit}).")
COUNT_TEXT_ONE = N_("{zone}: {n} person seen (limit: {limit}).")
COUNT_MAX_TEXT = N_("at most {n}")
COUNT_MIN_TEXT = N_("at least {n}")

STATUS_NAMES = {"open": N_("open"), "assigned": N_("assigned"), "resolved": N_("resolved"), "false_alarm": N_("false alarm")}
TASK_STATUS_NAMES = {"open": N_("open"), "in_progress": N_("in progress"), "closed": N_("closed")}
VERIFICATION_NAMES = {"verified": N_("verified by camera"), "recurred": N_("recurred"), "pending": N_("awaiting monitoring")}

_patterns: dict[str, re.Pattern] = {}


def _pattern(template: str) -> re.Pattern:
    """Regular expression for a template: the literal parts as they are, each {placeholder} as a named group."""
    if template not in _patterns:
        parts = []
        for literal, name, _, _ in string.Formatter().parse(template):
            parts.append(re.escape(literal))
            if name:
                parts.append(f"(?P<{name}>.+?)")
        _patterns[template] = re.compile("".join(parts), re.S)
    return _patterns[template]


def _variants(source: str) -> list[str]:
    """The English source text and its Turkish catalogue entry (if any)."""
    tr = catalog().get(source)
    return [source] + ([tr] if tr and tr != source else [])


def _parse(text: str, source: str, full: bool = True):
    """Values of a stored text written from template `source` (in English or Turkish): (values, rest) or None."""
    for template in _variants(source):
        pattern = _pattern(template)
        m = pattern.fullmatch(text) if full else pattern.match(text)
        if m:
            return m.groupdict(), text[m.end():]
    return None


def _translate(text, source: str | None):
    """The stored text in the current language if it is the source text or its Turkish entry; otherwise None."""
    if source and isinstance(text, str) and text in _variants(source):
        return T(source)
    return None


def category_name(code) -> str:
    """Label of a rule category in the current language (the id itself if it is unknown)."""
    name = rules.CATEGORIES.get(code)
    return T(name) if name else code


def _rule_id(rule_id):
    """A rule id from a request; old (v0.1.0) ids are mapped to the new ones."""
    return getattr(rules, "LEGACY_RULE_IDS", {}).get(rule_id, rule_id)


def _description(text, k: dict):
    if not isinstance(text, str) or not text:
        return text
    whole = _translate(text, k.get("description"))
    if whole is not None:
        return whole
    if k.get("mechanism") == "ergonomics":
        r = _parse(text, REBA_TEXT, full=False)
        if r:
            rest = _translate(r[1], k.get("description"))
            if rest is not None:
                return T(REBA_TEXT, score=r[0]["score"], threshold=r[0]["threshold"]) + rest
        return text
    if k.get("mechanism") == "count":
        r = _parse(text, COUNT_TEXT) or _parse(text, COUNT_TEXT_ONE)
        if r:
            for limit in (COUNT_MAX_TEXT, COUNT_MIN_TEXT):
                s = _parse(r[0]["limit"], limit)
                if s:
                    template = COUNT_TEXT_ONE if r[0]["n"] == "1" else COUNT_TEXT
                    return T(template, zone=paths.sample_name(r[0]["zone"]), n=r[0]["n"], limit=T(limit, n=s[0]["n"]))
        return text
    # A description from another version of the catalogue: Turkish stays as stored, other languages get today's text.
    return text if language() == "tr" else T(k["description"]) if k.get("description") else text


def localize_event(o: dict) -> dict:
    """Renders the human texts of an event (rule name, description) in the current language from its rule id.

    Changes `o` in place and returns it. Unknown rules and texts that do not match the catalogue (renamed rules, older
    versions) keep the stored text. Names of the sample cameras and their zones are shown in the current language."""
    for field in ("camera_name", "zone"):
        if o.get(field):
            o[field] = paths.sample_name(o[field])
    k = rules.RULE.get(o.get("rule"))
    if k is None:
        return o
    if "rule_name" in o:
        name = _translate(o["rule_name"], k.get("name"))
        if name is not None:
            o["rule_name"] = name
    if "description" in o:
        o["description"] = _description(o["description"], k)
    return o


def date_range(q: dict) -> tuple[str, str]:
    """Time range from the request: from/to (YYYY-MM-DD) or the last `days` days."""
    now = dt.datetime.now()
    if q.get("from") and q.get("to"):
        return q["from"][:10] + "T00:00:00", q["to"][:10] + "T23:59:59"
    try:
        days = max(1, min(730, int(q.get("days", 7))))
    except (TypeError, ValueError):
        days = 7
    start = (now - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.replace(microsecond=0).isoformat()


def safety_score(person_s: float, violation_s: float) -> int | None:
    if person_s <= 0:
        return None
    return int(round(100 * max(0.0, 1 - violation_s / person_s)))


def _violation(o: dict) -> float:
    return o["duration_s"] * o["people"] * o["severity"] / 5


def _events(start: str, end: str, camera: str | None = None, false_alarms: bool = False) -> list[dict]:
    sql, p = "SELECT * FROM event WHERE time >= ? AND time <= ?", [start, end]
    if camera:
        sql += " AND camera = ?"
        p.append(camera)
    if not false_alarms:
        sql += " AND status != 'false_alarm'"
    out = db.query(sql + " ORDER BY time DESC, id DESC", *p)
    shifts = db.settings()["shifts"]
    for o in out:
        o["shift"] = db.shift_of(o["time"], shifts)
        o["level"] = rules.level(o["score"])
        localize_event(o)
    return out


def _measurements(start: str, end: str, camera: str | None = None, full: bool = False) -> list[dict]:
    columns = "*" if full else ("id, camera, camera_name, start, duration_s, person_s, people_tracked, peak_people, vehicles_tracked, "
                                "ppe_compliant_s, ppe_noncompliant_s, last_frame")
    sql, p = f"SELECT {columns} FROM measurement WHERE start >= ? AND start <= ?", [start, end]
    if camera:
        sql += " AND camera = ?"
        p.append(camera)
    return db.query(sql + " ORDER BY start", *p)


def cameras() -> list[dict]:
    """Cameras that have a settings file, and their latest status."""
    out = []
    latest = {r["camera"]: r for r in db.query("SELECT camera, MAX(start) AS start, last_frame FROM measurement GROUP BY camera")}
    open_ = {r["camera"]: r for r in db.query(
        "SELECT camera, COUNT(*) AS n, SUM(serious) AS serious, MAX(score) AS score FROM event WHERE status = 'open' GROUP BY camera")}
    for path in sorted(paths.CAMERAS.glob("*.json")):
        if path.stem.startswith("_"):
            continue
        try:
            a = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        code = a.get("code") or path.stem
        s, op = latest.get(code), open_.get(code, {})
        frame = paths.CAMERAS / f"_frame_{code}.jpg"
        out.append({
            "code": code, "name": paths.sample_name(a.get("name", code)), "type": (a.get("source") or {}).get("type", "rtsp"),
            "zones": len(a.get("zones") or []), "rules": len(rules.resolve(a)),
            "last_analysis": s["start"] if s else None, "last_frame": f"/output/{s['last_frame']}" if s and s["last_frame"] else None,
            "setup_frame": f"/cameras/{frame.name}?t={int(frame.stat().st_mtime)}" if frame.exists() else None,
            "open": op.get("n", 0), "open_serious": op.get("serious") or 0, "max_score": op.get("score") or 0,
            "sample": code in paths.sample_codes(),
        })
    return out


def _days(start: str, end: str) -> list[str]:
    a, b = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def summary(q: dict) -> dict:
    """Control Room."""
    start, end = date_range(q)
    events_, measurements = _events(start, end), _measurements(start, end)
    daily = {d: {"day": d, "events": 0, "serious": 0, "person_s": 0.0, "violation_s": 0.0, "compliant": 0.0, "noncompliant": 0.0, "monitored_s": 0.0}
             for d in _days(start, end)}
    for m in measurements:
        d = daily.get(m["start"][:10])
        if d:
            d["person_s"] += m["person_s"]
            d["monitored_s"] += m["duration_s"]
            d["compliant"] += m["ppe_compliant_s"]
            d["noncompliant"] += m["ppe_noncompliant_s"]
    for o in events_:
        d = daily.get(o["time"][:10])
        if d:
            d["events"] += 1
            d["serious"] += o["serious"]
            d["violation_s"] += _violation(o)
    series = []
    for d in daily.values():
        total = d["compliant"] + d["noncompliant"]
        series.append({"day": d["day"], "events": d["events"], "serious": d["serious"], "score": safety_score(d["person_s"], d["violation_s"]),
                       "compliance": round(100 * d["compliant"] / total, 1) if total > 0 else None, "monitored_s": round(d["monitored_s"])})
    person_s = sum(m["person_s"] for m in measurements)
    compliant, noncompliant = sum(m["ppe_compliant_s"] for m in measurements), sum(m["ppe_noncompliant_s"] for m in measurements)
    open_events = sorted((o for o in events_ if o["status"] == "open"), key=lambda o: (-o["score"], o["time"]), reverse=False)
    return {
        "from": start, "to": end, "site": db.settings()["site"],
        "score": safety_score(person_s, sum(_violation(o) for o in events_)),
        "compliance": round(100 * compliant / (compliant + noncompliant), 1) if compliant + noncompliant > 0 else None,
        "monitored_s": round(sum(m["duration_s"] for m in measurements)), "events": len(events_),
        "serious": sum(o["serious"] for o in events_), "open": len(open_events),
        "series": series, "cameras": cameras(), "live": open_events[:12],
    }


def events(q: dict) -> dict:
    """Alerts list (filtered)."""
    start, end = date_range({"days": q.get("days", 30), **q})
    items = _events(start, end, q.get("camera") or None, false_alarms=True)
    for field in ("category", "status", "level", "shift", "rule"):
        if q.get(field):
            wanted = _rule_id(q[field]) if field == "rule" else q[field]
            items = [o for o in items if str(o[field]) == wanted]
    if q.get("serious") == "1":
        items = [o for o in items if o["serious"]]
    if q.get("sort") == "score":
        items.sort(key=lambda o: (-o["score"], o["time"]))
    try:
        limit = max(1, min(1000, int(q.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200
    tasks_ = defaultdict(list)
    for r in db.query("SELECT task, event FROM task_event"):
        tasks_[r["event"]].append(r["task"])
    for o in items:
        o["tasks"] = tasks_.get(o["id"], [])
    return {"total": len(items), "events": items[:limit]}


def analytics(q: dict) -> dict:
    """Visual Analytics: a camera's heat map, paths and counts."""
    start, end = date_range({"days": q.get("days", 7), **q})
    camera = q.get("camera") or None
    measurements = _measurements(start, end, camera, full=True)
    events_ = _events(start, end, camera)
    if q.get("category") == "serious":  # the panel's "Serious risk" choice in the category filter
        events_ = [o for o in events_ if o["serious"]]
    elif q.get("category"):
        events_ = [o for o in events_ if o["category"] == q["category"]]
    if q.get("rule"):
        events_ = [o for o in events_ if o["rule"] == _rule_id(q["rule"])]
    if q.get("serious") == "1":
        events_ = [o for o in events_ if o["serious"]]
    cols = rows = 0
    person = vehicle = None
    tracks: list = []
    hourly = [{"hour": h, "person_s": 0.0, "events": 0} for h in range(24)]
    for m in measurements:
        grid = json.loads(m["grid"])
        if person is None:
            cols, rows = grid["cols"], grid["rows"]
            person = [[0.0] * cols for _ in range(rows)]
            vehicle = [[0.0] * cols for _ in range(rows)]
        if grid["cols"] == cols and grid["rows"] == rows:
            for y in range(rows):
                for x in range(cols):
                    person[y][x] += grid["person"][y][x]
                    vehicle[y][x] += grid["vehicle"][y][x]
        hourly[int(m["start"][11:13])]["person_s"] += m["person_s"]
    for m in measurements[-25:]:
        tracks += json.loads(m["tracks"])
    for o in events_:
        hourly[int(o["time"][11:13])]["events"] += 1
    event_grid = [[0] * cols for _ in range(rows)] if cols else []
    for o in events_:
        if cols and o["x"] is not None:
            event_grid[min(rows - 1, max(0, int(o["y"] * rows)))][min(cols - 1, max(0, int(o["x"] * cols)))] += o["score"]
    day_count = max(1, len({m["start"][:10] for m in measurements}))
    compliant, noncompliant = sum(m["ppe_compliant_s"] for m in measurements), sum(m["ppe_noncompliant_s"] for m in measurements)
    busiest = max(hourly, key=lambda s: s["person_s"]) if measurements else None
    config = {}
    if camera and (paths.CAMERAS / f"{camera}.json").exists():
        config = json.loads((paths.CAMERAS / f"{camera}.json").read_text(encoding="utf-8"))
    rule_count = defaultdict(int)
    for o in events_:
        rule_count[(o["rule"], o["rule_name"])] += 1
    # share of the riskiest cell ("one corner holds X% of the risk")
    total_risk = sum(sum(s) for s in event_grid) if cols else 0
    top_cell = max((v for s in event_grid for v in s), default=0) if cols else 0
    return {
        "from": start, "to": end, "camera": camera, "slices": len(measurements),
        "last_frame": next((f"/output/{m['last_frame']}" for m in reversed(measurements) if m["last_frame"]), None),
        "grid": {"cols": cols, "rows": rows, "person": person or [], "vehicle": vehicle or [], "event": event_grid},
        "tracks": tracks[:400], "zones": [{**z, "name": paths.sample_name(z.get("name"))} for z in config.get("zones") or []],
        "points": [{"x": o["x"], "y": o["y"], "score": o["score"], "rule_name": o["rule_name"], "id": o["id"]} for o in events_ if o["x"] is not None][:500],
        "counts": {
            "people_per_day": round(sum(m["people_tracked"] for m in measurements) / day_count),
            "peak_people": max((m["peak_people"] for m in measurements), default=0),
            "vehicles_per_day": round(sum(m["vehicles_tracked"] for m in measurements) / day_count),
            "busiest_hour": f"{busiest['hour']:02d}:00" if busiest and busiest["person_s"] > 0 else None,
            "compliance": round(100 * compliant / (compliant + noncompliant), 1) if compliant + noncompliant > 0 else None, "events": len(events_),
            "monitored_s": round(sum(m["duration_s"] for m in measurements)),
            "riskiest_share": round(100 * top_cell / total_risk) if total_risk else None,
        },
        "hourly": [{"hour": s["hour"], "person_s": round(s["person_s"]), "events": s["events"]} for s in hourly],
        "rules": sorted(({"id": k, "name": name, "n": n} for (k, name), n in rule_count.items()), key=lambda r: -r["n"]),
    }


def report(q: dict) -> dict:
    """Reports: safety score, trend, breakdowns by rule / camera / shift."""
    start, end = date_range({"days": q.get("days", 30), **q})
    events_, measurements = _events(start, end), _measurements(start, end)
    days = _days(start, end)
    weekly = len(days) > 62

    def bucket(date: str) -> str:
        if not weekly:
            return date
        d = dt.date.fromisoformat(date)
        return (d - dt.timedelta(days=d.weekday())).isoformat()

    trend = {}
    for d in days:
        trend.setdefault(bucket(d), {"d": bucket(d), "person_s": 0.0, "violation_s": 0.0, "events": 0, "serious": 0, "risk": 0})
    for m in measurements:
        e = trend.get(bucket(m["start"][:10]))
        if e:
            e["person_s"] += m["person_s"]
    for o in events_:
        e = trend.get(bucket(o["time"][:10]))
        if e:
            e["violation_s"] += _violation(o)
            e["events"] += 1
            e["serious"] += o["serious"]
            e["risk"] += o["score"]

    def breakdown(field: str, name_field: str | None = None) -> list[dict]:
        g = defaultdict(lambda: {"n": 0, "serious": 0, "risk": 0, "duration_s": 0.0, "violation_s": 0.0})
        for o in events_:
            x = g[(o[field], o[name_field] if name_field else o[field])]
            x["n"] += 1
            x["serious"] += o["serious"]
            x["risk"] += o["score"]
            x["duration_s"] += o["duration_s"]
            x["violation_s"] += _violation(o)
        return sorted(({"id": k, "name": name, **{a: (round(v, 1) if isinstance(v, float) else v) for a, v in x.items()}}
                       for (k, name), x in g.items()), key=lambda r: -r["risk"])

    person_s_by_camera = defaultdict(float)
    for m in measurements:
        person_s_by_camera[m["camera"]] += m["person_s"]
    by_camera = breakdown("camera", "camera_name")
    for r in by_camera:
        r["score"] = safety_score(person_s_by_camera.get(r["id"], 0.0), r["violation_s"])
    person_s = sum(m["person_s"] for m in measurements)
    response = []
    for o in events_:
        if o["status_time"] and o["status"] != "open":
            try:
                response.append((dt.datetime.fromisoformat(o["status_time"]) - dt.datetime.fromisoformat(o["time"])).total_seconds() / 60)
            except ValueError:
                pass
    # comparison with the previous period of the same length
    length = dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)
    prev_start = (dt.datetime.fromisoformat(start) - length - dt.timedelta(seconds=1)).isoformat()
    prev_end = (dt.datetime.fromisoformat(start) - dt.timedelta(seconds=1)).isoformat()
    prev_events, prev_measurements = _events(prev_start, prev_end), _measurements(prev_start, prev_end)
    previous = safety_score(sum(m["person_s"] for m in prev_measurements), sum(_violation(o) for o in prev_events))
    task_counts = db.query("SELECT status, COUNT(*) AS n FROM task GROUP BY status")
    by_category = breakdown("category")
    for r in by_category:
        r["name"] = category_name(r["id"])
    return {
        "from": start, "to": end, "site": db.settings()["site"], "weekly": weekly,
        "score": safety_score(person_s, sum(_violation(o) for o in events_)), "previous_score": previous,
        "events": len(events_), "serious": sum(o["serious"] for o in events_), "risk": sum(o["score"] for o in events_),
        "open": sum(1 for o in events_ if o["status"] == "open"), "resolved": sum(1 for o in events_ if o["status"] == "resolved"),
        "monitored_s": round(sum(m["duration_s"] for m in measurements)), "person_hours": round(person_s / 3600, 2),
        "avg_response_min": round(sum(response) / len(response), 1) if response else None,
        "trend": [{"d": e["d"], "score": safety_score(e["person_s"], e["violation_s"]), "events": e["events"], "serious": e["serious"], "risk": e["risk"]}
                  for e in trend.values()],
        "rules": breakdown("rule", "rule_name"), "cameras": by_camera, "shifts": breakdown("shift"), "categories": by_category,
        "zones": [r for r in breakdown("zone") if r["id"]],
        "tasks": {r["status"]: r["n"] for r in task_counts},
    }


def tasks() -> list[dict]:
    """Tasks; for each, the linked events and the check after closing."""
    items = db.query("SELECT * FROM task ORDER BY (status = 'closed'), COALESCE(due_date, '9999'), id DESC")
    today = dt.date.today().isoformat()
    for g in items:
        linked = db.query("SELECT e.id, e.time, e.rule, e.rule_name, e.camera, e.camera_name, e.zone, e.score, e.snapshot, e.clip FROM task_event te "
                          "JOIN event e ON e.id = te.event WHERE te.task = ? ORDER BY e.time DESC", g["id"])
        for o in linked:
            localize_event(o)
        g["events"] = linked
        g["overdue"] = bool(g["due_date"] and g["status"] != "closed" and g["due_date"] < today)
        # weekly event count for the same camera + rule (+ zone): did the fix work?
        camera = g["camera"] or (linked[0]["camera"] if linked else None)
        rule = g["rule"] or (linked[0]["rule"] if linked else None)
        g["weeks"], g["verification"] = [], None
        if camera and rule:
            sql, p = "SELECT time, score FROM event WHERE camera = ? AND rule = ? AND status != 'false_alarm'", [camera, rule]
            if g["zone"]:
                sql += " AND zone = ?"
                p.append(g["zone"])
            week = defaultdict(int)
            for o in db.query(sql, *p):
                d = dt.date.fromisoformat(o["time"][:10])
                week[(d - dt.timedelta(days=d.weekday())).isoformat()] += 1
            g["weeks"] = [{"week": w, "n": n} for w, n in sorted(week.items())][-10:]
            if g["status"] == "closed" and g["closed"]:
                after = db.query(sql + " AND time > ?", *p, g["closed"])
                monitored = db.query("SELECT COUNT(*) AS n FROM measurement WHERE camera = ? AND start > ?", camera, g["closed"])[0]["n"]
                g["verification"] = {"events_after": len(after), "slices_after": monitored,
                                     "status": "pending" if monitored == 0 else ("recurred" if after else "verified")}
    return items


def _label(table: dict, code):
    """Label from a table of N_-marked texts in the current language; the code itself if it is unknown."""
    return T(table[code]) if code in table else code


def excel() -> Path:
    """Writes the report of the last 30 days to output/report.xlsx."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    r = report({"days": 30})
    book = Workbook()
    s = book.active
    s.title = T("Summary")
    for row in ([T("Site"), r["site"]], [T("Period"), f"{r['from'][:10]} – {r['to'][:10]}"], [T("Safety score (0-100)"), r["score"]],
                [T("Previous period score"), r["previous_score"]], [T("Event count"), r["events"]], [T("Serious-risk events"), r["serious"]],
                [T("Open events"), r["open"]], [T("Resolved events"), r["resolved"]], [T("Monitored time (hours)"), round(r["monitored_s"] / 3600, 2)],
                [T("Monitored person-hours"), r["person_hours"]], [T("Average response time (min)"), r["avg_response_min"]]):
        s.append(row)
    for title, column, items in ((N_("Rules"), N_("Rule"), r["rules"]), (N_("Cameras"), N_("Camera"), r["cameras"]),
                                 (N_("Shifts"), N_("Shift"), r["shifts"]), (N_("Zones"), N_("Zone"), r["zones"])):
        s = book.create_sheet(T(title))
        s.append([T(column), T("Event count"), T("Serious"), T("Total risk score"), T("Total duration (s)")])
        for x in items:
            s.append([x["name"], x["n"], x["serious"], x["risk"], x["duration_s"]])
    s = book.create_sheet(T("Event list"))
    s.append([T("ID"), T("Time"), T("Camera"), T("Rule"), T("Category"), T("Zone"), T("Shift"), T("Severity"), T("Likelihood"), T("Risk score"),
              T("Serious"), T("Duration (s)"), T("People"), T("Status"), T("Assignee"), T("Notes")])
    for o in _events(r["from"], r["to"], false_alarms=True):
        s.append([o["id"], o["time"].replace("T", " "), o["camera_name"], o["rule_name"], category_name(o["category"]),
                  o["zone"], o["shift"], o["severity"], o["likelihood"], o["score"], T("yes") if o["serious"] else "", o["duration_s"], o["people"],
                  _label(STATUS_NAMES, o["status"]), o["assignee"], o["notes"]])
    s = book.create_sheet(T("Tasks"))
    s.append([T("ID"), T("Title"), T("Responsible"), T("Due date"), T("Status"), T("Linked events"), T("Created"), T("Closed on"), T("Verification")])
    for g in tasks():
        s.append([g["id"], g["title"], g["owner"], g["due_date"], T(TASK_STATUS_NAMES[g["status"]]) if g["status"] in TASK_STATUS_NAMES else None,
                  len(g["events"]), g["created"].replace("T", " "), (g["closed"] or "").replace("T", " "),
                  _label(VERIFICATION_NAMES, (g["verification"] or {}).get("status")) if (g["verification"] or {}).get("status") in VERIFICATION_NAMES else ""])
    for s in book.worksheets:
        for cell in s[1]:
            cell.font = Font(bold=True)
        for col in s.columns:
            s.column_dimensions[col[0].column_letter].width = min(48, max(12, max(len(str(c.value or "")) for c in col) + 2))
    paths.OUTPUT.mkdir(parents=True, exist_ok=True)
    target = paths.OUTPUT / "report.xlsx"
    book.save(target)
    return target
