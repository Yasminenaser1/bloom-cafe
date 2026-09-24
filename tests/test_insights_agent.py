"""Regression tests for insights_agent.py.

The bad drafts below are REAL outputs from llama3.2:3b and llama3.1:8b in the
10-draft test (see NOTES.md). They prove two things:
1. Design: the model's recommendation is never used; code picks it.
2. Checker: those same mistakes are rejected if the model puts them in a title.

Uses fixed findings, so no database or Ollama is needed.
Run: python -m unittest -v
"""
import unittest
import urllib.error
from unittest import mock

import insights_agent as ia
import report
from db import MENU

PROTECTED = [m[0] for m in MENU] + report.WEEKDAYS + report.MONTHS + ["am", "pm", "noon", "midnight"]

LOST_SALES = {
    "kind": "lost_sales",
    "facts": {
        "item": "Cold Brew", "dates": "2026-09-04 to 2026-09-08", "days": "5 days",
        "units_sold": "0 sold", "units_expected": "about 74 expected",
        "item_revenue_drop": "$333.00 less Cold Brew revenue",
        "switched_to": "a little more of everything else",
        "other_items_extra_revenue": "$413.18 more from other items",
        "total_revenue": "$2,702.35 in total sales",
        "total_revenue_expected": "about $2,584.85 expected",
        "total_revenue_vs_usual": "about normal (5% above the usual level)",
    },
    "data": {"total_normal": True, "total_change_cents": 11750},
    "summary": "(code summary)",
}
QUIET = {
    "kind": "quiet_window",
    "facts": {"quiet_window": "2pm–4pm", "quiet_orders": "6.4 orders a day",
              "quiet_share": "9% of the day's orders", "busy_window": "8am–10am",
              "busy_orders": "23.6 orders a day"},
    "data": {"quiet_start": 14, "busy_start": 8},
    "summary": "(code summary)",
}
PASTRY = {
    "kind": "pastry_timing",
    "facts": {"morning_cutoff": "11am", "pastry_morning_share": "59% of pastries",
              "orders_morning_share": "55% of all orders",
              "afternoon_pastry": "Chocolate Chip Cookie", "afternoon_share": "28% of its sales"},
    "data": {"pastry_morning_pct": 59.0, "orders_morning_pct": 54.6,
             "afternoon_item_pct": 27.5, "orders_afternoon_pct": 19.2},
    "summary": "(code summary)",
}
TREND = {
    "kind": "trend",
    "facts": {"item": "Latte", "direction": "shrinking", "change": "18% down per order",
              "rate_now": "16.1 per 100 orders", "rate_before": "19.7 per 100 orders",
              "monthly_revenue_change": "about $300.00 a month less"},
    "data": {"growing": False, "change_pct": -18.0},
    "summary": "(code summary)",
}

# Real bad RECOMMENDATIONS from the 10-draft test (the old design used these as-is)
BAD_RECOMMENDATIONS = [
    (PASTRY, "Reduce pastry offerings during morning hours to 11am to focus on Chocolate Chip Cookie."),
    (PASTRY, "Consider offering discounts or promotions on Chocolate Chip Cookie during 11am "
             "to capitalize on its relatively lower sales."),
    (PASTRY, "Consider adjusting pastry offerings or promotions to match peak periods, noting that "
             "59% of pastries of pastries sell during the morning."),
    (QUIET, "Reduce staffing during 2pm–4pm to 9% of the day's orders of the day's orders."),
    (LOST_SALES, "Consider increasing $413.18 to offset the drop in Cold Brew sales."),
    (LOST_SALES, "You lost $333.00 less Cold Brew revenue on Cold Brew; fix the machine."),
]

# The same mistakes, written the way a model would put them in a TITLE (with placeholders)
BAD_TITLES = [
    # contradicts the data / gives advice
    (PASTRY, "Reduce pastry offerings during morning hours", "you must use"),  # verbatim draft
    (PASTRY, "Reduce pastry offerings before {morning_cutoff}", "advice"),
    # invented claim
    (PASTRY, "{afternoon_pastry} has relatively lower sales", "sells"),
    (PASTRY, "Promotions for {afternoon_pastry} and its lower sales", "sells"),
    # "lost money" when total sales were normal
    (LOST_SALES, "{item} outage cost you", "lost money"),
    (LOST_SALES, "{item} losses over {days}", "lost money"),
    (LOST_SALES, "You lost {item_revenue_drop} on {item}", "lost money"),
    # doubled nouns after a placeholder
    (PASTRY, "{pastry_morning_share} of pastries sell early", "complete phrase"),
    (PASTRY, "{afternoon_pastry} gets {afternoon_share} of sales late", "complete phrase"),
    (QUIET, "Only {quiet_share} of the day's orders at {quiet_window}", "complete phrase"),
    # item was unavailable, not unpopular / not steady (seen after the redesign)
    (LOST_SALES, "{item} was not as popular as expected", "unavailable"),
    (LOST_SALES, "{item} sales stayed steady despite its absence", "unavailable"),
    # superlative it wasn't given: Cold Brew is not normally the top seller (real 3B title)
    (LOST_SALES, "{item} was not the top seller for {days}", "superlatives"),
    # typing facts instead of using placeholders
    (LOST_SALES, "{item}: no Cold Brew for {days}", "placeholders"),
    (QUIET, "Quiet from 2pm, {quiet_window}", "number"),
    # wrong direction for a trend
    (TREND, "{item} is growing fast", "opposite"),
]

