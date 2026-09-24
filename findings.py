"""Find useful situations in Bloom Cafe sales and compute their numbers (code only, no model).

Each finding is a dict:
  kind     - "lost_sales", "trend", "quiet_window" or "pastry_timing"
  score    - how much it matters, used to pick the top findings
  facts    - formatted values; a model may only refer to these as {placeholders}
  summary  - one plain sentence written by code with the real numbers

Usage:
  python findings.py                 # findings as of yesterday
  python findings.py --day 2026-09-01
"""
import math
import sys

import pandas as pd

from anomalies import chance_at_least, detect
from db import get_conn
from insights import TZ, load_lines
from report import hour_label, join_names, money

RECENT_DAYS = 28          # "recently" = the last 4 weeks
LOST_SALES_LOOKBACK = 30  # only report item drops that ended in the last 30 days
SWITCH_CHANCE = 0.05      # an item "picked up" sales only if the extra is unlikely by chance
TREND_CHANCE = 1e-3       # a trend must be this unlikely to be chance...
TREND_MIN_CHANGE = 15     # ...and at least this big (percent)
MORNING_END = 11          # "morning" = before 11am


# ---------- shared helpers ----------

def window_label(start_hour, hours=2):
    return f"{hour_label(start_hour)}–{hour_label(start_hour + hours)}"


def pct(x):
    return f"{x:.0f}%"


def menu_info():
    with get_conn() as conn:
        rows = conn.execute("SELECT name, price_cents, seasonal FROM menu_items").fetchall()
    prices = {r["name"]: r["price_cents"] for r in rows}
    seasonal = {r["name"] for r in rows if r["seasonal"]}
    return prices, seasonal


def two_sided_chance(a, b, share_a):
    """Chance of a split at least this uneven if units followed the order counts.
    a, b = units in each period; share_a = period A's share of all orders."""
    n = a + b
    if n == 0 or share_a in (0, 1):
        return 1.0
    z = (a - n * share_a) / math.sqrt(n * share_a * (1 - share_a))
    return math.erfc(abs(z) / math.sqrt(2))


# ---------- 1. lost sales from item drops ----------

def daily_revenue(df, day):
    """Total revenue per Dallas day, up to and including `day` (days with no sales = 0)."""
    rev = df.groupby(df["created_at"].dt.normalize())["revenue_cents"].sum()
    return rev.reindex(pd.date_range(rev.index.min(), day, freq="D"), fill_value=0)


def total_revenue_check(rev, start, end, skip=()):
    """Total revenue in [start, end] vs a weekday-adjusted baseline.

    The stretch is already over, so (unlike the live anomaly detector) the baseline
    uses the 28 closest normal days on BOTH sides of it, up to `rev`'s last day
    only. That way a level change nearby (e.g. a seasonal launch) doesn't bias it.
    `skip` = dates (YYYY-MM-DD) of unusual days to leave out, like a festival."""
    normal = rev[[str(d.date()) not in skip for d in rev.index]]
    factor = (normal.groupby(normal.index.weekday).mean() / normal.mean()).to_dict()

    outside = normal[(normal.index < start) | (normal.index > end)]
    distance = [min(abs((d - start).days), abs((d - end).days)) for d in outside.index]
    near = outside.iloc[sorted(range(len(outside)), key=distance.__getitem__)[:RECENT_DAYS]]
    # Take the weekday effect out BEFORE the median, so weekends aren't underestimated
    adjusted = pd.Series([v / factor[d.weekday()] for d, v in near.items()])
    base = adjusted.median()

    window = rev[(rev.index >= start) & (rev.index <= end)]
    expected = sum(base * factor[d.weekday()] for d in window.index)
    actual = int(window.sum())
    change_pct = (actual - expected) / expected * 100

    # How far a normal stretch of this length wanders (2 standard deviations). Estimated
    # from the median absolute deviation, so one wild day can't inflate it.
    ratios = adjusted / base
    daily_sd = 1.4826 * (ratios - ratios.median()).abs().median()
    noise_pct = 2 * daily_sd / math.sqrt(len(window)) * 100
    return actual, round(expected), float(change_pct), float(noise_pct)


