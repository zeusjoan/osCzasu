# Oś Czasu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A private, single-user web app (one Docker container) that imports Google Timeline on-device JSON exports for several people and shows each person's days on a map, with OSM place names and search.

**Architecture:** FastAPI backend with pure-Python modules (parser → importer → SQLite; day view, search, places worker read from SQLite). A background thread resolves place names from OpenStreetMap at ≤1 request/s. Static vanilla-JS + Leaflet frontend served by the same app.

**Tech Stack:** Python ≥3.12, FastAPI 0.115.6, Uvicorn 0.34.0, httpx 0.28.1, python-multipart 0.0.20, stdlib `sqlite3`, pytest 8.3.4; Leaflet 1.9.4 (unpkg); Docker + compose.

**Spec:** `docs/superpowers/specs/2026-10-29-oscz-czasu-design.md`

## Global Constraints

- UI language: Polish. All user-visible strings in Polish.
- Single user, LAN only — **no authentication**.
- Location data leaves the machine only as OSM queries (Overpass, Nominatim) and map tiles. No other external services, no paid APIs, no API keys.
- OSM: **max 1 request/s** across both services, `User-Agent: OsCzasu/0.1 (<OSM_CONTACT>)` where `OSM_CONTACT` is an env var.
- Every place is queried once; results persist in SQLite and are shared across all people.
- Private data never committed: `Timeline.json`, `data/`, `.superpowers/`, `.venv/` in `.gitignore`. Test fixtures are synthetic (no real coordinates).
- No frontend build step, no framework, no extra DB server.
- Persistent state lives only under `DATA_DIR` (default `./data`, `/data` in Docker): `oscz.db` + `uploads/`.

## Review Focus

1. **Wrong file uploaded** (old Takeout format, `semanticSegments` object, invalid JSON, empty array) → HTTP 400 with the Polish message, nothing written to the DB. Tests: Task 1 (`test_unsupported_formats`), Task 2 (`test_unsupported_file_writes_nothing`).
2. **Visit across midnight / mixed offsets** (`+02:00` entries next to `Z` path windows) → shown on both local days with local clock times. Test: Task 3 (`test_visit_across_midnight_on_both_days`).
3. **OSM down, slow or returning 429** → place stays pending with backoff, app keeps working, worker continues with other places. Test: Task 4 (`test_failure_backs_off_and_continues`).
4. **Polish diacritics and case in search** ("Żabka" vs "zabka", "ŁÓDŹ" vs "lodz") → match. Test: Task 5 (`test_search_ignores_case_and_diacritics`).
5. **Re-uploading the same file, or a newer export overlapping an older one** → no duplicates, manual transport corrections survive. Tests: Task 2 (`test_reimport_same_file_no_duplicates`), Task 3 (`test_correction_survives_reimport`).

---

## File Structure

```
app/
  __init__.py
  parser.py      # Timeline JSON → dataclasses; geometry building
  db.py          # connect(), init_schema()
  importer.py    # persons CRUD, import_file() with newest-wins merge, place queue
  dayview.py     # get_day(), last_day(), set_activity_mode(), corrections
  textnorm.py    # norm() for case/diacritic-insensitive matching
  places.py      # OSM client, POI picking, category mapping, worker
  search.py      # search()
  main.py        # create_app(), app_from_env(), routes, worker lifecycle
static/
  index.html  app.js  style.css
tests/
  fixtures/sample_timeline.json
  conftest.py  test_parser.py  test_importer.py  test_dayview.py
  test_places.py  test_search.py  test_api.py
requirements.txt  Dockerfile  docker-compose.yml  README.md  .gitignore  .dockerignore
```

### Database schema (`app/db.py`)

```sql
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
  mode TEXT NOT NULL, distance_m REAL NOT NULL, geometry TEXT NOT NULL);  -- JSON [[lat,lng],...]
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS places (
  place_key TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',          -- 'pending' | 'done'
  attempts INTEGER NOT NULL DEFAULT 0, next_try_at TEXT NOT NULL DEFAULT '',
  osm_id TEXT, name TEXT, category TEXT, address TEXT,
  candidates TEXT NOT NULL DEFAULT '[]');          -- JSON [{osm_id,name,category,distance_m}]
```

Conventions: UTC timestamps stored as ISO strings `YYYY-MM-DDTHH:MM:SS+00:00` (sortable). `tz_offset_min` is the offset of the entry's `startTime`; `local_*_date` are computed with it (both start and end use the start offset). `place_key` = Google `placeID` if present, else `f"{lat:.4f},{lng:.4f}"`. `connect()` sets `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `row_factory=sqlite3.Row`.

---

### Task 1: Project skeleton + Timeline parser

