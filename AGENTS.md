# Working on MindBridge

Decisions that already cost something to learn. Each one is here so it does not
get re-derived, re-argued, or silently reverted.

## The standard this project is held to

MindBridge exists to survive an interview question. Its value is not that it
works — it is that every claim on it can be reproduced on demand. That makes one
rule non-negotiable:

**Never show a number, a metric, or a generated artifact without also showing
where it came from.**

In practice:

- `evals/results.json` ships with metrics `null`, and the landing page renders
  `null` as an amber **in progress** badge. A figure appears only after a script
  produced it. Do not fill these in by hand.
- A model-written diary card carries an amber "model-written · schema-validated"
  badge and keeps the reproducible rule-based headline directly beneath it. A
  generated card must never be indistinguishable from a computed one.
- `train/eval_holdout.py` refuses to publish a compliance rate computed on fewer
  than 30 holdout days. A rate over a handful of days has an error bar wider
  than the number, and it would land on a public page.
- If a feature is not wired into the product path, it is not shipped. Say so.

## Measured facts — do not re-guess these

| fact | value | n | where |
| --- | --- | --- | --- |
| **Teacher first-attempt schema compliance** | **84.7%** | **281** | current bar; `extract.runner --stats` |
| **Local 3B MLX first-attempt compliance** | **86.7%** | **45 seeded holdout prompts** | `evals/mlx_holdout_seed_3407.json` |
| **Base qwen2.5:7b first-attempt compliance** | **100%** | **15 real sessions via ollama** | this run, `meta.model="qwen2.5:7b"` in the dataset |
| Teacher on the same seeded holdout | 82.2% | 45 | `evals/mlx_holdout_seed_3407.json` |
| Same, earlier smaller sample | 81.4% | 86 | superseded — kept so older commits read correctly |
| Teacher compliance with repair loop | 100% | 86 | same run |
| T3 dedup threshold under nomic-embed-text | **0.80** | 170 rows read individually | `api/settings.py` |
| Semantic query cache viability | **not viable** | 27 queries / 54 requests | `cacheCostSaving` stays null |

**84.7% (n=281) is the bar** stage two's fine-tuned model must clear, judged by the
same rule: **first reply only, repairs excluded.** Changing that definition to
make a number look better invalidates the comparison the whole project rests on.

