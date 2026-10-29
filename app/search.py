"""Search visits by place name/category and date range, grouped by place (with nearby-candidate matches)."""
import json
import sqlite3
from datetime import date

from app.dayview import _duration, _local, display_name
from app.textnorm import norm


def _visit_entry(v, possible: bool, primary_name: str | None) -> dict:
    return {
        "visit_id": v["id"], "person_id": v["person_id"], "person_name": v["person_name"],
        "date": v["local_start_date"],
        "start": _local(v["start_utc"], v["tz_offset_min"]), "end": _local(v["end_utc"], v["tz_offset_min"]),
        "duration_s": _duration(v["start_utc"], v["end_utc"]),
        "possible": possible, "primary_name": primary_name,
    }


def search(conn: sqlite3.Connection, q: str, date_from: date | None, date_to: date | None,
           person_id: int | None) -> list[dict]:
    sql = ("SELECT v.*, per.name AS person_name, p.osm_id, p.name, p.category, p.address, p.candidates"
           " FROM visits v JOIN persons per ON per.id = v.person_id"
           " LEFT JOIN places p ON p.place_key = v.place_key WHERE 1=1")
    args: list = []
    if date_to:
        sql += " AND v.local_start_date <= ?"
        args.append(date_to.isoformat())
    if date_from:
        sql += " AND v.local_end_date >= ?"
        args.append(date_from.isoformat())
    if person_id is not None:
        sql += " AND v.person_id = ?"
        args.append(person_id)

    needle = norm(q.strip())
    groups: dict[str, dict] = {}

    def group(key: str, name: str, category: str | None, address: str | None) -> dict:
        return groups.setdefault(key, {"key": key, "name": name, "category": category, "address": address,
                                       "count": 0, "total_s": 0, "possible_count": 0, "visits": [],
                                       "_confident": False})

    for v in conn.execute(sql, args):
        name = display_name(v["semantic_type"], v)
        if not needle or needle in norm(name) or needle in norm(v["category"]):
            g = group(v["osm_id"] or v["place_key"], name, v["category"], v["address"])
            if not g["_confident"]:
                g.update(name=name, category=v["category"], address=v["address"], _confident=True)
            entry = _visit_entry(v, False, None)
            g["visits"].append(entry)
            g["count"] += 1
            g["total_s"] += entry["duration_s"]
            continue
        for c in json.loads(v["candidates"] or "[]"):
            if c["osm_id"] == v["osm_id"]:
                continue
            if needle in norm(c.get("name")) or needle in norm(c.get("category")):
                g = group(c["osm_id"], c["name"], c.get("category"), None)
                g["visits"].append(_visit_entry(v, True, name))
                g["possible_count"] += 1

    result = []
    for g in groups.values():
        g.pop("_confident")
        g["visits"].sort(key=lambda e: e["start"], reverse=True)
        result.append(g)
    result.sort(key=lambda g: g["total_s"], reverse=True)
    return result
