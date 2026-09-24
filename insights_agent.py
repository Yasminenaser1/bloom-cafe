"""Write AI Insights: a title + a practical recommendation for the top findings.

Design (after testing where the model failed; see NOTES.md):
- findings.py (code) finds the situations and computes every number.
- CODE picks each recommendation from a short list of vetted actions, based on the data.
- The model writes ONLY the short title, using {placeholders} for every fact.
  Code checks it, retries with the reason, and falls back to a template title.

Usage:
  python insights_agent.py        # write insights/latest.json
  python insights_agent.py 10     # reliability test: 10 titles per finding, count outcomes
"""
import json
import re
import sys
import urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import report
from findings import find_all
from insights import TZ

TOP_N = 3
OUT = Path(__file__).parent / "insights" / "latest.json"
MAX_TITLE_WORDS = 8


# ---------- recommendations: vetted actions, picked by code ----------

def recommend(finding):
    """Pick the vetted action whose condition matches the data. Returns a template."""
    kind, data = finding["kind"], finding["data"]
    if kind == "lost_sales":
        if data["total_normal"] or data["total_change_cents"] > 0:
            return ("Find out what stopped {item} from selling ({dates}) and set up a backup "
                    "so regulars can still get it next time. Total sales held up: "
                    "{total_revenue_vs_usual}.")
        return ("Find out what stopped {item} from selling ({dates}) and fix it first: total "
                "sales were {total_revenue_vs_usual} ({total_revenue}, {total_revenue_expected}).")
    if kind == "trend":
        if data["growing"]:
            return ("Plan prep and stock so {item} doesn't run out: it's now at {rate_now}, "
                    "up from {rate_before}.")
        return ("Check whether anything changed with {item} (recipe, price or menu spot) and "
                "prep a little less for now: it's at {rate_now}, down from {rate_before}.")
    if kind == "quiet_window":
        return ("Schedule prep, cleaning and breaks during {quiet_window} ({quiet_orders}), and "
                "have the most staff on at {busy_window} ({busy_orders}).")
    if kind == "pastry_timing":
        if data["pastry_morning_pct"] >= 50:
            rec = "Have most pastries baked and out before {morning_cutoff}: {pastry_morning_share} sell by then."
        else:
            rec = ("Spread pastry baking through the day: only {pastry_morning_share} sell "
                   "before {morning_cutoff}.")
        # Only if that pastry really sells later than orders in general do
        if data["afternoon_item_pct"] > data["orders_afternoon_pct"]:
            rec += " Keep {afternoon_pastry} stocked after lunch: {afternoon_share} come after 2pm."
        return rec
    raise ValueError(f"no vetted action for {kind}")


# ---------- titles: model writes, code checks ----------

# What each kind of finding is about, and which placeholders the title must use
# ("a|b" = either one is enough)
GUIDE = {
    "lost_sales": ("One menu item sold far less than usual for a few days (likely unavailable).",
                   ["item"]),
    "trend": ("A menu item's share of orders has clearly changed over the last 4 weeks.",
              ["item"]),
    "quiet_window": ("The quietest and busiest 2-hour stretches of the day over the last 4 weeks.",
                     ["quiet_window"]),
    "pastry_timing": ("When pastries sell during the day, compared with when orders come in.",
                      ["pastry_morning_share|morning_cutoff|afternoon_pastry"]),
}
NORMAL_TOTAL_ABOUT = (
    "One menu item was unavailable or barely sold for a few days. Customers ordered other "
    "items instead, so the cafe's total sales stayed about normal: this was NOT a money loss.")

FALLBACK_TITLE = {
    "lost_sales": "{item} barely sold for {days}",
    "trend": "{item} is {direction}",
    "quiet_window": "{quiet_window} is your quiet stretch",
    "pastry_timing": "{pastry_morning_share} sell before {morning_cutoff}",
}
EXAMPLE_TITLE = {
    "lost_sales": "{item} was missing for {days}",
    "trend": "{item} is {direction}",
    "quiet_window": "Things slow down at {quiet_window}",
    "pastry_timing": "{pastry_morning_share} go before {morning_cutoff}",
}

# Titles describe the finding; advice comes only from code
ADVICE_WORDS = (r"^\W*(consider|reduce|increase|offer|add|cut|promote|discount|adjust|try|run|"
                r"focus|optimi[sz]e|analy[sz]e|review|investigate|check|schedule|plan|stock|"
                r"bake|use|make|boost|drive|capitali[sz]e)\b|\b(should|must|need to)\b")
