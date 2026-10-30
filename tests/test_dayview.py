from datetime import date, datetime, timezone

import pytest

from app.dayview import get_day, last_day, set_activity_mode
from app.importer import import_file


def test_day_items_order_and_nesting(conn, imported):
    items = get_day(conn, imported, date(2025, 5, 14))
    assert [i["kind"] for i in items] == ["visit", "activity", "visit", "visit", "visit"]
    mall = items[2]
    assert [c["place_key"] for c in mall["children"]] == ["PSHOP"]
    assert items[0]["name"] == "Dom" and items[3]["name"] == "Nieznane miejsce"
    assert items[3]["start"] == "2025-05-14T16:24:00+02:00" and items[3]["duration_s"] == 17 * 60


def test_activity_mode_without_geometry(conn, imported):
    act = get_day(conn, imported, date(2025, 5, 14))[1]
    assert act["mode"] == "in passenger vehicle" and act["corrected"] is False
    assert "geometry" not in act


def test_visit_across_midnight_on_both_days(conn, imported):
    next_day = get_day(conn, imported, date(2025, 5, 15))
    assert [i["place_key"] for i in next_day] == ["52.2500,21.2500"]
    assert next_day[0]["end"] == "2025-05-15T01:00:00+02:00"


def test_last_day(conn, imported):
    assert last_day(conn, imported) == date(2025, 5, 15)


def test_last_day_without_data(conn, person):
    assert last_day(conn, person) is None


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


def test_unknown_activity_rejected(conn, imported):
    with pytest.raises(KeyError):
        set_activity_mode(conn, 999, "cycling")


def test_home_does_not_show_nearby_shop_category(conn, imported):
    with conn:
        conn.execute("UPDATE places SET status='done', name='Żabka', category='sklep spożywczy'"
                     " WHERE place_key='PHOME'")
    home = get_day(conn, imported, date(2025, 5, 14))[0]
    assert (home["name"], home["category"]) == ("Dom", None)
