# MindBridge learning infrastructure roadmap

Status: planned extension. MindBridge's temporal memory, local ingestion,
FastAPI/MCP service and MLX LoRA pilot exist today. The generic trajectory
dataset, GRPO/RLVR and model-registry layers below are not yet shipped.

## Product thesis

MindBridge already answers “what should an agent remember across sessions?” The
next layer answers “which experiences are safe and useful to learn from, how do
we reproduce the training run, and what evidence allows a new policy to ship?”

The extension keeps two paths separate:

- **Runtime memory:** retrieve source-linked, temporally valid context for the
  current task.
- **Learning data:** promote eligible, redacted, evaluated trajectories into a
  versioned dataset for training and offline evaluation.

A memory is not automatically a training example. Promotion is an explicit,
auditable operation.

## Inputs

| Source | Current/planned | Treatment |
| --- | --- | --- |
| Claude Code and Codex transcripts | current | existing cursor, redaction and T1/T2/T3 paths |
| CorpCheck `agent-run.v1` envelopes | planned | enterprise namespace; immutable source events |
| Human corrections/approvals | planned | join to run and step IDs, never overwrite source |
| GSM8K | scaffolded in sibling repository | public canary only; no personal or enterprise memory |

## Storage domains

```text
raw_events/          append-only, encrypted or content-addressed source events
normalized_runs/     schema-versioned agent-run envelopes
runtime_memory/      temporal T1/T2/T3 records used at inference time
dataset_manifests/   example IDs, lineage, filters, splits and licenses
run_artifacts/       config, code SHA, logs, checkpoints and metrics
model_registry/      candidate lineage, evaluation report and release state
```

Personal memories and CorpCheck enterprise runs must use separate namespaces,
retention policies and access controls. A query or training job may never join
them by default.

## Normalization contract

For every run, preserve:

- source run ID and immutable event order;
- environment, policy, tool, evaluator and model versions;
- redaction version and unresolved-sensitive-data status;
- tool inputs/results as hashes plus governed artifact references;
- evidence IDs, approvals, final-state hash and failure taxonomy;
- dataset eligibility and the human or rule that granted it.

Schema migration creates a new normalized version. It never mutates the raw
event stream.

## Dataset builder

Each dataset release needs a manifest containing:

```yaml
dataset_id: corpcheck-trajectories-v1
source_schema: agent-run.v1
builder_commit: <git-sha>
environment_versions: [<sha>]
policy_versions: [<sha>]
filters:
  redaction_status: passed
  dataset_eligible: true
split_strategy: issuer_or_period_disjoint
counts:
  train: null
  validation: null
  test: null
artifacts:
  examples_sha256: null
```

Split before near-duplicate expansion. For CorpCheck, hold out issuers or filing
periods to reduce evidence leakage. For GSM8K, preserve the official test set
and add a separately versioned perturbation set.

## Training jobs

### SFT/LoRA

- Train on successful policy-compliant traces plus human-corrected failures.
- Mask tool outputs and padding correctly; report effective tokens, truncation
  and dropped examples.
- Evaluate final task behavior, not only loss or JSON validity.

### GRPO/RLVR

- Begin only after the verifier agrees with human review on a held-out sample.
- Use group-relative comparisons for outputs from the same task/environment.
- Keep reward components visible: final state, evidence, policy, refusal,
  efficiency and format.
- Clip or gate reward on critical policy failures; a fluent violation cannot be
  rewarded into production.

### Checkpoint and recovery

- Persist optimizer/trainer state, RNG seeds, sampler position and dataset hash.
- An interrupted run resumes to an equivalent result or declares why it cannot.
- Every metric row links to code, config, hardware, dataset and checkpoint.

## GSM8K canary

The sibling [`ai-infra-gsm8k`](../../ai-infra-gsm8k) repository is the bring-up
workload for the generic training path.

Run a small Qwen model through:

1. frozen baseline inference;
2. LoRA SFT;
3. GRPO/RLVR with exact-answer reward;
4. official test plus a perturbed holdout.

Record pass@1, pass@k, answer coverage, reasoning tokens, latency, throughput,
peak memory, seed and artifact hashes. GSM8K proves the machinery can run and be
reproduced; it does not prove the agent can follow CorpCheck policy.

## Evaluation and release

| Gate | Required evidence |
| --- | --- |
| Data | manifest, lineage, license, redaction and disjoint split checks |
| Training | config, code SHA, hardware, logs, checkpoint hashes and recovery test |
| Capability | GSM8K and CorpCheck held-out comparisons against frozen baselines |
| Safety | unsupported claims, approval bypasses, unauthorized writes and refusal calibration |
| Operations | p50/p95 latency, cost, throughput, error rate and rollback drill |

Candidate stages:

```text
created -> trained -> evaluated -> rejected
                               -> shadow -> approved -> active -> rolled_back
```

Only an explicit release report changes the stage. Model files appearing in an
output directory do not constitute a release.

## Milestones and acceptance criteria

### L1 — Trace ingestion

- Add `agent-run.v1` validation, enterprise namespace and immutable source-event
  storage.
- Acceptance: golden CorpCheck runs round-trip without lost evidence, policy or
  final-state fields.

### L2 — Dataset registry

- Deterministic builder, manifests, lineage queries, redaction gate and disjoint
  split checks.
- Acceptance: any example can be traced to its run and rebuilt byte-identically
  from the same source/version set.

### L3 — GSM8K bring-up

- Connect the existing sibling harness to the shared run-artifact format and
  execute baseline, LoRA and GRPO.
- Acceptance: all comparison rows are measured, reproducible and carry no
  placeholder/null values.

### L4 — CorpCheck SFT

- Train the first domain candidate and evaluate it against the frozen task
  suite.
- Acceptance: improved held-out task success with no regression at any critical
  safety gate.

### L5 — GRPO/RLVR and reward audit

- Add verified rewards and measure agreement with human audit.
- Acceptance: reward/human disagreements are categorized and below a declared
  threshold before training; candidate clears the same release suite as SFT.

### L6 — Registry and shadow serving

- Model lineage, OpenAI-compatible or vLLM serving adapter, shadow routing,
  monitoring and rollback.
- Acceptance: replay plus shadow report passes capability, policy, latency and
  cost budgets; rollback drill succeeds.

## Non-goals for this cycle

- Rebranding schema compliance as semantic extraction quality.
- Mixing private personal memory with enterprise training data.
- Building a general-purpose distributed training platform before one-machine
  artifact lineage and recovery are correct.
- Treating GSM8K performance as the product moat.
- Automatically promoting a model because aggregate reward increased.
