from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_conn, init_db

STATIC = Path(__file__).parent / "static"

init_db()  # make sure the table and menu exist before serving

app = FastAPI(title="Bloom Cafe")


@app.get("/api/menu")
def get_menu():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, category, price_cents, description, seasonal "
            "FROM menu_items ORDER BY category, name"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
