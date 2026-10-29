"""Place names from OpenStreetMap (Overpass for nearby POIs, Nominatim for addresses) and the background worker."""
import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app import db

log = logging.getLogger(__name__)

OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
POI_KEYS = ("shop", "amenity", "tourism", "leisure", "office", "craft", "healthcare")

CATEGORY_LABELS = {
    ("shop", "supermarket"): "supermarket",
    ("shop", "convenience"): "sklep spożywczy",
    ("shop", "mall"): "centrum handlowe",
    ("shop", "bakery"): "piekarnia",
    ("shop", "butcher"): "mięsny",
    ("shop", "clothes"): "odzież",
    ("shop", "doityourself"): "market budowlany",
    ("shop", "hardware"): "sklep żelazny",
    ("amenity", "fuel"): "stacja paliw",
    ("amenity", "pharmacy"): "apteka",
    ("amenity", "restaurant"): "restauracja",
    ("amenity", "cafe"): "kawiarnia",
    ("amenity", "fast_food"): "fast food",
    ("amenity", "parking"): "parking",
    ("amenity", "bank"): "bank",
    ("amenity", "school"): "szkoła",
    ("amenity", "kindergarten"): "przedszkole",
    ("amenity", "place_of_worship"): "kościół",
    ("amenity", "hospital"): "szpital",
    ("amenity", "doctors"): "przychodnia",
    ("amenity", "post_office"): "poczta",
    ("leisure", "park"): "park",
    ("leisure", "playground"): "plac zabaw",
    ("leisure", "sports_centre"): "centrum sportowe",
    ("tourism", "hotel"): "hotel",
}


class OsmError(Exception):
    pass


def classify(tags: dict) -> str | None:
    for key in POI_KEYS:
        value = tags.get(key)
        if value:
            return CATEGORY_LABELS.get((key, value), value.replace("_", " "))
    return None


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def pick_poi(elements: list[dict], lat: float, lng: float) -> tuple[dict | None, list[dict]]:
    candidates = []
    for el in elements:
        tags = el.get("tags", {})
        if not tags.get("name"):
            continue
        el_lat = el.get("lat", el.get("center", {}).get("lat"))
        el_lng = el.get("lon", el.get("center", {}).get("lon"))
        if el_lat is None or el_lng is None:
            continue
        candidates.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "name": tags["name"],
            "category": classify(tags),
            "distance_m": round(_distance_m(lat, lng, el_lat, el_lng)),
        })
    candidates.sort(key=lambda c: c["distance_m"])
    return (candidates[0] if candidates else None), candidates


class OsmClient:
    def __init__(self, contact: str, min_interval_s: float = 1.1):
        agent = f"OsCzasu/0.1 ({contact})" if contact else "OsCzasu/0.1"
        self._http = httpx.Client(timeout=30, headers={"User-Agent": agent})
        self._min_interval = min_interval_s
        self._last = 0.0

    def _wait(self) -> None:
        delay = self._last + self._min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def nearby(self, lat: float, lng: float, radius_m: int = 50) -> list[dict]:
        keys = "|".join(POI_KEYS)
        query = (f'[out:json][timeout:25];nwr(around:{radius_m},{lat},{lng})[name][~"^({keys})$"~"."];'
                 "out center tags;")
        self._wait()
        try:
            r = self._http.post(OVERPASS_URL, data={"data": query})
            r.raise_for_status()
            return r.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as e:
            raise OsmError(str(e)) from e

    def reverse(self, lat: float, lng: float) -> str | None:
        self._wait()
        try:
            r = self._http.get(NOMINATIM_URL, params={
                "format": "jsonv2", "lat": lat, "lon": lng, "zoom": 18, "accept-language": "pl"})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OsmError(str(e)) from e
        return short_address(data)


def short_address(data: dict) -> str | None:
    """'Street 12, City' from a Nominatim reverse result, falling back to the full display name."""
    addr = data.get("address", {})
    road = addr.get("road") or addr.get("pedestrian") or addr.get("square") or addr.get("footway")
    street = " ".join(x for x in (road, addr.get("house_number")) if x)
    city = addr.get("city") or addr.get("town") or addr.get("village")
    short = ", ".join(x for x in (street, city) if x)
    return short or data.get("display_name")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def process_one(conn: sqlite3.Connection, client, now: datetime) -> bool:
    row = conn.execute(
        "SELECT * FROM places WHERE status='pending' AND next_try_at<=? ORDER BY next_try_at, rowid LIMIT 1",
        (_iso(now),),
    ).fetchone()
    if row is None:
        return False
    try:
        selected, candidates = pick_poi(client.nearby(row["lat"], row["lng"]), row["lat"], row["lng"])
        address = None if selected else client.reverse(row["lat"], row["lng"])
    except OsmError as e:
        attempts = row["attempts"] + 1
        retry = now + timedelta(minutes=min(2 ** attempts, 360))
        log.warning("OSM lookup failed for %s (attempt %d): %s", row["place_key"], attempts, e)
        with conn:
            conn.execute("UPDATE places SET attempts=?, next_try_at=? WHERE place_key=?",
                         (attempts, _iso(retry), row["place_key"]))
        return True
    with conn:
        conn.execute(
            "UPDATE places SET status='done', osm_id=?, name=?, category=?, address=?, candidates=?"
            " WHERE place_key=?",
            (selected and selected["osm_id"], selected and selected["name"], selected and selected["category"],
             address, json.dumps(candidates, ensure_ascii=False), row["place_key"]),
        )
    return True


def progress(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT SUM(status='done'), COUNT(*) FROM places").fetchone()
    return {"done": row[0] or 0, "total": row[1]}


def run_worker(db_path: Path, client, stop: threading.Event) -> None:
    conn = db.connect(db_path)
    try:
        while not stop.is_set():
            try:
                busy = process_one(conn, client, datetime.now(timezone.utc))
            except Exception:  # keep the worker alive whatever happens
                log.exception("place worker error")
                busy = False
            if not busy:
                stop.wait(5)
    finally:
        conn.close()
