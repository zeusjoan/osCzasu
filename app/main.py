"""FastAPI application: JSON API, static frontend and the place-name worker."""
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, dayview, importer, places, search
from app.parser import UnsupportedFormat

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class NameBody(BaseModel):
    name: str


class ModeBody(BaseModel):
    mode: str


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100] or "timeline.json"


def create_app(data_dir: Path, start_worker: bool = True, osm_contact: str = "") -> FastAPI:
    data_dir = Path(data_dir)
    uploads = data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "oscz.db"
    with db.connect(db_path) as c:
        db.init_schema(c)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        worker = None
        if start_worker:
            worker = threading.Thread(target=places.run_worker,
                                      args=(db_path, places.OsmClient(osm_contact), stop), daemon=True)
            worker.start()
        yield
        stop.set()
        if worker:
            worker.join(timeout=5)

    app = FastAPI(title="Oś Czasu", lifespan=lifespan)

    def get_conn():
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_person(conn, person_id: int) -> None:
        if conn.execute("SELECT 1 FROM persons WHERE id=?", (person_id,)).fetchone() is None:
            raise HTTPException(404, "Nie ma takiej osoby.")

    @app.get("/api/persons")
    def list_persons(conn=Depends(get_conn)):
        return importer.list_persons(conn)

    @app.post("/api/persons")
    def create_person(body: NameBody, conn=Depends(get_conn)):
        try:
            pid = importer.create_person(conn, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": pid, "name": body.name.strip()}

    @app.patch("/api/persons/{person_id}")
    def rename_person(person_id: int, body: NameBody, conn=Depends(get_conn)):
        require_person(conn, person_id)
        try:
            importer.rename_person(conn, person_id, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": person_id, "name": body.name.strip()}

    @app.delete("/api/persons/{person_id}", status_code=204)
    def delete_person(person_id: int, conn=Depends(get_conn)):
        require_person(conn, person_id)
        importer.delete_person(conn, person_id)
        return Response(status_code=204)

    @app.post("/api/persons/{person_id}/files")
    async def upload(person_id: int, file: UploadFile, conn=Depends(get_conn)):
        require_person(conn, person_id)
        raw = await file.read()
        try:
            report = importer.import_file(conn, person_id, file.filename or "timeline.json", raw,
                                          datetime.now(timezone.utc))
        except UnsupportedFormat as e:
            raise HTTPException(400, str(e))
        (uploads / f"{report.file_id}_{_safe_filename(file.filename or '')}").write_bytes(raw)
        return report.__dict__

    @app.get("/api/persons/{person_id}/day")
    def get_day(person_id: int, date: date | None = None, conn=Depends(get_conn)):
        require_person(conn, person_id)
        day = date or dayview.last_day(conn, person_id)
        if day is None:
            return {"date": None, "items": []}
        return {"date": day.isoformat(), "items": dayview.get_day(conn, person_id, day)}

    @app.put("/api/activities/{activity_id}/mode", status_code=204)
    def set_mode(activity_id: int, body: ModeBody, conn=Depends(get_conn)):
        try:
            dayview.set_activity_mode(conn, activity_id, body.mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except KeyError:
            raise HTTPException(404, "Nie ma takiego przejazdu.")
        return Response(status_code=204)

    @app.get("/api/modes")
    def modes():
        return dayview.MODES

    @app.get("/api/places/progress")
    def progress(conn=Depends(get_conn)):
        return places.progress(conn)

    @app.get("/api/search")
    def do_search(q: str = "", person_id: int | None = None,
                  date_from: date | None = Query(None, alias="from"),
                  date_to: date | None = Query(None, alias="to"), conn=Depends(get_conn)):
        return search.search(conn, q, date_from, date_to, person_id)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def app_from_env() -> FastAPI:
    return create_app(
        Path(os.environ.get("DATA_DIR", "./data")),
        start_worker=os.environ.get("OSM_WORKER", "1") != "0",
        osm_contact=os.environ.get("OSM_CONTACT", ""),
    )
