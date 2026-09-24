"""Routing accuracy for Ask Bloom.

  python evaluate_chat.py            # dev set: use this while tuning
  python evaluate_chat.py --final    # v1 test set (already used once: 67%)
  python evaluate_chat.py --final2   # v2 test set: run ONCE
"""
import json
import sys

from chat import NEAR, THRESHOLD, canonical_question, route

if "--final2" in sys.argv:
    path = "tests/chat_eval_test_v2.json"
elif "--final" in sys.argv:
    path = "tests/chat_eval_test.json"
else:
    path = "tests/chat_eval_dev.json"
cases = json.load(open(path))

correct, recovered, wrong = [], [], []
for c in cases:
    r = route(c["q"])
    suggested = next((t for t in r["scores"] if t != "unknown"), None) if r["suggestion"] else None
    if r["tool"] == c["tool"] and not (c["tool"] == "unknown" and r["suggestion"]):
        correct.append((c, r))
    elif r["suggestion"] and suggested == c["tool"]:
        recovered.append((c, r))
    else:
        wrong.append((c, r))

n = len(cases)
print(f"{path}  (threshold {THRESHOLD}, near {NEAR})")
print(f"Correct:              {len(correct)}/{n} ({len(correct) / n:.0%})")
print(f"Recovered in 1 click: {len(recovered)}/{n}")
print(f"Correct + recovered:  {len(correct) + len(recovered)}/{n} "
      f"({(len(correct) + len(recovered)) / n:.0%})\n")
for label, group in (("RECOVERED", recovered), ("WRONG", wrong)):
    for c, r in group:
        top3 = ", ".join(f"{k} {v}" for k, v in list(r["scores"].items())[:3])
        extra = f'  -> "Did you mean: {r["suggestion"]}"' if r["suggestion"] else ""
        print(f"  {label:<9} {c['q']!r}  expected {c['tool']}, got {r['tool']}{extra}")
        print(f"            [{top3}]")

# Every suggestion button must itself route correctly (it's what the button sends)
for tool in ["top_items", "busiest_times", "compare_periods", "recent_alerts"]:
    q = canonical_question(tool, None, 30)
    got = route(q)["tool"]
    assert got == tool, f"suggestion {q!r} routes to {got}, not {tool}"
q = canonical_question("sales_for_item", "Latte", 7)
assert route(q)["tool"] == "sales_for_item", f"suggestion {q!r} doesn't route to sales_for_item"
print("\nAll suggestion buttons route correctly.")
