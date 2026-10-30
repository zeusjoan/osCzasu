"""Parse the on-device Google Timeline export (a JSON array of segments)."""
from dataclasses import dataclass
from datetime import datetime

UNSUPPORTED_MESSAGE = (
    "Nieobsługiwany format pliku. Obsługiwany jest eksport osi czasu z telefonu (plik Timeline.json)."
)


class UnsupportedFormat(Exception):
    pass


@dataclass
class Visit:
    start: datetime
    end: datetime
    place_id: str | None
    lat: float
    lng: float
    semantic_type: str
    hierarchy_level: int


@dataclass
class Activity:
    start: datetime
    end: datetime
    mode: str
    distance_m: float
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float


@dataclass
class ParsedTimeline:
    visits: list[Visit]
    activities: list[Activity]
    skipped: int


def _geo(s: str) -> tuple[float, float]:
    if not s.startswith("geo:"):
        raise ValueError(f"not a geo string: {s!r}")
    lat, lng = s[4:].split(",")
    return float(lat), float(lng)


def _time(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError(f"timestamp without offset: {s!r}")
    return t


def _visit(e: dict) -> Visit:
    v = e["visit"]
    top = v["topCandidate"]
    lat, lng = _geo(top["placeLocation"])
    return Visit(
        start=_time(e["startTime"]),
        end=_time(e["endTime"]),
        place_id=top.get("placeID") or None,
        lat=lat,
        lng=lng,
        semantic_type=top.get("semanticType", "Unknown"),
        hierarchy_level=int(v.get("hierarchyLevel", "0")),
    )


def _activity(e: dict) -> Activity:
    a = e["activity"]
    s_lat, s_lng = _geo(a["start"])
    e_lat, e_lng = _geo(a["end"])
    return Activity(
        start=_time(e["startTime"]),
        end=_time(e["endTime"]),
        mode=a.get("topCandidate", {}).get("type", "unknown"),
        distance_m=float(a.get("distanceMeters", "0")),
        start_lat=s_lat,
        start_lng=s_lng,
        end_lat=e_lat,
        end_lng=e_lng,
    )


def parse_timeline(data: object) -> ParsedTimeline:
    if not isinstance(data, list):
        raise UnsupportedFormat(UNSUPPORTED_MESSAGE)
    result = ParsedTimeline(visits=[], activities=[], skipped=0)
    for e in data:
        try:
            if "visit" in e:
                result.visits.append(_visit(e))
            elif "activity" in e:
                result.activities.append(_activity(e))
            elif "timelinePath" in e or "timelineMemory" in e:
                continue
            else:
                result.skipped += 1
        except (KeyError, ValueError, TypeError, AttributeError):
            result.skipped += 1
    return result