def lost_sales(df, day, prices, seasonal):
    """Item drops from the anomaly detector: the item's missed sales, what customers bought
    instead, and whether the cafe's TOTAL revenue actually fell."""
    cutoff = str((day - pd.Timedelta(days=LOST_SALES_LOOKBACK - 1)).date())
    events = detect(90, as_of=day)
    drops = [e for e in events
             if e["direction"] == "drop" and e["metric"] != "All orders"
             and e["metric"] not in seasonal and e["end"] >= cutoff]
    traffic_days = {str(d.date()) for e in events if e["metric"] == "All orders"
                    for d in pd.date_range(e["start"], e["end"])}

    rev = daily_revenue(df, day)
    found = []
    for e in drops:
        start = pd.Timestamp(e["start"], tz=TZ)
        end = pd.Timestamp(e["end"], tz=TZ) + pd.Timedelta(days=1)
        total, total_expected, total_pct, noise_pct = total_revenue_check(
            rev, start, end - pd.Timedelta(days=1), skip=traffic_days)
        total_normal = bool(abs(total_pct) < noise_pct)
        during = df[(df["created_at"] >= start) & (df["created_at"] < end)]
        before = df[(df["created_at"] >= start - pd.Timedelta(days=RECENT_DAYS))
                    & (df["created_at"] < start)]

        lost_units = e["expected"] - e["actual"]
        lost_cents = lost_units * prices[e["metric"]]

        # What sold more than usual during the drop? "Usual" = each item's units per
        # order in the 4 weeks before, times the number of orders during the drop.
        orders_during = during["order_id"].nunique()
        rate_before = before.groupby("item")["quantity"].sum() / before["order_id"].nunique()
        sold_during = during.groupby("item")["quantity"].sum()
        switched, other_extra_cents = [], 0
        for item, rate in rate_before.items():
            if item == e["metric"]:
                continue
            expected = rate * orders_during
            actual = int(sold_during.get(item, 0))
            other_extra_cents += (actual - expected) * prices[item]
            extra = actual - round(expected)
            if (item not in seasonal and extra > 0
                    and chance_at_least(actual, expected) < SWITCH_CHANCE):
                switched.append({"item": item, "extra": extra})
        switched.sort(key=lambda s: s["extra"], reverse=True)
        other_extra_cents = round(other_extra_cents)
        total_change_cents = total - total_expected

        dates = e["start"] if e["days"] == 1 else f"{e['start']} to {e['end']}"
        days = f"{e['days']} day" + ("s" if e["days"] > 1 else "")
        if switched:
            switched_to = join_names([f"{s['item']} (+{s['extra']})" for s in switched[:2]])
        elif other_extra_cents > 0:
            switched_to = "a little more of everything else"
        else:
            switched_to = "nothing else"
        if total_normal:
            total_vs_usual = (f"about normal ({pct(abs(total_pct))} "
                              f"{'above' if total_pct >= 0 else 'below'} the usual level)")
            total_sentence = (f"But total sales were about normal ({money(total)} vs about "
                              f"{money(total_expected)} expected): customers ordered other items "
                              f"instead ({switched_to}), so this was not money the cafe lost.")
        else:
            total_vs_usual = (f"{pct(abs(total_pct))} {'above' if total_pct > 0 else 'below'} "
                              f"the usual level")
            total_sentence = (f"Total sales were {total_vs_usual} ({money(total)} vs about "
                              f"{money(total_expected)} expected), a difference of about "
                              f"{money(abs(total_change_cents))}.")

        found.append({
            "kind": "lost_sales",
            # Rank by what the cafe actually lost overall, not by one item's dip
            "score": 0 if total_normal else max(-total_change_cents, 0),
            "facts": {
                "item": e["metric"],
                "dates": dates,
                "days": days,
                "units_sold": f"{e['actual']} sold",
                "units_expected": f"about {e['expected']} expected",
                "item_revenue_drop": f"{money(lost_cents)} less {e['metric']} revenue",
                "switched_to": switched_to,
                "other_items_extra_revenue": f"{money(max(other_extra_cents, 0))} more from other items",
                "total_revenue": f"{money(total)} in total sales",
                "total_revenue_expected": f"about {money(total_expected)} expected",
                "total_revenue_vs_usual": total_vs_usual,
            },
            # Raw values for code checks only; a model never sees these
            "data": {"total_normal": total_normal, "total_change_cents": total_change_cents,
                     "total_change_pct": round(total_pct, 1), "noise_pct": round(noise_pct, 1),
                     "item_revenue_drop_cents": lost_cents,
                     "other_items_extra_cents": other_extra_cents},
            "summary": (f"{e['metric']} sold {e['actual']} instead of about {e['expected']} "
                        f"over {days} ({dates}), about {money(lost_cents)} less {e['metric']} "
                        f"revenue. {total_sentence}"),
        })
    return found


