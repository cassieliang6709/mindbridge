"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CaretRight,
  Check,
  GithubLogo,
  Moon,
  Warning,
} from "@phosphor-icons/react";
import { DEFAULT_LOCALE, GITHUB_URL, type Locale } from "../site";

/* ---------------------------------------------------------------------------
   The diary UI: what MindBridge produces for a person, not for a client
   library. Each day is one card written from that day's transcripts; the
   "look underneath" disclosure shows the T1/T2/T3 state the card came from,
   so the surface stays warm without hiding the mechanism.

   The data below is a fixed sample. Nothing here calls a model or a database —
   the banner says so, and the backend is still being built.
--------------------------------------------------------------------------- */

type Memory = {
  id: string;
  zh: string;
  en: string;
  createdAt: string;
  weight: number;
  supersededBy?: string;
};

type Day = {
  date: string;
  weekdayZh: string;
  weekdayEn: string;
  zhSummary: string;
  enSummary: string;
  zhItems: string[];
  enItems: string[];
  /** ids of memories this day wrote or refreshed */
  wrote: string[];
  zhBehaviour: string;
  enBehaviour: string;
  behaviourMeta: string;
  zhScribble: string;
  enScribble: string;
  /** raw turns the card was written from (T1) */
  turns: { tool: string; text: string }[];
  /** structured facts the extractor produced (T2) */
  facts: string[];
  sources: string;
};

const memories: Memory[] = [
  {
    id: "m_0104",
    zh: "Python 项目优先用 uv",
    en: "Prefer uv for Python projects",
    createdAt: "2026-08-04",
    weight: 0.98,
  },
  {
    id: "m_0091",
    zh: "周末不排会议",
    en: "No meetings on weekends",
    createdAt: "2026-07-28",
    weight: 0.91,
  },
  {
    id: "m_0088",
    zh: "周六早上健身",
    en: "Gym on Saturday mornings",
    createdAt: "2026-07-19",
    weight: 0.74,
  },
  {
    id: "m_0079",
    zh: "回答要直接，少寒暄",
    en: "Answer directly, skip the preamble",
    createdAt: "2026-07-11",
    weight: 0.66,
  },
  {
    id: "m_0031",
    zh: "周末可以加班",
    en: "Open to weekend work",
    createdAt: "2026-03-04",
    weight: 0.29,
    supersededBy: "m_0091",
  },
];

const days: Day[] = [
  {
    date: "2026-08-04",
    weekdayZh: "周二",
    weekdayEn: "Tue",
    zhSummary: "今天大半时间在打通检索链路",
    enSummary: "Most of today went into the retrieval path",
    zhItems: [
      "用 FastAPI 搭好 /retrieve 雏形，修掉 3 个 500。",
      "在 Codex 里过了 3 道 Graph 题，卡在拓扑排序。",
      "把包管理从 pip 换成 uv，重装了两个环境。",
    ],
    enItems: [
      "Stood up /retrieve in FastAPI and cleared three 500s.",
      "Worked three graph problems in Codex; stuck on topological sort.",
      "Switched package management from pip to uv, rebuilt two envs.",
    ],
    wrote: ["m_0104"],
    zhBehaviour: "凌晨 1:12 还有提交，比前一天晚了约两小时。",
    enBehaviour: "Last commit at 1:12am — about two hours later than yesterday.",
    behaviourMeta: "last_activity 01:12 · prev_day 23:04",
    zhScribble: "今天你自己没写一个字。",
    enScribble: "You wrote none of this yourself.",
    turns: [
      { tool: "claude-code", text: "为什么 /retrieve 在空 query 上返回 500？" },
      { tool: "claude-code", text: "以后所有 Python 项目都用 uv，别再用 pip。" },
      { tool: "codex-cli", text: "解释一下 Kahn 算法怎么检测环。" },
    ],
    facts: [
      "偏好：Python 项目优先用 uv / prefer uv",
      "进度：/retrieve 端点可用，500 已修 / endpoint works",
      "卡点：拓扑排序 / topological sort",
    ],
    sources: "3 sessions · 2 tools · 41 turns",
  },
  {
    date: "2026-08-03",
    weekdayZh: "周一",
    weekdayEn: "Mon",
    zhSummary: "调 pgvector 的召回质量",
    enSummary: "Tuning recall quality in pgvector",
    zhItems: [
      "把 embedding 维度从 1536 降到 768，重建索引。",
      "发现旧偏好一直被召回，加了时间衰减权重。",
      "跟 Claude 讨论要不要拆出独立的 summary 表。",
    ],
    enItems: [
      "Dropped embeddings from 1536 to 768 dims and rebuilt the index.",
      "Found stale preferences ranking too high; added time decay.",
      "Talked through splitting summaries into their own table.",
    ],
    wrote: ["m_0091"],
    zhBehaviour: "连续 4 小时同一个 session，没有中断。",
    enBehaviour: "One unbroken four-hour session.",
    behaviourMeta: "longest_session 4h02m",
    zhScribble: "衰减这件事是今天才想通的。",
    enScribble: "Decay only clicked today.",
    turns: [
      { tool: "claude-code", text: "为什么三月那条偏好还排在第一？" },
      { tool: "claude-code", text: "帮我给召回加一个时间衰减项。" },
    ],
    facts: [
      "决定：召回引入时间衰减 / add time decay",
      "参数：embedding 768 dims",
    ],
    sources: "2 sessions · 1 tool · 63 turns",
  },
  {
    date: "2026-08-02",
    weekdayZh: "周日",
    weekdayEn: "Sun",
    zhSummary: "只看了会儿论文，没写代码",
    enSummary: "Read a bit, wrote no code",
    zhItems: [
      "读了两篇长期记忆相关的 paper，做了笔记。",
      "没有开新的 coding session。",
    ],
    enItems: [
      "Read two papers on long-term memory and took notes.",
      "No coding session opened.",
    ],
    wrote: [],
    zhBehaviour: "周末没有排会议，与已记录的偏好一致。",
    enBehaviour: "No meetings booked this weekend — matches the stored preference.",
    behaviourMeta: "meetings 0 · matches m_0091",
    zhScribble: "休息也是一条记录。",
    enScribble: "Rest is a record too.",
    turns: [{ tool: "claude-desktop", text: "帮我总结这篇 MemGPT 的核心思路。" }],
    facts: ["活动：阅读，无代码提交 / reading, no commits"],
    sources: "1 session · 1 tool · 8 turns",
  },
];

