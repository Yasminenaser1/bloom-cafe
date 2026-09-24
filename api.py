from fastapi import FastAPI

from db import get_conn, init_db

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
