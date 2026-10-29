"""SQLite connection and schema."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, range_start TEXT, range_end TEXT);
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, tz_offset_min INTEGER NOT NULL,
  local_start_date TEXT NOT NULL, local_end_date TEXT NOT NULL,
  place_key TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
  semantic_type TEXT NOT NULL, hierarchy_level INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, tz_offset_min INTEGER NOT NULL,
  local_start_date TEXT NOT NULL, local_end_date TEXT NOT NULL,
  mode TEXT NOT NULL, distance_m REAL NOT NULL, geometry TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS places (
  place_key TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, next_try_at TEXT NOT NULL DEFAULT '',
  osm_id TEXT, name TEXT, category TEXT, address TEXT,
  candidates TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS visits_person_day ON visits(person_id, local_start_date, local_end_date);
CREATE INDEX IF NOT EXISTS activities_person_day ON activities(person_id, local_start_date, local_end_date);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
