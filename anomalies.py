"""Detect unusual days in Bloom Cafe sales.

Daily sales are counts, so we use a Poisson model: given what's normal for this
metric lately (adjusted for day of week), how likely is today's number by pure
chance? Very unlikely -> flag it.
"""
import math

import pandas as pd

from insights import TZ, load_lines

BASELINE_DAYS = 28    # "normal" = median of the 28 days before
MIN_HISTORY = 14      # need at least this many earlier days before judging a day
ALPHA = 1e-4          # flag only if chance of happening normally is below 1 in 10,000
MIN_EXPECTED = 0.5    # floor so an item that normally sells 0 can still be judged


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


def daily_series():
    """Units sold per item per day, and orders per day, in Dallas time.
    Days with no sales of an item become 0 instead of disappearing."""
    df = load_lines()
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


def group_events(flags):
    """Merge flagged days that are back-to-back for the same metric into one event."""
    events = []
    for f in sorted(flags, key=lambda f: (f["metric"], f["direction"], f["date"])):
        last = events[-1] if events else None
        if (last and last["metric"] == f["metric"] and last["direction"] == f["direction"]
                and (f["date"] - last["_end"]).days == 1):
            last["_end"] = f["date"]
            last["days"] += 1
            last["actual"] += f["actual"]
            last["expected"] += f["expected"]
            last["chance"] = min(last["chance"], f["chance"])
        else:
            events.append({"metric": f["metric"], "direction": f["direction"],
                           "_start": f["date"], "_end": f["date"], "days": 1,
                           "actual": f["actual"], "expected": f["expected"],
                           "chance": f["chance"]})
    for e in events:
        e["start"] = str(e.pop("_start"))
        e["end"] = str(e.pop("_end"))
    return sorted(events, key=lambda e: e["start"], reverse=True)


def detect(days=60):
    orders, units, today = daily_series()
    cutoff = (today - pd.Timedelta(days=days)).date()
    factor = weekday_factors(orders, before=cutoff)                     # NEW

    metrics = [("All orders", orders)] + [(item, units[item]) for item in units.columns]
    flags = []
    for name, s in metrics:
        baseline = s.shift(1).rolling(BASELINE_DAYS, min_periods=MIN_HISTORY).median()
        for day in s.index:
            if day < cutoff or pd.isna(baseline[day]):
                continue
            expected = float(baseline[day]) * factor[day.weekday()]    # NEW
            direction, chance = judge(int(s[day]), expected)
            if direction and chance < ALPHA:
                flags.append({
                    "metric": name, "date": day, "direction": direction,
                    "actual": int(s[day]), "expected": int(round(expected)),
                    "chance": chance,
                })

    # If total traffic spiked/dropped that day, item flags in the same direction are
    # explained by it: one event, not thirteen.
    traffic = {(f["date"], f["direction"]) for f in flags if f["metric"] == "All orders"}
    flags = [f for f in flags
             if f["metric"] == "All orders" or (f["date"], f["direction"]) not in traffic]

    return group_events(flags)


if __name__ == "__main__":
    events = detect()
    print(f"{len(events)} unusual events in the last 60 days:\n")
    for e in events:
        span = e["start"] if e["days"] == 1 else f"{e['start']} -> {e['end']}"
        print(f"  {span:<26} {e['metric']:<22} {e['direction']:<5} "
              f"{e['actual']} sold vs ~{e['expected']} expected  (chance {e['chance']:.0e})")
