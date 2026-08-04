"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CaretRight,
  Check,
  Database,
  GithubLogo,
  Moon,
  Warning,
} from "@phosphor-icons/react";
import { DEFAULT_LOCALE, GITHUB_URL, type Locale } from "../site";
import type { DiaryCard, DiaryMemory, DiaryTurn } from "../api/diary/route";
import { SAMPLE_DAYS, SAMPLE_MEMORIES, type SampleDay } from "./sample";

/* ---------------------------------------------------------------------------
   The diary reads the FastAPI backend through /api/diary. When the backend is
   not running — which is the case on the deployed site, since it lives on this
   machine — it falls back to the sample data and says so. The distinction is
   carried in the payload's `source`, never inferred, so sample rows can never
   be presented as if they came from Postgres.
--------------------------------------------------------------------------- */

type Mode = "loading" | "live" | "offline";

type DiaryPayload =
  | {
      source: "live";
      selected: string | null;
      cards: DiaryCard[];
      memories: DiaryMemory[];
      turns: DiaryTurn[];
      turnsTotal: number;
    }
  | { source: "offline"; apiBase: string; reason: string };

const copy = {
  zh: {
    back: "返回首页",
    eyebrowLive: "日记界面 · 真实数据",
    eyebrowSample: "日记界面 · 示例数据",
    eyebrowLoading: "日记界面 · 读取中",
    title: "MindBridge 记忆日记",
    lede: "每天一张卡，由当天的 AI 对话自动写成。你不用动手，也可以随时展开看它是从哪几条原始记录来的。",
    liveBanner: (cards: number, turns: number, memories: number, prose: number) =>
      `真实数据：${cards} 张日记卡由 Path A 从本机 transcript 解析生成，T1 有 ${turns} 轮记录、T3 有 ${memories} 条长期偏好。` +
      (prose > 0
        ? `其中 ${prose} 张已由 M2 写成散文（卡片上标了模型名，规则计数仍保留在下方）；其余是规则计数。`
        : "卡片内容是规则算出来的计数，不是模型写的散文 —— 叙述与偏好抽取要等 M2。"),
    offlineBanner: (base: string) =>
      `连不上后端（${base}），下面显示的是固定示例数据。本机跑 docker compose up -d api 之后刷新即可看到真实卡片。`,
    loadingBanner: "正在读取后端…",
    book: "预约真机演示",
    dayTitle: "日期",
    cardMetaRule: "Path A 计数",
    cardMetaModel: (model: string) => `${model} 撰写`,
    ruleBadge: "规则计数 · 可复现",
    modelBadge: "模型撰写 · 已过 schema 校验",
    threads: "还没做完的",
    cardMetaSample: "示例",
    items: "今天发生了什么",
    wrote: "当天写入的偏好",
    noWrote: "当天没有新偏好写入 —— 偏好靠 MCP 客户端调用 upsert_preference 写进来，Path A 只算事实。",
    behaviour: "行为观察",
    behaviourNote: "只陈述可观测的行为，不做情绪判断。",
    timeline: "记忆时间轴",
    timelineNote: "点上面的偏好标签可以定位到对应记录",
    emptyMemories: "T3 还没有长期偏好。Path A 不产出偏好，需要 MCP 客户端写入或等 M2 抽取。",
    stale: "已被取代",
    open: "valid_at 未关闭",
    underneath: "看底层",
    underneathMeta: "T1 / T2 / T3 原始状态",
    t1: "T1 会话缓冲区 — 卡片依据的原始轮次",
    t2: "T2 滚动摘要 — 存进数据库的结构化事实",
    t3: "T3 pgvector — 截至这一天的长期记忆",
    t1More: (shown: number, total: number) =>
      `显示 ${shown} / ${total} 轮（其余略去）`,
    batch: "当天统计",
    batchTurns: "T1 现存轮次（当日）",
    batchFacts: "卡片里的结构化事实",
    batchMemories: "截至当日的 T3 记忆",
    source: "源码",
    factsNote: "事实文本由后端逐字返回，未翻译。",
  },
  en: {
    back: "Back to home",
    eyebrowLive: "Diary · live data",
    eyebrowSample: "Diary · sample data",
    eyebrowLoading: "Diary · loading",
    title: "MindBridge memory diary",
    lede: "One card a day, written from that day's AI conversations. Nothing for you to do — and you can always open it up to see which raw records it came from.",
    liveBanner: (cards: number, turns: number, memories: number, prose: number) =>
      `Live data: ${cards} day card(s) written by Path A from transcripts on this machine, ${turns} turns in T1 and ${memories} long-term preference(s) in T3. ` +
      (prose > 0
        ? `${prose} of them have M2 prose (the card names the model, and the rule-based count stays below it); the rest are counts.`
        : "Card contents are rule-based counts, not model-written prose — narration and preference extraction wait for M2."),
    offlineBanner: (base: string) =>
      `Backend unreachable at ${base}, so this is fixed sample data. Run docker compose up -d api locally and reload to see the real cards.`,
    loadingBanner: "Reading the backend…",
    book: "Book a real walkthrough",
    dayTitle: "Day",
    cardMetaRule: "counted by Path A",
    cardMetaModel: (model: string) => `written by ${model}`,
    ruleBadge: "rule-based counts · reproducible",
    modelBadge: "model-written · schema-validated",
    threads: "Still open",
    cardMetaSample: "sample",
    items: "What happened",
    wrote: "Preferences written that day",
    noWrote:
      "No preference written that day — those arrive when an MCP client calls upsert_preference. Path A only computes facts.",
    behaviour: "Behavioural note",
    behaviourNote: "Observable behaviour only — no emotional inference.",
    timeline: "Memory timeline",
    timelineNote: "Tap a preference above to locate its record",
    emptyMemories:
      "T3 holds no long-term preferences yet. Path A does not produce them; they need an MCP client write, or M2 extraction.",
    stale: "superseded",
    open: "valid_at open",
    underneath: "Look underneath",
    underneathMeta: "raw T1 / T2 / T3 state",
    t1: "T1 session buffer — the raw turns behind this card",
    t2: "T2 rolling summary — the structured facts as stored",
    t3: "T3 pgvector — long-term memory as of this day",
    t1More: (shown: number, total: number) =>
      `showing ${shown} of ${total} turns`,
    batch: "That day",
    batchTurns: "turns now in T1 (that day)",
    batchFacts: "structured facts on the card",
    batchMemories: "T3 memories as of that day",
    source: "Source",
    factsNote: "Fact text is returned verbatim by the backend, untranslated.",
  },
};

