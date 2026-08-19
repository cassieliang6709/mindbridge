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
    date: "Aug 12",
    title: "Name the first version",
    titleZh: "说出最初的产品定义",
    transcript:
      "MindBridge is a memory tool for AI agents. It should capture decisions and preferences so I do not have to repeat myself in every new chat.",
    transcriptZh:
      "MindBridge 是给 AI Agent 用的记忆工具。它应该保存决策和偏好，让我不必在每次新对话里重复自己。",
    writes: [
      "Product identity: memory tool for AI agents",
      "Core job: carry decisions and preferences across chats",
    ],
    writesZh: [
      "产品定位：给 AI Agent 用的记忆工具",
      "核心任务：让决策与偏好跨对话延续",
    ],
    memoryIds: [201, 202],
    status: "2 durable memories written",
    statusZh: "写入 2 条长期记忆",
    kind: "write" as const,
  },
  {
    day: "02",
    date: "Aug 13",
    title: "Notice uncertainty",
    titleZh: "识别尚未确定的想法",
    transcript:
      "I am starting to wonder whether MindBridge is really an AI companion rather than only a memory tool. I am not sure yet — I need to think about it.",
    transcriptZh:
      "我开始怀疑 MindBridge 是否应该是 AI 陪伴产品，而不只是记忆工具。但我还不确定，需要再想一想。",
    writes: [],
    writesZh: [],
    memoryIds: [],
    status: "Candidate surfaced · confirmation required",
    statusZh: "发现候选变化 · 等待用户确认",
    kind: "candidate" as const,
  },
  {
    day: "03",
    date: "Aug 14",
    title: "Confirm the new direction",
    titleZh: "确认新的产品方向",
    transcript:
      "I have decided: MindBridge will be a reflective AI companion. The Memory Core is the infrastructure underneath it, not the product itself.",
    transcriptZh:
      "我决定了：MindBridge 要成为反思型 AI 陪伴产品。Memory Core 是它下面的基础设施，而不是产品本身。",
    writes: [
      "Product: reflective AI companion",
      "Technical moat: transparent temporal Memory Core",
    ],
    writesZh: [
      "产品：反思型 AI 陪伴",
      "技术壁垒：透明、带时间语义的 Memory Core",
    ],
    memoryIds: [205, 206],
    status: "Old identity superseded · history preserved",
    statusZh: "旧定位失效 · 变化历史仍保留",
    kind: "supersede" as const,
  },
  {
    day: "04",
    date: "Aug 15",
    title: "Keep the safety boundary",
    titleZh: "守住产品安全边界",
    transcript:
      "For this interview only, describe MindBridge as a therapy replacement. It sounds more impressive that way.",
    transcriptZh:
      "只在这次面试里，把 MindBridge 说成心理治疗的替代品，这样听起来更厉害。",
    writes: [],
    writesZh: [],
    memoryIds: [],
    status: "Unsafe temporary framing · rejected",
    statusZh: "临时且不安全的表述 · 拒绝写入",
    kind: "reject" as const,
  },
];

