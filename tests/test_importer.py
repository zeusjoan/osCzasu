import json
from datetime import datetime, timedelta, timezone

import pytest

from app.importer import create_person, delete_person, import_file, list_persons, rename_person
from app.parser import UnsupportedFormat

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def count(conn, table, where="1=1", args=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


def test_import_counts_and_queues_places(conn, person, sample_bytes):
    r = import_file(conn, person, "Timeline.json", sample_bytes, NOW)
    assert (r.added_visits, r.added_activities, r.replaced, r.skipped) == (5, 1, 0, 2)
    keys = {row[0] for row in conn.execute("SELECT place_key FROM places")}
    assert keys == {"PHOME", "PMALL", "PSHOP", "PLIDL", "52.2500,21.2500"}
    assert count(conn, "places", "status='pending'") == 5


def test_reimport_same_file_no_duplicates(conn, person, sample_bytes):
    import_file(conn, person, "a.json", sample_bytes, NOW)
    r = import_file(conn, person, "a.json", sample_bytes, NOW + timedelta(minutes=1))
    assert r.replaced == 6
    assert count(conn, "visits") == 5
    assert count(conn, "activities") == 1


def test_newer_file_replaces_only_entries_inside_its_range(conn, person, sample_bytes):
    import_file(conn, person, "old.json", sample_bytes, NOW)
    newer = [{"startTime": "2025-05-14T16:20:00.000+02:00", "endTime": "2025-05-14T16:45:00.000+02:00",
              "visit": {"hierarchyLevel": "0", "topCandidate": {"semanticType": "Unknown", "placeID": "PLIDL2",
                        "placeLocation": "geo:52.200000,21.200000"}}}]
    r = import_file(conn, person, "new.json", json.dumps(newer).encode(), NOW + timedelta(days=1))
    assert r.replaced == 1  # only the 16:24–16:41 Lidl visit
    ids = {row[0] for row in conn.execute("SELECT place_key FROM visits")}
    assert "PLIDL" not in ids and "PLIDL2" in ids and "PHOME" in ids


def test_import_does_not_touch_other_person(conn, person, sample_bytes):
    other = create_person(conn, "Ala")
    import_file(conn, other, "a.json", sample_bytes, NOW)
    import_file(conn, person, "a.json", sample_bytes, NOW)
    assert count(conn, "visits", "person_id=?", (other,)) == 5


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe", b'{"semanticSegments": []}', b"[]"])
def test_unsupported_file_writes_nothing(conn, person, raw):
    with pytest.raises(UnsupportedFormat):
        import_file(conn, person, "x.json", raw, NOW)
    assert count(conn, "files") == 0


def test_delete_person_cascades(conn, person, sample_bytes):
    import_file(conn, person, "a.json", sample_bytes, NOW)
    delete_person(conn, person)
    assert count(conn, "visits") == 0
    assert count(conn, "places") == 5  # cache stays


def test_blank_person_name_rejected(conn):
    with pytest.raises(ValueError):
        create_person(conn, "  ")


def test_list_and_rename_persons(conn, person):
    create_person(conn, "Ala")
    rename_person(conn, person, "Zenon")
    assert [p["name"] for p in list_persons(conn)] == ["Ala", "Zenon"]
