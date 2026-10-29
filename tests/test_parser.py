import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.parser import UnsupportedFormat, build_geometry, parse_timeline

FIX = Path(__file__).parent / "fixtures" / "sample_timeline.json"


def load():
    return parse_timeline(json.loads(FIX.read_text()))


def test_parse_counts():
    p = load()
    assert len(p.visits) == 5 and len(p.activities) == 1
    assert len(p.path_points) == 3
    assert p.skipped == 2  # malformed visit + unknown key; timelineMemory ignored silently


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


def test_path_point_time_is_window_start_plus_offset():
    pts = load().path_points
    assert datetime(2025, 5, 14, 4, 30, tzinfo=timezone.utc) in [p.time for p in pts]


def test_build_geometry_uses_points_inside_activity_sorted():
    p = load()
    assert build_geometry(p.activities[0], p.path_points) == [
        (52.0, 21.0), (52.05, 21.05), (52.07, 21.07), (52.1, 21.1)]


def test_build_geometry_without_points_is_straight_line():
    p = load()
    assert build_geometry(p.activities[0], []) == [(52.0, 21.0), (52.1, 21.1)]


@pytest.mark.parametrize("data", [{"semanticSegments": []}, {"locations": []}, "x", 5, None])
def test_unsupported_formats(data):
    with pytest.raises(UnsupportedFormat):
        parse_timeline(data)


def test_path_points_are_sorted_by_time():
    times = [p.time for p in load().path_points]
    assert times == sorted(times)