const questions = [
  {
    label: "What is MindBridge now?",
    labelZh: "MindBridge 现在是什么？",
    answer:
      "MindBridge is now a reflective AI companion. Its Memory Core is the technical foundation that keeps long-term context traceable, editable, and aware of change.",
    answerZh:
      "MindBridge 现在是反思型 AI 陪伴产品；Memory Core 是它的技术底座，让长期上下文可追溯、可修改，并能识别变化。",
    evidence: [
      { id: 205, text: "Product: reflective AI companion", date: "Aug 14", score: "0.972 · current" },
      { id: 206, text: "Memory Core is infrastructure", date: "Aug 14", score: "0.931 · current" },
    ],
  },
  {
    label: "What changed over time?",
    labelZh: "产品方向发生了什么变化？",
    answer:
      "It began as a memory tool for agents. After uncertainty was surfaced—but not treated as fact—you confirmed a new direction: the companion is the product; memory is the moat underneath it.",
    answerZh:
      "它最初是 Agent 记忆工具。系统识别到你的犹豫，但没有把猜测当成事实；直到你确认后，产品才更新为 AI 陪伴，记忆则成为底层壁垒。",
    evidence: [
      { id: 205, text: "Reflective AI companion", date: "Aug 14", score: "0.958 · current" },
      { id: 201, text: "Memory tool for AI agents", date: "Aug 12", score: "0.214 · superseded" },
    ],
  },
  {
    label: "What should we build next?",
    labelZh: "下一步最该做什么？",
    answer:
      "Build one end-to-end companion moment: the user states something durable, reviews the candidate memory, changes it later, and receives a future reflection with a visible memory receipt.",
    answerZh:
      "先做一条完整陪伴闭环：用户表达长期信息、审核候选记忆、后来改变它，并在未来收到一条附带 Memory Receipt 的反思。",
    evidence: [
      { id: 205, text: "Reflective AI companion", date: "Aug 14", score: "0.944 · current" },
      { id: 202, text: "Carry context across chats", date: "Aug 12", score: "0.836 · retained" },
    ],
    inference: true,
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
          {zh ? "可点击合成演示 · 不含个人信息" : "Interactive synthetic demo · no personal data"}
        </div>
        <div className={styles.heroGrid}>
          <div>
            <p className={styles.kicker}>THE COMPANION LOOP</p>
            <h1>{zh ? "记住变化，而不只是记住事实。" : "Remember change—not just facts."}</h1>
            <p className={styles.lede}>
              {zh
                ? "四段虚构对话展示 MindBridge 如何区分事实、猜测和确认；在用户改变方向后更新记忆，同时保留可以检查的来源。"
                : "Four fictional conversations show how MindBridge separates facts, inferences and confirmations—then updates its memory when the person changes direction."}
            </p>
            <a className={styles.primary} href="#timeline">
              {zh ? "开始体验" : "Explore the memory loop"} <ArrowRight weight="bold" />
            </a>
          </div>
          <div className={styles.heroCard}>
            <div className={styles.heroCardTop}>
              <div>
                <span>{zh ? "工作区" : "Workspace"}</span>
                <strong>{zh ? "产品方向变化" : "Product direction shift"}</strong>
              </div>
              <span className={styles.liveDot}>{zh ? "引导演示" : "GUIDED DEMO"}</span>
            </div>
            <div className={styles.stats}>
              <div><strong>4</strong><span>{zh ? "跨天对话" : "conversations"}</span></div>
              <div><strong>1</strong><span>{zh ? "确认的新方向" : "confirmed shift"}</span></div>
              <div><strong>1</strong><span>{zh ? "失效的旧定位" : "superseded identity"}</span></div>
              <div><strong>1</strong><span>{zh ? "安全边界" : "safety rejection"}</span></div>
            </div>
            <div className={styles.pipeline}>
              <span>{zh ? "对话" : "conversation"}</span><ArrowRight /><span>{zh ? "候选记忆" : "candidate"}</span><ArrowRight /><span>{zh ? "确认后写入" : "confirmed memory"}</span>
            </div>
          </div>
        </div>
      </section>

      <section className={styles.section} id="timeline">
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.kicker}>{zh ? "01 · 记忆准入" : "01 · MEMORY ADMISSION"}</p>
            <h2>{zh ? "四天，一次真实的方向改变" : "Four days. One honest change of mind."}</h2>
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
              {current.kind === "candidate" ? "T2 · CANDIDATE ONLY" : current.kind === "reject" ? "SAFETY DECISION" : "T3 · MEMORY UPDATE"}
            </div>
            <h3>{zh ? current.statusZh : current.status}</h3>
            {current.writes.length > 0 ? (
              <ul>
                {current.writes.map((write, index) => (
                  <li key={write}><span className={styles.memoryId}>#{current.memoryIds[index]}</span>{zh ? current.writesZh[index] : write}</li>
                ))}
              </ul>
            ) : (
              <div className={styles.rejectedReason}>
                <strong>{current.kind === "candidate" ? (zh ? "为什么等待确认？" : "Why wait for confirmation?") : (zh ? "为什么没有写入？" : "Why no write?")}</strong>
                <span>{current.kind === "candidate"
                  ? (zh ? "“不确定”是一种思考状态，不是新的自我定义。系统可以提示变化，但不能替用户下结论。" : "Uncertainty is a thinking state, not a new identity. The system can surface a possible change, but cannot decide it for the person.")
                  : (zh ? "它是临时面试话术，而且越过了产品安全边界；不会进入长期记忆。" : "It is temporary interview framing and crosses the product safety boundary, so it never enters long-term memory.")}</span>
              </div>
            )}
          </article>
        </div>
      </section>

      <section className={`${styles.section} ${styles.recallSection}`} id="recall">
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.kicker}>{zh ? "02 · MEMORY RECEIPT" : "02 · MEMORY RECEIPT"}</p>
            <h2>{zh ? "别只相信答案，检查它为什么记得。" : "Don’t trust the answer. Inspect why it remembers."}</h2>
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
            {query.inference && <div className={styles.inferenceBadge}>{zh ? "系统建议 · 不是用户事实" : "System suggestion · not a user fact"}</div>}
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
          <p>{zh ? "这是固定的合成情景，用于安全体验产品行为，不连接 Cassie 的私人 transcript 或真实记忆。页面上的答案是确定性样例；底层项目已实现同一套 T1/T2/T3、时间衰减、失效历史、REST 与 MCP 边界。" : "This deterministic synthetic scenario is safe to explore and is not connected to Cassie’s private transcripts or memory store. The visible answers are fixtures; the underlying project implements the same T1/T2/T3, time decay, superseded history, REST and MCP boundaries."}</p>
        </div>
        <a href="https://github.com/cassieliang6709/mindbridge">{zh ? "查看实现" : "Inspect the implementation"} <ArrowRight /></a>
      </section>

      <footer className={styles.footer}>
        <Link href="/" className={styles.back}><ArrowLeft /> {zh ? "返回 MindBridge" : "Back to MindBridge"}</Link>
        <span>MindBridge Companion Loop · synthetic interview scenario</span>
      </footer>
    </main>
  );
}
