"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowClockwise, CircleNotch } from "@phosphor-icons/react";
import styles from "./retrieval-evals.module.css";

type Judgement = {
  status: "draft" | "confirmed";
  judged_memory_ids: number[];
  relevant_memory_ids: number[];
  labelled_by: string | null;
};

type EvalCase = {
  case_id: string;
  query: string;
  provenance: { source_ref: string };
  judgement: Judgement;
};

type Hit = {
  memory_id: number;
  content: string;
  score: number;
  cosine_similarity: number;
};

type CaseResult = {
  case_id: string;
  hits: Hit[];
};

type EvalResult = {
  generated_at: string;
  retrieval: {
    implementation: string;
    embedder: string;
    corpus_size: number;
  };
  metrics: {
    total_cases: number;
    confirmed_cases: number;
    draft_cases: number;
    judgement_coverage_at_k: number | null;
    hit_rate_at_k: number | null;
    precision_at_k: number | null;
    mrr: number | null;
  };
  cases: CaseResult[];
};

type EvalState = {
  dataset: { cases: EvalCase[] };
  dataset_sha256: string;
  latest: EvalResult | null;
};

async function requestJson<T>(path = "", body?: object): Promise<T> {
  const response = await fetch(`/api/retrieval-evals${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? `request failed (${response.status})`);
  return payload;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function RetrievalEvalConsole() {
  const [state, setState] = useState<EvalState | null>(null);
  const [selected, setSelected] = useState<Record<string, number[]>>({});
  const [busy, setBusy] = useState<string | null>("load");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const next = await requestJson<EvalState>();
      setState(next);
      setSelected(
        Object.fromEntries(
          next.dataset.cases.map((item) => [
            item.case_id,
            item.judgement.relevant_memory_ids,
          ]),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load evals");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    async function initialLoad() {
      try {
        const next = await requestJson<EvalState>();
        setState(next);
        setSelected(
          Object.fromEntries(
            next.dataset.cases.map((item) => [
              item.case_id,
              item.judgement.relevant_memory_ids,
            ]),
          ),
        );
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not load evals");
      } finally {
        setBusy(null);
      }
    }
    void initialLoad();
  }, []);

  function toggle(caseId: string, memoryId: number) {
    setSelected((current) => {
      const values = new Set(current[caseId] ?? []);
      if (values.has(memoryId)) values.delete(memoryId);
      else values.add(memoryId);
      return { ...current, [caseId]: [...values].sort((a, b) => a - b) };
    });
  }

  async function runBaseline() {
    setBusy("run");
    setError(null);
    try {
      await requestJson<EvalResult>("/run", {});
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Baseline failed");
      setBusy(null);
    }
  }

  async function confirm(caseId: string, relevantMemoryIds: number[]) {
    setBusy(caseId);
    setError(null);
    try {
      const next = await requestJson<EvalState>(`/${encodeURIComponent(caseId)}/confirm`, {
        relevant_memory_ids: relevantMemoryIds,
      });
      setState(next);
      setSelected((current) => ({
        ...current,
        [caseId]: relevantMemoryIds,
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save label");
    } finally {
      setBusy(null);
    }
  }

  const latestByCase = new Map(state?.latest?.cases.map((item) => [item.case_id, item]));
  const metrics = state?.latest?.metrics;

  return (
    <main className={styles.page}>
      <nav className={styles.nav}>
        <Link href="/runtime" className={styles.back}><ArrowLeft /> Agent</Link>
        <span>MindBridge / Retrieval lab</span>
      </nav>

      <section className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p>DEVELOPER EVAL</p>
            <h1>Retrieval lab.</h1>
            <span>Confirm relevance first. Change ranking second.</span>
          </div>
          <button type="button" onClick={runBaseline} disabled={busy !== null}>
            {busy === "run" ? <CircleNotch className={styles.spinner} /> : <ArrowClockwise />}
            Run baseline
          </button>
        </header>

        {error && <p className={styles.error}>{error}</p>}

        {state?.latest && (
          <div className={styles.metrics}>
            <div><b>{metrics?.confirmed_cases ?? 0}</b><span>confirmed</span></div>
            <div><b>{metrics?.draft_cases ?? 0}</b><span>draft</span></div>
            <div><b>{percent(metrics?.precision_at_k ?? null)}</b><span>Precision@K</span></div>
            <div><b>{percent(metrics?.mrr ?? null)}</b><span>MRR</span></div>
            <small>{state.latest.retrieval.embedder} · {state.latest.retrieval.corpus_size} memories · {percent(metrics?.judgement_coverage_at_k ?? null)} label coverage · access counts untouched</small>
          </div>
        )}

        {busy === "load" && <p className={styles.loading}><CircleNotch className={styles.spinner} /> Loading cases…</p>}

        <div className={styles.cases}>
          {state?.dataset.cases.map((item, index) => {
            const current = latestByCase.get(item.case_id);
            const values = selected[item.case_id] ?? [];
            return (
              <article className={styles.case} key={item.case_id}>
                <header>
                  <div><span>{String(index + 1).padStart(2, "0")}</span><h2>{item.query}</h2></div>
                  <em data-status={item.judgement.status}>{item.judgement.status}</em>
                </header>
                <p className={styles.source}>source · {item.provenance.source_ref}</p>

                <div className={styles.hits}>
                  {current?.hits.map((hit) => (
                    <label key={hit.memory_id}>
                      <input
                        type="checkbox"
                        checked={values.includes(hit.memory_id)}
                        onChange={() => toggle(item.case_id, hit.memory_id)}
                      />
                      <span className={styles.check} />
                      <div>
                        <b>#{hit.memory_id}</b>
                        <p>{hit.content}</p>
                      </div>
                      <code>{hit.score.toFixed(3)}</code>
                    </label>
                  )) ?? <p className={styles.empty}>Run the baseline to capture candidates.</p>}
                </div>

                <footer>
                  <button
                    type="button"
                    disabled={busy !== null || values.length === 0}
                    onClick={() => confirm(item.case_id, values)}
                  >
                    {busy === item.case_id ? "Saving…" : `Confirm ${values.length || "selected"}`}
                  </button>
                  <button
                    type="button"
                    className={styles.secondary}
                    disabled={busy !== null}
                    onClick={() => confirm(item.case_id, [])}
                  >
                    None of these are relevant
                  </button>
                </footer>
              </article>
            );
          })}
        </div>

        {state && <p className={styles.receipt}>dataset · {state.dataset_sha256.slice(0, 16)}</p>}
      </section>
    </main>
  );
}