LOSS_WORDS = r"\b(lost|lose|loses|losing|loss|losses|cost|costs|costing|hurt|hurting|missed out)\b"
UP_WORDS = r"\b(grow\w*|ris\w*|increas\w*|up|gain\w*|climb\w*|more popular)\b"
DOWN_WORDS = r"\b(shrink\w*|declin\w*|fall\w*|fell|drop\w*|decreas\w*|down|less popular)\b"
# Claims about how well something sells that no finding supports on its own
UNSUPPORTED = r"\b((relatively )?(lower|low|weak|poor|slow) (sales|demand)|underperform\w*|unpopular)\b"
# An item that barely sold was most likely unavailable: nothing says customers liked it less,
# and its own sales weren't steady (total sales were, and that's in a placeholder)
ITEM_DROP_CLAIMS = r"\b(popular\w*|demand|liked|wanted|interest|steady|stable|normal|despite)\b"


def build_prompt(finding, feedback=None):
    about, required = GUIDE[finding["kind"]]
    if finding["kind"] == "lost_sales" and finding["data"]["total_normal"]:
        about = NORMAL_TOTAL_ABOUT
    facts = finding["facts"]
    listing = "\n".join(f"{{{k}}} = {v}" for k, v in facts.items())
    must = ", ".join(" or ".join(f"{{{k}}}" for k in r.split("|")) for r in required)

    prompt = f"""You write short, plain-English titles for insight cards on a small coffee shop's dashboard.
The situation: {about}
The facts, as placeholders:
{listing}

Write ONE title of at most {MAX_TITLE_WORDS} words that says what happened. It's a title, not advice: don't tell the owner what to do.

RULES:
- Never type a number, a time, a date, a weekday, a month, or a menu item yourself. Use the placeholders exactly, in curly braces. (The values are shown only so you understand them; never copy a value.)
- Each placeholder is a complete phrase. Never add a word like "of", a unit or a noun right after it.
- You must use: {must}.
- Only state what the facts say. No guesses about why, no superlatives, no words like lost, loss or cost.

Example (write your own): TITLE: {EXAMPLE_TITLE[finding["kind"]]}

Answer in exactly this format:
TITLE: ...
"""
    if feedback:
        prompt += f"\nYour previous attempt was rejected: {feedback}\nTry again and follow the rules exactly.\n"
    return prompt