# ---------- 2. items clearly growing or shrinking ----------

def trends(df, day, prices, seasonal):
    """Compare each item's units per 100 orders: last 4 weeks vs the 4 weeks before.
    Per-order rates, so a busier month doesn't make every item look like it's growing.
    Days inside an anomaly for that item are left out of both periods."""
    end = day + pd.Timedelta(days=1)
    mid = end - pd.Timedelta(days=RECENT_DAYS)
    start = mid - pd.Timedelta(days=RECENT_DAYS)
    events = detect(2 * RECENT_DAYS + 30, as_of=day)

    found = []
    for item in sorted(df["item"].unique()):
        if item in seasonal:
            continue
        skip = set()
        for e in events:
            if e["metric"] == item:
                skip |= {str(d.date()) for d in pd.date_range(e["start"], e["end"])}
        d = df[(df["created_at"] >= start) & (df["created_at"] < end)
               & ~df["created_at"].dt.strftime("%Y-%m-%d").isin(skip)]
        recent, prior = d[d["created_at"] >= mid], d[d["created_at"] < mid]

        a, b = int(recent.loc[recent["item"] == item, "quantity"].sum()), \
            int(prior.loc[prior["item"] == item, "quantity"].sum())
        orders_a, orders_b = recent["order_id"].nunique(), prior["order_id"].nunique()
        if not (a and b and orders_a and orders_b):
            continue
        rate_a, rate_b = a / orders_a * 100, b / orders_b * 100
        change = (rate_a - rate_b) / rate_b * 100
        chance = two_sided_chance(a, b, orders_a / (orders_a + orders_b))
        if chance >= TREND_CHANCE or abs(change) < TREND_MIN_CHANGE:
            continue

        # Monthly revenue difference if the rate change holds at recent traffic levels
        monthly_cents = round((rate_a - rate_b) / 100 * orders_a * prices[item])
        direction = "growing" if change > 0 else "shrinking"
        found.append({
            "kind": "trend",
            "score": abs(monthly_cents),
            "facts": {
                "item": item,
                "direction": direction,
                "change": f"{pct(abs(change))} {'up' if change > 0 else 'down'} per order",
                "rate_now": f"{rate_a:.1f} per 100 orders",
                "rate_before": f"{rate_b:.1f} per 100 orders",
                "monthly_revenue_change": f"about {money(abs(monthly_cents))} a month {'more' if change > 0 else 'less'}",
            },
            "data": {"growing": bool(change > 0), "change_pct": round(float(change), 1)},
            "summary": (f"{item} is {direction}: {rate_a:.1f} sold per 100 orders in the last "
                        f"4 weeks vs {rate_b:.1f} the 4 weeks before ({pct(abs(change))} "
                        f"{'up' if change > 0 else 'down'}), about {money(abs(monthly_cents))} "
                        f"a month {'more' if change > 0 else 'less'} in revenue."),
        })
    return found


# ---------- 3. quietest 2-hour window ----------

