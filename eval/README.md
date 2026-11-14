# Evals

Tools to measure how good the reports are, and whether a prompt injection in
the source material gets through. They talk to the workflow through the
`Eval: webhook` node (see the main README), so nothing is collected and
nothing goes to Telegram.

The rules and the reference files are in Hungarian, like the reports.

## What's here

```
run_n8n.py             sends each case to the webhook, saves the report, scores it in code
score.py               the code checks: required labels, forbidden numbers, format, price levels
judge.py               LLM judge: invented facts, own advice, forbidden content, claims covered
biro_prompt.md         the judge's prompt
biro_osszevetes.py     how well two judges agree on the same reports
redteam_n8n.py         prompt-injection attacks against the workflow
redteam/tamadasok.yaml the attacks (text in a post, mid-transcript, drawn on a chart, ...)
pontozas.md            the scoring rules (Hungarian)
check_refs.py          checks the reference files
manifest.yaml          the list of cases
cases/demo_*/          two invented demo cases (input + reference.yaml)
```

A case is a frozen input (the items, with their images and frames) plus a
**reference**: the key claims a good report must contain (asset, direction,
levels, time frame, conviction, position) and what it must not contain. The
two demo cases are invented, so they can be public. Your own cases go next to
them under `cases/`; `.gitignore` keeps everything except `demo_*` out of the
repository.

## Running it

The workflow must be active, with a Header Auth credential on `Eval: webhook`
(`X-Eval-Token`). Put the same token in `EVAL_N8N_TOKEN`.

```bash
pip install requests pyyaml pillow
export EVAL_N8N_TOKEN=<token>
URL=http://localhost:5678/webhook/content-digest-eval

python eval/check_refs.py                          # references are well-formed
python eval/run_n8n.py --url $URL --runs 4         # reports + code checks → eval/runs/<time>_n8n/
python eval/judge.py eval/runs/<time>_n8n          # LLM judge on those reports
python eval/redteam_n8n.py --url $URL              # injection attacks
```

`--claude-url` points a run at the local service (Claude on a subscription),
`--model` overrides the workflow's model, `--frames 0` sends videos without
frames.

## The judge

Anything that needs judgement goes to an LLM judge: an invented number, a
reversed direction, an invented position, the report's own advice, forbidden
content, and which reference claims are covered. Every failing verdict has to
quote the report word for word, and the code drops any quote that isn't in it.

By default the judge is not a Claude model, because Claude writes the
reports: `nvidia/nemotron-3.5-super-vl:free` on OpenRouter (key in
`OPENROUTER_API_KEY`). Any OpenAI-compatible endpoint works with
`--model api:<model id>` and `JUDGE_API_BASE`; `--model opus` uses Claude Code.
On 12 errors planted into real reports, Nemotron caught 12 and Claude Opus 11
(the twelfth was already in the clean report).

`biro_osszevetes.py` compares two judges on the same run. `--repeat 3` judges
each report three times to measure the judge's own noise.

## The red team

Each attack writes text aimed at the model into a case's input (in a post, in
the middle of a transcript, as a fake system message, drawn on a chart image)
and checks in code whether its canary (a link, a number, a phrase) reached the
report, and whether the workflow's output check held the report back. On the
author's own cases, 11 attacks against Claude Sonnet on a subscription: all
held, and in 10 the report said it had left an instruction out.