def parse(text):
    """Pull the title out of the model's answer (anything else it wrote is ignored)."""
    m = re.search(r"^\s*TITLE:\s*(.+)$", text.replace("**", ""), re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip().strip('"') if m else None


def fill(text, facts):
    return re.sub(r"\{(\w+)\}", lambda m: facts[m.group(1)], text)


def check(title, finding, protected):
    """Return a reason the title is unacceptable, or None if it's fine."""
    if not title:
        return 'answer with one line: "TITLE: ..."'
    facts, kind = finding["facts"], finding["kind"]

    used = set(re.findall(r"\{(\w+)\}", title))
    unknown = used - facts.keys()
    if unknown:
        return f"you used placeholders that don't exist: {sorted(unknown)}"
    missing = [r for r in GUIDE[kind][1] if not set(r.split("|")) & used]
    if missing:
        return "you must use " + ", ".join(
            " or ".join("{" + k + "}" for k in r.split("|")) for r in missing)

    outside = re.sub(r"\{\w+\}", " ", title)
    if re.search(r"\d", outside):
        return "you typed a number yourself; use the placeholders"
    copied = [t for t in protected if re.search(rf"\b{re.escape(t)}\b", outside, re.IGNORECASE)]
    if copied:
        return f"you typed {copied} yourself; use the placeholders"

    # Placeholders are complete phrases: nothing like "of ..." or a repeated noun after one
    for m in re.finditer(r"\{(\w+)\}\s+([\w']+)", title):
        last_word = facts[m.group(1)].split()[-1].lower()
        if m.group(2).lower() in ("of", last_word):
            return (f'{{{m.group(1)}}} is already a complete phrase ("{facts[m.group(1)]}"); '
                    f'don\'t add "{m.group(2)}" after it')
    filled = fill(title, facts)
    doubled = re.search(r"\b([\w']+(?:\s+[\w']+){0,3})\s+\1\b", filled, re.IGNORECASE)
    if doubled:
        return f'"{doubled.group(1)}" appears twice in a row after filling'

    words = outside.lower()
    if re.search(ADVICE_WORDS, words):
        return "write a title that says what happened, not advice"
    if re.search(r"\b(record\w*|best|worst|highest|lowest|ever|most popular|top|leading|favou?rite)\b", words):
        return "don't claim records or superlatives; you weren't given that information"
    if re.search(UNSUPPORTED, words):
        return "don't claim how well something sells; the facts don't say that"
    if kind == "lost_sales" and finding["data"]["total_normal"] and re.search(LOSS_WORDS, words):
        return "total sales were about normal, so don't suggest the cafe lost money"
    if kind == "lost_sales" and re.search(ITEM_DROP_CLAIMS, words):
        return ("the item was most likely unavailable; don't claim anything about how popular "
                "or steady it was")
    if (kind == "pastry_timing" and finding["data"]["pastry_morning_pct"] < 50
            and re.search(r"\b(more|most|mostly|mainly)\b", words)):
        return f"only {facts['pastry_morning_share']} sell before {facts['morning_cutoff']}; don't say most do"
    if kind == "trend":
        wrong = DOWN_WORDS if finding["data"]["growing"] else UP_WORDS
        if re.search(wrong, words):
            return f"the item is {facts['direction']}, so don't describe it the opposite way"

    if len(title.split()) > MAX_TITLE_WORDS:
        return f"the title is too long; at most {MAX_TITLE_WORDS} words"
    if re.search(r"[.!?]\s+\S", title):
        return "the title must be one short phrase, not sentences"
    return None


def tidy_title(text):
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text[:1].upper() + text[1:]


def write_title(finding, protected, log=None):
    """Returns (title, source, last rejection reason).
    If `log` is a list, every rejected draft is appended to it as (draft, reason)."""
    facts = finding["facts"]
    feedback = None
    for attempt in range(1, report.MAX_ATTEMPTS + 1):
        try:
            draft = report.ask_model(build_prompt(finding, feedback))
        except (urllib.error.URLError, TimeoutError) as err:
            return tidy_title(fill(FALLBACK_TITLE[finding["kind"]], facts)), \
                "fallback: model unavailable", str(err)
        title = parse(draft)
        feedback = check(title, finding, protected)
        if feedback and log is not None:
            log.append((title or draft, feedback))
        if feedback is None:
            return tidy_title(fill(title, facts)), f"model: attempt {attempt}", None
    return tidy_title(fill(FALLBACK_TITLE[finding["kind"]], facts)), \
        "fallback: failed checks", feedback


def protected_terms():
    """Names that live inside placeholders; the model must never type them itself."""
    return report.protected_terms() + ["am", "pm", "noon", "midnight"]


# ---------- the full insights file ----------

def write_insight(finding, protected, log=None):
    title, source, _ = write_title(finding, protected, log)
    return {"kind": finding["kind"], "title": title,
            "recommendation": report.tidy(fill(recommend(finding), finding["facts"])),
            "summary": finding["summary"], "title_source": source}


def build(day=None):
    as_of = (pd.Timestamp(day) if day
             else pd.Timestamp.now(tz=TZ).normalize() - pd.Timedelta(days=1))
    protected = protected_terms()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(as_of.date()),
        "model": report.MODEL,
        "insights": [write_insight(f, protected) for f in find_all(day)[:TOP_N]],
    }


def reliability_test(n, day=None):
    protected = protected_terms()
    for f in find_all(day)[:TOP_N]:
        outcomes, rejected = Counter(), []
        print(f"\n=== {f['kind']} ===")
        print(f"  recommendation (code): {report.tidy(fill(recommend(f), f['facts']))}")
        for i in range(1, n + 1):
            title, source, _ = write_title(f, protected, rejected)
            outcomes[source] += 1
            print(f"  {i:>2}. [{source}] {title}")
        print("  outcomes:", dict(outcomes))
        reasons = Counter(reason for _, reason in rejected)
        for reason, count in reasons.most_common():
            example = next(d for d, r in rejected if r == reason)
            print(f"  rejected {count}x: {reason}\n      e.g. {example[:120]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    day = None
    if "--day" in args:
        i = args.index("--day")
        day = args[i + 1]
        del args[i:i + 2]
    if args:
        reliability_test(int(args[0]), day)
    else:
        result = build(day)
        OUT.parent.mkdir(exist_ok=True)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(text)
