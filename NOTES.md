# Bloom Cafe: design notes

## AI Insights: why the model writes only the title

**Rule for the whole project:** every number a user sees is computed by code. A model only
writes words, using `{placeholders}` that code fills in, and code checks every draft.

### First design: the model wrote a title and a recommendation

`findings.py` (code) found three situations: a 5-day Cold Brew outage, the quietest
2-hour window, and when pastries sell. For each one, a local model (Ollama, CPU only)
wrote a title and a recommendation. Code checked each draft, retried up to 3 times with
the reason for rejection, and fell back to a code template if the model kept failing.

10 drafts per finding:

| Finding | `llama3.2:3b` passed checks | `llama3.1:8b` passed checks |
|---|---|---|
| Cold Brew outage | 0/10, all fell back to the template | 0/10, all fell back |
| Quietest window | 10/10 | 7/10 |
| Pastry timing | 9/10 | 9/10 |

**The pass rate hid the real problem.** Many drafts that passed were wrong or useless:

- *"Reduce pastry offerings during morning hours"* contradicts the data (59% of pastries sell before 11am). 3B, passed.
- *"...promotions on Chocolate Chip Cookie ... to capitalize on its relatively lower sales"* is an invented claim. 3B, passed.
- *"...59% of pastries of pastries sell during the morning"* and *"9% of the day's orders of the day's orders"* repeat the noun right after a placeholder. Both models did this.
- *"Analyze the sales data to understand the patterns and trends..."* is empty advice. 8B, passed.
- *"Consider increasing $413.18 to offset the drop..."* is nonsense built from a bare number. 3B, passed.
- For Cold Brew, neither model would stop writing "lost" or "cost". But total sales over those days were about normal (+4.5%, within normal swings): customers just ordered other items.

The bigger model was slower (4.1 s vs 0.8 s per call on CPU), needed more retries, and
wasn't more correct. Regex checks can catch format mistakes. They can't reliably catch
advice that sounds reasonable but contradicts the data.

### Decision (option A): code picks the recommendation; the model writes only the title

- `recommend()` in `insights_agent.py` picks from a short list of vetted actions, each
  with an explicit data condition. Example: "bake for the morning" only if at least 50% of
  pastries sell before 11am. The cookie tip appears only if that pastry sells later in the
  day than orders do in general.
- The model writes a title of at most 8 words that says what happened. Titles can't give
  advice, can't claim how well something sells ("lower sales", "top seller", "not as
  popular"), and can't add nouns after a placeholder.

Why not the other options:
- **B. Try another model (`qwen2.5:7b`):** might reduce the bad advice, but not remove it.
  The checks still couldn't tell good advice from plausible-but-wrong advice.
- **C. Keep the design and add more checks:** endless patching; advice like "reduce
  morning pastries" would still get through.

This is the same lesson as `report.py`, where the model writes only the headline.

### Results after the change (`llama3.2:3b`, 10 titles per finding)

| Finding | Passed checks | Fell back to template |
|---|---|---|
| Cold Brew outage | 10/10 (3 on the first try, 7 on the second) | 0 |
| Quietest window | 10/10 | 0 |
| Pastry timing | 10/10 | 0 |

Reading the titles still found two new unsupported claims, and each one got a check and a
regression test:
- *"Cold Brew was not as popular as expected"*: Cold Brew was unavailable, not unpopular.
- *"Cold Brew was not the top seller for 5 days"*: it isn't normally the top seller.

**Lesson:** a high pass rate isn't the goal. Read what passes, turn each new mistake into
a test case, and give the model a smaller job when it keeps being wrong in ways code
can't check.

### Regression tests

`tests/test_insights_agent.py` uses the real bad drafts above as test cases and proves:
1. **Design:** even if the model writes a bad recommendation, the output uses code's recommendation.
2. **Checker:** each mistake is rejected when written as a title.
3. **Recommendations follow the data:** for example, "fix it first" only when total sales really fell.

To confirm the tests have teeth, I turned off the "lost money" and "complete phrase"
checks on purpose. 6 cases failed, then all passed again once the checks were back.

Run with: `python -m unittest -v`

## Cold Brew outage: "lost sales" vs what the cafe actually lost

The anomaly detector flagged Cold Brew at 0 sold vs about 74 expected (Sep 4–8), which is
$333 of Cold Brew revenue. Total revenue over those days was $2,702 vs about $2,585
expected: +4.5%, inside the normal ±9.6% swing for a 5-day stretch. Customers ordered
other items instead. The finding now says so, and the model never gets to call $333 a loss.

The total-revenue baseline in `findings.py`:
- Removes the weekday effect before taking a median, so weekends aren't underestimated.
- Uses the 28 closest normal days on both sides of the stretch, only up to the report
  date. The stretch is already over, so days after it are fair to use, and a nearby
  level change like the Pumpkin Spice Latte launch doesn't bias the baseline.
- Skips unusual days that `anomalies.detect()` finds in the sales data (for example, a
  festival), and never uses the simulator's planted events.
- Measures normal noise from the median absolute deviation, so one wild day can't inflate it.

Check on 40 normal 5-day windows (all taken from detector output): average error +1.1%,
and 2 of 40 fell outside the ±2-standard-deviation band, about the 1 in 20 expected.