const copy = {
  zh: {
    back: "返回首页",
    eyebrow: "日记界面 · 示例数据",
    title: "MindBridge 记忆日记",
    lede: "每天一张卡，由当天的 AI 对话自动写成。你不用动手，也可以随时展开看它是从哪几条原始记录来的。",
    banner:
      "这里是固定的示例数据，用来展示界面形态。它不调用任何模型、不读你本机的 transcript、也不连数据库；日志解析与 MCP server 还在建。",
    book: "预约真机演示",
    dayTitle: "日期",
    cardMeta: "自动生成",
    items: "今天发生了什么",
    wrote: "写入的偏好",
    noWrote: "今天没有新偏好写入。",
    behaviour: "行为观察",
    behaviourNote: "只陈述可观测的行为，不做情绪判断。",
    timeline: "记忆时间轴",
    timelineNote: "点上面的偏好标签可以定位到对应记录",
    stale: "已被取代",
    open: "valid_at 未关闭",
    underneath: "看底层",
    underneathMeta: "T1 / T2 / T3 原始状态",
    t1: "T1 会话缓冲区 — 卡片依据的原始轮次",
    t2: "T2 滚动摘要 — 抽取出的结构化事实",
    t3: "T3 pgvector — 这一天之后的长期记忆",
    source: "源码",
  },
  en: {
    back: "Back to home",
    eyebrow: "Diary · sample data",
    title: "MindBridge memory diary",
    lede: "One card a day, written from that day's AI conversations. Nothing for you to do — and you can always open it up to see which raw records it came from.",
    banner:
      "Fixed sample data, here to show the interface. It calls no model, reads none of your transcripts, and touches no database; the log parser and MCP server are still being built.",
    book: "Book a real walkthrough",
    dayTitle: "Day",
    cardMeta: "auto-generated",
    items: "What happened",
    wrote: "Preferences written",
    noWrote: "No new preference written today.",
    behaviour: "Behavioural note",
    behaviourNote: "Observable behaviour only — no emotional inference.",
    timeline: "Memory timeline",
    timelineNote: "Tap a preference above to locate its record",
    stale: "superseded",
    open: "valid_at open",
    underneath: "Look underneath",
    underneathMeta: "raw T1 / T2 / T3 state",
    t1: "T1 session buffer — the raw turns behind this card",
    t2: "T2 rolling summary — extracted structured facts",
    t3: "T3 pgvector — long-term memory as of this day",
    source: "Source",
  },
};

