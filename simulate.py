"""Generate a year of realistic Bloom Cafe order history.

Same seed -> same patterns every run. History always ends yesterday,
so the dashboard looks current whenever the app starts.
"""
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from db import get_conn, init_db

SEED = 42
DAYS = 365
TZ = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")

# Relative weight of orders in each opening hour (7am-6pm, Dallas time)
HOUR_WEIGHTS = {7: 8, 8: 12, 9: 10, 10: 7, 11: 6, 12: 7,
                13: 5, 14: 3, 15: 3, 16: 4, 17: 3}

# How often each item gets picked, before time/season adjustments
BASE_POPULARITY = {
    "Espresso": 3, "Americano": 5, "Latte": 10, "Oat Milk Latte": 7,
    "Cappuccino": 5, "Pumpkin Spice Latte": 0, "Cold Brew": 6,
    "Iced Vanilla Latte": 6, "Matcha Latte": 5, "Chai Latte": 4,
    "Butter Croissant": 6, "Blueberry Muffin": 4, "Chocolate Chip Cookie": 4,
}
PASTRIES = {"Butter Croissant", "Blueberry Muffin", "Chocolate Chip Cookie"}
ICED = {"Cold Brew", "Iced Vanilla Latte"}

# Known events planted in the data, so we can check whether the anomaly
# detector actually finds them. (days_ago is inclusive.)
# item + item_x: multiply that item's popularity (0.0 = unavailable)
# traffic_x:     multiply the number of orders that day
PLANTED_EVENTS = [                                                         # CHANGED
    {"name": "Cold brew machine broken", "days_ago": range(16, 21),
     "item": "Cold Brew", "item_x": 0.0},
    {"name": "Neighborhood festival", "days_ago": [45], "traffic_x": 2.0},
]


def events_for(days_ago, planted):                                         # CHANGED
    return [e for e in planted if days_ago in e["days_ago"]]


def item_weight(name, day, hour, events):
    w = BASE_POPULARITY[name]
    warm = day.month in (5, 6, 7, 8, 9)
    if name in ICED:
        w *= 1.8 if warm else 0.6
    if name == "Pumpkin Spice Latte":
        w = 12 if day.month in (9, 10, 11) else 0
    if name in PASTRIES and hour <= 10:
        w *= 1.6
    if name == "Chocolate Chip Cookie" and hour >= 14:
        w *= 2
    for e in events:                                                       # CHANGED
        if e.get("item") == name:
            w *= e["item_x"]
    return w


def orders_that_day(rng, day, i, events):
    base = 55 * (1 + 0.25 * i / DAYS)          # slow growth over the year
    weekday = {0: 0.9, 5: 1.35, 6: 1.2}.get(day.weekday(), 1.0)
    noise = rng.gauss(1, 0.08)
    boost = 1.0
    for e in events:
        boost *= e.get("traffic_x", 1.0)
    return max(0, round(base * weekday * noise * boost))


def simulate(seed=SEED, planted=PLANTED_EVENTS):                          # CHANGED
    rng = random.Random(seed)                                              # CHANGED
    init_db()

    with get_conn() as conn:
        menu = {r["name"]: (r["id"], r["price_cents"])
                for r in conn.execute("SELECT id, name, price_cents FROM menu_items")}

        # Replace previous simulated history; leave real web orders alone
        conn.execute("DELETE FROM order_items WHERE order_id IN "
                     "(SELECT id FROM orders WHERE source = 'sim')")
        conn.execute("DELETE FROM orders WHERE source = 'sim'")

        today = datetime.now(TZ).date()
        names = list(BASE_POPULARITY)
        hours = list(HOUR_WEIGHTS)
        total_orders = 0

        for i in range(DAYS):
            day = today - timedelta(days=DAYS - i)
            days_ago = (today - day).days
            events = events_for(days_ago, planted)                         # CHANGED

            for _ in range(orders_that_day(rng, day, i, events)):
                hour = rng.choices(hours, weights=list(HOUR_WEIGHTS.values()))[0]
                local = datetime.combine(
                    day, time(hour, rng.randrange(60), rng.randrange(60)), tzinfo=TZ)
                created_at = local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

                weights = [item_weight(n, day, hour, events) for n in names]
                n_items = rng.choices([1, 2, 3], weights=[55, 35, 10])[0]
                basket = {}
                for name in rng.choices(names, weights=weights, k=n_items):
                    basket[name] = basket.get(name, 0) + 1

                total = sum(menu[n][1] * q for n, q in basket.items())
                cur = conn.execute(
                    "INSERT INTO orders (created_at, source, total_cents) VALUES (?, 'sim', ?)",
                    (created_at, total))
                conn.executemany(
                    "INSERT INTO order_items (order_id, menu_item_id, quantity, unit_price_cents) "
                    "VALUES (?, ?, ?, ?)",
                    [(cur.lastrowid, menu[n][0], q, menu[n][1]) for n, q in basket.items()])
                total_orders += 1

    return total_orders


if __name__ == "__main__":
    n = simulate()
    with get_conn() as conn:
        span = conn.execute(
            "SELECT MIN(created_at), MAX(created_at), SUM(total_cents) FROM orders WHERE source='sim'"
        ).fetchone()
        top = conn.execute("""
            SELECT m.name, SUM(oi.quantity) AS sold
            FROM order_items oi JOIN menu_items m ON m.id = oi.menu_item_id
            GROUP BY m.name ORDER BY sold DESC LIMIT 5
        """).fetchall()
    print(f"Simulated {n:,} orders  ({span[0]} -> {span[1]} UTC)")
    print(f"Revenue: ${span[2] / 100:,.2f}")
    print("Top sellers:")
    for r in top:
        print(f"  {r['name']:<24} {r['sold']:,}")
