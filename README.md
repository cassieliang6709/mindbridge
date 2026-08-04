# MindBridge

A long-term memory engine for LLMs, packaged as something a person actually
uses: it parses the transcripts your local AI coding tools already write, turns
each day into one memory card, and serves the same store to any MCP client.
Nothing leaves your machine.

[Live site](https://mindbridge-demo-psi.vercel.app) ·
[Diary](https://mindbridge-demo-psi.vercel.app/demo)

## Two capture paths

**Path A — passive log parsing.** Nothing for you to do. A background pass reads
structured logs already on disk and extracts what happened that day. This only
works for tools that write such logs:

- `~/.claude/projects/**/*.jsonl` (Claude Code)
- `~/.codex/archived_sessions/` (Codex CLI)

**Path B — active MCP read/write.** Mounted as a standard MCP server exposing
`upsert_preference` and `temporal_query`, so the model decides mid-conversation
when to write and when to recall. Works with any MCP client — Claude Desktop,
Claude Code, Cursor, VS Code.

ChatGPT and web Claude expose no history API; they need a manual export and are
out of scope for the automatic path. The landing page states this rather than
implying full coverage.

## Three memory tiers

| Tier | Holds | Backed by |
| --- | --- | --- |
| T1 | The day's raw turns | session buffer, in process |
| T2 | One structured card per day | rolling summary |
| T3 | Long-term preferences, time-decayed | Postgres + pgvector |

Every T3 record carries `created_at` and `valid_at`, so a superseded preference
decays out of recall instead of being silently overwritten. Writes run a
cosine-similarity check first, so one preference stays one row.

T1 also stores the project, git branch and tool names of each turn. That is what
lets a day card be rebuilt over the whole day from Postgres: building it from
only the turns an incremental run happened to parse would rewrite a full card
with a partial one, so a nightly job would shrink every card it touched.

## Current state

The front end and the M1/M3 backend are built. Ingestion and the local
extractor are not. Nothing in this repo pretends otherwise:

| Piece | State |
| --- | --- |
| Landing page (`/`) — two paths, architecture, metrics table, signup | done |
| Diary (`/demo`) — daily card, memory timeline, raw T1/T2/T3 disclosure | done |
| `api/` — FastAPI, three tiers, decay retrieval, dedup, query cache | done (M1) |
| `mcp_server/` — `upsert_preference`, `temporal_query` over stdio | done (M3) |
| `evals/eval_memory_engine.py` — decay, dedup and token benchmarks | done |
| `ingest/` — Path A readers for Claude Code and Codex CLI | done |
| `/demo` wired to the API, with an offline fallback | done |
| Nightly scheduler for Path A (launchd, opt-in) | done |
| `train/` — dialogue-pair generation, QLoRA fine-tune (M2) | not started |
| Semantic cache with threshold matching (M5) | not started |

The diary at `/demo` now reads the backend through `/api/diary`. When the API is
reachable it renders real cards, real T1 turns and the real T3 timeline, and the
banner turns green. When it is not — which is the case on the deployed site,
since the backend runs on one laptop — it falls back to sample data, turns the
banner amber, and names the unreachable URL. The distinction is carried in the
payload's `source` field rather than inferred from a failed request, so sample
rows can never be presented as if they came from Postgres.

A limit is by design: Path A produces **rule-based** day cards — counts,
tool tallies, time spans, git branches. It does not narrate a day in prose or
extract durable preferences from it, because both need the M2 local extractor.
Preferences therefore still arrive only through Path B, i.e. a model deciding to
call `upsert_preference`.

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
      "cwd": "/absolute/path/to/mindbridge-demo",
      "env": {
        "MINDBRIDGE_DATABASE_URL": "postgresql://mindbridge:mindbridge@localhost:5433/mindbridge"
      }
    }
  }
}
```

Both transports call the same `MemoryService`, so `upsert_preference` over MCP
and `POST /memories` over HTTP cannot drift apart.

| Endpoint | Tier | Purpose |
| --- | --- | --- |
| `POST /sessions/{id}/turns` · `GET /sessions/{id}/buffer` | T1 | append and read the raw window |
| `POST /summaries` · `GET /summaries` | T2 | write and list period cards |
| `POST /memories` | T3 | dedup-then-write a preference |
| `GET /memories` | T3 | newest-first listing with decay weight (no cosine, no access bump) |
| `GET /turns?start=&end=` | T1 | raw turns in a range; the caller owns the timezone |
| `POST /memories/query` | T3 | time-decayed top-K recall |

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

Stage one uses a hosted provider. It can call OpenAI/Gemini with an API key, or
reuse an existing Claude Code sign-in without copying a key into MindBridge.
Stage two replaces it with a fine-tuned model running locally; the code is
written, the fine-tune is not run.

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

# cumulative schema-compliance figures
docker compose run --rm extract --stats

# offline tests of the schema and repair loop — no key, no network
docker compose run --rm --no-deps --entrypoint python extract \
    -m extract.test_pipeline
```

