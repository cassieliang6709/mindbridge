"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowClockwise, Check, CircleNotch, Moon, X } from "@phosphor-icons/react";
import styles from "./night-shift.module.css";

type Job = {
  job_id: string;
  kind: "extract_card" | "retrieval_eval";
  status: "queued" | "running" | "retrying" | "succeeded" | "failed";
  attempt: number;
  max_attempts: number;
  error_message: string | null;
  created_at: string;
};

type Candidate = {
  id: number;
  content: string;
  category: string;
  project: string | null;
  confidence: number;
  evidence: string | null;
  source_period: string;
};

type Dashboard = {
  jobs: Job[];
  memory_candidates: Candidate[];
  latest_eval: null | {
    generated_at: string;
    metrics: {
      confirmed_cases: number;
      precision_at_k: number | null;
      mrr: number | null;
    };
  };
};

async function requestJson<T>(path = "", body?: object): Promise<T> {
  const response = await fetch(`/api/night-shift${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? `request failed (${response.status})`);
  return payload;
}

function percent(value: number | null) {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function NightShiftConsole() {
  const [state, setState] = useState<Dashboard | null>(null);
  const [busy, setBusy] = useState<string | null>("load");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setState(await requestJson<Dashboard>());
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load Night Shift");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    async function initialLoad() {
      try {
        setState(await requestJson<Dashboard>());
        setError(null);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not load Night Shift");
      } finally {
        setBusy(null);
      }
    }
    void initialLoad();
  }, []);

  async function run() {
    setBusy("run");
    try {
      await requestJson("/run", { extract_missing: true, run_retrieval_eval: true, limit: 3 });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start Night Shift");
      setBusy(null);
    }
  }

  async function resolve(candidate: Candidate, decision: "confirm" | "reject") {
    setBusy(`candidate-${candidate.id}`);
    try {
      await requestJson(`/candidates/${candidate.id}/resolve`, { decision });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save decision");
      setBusy(null);
    }
  }

  async function retry(jobId: string) {
    setBusy(`job-${jobId}`);
    try {
      await requestJson(`/jobs/${jobId}/retry`, {});
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not retry job");
      setBusy(null);
    }
  }

  const metrics = state?.latest_eval?.metrics;
  const active = state?.jobs.filter((job) => ["queued", "running", "retrying"].includes(job.status)).length ?? 0;

  return (
    <main className={styles.page}>
      <nav className={styles.nav}>
        <Link href="/runtime"><ArrowLeft /> Agent</Link>
        <span>MindBridge / Night Shift</span>
      </nav>

      <section className={styles.shell}>
        <header className={styles.header}>
          <div><p>LOCAL BACKGROUND MEMORY</p><h1>Night Shift.</h1><span>Slow work runs off the chat path. Nothing enters long-term memory without review.</span></div>
          <button type="button" onClick={run} disabled={busy !== null}>
            {busy === "run" ? <CircleNotch className={styles.spinner} /> : <Moon />}
            Run now
          </button>
        </header>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.metrics}>
          <div><b>{active}</b><span>active jobs</span></div>
          <div><b>{state?.memory_candidates.length ?? 0}</b><span>in inbox</span></div>
          <div><b>{percent(metrics?.precision_at_k ?? null)}</b><span>Precision@K</span></div>
          <div><b>{percent(metrics?.mrr ?? null)}</b><span>MRR</span></div>
        </div>

        <section className={styles.section}>
          <div className={styles.sectionTitle}><div><p>MEMORY INBOX</p><h2>Review before recall.</h2></div><span>{state?.memory_candidates.length ?? 0} pending</span></div>
          <div className={styles.inbox}>
            {state?.memory_candidates.map((candidate) => (
              <article key={candidate.id}>
                <div className={styles.candidateMeta}><span>{candidate.category}</span><span>{candidate.source_period}</span><b>{Math.round(candidate.confidence * 100)}%</b></div>
                <p>{candidate.content}</p>
                {candidate.evidence && <blockquote>{candidate.evidence}</blockquote>}
                {candidate.project && <small>project · {candidate.project}</small>}
                <footer>
                  <button onClick={() => resolve(candidate, "confirm")} disabled={busy !== null}><Check /> Keep</button>
                  <button onClick={() => resolve(candidate, "reject")} disabled={busy !== null}><X /> Reject</button>
                </footer>
              </article>
            ))}
            {state && state.memory_candidates.length === 0 && <p className={styles.empty}>No proposed memories waiting for you.</p>}
          </div>
        </section>

        <section className={styles.section}>
          <div className={styles.sectionTitle}><div><p>JOB RECEIPTS</p><h2>What ran while you were away.</h2></div><button className={styles.refresh} onClick={load} disabled={busy !== null}><ArrowClockwise /></button></div>
          <ol className={styles.jobs}>
            {state?.jobs.slice(0, 12).map((job) => (
              <li key={job.job_id}>
                <i data-status={job.status} />
                <div><b>{job.kind === "extract_card" ? "Memory extraction" : "Retrieval replay"}</b><span>{job.status} · attempt {job.attempt}/{job.max_attempts}</span>{job.error_message && <small>{job.error_message}</small>}</div>
                <div className={styles.jobAction}>
                  <code>{job.job_id.slice(-8)}</code>
                  {job.status === "failed" && <button onClick={() => retry(job.job_id)} disabled={busy !== null}>Retry</button>}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </section>
    </main>
  );
}
