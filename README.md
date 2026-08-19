# MindBridge

A reflective AI companion powered by a transparent temporal Memory Core. The
product helps a person see what stayed, what changed, and what should no longer
define them; the technical core preserves the source, time and validity of each
memory instead of flattening a person into timeless vector chunks.

The current shipped backend parses transcripts from local AI coding tools,
builds daily memory cards, and serves the same T1/T2/T3 store to any MCP client.
The public Companion Loop is a deterministic synthetic product demo; it does
not connect to Cassie's private store. Parsing, storage and the MLX extraction
path stay on the user's machine. Hosted extraction remains an explicit opt-in
for generating training data.

[Live site](https://mindbridge.liangyue.site) ·
[Diary](https://mindbridge.liangyue.site/demo) ·
[Interview demo](https://mindbridge.liangyue.site/interview-demo)

## Two-minute interview demo

Open the **[Companion Loop](https://mindbridge.liangyue.site/interview-demo)**
([中文](https://mindbridge.liangyue.site/interview-demo/zh)) with no login:

1. Click Day 01 → Day 04 to see an identity move from “memory tool” to
   “reflective AI companion.”
2. Notice that uncertainty becomes a candidate, not a silently stored fact;
   only explicit confirmation supersedes the old identity.
3. In **Memory Receipt**, ask what MindBridge is now, what changed, and what to
   build next. Each answer exposes current and superseded evidence; the final
   recommendation is labelled as an inference.

All visible data is synthetic and deterministic. The page demonstrates product
behavior safely; the repository contains the real temporal memory, REST, MCP,
ingestion and local MLX implementation underneath it.

## Two capture paths

**Path A — passive log parsing.** Nothing for you to do. A background pass reads
structured logs already on disk and extracts what happened that day. This only
works for tools that write such logs:

- `~/.claude/projects/**/*.jsonl` (Claude Code)
- `~/.codex/sessions/**/rollout-*.jsonl` plus
  `~/.codex/archived_sessions/rollout-*.jsonl` (active and archived Codex CLI)

**Path B — active MCP read/write.** Mounted as a standard MCP server exposing
`get_daily_review` across all review layers; separate T2/T3 reads; confirmed
durable writes; and a Pattern Candidate review loop. Works with any MCP client
— Codex, Claude Desktop, Claude Code, Cursor and VS Code.

ChatGPT and web Claude expose no history API; they need a manual export and are
out of scope for the automatic path. The landing page states this rather than
implying full coverage.

## Three memory tiers

| Tier | Holds | Backed by |
| --- | --- | --- |
| T1 | The day's raw turns | session buffer, in process |
| T2 | One structured card per day | rolling summary |
| T3 | Long-term operational + reflective memory, time-decayed | Postgres + pgvector |

Every T3 record carries `created_at` and `valid_at`, so a superseded preference
decays out of recall instead of being silently overwritten. Writes run a
cosine-similarity check first, so one preference stays one row.

T3 has two explicit namespaces. **Operational** memory tells an agent how to
work with the user (`coding_style`, `tool_preference`, `behavioral_fact`,
`schedule`, `other`). **Reflective** memory holds wording the user has reviewed
about recurring patterns, values, triggers, helpful strategies or an identity
hypothesis. Reflective writes fail validation unless `confirmed_by_user=true`;
automatic transcript extraction continues to write operational memory only.
An MBTI-like label can therefore be generated as a dated, sourced hypothesis,
not silently stored as a fact about the person.

T1 also stores the project, git branch and tool names of each turn. That is what
lets a day card be rebuilt over the whole day from Postgres: building it from
only the turns an incremental run happened to parse would rewrite a full card
with a partial one, so a nightly job would shrink every card it touched.

## Current state

The front end, ingestion, memory service and local extraction loop are built.
The deployed site still uses sample data because the database and model live on
one Mac; it labels that state instead of presenting the sample as live.

| Piece | State |
| --- | --- |
| Landing page (`/`) — two paths, architecture, metrics table, signup | done |
| Diary (`/demo`) — daily card, memory timeline, raw T1/T2/T3 disclosure | done |
| `api/` — FastAPI, three tiers, decay retrieval, dedup, query cache | done (M1) |
| `mcp_server/` — separate T2/T3 review, T3 recall and confirmed writes over stdio | done (M3) |
| Pattern Candidate → confirm/edit/reject → reflective T3 receipt | done locally |
| `get_daily_review` — T2 + both T3 lanes + pending candidates | done locally |
| `evals/eval_memory_engine.py` — decay, dedup and token benchmarks | done |
| `ingest/` — Path A readers for Claude Code and Codex CLI | done |
| `/demo` wired to the API, with an offline fallback | done |
| Nightly scheduler for Path A (launchd, opt-in) | done |
| `train/` — 3B 4-bit MLX LoRA training and holdout evaluation (M2) | completed locally (2026-08-09) |
| Local MLX HTTP provider → T2/T3 → API/Diary/MCP | working locally |
| Semantic query cache experiment (M5) | measured unsafe; disabled |

The diary at `/demo` now reads the backend through `/api/diary`. When the API is
reachable it renders real cards, real T1 turns and the real T3 timeline, and the
banner turns green. When it is not — which is the case on the deployed site,
since the backend runs on one laptop — it falls back to sample data, turns the
banner amber, and names the unreachable URL. The distinction is carried in the
payload's `source` field rather than inferred from a failed request, so sample
rows can never be presented as if they came from Postgres.

Path A first produces a reproducible **rule-based** day card — counts, tool
tallies, time spans and git branches. The optional local MLX pass adds narrative
and durable preferences, while keeping the rule-based facts beneath it. Those
preferences use the same `MemoryService` write path as MCP, so extraction and an
agent call cannot implement different dedup behaviour.

The default embedder is a **deterministic hashing fallback**: offline, no key,
and lexical-only. It exists so the stack boots and the mechanical tests run
anywhere. It is not a semantic model, and the eval refuses to publish
retrieval-quality numbers measured under it.

## Backend quickstart

```bash
cp .env.example .env
docker compose up -d db redis     # Postgres+pgvector on :5433, Redis on :6379
docker compose up -d api          # FastAPI on :8000, OpenAPI at /docs
curl localhost:8000/healthz
docker compose run --rm evals     # benchmark -> evals/results.json
```

### Path A: ingest your own transcripts

```bash
docker compose run --rm ingest                      # dry run, writes nothing
docker compose run --rm ingest --since 7d           # ingest the last week
docker compose run --rm ingest --full               # re-read everything
docker compose run --rm ingest --status             # what has been read so far
```

Transcripts are mounted **read-only**, and only the transcript directories —
not all of `~/.claude` or `~/.codex`, which also hold credentials the container
has no reason to see. Text is passed through `ingest/redaction.py` before it is
stored, which masks well-known key shapes (provider keys, bearer tokens, JWTs,
`SECRET=`-style assignments, DSN passwords). That is a safety net, not a
guarantee: it cannot catch a secret that looks like ordinary prose.

Ingestion is incremental and idempotent. Each file has a byte cursor, so a
re-run reads only what was appended; every turn also carries a `source_key`, so
even `--full` on an already-ingested file inserts nothing. Two details the
Claude Code format forces:

- **One response spans several records**, one per content block, and each
  repeats the same final `message.usage`. Summing per record inflated the token
  total by 2.5x on real data, so records sharing a `message.id` are merged into
  one turn and usage is counted once.
- **The newest group may still be streaming.** Unless a file has been quiet for
  a minute, its trailing group is held back and the cursor stops before it, so
  a half-written response is never stored.

Tool arguments and tool results are **not** stored by default: they are large,
they duplicate file contents already on disk, and they are the likeliest place
for a credential to appear. Only the tool *name* is kept. `--include-tool-io`
overrides this.

Register the MCP server with Claude Desktop, Claude Code, Cursor or VS Code:

```json
{
  "mcpServers": {
    "mindbridge": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/mindbridge",
      "env": {
        "MINDBRIDGE_DATABASE_URL": "postgresql://mindbridge:mindbridge@localhost:5433/mindbridge"
      }
    }
  }
}
```

For the two local coding clients, the repository includes idempotent installers.
Both launch the same STDIO server and therefore read and write the same local
Postgres memory store:

```bash
scripts/install-codex-mcp.sh
scripts/install-claude-mcp.sh
codex mcp list
claude mcp list
```

MCP is the active read/write path. Passive transcript capture is separate:
`python -m ingest.runner --source all` reads Claude Code plus active and archived
Codex JSONL into one T1 table, labels every row by source, then rebuilds shared
T2 cards. Source files are read-only and `source_key` keeps re-runs idempotent.

### Use MindBridge inside Codex

Codex supports local STDIO MCP servers. Point it at the repository virtualenv,
then set the repository as the server working directory:

```toml
[mcp_servers.mindbridge]
command = "/absolute/path/to/mindbridge/.venv/bin/python"
args = ["-m", "mcp_server.server"]
cwd = "/absolute/path/to/mindbridge"
startup_timeout_sec = 20
tool_timeout_sec = 60

[mcp_servers.mindbridge.tools.temporal_query]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.get_daily_card]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.review_long_term_memory]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.upsert_preference]
approval_mode = "prompt"
```

Start Postgres, Redis and the configured local embedder before opening a fresh
Codex session:

```bash
docker compose up -d db redis
ollama list                         # must include nomic-embed-text
codex mcp list                      # mindbridge should be enabled
```

Try: `Call MindBridge's get_daily_card for the latest T2 card, then review the
newest five T3 memories separately. Cite every card and memory id.` Reads can
run directly; durable writes ask for confirmation.
On 2026-08-19 a fresh Codex session called `temporal_query` against the local
store and returned three real T3 records with source ids. This is a local
integration, not a public hosted memory service; restarting Codex or opening a
new session is required after changing MCP configuration.

On 2026-08-20 Claude Code was registered at user scope and reported the same
MindBridge STDIO server connected. A dual-source ingest then read Claude Code,
active Codex and archived Codex logs into the same Postgres store; the database
contained both source labels and zero duplicate `source_key` values. A fresh,
non-persistent Claude Code session then called `get_daily_review` through the
registered MCP server and received T2, operational T3, reflective T3 and Pattern
Candidates as four separate sections without invoking any write tool.

That validation ran on a MacBook Pro (Apple M1 Pro, 8-core, 16 GB RAM), macOS
26.5.2 / arm64, Claude Code 2.1.226, Python 3.12.13 inside the MindBridge
virtualenv, Docker 29.2.1 and Ollama 0.32.5. The landing page separates this
verified device from Anthropic's official minimum requirements.

Both transports call the same `MemoryService`, so `upsert_preference` over MCP
and `POST /memories` over HTTP cannot drift apart.

| Endpoint | Tier | Purpose |
| --- | --- | --- |
| `POST /sessions/{id}/turns` · `GET /sessions/{id}/buffer` | T1 | append and read the raw window |
| `POST /summaries` · `GET /summaries` | T2 | write and list period cards |
| `POST /memories` | T3 | dedup-then-write operational or confirmed reflective memory |
| `GET /memories?namespace=` | T3 | newest-first listing, optionally one namespace |
| `GET /turns?start=&end=` | T1 | raw turns in a range; the caller owns the timezone |
| `POST /memories/query` | T3 | time-decayed top-K recall, optionally one namespace |
| `POST /patterns` · `GET /patterns` | Reflection | create/review candidates outside T3 |
| `POST /patterns/{id}/resolve` | Reflection | confirm/edit into reflective T3, or reject |
| `GET /daily-review` | Companion | one review surface across T2, T3 and candidates |

### Pattern Candidate discovery (deterministic)

Rule-based candidate discovery now turns repeated T2 facts into reviewable
Pattern Candidates with no model in the loop:

```bash
.venv/bin/python -m scripts.suggest_patterns --since 30d
# add --apply to insert pending candidates into Postgres
```

The scanner currently extracts repeatable signals from deterministic facts such as
project touches, tool usage, git branches, source labels and late-hour work.
It enforces the same safety gate as manual proposals:
at least 3 supporting observations across at least 2 distinct dates.

Measured on this machine (2026-08-04, `--since 7d`): 91 of 282 transcript files
had new content, yielding 3,067 T1 turns across 51 sessions and 5 T2 day cards,
with 8 suspected secrets masked. A second run read 2 files; `--full` re-read all
3,065 turns and inserted 0.

## Running Path A on a schedule

```bash
scripts/mindbridge-scheduler.sh status      # installed? when did it last run?
scripts/mindbridge-scheduler.sh run-now     # run once, in the foreground
scripts/mindbridge-scheduler.sh install     # schedule it nightly at 23:30
scripts/mindbridge-scheduler.sh uninstall   # remove the schedule
```

`install` writes `~/Library/LaunchAgents/com.mindbridge.nightly-ingest.plist`
and registers it with launchd. That is a persistent change to the machine, so it
is opt-in and never happens as part of a build or a test — `status` and
`run-now` change nothing. Override the hour with `MINDBRIDGE_INGEST_HOUR` /
`MINDBRIDGE_INGEST_MINUTE`.

The job is safe to repeat: turns are keyed by source record, and day cards are
rebuilt from the database rather than from the delta. If Docker Desktop is not
running it logs a skip, leaves the cursors untouched and exits 0, so the next
run resumes exactly where this one would have. Logs land in
`~/Library/Logs/mindbridge/ingest.log`, rotated once at 5 MB.

An incremental run takes about seven seconds on an already-ingested corpus.

## M2 — turning a day into a diary

Stage one can use OpenAI, Gemini or an existing Claude Code sign-in to produce
validated training pairs. That hosted path requires an explicit send flag.
Stage two is now available locally: `mlx_lm.server` loads the fine-tuned
Qwen2.5-3B 4-bit adapter, and `extract.runner --provider mlx` calls its
OpenAI-compatible endpoint without an API key or send flag.

```bash
# see the exact prompt and what it would cost — no key needed, sends nothing
docker compose run --rm extract --date 2026-08-04 --dry-run

# actually extract (requires a key AND the explicit send flag)
export MINDBRIDGE_OPENAI_API_KEY=...      # or MINDBRIDGE_GEMINI_API_KEY
docker compose run --rm extract --missing --limit 5 --send-to-provider

# or reuse the signed-in host Claude Code CLI — no API key
# (run on the host because the Docker image intentionally has no CLI credentials)
uv run --with-requirements requirements.txt python -m extract.runner \
    --date 2026-08-04 --provider claude-cli --send-to-provider

# local model service — the adapter stays on this Mac
.venv/bin/mlx_lm.server \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit \
    --adapter-path train/outputs/mlx-adapters \
    --host 127.0.0.1 --port 8080 --max-tokens 1200 --temp 0.2

# in another terminal: transcript → schema JSON → T2 narrative + T3 preferences
.venv/bin/python -m extract.runner --missing --limit 1 --provider mlx

# cumulative schema-compliance figures
docker compose run --rm extract --stats

# offline tests of the schema and repair loop — no key, no network
docker compose run --rm --no-deps --entrypoint python extract \
    -m extract.test_pipeline
```

**Only the hosted providers send data off the machine.** OpenAI, Gemini and
Claude Code CLI transmit that day's excerpts, so they are gated behind
`--send-to-provider`. `--dry-run` prints byte-for-byte what would be sent. The
`mlx` provider talks only to `127.0.0.1` by default and deliberately does not
require that flag.

Compliance is reported two ways and the difference matters: `first_attempt_rate`
is how often the model returned a schema-valid object *without* correction, and
`eventual_rate` includes the repair loop. Only the first is comparable to the
tuned model, so only the first is the number to quote.

Validation is not decorative. `DiaryDraft` forbids extra keys, rejects any
narrative that infers an emotional state ("you seemed frustrated"), and rejects
a one-off task masquerading as a durable preference — a model drifts into all
three, and a prompt alone does not stop it.

### Training and evaluation on Apple silicon

```bash
python -m train.prepare_dataset --report
python -m train.train_mlx \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit
python -m train.eval_mlx \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit \
    --adapter train/outputs/mlx-adapters --seed 3407 \
    --out train/outputs/mlx-adapters/holdout-eval.json
```

The split is by date and deterministic, so a day's pairs never straddle
train/holdout. `eval_holdout.py` refuses to publish a compliance rate computed
on fewer than 30 holdout days, because a rate over a handful of days has an
error bar wider than the number and it would land on a public page.

### Per-session cards

One card per day caps the training set at one pair per day, which is far too
slow to reach a usable fine-tune. Ingest also writes one card per session, which
on this history is 242 cards against 56 days — about 4x the extraction targets,
and they compound as you work.

```bash
# session cards are written by default; --no-session-cards opts out
docker compose run --rm ingest --since 3d

# rebuild every day's cards from T1, no transcript read
docker compose run --rm ingest --rebuild-cards all

# extract prose for session cards instead of day cards
.venv/bin/python -m extract.runner --missing --scope session --limit 10 \
    --provider claude-cli --send-to-provider
```

Sessions under 6 turns are skipped: a single question and a one-line answer
produces a card that says less than its own metadata, and as training data it
teaches the model to pad.

Day and session cards share one table, so every read states its scope
(`/summaries?scope=day|session|all`, default `day`). Without that the diary's day
list would silently fill with hundreds of session rows.

A session card costs about 1.7k input tokens against ~11k for a day card, since
it only sees its own session's turns.

### Prompt versioning

`PROMPT_VERSION` is recorded on every attempt and `--stats` breaks compliance
down by it. A prompt change moves the rate, so a single pooled number across
versions would describe neither. The first 46 days ran on `v1`, whose system
prompt never mentioned `confidence` even though the schema requires it — that one
omission caused 17 of 19 failures.

## Measured baselines

| what | value | n | how |
| --- | --- | --- | --- |
| Teacher first-attempt schema compliance | **84.7%** | 281 | Sonnet via claude-cli; `--stats` |
| Teacher on the date-isolated local holdout | **82.2%** | 45 | captured first-attempt flags |
| Qwen2.5-3B MLX LoRA pilot | **86.7%** | 45 | seed 3407; repairs excluded; `evals/mlx_holdout_seed_3407.json` |

The MLX LoRA run is complete: 198 fit rows, 34 training-side validation rows,
and 45 date-isolated holdout rows produced the adapter in
`train/outputs/mlx-adapters/`. “Pilot” describes the evidence scope, not an
unexecuted training plan.

One earlier conclusion has since been overturned by its own method. At n=40 the
v2 prompt scored 80% against v1's 83%, and this file recorded that the change
"did not help". At n=235 v2 is **85%** against the same 83%. The original verdict
was itself the small-sample artifact it warned about; re-read `--stats` rather
than trusting a remembered comparison.

The 84.7% teacher figure is the broad baseline. The 45-pair comparison is the
like-for-like one because both models see the same held-out dates. The local
pilot is reproducible and encouraging, not a claim of statistical superiority:
39 valid replies versus 37 is a two-case difference, and 13 prompts required
the same 4,096-token truncation used during training.

## Current local state

Read from the running store on 2026-08-11, not copied forward from an earlier
run. Everything here is rebuildable, so treat it as a snapshot rather than a
fact about the project.

| | |
| --- | --- |
| T1 turns | 13,072 |
| T2 day cards / session cards | 56 / 242 |
| Cards carrying model-written prose | 238 |
| T3 open preferences | 329 |
| Captured extraction pairs | 281 |
| MLX adapter | `train/outputs/mlx-adapters/adapters.safetensors` |

T3 was briefly down to a single preference after a rebuild replayed narratives
without replaying preferences. Restored on 2026-08-11 by:

```bash
.venv/bin/python -m scripts.replay_extractions --apply
```

That reads the captured pairs and writes their preferences back through the
current embedder, calling no model. It restored 329 open preferences and merged
85 of 413 writes on the way in — the top rows absorbed three duplicates each,
which is the dedup path doing its job rather than a coincidence.

## Rebuilding the database from scratch

Postgres is disposable. Everything needed to reconstruct it lives outside:
transcripts on disk, and every extraction in `train/dataset/extraction.jsonl`,
written there before it ever touched the database.

```bash
# T1 turns + T2 day/session cards, from the transcripts
.venv/bin/python -m ingest.runner --full

# T2 narratives + T3 preferences, from the captured dataset — no model calls
.venv/bin/python -m scripts.replay_extractions --apply
```

Replaying costs nothing and reproduces byte-identical prose. It also re-writes
preferences through the *current* embedder and threshold, so rows first stored
under the non-semantic hashing embedder come back deduplicated.

## Metrics policy

The landing page reads `evals/results.json` directly. Every value traces to a
re-runnable script against a live database, so every number on the page is
reproducible with one command.

- The four engine metrics (`promptTokenReduction`, `dedupAccuracy`,
  `decayOrdering`, `supersedeExclusion`) come from `evals/eval_memory_engine.py`.
- `extractionJsonAccuracy` (86.7%) and `localExtractionCostDelta` (18.8s, the
  local MLX latency per holdout pair) come from the fixed-seed MLX run in
  `evals/mlx_holdout_seed_3407.json` (`train/eval_mlx.py`).
- `cacheCostSaving` is a qualitative verdict, not a number: the semantic cache
  was measured and found unsafe (an unrelated short-query pair scored 0.9992,
  above the 0.9064 true paraphrase), so it stays off rather than publishing a
  saving it cannot back.

Where a metric has no honest number, the page states the measured conclusion
instead of inventing one.

| Metric | Depends on embedder | Script |
| --- | --- | --- |
| Time-decay scoring correctness | no | `evals/eval_memory_engine.py` |
| Superseded-record isolation | no | `evals/eval_memory_engine.py` |
| Write-time dedup accuracy | yes | `evals/eval_memory_engine.py` |
| Per-turn prompt token reduction | yes | `evals/eval_memory_engine.py` |
| Preference-extraction JSON validity | yes | `train/eval_mlx.py` (local evidence; public metric still null) |
| Extraction API cost delta | yes | not yet measured on a deployable serving target |
| Cost saved by the semantic cache | yes | measured unsafe; remains null and disabled |

"Time-decay scoring correctness" stores identical content at several ages and
checks both newest-first ordering and that every score matches
`cosine · exp(-λ·Δt)` to within 1e-6. It is a formula check, not a claim about
retrieval quality — the table on the site says so too.

### Why semantic query caching stays off

An exact-key cache is safe and remains enabled. The semantic-neighbour path is
implemented but failed its own acceptance test under `nomic-embed-text`:

- an unrelated short-query pair scored cosine **0.9992**;
- the one true cacheable paraphrase pair scored only **0.9064**;
- only **1 of 35** same-intent query pairs retrieved identical memory ids;
- the least-bad threshold still produced a **50% false-hit rate**.

No threshold separates the positive and negative populations. Raising it drops
the true paraphrase; lowering it serves another question's cached answer. A
miss costs one vector search, while a false hit returns the wrong memory, so the
measured result is `cache_semantic_enabled=false`, not a manufactured savings
number. This does not invalidate the 0.80 write-dedup threshold: dedup compares
long stored statements, while cache keys are short queries with much less
discriminating signal.

## Implemented architecture

```
jsonl / MCP → T1 buffer → extract → cosine dedup → pgvector
                                                       ↓
                            memory card ← temporal_query → MCP client
```

- **Ingest** — incremental `jsonl` parsing split by session; FastAPI endpoints
  for sessions, preferences, and recall; one nightly batch writes the card.
- **Extraction** — Qwen2.5-3B-Instruct-4bit tuned with MLX LoRA on 198 fit rows
  (34 training-side validation rows; 45 date-isolated holdout rows), served by
  `mlx_lm.server` on the Mac and retried only after a schema failure.
- **Storage** — T1 session buffer, T2 rolling summary, T3 pgvector with time
  decay over `created_at` / `valid_at`.
- **Caching** — bounded in-process LRU plus Redis exact-key caching. Semantic
  neighbour matching is implemented but off: unrelated short questions scored
  0.9992, above the 0.9064 true paraphrase, so no tested threshold was safe.

## Developer-preview signups

`POST /api/waitlist` forwards to `WAITLIST_WEBHOOK_URL` (any endpoint accepting
a JSON body — Formspree, Resend, a Slack webhook, an Apps Script). With that
variable unset the route returns 503 and the form falls back to a pre-filled
`mailto:` draft, so an address is never silently dropped. The form carries a
honeypot field; a filled one shows success and sends nothing.

## Run locally

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000), or
[/demo](http://localhost:3000/demo) for the diary.

## Validate

```bash
npm run lint && npm run build
```

### One-command local-loop proof

For an interview or pre-release check, one command starts any missing local
services, refreshes one real day card through the private MLX adapter, and
verifies the same store through REST, the Diary route and an actual MCP stdio
client:

```bash
scripts/verify-local-loop.sh             # reuse the newest MLX-written day
scripts/verify-local-loop.sh 2026-08-08  # or name a T2 day card explicitly
```

The run refreshes that day's T2 narrative. It deliberately disables preference
writes during extraction and uses an existing real T3 row as a read-only recall
probe, so an acceptance test never becomes durable user memory. Services that
were already running stay running; services started by the script are stopped
on exit, and Postgres data is preserved.

## Design

The visual system is ported from the 1Day landing page: blue brand (`#1875ef`),
DM Sans body, Manrope headings, Nanum Pen Script accents, the same phone
mockups, and the same paper-on-ink card. The page is built around one continuous
visual — the two capture paths converging into the memory layer — rather than a
hero plus three feature blocks. All user-visible copy lives in one `copy` object
keyed by locale (`zh` / `en`); technical identifiers stay English in both.

## Stack

**Front end** — Next.js, React, TypeScript, CSS.
**Backend** — Python 3.11, FastAPI, asyncpg, Postgres 16 + pgvector, Redis,
the MCP Python SDK, Docker Compose.
**Local model** — Qwen2.5-3B-Instruct-4bit, MLX LoRA, `mlx_lm.server`.
**Roadmap, not shipped** — export to a portable CUDA/vLLM serving target if the
local pilot ever needs to run away from Apple silicon.
