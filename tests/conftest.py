from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db
from app.importer import create_person, import_file

FIX = Path(__file__).parent / "fixtures" / "sample_timeline.json"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def sample_bytes():
    return FIX.read_bytes()


@pytest.fixture
def person(conn):
    return create_person(conn, "Jan")


@pytest.fixture
def imported(conn, person, sample_bytes):
    import_file(conn, person, "Timeline.json", sample_bytes, datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    return person
