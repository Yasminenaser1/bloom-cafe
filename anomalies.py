"""Detect unusual periods in Bloom Cafe sales.

Daily sales are counts, so we use a Poisson model: given what's normal for this
metric (adjusted for day of week), how likely is this number by pure chance?

We judge windows of 1-7 days, not just single days: a small dip that lasts
several days adds up to strong evidence even when no single day looks odd.
"""
import math
from datetime import timedelta

import pandas as pd

from insights import TZ, load_lines

BASELINE_DAYS = 28        # "normal" = median of the 28 days before a window starts
MIN_HISTORY = 14          # need at least this many earlier days to judge
WINDOWS = (1, 2, 3, 5, 7) # lengths of consecutive-day windows to check
ALPHA = 1e-5              # flag only if chance of happening normally is below 1 in 100,000
MIN_EXPECTED = 0.5        # floor so an item that normally sells 0 can still be judged


def poisson_pmf(i, lam):
    return math.exp(i * math.log(lam) - lam - math.lgamma(i + 1))


def chance_at_most(k, lam):
    """Chance of seeing k or fewer, if the true average is lam."""
    return sum(poisson_pmf(i, lam) for i in range(k + 1))


def chance_at_least(k, lam):
    """Chance of seeing k or more, if the true average is lam."""
    stop = k + int(10 * math.sqrt(lam)) + 60   # later terms are negligible
    return sum(poisson_pmf(i, lam) for i in range(k, stop))


def judge(actual, expected):
    lam = max(expected, MIN_EXPECTED)
    if actual > lam:
        return "spike", chance_at_least(actual, lam)
    if actual < lam:
        return "drop", chance_at_most(actual, lam)
    return None, 1.0


def daily_series(as_of=None):
    """Units sold per item per day, and orders per day, in Dallas time.
    Days with no sales of an item become 0 instead of disappearing."""
    df = load_lines()
    # "today" = the day after as_of, so as_of itself is the last day included
    if as_of is not None:
        t = pd.Timestamp(as_of)
        t = t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)
        today = t.normalize() + pd.Timedelta(days=1)
    else:
        today = pd.Timestamp.now(tz=TZ).normalize()
    df = df[df["created_at"] < today].assign(day=lambda d: d["created_at"].dt.date)

    all_days = pd.date_range(df["day"].min(), (today - pd.Timedelta(days=1)).date(), freq="D").date
    units = (df.pivot_table(index="day", columns="item", values="quantity",
                            aggfunc="sum", fill_value=0)
               .reindex(all_days, fill_value=0))
    orders = df.groupby("day")["order_id"].nunique().reindex(all_days, fill_value=0)
    return orders, units, today


def weekday_factors(orders, before):
    """How busy each weekday is vs. an average day (Saturday ~1.35, Monday ~0.9).
    Learned only from days BEFORE the window being checked, to avoid data leakage."""
    hist = orders[[d < before for d in orders.index]]
    weekdays = [d.weekday() for d in hist.index]
    return (hist.groupby(weekdays).mean() / hist.mean()).to_dict()


def scan(name, series, cutoff, factor):
    """Judge every 1-7 day window that falls inside the checked period."""
    days = list(series.index)
    values = [int(v) for v in series]
    baseline = series.shift(1).rolling(BASELINE_DAYS, min_periods=MIN_HISTORY).median()

    flags = []
    for end_i, end in enumerate(days):
        if end < cutoff:
            continue
        for k in WINDOWS:
            start_i = end_i - k + 1
            if start_i < 0 or days[start_i] < cutoff:
                continue
            base = baseline[days[start_i]]        # normal level from BEFORE the window
            if pd.isna(base):
                continue
            expected = sum(float(base) * factor[days[t].weekday()]
                           for t in range(start_i, end_i + 1))
            actual = sum(values[start_i:end_i + 1])
            direction, chance = judge(actual, expected)
            if direction and chance < ALPHA:
                flags.append({"metric": name, "direction": direction,
                              "start": days[start_i], "end": end,
                              "actual": actual, "expected": expected, "chance": chance})
    return flags


def overlaps(a, b):
    return a["start"] <= b["end"] and b["start"] <= a["end"]


def merge(flags):
    """Merge overlapping or back-to-back windows for the same metric into one event.
    The event is described by its strongest window (least likely by chance)."""
    events = []
    for f in sorted(flags, key=lambda f: (f["metric"], f["direction"], f["start"])):
        last = events[-1] if events else None
        if (last and last["metric"] == f["metric"] and last["direction"] == f["direction"]
                and f["start"] <= last["_reach"] + timedelta(days=1)):
            last["_reach"] = max(last["_reach"], f["end"])
            if f["chance"] < last["chance"]:
                last.update({k: f[k] for k in ("start", "end", "actual", "expected", "chance")})
        else:
            events.append(dict(f, _reach=f["end"]))

    for e in events:
        e.pop("_reach")
        e["days"] = (e["end"] - e["start"]).days + 1
        e["start"], e["end"] = str(e["start"]), str(e["end"])
        e["expected"] = int(round(e["expected"]))
    return sorted(events, key=lambda e: e["start"], reverse=True)


def detect(days=60, as_of=None):
    orders, units, today = daily_series(as_of)
    cutoff = (today - pd.Timedelta(days=days)).date()
    factor = weekday_factors(orders, before=cutoff)

    traffic = scan("All orders", orders, cutoff, factor)
    items = []
    for item in units.columns:
        items += scan(item, units[item], cutoff, factor)

    # Item windows that overlap a traffic event in the same direction are explained
    # by it (everything sold more because more people came in): one event, not many.
    items = [f for f in items
             if not any(f["direction"] == t["direction"] and overlaps(f, t) for t in traffic)]

    return merge(traffic + items)


if __name__ == "__main__":
    events = detect()
    print(f"{len(events)} unusual events in the last 60 days:\n")
    for e in events:
        span = e["start"] if e["days"] == 1 else f"{e['start']} -> {e['end']}"
        print(f"  {span:<26} {e['metric']:<22} {e['direction']:<5} "
              f"{e['actual']} sold vs ~{e['expected']} expected  (chance {e['chance']:.0e})")
