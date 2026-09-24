"""Daily job: write yesterday's morning report and save it to reports/.

- Idempotent: if the report for that day already exists, do nothing (use --force to redo).
- Atomic: files are written to a temp name, then renamed, so a crash never leaves
  a half-written report behind.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from report import compose, gather_facts, write_headline
from simulate import TZ, simulate

REPORTS = Path(__file__).parent / "reports"


def write_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)          # rename is all-or-nothing


def publish(force=False):
    day = (datetime.now(TZ).date() - timedelta(days=1)).isoformat()   # yesterday
    path = REPORTS / f"{day}.json"
    if path.exists() and not force:
        print(f"Report for {day} already published; nothing to do.")
        return path, False

    simulate()                 # DEMO ONLY: refresh simulated history so "yesterday" exists
    facts = gather_facts()
    headline, source, reason = write_headline(facts)

    record = {
        "date": day,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "source": source,
        "headline": headline,
        "text": compose(headline, facts),
        "facts": facts,
    }
    REPORTS.mkdir(exist_ok=True)
    write_atomic(path, record)
    write_atomic(REPORTS / "latest.json", record)

    print(f"Published report for {day}  [{source}]")
    if reason:
        print(f"  note: {reason}")
    return path, True


if __name__ == "__main__":
    publish(force="--force" in sys.argv)
