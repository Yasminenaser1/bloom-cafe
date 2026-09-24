import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import get_conn
from insights import insights
from anomalies import detect
from chat import answer as chat_answer
from simulate import simulate

STATIC = Path(__file__).parent / "static"

simulate()  # builds tables + menu, then (re)generates a year of demo orders

app = FastAPI(title="Bloom Cafe")


class OrderLine(BaseModel):
    menu_item_id: int
    quantity: int = Field(ge=1, le=20)


class OrderIn(BaseModel):
    items: list[OrderLine] = Field(min_length=1)


@app.get("/api/menu")
def get_menu():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, category, price_cents, description, seasonal "
            "FROM menu_items ORDER BY category, name"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/orders", status_code=201)
def create_order(order: OrderIn):
    ids = [line.menu_item_id for line in order.items]
    placeholders = ",".join("?" * len(ids))

    with get_conn() as conn:
        # Prices come from the database, never from the customer
        prices = {
            r["id"]: r["price_cents"]
            for r in conn.execute(
                f"SELECT id, price_cents FROM menu_items WHERE id IN ({placeholders})", ids
            )
        }
        missing = set(ids) - prices.keys()
        if missing:
            raise HTTPException(400, f"Unknown menu item ids: {sorted(missing)}")

        total = sum(prices[line.menu_item_id] * line.quantity for line in order.items)
        cur = conn.execute("INSERT INTO orders (total_cents) VALUES (?)", (total,))
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, menu_item_id, quantity, unit_price_cents) "
            "VALUES (?, ?, ?, ?)",
            [(order_id, line.menu_item_id, line.quantity, prices[line.menu_item_id])
             for line in order.items],
        )

    return {"order_id": order_id, "total_cents": total}


@app.get("/api/insights")
def get_insights(days: int = Query(30, ge=7, le=180)):
    return insights(days)


@app.api_route("/", methods=["GET", "HEAD"])
def home():
    return FileResponse(STATIC / "home.html")


@app.get("/menu")
def menu_page():
    return FileResponse(STATIC / "menu.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/api/anomalies")
def get_anomalies(days: int = Query(60, ge=14, le=180)):
    with get_conn() as conn:
        seasonal = {r["name"] for r in conn.execute(
            "SELECT name FROM menu_items WHERE seasonal = 1")}
    events = detect(days)
    for e in events:
        if e["metric"] in seasonal and e["direction"] == "spike":
            e["note"] = "Seasonal item launch: expected, not a problem."
    return events


def read_latest(folder):
    """Serve the newest published file, or say clearly that none exists yet."""
    path = Path(__file__).parent / folder / "latest.json"
    if not path.exists():
        return {"available": False}
    return {"available": True, **json.loads(path.read_text())}


@app.get("/api/report/latest")
def latest_report():
    return read_latest("reports")


@app.get("/api/ai-insights")
def latest_insights():
    return read_latest("insights")


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)


@app.post("/api/chat")
def chat(body: ChatIn):
    res = chat_answer(body.question)
    return {"answer": res["answer"], "tool": res["tool"], "suggestion": res.get("suggestion")}


@app.get("/chat")
def chat_page():
    return FileResponse(STATIC / "chat.html")