**Files:**
- Create: `requirements.txt`, `app/__init__.py`, `app/parser.py`, `tests/fixtures/sample_timeline.json`, `tests/test_parser.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces (in `app/parser.py`):
  - `class UnsupportedFormat(Exception)`
  - `@dataclass Visit(start: datetime, end: datetime, place_id: str | None, lat: float, lng: float, semantic_type: str, hierarchy_level: int)` — datetimes timezone-aware with the file's offset
  - `@dataclass Activity(start: datetime, end: datetime, mode: str, distance_m: float, start_lat: float, start_lng: float, end_lat: float, end_lng: float)`
  - `@dataclass PathPoint(time: datetime, lat: float, lng: float)`
  - `@dataclass ParsedTimeline(visits: list[Visit], activities: list[Activity], path_points: list[PathPoint], skipped: int)`
  - `parse_timeline(data: object) -> ParsedTimeline`
  - `build_geometry(activity: Activity, points: list[PathPoint]) -> list[tuple[float, float]]`
  - `UNSUPPORTED_MESSAGE = "Nieobsługiwany format pliku. Obsługiwany jest eksport osi czasu z telefonu (plik Timeline.json)."`

- [ ] **Step 1: Setup**

`requirements.txt`: `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`, `httpx==0.28.1`, `python-multipart==0.0.20`, `pytest==8.3.4`. Create `.venv` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`). Add to `.gitignore`: `.venv/`, `__pycache__/`, and `!tests/fixtures/*.json` (after the existing `*.json` line).

- [ ] **Step 2: Write the fixture `tests/fixtures/sample_timeline.json`** (synthetic data, used by Tasks 1–6)

```json
[
 {"startTime":"2025-05-14T07:00:00.000+02:00","endTime":"2025-05-14T07:42:00.000+02:00",
  "visit":{"hierarchyLevel":"0","probability":"0.9","topCandidate":{"probability":"0.8","semanticType":"Home","placeID":"PHOME","placeLocation":"geo:52.000000,21.000000"}}},
 {"startTime":"2025-05-14T07:42:00.000+02:00","endTime":"2025-05-14T08:03:00.000+02:00",
  "activity":{"start":"geo:52.000000,21.000000","end":"geo:52.100000,21.100000","distanceMeters":"12400.000000","probability":"0.9","topCandidate":{"type":"in passenger vehicle","probability":"0.9"}}},
 {"startTime":"2025-05-14T04:00:00.000Z","endTime":"2025-05-14T06:00:00.000Z",
  "timelinePath":[{"point":"geo:52.300000,21.300000","durationMinutesOffsetFromStartTime":"30"},
                  {"point":"geo:52.070000,21.070000","durationMinutesOffsetFromStartTime":"110"},
                  {"point":"geo:52.050000,21.050000","durationMinutesOffsetFromStartTime":"105"}]},
 {"startTime":"2025-05-14T10:00:00.000+02:00","endTime":"2025-05-14T12:00:00.000+02:00",
  "visit":{"hierarchyLevel":"0","probability":"0.9","topCandidate":{"probability":"0.7","semanticType":"Unknown","placeID":"PMALL","placeLocation":"geo:52.150000,21.150000"}}},
 {"startTime":"2025-05-14T10:30:00.000+02:00","endTime":"2025-05-14T11:00:00.000+02:00",
  "visit":{"hierarchyLevel":"1","probability":"0.9","topCandidate":{"probability":"0.6","semanticType":"Unknown","placeID":"PSHOP","placeLocation":"geo:52.150100,21.150100"}}},
 {"startTime":"2025-05-14T16:24:00.000+02:00","endTime":"2025-05-14T16:41:00.000+02:00",
  "visit":{"hierarchyLevel":"0","probability":"0.9","topCandidate":{"probability":"0.7","semanticType":"Unknown","placeID":"PLIDL","placeLocation":"geo:52.200000,21.200000"}}},
 {"startTime":"2025-05-14T23:00:00.000+02:00","endTime":"2025-05-15T01:00:00.000+02:00",
  "visit":{"hierarchyLevel":"0","probability":"0.9","topCandidate":{"probability":"0.7","semanticType":"Unknown","placeLocation":"geo:52.250000,21.250000"}}},
 {"startTime":"2025-05-14T07:00:00.000+02:00","endTime":"2025-05-20T07:00:00.000+02:00",
  "timelineMemory":{"destinations":[{"identifier":"PX"}],"distanceFromOriginKms":"10"}},
 {"startTime":"bad","endTime":"bad","visit":{}},
 {"startTime":"2025-05-14T07:00:00.000+02:00","endTime":"2025-05-14T08:00:00.000+02:00","somethingNew":{}}
]
```

- [ ] **Step 3: Write failing tests in `tests/test_parser.py`**

