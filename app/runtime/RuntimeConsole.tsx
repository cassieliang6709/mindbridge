"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { ArrowLeft, ArrowUp, CircleNotch, Moon, Plus } from "@phosphor-icons/react";
import styles from "./runtime.module.css";

type RuntimeEvent = {
  sequence: number;
  event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  payload_sha256: string;
  created_at: string;
};

type Snapshot = {
  run: {
    run_id: string;
    task: string;
    status: "running" | "completed" | "failed";
    model: string | null;
  };
  events: RuntimeEvent[];
  pending_tool_calls: string[];
};

type AgentResponse = {
  answer: string;
  selected_tool: string | null;
  hit_count: number;
  planner: string;
  snapshot: Snapshot;
};

const LAST_RUN_KEY = "mindbridge:last-agent-run";

const labels: Record<string, string> = {
  run_input: "Task received",
  model_requested: "Model called",
  model_completed: "Model replied",
  tool_requested: "Tool selected",
  tool_started: "Tool started",
  tool_completed: "Tool returned",
  tool_failed: "Tool failed",
  run_completed: "Run completed",
  run_failed: "Run failed",
};

async function requestJson<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/runtime${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail ?? `request failed (${response.status})`);
  }
  return payload;
}

function outcome(snapshot: Snapshot): {
  answer: string;
  selectedTool: string | null;
  hitCount: number;
} | null {
  const terminal = [...snapshot.events]
    .reverse()
    .find((event) => event.event_type === "run_completed");
  const value = terminal?.payload.outcome as Record<string, unknown> | undefined;
  if (!value || typeof value.answer !== "string") return null;
  return {
    answer: value.answer,
    selectedTool:
      typeof value.selected_tool === "string" ? value.selected_tool : null,
    hitCount: typeof value.hit_count === "number" ? value.hit_count : 0,
  };
}

export function RuntimeConsole() {
  const [task, setTask] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [selectedTool, setSelectedTool] = useState<string | null>(null);
  const [hitCount, setHitCount] = useState(0);
  const [planner, setPlanner] = useState("ollama:qwen2.5:7b");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function restoreLastRun() {
      const runId = window.localStorage.getItem(LAST_RUN_KEY);
      if (!runId) return;
      try {
        const restored = await requestJson<Snapshot>(
          `/${encodeURIComponent(runId)}`,
        );
        const stored = outcome(restored);
        if (!stored) return;
        setTask(restored.run.task);
        setAnswer(stored.answer);
        setSelectedTool(stored.selectedTool);
        setHitCount(stored.hitCount);
        setSnapshot(restored);
        setPlanner(`ollama:${restored.run.model ?? "qwen2.5:7b"}`);
      } catch {
        window.localStorage.removeItem(LAST_RUN_KEY);
      }
    }
    void restoreLastRun().finally(() => setRestoring(false));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = task.trim();
    if (!prompt || busy) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    setSnapshot(null);
    try {
      const response = await requestJson<AgentResponse>("/auto", { task: prompt });
      setAnswer(response.answer);
      setSelectedTool(response.selected_tool);
      setHitCount(response.hit_count);
      setPlanner(response.planner);
      setSnapshot(response.snapshot);
      window.localStorage.setItem(LAST_RUN_KEY, response.snapshot.run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent run failed");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setTask("");
    setAnswer(null);
    setSelectedTool(null);
    setHitCount(0);
    setSnapshot(null);
    setError(null);
    window.localStorage.removeItem(LAST_RUN_KEY);
  }

  return (
    <main className={styles.page}>
      <nav className={styles.nav}>
        <Link href="/" className={styles.brand}>
          <span /> MindBridge
        </Link>
        <div className={styles.navLinks}>
          <Link href="/runtime/night-shift" className={styles.back}><Moon /> Night Shift</Link>
          <Link href="/demo" className={styles.back}><ArrowLeft /> Memory diary</Link>
        </div>
      </nav>

      <section className={styles.shell}>
        <header className={styles.intro}>
          <p>LOCAL MEMORY AGENT</p>
          <h1>Ask your memory.</h1>
          <span>One local model. One read-only tool. Every step recoverable.</span>
        </header>

        {restoring ? (
          <div className={styles.loading}>
            <CircleNotch className={styles.spinner} /> Restoring the last run…
          </div>
        ) : (
          <>
            <form className={styles.composer} onSubmit={submit}>
              <textarea
                value={task}
                onChange={(event) => setTask(event.target.value)}
                placeholder="What have I learned about writing project experience?"
                aria-label="Ask MindBridge"
                rows={3}
                disabled={busy}
              />
              <button type="submit" aria-label="Run agent" disabled={!task.trim() || busy}>
                {busy ? <CircleNotch className={styles.spinner} /> : <ArrowUp weight="bold" />}
              </button>
            </form>

            {busy && <p className={styles.status}>Reading tools, searching memory, then answering…</p>}
            {error && <p className={styles.error}>{error}</p>}

            {answer && snapshot && (
              <article className={styles.answer}>
                <p>{answer}</p>
                <footer>
                  <span>{planner.replace("ollama:", "")}</span>
                  <i />
                  <span>{selectedTool ?? "no tool"}</span>
                  <i />
                  <span>{hitCount} memories</span>
                  <button type="button" onClick={reset}><Plus /> New question</button>
                </footer>
              </article>
            )}

            {snapshot && (
              <details className={styles.details}>
                <summary>Developer details · {snapshot.events.length} durable events</summary>
                <Link href="/runtime/evals" className={styles.evalLink}>Open retrieval evals →</Link>
                <div className={styles.runMeta}>
                  <span>run</span><code>{snapshot.run.run_id}</code>
                  <span>status</span><code>{snapshot.run.status}</code>
                  <span>pending</span><code>{snapshot.pending_tool_calls.length}</code>
                </div>
                <ol>
                  {snapshot.events.map((event) => (
                    <li key={event.event_id}>
                      <b>{String(event.sequence).padStart(2, "0")}</b>
                      <div>
                        <strong>{labels[event.event_type] ?? event.event_type}</strong>
                        <small>{event.event_type}</small>
                        <details>
                          <summary>payload</summary>
                          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                        </details>
                      </div>
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </>
        )}
      </section>
    </main>
  );
}