def quiet_window(df, day):
    recent = df[df["created_at"] >= day - pd.Timedelta(days=RECENT_DAYS - 1)]
    recent = recent[recent["created_at"] < day + pd.Timedelta(days=1)]
    orders = recent.drop_duplicates("order_id")
    n_days = orders["created_at"].dt.date.nunique()
    by_hour = orders["created_at"].dt.hour.value_counts()
    hours = range(by_hour.index.min(), by_hour.index.max())      # window start hours
    per_day = {h: (by_hour.get(h, 0) + by_hour.get(h + 1, 0)) / n_days for h in hours}

    quiet = min(per_day, key=per_day.get)
    busy = max(per_day, key=per_day.get)
    share = per_day[quiet] / (len(orders) / n_days) * 100
    return [{
        "kind": "quiet_window",
        "score": 0,
        "facts": {
            "quiet_window": window_label(quiet),
            "quiet_orders": f"{per_day[quiet]:.1f} orders a day",
            "quiet_share": f"{pct(share)} of the day's orders",
            "busy_window": window_label(busy),
            "busy_orders": f"{per_day[busy]:.1f} orders a day",
        },
        "data": {"quiet_start": int(quiet), "busy_start": int(busy)},
        "summary": (f"Over the last 4 weeks, {window_label(quiet)} was the quietest stretch: "
                    f"{per_day[quiet]:.1f} orders a day ({pct(share)} of the day's orders), "
                    f"vs {per_day[busy]:.1f} a day at {window_label(busy)}, the busiest."),
    }]


# ---------- 4. when pastries sell ----------

def pastry_timing(df, day):
    recent = df[(df["created_at"] >= day - pd.Timedelta(days=RECENT_DAYS - 1))
                & (df["created_at"] < day + pd.Timedelta(days=1))]
    pastries = recent[recent["category"] == "Pastries"]
    if pastries.empty:
        return []
    hour = pastries["created_at"].dt.hour
    morning_share = pastries.loc[hour < MORNING_END, "quantity"].sum() / pastries["quantity"].sum() * 100
    orders = recent.drop_duplicates("order_id")
    orders_morning_share = (orders["created_at"].dt.hour < MORNING_END).mean() * 100
    orders_late_share = (orders["created_at"].dt.hour >= 14).mean() * 100

    # The pastry that sells latest in the day (biggest share after 2pm)
    late_share = (pastries.assign(late=hour >= 14)
                          .groupby("item")
                          .apply(lambda g: g.loc[g["late"], "quantity"].sum() / g["quantity"].sum() * 100,
                                 include_groups=False))
    late_item = late_share.idxmax()
    return [{
        "kind": "pastry_timing",
        "score": 0,
        "facts": {
            "morning_cutoff": hour_label(MORNING_END),
            "pastry_morning_share": f"{pct(morning_share)} of pastries",
            "orders_morning_share": f"{pct(orders_morning_share)} of all orders",
            "afternoon_pastry": late_item,
            "afternoon_share": f"{pct(late_share[late_item])} of its sales",
        },
        # Percent values, for code to decide which advice fits
        "data": {"pastry_morning_pct": round(float(morning_share), 1),
                 "orders_morning_pct": round(float(orders_morning_share), 1),
                 "afternoon_item_pct": round(float(late_share[late_item]), 1),
                 "orders_afternoon_pct": round(float(orders_late_share), 1)},
        "summary": (f"{pct(morning_share)} of pastries sell before {hour_label(MORNING_END)}, "
                    f"when {pct(orders_morning_share)} of orders come in. {late_item} is the "
                    f"afternoon pastry: {pct(late_share[late_item])} of it sells after 2pm."),
    }]


# ---------- all together ----------

# Money findings first (biggest dollar amount wins), then the timing tips
KIND_ORDER = {"lost_sales": 0, "trend": 1, "quiet_window": 2, "pastry_timing": 3}


def find_all(day=None):
    """All findings as of `day` (default: yesterday), most important first."""
    day = (pd.Timestamp(day, tz=TZ) if day
           else pd.Timestamp.now(tz=TZ).normalize() - pd.Timedelta(days=1))
    df = load_lines()
    df = df[df["created_at"] < day + pd.Timedelta(days=1)]    # nothing after `day`
    prices, seasonal = menu_info()

    found = (lost_sales(df, day, prices, seasonal) + trends(df, day, prices, seasonal)
             + quiet_window(df, day) + pastry_timing(df, day))
    found.sort(key=lambda f: (KIND_ORDER[f["kind"]], -f["score"]))
    return found


if __name__ == "__main__":
    args = sys.argv[1:]
    day = args[args.index("--day") + 1] if "--day" in args else None
    for i, f in enumerate(find_all(day), 1):
        print(f"{i}. [{f['kind']}] {f['summary']}")
        for k, v in f["facts"].items():
            print(f"     {{{k}}} = {v}")
        print()