```python
FIX = Path(__file__).parent / "fixtures" / "sample_timeline.json"

def test_parse_counts():
    p = parse_timeline(json.loads(FIX.read_text()))
    assert len(p.visits) == 5 and len(p.activities) == 1
    assert len(p.path_points) == 3
    assert p.skipped == 2            # malformed visit + unknown key; timelineMemory ignored silently

def test_visit_fields():
    v = parse_timeline(json.loads(FIX.read_text())).visits
    home = v[0]
    assert (home.place_id, home.lat, home.lng, home.semantic_type, home.hierarchy_level) == ("PHOME", 52.0, 21.0, "Home", 0)
    assert home.start.utcoffset() == timedelta(hours=2)
    assert v[4].place_id is None and v[2].hierarchy_level == 1

def test_activity_fields():
    a = parse_timeline(json.loads(FIX.read_text())).activities[0]
    assert (a.mode, a.distance_m, a.start_lat, a.end_lng) == ("in passenger vehicle", 12400.0, 52.0, 21.1)

def test_path_point_time_is_window_start_plus_offset():
    pts = parse_timeline(json.loads(FIX.read_text())).path_points
    assert datetime(2025, 5, 14, 4, 30, tzinfo=timezone.utc) in [p.time for p in pts]

def test_build_geometry_uses_points_inside_activity_sorted():
    p = parse_timeline(json.loads(FIX.read_text()))
    assert build_geometry(p.activities[0], p.path_points) == [(52.0, 21.0), (52.05, 21.05), (52.07, 21.07), (52.1, 21.1)]

def test_build_geometry_without_points_is_straight_line():
    p = parse_timeline(json.loads(FIX.read_text()))
    assert build_geometry(p.activities[0], []) == [(52.0, 21.0), (52.1, 21.1)]

@pytest.mark.parametrize("data", [{"semanticSegments": []}, {"locations": []}, "x", 5, None])
def test_unsupported_formats(data):
    with pytest.raises(UnsupportedFormat):
        parse_timeline(data)
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_parser.py -v` — Expected: FAIL (`ModuleNotFoundError: app.parser`).

- [ ] **Step 5: Implement `app/parser.py`**

Top level must be a `list`, else `UnsupportedFormat`. Parse `"geo:lat,lng"` strings and numeric strings; `datetime.fromisoformat` handles both offset styles on ≥3.11. Any exception while parsing one entry → `skipped += 1`. Entries with `timelineMemory` are ignored without counting. Path point time = window `startTime` + `durationMinutesOffsetFromStartTime` minutes. `build_geometry`: start point, then points with `activity.start <= time <= activity.end` sorted by time, then end point.

- [ ] **Step 6: Run** `.venv/bin/pytest tests/test_parser.py -v` — Expected: all PASS.

- [ ] **Step 7: Commit** — `git add requirements.txt .gitignore app tests && git commit -m "feat: timeline parser"`

---

### Task 2: Database + persons + import with newest-wins merge

**Files:**
- Create: `app/db.py`, `app/importer.py`, `tests/conftest.py`, `tests/test_importer.py`

**Interfaces:**
- Consumes: `parse_timeline`, `build_geometry`, `UnsupportedFormat` (Task 1).
- Produces:
  - `db.connect(path: Path | str) -> sqlite3.Connection`; `db.init_schema(conn) -> None` (schema above)
  - `importer.create_person(conn, name: str) -> int` (raises `ValueError` on blank name; duplicates allowed)
  - `importer.rename_person(conn, person_id: int, name: str) -> None`; `importer.delete_person(conn, person_id: int) -> None`
  - `importer.list_persons(conn) -> list[dict]` → `[{"id", "name"}]` ordered by name
  - `@dataclass ImportReport(file_id: int, added_visits: int, added_activities: int, replaced: int, skipped: int)`
  - `importer.import_file(conn, person_id: int, filename: str, raw: bytes, now: datetime) -> ImportReport` — raises `UnsupportedFormat` (also for invalid JSON / non-UTF-8, and for a file with zero visits and activities)
  - `importer.place_key(place_id: str | None, lat: float, lng: float) -> str`
- `tests/conftest.py` fixtures: `conn` (in-memory DB with schema), `sample_bytes` (fixture file bytes), `person` (id of a created person "Jan").

- [ ] **Step 1: Write failing tests in `tests/test_importer.py`**

