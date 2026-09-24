"""Safe tools for the Ask Bloom chat. Plain code, no AI: each tool computes real
numbers and writes the answer from a template. The router (next step) only picks
WHICH tool to run; it never writes numbers."""
import difflib
import re

import pandas as pd

from anomalies import detect
from db import get_conn
from insights import TZ, insights, load_lines

DEFAULT_DAYS = 30


# ---------- helpers ----------

def money(cents):
    return f"${cents / 100:,.2f}"


def hour_label(h):
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def period_label(days):
    return "yesterday" if days == 1 else f"the last {days} days"


def change_phrase(new, old):
    if not old:
        return "with nothing to compare against before that"
    p = (new - old) / old * 100
    if abs(p) < 3:
        return "about the same as the period before"
    return f"{'up' if p > 0 else 'down'} {abs(p):.0f}% from the period before"


def menu_items():
    with get_conn() as conn:
        return {r["name"]: dict(r) for r in conn.execute(
            "SELECT name, category, price_cents, seasonal FROM menu_items")}


def window(df, days, offset=0):
    """Rows from the `days` full days ending `offset` days before today (Dallas time)."""
    today = pd.Timestamp.now(tz=TZ).normalize()
    end = today - pd.Timedelta(days=offset)
    start = end - pd.Timedelta(days=days)
    return df[(df["created_at"] >= start) & (df["created_at"] < end)]


# ---------- pulling details out of the question ----------

def parse_days(text):
    t = text.lower()
    m = re.search(r"(\d+)\s*(day|week|month|year)s?", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    elif "yesterday" in t:
        days = 1
    elif re.search(r"\b(week|weekly)\b", t):
        days = 7
    elif "quarter" in t:
        days = 90
    elif "year" in t:
        days = 365
    elif "month" in t:
        days = 30
    else:
        days = DEFAULT_DAYS
    return max(1, min(days, 365))


def item_aliases(names):
    """Full names, plus any single word that belongs to only one item ('pumpkin', 'oat')."""
    aliases = {n.lower(): n for n in names}
    word_owners = {}
    for n in names:
        for w in n.lower().split():
            word_owners.setdefault(w, set()).add(n)
    for w, owners in word_owners.items():
        if len(owners) == 1 and len(w) > 3:
            aliases.setdefault(w, next(iter(owners)))
    aliases["psl"] = "Pumpkin Spice Latte"
    return aliases


def find_item(text, names):
    t = text.lower()
    aliases = item_aliases(names)
    # 1. exact alias match, longest first (so "oat milk latte" beats "latte")
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}s?\b", t):
            return aliases[alias]
    # 2. small typos: compare each word (and word pair) with the aliases
    words = re.findall(r"[a-z]+", t)
    chunks = words + [" ".join(p) for p in zip(words, words[1:])]
    for chunk in chunks:
        match = difflib.get_close_matches(chunk.rstrip("s"), list(aliases), n=1, cutoff=0.85)
        if match:
            return aliases[match[0]]
    return None


# ---------- the tools ----------

def sales_for_item(item, days):
    df = load_lines()
    cur = window(df, days)
    prev = window(df, days, offset=days)
    cur_i, prev_i = cur[cur["item"] == item], prev[prev["item"] == item]
    units, before = int(cur_i["quantity"].sum()), int(prev_i["quantity"].sum())
    revenue = int(cur_i["revenue_cents"].sum())
    answer = (f"{item}: {units:,} sold in {period_label(days)} ({money(revenue)}), "
              f"{change_phrase(units, before)}.")
    return {"tool": "sales_for_item", "answer": answer,
            "data": {"item": item, "days": days, "units": units,
                     "units_before": before, "revenue_cents": revenue}}


