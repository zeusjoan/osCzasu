import json
from datetime import date

import pytest

from app.importer import create_person
from app.search import search


@pytest.fixture
def resolved(conn, imported):
    lidl = {"osm_id": "node/2", "name": "Lidl", "category": "supermarket", "distance_m": 10}
    apteka = {"osm_id": "node/1", "name": "Apteka Żywa", "category": "apteka", "distance_m": 30}
    zabka = {"osm_id": "node/9", "name": "Żabka", "category": "sklep spożywczy", "distance_m": 5}
    with conn:
        conn.execute(
            "UPDATE places SET status='done', osm_id='node/2', name='Lidl', category='supermarket',"
            " address='Puławska 12, Warszawa', candidates=? WHERE place_key='PLIDL'",
            (json.dumps([lidl, apteka]),))
        conn.execute(
            "UPDATE places SET status='done', osm_id='node/9', name='Żabka', category='sklep spożywczy',"
            " candidates=? WHERE place_key='PSHOP'",
            (json.dumps([zabka, {**lidl, "distance_m": 40}]),))
    return imported


def test_confident_and_possible_in_same_group(conn, resolved):
    groups = search(conn, "lidl", None, None, None)
    assert len(groups) == 1 and groups[0]["key"] == "node/2"
    assert (groups[0]["count"], groups[0]["possible_count"]) == (1, 1)
    assert groups[0]["address"] == "Puławska 12, Warszawa"
    possible = [v for v in groups[0]["visits"] if v["possible"]][0]
    assert possible["primary_name"] == "Żabka"


def test_search_ignores_case_and_diacritics(conn, resolved):
    assert [g["name"] for g in search(conn, "ZABKA", None, None, None)] == ["Żabka"]
    assert search(conn, "apteka zywa", None, None, None)[0]["possible_count"] == 1


def test_category_match(conn, resolved):
    assert [g["name"] for g in search(conn, "spożywczy", None, None, None)] == ["Żabka"]


def test_empty_query_with_range_lists_all_by_total_time(conn, resolved):
    groups = search(conn, "", date(2025, 5, 14), date(2025, 5, 14), None)
    assert len(groups) == 5 and groups[-1]["name"] == "Lidl"  # shortest: 17 min
    assert all(g["possible_count"] == 0 for g in groups)


def test_date_range_and_person_filter(conn, resolved):
    assert search(conn, "lidl", date(2025, 6, 1), None, None) == []
    other = create_person(conn, "Ala")
    assert search(conn, "lidl", None, None, other) == []


def test_visit_fields(conn, resolved):
    v = search(conn, "lidl", None, None, resolved)[0]["visits"][0]
    assert v["person_name"] == "Jan" and v["date"] == "2025-05-14"
    assert v["start"] == "2025-05-14T16:24:00+02:00" and v["duration_s"] == 17 * 60