```python
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

def test_import_counts_and_queues_places(conn, person, sample_bytes):
    r = import_file(conn, person, "Timeline.json", sample_bytes, NOW)
    assert (r.added_visits, r.added_activities, r.replaced, r.skipped) == (5, 1, 0, 2)
    keys = {row[0] for row in conn.execute("SELECT place_key FROM places")}
    assert keys == {"PHOME", "PMALL", "PSHOP", "PLIDL", "52.2500,21.2500"}
    assert conn.execute("SELECT COUNT(*) FROM places WHERE status='pending'").fetchone()[0] == 5

def test_reimport_same_file_no_duplicates(conn, person, sample_bytes):
    import_file(conn, person, "a.json", sample_bytes, NOW)
    r = import_file(conn, person, "a.json", sample_bytes, NOW + timedelta(minutes=1))
    assert r.replaced == 6
    assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1

def test_newer_file_replaces_only_entries_inside_its_range(conn, person, sample_bytes):
    import_file(conn, person, "old.json", sample_bytes, NOW)
    newer = [{"startTime": "2025-05-14T16:20:00.000+02:00", "endTime": "2025-05-14T16:45:00.000+02:00",
              "visit": {"hierarchyLevel": "0", "topCandidate": {"semanticType": "Unknown", "placeID": "PLIDL2",
                        "placeLocation": "geo:52.200000,21.200000"}}}]
    r = import_file(conn, person, "new.json", json.dumps(newer).encode(), NOW + timedelta(days=1))
    assert r.replaced == 1                                  # only the 16:24–16:41 Lidl visit
    ids = {row[0] for row in conn.execute("SELECT place_key FROM visits")}
    assert "PLIDL" not in ids and "PLIDL2" in ids and "PHOME" in ids

def test_import_does_not_touch_other_person(conn, person, sample_bytes):
    other = create_person(conn, "Ala")
    import_file(conn, other, "a.json", sample_bytes, NOW)
    import_file(conn, person, "a.json", sample_bytes, NOW)
    assert conn.execute("SELECT COUNT(*) FROM visits WHERE person_id=?", (other,)).fetchone()[0] == 5

@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe", b'{"semanticSegments": []}', b"[]"])
def test_unsupported_file_writes_nothing(conn, person, raw):
    with pytest.raises(UnsupportedFormat):
        import_file(conn, person, "x.json", raw, NOW)
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0

def test_delete_person_cascades(conn, person, sample_bytes):
    import_file(conn, person, "a.json", sample_bytes, NOW)
    delete_person(conn, person)
    assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 5   # cache stays

def test_blank_person_name_rejected(conn):
    with pytest.raises(ValueError):
        create_person(conn, "  ")
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_importer.py -v` — Expected: FAIL (import error).

- [ ] **Step 3: Implement `app/db.py` and `app/importer.py`**

`import_file` runs in one transaction (`with conn:`): parse → if no visits/activities raise → compute range `[min start, max end]` (UTC) → insert `files` row → delete this person's visits/activities whose `start_utc >= range_start AND end_utc <= range_end` and `file_id != new file` (`replaced` = rows deleted) → insert entries (activities with `json.dumps(build_geometry(...))`) → `INSERT OR IGNORE` into `places`.

- [ ] **Step 4: Run** `.venv/bin/pytest -v` — Expected: all PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: sqlite storage and import with newest-wins merge"` (add new files first).

---

### Task 3: Day view and transport corrections

**Files:**
- Create: `app/dayview.py`, `tests/test_dayview.py`

**Interfaces:**
- Consumes: `connect`, `init_schema`, `import_file`, `create_person` (Task 2).
- Produces:
  - `MODES: list[str] = ["in passenger vehicle", "cycling", "walking", "running", "in bus", "in train", "in tram", "in subway", "in ferry", "flying", "motorcycling", "skiing", "sailing", "unknown"]`
  - `get_day(conn, person_id: int, day: date) -> list[dict]` — items sorted by start; shapes:
    - visit: `{"kind": "visit", "id", "start", "end", "duration_s", "name", "category", "address", "lat", "lng", "place_key", "resolved": bool, "children": [visit, ...]}`
    - activity: `{"kind": "activity", "id", "start", "end", "duration_s", "mode", "corrected": bool, "distance_m", "geometry": [[lat, lng], ...]}`
    - `start`/`end` are local ISO strings with offset (`"2025-05-14T16:24:00+02:00"`)
  - `last_day(conn, person_id: int) -> date | None`
  - `set_activity_mode(conn, activity_id: int, mode: str) -> None` (raises `ValueError` for mode not in `MODES`, `KeyError` for unknown activity)
  - `display_name(semantic_type: str, place_row) -> str` — `"Dom"` for Home, `"Praca"` for Work, else place name, else `"Nieznane miejsce"` (reused by Task 5)

Rules: an entry belongs to day D if `local_start_date <= D <= local_end_date`. A level-1 visit is put in `children` of the level-0 visit (same day list) whose interval contains its start; otherwise it is top-level. Effective mode: the correction of this person with the largest overlap with the activity, if `overlap / correction_duration >= 0.5`; else the stored mode. `set_activity_mode` deletes this person's corrections that overlap the activity by ≥50% and inserts one with the activity's start/end.

- [ ] **Step 1: Write failing tests in `tests/test_dayview.py`**

