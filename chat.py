"""Ask Bloom: route a question to a safe tool using a small embedding model.

No generative AI on the server. MiniLM (via fastembed/ONNX) only measures how
similar the question is to example questions; code computes every answer.
"""
import difflib
import re
import sys

import numpy as np

from chat_tools import (busiest_times, compare_periods, find_item, item_aliases, menu_items,
                        parse_days, recent_alerts, sales_for_item, top_items)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
THRESHOLD = 0.40   # below this, the question isn't close enough to anything we can answer
NEAR = 0.25        # between NEAR and THRESHOLD: close, so offer "Did you mean...?"
TYPO_CUTOFF = 0.85 # same fuzzy rule as chat_tools.find_item, so mask and find_item agree

EXAMPLES = {
    "sales_for_item": [
        "how many lattes did we sell", "sales of cold brew", "how is the croissant selling",
        "units sold of matcha last week", "how much chai did we sell",
        "is the cappuccino popular", "revenue from muffins", "how well is espresso doing",
    ],
    "top_items": [
        "what are our best sellers", "most popular items", "which products sell the most",
        "ranking of items by sales", "what do customers buy most often",
        "top 5 items this month", "favorite drinks of our customers",
        "which items do customers order most",
        "which items sell the least", "worst selling items",
    ],
    "busiest_times": [
        "what time of day is busiest", "peak hours", "when is the shop quiet",
        "which hours are slow", "what day of the week gets the most customers",
        "when do we get the most orders", "staffing for the rush",
    ],
    "compare_periods": [
        "how are sales compared to before", "is revenue growing", "total revenue this month",
        "how many orders did we get", "average ticket size", "sales this week versus last week",
        "is business up or down", "overall performance",
    ],
    "recent_alerts": [
        "any anomalies", "something unusual in the data", "were there any problems recently",
        "any drops or spikes", "did anything strange happen", "show me alerts",
    ],
    "unknown": [
        "what's the weather", "tell me a joke", "write a story",
        "how do I brew coffee at home", "recipe for a cappuccino",
        "what is the capital of France", "translate this sentence", "what's in the news today",
    ],
}

# Clear comparison wording is a strong signal: no model needed to spot "X vs Y".
COMPARE = re.compile(r"\b(vs\.?|versus|compar\w*|than)\b")
LEAST = re.compile(r"\b(least|worst|lowest|slowest|bottom)\b")

HELP = ("I can answer questions about Bloom Cafe's sales. Try: "
        "\"How did lattes do last week?\", \"What sold best this month?\", "
        "\"When are we busiest?\", \"How was this month vs last month?\", "
        "or \"Anything unusual lately?\"")

# Specific time phrases only ("last week", "3 days", "yesterday").
# Phrases like "day of the week" are left alone: there they ARE the meaning.
TIME = re.compile(
    r"\b(?:this|last|past|previous)\s+(?:\d+\s+)?(?:day|week|month|year|quarter)s?\b"
    r"|\b\d+\s*(?:day|week|month|year)s?\b"
    r"|\byesterday\b"
)
_aliases = None


def mask(text):
    """Hide WHAT the question is about (item, time period) so the router only
    judges WHAT KIND of question it is. Code extracts item and period separately."""
    global _aliases
    if _aliases is None:
        _aliases = sorted(item_aliases(list(menu_items())), key=len, reverse=True)
    t = TIME.sub(" ", text.lower())
    for alias in _aliases:
        t = re.sub(rf"\b{re.escape(alias)}s?\b", "item", t)

    def is_item(chunk):
        chunk = re.sub(r"[^a-z ]", "", chunk).rstrip("s")
        return bool(chunk) and bool(difflib.get_close_matches(chunk, _aliases, n=1, cutoff=TYPO_CUTOFF))

    words, out, i = t.split(), [], 0
    while i < len(words):
        pair = " ".join(words[i:i + 2]) if i + 1 < len(words) else ""
        if pair and "item" not in pair and is_item(pair):
            out.append("item"); i += 2
        elif words[i] != "item" and is_item(words[i]):
            out.append("item"); i += 1
        else:
            out.append(words[i]); i += 1
    t = re.sub(r"\bitem(?:\s+item)+\b", "item", " ".join(out))   # "oat latte" -> one item
    return re.sub(r"\s+", " ", t).strip()


