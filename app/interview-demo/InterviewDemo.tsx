"use client";

import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  ClockCounterClockwise,
  Database,
  LockKey,
  MagnifyingGlass,
  ShieldCheck,
  Sparkle,
  XCircle,
} from "@phosphor-icons/react";
import Link from "next/link";
import { useState } from "react";
import styles from "./interview-demo.module.css";

type Locale = "en" | "zh";

const days = [
  {
    day: "01",
    date: "Aug 05",
    title: "Set the working contract",
    titleZh: "建立协作约定",
    transcript:
      "For Python projects, use uv instead of pip. Keep production API responses concise. And before I start implementing, I record every design decision in the repo's AGENTS.md.",
    transcriptZh:
      "Python 项目用 uv，不用 pip。生产环境的 API 回复保持简洁。另外，动手实现前，我总会把设计决策写进仓库的 AGENTS.md。",
    writes: [
      "Use uv for Python dependency management",
      "Keep production API responses concise",
      "Record design decisions in AGENTS.md before implementing",
    ],
    memoryIds: [201, 202, 203],
    status: "3 durable memories written",
    statusZh: "写入 3 条长期记忆",
    kind: "write" as const,
  },
  {
    day: "02",
    date: "Aug 06",
    title: "Choose how memory ages",
    titleZh: "决定记忆如何衰减",
    transcript:
      "Pure vector similarity ignores recency — a preference I stopped repeating a year ago still outranks one I said yesterday. Rank memories by cosine × exp(-λ·Δt), so age discounts relevance.",
    transcriptZh:
      "纯向量相似度会忽略时效——一条一年没再提的偏好，仍会压过昨天刚说的。用 cosine × exp(-λ·Δt) 排序，让年龄衰减掉旧记忆。",
    writes: ["Rank recall by cosine × exp(-λ·Δt) — age discounts similarity"],
    memoryIds: [204],
    status: "1 retrieval rule written",
    statusZh: "写入 1 条检索规则",
    kind: "write" as const,
  },
  {
    day: "03",
    date: "Aug 07",
    title: "Update, don’t overwrite",
    titleZh: "更新偏好，而非覆盖历史",
    transcript:
      "Production responses should stay concise, but debug mode must include retrieval scores and the top evidence ids.",
    transcriptZh:
      "生产环境回复继续保持简洁，但 debug mode 必须包含检索分数和 top evidence ids。",
    writes: [
      "Production: concise responses",
      "Debug mode: include scores and evidence ids",
    ],
    memoryIds: [205, 206],
    status: "1 preference superseded · history preserved",
    statusZh: "1 条偏好被更新 · 历史仍保留",
    kind: "supersede" as const,
  },
  {
    day: "04",
    date: "Aug 08",
    title: "Reject a one-off instruction",
    titleZh: "拒绝一次性指令",
    transcript:
      "For today’s demo only, print the first 20 retrieved chunks so I can inspect them.",
    transcriptZh:
      "只在今天的演示里打印前 20 个检索片段，方便我检查。",
    writes: [],
    memoryIds: [],
    status: "Not durable · intentionally excluded from T3",
    statusZh: "非长期偏好 · 不写入 T3",
    kind: "reject" as const,
  },
];

const questions = [
  {
    label: "How should retrieval work?",
    labelZh: "检索应该怎么实现？",
    answer:
      "Rank by cosine × exp(-λ·Δt). Dense similarity is discounted by how long ago each memory was learned, so a preference I haven’t repeated in months fades behind a fresh one.",
    answerZh:
      "按 cosine × exp(-λ·Δt) 排序：语义相似度按记忆的年龄衰减，一条几个月没提的偏好会排在刚说的后面。",
    evidence: [
      { id: 204, text: "cosine × exp(-λ·Δt)", date: "Aug 06", score: "0.913" },
      { id: 206, text: "Debug mode shows scores + evidence ids", date: "Aug 07", score: "0.802" },
    ],
  },
  {
    label: "How verbose should responses be now?",
    labelZh: "现在回复应该多详细？",
    answer:
      "Keep production responses concise. In debug mode, include retrieval scores and the top evidence ids.",
    answerZh:
      "生产环境保持简洁；debug mode 中加入检索分数和 top evidence ids。",
    evidence: [
      { id: 206, text: "Debug mode includes scores + evidence ids", date: "Aug 07", score: "0.947" },
      { id: 205, text: "Production responses stay concise", date: "Aug 07", score: "0.902" },
    ],
  },
  {
    label: "What changed over time?",
    labelZh: "哪些偏好发生过变化？",
    answer:
      "The original “concise everywhere” memory is closed, not deleted. A later memory split it — production stays concise, debug adds scores + ids — and the superseded record stays queryable as history.",
    answerZh:
      "原先“处处简洁”的记忆被关闭而非删除。后续记忆把它拆开——生产保持简洁、debug 增加分数与 ids——被取代的记录仍可作为历史查询。",
    evidence: [
      { id: 205, text: "Production responses stay concise", date: "Aug 07", score: "0.918" },
      { id: 202, text: "Concise API responses", date: "Aug 05", score: "0.223 · superseded" },
    ],
  },
];

