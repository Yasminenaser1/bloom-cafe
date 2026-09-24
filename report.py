"""Write the Bloom Cafe morning report.

Design (after testing where the model failed):
- The model writes ONLY a 1-2 sentence headline: the "how did it go" summary.
- Code writes every fact underneath (revenue, orders, top sellers, alerts).
- The headline uses {placeholders} for anything factual; code checks it, retries
  with the reason, and falls back to a template if the model keeps failing.

Usage:
  python report.py        # write today's report
  python report.py 10     # reliability test: generate 10 headlines and count outcomes
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter

import pandas as pd

from anomalies import detect
from db import get_conn
from insights import TZ, load_lines

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = os.environ.get("BLOOM_MODEL", "llama3.2:3b")   # chosen in the step 2 benchmark
MAX_ATTEMPTS = 3
HEADLINE_KEYS = ["day", "revenue", "revenue_vs_usual", "alert_status"]
FALLBACK_HEADLINE = "{day} brought in {revenue}, {revenue_vs_usual}, and {alert_status}."

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "June", "July", "August",
          "September", "October", "November", "December"]   # "May" left out: it's also a normal word


# ---------- facts (code only) ----------

def money(cents):
    return f"${cents / 100:,.2f}"


def hour_label(h):
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def compare(new, old, weekday):
    if not old:
        return f"with no earlier {weekday}s to compare against"
    p = (new - old) / old * 100
    if abs(p) < 3:
        return f"about the same as a typical {weekday}"
    return f"{abs(p):.0f}% {'above' if p > 0 else 'below'} a typical {weekday}"


def join_names(names):
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def top_sellers_phrase(counts):
    """Describe the top 3 sellers, grouping ties so the wording can't mislead."""
    if counts.empty:
        return "nothing sold"
    groups = []                                   # [(count, [names])], highest first
    for name, n in counts.sort_values(ascending=False).items():
        n = int(n)
        if groups and groups[-1][0] == n:
            groups[-1][1].append(name)
        elif sum(len(g[1]) for g in groups) < 3:
            groups.append((n, [name]))
        else:
            break
    parts = []
    for n, names in groups:
        if len(names) == 1:
            parts.append(f"{names[0]} ({n} sold)")
        else:
            parts.append(f"{join_names(sorted(names))} ({n} each)")
    return "; then ".join(parts)


def gather_facts(day=None):
    df = load_lines()
    df["day"] = df["created_at"].dt.normalize()
    day = (pd.Timestamp(day, tz=TZ) if day
           else pd.Timestamp.now(tz=TZ).normalize() - pd.Timedelta(days=1))   # default: yesterday
    weekday = day.day_name()

    y = df[df["day"] == day]
    past_days = [day - pd.Timedelta(weeks=w) for w in range(1, 5)]    # same weekday, last 4 weeks
    past = df[df["day"].isin(past_days)]

    revenue = int(y["revenue_cents"].sum())
    orders = int(y["order_id"].nunique())
    usual_revenue = past["revenue_cents"].sum() / 4
    counts = y.groupby("item")["quantity"].sum()
    busiest = y.drop_duplicates("order_id")["created_at"].dt.hour.value_counts().idxmax()

    # Alerts from the last 7 days, skipping expected seasonal launches
    with get_conn() as conn:
        seasonal = {r["name"] for r in conn.execute(
            "SELECT name FROM menu_items WHERE seasonal = 1")}
    week_start = str((day - pd.Timedelta(days=6)).date())
    recent = [e for e in detect(60, as_of=day)
              if e["end"] >= week_start and e["start"] <= str(day.date())
              and not (e["metric"] in seasonal and e["direction"] == "spike")]
    if recent:
        alerts = "; ".join(
            f"{e['metric']} was unusually {'low' if e['direction'] == 'drop' else 'high'} "
            f"({e['actual']} vs about {e['expected']} expected, {e['start']} to {e['end']})"
            for e in recent)
        alert_status = "something needs your attention" if len(recent) == 1 \
            else "a few things need your attention"
    else:
        alerts = "none in the past week"
        alert_status = "nothing unusual came up this week"

    return {
        "day": weekday,
        "date": day.strftime("%A, %B %-d"),
        "revenue": money(revenue),
        "revenue_vs_usual": compare(revenue, usual_revenue, weekday),
        "orders": f"{orders:,} orders",
        "avg_ticket": money(round(revenue / orders)) if orders else "$0.00",
        "top_sellers": top_sellers_phrase(counts),
        "busiest_hour": hour_label(int(busiest)),
        "alerts": alerts,
        "alert_status": alert_status,
    }


# ---------- headline: model writes, code checks ----------