The figure rose from 81.4% as the sample grew; quote it with its n, and re-read
it from `--stats` rather than copying it from here. The local model cleared the
bar on the fixed 45-prompt holdout (39 valid versus the teacher's 37), but that
two-prompt difference is not evidence of statistical superiority. Thirteen
prompts were truncated to the 4096-token context limit. Keep the public metric
in progress while the adapter and its training data remain private artifacts.

## Traps in the data — each of these was a real bug

**Claude Code writes one JSONL record per content block, and repeats the
response's `usage` on every one.** Summing per record inflated token totals by
**2.5x**. Records sharing a `message.id` are merged into one turn and usage is
counted once. Do not undo this.

**The newest record group may still be streaming.** Unless a file has been quiet
for a minute, the trailing group is held back and the cursor stops before it, so
a half-written response is never stored.

**Transcripts contain NUL bytes**, which Postgres `text` cannot store. Stripped
in a validator on `ParsedTurn.text` so every reader is covered at once.

**The same session can be written under two project directories**, producing
duplicate uuids in one batch. Postgres rejects an `ON CONFLICT DO UPDATE` whose
input proposes the same key twice; batches are collapsed to the first
occurrence.

**Day cards must be rebuilt from Postgres over the whole local day**, never from
the turns an incremental run happened to parse. Building from the delta rewrote
a 683-turn card as a 223-turn one. This is why `session_turns` persists
`project`, `git_branch` and `tool_names`.

**Day cards and session cards share one table.** Every read states a scope
(`/summaries?scope=day|session|all`, default `day`). Without it the diary's day
list fills with hundreds of session rows.

**nomic-embed-text collapses on short queries.** Two unrelated questions —
`回复应该写得多详细` and `测试数据应该怎么准备` — score cosine **0.9992**, above a
genuine paraphrase pair at 0.9064. Negatives sit above positives, so no
threshold separates them and a semantic *query* cache cannot be made safe with
this model. Measured over 27 queries in 8 adjacent intent groups; the best point
(0.90) still had a 50% false-hit rate.

This does **not** impugn the 0.80 dedup threshold. Dedup compares long stored
*statements*, where the model behaves; the cache compares short *queries*, which
is the regime where it degrades. Two different populations, two different
numbers — do not copy one to the other.

Deeper ceiling: only 1 of 35 same-intent query pairs retrieved the same rows at
all. Retrieval is not stable across paraphrases, so the thing a query cache
would cache is not stable either.

## Embeddings and dedup

The `hashing` embedder measures token overlap, not meaning. It scores real
duplicates 0.13–0.73 — "Use uv instead of pip" against "Python 项目优先用 uv" is
**0.251**, because two languages share no tokens. Under it, write-time dedup
never fires: 121 preferences all sat at `access_count` 0.

Use **ollama + nomic-embed-text (768 dims)**. It keeps the local-only promise,
needs no key, and merged 103 of 411 preference writes on replay.

**The 0.80 threshold is model-specific and was read off 170 real rows, not
picked.** nomic-embed-text puts *topically* related preferences at 0.62–0.75 —
everything in T3 is "how Cassie likes to work", so the space is compressed. In
that band merges are mostly wrong; one at 0.686 would have merged "validate with
mock data first" into "no fake data anywhere", which are nearly opposite.

Err high. **A missed merge leaves visible clutter; a false merge silently closes
a distinct preference and nobody notices.** Retune when changing model.

## Privacy boundaries

Path A (log parsing) and Path B (MCP) never leave the machine. Hosted extraction
does, so it is gated behind `--send-to-provider` and refuses to run without it.
`--dry-run` prints byte-for-byte what would be sent.

The landing page says "parsing and storage are local" and states the hosted
extractor as an explicit exception. **Do not shorten that back to "nothing
leaves your machine"** while stage one still calls a hosted API.

Transcripts are mounted read-only into containers, and only the transcript
subdirectories — never all of `~/.claude`, which holds credentials.

Secrets are masked before any transcript text is stored (`ingest/redaction.py`).
70 were caught across the first corpus.

## The database is disposable

Postgres holds nothing that cannot be rebuilt. The durable artifacts are
transcripts on disk and `train/dataset/extraction.jsonl`, written before
anything touches the database.

```bash
.venv/bin/python -m ingest.runner --full
.venv/bin/python -m scripts.replay_extractions --apply
```

Replay calls no model and reproduces byte-identical prose. It also rewrites
preferences through the *current* embedder, so rows stored under an old one come
back deduplicated.

This was learned the hard way: a `docker compose --profile tools down` during
cleanup is the likely cause of losing the store once. **Think before running
compose lifecycle commands against a stateful volume**, and verify a backup is
non-empty before trusting it.

## Method notes

**Small samples lie, and 30 is still small.** A 7-pair sample produced the 0.62
threshold, which collapsed on 170 rows. A 3-sample run showed 100% compliance
where 30 samples showed 80% — and that 80% was itself wrong: at n=235 the same
v2 prompt scores **85%**, above v1's 83%. The conclusion "the prompt change did
not help" was recorded confidently and survived two sessions before the data
overturned it. Re-read `--stats`; do not quote a remembered comparison. When n
is small, read every case individually rather than the aggregate rate.

**Version anything that can move a metric.** `PROMPT_VERSION` is recorded per
attempt and `--stats` breaks compliance down by it, so a prompt change cannot
silently blend two populations into a number describing neither.

**Testing the happy path proves nothing.** Dedup "worked" when sent identical
text and had never once worked on a paraphrase.

**Pipes hide exit codes.** `npm run build | tail -4 && git commit` commits on a
failed build, because the status comes from `tail`.

## Ask before

Installing the launchd agent, writing API keys, running a migration, pushing,
renaming the public repo. Build it, show what it would do, then wait.


## Schema compliance is not extraction quality (measured twice)

Base `qwen2.5:7b` scored **15/15 first-attempt schema compliance** against the
teacher's **84.4%** — and was still the worse extractor. Of the 5 preferences it
wrote across 15 sessions, 3 were the session's next task ("增加 GitHub Actions CI
是下一步的重要任务"), not a durable preference. The 3B failed the same way in the
other direction, inventing preferences out of activity narration.

Compliance measures whether the JSON parses. It says nothing about whether the
right thing went into T3. Do not quote compliance as an extraction-quality
number, on the résumé or anywhere else.

Corollary found the same run: `reject_transient` was English-regex only. The
teacher never tripped it in Chinese (**0 of 573 open T3 rows** matched Chinese
todo markers), so the gap stayed invisible until a local model wrote Chinese
todos straight through. A guard that only ever sees compliant input is not a
guard that works — it is a guard that has never been tested.