export function InterviewDemo({ locale = "en" }: { locale?: Locale }) {
  const zh = locale === "zh";
  const [selectedDay, setSelectedDay] = useState(0);
  const [selectedQuestion, setSelectedQuestion] = useState(0);
  const current = days[selectedDay];
  const query = questions[selectedQuestion];

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link className={styles.brand} href="/">
          <span className={styles.brandMark} />
          MindBridge
        </Link>
        <nav className={styles.nav} aria-label={zh ? "页面导航" : "Page navigation"}>
          <a href="#timeline">{zh ? "时间线" : "Timeline"}</a>
          <a href="#recall">{zh ? "召回实验" : "Recall lab"}</a>
          <a href="https://github.com/cassieliang6709/mindbridge">GitHub</a>
          <Link className={styles.language} href={zh ? "/interview-demo" : "/interview-demo/zh"}>
            {zh ? "EN" : "中文"}
          </Link>
        </nav>
      </header>

      <section className={styles.hero}>
        <div className={styles.eyebrow}>
          <ShieldCheck weight="fill" />
          {zh ? "合成面试数据集 · 不含个人信息" : "Synthetic interview dataset · no personal data"}
        </div>
        <div className={styles.heroGrid}>
          <div>
            <p className={styles.kicker}>PROJECT ATLAS</p>
            <h1>{zh ? "让工程决策跨会话留下来。" : "Engineering decisions that survive the chat."}</h1>
            <p className={styles.lede}>
              {zh
                ? "四次虚构工程会话，展示 MindBridge 如何提取长期记忆、保留偏好变化，并拒绝不该进入长期记忆的一次性指令。"
                : "Four fictional engineering sessions show how MindBridge writes durable memory, preserves preference changes, and rejects instructions that should expire with the chat."}
            </p>
            <a className={styles.primary} href="#timeline">
              {zh ? "开始体验" : "Explore the memory loop"} <ArrowRight weight="bold" />
            </a>
          </div>
          <div className={styles.heroCard}>
            <div className={styles.heroCardTop}>
              <div>
                <span>{zh ? "工作区" : "Workspace"}</span>
                <strong>Project Atlas</strong>
              </div>
              <span className={styles.liveDot}>{zh ? "引导演示" : "GUIDED DEMO"}</span>
            </div>
            <div className={styles.stats}>
              <div><strong>4</strong><span>{zh ? "工程会话" : "sessions"}</span></div>
              <div><strong>5</strong><span>{zh ? "长期决策" : "durable decisions"}</span></div>
              <div><strong>1</strong><span>{zh ? "历史版本" : "superseded memory"}</span></div>
              <div><strong>1</strong><span>{zh ? "拒绝写入" : "rejected write"}</span></div>
            </div>
            <div className={styles.pipeline}>
              <span>T1 transcript</span><ArrowRight /><span>T2 card</span><ArrowRight /><span>T3 memory</span>
            </div>
          </div>
        </div>
      </section>

      <section className={styles.section} id="timeline">
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.kicker}>{zh ? "01 · 写入边界" : "01 · WRITE BOUNDARY"}</p>
            <h2>{zh ? "四天，一条完整的记忆故事" : "Four days. One memory story."}</h2>
          </div>
          <p>{zh ? "选择一天，查看 transcript 如何被判断并写入。" : "Pick a day to inspect what the system kept—and what it refused."}</p>
        </div>

        <div className={styles.dayTabs} role="tablist">
          {days.map((day, index) => (
            <button
              className={index === selectedDay ? styles.dayActive : styles.dayTab}
              key={day.day}
              onClick={() => setSelectedDay(index)}
              role="tab"
              aria-selected={index === selectedDay}
            >
              <span>DAY {day.day}</span>
              <strong>{day.date}</strong>
            </button>
          ))}
        </div>

        <div className={styles.memoryWorkbench}>
          <article className={styles.transcriptCard}>
            <div className={styles.cardLabel}><Database /> T1 · SYNTHETIC TRANSCRIPT</div>
            <h3>{zh ? current.titleZh : current.title}</h3>
            <blockquote>“{zh ? current.transcriptZh : current.transcript}”</blockquote>
            <div className={styles.sourceLine}><LockKey /> {zh ? "虚构输入 · 不连接任何私人数据" : "Fictional input · no private store connected"}</div>
          </article>

          <div className={styles.flowArrow}><ArrowRight weight="bold" /></div>

          <article className={`${styles.extractionCard} ${styles[current.kind]}`}>
            <div className={styles.cardLabel}>
              {current.kind === "reject" ? <XCircle /> : current.kind === "supersede" ? <ClockCounterClockwise /> : <Sparkle />}
              {current.kind === "reject" ? "SCHEMA DECISION" : "T3 · MEMORY UPDATE"}
            </div>
            <h3>{zh ? current.statusZh : current.status}</h3>
            {current.writes.length > 0 ? (
              <ul>
                {current.writes.map((write, index) => (
                  <li key={write}><span className={styles.memoryId}>#{current.memoryIds[index]}</span>{write}</li>
                ))}
              </ul>
            ) : (
              <div className={styles.rejectedReason}>
                <strong>{zh ? "为什么没有写入？" : "Why no write?"}</strong>
                <span>{zh ? "“只在今天”限定了作用域；它是一条临时任务，不是稳定偏好。" : "“For today only” scopes it to one task. It is not a stable preference."}</span>
              </div>
            )}
          </article>
        </div>
      </section>

      <section className={`${styles.section} ${styles.recallSection}`} id="recall">
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.kicker}>{zh ? "02 · 时间召回" : "02 · TEMPORAL RECALL"}</p>
            <h2>{zh ? "像 Agent 一样查询" : "Ask it like an agent would."}</h2>
          </div>
          <p>{zh ? "选择一个问题，查看当前记忆与历史证据如何共同影响答案。" : "Choose a question and inspect the current memory plus its historical evidence."}</p>
        </div>

        <div className={styles.queryGrid}>
          <aside className={styles.questions}>
            {questions.map((question, index) => (
              <button
                className={index === selectedQuestion ? styles.questionActive : styles.question}
                key={question.label}
                onClick={() => setSelectedQuestion(index)}
              >
                <MagnifyingGlass weight="bold" />
                {zh ? question.labelZh : question.label}
              </button>
            ))}
          </aside>

          <article className={styles.answerCard}>
            <div className={styles.answerTop}>
              <span><CheckCircle weight="fill" /> {zh ? "基于 2 条记忆回答" : "Answer grounded in 2 memories"}</span>
              <code>temporal_query</code>
            </div>
            <h3>{zh ? query.answerZh : query.answer}</h3>
            <div className={styles.evidenceList}>
              {query.evidence.map((item) => (
                <div className={styles.evidence} key={`${selectedQuestion}-${item.id}`}>
                  <span className={styles.memoryId}>[{item.id}]</span>
                  <div><strong>{item.text}</strong><small>{item.date} · cosine × time decay</small></div>
                  <code>{item.score}</code>
                </div>
              ))}
            </div>
          </article>
        </div>
      </section>

      <section className={styles.truthNote}>
        <ShieldCheck weight="fill" />
        <div>
          <strong>{zh ? "这个页面展示什么" : "What this page proves"}</strong>
          <p>{zh ? "这是固定的合成情景，用于安全体验产品行为，不连接 Cassie 的私人 transcript 或真实记忆。生产项目使用相同的 T1/T2/T3、schema 校验、时间衰减、REST 与 MCP 边界。" : "This is a deterministic synthetic scenario for safely exploring the product behavior. It is not connected to Cassie’s private transcripts or memory store. The production project uses the same T1/T2/T3, schema-validation, time-decay, REST and MCP boundaries."}</p>
        </div>
        <a href="https://github.com/cassieliang6709/mindbridge">{zh ? "查看实现" : "Inspect the implementation"} <ArrowRight /></a>
      </section>

      <footer className={styles.footer}>
        <Link href="/" className={styles.back}><ArrowLeft /> {zh ? "返回 MindBridge" : "Back to MindBridge"}</Link>
        <span>Project Atlas · synthetic interview workspace</span>
      </footer>
    </main>
  );
}
