import json
from datetime import datetime, timedelta, timezone

from app.places import OsmError, classify, pick_poi, process_one, progress
from app.textnorm import norm

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

ELEMENTS = [
    {"type": "node", "id": 1, "lat": 52.2003, "lon": 21.2, "tags": {"name": "Apteka", "amenity": "pharmacy"}},
    {"type": "node", "id": 2, "lat": 52.2001, "lon": 21.2, "tags": {"name": "Lidl", "shop": "supermarket"}},
    {"type": "node", "id": 3, "lat": 52.2001, "lon": 21.2, "tags": {"shop": "kiosk"}},
]


class FakeClient:
    def __init__(self, nearby=(), reverse=None, fail_coords=()):
        self._nearby, self._reverse, self._fail = list(nearby), reverse, set(fail_coords)

    def nearby(self, lat, lng, radius_m=50):
        if (lat, lng) in self._fail:
            raise OsmError("boom")
        return self._nearby

    def reverse(self, lat, lng):
        return self._reverse


def test_norm():
    assert norm("Żabka ŁÓDŹ") == "zabka lodz" and norm(None) == ""


def test_classify():
    assert classify({"shop": "supermarket"}) == "supermarket"
    assert classify({"amenity": "fuel"}) == "stacja paliw"
    assert classify({"shop": "garden_centre"}) == "garden centre"
    assert classify({"name": "x"}) is None


def test_pick_poi_nearest_and_all_candidates():
    sel, cands = pick_poi(ELEMENTS, 52.2, 21.2)
    assert sel["osm_id"] == "node/2" and [c["name"] for c in cands] == ["Lidl", "Apteka"]
    assert cands[1]["category"] == "apteka"


def test_pick_poi_uses_way_center():
    els = [{"type": "way", "id": 7, "center": {"lat": 52.2, "lon": 21.2}, "tags": {"name": "Park", "leisure": "park"}}]
    sel, _ = pick_poi(els, 52.2, 21.2)
    assert sel["osm_id"] == "way/7" and sel["distance_m"] == 0


def test_process_one_stores_poi_and_candidates(conn, imported):
    client = FakeClient(nearby=ELEMENTS)
    while process_one(conn, client, NOW):
        pass
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
    client = FakeClient(nearby=ELEMENTS, fail_coords={(52.0, 21.0)})  # PHOME
    while process_one(conn, client, NOW):
        pass
    home = conn.execute("SELECT * FROM places WHERE place_key='PHOME'").fetchone()
    assert home["status"] == "pending" and home["attempts"] == 1
    assert progress(conn) == {"done": 4, "total": 5}
    assert process_one(conn, client, NOW) is False  # not due yet
    assert process_one(conn, client, NOW + timedelta(minutes=3)) is True


def test_short_address_uses_square_or_pedestrian_when_no_road():
    from app.places import short_address
    assert short_address({"address": {"square": "plac Defilad", "house_number": "1", "city": "Warszawa"}}) \
        == "plac Defilad 1, Warszawa"
    assert short_address({"address": {"road": "Puławska", "house_number": "12", "town": "Piaseczno"}}) \
        == "Puławska 12, Piaseczno"
    assert short_address({"address": {}, "display_name": "Gdzieś"}) == "Gdzieś"


def test_overpass_remark_error_is_a_failure(conn, imported):
    from app.places import check_overpass
    import pytest
    with pytest.raises(OsmError):
        check_overpass({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 1"})
    assert check_overpass({"elements": [1]}) == [1]


def test_pick_poi_builds_address_from_tags():
    els = [{"type": "node", "id": 2, "lat": 52.2, "lon": 21.2, "tags": {
        "name": "Lidl", "shop": "supermarket", "addr:street": "Puławska", "addr:housenumber": "12",
        "addr:city": "Warszawa"}}]
    sel, _ = pick_poi(els, 52.2, 21.2)
    assert sel["address"] == "Puławska 12, Warszawa"


def test_process_one_stores_poi_address(conn, imported):
    els = [{"type": "node", "id": 2, "lat": 52.2, "lon": 21.2, "tags": {
        "name": "Lidl", "shop": "supermarket", "addr:street": "Puławska", "addr:housenumber": "12"}}]
    process_one(conn, FakeClient(nearby=els), NOW)
    assert conn.execute("SELECT address FROM places WHERE status='done'").fetchone()[0] == "Puławska 12"
