"""One person's day: visits (with nested level-1 visits) and activities, plus transport corrections."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

MODES: list[str] = [
    "in passenger vehicle", "cycling", "walking", "running", "in bus", "in train", "in tram", "in subway",
    "in ferry", "flying", "motorcycling", "skiing", "sailing", "unknown",
]

SEMANTIC_NAMES = {"Home": "Dom", "Work": "Praca"}


def display_name(semantic_type: str, place_row) -> str:
    if semantic_type in SEMANTIC_NAMES:
        return SEMANTIC_NAMES[semantic_type]
    if place_row is not None and place_row["name"]:
        return place_row["name"]
    return "Nieznane miejsce"


def _local(utc_iso: str, offset_min: int) -> str:
    tz = timezone(timedelta(minutes=offset_min))
    return datetime.fromisoformat(utc_iso).astimezone(tz).isoformat(timespec="seconds")


def _duration(start_utc: str, end_utc: str) -> int:
    return int((datetime.fromisoformat(end_utc) - datetime.fromisoformat(start_utc)).total_seconds())


def _overlap_s(a0: str, a1: str, b0: str, b1: str) -> float:
    start = max(datetime.fromisoformat(a0), datetime.fromisoformat(b0))
    end = min(datetime.fromisoformat(a1), datetime.fromisoformat(b1))
    return max(0.0, (end - start).total_seconds())


def _matching_correction(corrections, start_utc: str, end_utc: str):
    """Correction with the largest overlap, if that overlap is >= 50% of the correction's duration."""
    best, best_overlap = None, 0.0
    for c in corrections:
        overlap = _overlap_s(c["start_utc"], c["end_utc"], start_utc, end_utc)
        length = _duration(c["start_utc"], c["end_utc"]) or 1
        if overlap / length >= 0.5 and overlap > best_overlap:
            best, best_overlap = c, overlap
    return best


def _corrections_for(conn: sqlite3.Connection, person_id: int):
    return conn.execute("SELECT * FROM corrections WHERE person_id=?", (person_id,)).fetchall()


def get_day(conn: sqlite3.Connection, person_id: int, day: date) -> list[dict]:
    d = day.isoformat()
    visits = conn.execute(
        "SELECT v.*, p.name, p.category, p.address, p.status FROM visits v"
        " LEFT JOIN places p ON p.place_key = v.place_key"
        " WHERE v.person_id=? AND v.local_start_date<=? AND v.local_end_date>=? ORDER BY v.start_utc",
        (person_id, d, d),
    ).fetchall()
    activities = conn.execute(
        "SELECT * FROM activities WHERE person_id=? AND local_start_date<=? AND local_end_date>=?"
        " ORDER BY start_utc",
        (person_id, d, d),
    ).fetchall()
    corrections = _corrections_for(conn, person_id)

    items: list[tuple[str, dict]] = []
    parents: list[tuple[dict, str, str]] = []
    nested: list[tuple[dict, str]] = []
    for v in visits:
        item = {
            "kind": "visit", "id": v["id"],
            "start": _local(v["start_utc"], v["tz_offset_min"]), "end": _local(v["end_utc"], v["tz_offset_min"]),
            "duration_s": _duration(v["start_utc"], v["end_utc"]),
            "name": display_name(v["semantic_type"], v),
            "category": None if v["semantic_type"] in SEMANTIC_NAMES else v["category"], "address": v["address"],
            "lat": v["lat"], "lng": v["lng"], "place_key": v["place_key"],
            "resolved": v["status"] == "done", "children": [],
        }
        if v["hierarchy_level"] == 0:
            parents.append((item, v["start_utc"], v["end_utc"]))
            items.append((v["start_utc"], item))
        else:
            nested.append((item, v["start_utc"]))
    for item, start in nested:
        parent = next((p for p, s, e in parents if s <= start <= e), None)
        if parent is not None:
            parent["children"].append(item)
        else:
            items.append((start, item))

    for a in activities:
        c = _matching_correction(corrections, a["start_utc"], a["end_utc"])
        items.append((a["start_utc"], {
            "kind": "activity", "id": a["id"],
            "start": _local(a["start_utc"], a["tz_offset_min"]), "end": _local(a["end_utc"], a["tz_offset_min"]),
            "duration_s": _duration(a["start_utc"], a["end_utc"]),
            "mode": c["mode"] if c else a["mode"], "corrected": c is not None,
            "distance_m": a["distance_m"],
        }))

    items.sort(key=lambda t: t[0])
    return [item for _, item in items]


def last_day(conn: sqlite3.Connection, person_id: int) -> date | None:
    row = conn.execute(
        "SELECT MAX(d) FROM (SELECT MAX(local_end_date) d FROM visits WHERE person_id=?"
        " UNION ALL SELECT MAX(local_end_date) FROM activities WHERE person_id=?)",
        (person_id, person_id),
    ).fetchone()
    return date.fromisoformat(row[0]) if row[0] else None


def set_activity_mode(conn: sqlite3.Connection, activity_id: int, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Nieznany rodzaj transportu: {mode}")
    a = conn.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if a is None:
        raise KeyError(activity_id)
    with conn:
        for c in _corrections_for(conn, a["person_id"]):
            overlap = _overlap_s(c["start_utc"], c["end_utc"], a["start_utc"], a["end_utc"])
            if overlap / (_duration(c["start_utc"], c["end_utc"]) or 1) >= 0.5:
                conn.execute("DELETE FROM corrections WHERE id=?", (c["id"],))
        conn.execute(
            "INSERT INTO corrections(person_id, start_utc, end_utc, mode) VALUES (?,?,?,?)",
            (a["person_id"], a["start_utc"], a["end_utc"], mode),
        )