def top_items(days, least=False):
    cur = window(load_lines(), days)
    counts = (cur.groupby("item")["quantity"].sum()
                 .reindex(list(menu_items()), fill_value=0))     # include items that sold 0
    top = counts.sort_values(ascending=least).head(5)
    listing = "; ".join(f"{name} ({int(n):,})" for name, n in top.items())
    label = "Slowest sellers" if least else "Top sellers"
    answer = f"{label} in {period_label(days)}: {listing}."
    return {"tool": "top_items", "answer": answer,
            "data": {"days": days, "top": {k: int(v) for k, v in top.items()}}}


def busiest_times(days):
    cur = window(load_lines(), days)
    orders = cur.drop_duplicates("order_id")
    per_hour = orders["created_at"].dt.hour.value_counts() / days
    busiest_h = int(per_hour.idxmax())
    quiet_h = min(range(7, 17), key=lambda h: per_hour.get(h, 0) + per_hour.get(h + 1, 0))
    daily = orders.groupby(orders["created_at"].dt.date).size()
    by_weekday = daily.groupby(pd.to_datetime(daily.index).day_name()).mean()
    busiest_day = by_weekday.idxmax()
    answer = (f"In {period_label(days)}, the busiest hour was {hour_label(busiest_h)} "
              f"(about {per_hour[busiest_h]:.1f} orders a day), the quietest stretch was "
              f"{hour_label(quiet_h)}–{hour_label(quiet_h + 2)}, and the busiest day of the week "
              f"was {busiest_day} (about {by_weekday[busiest_day]:.0f} orders).")
    return {"tool": "busiest_times", "answer": answer,
            "data": {"days": days, "busiest_hour": busiest_h, "quiet_start": quiet_h,
                     "busiest_weekday": busiest_day}}


def compare_periods(days):
    days = max(days, 7)                     # shorter comparisons are mostly noise
    k = insights(days)["kpis"]
    rev, orders, ticket = k["revenue_cents"], k["orders"], k["avg_ticket_cents"]

    def pct(c):
        return "no earlier data" if c is None else f"{c:+.1f}%"

    answer = (f"In {period_label(days)} vs the {days} days before: revenue {money(rev['value'])} "
              f"({pct(rev['change_pct'])}), {orders['value']:,} orders ({pct(orders['change_pct'])}), "
              f"average ticket {money(ticket['value'])} ({pct(ticket['change_pct'])}).")
    return {"tool": "compare_periods", "answer": answer, "data": {"days": days, "kpis": k}}


def recent_alerts(days=30):
    seasonal = {n for n, r in menu_items().items() if r["seasonal"]}
    events = detect(max(days, 14))
    if not events:
        return {"tool": "recent_alerts", "answer": f"Nothing unusual in {period_label(days)}.",
                "data": {"events": []}}
    lines = []
    for e in events:
        span = e["start"] if e["days"] == 1 else f"{e['start']} to {e['end']}"
        what = "unusually low" if e["direction"] == "drop" else "unusually high"
        note = " (expected: seasonal launch)" if e["metric"] in seasonal and e["direction"] == "spike" else ""
        lines.append(f"{e['metric']} was {what} {span}: {e['actual']} vs about {e['expected']} expected{note}")
    answer = f"Unusual activity in {period_label(max(days, 14))}: " + "; ".join(lines) + "."
    return {"tool": "recent_alerts", "answer": answer, "data": {"events": events}}


if __name__ == "__main__":
    names = list(menu_items())
    print("--- item finding ---")
    for q in ["how are lattes doing", "oat milk latte sales", "any pumpkin sales?",
              "capuccino this week", "PSL numbers", "how's business"]:
        print(f"  {q!r:<28} -> {find_item(q, names)}")
    print("--- period finding ---")
    for q in ["yesterday", "last week", "this month", "past 3 months", "14 days", "overall"]:
        print(f"  {q!r:<28} -> {parse_days(q)} days")
    print("--- tools ---")
    for result in [sales_for_item("Latte", 7), top_items(30), busiest_times(30),
                   compare_periods(30), recent_alerts()]:
        print(f"\n[{result['tool']}] {result['answer']}")
