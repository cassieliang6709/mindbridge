# MindBridge interview demo

## Links

- English: https://mindbridge.liangyue.site/interview-demo
- 中文: https://mindbridge.liangyue.site/interview-demo/zh
- Source: https://github.com/cassieliang6709/mindbridge

## 60-second path

1. “MindBridge is a reflective AI companion, but the technical moat is its
   transparent temporal Memory Core.”
2. Click **Day 02**: uncertainty is surfaced as a candidate and is not written
   as user truth.
3. Click **Day 03**: explicit confirmation writes the new direction and closes
   the old identity without deleting history.
4. Click **What changed over time?**: the answer cites both the current memory
   and the superseded record.
5. Click **What should we build next?**: the recommendation is visibly labelled
   “system suggestion · not a user fact.”

## What this proves

- Memory admission is different from indiscriminate transcript storage.
- Change is represented with temporal validity, not destructive overwrite.
- Answers expose a Memory Receipt: source, date, score and current state.
- System inference is separated from user-confirmed fact.
- The public scenario is synthetic; no personal transcripts or credentials are
  shipped.

## Honest boundary

The browser demo is deterministic and uses fixed synthetic fixtures. The
repository implements the real T1/T2/T3 store, time-decay retrieval,
supersession, REST/MCP boundaries, local ingestion and MLX extraction path. The
deployed page is not connected to Cassie's laptop or private database.

## Local fallback

```bash
npm install
npm run dev
# open http://localhost:3000/interview-demo
```

For the backend acceptance loop:

```bash
scripts/verify-local-loop.sh
```
