"""Routing accuracy for Ask Bloom.

  python evaluate_chat.py          # dev set: use this while tuning
  python evaluate_chat.py --final  # final test set: run ONCE, at the very end
"""
import json
import sys

from chat import THRESHOLD, route

path = "tests/chat_eval_test.json" if "--final" in sys.argv else "tests/chat_eval_dev.json"
cases = json.load(open(path))

correct, misses = 0, []
for c in cases:
    r = route(c["q"])
    if r["tool"] == c["tool"]:
        correct += 1
    else:
        misses.append((c, r))

print(f"{path}  (threshold {THRESHOLD})")
print(f"Routing accuracy: {correct}/{len(cases)} ({correct / len(cases):.0%})\n")
for c, r in misses:
    top3 = ", ".join(f"{k} {v}" for k, v in list(r["scores"].items())[:3])
    print(f"  MISS  {c['q']!r}\n        expected {c['tool']}, got {r['tool']}   [{top3}]")