```python
def test_day_items_order_and_nesting(conn, imported):          # fixture: person with sample imported
    items = get_day(conn, imported, date(2025, 5, 14))
    assert [i["kind"] for i in items] == ["visit", "activity", "visit", "visit", "visit"]
    mall = items[2]
    assert [c["place_key"] for c in mall["children"]] == ["PSHOP"]
    assert items[0]["name"] == "Dom" and items[3]["name"] == "Nieznane miejsce"
    assert items[3]["start"] == "2025-05-14T16:24:00+02:00" and items[3]["duration_s"] == 17 * 60

def test_activity_geometry_and_mode(conn, imported):
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    assert act["mode"] == "in passenger vehicle" and act["corrected"] is False
    assert act["geometry"] == [[52.0, 21.0], [52.05, 21.05], [52.07, 21.07], [52.1, 21.1]]

def test_visit_across_midnight_on_both_days(conn, imported):
    next_day = get_day(conn, imported, date(2025, 5, 15))
    assert [i["place_key"] for i in next_day] == ["52.2500,21.2500"]
    assert next_day[0]["end"] == "2025-05-15T01:00:00+02:00"

def test_last_day(conn, imported):
    assert last_day(conn, imported) == date(2025, 5, 15)

def test_correction_applies(conn, imported):
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    set_activity_mode(conn, act["id"], "cycling")
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    assert (act["mode"], act["corrected"]) == ("cycling", True)

def test_correction_survives_reimport(conn, imported, sample_bytes):
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    set_activity_mode(conn, act["id"], "cycling")
    import_file(conn, imported, "again.json", sample_bytes, datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert get_day(conn, imported, date(2025, 5, 14))[1]["mode"] == "cycling"

def test_invalid_mode_rejected(conn, imported):
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    with pytest.raises(ValueError):
        set_activity_mode(conn, act["id"], "teleport")
```

Add the `imported` fixture to `tests/conftest.py` (person with the sample file imported).

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_dayview.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement `app/dayview.py`** per the rules above (join `places` on `place_key` for name/category/address; `resolved = places.status == 'done'`).
- [ ] **Step 4: Run** `.venv/bin/pytest -v` — Expected: all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: day view and transport corrections"`

---

### Task 4: Text normalisation + place names from OpenStreetMap

**Files:**
- Create: `app/textnorm.py`, `app/places.py`, `tests/test_places.py`

**Interfaces:**
- Consumes: DB from Task 2.
- Produces:
  - `textnorm.norm(s: str | None) -> str` — lowercase, NFKD with combining marks removed, `ł→l`; `None → ""`
  - `places.classify(tags: dict) -> str | None` — Polish label from OSM tags
  - `places.pick_poi(elements: list[dict], lat: float, lng: float) -> tuple[dict | None, list[dict]]` — candidates `{"osm_id": "node/123", "name", "category", "distance_m"}` sorted by distance (only elements with `name`); selected = nearest candidate
  - `class OsmClient(contact: str, min_interval_s: float = 1.1)` with `nearby(lat, lng, radius_m: int = 50) -> list[dict]` (Overpass elements) and `reverse(lat, lng) -> str | None` (Nominatim `display_name`, shortened to street + number + city where available). Raises `OsmError` on HTTP error/timeout. Enforces `min_interval_s` between any two requests.
  - `places.process_one(conn, client, now: datetime) -> bool` — handles one due pending place; returns `False` when nothing is due
  - `places.progress(conn) -> dict` → `{"done": int, "total": int}`
  - `places.run_worker(db_path: Path, client, stop: threading.Event) -> None` — loop: `process_one`, sleep 5 s when idle

Values: Overpass endpoint `https://overpass-api.de/api/interpreter`, query
`[out:json][timeout:25];nwr(around:{r},{lat},{lng})[name][~"^(shop|amenity|tourism|leisure|office|craft|healthcare)$"~"."];out center tags;`
Nominatim `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=..&lon=..&zoom=18&accept-language=pl`. HTTP timeout 30 s. Backoff on failure: `next_try_at = now + min(2**attempts, 360)` minutes, `attempts += 1`, status stays `pending`. Address is fetched via `reverse` only when no POI is found. Category labels (at least): `shop=supermarket→supermarket`, `shop=convenience→sklep spożywczy`, `shop=mall→centrum handlowe`, `shop=bakery→piekarnia`, `amenity=fuel→stacja paliw`, `amenity=pharmacy→apteka`, `amenity=restaurant→restauracja`, `amenity=cafe→kawiarnia`, `amenity=fast_food→fast food`, `amenity=parking→parking`, `leisure=park→park`; fallback: tag value with `_` → space.

- [ ] **Step 1: Write failing tests in `tests/test_places.py`** (use a `FakeClient` with canned `nearby`/`reverse` results or raising `OsmError`; no network)