GOOD_TITLES = [
    (LOST_SALES, "{item} was missing for {days}"),
    (QUIET, "Things slow down at {quiet_window}"),
    (QUIET, "Fewest orders come in during {quiet_window}"),
    (PASTRY, "{pastry_morning_share} go before {morning_cutoff}"),
    (TREND, "{item} is {direction}"),
]


def fake_model(answer):
    return mock.patch.object(report, "ask_model", return_value=answer)


class DesignTests(unittest.TestCase):
    def test_model_recommendation_is_never_used(self):
        for finding, bad in BAD_RECOMMENDATIONS:
            answer = f"TITLE: {ia.EXAMPLE_TITLE[finding['kind']]}\nRECOMMENDATION: {bad}"
            with self.subTest(bad=bad[:50]), fake_model(answer):
                out = ia.write_insight(finding, PROTECTED)
                expected = report.tidy(ia.fill(ia.recommend(finding), finding["facts"]))
                self.assertEqual(out["recommendation"], expected)
                self.assertNotIn(bad[:30], out["recommendation"])

    def test_bad_title_every_attempt_falls_back_to_template(self):
        with fake_model("TITLE: Cold Brew outage cost you $333"):
            out = ia.write_insight(LOST_SALES, PROTECTED)
        self.assertEqual(out["title_source"], "fallback: failed checks")
        self.assertEqual(out["title"], "Cold Brew barely sold for 5 days")

    def test_model_unavailable_falls_back_to_template(self):
        with mock.patch.object(report, "ask_model", side_effect=urllib.error.URLError("down")):
            out = ia.write_insight(QUIET, PROTECTED)
        self.assertEqual(out["title_source"], "fallback: model unavailable")
        self.assertEqual(out["title"], "2pm–4pm is your quiet stretch")


class CheckerTests(unittest.TestCase):
    def test_bad_titles_are_rejected(self):
        for finding, title, why in BAD_TITLES:
            with self.subTest(title=title):
                reason = ia.check(title, finding, PROTECTED)
                self.assertIsNotNone(reason, f"should have been rejected: {title}")
                self.assertIn(why, reason)

    def test_good_titles_pass(self):
        for finding, title in GOOD_TITLES:
            with self.subTest(title=title):
                self.assertIsNone(ia.check(title, finding, PROTECTED))

    def test_fallback_titles_pass_their_own_checks(self):
        for finding in (LOST_SALES, QUIET, PASTRY, TREND):
            with self.subTest(kind=finding["kind"]):
                self.assertIsNone(ia.check(ia.FALLBACK_TITLE[finding["kind"]], finding, PROTECTED))

    def test_most_pastries_claim_needs_the_data(self):
        few = dict(PASTRY, data=dict(PASTRY["data"], pastry_morning_pct=41.0))
        self.assertIsNone(ia.check("Pastries sell most before {morning_cutoff}", PASTRY, PROTECTED))
        self.assertIsNotNone(ia.check("Pastries sell most before {morning_cutoff}", few, PROTECTED))


class RecommendationTests(unittest.TestCase):
    def fill(self, finding):
        return ia.fill(ia.recommend(finding), finding["facts"])

    def test_normal_total_never_says_money_was_lost(self):
        text = self.fill(LOST_SALES).lower()
        self.assertIn("held up", text)
        self.assertNotRegex(text, ia.LOSS_WORDS)

    def test_real_drop_in_total_says_fix_it_first(self):
        down = dict(LOST_SALES, data={"total_normal": False, "total_change_cents": -40000})
        self.assertIn("fix it first", self.fill(down))

    def test_trend_direction_matches_data(self):
        self.assertIn("prep a little less", self.fill(TREND))
        up = dict(TREND, data={"growing": True})
        self.assertIn("doesn't run out", self.fill(up))

    def test_pastry_advice_follows_the_data(self):
        self.assertIn("before 11am", self.fill(PASTRY))
        few = dict(PASTRY, data=dict(PASTRY["data"], pastry_morning_pct=41.0))
        self.assertIn("Spread pastry baking", self.fill(few))
        # Afternoon tip only if that pastry really sells later than orders in general
        self.assertIn("Chocolate Chip Cookie", self.fill(PASTRY))
        early = dict(PASTRY, data=dict(PASTRY["data"], afternoon_item_pct=15.0))
        self.assertNotIn("Chocolate Chip Cookie", self.fill(early))


if __name__ == "__main__":
    unittest.main()