export function MemoryConsole() {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);
  const [dayIndex, setDayIndex] = useState(0);
  const [activeMemory, setActiveMemory] = useState<string | null>(null);
  const t = copy[locale];
  const day = days[dayIndex];
  const zh = locale === "zh";

  // Only memories that already existed on the selected day belong on its card.
  const visible = useMemo(
    () => memories.filter((memory) => memory.createdAt <= day.date),
    [day.date],
  );
  const wrote = visible.filter((memory) => day.wrote.includes(memory.id));

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
            <p className="eyebrow">{t.eyebrow}</p>
            <h1>{t.title}</h1>
            <p>{t.lede}</p>
          </div>
          <Link className="store-button" href="/#book">
            <ArrowRight weight="bold" />
            <span>{t.book}</span>
          </Link>
        </div>

        <p className="console-banner">
          <Warning weight="fill" />
          {t.banner}
        </p>

        <div className="console-grid">
          <section className="panel">
            <div className="panel-head">
              <h2>{t.dayTitle}</h2>
            </div>
            <div className="day-list">
              {days.map((option, index) => (
                <button
                  type="button"
                  key={option.date}
                  className={index === dayIndex ? "selected" : ""}
                  aria-pressed={index === dayIndex}
                  onClick={() => {
                    setDayIndex(index);
                    setActiveMemory(null);
                  }}
                >
                  <strong>
                    {option.date} · {zh ? option.weekdayZh : option.weekdayEn}
                  </strong>
                  <small>{zh ? option.zhSummary : option.enSummary}</small>
                </button>
              ))}
            </div>
          </section>

          <article className="card-lg">
            <p className="card-lg-head">
              <span>
                {day.date} · {zh ? day.weekdayZh : day.weekdayEn}
              </span>
              <span>{t.cardMeta}</span>
            </p>
            <h2>{zh ? day.zhSummary : day.enSummary}</h2>

            <div className="card-section">
              <p>{t.items}</p>
              <ul className="card-list">
                {(zh ? day.zhItems : day.enItems).map((item) => (
                  <li key={item}>
                    <Check weight="bold" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="card-section">
              <p>{t.wrote}</p>
              {wrote.length === 0 ? (
                <small style={{ color: "#667085", fontSize: 12 }}>
                  {t.noWrote}
                </small>
              ) : (
                <div className="pills">
                  {wrote.map((memory) => (
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
                      {zh ? memory.zh : memory.en}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="card-section">
              <p>{t.behaviour}</p>
              <div className="behaviour">
                <p>{zh ? day.zhBehaviour : day.enBehaviour}</p>
                <small>{day.behaviourMeta}</small>
              </div>
              <small style={{ color: "#667085", fontSize: 11 }}>
                {t.behaviourNote}
              </small>
            </div>

            <p className="card-scribble">
              {zh ? day.zhScribble : day.enScribble}
            </p>
          </article>

          <div className="console-col">
            <section className="panel">
              <div className="panel-head">
                <h2>{t.timeline}</h2>
                <span>{day.sources}</span>
              </div>
              <div className="tier-card">
                {visible.map((memory) => (
                  <div
                    className={`record ${
                      activeMemory === memory.id ? "active" : ""
                    } ${day.wrote.includes(memory.id) ? "fresh" : ""}`}
                    key={memory.id}
                  >
                    <span>
                      <b>{memory.id}</b> {zh ? memory.zh : memory.en}
                    </span>
                    <small>
                      created_at {memory.createdAt} ·{" "}
                      {memory.supersededBy
                        ? `${t.stale} → ${memory.supersededBy}`
                        : t.open}
                    </small>
                    <span className="decay">
                      <span className="decay-bar">
                        <i style={{ width: `${memory.weight * 100}%` }} />
                      </span>
                      <span>{memory.weight.toFixed(2)}</span>
                    </span>
                  </div>
                ))}
              </div>
              <p className="empty">{t.timelineNote}</p>
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
                    <span>session_buffer</span>
                  </header>
                  {day.turns.map((turn) => (
                    <div className="record" key={turn.text}>
                      <span>
                        <b>{turn.tool}</b> {turn.text}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="tier-card">
                  <header>
                    <strong>{t.t2}</strong>
                    <span>rolling_summary</span>
                  </header>
                  {day.facts.map((fact) => (
                    <div className="record" key={fact}>
                      <span>{fact}</span>
                    </div>
                  ))}
                </div>
                <div className="tier-card">
                  <header>
                    <strong>{t.t3}</strong>
                    <span>pgvector · {visible.length} rows</span>
                  </header>
                  {visible.map((memory) => (
                    <div className="record" key={memory.id}>
                      <span>
                        <b>{memory.id}</b> {zh ? memory.zh : memory.en}
                      </span>
                      <small>
                        weight {memory.weight.toFixed(2)}
                        {memory.supersededBy
                          ? ` · ${t.stale} → ${memory.supersededBy}`
                          : ""}
                      </small>
                    </div>
                  ))}
                </div>
              </div>
            </details>

            <section className="panel">
              <div className="panel-head">
                <h2>{zh ? "夜间批处理" : "Nightly batch"}</h2>
                <span>{zh ? "示例" : "sample"}</span>
              </div>
              <div className="metrics">
                <div className="metric">
                  <strong>{day.turns.length}</strong>
                  <small>{zh ? "解析的轮次" : "turns parsed"}</small>
                </div>
                <div className="metric">
                  <strong>{day.facts.length}</strong>
                  <small>{zh ? "抽出的事实" : "facts extracted"}</small>
                </div>
                <div className="metric">
                  <strong>{day.wrote.length}</strong>
                  <small>{zh ? "写入的偏好" : "preferences written"}</small>
                </div>
              </div>
              <p className="empty">
                <Moon weight="fill" style={{ width: 12, height: 12 }} />{" "}
                {zh
                  ? "真实版本由每晚一次批处理生成。"
                  : "The real version runs as one nightly batch."}
              </p>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
