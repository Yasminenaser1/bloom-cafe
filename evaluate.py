"""Test the anomaly detector on simulated data it has never seen.

Each run plants random events with a new seed, runs the detector, and scores:
- recall: how many planted events it caught
- false alarms: events it flagged that were never planted
"""
import random
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import db
from anomalies import detect
from simulate import BASE_POPULARITY, TZ, simulate

EVAL_SEEDS = range(100, 110)          # never used while building the detector (dev seed = 42)
WINDOW = 60                           # days the detector checks
IGNORE = {"Pumpkin Spice Latte"}      # seasonal launch: always flagged in Sept, handled separately

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


def run():
    today = datetime.now(TZ).date()
    by_type = defaultdict(lambda: [0, 0])     # type -> [caught, planted]
    false_alarms = 0

    with tempfile.TemporaryDirectory() as tmp:
        db.DB_PATH = Path(tmp) / "eval.db"    # never touch the real bloom.db

        for seed in EVAL_SEEDS:
            db.DB_PATH.unlink(missing_ok=True)
            planted = make_events(random.Random(seed))
            simulate(seed=seed, planted=planted)
            found = [f for f in detect(days=WINDOW) if f["metric"] not in IGNORE]

            matched, marks = set(), []
            for event in planted:
                tgt = target(event, today)
                hits = {i for i, f in enumerate(found) if matches(f, tgt)}
                matched |= hits
                by_type[event["type"]][1] += 1
                by_type[event["type"]][0] += bool(hits)
                marks.append(f"{'✓' if hits else '✗'} {event['type']} ({tgt[0]})")

            extra = [f for i, f in enumerate(found) if i not in matched]
            false_alarms += len(extra)
            print(f"seed {seed}:  " + "   ".join(marks) + f"   | false alarms: {len(extra)}")
            for f in extra:
                print(f"            false alarm: {f['start']} {f['metric']} {f['direction']}")

    caught = sum(c for c, _ in by_type.values())
    total = sum(t for _, t in by_type.values())
    print(f"\nRecall: {caught}/{total} planted events caught ({caught / total:.0%})")
    for kind in EVENT_TYPES:
        c, t = by_type[kind]
        if t:
            print(f"  {kind:<14} {c}/{t}")
    print(f"False alarms: {false_alarms} across {len(EVAL_SEEDS)} runs "
          f"({false_alarms / len(EVAL_SEEDS):.1f} per 60 days)")


if __name__ == "__main__":
    run()
