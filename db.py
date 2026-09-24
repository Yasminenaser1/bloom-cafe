import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bloom.db"

# (name, category, price_cents, description, seasonal)
MENU = [
    ("Espresso",            "Coffee",      300, "A rich double shot.", 0),
    ("Americano",           "Coffee",      375, "Espresso with hot water.", 0),
    ("Latte",               "Coffee",      495, "Espresso with steamed milk.", 0),
    ("Oat Milk Latte",      "Coffee",      545, "Our latte with creamy oat milk.", 0),
    ("Cappuccino",          "Coffee",      475, "Equal parts espresso, milk, and foam.", 0),
    ("Pumpkin Spice Latte", "Coffee",      595, "Fall favorite with pumpkin and warm spices.", 1),
    ("Cold Brew",           "Cold Drinks", 450, "Steeped 18 hours, smooth and bold.", 0),
    ("Iced Vanilla Latte",  "Cold Drinks", 525, "Vanilla, espresso, and milk over ice.", 0),
    ("Matcha Latte",        "Tea",         525, "Ceremonial matcha with steamed milk.", 0),
    ("Chai Latte",          "Tea",         495, "Spiced black tea with milk.", 0),
    ("Butter Croissant",    "Pastries",    375, "Flaky and baked fresh daily.", 0),
    ("Blueberry Muffin",    "Pastries",    350, "Loaded with wild blueberries.", 0),
    ("Chocolate Chip Cookie","Pastries",   295, "Warm, gooey, and big.", 0),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS menu_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                category    TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                description TEXT,
                seasonal    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO menu_items "
            "(name, category, price_cents, description, seasonal) VALUES (?, ?, ?, ?, ?)",
            MENU,
        )


if __name__ == "__main__":
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category, name, price_cents FROM menu_items ORDER BY category, name"
        ).fetchall()
    for r in rows:
        print(f"{r['category']:<12} {r['name']:<24} ${r['price_cents'] / 100:.2f}")
