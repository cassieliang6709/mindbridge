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
| Path A `jsonl` parser — reads Claude Code / Codex CLI transcripts | not started |
| `train/` — dialogue-pair generation, QLoRA fine-tune (M2) | not started |
| Semantic cache with threshold matching (M5) | not started |

Two gaps worth naming precisely. **The diary at `/demo` is not yet wired to the
API** — it still runs on fixed sample data in the browser, and says so in a
banner. **Nothing ingests transcripts yet**: the engine stores and recalls what
it is given, but the Path A parser that would read
`~/.claude/projects/**/*.jsonl` is not written, so today memory arrives only
through Path B (an MCP client calling `upsert_preference`).

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
| `POST /memories/query` | T3 | time-decayed top-K recall |

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
