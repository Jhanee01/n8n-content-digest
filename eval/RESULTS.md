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

**Agreement on 31 baseline reports** (10 cases, written by Claude Sonnet,
judged by both):

- pass/fail agreed on **27 of 31** (87%);
- "claim covered" agreed on 67 of 80 claims, conviction on 58 of 65;
- average score difference: A (claims) 6.5 of 40, B (levels) 3.6 of 25,
  C 0.8 of 10, E 1.3 of 10.

| Failing code | Opus | Nemotron | Both |
|---|---|---|---|
| K1a invented number | 1 | 1 | 0 |
| K1b reversed direction | 1 | 0 | 0 |
| K4 forbidden content | 10 | 10 | 8 |
| K4 (D2) misheard name or number repeated | 7 | 5 | 5 |
| K4 (D6) content of a failed extraction | 3 | 3 | 3 |

The two judges agree on the clear errors and split on the borderline ones.
Nemotron was a little stricter on the planted set and a little more lenient
on the misheard-name rule. Where they disagree, a person decides; that is the
calibration step in `pontozas.md`.

Nemotron is free on OpenRouter but slow (about 100 seconds per report), and
without credits the free tier allows only a few dozen calls a day.

## Prompt injection

| System | Attacks | Held | Report flagged the instruction |
|---|---|---|---|
| this n8n workflow, Claude Sonnet | 11 | 11 | 10 |
| the same, on the two demo cases | 5 | 5 | 5 |

None got through, so the output check (the second line of defence) had
nothing to catch. With a mock model that did put a phishing link or a token
into the report, the output check held it back every time, and the report
went only to `alertChatId`.
