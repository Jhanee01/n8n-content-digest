# Results so far

Measured on the author's own cases (real posts, videos and newsletters, not
in this repository). The numbers are here so you know what to expect from the
tools, not as a benchmark.

## Is the judge any good? (November 2026)

**Planted errors.** Twelve errors were written into five real reports, each
with exactly one known problem: an invented price level read off a chart, a
made-up position, the report's own advice, a reversed direction, a misheard
name, a forecast the author didn't make. Each was judged next to the clean
version of the same report.

| Judge | Caught | Failing verdicts on the clean reports |
|---|---|---|
| `nvidia/nemotron-3.5-super-vl:free` | 12/12 | 5 |
| Claude Opus | 11/11 (one error was already in the clean report) | 4 |

**Both judges on the same 40 reports** (10 cases × 4 runs, written by
Claude Sonnet, same judge prompt):

| | Claude Opus | Nemotron |
|---|---|---|
| Passed | 14 | 19 |
| Failed only on a rule added after the reports were written (D2, D6) | 12 | 9 |
| Failed on a real error | 14 | 12 |
| A, claims covered (of 40) | 35.8 | 33.1 |
| B, price levels and timeframes (of 25) | 21.7 | 18.3 |
| C, conviction (of 10) | 9.2 | 9.4 |
| D, form, scored in code (of 15) | 13.7 | 13.7 |
| E, style (of 10) | 8.1 | 7.9 |

Agreement: pass/fail on **31 of 40** (78%), "claim covered" on 109 of 136
claims, conviction on 84 of 100. Average score difference: A 7.4, B 3.9,
C 0.9, E 1.5.

| Failing code (number of reports) | Opus | Nemotron | Both |
|---|---|---|---|
| K1a invented number | 1 | 2 | 0 |
| K1b reversed direction | 2 | 1 | 0 |
| K1c invented position | 2 | 0 | 0 |
| K4 forbidden content | 10 | 10 | 8 |
| K4 (D2) misheard name or number repeated | 9 | 6 | 6 |
| K4 (D6) content of a failed extraction | 4 | 4 | 4 |

**Against a person's decisions.** The author went through 32 disputed
failing verdicts from this run and decided each one: 28 were right (27
distinct errors, since the two judges coded one sentence differently) and 4
were wrong.

| | Claude Opus | Nemotron |
|---|---|---|
| Real errors caught (of 27) | 27 (100%) | 19 (70%) |
| Real errors missed | 0 | 8 |
| Wrong failing verdicts | 2 | 3 |
| Its failing verdicts that were right | 27 of 29 (93%) | 19 of 22 (86%) |

Nemotron missed both invented positions, the reversed direction, 3 of 9
repeated misheard values and 2 of 10 forbidden-content errors. One caveat:
the list started from Opus's verdicts, so an error neither judge flagged, or
only Nemotron flagged in a report it judged later, is not in it; that
favours Opus.

The two judges agree on the clear errors (forbidden content, failed
extraction) and split on the borderline ones: one-off invented numbers,
directions and positions. Nemotron was a little stricter on the planted set
and more lenient on positions and on the misheard-name rule. Where they
disagree, a person decides; that is the calibration step in `pontozas.md`.
Calibrating 32 disputed verdicts this way made the judge prompt clearer in
two places (an approximate chart reading is not an invented number; a
claim stated too strongly is a conviction error, not a reversed direction).

## Did the new writing rules help? (November 2026)

Three rules were added to the report skill after the baseline: a misheard
name or number never appears, not even as a correction (D2); a missing
transcript is flagged first (D3); a failed extraction gets only its title
(D6). The same 10 cases were written again twice and judged by Nemotron.

| | Before (40 reports) | After (20 reports) |
|---|---|---|
| Passed | 19 (48%) | 17 (85%) |
| Failed on a real error | 12 (30%) | 3 (15%) |
| D2: misheard value repeated | 6 | 0 |
| D6: content of a failed extraction | 4 | 0 |
| A, claims covered (of 40) | 33.1 | 38.8 |
| B, levels and timeframes (of 25) | 18.3 | 21.0 |

Two caveats. The "after" reports were judged with the clarified judge prompt,
the "before" ones with the earlier version, so part of the difference may be
the judge. And two of the three remaining failures were the failed-extraction
label in the wrong place, which a follow-up fix solved (2 of 2 passed in the
code checks afterwards).

Nemotron is free on OpenRouter but slow (about 100 seconds per report), and
without credits the free tier allows only a few dozen calls a day. About one
answer in twenty came back as invalid JSON, mostly a Hungarian opening quote
(„) closed with a straight one; the judge now repairs that case. One in forty
ran out of its 32,000 output tokens while thinking and was retried.

## Which model should write the reports? (November 2026)