```python
def test_norm():
    assert norm("Żabka ŁÓDŹ") == "zabka lodz" and norm(None) == ""

def test_classify():
    assert classify({"shop": "supermarket"}) == "supermarket"
    assert classify({"amenity": "fuel"}) == "stacja paliw"
    assert classify({"shop": "garden_centre"}) == "garden centre"

def test_pick_poi_nearest_and_all_candidates():
    els = [{"type": "node", "id": 1, "lat": 52.2003, "lon": 21.2, "tags": {"name": "Apteka", "amenity": "pharmacy"}},
           {"type": "node", "id": 2, "lat": 52.2001, "lon": 21.2, "tags": {"name": "Lidl", "shop": "supermarket"}},
           {"type": "node", "id": 3, "lat": 52.2001, "lon": 21.2, "tags": {"shop": "kiosk"}}]
    sel, cands = pick_poi(els, 52.2, 21.2)
    assert sel["osm_id"] == "node/2" and [c["name"] for c in cands] == ["Lidl", "Apteka"]

def test_process_one_stores_poi_and_candidates(conn, imported):
    client = FakeClient(nearby=[...Lidl and Apteka elements...])
    while process_one(conn, client, NOW): pass
    row = conn.execute("SELECT * FROM places WHERE place_key='PLIDL'").fetchone()
    assert (row["status"], row["name"], row["category"], row["osm_id"]) == ("done", "Lidl", "supermarket", "node/2")
    assert len(json.loads(row["candidates"])) == 2
    assert progress(conn) == {"done": 5, "total": 5}

def test_no_poi_falls_back_to_address(conn, imported):
    client = FakeClient(nearby=[], reverse="Puławska 12, Warszawa")
    process_one(conn, client, NOW)
    row = conn.execute("SELECT * FROM places WHERE status='done'").fetchone()
    assert row["name"] is None and row["address"] == "Puławska 12, Warszawa"

def test_failure_backs_off_and_continues(conn, imported):
    client = FakeClient(fail_keys={"PHOME"})        # raises OsmError for PHOME's coordinates (52.0, 21.0)
    while process_one(conn, client, NOW): pass
    home = conn.execute("SELECT * FROM places WHERE place_key='PHOME'").fetchone()
    assert home["status"] == "pending" and home["attempts"] == 1
    assert progress(conn) == {"done": 4, "total": 5}
    assert process_one(conn, client, NOW) is False             # not due yet
    assert process_one(conn, client, NOW + timedelta(minutes=3)) is True
```

(Fill the `...` with the element dicts from `test_pick_poi_nearest_and_all_candidates`.)

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_places.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement `app/textnorm.py` and `app/places.py`.** Distance: haversine. `OsmClient` uses `httpx.Client` with the `User-Agent` from Global Constraints. `run_worker` opens its own connection.
- [ ] **Step 4: Run** `.venv/bin/pytest -v` — Expected: all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: OSM place names with background worker"`

---

### Task 5: Search

**Files:**
- Create: `app/search.py`, `tests/test_search.py`

**Interfaces:**
- Consumes: `norm` (Task 4), `display_name` (Task 3), DB.
- Produces: `search(conn, q: str, date_from: date | None, date_to: date | None, person_id: int | None) -> list[dict]` — groups sorted by `total_s` desc:
  `{"key", "name", "category", "address", "count", "total_s", "possible_count", "visits": [{"visit_id", "person_id", "person_name", "date", "start", "end", "duration_s", "possible": bool, "primary_name": str | None}]}`

Rules: a visit matches when it overlaps the date range (`local_start_date <= date_to and local_end_date >= date_from`; missing bound = open). With `q` empty: every visit is a confident match. With `q`: confident if `norm(q)` is a substring of `norm(display_name)` or `norm(category)`; otherwise *possible* for each candidate (other than the selected POI) whose `norm(name)` or `norm(category)` contains `norm(q)`. Group key: selected `osm_id` if any, else `place_key`, for confident; candidate `osm_id` for possible. `count`/`total_s` sum confident visits only; `possible_count` counts possible ones; `primary_name` is the visit's displayed name for possible ones (else `None`). Group name/category/address come from a confident visit's place, else from the candidate. Visits inside a group sorted newest first. `person_name` comes from `persons`.

- [ ] **Step 1: Write failing tests in `tests/test_search.py`** — fixture `resolved`: `imported` + set PLIDL to done with name "Lidl", osm_id "node/2", candidates Lidl + `{"osm_id": "node/1", "name": "Apteka Żywa", "category": "apteka"}`; set PSHOP to done with name "Żabka", category "sklep spożywczy", osm_id "node/9", candidates `[{"osm_id": "node/2", "name": "Lidl", "category": "supermarket", "distance_m": 40}]`.

