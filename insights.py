"""Sales insights computed from the orders database (all times in Dallas time)."""
from collections import Counter
from itertools import combinations

import pandas as pd

from db import get_conn

TZ = "America/Chicago"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_lines():
    """One row per order line, with times converted from UTC to Dallas time."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT o.id AS order_id, o.created_at, m.name AS item, m.category,
                   oi.quantity, oi.unit_price_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN menu_items m ON m.id = oi.menu_item_id
            """,
            conn,
        )
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(TZ)
    df["revenue_cents"] = df["quantity"] * df["unit_price_cents"]
    return df


def summarize(lines):
    orders = int(lines["order_id"].nunique())
    revenue = int(lines["revenue_cents"].sum())
    return {
        "revenue_cents": revenue,
        "orders": orders,
        "avg_ticket_cents": round(revenue / orders) if orders else 0,
    }


def pct_change(new, old):
    return round((new - old) / old * 100, 1) if old else None


def insights(days=30):
    df = load_lines()
    today = pd.Timestamp.now(tz=TZ).normalize()          # midnight today, Dallas
    start = today - pd.Timedelta(days=days)
    prev_start = start - pd.Timedelta(days=days)

    # Only full days: today is still in progress, so it's left out
    cur = df[(df["created_at"] >= start) & (df["created_at"] < today)]
    prev = df[(df["created_at"] >= prev_start) & (df["created_at"] < start)]

    # Headline numbers, compared with the period before
    now_k, prev_k = summarize(cur), summarize(prev)
    kpis = {k: {"value": now_k[k], "change_pct": pct_change(now_k[k], prev_k[k])} for k in now_k}

    # Best sellers
    top = (cur.groupby("item")[["quantity", "revenue_cents"]].sum()
              .sort_values("quantity", ascending=False).head(5))
    top_items = [{"item": name, "sold": int(r.quantity), "revenue_cents": int(r.revenue_cents)}
                 for name, r in top.iterrows()]

    # Busiest hours (count orders, not order lines)
    orders = cur.drop_duplicates("order_id")
    hours = orders["created_at"].dt.hour.value_counts().sort_index()
    by_hour = [{"hour": int(h), "orders": int(n)} for h, n in hours.items()]

    # Average orders per weekday
    daily_counts = orders.groupby(orders["created_at"].dt.date).size()
    weekday_avg = daily_counts.groupby(pd.to_datetime(daily_counts.index).day_name()).mean()
    by_weekday = [{"day": d, "avg_orders": round(float(weekday_avg[d]), 1)}
                  for d in WEEKDAYS if d in weekday_avg]

    # Revenue per day, for a trend chart
    daily_rev = cur.groupby(cur["created_at"].dt.date)["revenue_cents"].sum()
    daily = [{"date": str(d), "revenue_cents": int(v), "orders": int(daily_counts.get(d, 0))}
             for d, v in daily_rev.items()]

    # Items most often bought together in the same order
    pairs = Counter()
    for _, items in cur.groupby("order_id")["item"]:
        for a, b in combinations(sorted(set(items)), 2):
            pairs[(a, b)] += 1
    bought_together = [{"items": [a, b], "orders": n} for (a, b), n in pairs.most_common(3)]

    return {
        "period": {"start": str(start.date()),
                   "end": str((today - pd.Timedelta(days=1)).date()),
                   "days": days},
        "kpis": kpis,
        "top_items": top_items,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "daily": daily,
        "bought_together": bought_together,
    }


if __name__ == "__main__":
    r = insights()
    p, k = r["period"], r["kpis"]
    print(f"Last {p['days']} days ({p['start']} -> {p['end']})")
    print(f"  Revenue     ${k['revenue_cents']['value'] / 100:,.2f}  ({k['revenue_cents']['change_pct']:+}%)")
    print(f"  Orders      {k['orders']['value']:,}  ({k['orders']['change_pct']:+}%)")
    print(f"  Avg ticket  ${k['avg_ticket_cents']['value'] / 100:.2f}  ({k['avg_ticket_cents']['change_pct']:+}%)")
    print("Top sellers:")
    for t in r["top_items"]:
        print(f"  {t['item']:<24} {t['sold']:,}")
    busiest = max(r["by_hour"], key=lambda h: h["orders"])
    print(f"Busiest hour: {busiest['hour']}:00")
    print("Bought together:")
    for b in r["bought_together"]:
        print(f"  {b['items'][0]} + {b['items'][1]}  ({b['orders']} orders)")
