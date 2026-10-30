"""Persons and file import with the "newer file wins" merge rule."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.parser import UNSUPPORTED_MESSAGE, UnsupportedFormat, parse_timeline


@dataclass
class ImportReport:
    file_id: int
    added_visits: int
    added_activities: int
    replaced: int
    skipped: int


def utc_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def place_key(place_id: str | None, lat: float, lng: float) -> str:
    return place_id or f"{lat:.4f},{lng:.4f}"


def _local_fields(start: datetime, end: datetime) -> tuple[int, str, str]:
    """Offset (minutes) of the start time and local dates of start/end, both in that offset."""
    offset = start.utcoffset()
    end_local = end.astimezone(start.tzinfo)
    return int(offset.total_seconds() // 60), start.date().isoformat(), end_local.date().isoformat()


def create_person(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Imię nie może być puste.")
    with conn:
        return conn.execute("INSERT INTO persons(name) VALUES (?)", (name,)).lastrowid


def rename_person(conn: sqlite3.Connection, person_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Imię nie może być puste.")
    with conn:
        conn.execute("UPDATE persons SET name=? WHERE id=?", (name, person_id))


def delete_person(conn: sqlite3.Connection, person_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM persons WHERE id=?", (person_id,))


def list_persons(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM persons ORDER BY name COLLATE NOCASE, id")
    return [dict(r) for r in rows]


def import_file(conn: sqlite3.Connection, person_id: int, filename: str, raw: bytes, now: datetime) -> ImportReport:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UnsupportedFormat(UNSUPPORTED_MESSAGE) from e
    parsed = parse_timeline(data)
    entries = parsed.visits + parsed.activities
    if not entries:
        raise UnsupportedFormat(UNSUPPORTED_MESSAGE)

    range_start = utc_iso(min(e.start for e in entries))
    range_end = utc_iso(max(e.end for e in entries))

    with conn:
        file_id = conn.execute(
            "INSERT INTO files(person_id, filename, uploaded_at, range_start, range_end) VALUES (?,?,?,?,?)",
            (person_id, filename, utc_iso(now), range_start, range_end),
        ).lastrowid
        replaced = 0
        for table in ("visits", "activities"):
            replaced += conn.execute(
                f"DELETE FROM {table} WHERE person_id=? AND file_id<>? AND start_utc>=? AND end_utc<=?",
                (person_id, file_id, range_start, range_end),
            ).rowcount

        for v in parsed.visits:
            key = place_key(v.place_id, v.lat, v.lng)
            off, d0, d1 = _local_fields(v.start, v.end)
            conn.execute(
                "INSERT INTO visits(person_id, file_id, start_utc, end_utc, tz_offset_min, local_start_date,"
                " local_end_date, place_key, lat, lng, semantic_type, hierarchy_level)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (person_id, file_id, utc_iso(v.start), utc_iso(v.end), off, d0, d1, key, v.lat, v.lng,
                 v.semantic_type, v.hierarchy_level),
            )
            conn.execute("INSERT OR IGNORE INTO places(place_key, lat, lng) VALUES (?,?,?)", (key, v.lat, v.lng))

        for a in parsed.activities:
            off, d0, d1 = _local_fields(a.start, a.end)
            conn.execute(
                "INSERT INTO activities(person_id, file_id, start_utc, end_utc, tz_offset_min, local_start_date,"
                " local_end_date, mode, distance_m) VALUES (?,?,?,?,?,?,?,?,?)",
                (person_id, file_id, utc_iso(a.start), utc_iso(a.end), off, d0, d1, a.mode, a.distance_m),
            )

    return ImportReport(file_id, len(parsed.visits), len(parsed.activities), replaced, parsed.skipped)
