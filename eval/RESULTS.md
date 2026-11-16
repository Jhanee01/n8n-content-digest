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
(D2, D3, D6 and the 300-word video limit), on a Claude subscription. All 40
reports were judged once by Nemotron with the same judge prompt.

| | Claude Haiku | Claude Sonnet |
|---|---|---|
| Passed (judge) | 10/20 | 14/20 |
| Failed only on a newer rule (D2) | 2 | 0 |
| Failed on a real error | 8 | 6 |
| Passed the code checks | 19/20 | 19/20 |
| A, claims covered (of 40) | 36.5 | 39.0 |
| B, price levels and timeframes (of 25) | 19.5 | 20.9 |
| C, conviction (of 10) | 9.5 | 9.6 |
| D, form, scored in code (of 15) | 14.2 | 14.1 |
| E, style (of 10) | 8.8 | 8.8 |
| Time per report | 52 s | 26 s |
| Output tokens per report | ~10,000 | ~2,800 |
| Cost of 20 reports at API prices | $0.27 | $2.92 |

| Failing code (number of reports) | Haiku | Sonnet |
|---|---|---|
| K1a invented number | 3 | 1 |
| K3 missing label | 1 | 1 |
| K3e label not first | 0 | 1 |
| K4 forbidden content | 5 | 4 |
| K4 (D2) misheard name or number repeated | 2 | 0 |

Sonnet is better on content: fewer invented numbers and no misheard names
repeated, and it passed three cases twice that Haiku failed at least once.
Haiku costs a tenth as much but takes twice as long and writes almost four
times as many output tokens. Both models failed the same hard cases (a video
without a transcript 2/2, a cycle post and a mocking quote 1/2 each), which
points at the writing rules or the reference rather than the model. With two
runs per case and one judge verdict per report, a 10 vs 14 difference is close
to the noise; it is a direction, not a ranking.

## Prompt injection

| System | Attacks | Held | Report flagged the instruction |
|---|---|---|---|
| this n8n workflow, Claude Sonnet | 11 | 11 | 10 |
| the same, on the two demo cases | 5 | 5 | 5 |

None got through, so the output check (the second line of defence) had
nothing to catch. With a mock model that did put a phishing link or a token
into the report, the output check held it back every time, and the report
went only to `alertChatId`.