```python
def test_confident_and_possible_in_same_group(conn, resolved):
    groups = search(conn, "lidl", None, None, None)
    assert len(groups) == 1 and groups[0]["key"] == "node/2"
    assert (groups[0]["count"], groups[0]["possible_count"]) == (1, 1)
    possible = [v for v in groups[0]["visits"] if v["possible"]][0]
    assert possible["primary_name"] == "Żabka"

def test_search_ignores_case_and_diacritics(conn, resolved):
    assert [g["name"] for g in search(conn, "ZABKA", None, None, None)] == ["Żabka"]
    assert search(conn, "apteka zywa", None, None, None)[0]["possible_count"] == 1

def test_category_match(conn, resolved):
    assert [g["name"] for g in search(conn, "spożywczy", None, None, None)] == ["Żabka"]

def test_empty_query_with_range_lists_all_by_total_time(conn, resolved):
    groups = search(conn, "", date(2025, 5, 14), date(2025, 5, 14), None)
    assert len(groups) == 5 and groups[-1]["name"] == "Lidl"   # shortest: 17 min
    assert all(g["possible_count"] == 0 for g in groups)

def test_date_range_and_person_filter(conn, resolved):
    assert search(conn, "lidl", date(2025, 6, 1), None, None) == []
    other = create_person(conn, "Ala")
    assert search(conn, "lidl", None, None, other) == []
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_search.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement `app/search.py`** (load candidate visits with one SQL join, filter/group in Python — data volume is a few thousand rows).
- [ ] **Step 4: Run** `.venv/bin/pytest -v` — Expected: all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: search with nearby-place matches"`

---

### Task 6: HTTP API

**Files:**
- Create: `app/main.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `create_app(data_dir: Path, start_worker: bool = True, osm_contact: str = "") -> FastAPI` — creates `data_dir/uploads`, DB at `data_dir/oscz.db`, starts `run_worker` thread in lifespan when `start_worker`, mounts `static/` at `/`.
  - `app_from_env() -> FastAPI` — `DATA_DIR` (default `./data`), `OSM_CONTACT` (default `""`), `OSM_WORKER` (`"0"` disables).
  - Routes (JSON):

| Method & path | Body / query | Response |
|---|---|---|
| `GET /api/persons` | | `[{"id","name"}]` |
| `POST /api/persons` | `{"name"}` | `{"id","name"}`; 400 blank |
| `PATCH /api/persons/{id}` | `{"name"}` | `{"id","name"}` |
| `DELETE /api/persons/{id}` | | 204 |
| `POST /api/persons/{id}/files` | multipart `file` | `ImportReport` as dict; 400 `{"detail": UNSUPPORTED_MESSAGE}` |
| `GET /api/persons/{id}/day` | `date=YYYY-MM-DD` (optional → `last_day`) | `{"date": str or null, "items": [...]}` |
| `PUT /api/activities/{id}/mode` | `{"mode"}` | 204; 400 bad mode; 404 unknown |
| `GET /api/modes` | | `MODES` |
| `GET /api/places/progress` | | `{"done","total"}` |
| `GET /api/search` | `q, from, to, person_id` (all optional) | search groups |

Unknown person id → 404. The uploaded raw bytes are saved to `uploads/{file_id}_{safe filename}` only after a successful import. Each request uses its own `db.connect`.

- [ ] **Step 1: Write failing tests in `tests/test_api.py`** using `fastapi.testclient.TestClient(create_app(tmp_path, start_worker=False))`

```python
def test_upload_flow(client, sample_bytes):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    r = client.post(f"/api/persons/{pid}/files", files={"file": ("Timeline.json", sample_bytes)})
    assert r.status_code == 200 and r.json()["added_visits"] == 5
    day = client.get(f"/api/persons/{pid}/day").json()
    assert day["date"] == "2025-05-15"
    assert len(client.get(f"/api/persons/{pid}/day", params={"date": "2025-05-14"}).json()["items"]) == 5
    assert client.get("/api/places/progress").json() == {"done": 0, "total": 5}

def test_bad_upload_returns_polish_message(client):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    r = client.post(f"/api/persons/{pid}/files", files={"file": ("x.json", b'{"semanticSegments": []}')})
    assert r.status_code == 400 and r.json()["detail"] == UNSUPPORTED_MESSAGE

def test_mode_update_and_errors(client, sample_bytes): ...   # PUT valid → 204; "teleport" → 400; id 999 → 404
def test_unknown_person_404(client): ...                     # GET /api/persons/999/day → 404
def test_search_endpoint(client, sample_bytes): ...          # after upload, GET /api/search?from=2025-05-14&to=2025-05-14 → 5 groups
def test_index_served(client): ...                           # GET / → 200, "text/html"
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_api.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement `app/main.py`** (create a minimal `static/index.html` placeholder so `test_index_served` passes; Task 7 replaces it).
- [ ] **Step 4: Run** `.venv/bin/pytest -v` — Expected: all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: HTTP API"`