**This step sends data off the machine.** Path A and Path B are local; hosted
extraction is not. It transmits that day's transcript excerpts, so it is gated
behind `--send-to-provider` and refuses to run without it. `--dry-run` prints
byte-for-byte what would be sent. Stage two removes the exposure by serving the
tuned model locally.

Compliance is reported two ways and the difference matters: `first_attempt_rate`
is how often the model returned a schema-valid object *without* correction, and
`eventual_rate` includes the repair loop. Only the first is comparable to the
tuned model, so only the first is the number to quote.

Validation is not decorative. `DiaryDraft` forbids extra keys, rejects any
narrative that infers an emotional state ("you seemed frustrated"), and rejects
a one-off task masquerading as a durable preference — a model drifts into all
three, and a prompt alone does not stop it.

### Stage two

```bash
python -m train.prepare_dataset --report      # readiness and the split
python -m train.train_qlora --epochs 2        # on a rented CUDA GPU, not a Mac
python -m train.eval_holdout --model ... --write-results
```

The split is by date and deterministic, so a day's pairs never straddle
train/holdout. `eval_holdout.py` refuses to publish a compliance rate computed
on fewer than 30 holdout days, because a rate over a handful of days has an
error bar wider than the number and it would land on a public page.

## Metrics policy

The landing page reads `evals/results.json` and renders any `null` as an amber
**in progress** badge instead of a number. A figure appears only once
`evals/eval_memory_engine.py` has produced it against a live database, so every
number on the page is reproducible with one command.

Measurements split by whether they depend on embedding quality. The two
mechanical invariants publish from any run; the rest stay null until a real
semantic provider is configured.

| Metric | Depends on embedder | Script |
| --- | --- | --- |
| Time-decay scoring correctness | no | `evals/eval_memory_engine.py` |
| Superseded-record isolation | no | `evals/eval_memory_engine.py` |
| Write-time dedup accuracy | yes | `evals/eval_memory_engine.py` |
| Per-turn prompt token reduction | yes | `evals/eval_memory_engine.py` |
| Preference-extraction JSON validity | yes | not written (M2) |
| Extraction API cost delta | yes | not written (M2) |
| Cost saved by the semantic cache | yes | not written (M5) |

"Time-decay scoring correctness" stores identical content at several ages and
checks both newest-first ordering and that every score matches
`cosine · exp(-λ·Δt)` to within 1e-6. It is a formula check, not a claim about
retrieval quality — the table on the site says so too.

## Planned architecture

```
jsonl / MCP → T1 buffer → extract → cosine dedup → pgvector
                                                       ↓
                            memory card ← temporal_query → MCP client
```

- **Ingest** — incremental `jsonl` parsing split by session; FastAPI endpoints
  for sessions, preferences, and recall; one nightly batch writes the card.
- **Extraction** — Qwen2.5-7B tuned with QLoRA (Unsloth) on ~1k dialogue→JSON
  pairs, served from local vLLM, retried on schema failure.
- **Storage** — T1 session buffer, T2 rolling summary, T3 pgvector with time
  decay over `created_at` / `valid_at`.
- **Caching** — in-process LRU plus a Redis semantic cache, thresholds tuned on
  a test set to avoid false hits.

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
**Planned** — Unsloth/QLoRA and vLLM for the local extractor (M2).
