"""Ask Bloom: route a question to a safe tool using a small embedding model.

No generative AI on the server. MiniLM (via fastembed/ONNX) only measures how
similar the question is to example questions; code computes every answer.
"""
import re
import sys

import numpy as np

from chat_tools import (busiest_times, compare_periods, find_item, item_aliases, menu_items,
                        parse_days, recent_alerts, sales_for_item, top_items)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
THRESHOLD = 0.40   # below this, the question isn't close enough to anything we can answer

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
    if score < THRESHOLD:
        tool = "unknown"
    return {
        "tool": tool,
        "score": round(score, 2),
        "scores": {k: round(v, 2) for k, v in ranked},
        "item": item,
        "days": parse_days(question),
    }


def answer(question):
    r = route(question)
    tool, days = r["tool"], r["days"]
    if tool == "unknown":
        result = {"tool": "unknown", "answer": HELP}
    elif tool == "sales_for_item" and not r["item"]:
        result = {"tool": "clarify", "answer": "Which item? For example: \"How did lattes do last week?\""}
    elif tool == "sales_for_item":
        result = sales_for_item(r["item"], days)
    elif tool == "top_items":
        result = top_items(days)
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
