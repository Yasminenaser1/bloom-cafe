"""Test the anomaly detector on simulated data it has never seen.

Each run plants random events with a new seed, runs the detector, and scores:
- recall: how many planted events it caught
- false alarms: events it flagged that were never planted

Usage:
  python evaluate.py          # detailed results at the current threshold
  python evaluate.py sweep    # compare several thresholds (the recall/false-alarm trade-off)
"""
import random
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import anomalies
import db
from simulate import BASE_POPULARITY, TZ, simulate

EVAL_SEEDS = range(200, 220)          # FINAL held-out test set: run once
WINDOW = 60                           # days the detector checks
IGNORE = {"Pumpkin Spice Latte"}      # seasonal launch: always flagged in Sept, handled separately
SWEEP = (1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8)

EVENT_TYPES = {
    "item_outage":   {"length": (2, 6), "item_x": 0.0},
    "item_dip":      {"length": (3, 5), "item_x": 0.4},
    "item_spike":    {"length": (1, 3), "item_x": 2.5},
    "traffic_spike": {"length": (1, 1), "traffic_x": 2.0},
    "traffic_drop":  {"length": (1, 2), "traffic_x": 0.45},
}


def make_events(rng, n=3):
    """Pick n different event types and place them on non-overlapping days."""
    items = [i for i in BASE_POPULARITY if i not in IGNORE]
    events, busy = [], set()
    for kind in rng.sample(list(EVENT_TYPES), n):
        spec = EVENT_TYPES[kind]
        length = rng.randint(*spec["length"])
        while True:
            start = rng.randint(3, WINDOW - 5 - length)
            if not set(range(start, start + length)) & busy:
                break
        busy |= set(range(start - 5, start + length + 5))   # keep events apart
        event = {"type": kind, "days_ago": range(start, start + length)}
        if "item_x" in spec:
            event.update(item=rng.choice(items), item_x=spec["item_x"])
        else:
            event["traffic_x"] = spec["traffic_x"]
        events.append(event)
    return events


def target(event, today):
    """What a correct detection of this planted event looks like."""
    metric = event.get("item", "All orders")
    factor = event.get("item_x", event.get("traffic_x"))
    direction = "drop" if factor < 1 else "spike"
    start = str(today - timedelta(days=max(event["days_ago"])))
    end = str(today - timedelta(days=min(event["days_ago"])))
    return metric, direction, start, end


def matches(found, tgt):
    metric, direction, start, end = tgt
    return (found["metric"] == metric and found["direction"] == direction
            and found["start"] <= end and found["end"] >= start)


def score(planted, found, today):
    """Returns (list of (type, caught?, metric), list of false-alarm events)."""
    matched, results = set(), []
    for event in planted:
        tgt = target(event, today)
        hits = {i for i, f in enumerate(found) if matches(f, tgt)}
        matched |= hits
        results.append((event["type"], bool(hits), tgt[0]))
    extra = [f for i, f in enumerate(found) if i not in matched]
    return results, extra


def run(alphas, verbose):
    today = datetime.now(TZ).date()
    totals = {a: {"by_type": defaultdict(lambda: [0, 0]), "false": 0} for a in alphas}

    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "eval.db"    # never touch the real bloom.db

        for seed in EVAL_SEEDS:
            db.DB_PATH.unlink(missing_ok=True)
            planted = make_events(random.Random(seed))
            simulate(seed=seed, planted=planted)

            for a in alphas:
                anomalies.ALPHA = a
                found = [f for f in anomalies.detect(days=WINDOW) if f["metric"] not in IGNORE]
                results, extra = score(planted, found, today)
                for kind, caught, _ in results:
                    totals[a]["by_type"][kind][0] += caught
                    totals[a]["by_type"][kind][1] += 1
                totals[a]["false"] += len(extra)

                if verbose:
                    marks = [f"{'✓' if c else '✗'} {k} ({m})" for k, c, m in results]
                    print(f"seed {seed}:  " + "   ".join(marks) + f"   | false alarms: {len(extra)}")
                    for f in extra:
                        print(f"            false alarm: {f['start']} {f['metric']} {f['direction']}")
            if not verbose:
                print(f"  seed {seed} done")
    return totals


def summary(totals):
    n = len(EVAL_SEEDS)
    kinds = list(EVENT_TYPES)
    short = {"item_outage": "outage", "item_dip": "dip", "item_spike": "spike",
             "traffic_spike": "t_spike", "traffic_drop": "t_drop"}
    print("\n threshold   recall   " + "  ".join(f"{short[k]:>7}" for k in kinds)
          + "   false alarms / 60 days")
    for a, t in totals.items():
        caught = sum(c for c, _ in t["by_type"].values())
        total = sum(tt for _, tt in t["by_type"].values())
        cols = "  ".join(f"{t['by_type'][k][0]:>3}/{t['by_type'][k][1]:<3}" for k in kinds)
        print(f"  {a:>8.0e}   {caught / total:>5.0%}    {cols}   {t['false'] / n:>6.1f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        summary(run(SWEEP, verbose=False))
    else:
        summary(run([anomalies.ALPHA], verbose=True))