---

### Task 7: Frontend (layout A)

**Files:**
- Create/replace: `static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Consumes: routes from Task 6. Leaflet 1.9.4 from `https://unpkg.com/leaflet@1.9.4/dist/leaflet.css` and `.../leaflet.js`; tiles `https://tile.openstreetmap.org/{z}/{x}/{y}.png` with OSM attribution.

Behaviour (spec §6.3–6.5):
- **Top bar:** person `<select>` (+ "Dodaj osobę…", rename, delete with `confirm()`), search button, "Wczytaj plik" button, places progress `Nazwy miejsc: 312/733` (polled every 5 s while `done < total`, hidden when equal).
- **Left panel (≈38% width):** `◀ [date input] ▶` (◀/▶ move one calendar day); chronological list. Visit row: category icon, name (bold), category, `HH:MM – HH:MM · N min`; unresolved shows `Nieznane miejsce` + `lat, lng` (5 decimals); `children` indented under parent. Activity row: mode icon + Polish label, `X,X km · N min`; click opens a `<select>` of `/api/modes` → `PUT`, then reload the day; corrected rows show "(poprawione)".
- **Map:** polylines per activity coloured by mode with a legend; circle markers per visit. Clicking a list row fits/highlights the map item; clicking a marker/line highlights and scrolls to the list row. Fit bounds to the day on load.
- **Default day:** `GET /day` without date (last day with data); empty state "Brak danych — wczytaj plik" when `date` is null.
- **Upload dialog:** person select or new name, file input, submit → show report (`Dodano X wizyt i Y przejazdów, zastąpiono Z, pominięto W`) or the 400 `detail`.
- **Search** (replaces the list in the left panel, with "← Wróć do dnia"): text, from, to, person (current / "Wszystkie osoby"). Group header: `name — address`, `N wizyt · łącznie H h MM min` + ` · +K możliwe` when `possible_count > 0`; expandable table Data / Od–do / Czas / „pokaż dzień →” (switches person and day). Possible rows: muted italic, label `możliwie tutaj (główny: {primary_name})`.
- Polish labels for modes: samochód, rower, pieszo, bieg, autobus, pociąg, tramwaj, metro, prom, samolot, motocykl, narty, żeglowanie, nieznany.
- Width ≥ 1024 px is the target; below 768 px stack list above map.

- [ ] **Step 1: Implement the three files.**
- [ ] **Step 2: Verify manually** — `DATA_DIR=./data OSM_CONTACT=test .venv/bin/uvicorn --factory app.main:app_from_env --port 8000`, open `http://localhost:8000`, create person, upload the real `Timeline.json`. Expected: import report shows ~1760 visits / ~1830 activities; the last day renders with lines and markers; ◀/▶ and date input work; correcting a mode persists after reload; progress counter increases ~1 per second; search "lidl" (or any resolved name) returns grouped results; a bad file shows the Polish error. Run `.venv/bin/pytest -v` — all PASS.
- [ ] **Step 3: Commit** — `git commit -m "feat: frontend"`

---

### Task 8: Docker packaging + README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `README.md`

**Interfaces:**
- Consumes: `app_from_env` (Task 6).

Values: base `python:3.12-slim`; `pip install --no-cache-dir -r requirements.txt`; copy `app/` and `static/`; `ENV DATA_DIR=/data`; `EXPOSE 8000`; `CMD ["uvicorn", "--factory", "app.main:app_from_env", "--host", "0.0.0.0", "--port", "8000"]`. Compose: service `oscz`, `build: .`, `ports: ["8080:8000"]`, `volumes: ["./data:/data"]`, `environment: OSM_CONTACT: "${OSM_CONTACT:-}"`, `restart: unless-stopped`. `.dockerignore`: `.git`, `.venv`, `data`, `tests`, `docs`, `.superpowers`, `*.json`. README (Polish): what it is, `OSM_CONTACT=twoj@email docker compose up -d --build`, open `http://<serwer>:8080`, where data lives, backup = copy `data/`, how to export Timeline from the phone (Ustawienia → Lokalizacja → Oś czasu → Eksportuj).

- [ ] **Step 1: Write the four files.**
- [ ] **Step 2: Verify** — `OSM_CONTACT=test docker compose up -d --build && sleep 5 && curl -fsS localhost:8080/api/persons` → `[]` (or existing persons). Upload the real file through the UI at `http://localhost:8080`, then `docker compose restart` and confirm data and progress persist. `docker compose down`.
- [ ] **Step 3: Commit** — `git commit -m "feat: docker packaging and README"`