def build_prompt(facts, feedback=None):
    listing = "\n".join(f"{{{k}}} = {facts[k]}" for k in HEADLINE_KEYS)
    prompt = f"""You write the one-line headline of a morning report for the owner of Bloom Cafe, a small coffee shop.
Write ONE or TWO short, warm, plain-English sentences summing up how yesterday went.

RULES:
- Never type a number, a date, a weekday, or a menu item yourself. Use these placeholders exactly, in curly braces:
{listing}
(The values are shown only so you understand them; never copy a value.)
- Each placeholder is a complete phrase. Don't add a noun right after it.
- Always use {{revenue}} and {{revenue_vs_usual}}.
- Include one describing word for the day (e.g. strong, busy, steady, slow, quiet) that honestly matches {{revenue_vs_usual}}.
- No greeting, no sign-off, no quotes around your answer.

Example structure (pick your own describing word): {{day}} was a ___ day: {{revenue}}, {{revenue_vs_usual}}; {{alert_status}}.
"""
    if feedback:
        prompt += f"\nYour previous attempt was rejected: {feedback}\nTry again and follow the rules exactly.\n"
    return prompt


def ask_model(prompt):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.3}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as res:
        return json.loads(res.read())["response"].strip().strip('"')


def protected_terms():
    """Names that live inside placeholders; the model must never type them itself."""
    with get_conn() as conn:
        items = [r["name"] for r in conn.execute("SELECT name FROM menu_items")]
    return items + WEEKDAYS + MONTHS


def fill(text, facts):
    return re.sub(r"\{(\w+)\}", lambda m: facts[m.group(1)], text)


def check(text, facts):
    """Return a reason the headline is unacceptable, or None if it's fine."""
    used = set(re.findall(r"\{(\w+)\}", text))
    unknown = used - set(HEADLINE_KEYS)
    if unknown:
        return f"you used placeholders that aren't allowed: {sorted(unknown)}"
    if not {"revenue", "revenue_vs_usual"} <= used:
        return "you must use both {revenue} and {revenue_vs_usual}"

    outside = re.sub(r"\{\w+\}", " ", text)
    if re.search(r"\d", outside):
        return "you typed a number yourself"
    copied = [t for t in protected_terms() if re.search(rf"\b{re.escape(t)}\b", outside)]
    if copied:
        return f"you typed {copied} yourself; use the placeholders"

    filled = fill(text, facts)
    doubled = re.search(r"\b(\w+) \1\b", filled, re.IGNORECASE)
    if doubled:
        return f'the word "{doubled.group(1)}" appears twice in a row after filling'
    tone = outside.lower()
    if re.search(r"\b(record\w*|best|worst|highest|lowest|ever)\b", tone):
        return "don't claim records or superlatives; you weren't given that information"
    if "below" in facts["revenue_vs_usual"] and re.search(r"\b(strong|great|busy|record|excellent)\b", tone):
        return "revenue was below usual, so an upbeat word like that is misleading"
    if "above" in facts["revenue_vs_usual"] and re.search(r"\b(slow|quiet|weak|disappointing)\b", tone):
        return "revenue was above usual, so a gloomy word like that is misleading"
    if len(re.findall(r"[.!?](?:\s|$)", filled)) > 2:
        return "too long; write at most two sentences"
    return None


def tidy(text):
    """Capitalize sentence starts, remove doubled punctuation, end with a period."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"([.!?])\.+", r"\1", text)
    return text if text.endswith((".", "!", "?")) else text + "."


def write_headline(facts):
    feedback = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            draft = ask_model(build_prompt(facts, feedback))
        except (urllib.error.URLError, TimeoutError) as err:
            return tidy(fill(FALLBACK_HEADLINE, facts)), "fallback: model unavailable", str(err)
        feedback = check(draft, facts)
        if feedback is None:
            return tidy(fill(draft, facts)), f"model: attempt {attempt}", None
    return tidy(fill(FALLBACK_HEADLINE, facts)), "fallback: failed checks", feedback


# ---------- the full report ----------

def compose(headline, facts):
    return "\n".join([
        f"Bloom Cafe · {facts['date']}",
        "",
        headline,
        "",
        f"• Revenue: {facts['revenue']} from {facts['orders']} (average ticket {facts['avg_ticket']})",
        f"• Top sellers: {facts['top_sellers']}",
        f"• Busiest hour: {facts['busiest_hour']}",
        f"• Alerts: {facts['alerts']}",
    ])


def write_report(day=None):
    facts = gather_facts(day)
    headline, source, _ = write_headline(facts)
    return compose(headline, facts), source


def reliability_test(n, day=None):
    facts = gather_facts(day)
    outcomes = Counter()
    for i in range(1, n + 1):
        headline, source, reason = write_headline(facts)
        outcomes[source] += 1
        print(f"{i:>2}. [{source}] {headline}" + (f"   (last rejection: {reason})" if reason else ""))
    print("\nOutcomes:")
    for source, count in outcomes.most_common():
        print(f"  {source:<26} {count}/{n}")


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
        report, source = write_report(day)
        print(f"[{source}]\n")
        print(report)