The same 10 cases, written twice by each model with the current writing rules
(D2, D3, D6 and the 300-word video limit), on a Claude subscription. All 60
reports were judged once by Nemotron with the same judge prompt.

| | Claude Haiku | Claude Sonnet | Claude Opus |
|---|---|---|---|
| **Passed, chart readings checked** | **11/20** | **15/20** | **16/20** |
| Passed (judge, as given) | 10/20 | 14/20 | 12/20 |
| Failed only on a newer rule (D2) | 2 | 0 | 0 |
| Failed on a real error (after the check) | 7 | 5 | 4 |
| Passed the code checks | 19/20 | 19/20 | 20/20 |
| A, claims covered (of 40) | 36.5 | 39.0 | 40.0 |
| B, price levels and timeframes (of 25) | 19.5 | 20.9 | 21.3 |
| C, conviction (of 10) | 9.5 | 9.6 | 10.0 |
| D, form, scored in code (of 15) | 14.2 | 14.1 | 13.7 |
| E, style (of 10) | 8.8 | 8.8 | 8.5 |
| Time per report | 52 s | 26 s | 37 s |
| Output tokens per report | ~10,000 | ~2,800 | ~3,100 |
| Cost of 20 reports at API prices | $0.27 | $2.92 | $6.28 |

| Failing code (number of reports) | Haiku | Sonnet | Opus |
|---|---|---|---|
| K1a invented number (judge / after the check) | 3 / 2 | 1 / 0 | 4 / 0 |
| K3 missing label | 1 | 1 | 0 |
| K3e label not first | 0 | 1 | 0 |
| K4 forbidden content | 5 | 4 | 4 |
| K4 (D2) misheard name or number repeated | 2 | 0 | 0 |

**The chart check.** Nemotron failed seven reports for an "invented
number" that came from a chart. Each was checked against the image the
report was written from: six numbers are on the image (a FRED balance-sheet
value, a CoinMarketCap price, market cap and rank, a TradingView measuring
tool's +1135.67% over 265 candles, two horizontal levels), so those verdicts
were the judge's mistake. One was a real misreading (Haiku wrote 6.52 for a
level labelled 8.52). One Haiku K1a was not about a chart and was not
checked. None of the corrected reports had another failing code, so the
corrected pass counts are 11 / 15 / 16.

Opus is the most complete: every claim in every positive case (A 40.0 of
40), the best price levels and conviction, no form failures in code, and
after the chart check the best pass rate (16/20), at about twice the cost of
Sonnet. Sonnet is close (15/20) and the fastest. Haiku costs a tenth of
Sonnet but is twice as slow, repeated misheard names twice and misread a
chart level once.

All three failed the same hard cases (a video without a transcript 2/2, a
cycle post and a mocking quote 1/2 each), which points at the writing rules
or the reference rather than the model. With two runs per case and one judge
verdict per report, an 11 / 15 / 16 spread is a direction, not a ranking.

The judge lesson: Nemotron treats an exact number read off an image as
invented. The next judge-prompt change should say that a number is K1a only
if it is neither in the transcript nor on any image.

## Judge fix: numbers read off a chart (November 2026)

The chart check above showed that Nemotron sees the images but misreads
small print (a toolbar, a watchlist, a measuring tool), and then calls an
exact chart reading invented. The judge prompt already said chart numbers
are not invented, so repeating the rule would not help. The way it judges
changed instead:

- For a number the report takes from an image, an "invented number" (K1a)
  now counts only if the judge names the image and writes down the
  **different** number it reads there.
- If it cannot read that spot, the sentence goes on a **to-check list**: not
  a failure, a person looks at it. `judge.py` also moves an image K1a with
  no number written down to that list.
- Invented numbers that do not come from an image fail as before.

| Measurement | Before | After |
|---|---|---|
| Planted errors caught (incl. an invented chart level) | 12/12 | 11/11 (+1 not measurable) |
| Failures on the untouched reports in that test | 5 | 3 |
| The 7 disputed chart verdicts that still fail | 7 | 1 |

The one that still fails is a misreading by the judge (it read a follower
count in the corner instead of the price). The trade-off: a real chart
misreading (Haiku wrote 6.52 for a level labelled 8.52) is now on the
to-check list instead of failing, so a pass count must be read together with
the to-check count, and someone has to look at that list.

## Prompt injection

| System | Attacks | Held | Report flagged the instruction |
|---|---|---|---|
| this n8n workflow, Claude Sonnet | 11 | 11 | 10 |
| the same, on the two demo cases | 5 | 5 | 5 |

None got through, so the output check (the second line of defence) had
nothing to catch. With a mock model that did put a phishing link or a token
into the report, the output check held it back every time, and the report
went only to `alertChatId`.