_model = None
_index = None


def model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME, cache_dir="models", threads=1)
    return _model


def embed(texts):
    vecs = np.array(list(model().embed(texts)))
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)   # unit length -> dot product = cosine


def index():
    """Embed all example questions once, then reuse."""
    global _index
    if _index is None:
        labels = [tool for tool, qs in EXAMPLES.items() for _ in qs]
        texts = [q for qs in EXAMPLES.values() for q in qs]
        _index = (labels, embed([mask(t) for t in texts]))
    return _index


def route(question):
    labels, vecs = index()
    sims = vecs @ embed([mask(question)])[0]
    best = {}
    for label, s in zip(labels, sims):
        best[label] = max(best.get(label, -1.0), float(s))
    item = find_item(question, list(menu_items()))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    tool, score = ranked[0]
    # sales_for_item needs an item. If none was mentioned it isn't a valid choice:
    # use the next-best tool if that's a confident match, otherwise ask which item.
    if tool == "sales_for_item" and item is None:
        alt, alt_score = ranked[1]
        if alt != "unknown" and alt_score >= THRESHOLD:
            tool, score = alt, alt_score
    rule = None
    if item is None and COMPARE.search(question.lower()):
        tool, score, rule = "compare_periods", max(score, THRESHOLD), "comparison words"

    suggestion = None
    if score < THRESHOLD:
        if tool != "unknown" and score >= NEAR:
            suggestion = canonical_question(tool, item, parse_days(question))
        tool = "unknown"
    return {
        "suggestion": suggestion,
        "rule": rule,
        "tool": tool,
        "score": round(score, 2),
        "scores": {k: round(v, 2) for k, v in ranked},
        "item": item,
        "days": parse_days(question),
    }


def period_words(days):
    return {1: "yesterday", 7: "last week", 30: "this month"}.get(days, f"in the last {days} days")


def canonical_question(tool, item, days):
    """A clearly-worded version of the question for the "Did you mean...?" button."""
    when = period_words(days)
    if tool == "sales_for_item":
        return f"How did {item} sell {when}?" if item else None
    return {
        "top_items": f"What sold best {when}?",
        "busiest_times": "When are we busiest?",
        "compare_periods": f"How did sales {when} compare with before?",
        "recent_alerts": "Anything unusual lately?",
    }[tool]


def answer(question):
    r = route(question)
    tool, days = r["tool"], r["days"]
    if tool == "unknown" and r["suggestion"]:
        result = {"tool": "suggest", "answer": f'Did you mean: "{r["suggestion"]}"',
                  "suggestion": r["suggestion"]}
    elif tool == "unknown":
        result = {"tool": "unknown", "answer": HELP}
    elif tool == "sales_for_item" and not r["item"]:
        result = {"tool": "clarify", "answer": "Which item? For example: \"How did lattes do last week?\""}
    elif tool == "sales_for_item":
        result = sales_for_item(r["item"], days)
    elif tool == "top_items":
        result = top_items(days, least=bool(LEAST.search(question.lower())))
    elif tool == "busiest_times":
        result = busiest_times(days)
    elif tool == "compare_periods":
        result = compare_periods(days)
    else:
        result = recent_alerts(days)
    result["route"] = r
    return result


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "how did lattes do last week"
    res = answer(q)
    print(f"Q: {q}")
    print(f"-> {res['route']['tool']}  (similarity {res['route']['score']}, "
          f"item={res['route']['item']}, days={res['route']['days']})")
    print(f"A: {res['answer']}")
