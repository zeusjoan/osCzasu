import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.parser import UnsupportedFormat, parse_timeline

FIX = Path(__file__).parent / "fixtures" / "sample_timeline.json"


def load():
    return parse_timeline(json.loads(FIX.read_text()))


def test_parse_counts():
    p = load()
    assert len(p.visits) == 5 and len(p.activities) == 1
    assert p.skipped == 2  # malformed visit + unknown key; timelinePath/timelineMemory ignored silently
    assert not hasattr(p, "path_points")


def test_visit_fields():
    v = load().visits
    home = v[0]
    assert (home.place_id, home.lat, home.lng, home.semantic_type, home.hierarchy_level) == (
        "PHOME", 52.0, 21.0, "Home", 0)
    assert home.start.utcoffset() == timedelta(hours=2)
    assert v[4].place_id is None and v[2].hierarchy_level == 1


def test_activity_fields():
    a = load().activities[0]
    assert (a.mode, a.distance_m, a.start_lat, a.end_lng) == ("in passenger vehicle", 12400.0, 52.0, 21.1)


@pytest.mark.parametrize("data", [{"semanticSegments": []}, {"locations": []}, "x", 5, None])
def test_unsupported_formats(data):
    with pytest.raises(UnsupportedFormat):
        parse_timeline(data)
