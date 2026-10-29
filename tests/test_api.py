import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.parser import UNSUPPORTED_MESSAGE


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as c:
        yield c


@pytest.fixture
def uploaded(client, sample_bytes):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    client.post(f"/api/persons/{pid}/files", files={"file": ("Timeline.json", sample_bytes)})
    return pid


def test_upload_flow(client, sample_bytes, tmp_path):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    r = client.post(f"/api/persons/{pid}/files", files={"file": ("Timeline.json", sample_bytes)})
    assert r.status_code == 200 and r.json()["added_visits"] == 5
    assert len(list((tmp_path / "uploads").iterdir())) == 1
    day = client.get(f"/api/persons/{pid}/day").json()
    assert day["date"] == "2025-05-15"
    assert len(client.get(f"/api/persons/{pid}/day", params={"date": "2025-05-14"}).json()["items"]) == 5
    assert client.get("/api/places/progress").json() == {"done": 0, "total": 5}


def test_bad_upload_returns_polish_message(client, tmp_path):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    r = client.post(f"/api/persons/{pid}/files", files={"file": ("x.json", b'{"semanticSegments": []}')})
    assert r.status_code == 400 and r.json()["detail"] == UNSUPPORTED_MESSAGE
    assert list((tmp_path / "uploads").iterdir()) == []


def test_persons_crud(client):
    assert client.post("/api/persons", json={"name": "  "}).status_code == 400
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    assert client.patch(f"/api/persons/{pid}", json={"name": "Janek"}).json() == {"id": pid, "name": "Janek"}
    assert client.get("/api/persons").json() == [{"id": pid, "name": "Janek"}]
    assert client.delete(f"/api/persons/{pid}").status_code == 204
    assert client.get("/api/persons").json() == []


def test_mode_update_and_errors(client, uploaded):
    act = client.get(f"/api/persons/{uploaded}/day", params={"date": "2025-05-14"}).json()["items"][1]
    assert client.put(f"/api/activities/{act['id']}/mode", json={"mode": "cycling"}).status_code == 204
    assert client.put(f"/api/activities/{act['id']}/mode", json={"mode": "teleport"}).status_code == 400
    assert client.put("/api/activities/999/mode", json={"mode": "cycling"}).status_code == 404
    assert "cycling" in client.get("/api/modes").json()


def test_unknown_person_404(client):
    assert client.get("/api/persons/999/day").status_code == 404
    assert client.post("/api/persons/999/files", files={"file": ("a.json", b"[]")}).status_code == 404


def test_empty_person_day_is_null(client):
    pid = client.post("/api/persons", json={"name": "Jan"}).json()["id"]
    assert client.get(f"/api/persons/{pid}/day").json() == {"date": None, "items": []}


def test_search_endpoint(client, uploaded):
    r = client.get("/api/search", params={"from": "2025-05-14", "to": "2025-05-14"})
    assert r.status_code == 200 and len(r.json()) == 5
    assert client.get("/api/search", params={"q": "x", "person_id": uploaded}).json() == []


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


def test_delete_person_removes_uploaded_files(client, uploaded, tmp_path):
    assert len(list((tmp_path / "uploads").iterdir())) == 1
    client.delete(f"/api/persons/{uploaded}")
    assert list((tmp_path / "uploads").iterdir()) == []
