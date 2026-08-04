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

The **front end is built**. The **backend is not** — it is being added in
stages, and nothing in this repo pretends otherwise:

| Piece | State |
| --- | --- |
| Landing page (`/`) — two paths, architecture, metrics table, signup | done |
| Diary (`/demo`) — daily card, memory timeline, raw T1/T2/T3 disclosure | done |
| `api/` — FastAPI ingest, three-tier store, cache | not started |
| `mcp/` — `upsert_preference`, `temporal_query` | not started |
| `train/` — dialogue-pair generation, QLoRA fine-tune | not started |
| `evals/` — the four scripts behind `results.json` | not started |

`/demo` runs on fixed sample data entirely in the browser: no model call, no
transcript read, no database. The page says so in a banner.

## Metrics policy

`evals/results.json` ships with every metric `null`, and the landing page
renders `null` as an amber **in progress** badge instead of a number. A figure
appears only once a script in `evals/` has produced it. Every number on the page
should be reproducible on request.

| Metric | Script |
| --- | --- |
| Preference-extraction JSON validity | `evals/extraction.py` |
| Extraction API cost delta (hosted vs. local vLLM) | `evals/cost_extraction.py` |
| Per-turn prompt token reduction | `evals/tokens.py` |
| Cost saved by the semantic cache | `evals/cache.py` |

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

Next.js, React, TypeScript, CSS. Planned backend: Python, FastAPI, Unsloth,
vLLM, MCP, Postgres + pgvector, Redis, Docker.