/** Local calendar date of an ISO timestamp, matching the day-card boundary. */
function localDate(iso: string): string {
  const date = new Date(iso);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function weekday(date: string, locale: Locale): string {
  const parsed = new Date(`${date}T12:00:00`);
  return parsed.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
    weekday: "short",
  });
}

export function MemoryConsole() {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);
  const [mode, setMode] = useState<Mode>("loading");
  const [apiBase, setApiBase] = useState("");
  const [cards, setCards] = useState<DiaryCard[]>([]);
  const [memories, setMemories] = useState<DiaryMemory[]>([]);
  const [turns, setTurns] = useState<DiaryTurn[]>([]);
  const [turnsTotal, setTurnsTotal] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [sampleIndex, setSampleIndex] = useState(0);
  const [activeMemory, setActiveMemory] = useState<number | string | null>(null);
  const t = copy[locale];
  const zh = locale === "zh";

  const fetchDiary = useCallback(
    async (date: string | null): Promise<DiaryPayload> => {
      // getTimezoneOffset() is what the backend needs to turn a local date into
      // the matching UTC instant, so the day boundary is the browser's, not the
      // server's.
      const query = new URLSearchParams({
        offset: String(new Date().getTimezoneOffset()),
      });
      if (date) query.set("date", date);
      const response = await fetch(`/api/diary?${query}`, { cache: "no-store" });
      return (await response.json()) as DiaryPayload;
    },
    [],
  );

  const apply = useCallback((payload: DiaryPayload) => {
    if (payload.source === "offline") {
      setApiBase(payload.apiBase);
      setMode("offline");
      return;
    }
    setCards(payload.cards);
    setMemories(payload.memories);
    setTurns(payload.turns);
    setTurnsTotal(payload.turnsTotal);
    setSelected(payload.selected);
    setMode("live");
  }, []);

  useEffect(() => {
    // The flag keeps a slow response from writing state after unmount, which
    // matters here because switching day refetches.
    let cancelled = false;
    (async () => {
      try {
        const payload = await fetchDiary(null);
        if (!cancelled) apply(payload);
      } catch {
        if (!cancelled) setMode("offline");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apply, fetchDiary]);

  const selectDay = useCallback(
    async (date: string) => {
      try {
        apply(await fetchDiary(date));
      } catch {
        setMode("offline");
      }
    },
    [apply, fetchDiary],
  );

  const live = mode === "live";
  const sampleDay: SampleDay = SAMPLE_DAYS[sampleIndex];
  const card = live ? cards.find((entry) => entry.period === selected) : undefined;

  // Memories created on the shown day, matched on the same local-date boundary
  // the cards use.
  const wroteThatDay = useMemo(() => {
    if (!live || !selected) return [];
    return memories.filter((memory) => localDate(memory.created_at) === selected);
  }, [live, memories, selected]);

  // The timeline should show memory as it stood on that day, so anything
  // learned later is excluded rather than shown as if it already existed.
  const visibleMemories = useMemo(() => {
    if (!live) return [];
    if (!selected) return memories;
    return memories.filter((memory) => localDate(memory.created_at) <= selected);
  }, [live, memories, selected]);

  const dayList = live
    ? cards.map((entry) => ({
        key: entry.period,
        date: entry.period,
        label: entry.summary,
      }))
    : SAMPLE_DAYS.map((day, index) => ({
        key: String(index),
        date: day.date,
        label: zh ? day.zhSummary : day.enSummary,
      }));

  const activeKey = live ? selected : String(sampleIndex);

  return (
    <main className="console-page" lang={zh ? "zh-CN" : "en"}>
      <header className="nav shell">
        <Link href="/" className="brand" aria-label="MindBridge">
          <span className="brand-mark" />
          MindBridge
        </Link>
        <nav>
          <Link className="text-link" href="/">
            <ArrowLeft weight="bold" /> {t.back}
          </Link>
        </nav>
        <div className="nav-actions">
          <button
            className="language"
            type="button"
            onClick={() => setLocale(zh ? "en" : "zh")}
          >
            {zh ? "EN" : "中文"}
          </button>
          <a
            className="store-button ghost compact"
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
          >
            <GithubLogo weight="fill" />
            <span>{t.source}</span>
          </a>
        </div>
      </header>

      <div className="shell">
        <div className="console-head">
          <div>
            <p className="eyebrow">
              {mode === "loading"
                ? t.eyebrowLoading
                : live
                  ? t.eyebrowLive
                  : t.eyebrowSample}
            </p>
            <h1>{t.title}</h1>
            <p>{t.lede}</p>
          </div>
          <Link className="store-button" href="/#book">
            <ArrowRight weight="bold" />
            <span>{t.book}</span>
          </Link>
        </div>

        {live ? (
          <p className="console-banner live">
            <Database weight="fill" />
            {t.liveBanner(
              cards.length,
              turnsTotal,
              memories.filter((memory) => memory.valid_at === null).length,
              cards.filter(
                (entry) => entry.narrative && entry.generated_by !== "rule",
              ).length,
            )}
          </p>
        ) : (
          <p className="console-banner">
            <Warning weight="fill" />
            {mode === "loading" ? t.loadingBanner : t.offlineBanner(apiBase)}
          </p>
        )}

        <div className="console-grid">
          <section className="panel">
            <div className="panel-head">
              <h2>{t.dayTitle}</h2>
              <span>{live ? `${cards.length} cards` : "sample"}</span>
            </div>
            <div className="day-list">
              {dayList.map((entry, index) => (
                <button
                  type="button"
                  key={entry.key}
                  className={entry.key === activeKey ? "selected" : ""}
                  aria-pressed={entry.key === activeKey}
                  onClick={() => {
                    setActiveMemory(null);
                    if (live) {
                      setSelected(entry.date);
                      void selectDay(entry.date);
                    } else {
                      setSampleIndex(index);
                    }
                  }}
                >
                  <strong>
                    {entry.date} · {weekday(entry.date, locale)}
                  </strong>
                  <small>{entry.label}</small>
                </button>
              ))}
            </div>
          </section>

          <article className="card-lg">
            <p className="card-lg-head">
              <span>
                {live ? selected : sampleDay.date} ·{" "}
                {weekday(live ? (selected ?? "") : sampleDay.date, locale)}
              </span>
              <span>
                {live
                  ? card?.narrative && card.generated_by !== "rule"
                    ? t.cardMetaModel(card.model ?? card.generated_by)
                    : t.cardMetaRule
                  : t.cardMetaSample}
              </span>
            </p>
            <h2>
              {live
                ? (card?.narrative ?? card?.summary ?? "—")
                : zh
                  ? sampleDay.zhSummary
                  : sampleDay.enSummary}
            </h2>
            {live && card && (
              <p
                className={`provenance ${
                  card.narrative && card.generated_by !== "rule" ? "model" : "rule"
                }`}
              >
                {card.narrative && card.generated_by !== "rule"
                  ? t.modelBadge
                  : t.ruleBadge}
              </p>
            )}
            {live && card?.narrative && (
              <p className="rule-headline">{card.summary}</p>
            )}

            <div className="card-section">
              <p>{t.items}</p>
              <ul className="card-list">
                {(live
                  ? (card?.developer_behavior_facts ?? [])
                  : zh
                    ? sampleDay.zhItems
                    : sampleDay.enItems
                ).map((item) => (
                  <li key={item}>
                    <Check weight="bold" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              {live && (
                <small style={{ color: "#667085", fontSize: 11 }}>
                  {t.factsNote}
                </small>
              )}
            </div>

            {live && card && card.open_threads.length > 0 && (
              <div className="card-section">
                <p>{t.threads}</p>
                <ul className="card-list">
                  {card.open_threads.map((thread) => (
                    <li key={thread}>
                      <CaretRight weight="bold" />
                      <span>{thread}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="card-section">
              <p>{t.wrote}</p>
              {live ? (
                wroteThatDay.length === 0 ? (
                  <small style={{ color: "#667085", fontSize: 12 }}>
                    {t.noWrote}
                  </small>
                ) : (
                  <div className="pills">
                    {wroteThatDay.map((memory) => (
                      <span
                        key={memory.id}
                        role="button"
                        tabIndex={0}
                        className={activeMemory === memory.id ? "active" : ""}
                        onClick={() =>
                          setActiveMemory(
                            activeMemory === memory.id ? null : memory.id,
                          )
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setActiveMemory(
                              activeMemory === memory.id ? null : memory.id,
                            );
                          }
                        }}
                      >
                        {memory.content}
                      </span>
                    ))}
                  </div>
                )
              ) : sampleDay.wrote.length === 0 ? (
                <small style={{ color: "#667085", fontSize: 12 }}>
                  {t.noWrote}
                </small>
              ) : (
                <div className="pills">
                  {sampleDay.wrote.map((id) => {
                    const memory = SAMPLE_MEMORIES.find(
                      (entry) => entry.id === id,
                    );
                    if (!memory) return null;
                    return (
                      <span
                        key={id}
                        role="button"
                        tabIndex={0}
                        className={activeMemory === id ? "active" : ""}
                        onClick={() =>
                          setActiveMemory(activeMemory === id ? null : id)
                        }
                      >
                        {zh ? memory.zh : memory.en}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>

            {!live && (
              <div className="card-section">
                <p>{t.behaviour}</p>
                <div className="behaviour">
                  <p>{zh ? sampleDay.zhBehaviour : sampleDay.enBehaviour}</p>
                  <small>{sampleDay.behaviourMeta}</small>
                </div>
                <small style={{ color: "#667085", fontSize: 11 }}>
                  {t.behaviourNote}
                </small>
              </div>
            )}

            <p className="card-scribble">
              {live
                ? zh
                  ? "这一整张卡你自己没写一个字。"
                  : "You wrote none of this card."
                : zh
                  ? sampleDay.zhScribble
                  : sampleDay.enScribble}
            </p>
          </article>

          <div className="console-col">
            <section className="panel">
              <div className="panel-head">
                <h2>{t.timeline}</h2>
                <span>
                  {live
                    ? `${visibleMemories.length} rows`
                    : sampleDay.sources}
                </span>
              </div>
              <div className="tier-card">
                {live && visibleMemories.length === 0 && (
                  <p className="empty">{t.emptyMemories}</p>
                )}
                {(live
                  ? visibleMemories.map((memory) => ({
                      id: memory.id as number | string,
                      label: `m_${memory.id}`,
                      text: memory.content,
                      createdAt: localDate(memory.created_at),
                      closed: memory.valid_at !== null,
                      supersededBy:
                        memory.superseded_by === null
                          ? null
                          : `m_${memory.superseded_by}`,
                      weight: memory.decay_multiplier,
                      fresh: localDate(memory.created_at) === selected,
                    }))
                  : SAMPLE_MEMORIES.filter(
                      (memory) => memory.createdAt <= sampleDay.date,
                    ).map((memory) => ({
                      id: memory.id as number | string,
                      label: memory.id,
                      text: zh ? memory.zh : memory.en,
                      createdAt: memory.createdAt,
                      closed: memory.supersededBy !== undefined,
                      supersededBy: memory.supersededBy ?? null,
                      weight: memory.weight,
                      fresh: sampleDay.wrote.includes(memory.id),
                    }))
                ).map((row) => (
                  <div
                    className={`record ${activeMemory === row.id ? "active" : ""} ${
                      row.fresh ? "fresh" : ""
                    }`}
                    key={row.id}
                  >
                    <span>
                      <b>{row.label}</b> {row.text}
                    </span>
                    <small>
                      created_at {row.createdAt} ·{" "}
                      {row.closed
                        ? `${t.stale}${row.supersededBy ? ` → ${row.supersededBy}` : ""}`
                        : t.open}
                    </small>
                    <span className="decay">
                      <span className="decay-bar">
                        <i style={{ width: `${Math.max(2, row.weight * 100)}%` }} />
                      </span>
                      <span>{row.weight.toFixed(2)}</span>
                    </span>
                  </div>
                ))}
              </div>
              {(live ? visibleMemories.length > 0 : true) && (
                <p className="empty">{t.timelineNote}</p>
              )}
            </section>

            <details className="underneath">
              <summary>
                <CaretRight weight="bold" />
                {t.underneath}
                <em>{t.underneathMeta}</em>
              </summary>
              <div className="underneath-body">
                <div className="tier-card">
                  <header>
                    <strong>{t.t1}</strong>
                    <span>session_turns</span>
                  </header>
                  {(live
                    ? turns.map((turn) => ({
                        key: String(turn.id),
                        tool: turn.tool ?? turn.role,
                        text: turn.content.slice(0, 220),
                      }))
                    : sampleDay.turns.map((turn) => ({
                        key: turn.text,
                        tool: turn.tool,
                        text: turn.text,
                      }))
                  ).map((row) => (
                    <div className="record" key={row.key}>
                      <span>
                        <b>{row.tool}</b> {row.text}
                      </span>
                    </div>
                  ))}
                  {live && turnsTotal > turns.length && (
                    <p className="empty">
                      {t.t1More(turns.length, turnsTotal)}
                    </p>
                  )}
                </div>
                <div className="tier-card">
                  <header>
                    <strong>{t.t2}</strong>
                    <span>
                      rolling_summaries
                      {live && card ? ` · ${card.token_count} tokens` : ""}
                    </span>
                  </header>
                  {(live
                    ? (card?.developer_behavior_facts ?? [])
                    : sampleDay.facts
                  ).map((fact) => (
                    <div className="record" key={fact}>
                      <span>{fact}</span>
                    </div>
                  ))}
                </div>
                <div className="tier-card">
                  <header>
                    <strong>{t.t3}</strong>
                    <span>
                      pgvector ·{" "}
                      {live ? visibleMemories.length : SAMPLE_MEMORIES.length} rows
                    </span>
                  </header>
                  {live
                    ? visibleMemories.map((memory) => (
                        <div className="record" key={memory.id}>
                          <span>
                            <b>m_{memory.id}</b> {memory.content}
                          </span>
                          <small>
                            category {memory.category} · age{" "}
                            {memory.age_days.toFixed(2)}d · weight{" "}
                            {memory.decay_multiplier.toFixed(4)} · accessed{" "}
                            {memory.access_count}×
                          </small>
                        </div>
                      ))
                    : SAMPLE_MEMORIES.map((memory) => (
                        <div className="record" key={memory.id}>
                          <span>
                            <b>{memory.id}</b> {zh ? memory.zh : memory.en}
                          </span>
                        </div>
                      ))}
                </div>
              </div>
            </details>

            <section className="panel">
              <div className="panel-head">
                <h2>{t.batch}</h2>
                <span>{live ? "from postgres" : zh ? "示例" : "sample"}</span>
              </div>
              <div className="metrics">
                <div className="metric">
                  <strong>{live ? turnsTotal : sampleDay.turns.length}</strong>
                  <small>{t.batchTurns}</small>
                </div>
                <div className="metric">
                  <strong>
                    {live
                      ? (card?.developer_behavior_facts.length ?? 0)
                      : sampleDay.facts.length}
                  </strong>
                  <small>{t.batchFacts}</small>
                </div>
                <div className="metric">
                  <strong>
                    {live ? visibleMemories.length : SAMPLE_MEMORIES.length}
                  </strong>
                  <small>{t.batchMemories}</small>
                </div>
              </div>
              <p className="empty">
                <Moon weight="fill" style={{ width: 12, height: 12 }} />{" "}
                {zh
                  ? "Path A 由 docker compose run --rm ingest 触发，尚未接定时任务。卡片里的轮次是当次解析的计数，这里是 T1 现在的行数 —— 后续再跑一次 ingest 两者就会有差。"
                  : "Path A runs via docker compose run --rm ingest; no scheduler yet. The card's turn count is from the run that wrote it, while this is the current row count in T1 — a later ingest makes them differ."}
              </p>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
