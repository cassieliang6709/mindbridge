"use client";

import Link from "next/link";
import { ReactNode, useEffect, useState } from "react";
import {
  ArrowRight,
  Book,
  CaretRight,
  Check,
  FileCode,
  GithubLogo,
  Laptop,
  Path,
  PlugsConnected,
  ShieldCheck,
  Sparkle,
  Warning,
} from "@phosphor-icons/react";
import rawResults from "../evals/results.json";
import {
  CONTACT_EMAIL,
  GITHUB_URL,
  LOCAL_CORPUS,
  type Locale,
} from "./site";

/**
 * evals/results.json ships with every metric null. The cast keeps the page
 * compiling once a real run fills those fields in with strings.
 */
const results = rawResults as { metrics: Record<string, string | null> };

/* --- benchmark rows ----------------------------------------------------- */

const benchRows: {
  key: string;
  zh: string;
  en: string;
  zhHow: ReactNode;
  enHow: ReactNode;
}[] = [
  {
    key: "decayOrdering",
    zh: "时间衰减打分正确性",
    en: "Time-decay scoring correctness",
    zhHow: (
      <>
        同一条内容存成不同年龄，检验排序是否新的在前、且每个分数与闭式
        cosine · exp(-λ·Δt) 相差在 1e-6 内。与 embedder 无关。脚本：
        <code>evals/eval_memory_engine.py</code>
      </>
    ),
    enHow: (
      <>
        Identical content stored at several ages: checks newest-first ordering
        and that every score matches the closed form cosine · exp(-λ·Δt) to
        within 1e-6. Embedder-independent. Script:{" "}
        <code>evals/eval_memory_engine.py</code>
      </>
    ),
  },
  {
    key: "supersedeExclusion",
    zh: "失效记忆隔离正确性",
    en: "Superseded-record isolation",
    zhHow: (
      <>
        两个确定性用例分别模拟「周末安排改变」和「Python 包管理器改变」。
        旧记录写入 <code>valid_at</code> 后，默认检索必须隐藏它；只有显式传入
        <code>include_superseded=true</code> 才能再次取回。2/2 通过，所以是
        100%；它验证的是隔离逻辑，不是模型质量。脚本：
        <code>evals/eval_memory_engine.py</code>。
      </>
    ),
    enHow: (
      <>
        Two deterministic cases model a changed weekend rule and a changed
        Python package-manager preference. Once <code>valid_at</code> closes the
        old row, default retrieval must hide it; only
        <code>include_superseded=true</code> may return it. Both cases pass, so
        this is 100%. It tests isolation logic, not model quality. Script:
        <code>evals/eval_memory_engine.py</code>.
      </>
    ),
  },
  {
    key: "dedupAccuracy",
    zh: "写入去重准确率",
    en: "Write-time dedup accuracy",
    zhHow: (
      <>
        在标注好的「同一事实 / 不同事实」偏好对上跑混淆矩阵。取决于 embedder
        质量，只在真实语义模型下发布。脚本：
        <code>evals/eval_memory_engine.py</code>
      </>
    ),
    enHow: (
      <>
        Confusion matrix over labelled same-fact / different-fact preference
        pairs. Depends on embedding quality, so it is only published from a real
        semantic provider. Script: <code>evals/eval_memory_engine.py</code>
      </>
    ),
  },
  {
    key: "extractionJsonAccuracy",
    zh: "偏好抽取 JSON 合规率",
    en: "Preference-extraction JSON validity",
    zhHow: (
      <>
        「合规」定义为第一次原始回复既能解析成 JSON，又通过 Pydantic 对必填
        字段、类型、枚举与列表长度的校验；修复重试不计。按日期隔离的 45 例
        holdout 中 39 例通过，39/45 = 86.7%。这只衡量输出契约，不代表偏好内容
        一定抽对；内容质量需要单独评测。固定 seed 3407，脚本：
        <code>train/eval_mlx.py</code>。
      </>
    ),
    enHow: (
      <>
        “Valid” means the first raw reply parses as JSON and passes Pydantic
        checks for required fields, types, enums, and list limits; repair attempts
        do not count. The model passed 39 of 45 date-isolated holdout cases:
        86.7%. This measures the output contract, not whether every extracted
        preference is semantically correct; content quality needs a separate
        evaluation. Fixed seed 3407. Script: <code>train/eval_mlx.py</code>.
      </>
    ),
  },
  {
    key: "localExtractionCostDelta",
    zh: "本机 MLX 抽取延迟",
    en: "Local MLX extraction latency",
    zhHow: (
      <>
        <code>train/eval_mlx.py</code> 用 <code>time.perf_counter()</code>
        包住每次本地生成；Qwen2.5-3B-Instruct-4bit + MLX LoRA 在 M1 Pro、
        16GB MacBook Pro 上跑完 45 例共 845.7 秒，平均 18.8 秒/例。它包含生成
        时间，不包含模型首次加载，是这台机器上的延迟，不是通用性能承诺。
      </>
    ),
    enHow: (
      <>
        <code>train/eval_mlx.py</code> wraps every local generation with
        <code>time.perf_counter()</code>. Qwen2.5-3B-Instruct-4bit + MLX LoRA
        completed 45 cases in 845.7 seconds on an M1 Pro, 16GB MacBook Pro:
        18.8 seconds per case. It includes generation but not initial model
        loading, and is a measurement on this machine—not a general SLA.
      </>
    ),
  },
  {
    key: "promptTokenReduction",
    zh: "每轮 prompt token 降幅",
    en: "Per-turn prompt token reduction",
    zhHow: (
      <>
        长对话下，喂结构化摘要 + 实际召回，与重发原始历史的 token 数对比。
        取决于 embedder，只在真实语义模型下发布。脚本：
        <code>evals/eval_memory_engine.py</code>
      </>
    ),
    enHow: (
      <>
        One summary card plus actual recall versus resending raw history.
        Depends on the embedder, so it is only published from a real semantic
        provider. Script: <code>evals/eval_memory_engine.py</code>
      </>
    ),
  },
  {
    key: "cacheCostSaving",
    zh: "语义缓存",
    en: "Semantic cache",
    zhHow: (
      <>
        精确 key 的 LRU + Redis 缓存已启用并继续生效。语义近邻匹配也已实现并
        测量，但测试中无关短问题相似度 0.9992 高于真实同义 0.9064，没有安全
        阈值，因此保持关闭。关掉它、而不是发布一个假节省，这就是结论。
      </>
    ),
    enHow: (
      <>
        Exact-key LRU + Redis caching is on and stays on. Semantic neighbour
        matching is implemented and measured, but an unrelated short-query pair
        scored 0.9992 — above the 0.9064 true paraphrase — so no tested
        threshold was safe and it stays off. Disabling it, rather than
        publishing a false saving, is the result.
      </>
    ),
  },
];

/* --- copy: every user-visible string lives here ------------------------- */

const copy = {
  zh: {
    htmlLang: "zh-CN",
    docTitle: "MindBridge — 让 AI 记得你，也记得你已经改变",
    docDesc:
      "MindBridge 是一款反思型 AI Companion：它用透明、可追溯、能识别变化的 Memory Core，帮助你看见什么没变、什么已经改变。",
    nav: ["在编程 Agent 中使用", "它如何记忆", "实验依据"],
    demo: "连接 Codex 和 Claude Code",
    stage: "MindBridge MCP",
    demoSecondary: "或先看 2 分钟合成演示",
    heroNote: "你的 AI 记得你，也记得你已经改变。",
    title: (
      <>
        让 AI 记得你，
        <br />
        也记得你已经 <em>改变</em>。
      </>
    ),
    lede: "MindBridge 让 Codex 在回答前先查清两件事：应该怎样与你协作，以及哪些关于你的判断已经得到你的确认。每次召回都带记忆 id、时间和有效状态；Memory Core 默认在本地运行。",
    learn: "连接 Codex / Claude Code",
    proofTitle: "一张卡片记录今天；一条时间轴解释变化。",
    proofNote:
      "卡片把一天的工作压缩成可回看的事实，不必每次重放整段 transcript；时间轴保留记忆何时写入、何时失效，让 AI 分得清过去与现在。",
    principlesEyebrow: "为什么这种记忆更可信",
    principlesTitle: "每次召回，都附上三个可检查的状态。",
    principles: [
      ["它是哪条记录？", "回答引用具体 memory id、namespace 与 category，不把模型语气当成证据。", "RECORD"],
      ["它何时成立？", "created_at 说明写入时间，valid_at 说明它是否仍有效。", "TIME"],
      ["后来发生了什么？", "新偏好会取代冲突记录；旧历史保留，但不再代表现在。", "CHANGE"],
    ] as [string, string, string][],
    principleHowLabel: "展开看实现",
    principleHow: [
      "MCP 的 T3 返回 memory id、namespace、category 与日期；T2 另带 generated_by，T1 用 source_key 对应本地日志位置并防止重复摄入。当前 T3 schema 还没有指向原始 transcript turn 的一跳引用，因此这里承诺的是可审计到存储记录，而不是假装已经能一键回到原文。",
      "memory_vectors 每条记录都有 created_at、valid_at 和 superseded_by。正常召回只查询 valid_at IS NULL；只有明确要求 include_superseded 才会带回旧记录。",
      "edit_memory 在同一事务中先写新记录，再给旧记录写 valid_at 与 superseded_by；archive_memory 只关闭 valid_at，不删除行，所以时间线仍能复原。",
    ],
    // phone 1 — daily card
    cardDate: "2026-08-04 · 周二",
    cardMeta: "自动生成",
    cardTitle: "今日记忆卡",
    cardItems: [
      "在 Codex 里过了 3 道 Graph 题，卡在拓扑排序。",
      "用 FastAPI 搭好 /retrieve 雏形，修掉 3 个 500。",
      "凌晨 1:12 还在改架构，比前一天晚了两小时。",
    ],
    cardPillLabel: "新增偏好",
    cardPills: ["Python 用 uv", "周末不排会议", "回答要直接"],
    cardScribble: "今天你自己没写一个字。",
    cardCallout: "每晚一次，自动写好。",
    cardSource: `来自 ${LOCAL_CORPUS.projects} 个项目 · ${LOCAL_CORPUS.transcripts} 份 transcript`,
    // phone 2 — timeline
    timelineTitle: "记忆时间轴",
    timelineRows: [
      ["Python 项目优先用 uv", "created_at 2026-08-04 · 刚写入", 0.98, false],
      ["周末不排会议", "created_at 2026-07-28 · valid_at 未关闭", 0.91, false],
      ["周六早上健身", "created_at 2026-07-19", 0.74, false],
      ["周末可以加班", "已被 2026-07-28 那条取代", 0.29, true],
    ] as [string, string, number, boolean][],
    timelineCallout: "没被提起的记忆会自己变淡。",
    // paths
    flowEyebrow: "技术架构",
    flowTitle: "同一份 transcript，分成可复现的 T2 与可召回的 T3",
    flowNote:
      "T2 不是先压缩后再变成 T3：两者都从 T1 出发。T2 由规则重建事实；本地微调模型提取 T3 候选，再经过 schema、去重与确认边界。点击每个 bullet 查看为什么这样设计。",
    flowStages: [
      {
        tag: "01",
        name: "来源",
        where: "本机磁盘",
        local: true,
        items: [
          "~/.claude/projects/**/*.jsonl",
          "~/.codex/archived_sessions/",
        ],
      },
      {
        tag: "02",
        name: "Path A · 增量解析",
        where: "本机 · 纯 Python，无模型",
        local: true,
        items: [
          "字节游标断点续读",
          "合并同 message.id（修正 2.5x token 重复计数）",
          "密钥脱敏后才落库",
        ],
      },
      {
        tag: "03",
        name: "T1 会话缓冲",
        where: "Postgres",
        local: true,
        items: ["原始轮次 + project / git 分支 / 工具名"],
      },
      {
        tag: "04",
        name: "T2 每日卡",
        where: "本机 · 规则计算",
        local: true,
        items: [
          "从 T1 按整天重建，不依赖增量",
          "计数、时间跨度、工具统计——可复现",
        ],
      },
      {
        tag: "05",
        name: "本地模型抽取",
        where: "Qwen2.5-3B 4-bit · MLX LoRA",
        local: true,
        items: [
          "从 T1 生成叙述与结构化偏好候选",
          "Pydantic 校验模型输出契约",
          "operational 可写入；reflective 必须先确认",
        ],
      },
      {
        tag: "06",
        name: "T3 长期记忆",
        where: "pgvector · 本地 embedding",
        local: true,
        items: [
          "写入前余弦去重（阈值 0.80）",
          "operational / reflective 双通道",
          "召回按 cosine × e^(-λΔt) 时间衰减",
        ],
      },
    ],
    flowStories: [
      [
        "Path A 只读取 Claude Code 与 Codex CLI 已经写在本机的结构化 JSONL，不抓取浏览器历史，也不读取整份 ~/.claude 或 ~/.codex。",
        "来源文件以绝对路径、session 与 source_key 标识；容器只读挂载 transcript 子目录，避免把凭据目录一起暴露。",
      ],
      [
        "每个文件在 ingest_cursors 表保存 bytes_read。下一次运行直接从这个字节偏移继续，不重扫旧内容；若文件还在写，游标停在最后一个未完成 message group 之前。",
        "Claude Code 会把同一 assistant 回复拆成多个 content block，并在每条重复 usage。解析器按 message.id 合并，只计一次 usage；否则 token 会被放大约 2.5 倍。",
        "redaction.py 在任何 transcript 文本落库前遮蔽常见 API key、Bearer、JWT、SECRET= 与 DSN 密码形状，并记录命中次数。",
      ],
      [
        "session_turns 保存 role、content、token_count、project、git_branch、tool_names 与 source_key；source_key 有唯一约束，所以即使游标丢失重读也不会重复写入。",
      ],
      [
        "每日卡不是拿本次增量拼出来，而是按本地日期从 Postgres 重新读取整天 T1；这样晚间增量不会把一张 683-turn 卡缩成 223-turn 卡。",
        "计数、时间跨度、项目、分支与工具统计完全由规则生成；可选模型只在其上增加 narrative，并通过 generated_by 明确标识。",
      ],
      [
        "本地 Qwen2.5-3B-Instruct-4bit + MLX LoRA 读取当天 transcript excerpt，输出 narrative、highlights、open_threads 与 durable preference candidates；它和规则 T2 是两条并行产物。",
        "第一次输出必须解析为 JSON 并通过 Pydantic 的字段、类型、枚举和长度限制；失败才进入带错误信息的 repair，评测中的 86.7% 不包含 repair。",
        "自动抽取只能进入 operational 通道。关于模式、价值或身份的 reflective 内容先留在 Pattern Candidate，必须由用户确认或改写后才能进入 T3。",
      ],
      [
        "不去重会让同一句偏好反复占据 top-k，挤掉别的事实。新陈述先由本地 nomic-embed-text 生成 768 维向量，再用 pgvector 找同 namespace/category 的最近有效记录；cosine ≥ 0.80 时刷新原记录，否则新增。0.80 来自逐条阅读 170 个真实候选，不是通用常数。",
        "operational 保存 AI 应该怎样与你协作；reflective 只保存你确认过的模式、价值、触发因素、策略与身份假设。两者共用存储，但写入权限不同。",
        "召回分数是 cosine × exp(-λΔt)：相关性相同的情况下近期记忆优先；valid_at 已关闭的记录默认不参与，只有审计历史时才显式取回。",
      ],
    ],
    flowConsumers: [
      ["MCP 客户端", "Claude Code / Cursor / VS Code 直接读写同一份记忆"],
      ["日记界面", "/demo 读同一个 API，连不上时降级为示例数据并标明"],
      ["Pattern 循环", "模式候选 → 你确认 → 才写入 reflective 记忆"],
    ],
    flowLocalTag: "本机",
    flowRemoteTag: "会出本机",
    pathsTitle: "两条捕获路径",
    pathsNote: "一条不用你动手，一条让模型自己来。两条写进同一个记忆层。",
    laneA: {
      tag: "路径 A",
      name: "被动日志解析",
      note: "解析器把每个文件读到的字节位置存成 cursor；下一次从该位置继续。仍在写入的最后一组会暂缓，完成后再合并、脱敏并写入 T1。",
      sources: ["~/.claude/projects/**/*.jsonl", "~/.codex/sessions/** + archived_sessions/"],
    },
    laneB: {
      tag: "路径 B",
      name: "主动 MCP 读写",
      note: "挂成标准 MCP server，模型在对话里自己决定何时写入、何时召回；记忆写入有用户确认，支持按 id 精修与归档。",
      sources: [
        "get_daily_card",
        "get_daily_review",
        "review_long_term_memory",
        "temporal_query",
        "upsert_preference",
        "propose_pattern",
        "review_pattern_candidates",
        "resolve_pattern",
        "get_memory_record",
        "archive_memory",
        "edit_memory",
      ],
    },
    laneBClients: "Codex · Claude Desktop · Claude Code · Cursor · VS Code",
    storeTitle: "MindBridge 记忆层",
    storeTiers: [
      ["T1", "Postgres session_turns：原始轮次、项目、分支、工具与 source_key"],
      ["T2", "summary_cards：规则统计 + 标明 generated_by 的可选模型叙述"],
      ["T3", "pgvector memory_vectors：768d embedding、双 namespace、created_at / valid_at"],
    ] as [string, string][],
    outputs: [
      ["每日记忆卡", "不用主动写，翻回上个月也能看见自己的变化。", Book],
      ["跨客户端召回", "在 Claude 里说过的习惯，Cursor 里立刻生效。", PlugsConnected],
    ] as [string, string, typeof Book][],
    localNote:
      "解析与存储都在本机：读你自己机器上的 transcript，写本地 Postgres。",
    extractorNote:
      "本地路径已跑通：Qwen2.5-3B MLX LoRA 在本机把 transcript 抽成 schema JSON，并经同一个 MemoryService 写入 T2/T3。托管抽取仍保留为生成训练数据的显式选项，只有传入 --send-to-provider 才会发送摘录。",
    coverage:
      "覆盖范围说清楚：路径 A 只对有本地结构化日志的工具成立（Claude Code、Codex CLI）；路径 B 对任意 MCP 客户端成立。ChatGPT 与网页版 Claude 的历史没有 API，只能手动导出，不在自动范围内。",
    codexTitle: "安装一次，之后直接在 Codex 里问。",
    codexNote:
      "Codex 与 Claude Code 通过本地 STDIO MCP 调用同一个 MemoryService。先复制安装指令，再复制一个问题；AI 会自己选择函数、返回对应记忆，并在回答里引用 ids。",
    codexSteps: [
      ["01", "把下面的安装指令粘贴进 Codex 或 Claude Code"],
      ["02", "安装完成后，在对应客户端新开一个会话"],
      ["03", "先分开查看 T2 / T3，再按需确认长期写入"],
    ] as [string, string][],
    codexPrompt: "调用 MindBridge 的 get_daily_card 读取最新 T2，再用 review_long_term_memory 读取最新 3 条 T3。分开回答并标注 ids，不要写入。",
    codexInstallPrompt: "Read https://mindbridge.liangyue.site/install.md to install MindBridge locally, connect Codex and Claude Code, and ingest both local transcript sources.",
    codexCopy: "复制安装指令",
    codexCopied: "已复制，粘贴进 Codex",
    promptCopy: "复制 Prompt",
    promptCopied: "已复制",
    metricHow: "展开看怎么测",
    metricClose: "收起说明",
    codexGuide: "查看完整安装与使用指南",
    codexBoundary: "需要本地数据层运行；公开 Companion Loop 使用合成数据，不连接私人记忆。",
    usageEyebrow: "在 Codex / Claude Code 里的六步用法",
    usageTitle: "先看今天，再区分工作偏好与关于自己的假设。",
    usageNote:
      "Daily Review 同时显示 T2、operational T3、reflective T3 与待确认 Pattern Candidate。推断必须先留在候选层；只有用户确认后才能进入 reflective T3。",
    usageItems: [
      [
        "01 · 回顾今天（T2）",
        "读取按天生成的事实、行为观察与未完成事项，不把它们自动当成长期性格。",
        "调用 get_daily_review 读取最新日报，分开显示 T2、两条 T3 和待确认候选。",
      ],
      [
        "02 · 审计长期记忆（T3）",
        "完整查看当前和已失效的长期记忆，而不是只看语义搜索命中的几条。",
        "分别用 namespace=operational 和 namespace=reflective 调用 review_long_term_memory，标注 ids。",
      ],
      [
        "03 · 带着记忆工作",
        "只召回与当前任务有关的 T3，让 Codex 使用偏好时同时交代来源。",
        "先用 temporal_query 查询我的写作偏好，引用 memory ids，然后再修改这篇文章。",
      ],
      [
        "04 · 先提出 Pattern Candidate",
        "至少三条证据、跨两个日期，同时展示反例；候选不是人格事实。",
        "调用 propose_pattern 提出一个关于我如何处理范围不确定性的候选，但不要写入 T3。",
      ],
      [
        "05 · 用户确认后再进入 Reflective T3",
        "确认、改写或拒绝都会留下 receipt；只有确认/改写会创建长期记忆。",
        "先让我查看候选；得到明确决定后再调用 resolve_pattern。",
      ],
      [
        "06 · Memory Garden 精修",
        "先列出记忆让你选择，再查看某条的当前状态；修改和归档都不会删除历史。",
        "先列出所有当前与已失效的长期记忆并标注 ids，让我选择一条。选择后调用 get_memory_record 展示完整记录；未经我明确确认不要修改。确认后再调用 edit_memory 或 archive_memory，并返回新旧 ids。",
      ],
    ] as [string, string, string][],
    laneToolsLink: "看这 11 个工具分别能做什么",
    toolRefTitle: "Agent 在后台如何组合这 11 个函数",
    toolRefNote:
      "上面是你可以直接复制的自然语言 Prompt；这里是 Agent 完成任务时使用的函数地图。平时不需要手动逐个调用。",
    toolRefGroups: [
      ["回顾一天 · 先读事实", [
        ["get_daily_card", "读某一天的 T2 卡片，看那天到底做了什么。"],
        ["get_daily_review", "一次看全：当天 T2、两条 T3 通道，以及待确认的模式候选。"],
      ]],
      ["带着记忆工作 · 找当前上下文", [
        ["review_long_term_memory", "按 namespace 列出长期记忆，不做语义排序，用于完整审计。"],
        ["temporal_query", "按问题召回相关偏好，越近的权重越高。"],
        ["upsert_preference", "存一条持久事实；写入前先和已有记忆去重。"],
      ]],
      ["反思模式 · 候选永远先于结论", [
        ["propose_pattern", "提出一个关于你的假设，先放在 T3 之外的候选层。"],
        ["review_pattern_candidates", "查看候选和它们的证据，不改动 T3。"],
        ["resolve_pattern", "把你的确认 / 改写 / 拒绝落到候选上，只有前两者会进 T3。"],
      ]],
      ["Memory Garden · 查看后再修改", [
        ["get_memory_record", "按 id 精确读一条 T3，用于引用和核对。"],
        ["edit_memory", "用你确认过的措辞替换一条记忆，旧版本仍可追溯。"],
        ["archive_memory", "关闭一条记忆，不删历史。"],
      ]],
    ] as [string, [string, string][]][],
    trustEyebrow: "信任边界",
    trustTitle: "本地优先，不等于含糊其辞。",
    trustNote:
      "我们把默认行为、可选的外部处理和暂未支持的范围放在同一个地方说明。",
    trustItems: [
      ["默认留在本机", "transcript 解析、Postgres / pgvector、Redis 与检索都在你的设备上运行。"],
      ["外部抽取需显式开启", "托管模型只用于可选的训练数据生成；不传 --send-to-provider 就不会发送摘录。"],
      ["公开演示不含真实数据", "Companion Loop 使用合成数据；本机验证结果与公开 demo 明确分开。"],
      ["Codex 原生 Memories 尚未合并", "当前通过 MCP 连接。若要形成单一事实源，需要另做可审计迁移，而不是直接改 ~/.codex/memories/。"],
    ] as [string, string][],
    evidenceEyebrow: "可复现实验",
    evidenceTitle: "只展示能复跑、能解释的结果。",
    evidenceNote:
      "所有数字来自仓库中的 eval 脚本与 results.json。样本小的结果会明确标成 pilot；没有安全阈值的功能就保持关闭。",
    evidenceLink: "查看评测脚本与原始结果",
    finalEyebrow: "把它接进你的工作流",
    finalTitle: "下一次打开 Codex 或 Claude Code，让它先问问：你现在还是这样吗？",
    finalNote:
      "按安装指南启动本地 Memory Core、连接 MCP，然后用一条带 memory ids 的查询检查它记住了什么。",
    finalPrimary: "连接两个编程 Agent",
    finalSecondary: "打开合成演示",
    modelEyebrow: "推荐的记忆架构",
    modelTitle: "MindBridge 做主存储，Codex 通过 MCP 使用。",
    modelNote:
      "这是推荐方向，不是已经完成的 Codex 原生 Memories 替换。先把旧记忆做一次可审计迁移，再关闭原生生成与注入，才能得到逻辑上的单一事实源。",
    modelStatus: "推荐 · 需要一次迁移",
    modelProsLabel: "得到什么",
    modelConsLabel: "接受什么",
    modelPros: [
      "跨 Codex、Claude 与 Cursor 使用同一组 memory ids",
      "偏好变化通过 created_at / valid_at 保留完整历史",
      "查询、写入、去重和权限走同一个 MemoryService",
    ],
    modelCons: [
      "依赖本地 MCP、Postgres、Ollama 等服务可用",
      "旧 Codex Memories 需要一次迁移与去重验证",
      "重要规则仍应保留在 AGENTS.md，而不是只放记忆库",
    ],
    alternativesTitle: "另外两种方式",
    alternatives: [
      [
        "两套存储并存",
        "现在即可",
        "Codex 原生 Memories 与 MindBridge 各自运行。零迁移、保留原生自动注入；代价是重复、冲突和两套来源难以解释。",
      ],
      [
        "单向导入 MindBridge",
        "稳妥过渡",
        "定期把 Codex 生成记忆导入 MindBridge，不回写 Codex 文件。能保留历史并避免循环同步；停用原生记忆前仍然存在两份物理存储。",
      ],
    ] as [string, string, string][],
    modelBoundary:
      "不直接编辑、替换或软链接 ~/.codex/memories/。它是 Codex 管理的生成状态；MindBridge 通过导入器和 MCP 在边界外完成融合。",
    archTitle: "技术结构",
    archNote:
      "Python · FastAPI · Qwen2.5-3B 4-bit + MLX LoRA · MLX-LM · MCP · Postgres/pgvector · Redis · Docker",
    arch: [
      [
        "摄入层",
        [
          <>
            <code>jsonl</code> 解析器：增量读取本地 transcript，按 session 切分
          </>,
          <>
            <code>FastAPI</code> 端点：会话、偏好、召回
          </>,
          <>每晚一次批处理，生成当天的记忆卡</>,
        ],
      ],
      [
        "抽取层",
        [
          <>
            <code>Qwen2.5-3B-Instruct-4bit</code> + MLX LoRA，198 条 fit / 34 条训练侧 validation / 45 条按日期隔离 holdout
          </>,
          <>训练数据由托管 API 生成，再蒸馏到本地</>,
          <>
            推理跑在本地 <code>mlx_lm.server</code>，schema 校验失败才重试
          </>,
        ],
      ],
      [
        "存储层",
        [
          <>
            T1 会话缓冲区 · T2 滚动摘要 · T3 <code>pgvector</code>
          </>,
          <>
            每条记录带 <code>created_at</code> 与 <code>valid_at</code>
          </>,
          <>写入前 cosine 去重，避免同一偏好反复入库</>,
        ],
      ],
      [
        "缓存层",
        [
          <>进程内 LRU + Redis exact-key 缓存挡完全相同的查询</>,
          <>
            语义近邻缓存已实现并测量，但没有安全阈值，默认关闭
          </>,
          <>宁可多查一次向量，也不把另一个问题的答案返回给用户</>,
        ],
      ],
    ] as [string, ReactNode[]][],
    benchEyebrow: "可复现指标",
    benchTitle: (
      <>
        数字来自 evals，
        <br />
        不来自文案。
      </>
    ),
    benchNote:
      "下面每一项都对应 evals/ 里一个可重跑的脚本，结果写进 evals/results.json，这个页面直接读它。没有可复现数字的项，给出实测结论而不是空着 —— 我不会在这里放一个自己复现不出来的数字。",
    pending: "待测量",
    cacheVerdict: "实测不安全 · 已关闭",
    honesty:
      "每一行都对应一个可重跑的脚本与原始输出。语义缓存的结论来自一次真实测量：没有安全阈值，所以保持关闭。",
    bookEyebrow: "开发者预览",
    bookTitle: (
      <>
        预约一次
        <br />
        <em>上手演示</em>。
      </>
    ),
    bookNote:
      "本地 demo 已可运行：启动数据层与 MLX 服务，指向你自己的 transcript，看它生成记忆卡并通过 MCP 召回。留个邮箱预约 walkthrough。",
    emailLabel: "邮箱",
    emailPlaceholder: "you@company.com",
    roleLabel: "你的身份（选填）",
    roles: ["选一个", "招聘方 / 面试官", "工程师", "研究者", "其他"],
    submit: "预约演示",
    submitting: "提交中…",
    ok: "收到，等 demo 可跑我就发到这个邮箱。",
    errEmail: "邮箱格式看起来不对，检查一下？",
    mailtoFallback: "改用邮件发给我",
    errServer: "提交通道还没接上。",
    modes: [
      ["自动生成", "连上本地 agent，不用你主动写。", Sparkle],
      ["MCP 原生", "十一个工具，任意 MCP 客户端接入。", PlugsConnected],
      ["本地优先", "对话与数据库都留在你机器上。", ShieldCheck],
    ] as [string, string, typeof Sparkle][],
    footer: ["源码", "日记界面", "联系"],
  },
  en: {
    htmlLang: "en",
    docTitle: "MindBridge — memory that knows you changed",
    docDesc:
      "A reflective AI companion with a transparent temporal Memory Core: trace what stayed, what changed, and what should no longer define you.",
    nav: ["Use with coding agents", "How memory works", "Evidence"],
    demo: "Connect Codex and Claude Code",
    stage: "MindBridge MCP",
    demoSecondary: "Or watch the 2-minute synthetic demo",
    heroNote: "Your AI should remember you — and remember that you changed.",
    title: (
      <>
        Give AI a memory
        <br />
        that knows you <em>changed</em>.
      </>
    ),
    lede: "MindBridge lets Codex check two things before it answers: how it should work with you, and which conclusions about you have actually been confirmed. Every recall carries a memory id, date, and validity state. The Memory Core runs locally by default.",
    learn: "Connect Codex / Claude Code",
    proofTitle: "One card captures today. One timeline explains change.",
    proofNote:
      "The card compresses a day into reviewable facts, so the full transcript does not need to be replayed. The timeline preserves when each memory was written and invalidated, so AI can tell the past from the present.",
    principlesEyebrow: "What makes this memory trustworthy",
    principlesTitle: "Every recall carries three inspectable states.",
    principles: [
      ["Which record is it?", "The answer cites a memory id, namespace, and category instead of asking you to trust its tone.", "RECORD"],
      ["When was it true?", "created_at records the write; valid_at tells you whether it still applies.", "TIME"],
      ["What changed later?", "A new preference supersedes conflicts. History stays visible without defining the present.", "CHANGE"],
    ] as [string, string, string][],
    principleHowLabel: "See the implementation",
    principleHow: [
      "T3 responses include memory id, namespace, category, and dates; T2 separately labels generated_by, while T1 uses source_key to map local log input and prevent duplicate ingestion. The current T3 schema does not yet carry a direct pointer to the raw transcript turn, so the honest promise is an auditable stored record—not one-click raw provenance.",
      "Every memory_vectors row carries created_at, valid_at, and superseded_by. Normal recall filters to valid_at IS NULL; old rows return only when include_superseded is explicitly requested.",
      "edit_memory inserts the replacement and closes the old row with valid_at plus superseded_by in one transaction. archive_memory stamps valid_at without deleting the row, so the timeline can still be reconstructed.",
    ],
    cardDate: "2026-08-04 · Tue",
    cardMeta: "auto-generated",
    cardTitle: "Today's memory card",
    cardItems: [
      "Three graph problems in Codex; stuck on topological sort.",
      "Stood up /retrieve in FastAPI, cleared three 500s.",
      "Still editing architecture at 1:12am — two hours later than yesterday.",
    ],
    cardPillLabel: "New preferences",
    cardPills: ["uv for Python", "No weekend meetings", "Answer directly"],
    cardScribble: "You wrote none of this yourself.",
    cardCallout: "Written for you, once a night.",
    cardSource: `from ${LOCAL_CORPUS.projects} projects · ${LOCAL_CORPUS.transcripts} transcripts`,
    timelineTitle: "Memory timeline",
    timelineRows: [
      ["Prefer uv for Python projects", "created_at 2026-08-04 · just written", 0.98, false],
      ["No meetings on weekends", "created_at 2026-07-28 · valid_at open", 0.91, false],
      ["Gym on Saturday mornings", "created_at 2026-07-19", 0.74, false],
      ["Open to weekend work", "superseded by the 2026-07-28 record", 0.29, true],
    ] as [string, string, number, boolean][],
    timelineCallout: "Memory you stop mentioning fades on its own.",
    flowEyebrow: "Architecture",
    flowTitle: "One transcript branches into reproducible T2 and recallable T3",
    flowNote:
      "T2 is not compressed and then turned into T3: both start from T1. Rules rebuild T2 facts; the locally fine-tuned model extracts T3 candidates, then schema, dedup, and confirmation boundaries decide what persists. Open any bullet for the engineering story.",
    flowStages: [
      {
        tag: "01",
        name: "Sources",
        where: "local disk",
        local: true,
        items: [
          "~/.claude/projects/**/*.jsonl",
          "~/.codex/archived_sessions/",
        ],
      },
      {
        tag: "02",
        name: "Path A · incremental parse",
        where: "local · plain Python, no model",
        local: true,
        items: [
          "byte cursors resume mid-file",
          "merges records sharing message.id (fixes a 2.5x token overcount)",
          "secrets masked before anything is stored",
        ],
      },
      {
        tag: "03",
        name: "T1 session buffer",
        where: "Postgres",
        local: true,
        items: ["raw turns + project / git branch / tool names"],
      },
      {
        tag: "04",
        name: "T2 day card",
        where: "local · rule-computed",
        local: true,
        items: [
          "rebuilt from T1 over the whole day, never from a delta",
          "counts, spans, tool tallies — reproducible",
        ],
      },
      {
        tag: "05",
        name: "Local model extraction",
        where: "Qwen2.5-3B 4-bit · MLX LoRA",
        local: true,
        items: [
          "narrative plus structured preference candidates from T1",
          "Pydantic validates the model output contract",
          "operational may write; reflective waits for confirmation",
        ],
      },
      {
        tag: "06",
        name: "T3 long-term memory",
        where: "pgvector · local embeddings",
        local: true,
        items: [
          "cosine dedup before write (threshold 0.80)",
          "operational / reflective lanes",
          "recall scored cosine × e^(-λΔt)",
        ],
      },
    ],
    flowStories: [
      [
        "Path A reads structured JSONL already written locally by Claude Code and Codex CLI. It does not scrape browser history or mount the whole ~/.claude or ~/.codex directory.",
        "Each source is identified by absolute path, session, and source_key. Containers mount only transcript subdirectories read-only, keeping credential directories outside the boundary.",
      ],
      [
        "ingest_cursors stores bytes_read per file. The next run seeks directly to that byte offset instead of rescanning old lines; if the file is still streaming, the cursor stops before its unfinished message group.",
        "Claude Code splits one assistant response into content-block records and repeats usage on each. The parser merges matching message.id values and counts usage once; without that merge, tokens were inflated by roughly 2.5×.",
        "redaction.py masks common API-key, Bearer, JWT, SECRET=, and DSN-password shapes before any transcript text reaches Postgres, and records how many matches were removed.",
      ],
      [
        "session_turns stores role, content, token count, project, git branch, tool names, and source_key. A unique source_key makes re-ingestion a no-op even if a cursor is lost.",
      ],
      [
        "A day card is rebuilt from the entire local day in Postgres, not from the latest delta. That prevents an evening increment from shrinking a 683-turn card into a 223-turn card.",
        "Counts, time span, projects, branches, and tool usage are rule-generated. An optional model adds narrative on top and generated_by makes the distinction visible.",
      ],
      [
        "Local Qwen2.5-3B-Instruct-4bit + MLX LoRA reads transcript excerpts and outputs narrative, highlights, open threads, and durable-preference candidates. It is a parallel product of T1, not prose distilled from T2.",
        "The first reply must parse as JSON and pass Pydantic field, type, enum, and length checks. Only failures enter a repair turn; the published 86.7% excludes repairs.",
        "Automatic extraction can write only operational memory. Patterns, values, or identity hypotheses stay as Pattern Candidates until the user confirms or edits their wording.",
      ],
      [
        "Without dedup, repeated wording would fill top-k and crowd out distinct facts. A new statement becomes a local 768-dimensional nomic-embed-text vector; pgvector finds the nearest open row in the same namespace/category. cosine ≥ 0.80 refreshes that row, otherwise a new one is inserted. The 0.80 threshold came from reading 170 real candidates, not from a universal rule.",
        "Operational memory tells AI how to work with you. Reflective memory holds only user-confirmed patterns, values, triggers, strategies, and identity hypotheses. They share storage but not write permissions.",
        "Recall scores cosine × exp(-λΔt): equally relevant recent memories rank above older ones. Rows closed by valid_at are excluded unless an audit explicitly requests history.",
      ],
    ],
    flowConsumers: [
      ["MCP clients", "Claude Code / Cursor / VS Code read and write the same memory"],
      ["Diary UI", "/demo reads the same API; falls back to sample data and says so"],
      ["Pattern loop", "candidate → you confirm → only then a reflective write"],
    ],
    flowLocalTag: "local",
    flowRemoteTag: "leaves machine",
    pathsTitle: "Two capture paths",
    pathsNote:
      "One needs nothing from you. One lets the model do it. Both write to the same store.",
    laneA: {
      tag: "Path A",
      name: "Passive log parsing",
      note: "The parser stores the byte position reached in each file and resumes there next time. A still-streaming final group waits until complete, then gets merged, redacted, and written to T1.",
      sources: ["~/.claude/projects/**/*.jsonl", "~/.codex/sessions/** + archived_sessions/"],
    },
    laneB: {
      tag: "Path B",
      name: "Active MCP read/write",
      note: "Mounted as a standard MCP server, so the model decides mid-conversation when to write, recall, and keep memory edits auditable.",
      sources: [
        "get_daily_card",
        "get_daily_review",
        "review_long_term_memory",
        "temporal_query",
        "upsert_preference",
        "propose_pattern",
        "review_pattern_candidates",
        "resolve_pattern",
        "get_memory_record",
        "archive_memory",
        "edit_memory",
      ],
    },
    laneBClients: "Codex · Claude Desktop · Claude Code · Cursor · VS Code",
    storeTitle: "MindBridge memory layer",
    storeTiers: [
      ["T1", "Postgres session_turns: raw turns, project, branch, tools, and source_key"],
      ["T2", "summary_cards: rule metrics plus optional narrative labelled by generated_by"],
      ["T3", "pgvector memory_vectors: 768d embeddings, two namespaces, created_at / valid_at"],
    ] as [string, string][],
    outputs: [
      ["Daily memory card", "Written for you, and readable a month later.", Book],
      [
        "Cross-client recall",
        "A habit mentioned in Claude applies in Cursor.",
        PlugsConnected,
      ],
    ] as [string, string, typeof Book][],
    localNote:
      "Parsing and storage are local: it reads transcripts on your machine and writes to a local Postgres.",
    extractorNote:
      "The local path now works end to end: a Qwen2.5-3B MLX LoRA model turns transcript excerpts into schema-valid JSON on this Mac, then the shared MemoryService writes T2/T3. Hosted extraction remains an explicit training-data option and sends excerpts only with --send-to-provider.",
    coverage:
      "Stated plainly: Path A only works for tools that already write structured local logs (Claude Code, Codex CLI). Path B works with any MCP client. ChatGPT and web Claude expose no history API — those need a manual export and are out of scope for the automatic path.",
    codexTitle: "Install once, then ask from Codex.",
    codexNote:
      "Codex and Claude Code call the same MemoryService over local STDIO MCP. Copy the install instruction, then copy a question; the agent chooses the function, returns the matching memory, and cites ids in its answer.",
    codexSteps: [
      ["01", "Paste the install instruction below into Codex or Claude Code"],
      ["02", "After setup, open a fresh session in that client"],
      ["03", "Review T2 and T3 separately, then confirm durable writes only when needed"],
    ] as [string, string][],
    codexPrompt: "Call get_daily_card for the latest T2 card, then review_long_term_memory for the newest three T3 records. Answer in separate sections, cite every id, and do not write.",
    codexInstallPrompt: "Read https://mindbridge.liangyue.site/install.md to install MindBridge locally, connect Codex and Claude Code, and ingest both local transcript sources.",
    codexCopy: "Copy install instruction",
    codexCopied: "Copied — paste it into Codex",
    promptCopy: "Copy prompt",
    promptCopied: "Copied",
    metricHow: "See how it was measured",
    metricClose: "Close explanation",
    codexGuide: "Open the full install and usage guide",
    codexBoundary: "The local data layer must be running. The public Companion Loop uses synthetic data and never connects to private memory.",
    usageEyebrow: "SIX STEPS IN CODEX / CLAUDE CODE",
    usageTitle: "Review today, then separate work preferences from hypotheses about yourself.",
    usageNote:
      "Daily Review separates T2, operational T3, reflective T3, and pending Pattern Candidates. An inference stays outside memory until the user confirms its wording.",
    usageItems: [
      [
        "01 · Review today (T2)",
        "Read facts, observed work and open threads from a day card without turning them into durable traits.",
        "Call get_daily_review and show T2, both T3 lanes, and pending candidates separately.",
      ],
      [
        "02 · Audit memory (T3)",
        "List current and superseded long-term records instead of seeing only semantic search hits.",
        "Call review_long_term_memory twice, once per namespace, and cite every id.",
      ],
      [
        "03 · Work with context",
        "Recall only the T3 records relevant to the current task, with provenance attached.",
        "Use temporal_query for my writing preferences, cite the memory ids, then edit this essay.",
      ],
      [
        "04 · Propose a Pattern Candidate",
        "Require three observations across two dates and show counter-evidence. A candidate is not a trait.",
        "Use propose_pattern for a hypothesis about how I respond to unclear scope, but do not write T3.",
      ],
      [
        "05 · Confirm before Reflective T3",
        "Confirm, edit, or reject with a receipt; only confirm/edit creates durable memory.",
        "Show me the candidate first. Call resolve_pattern only after my explicit decision.",
      ],
      [
        "06 · Memory Garden repair path",
        "List memories first, choose one, and inspect its current state. Editing and archiving preserve history.",
        "List every current and superseded long-term memory with ids and let me choose one. Then call get_memory_record for the selected id. Do not modify anything until I explicitly confirm; afterward use edit_memory or archive_memory and return both old and new ids.",
      ],
    ] as [string, string, string][],
    laneToolsLink: "See what each of these 11 tools does",
    toolRefTitle: "How the agent combines the 11 functions",
    toolRefNote:
      "The cards above are natural-language prompts you can copy. This is the function map the agent uses behind the scenes; you do not need to call each tool manually.",
    toolRefGroups: [
      ["REVIEW A DAY · FACTS FIRST", [
        ["get_daily_card", "Read one day's T2 card to see what actually happened."],
        ["get_daily_review", "One day across T2, both T3 lanes, and pending pattern candidates."],
      ]],
      ["WORK WITH MEMORY · FIND CURRENT CONTEXT", [
        ["review_long_term_memory", "List long-term memory by namespace, unranked, for a full audit."],
        ["temporal_query", "Recall preferences relevant to a question, newest weighted heavier."],
        ["upsert_preference", "Store a durable fact, deduplicated against what is already known."],
      ]],
      ["REFLECTIVE PATTERNS · CANDIDATE BEFORE CONCLUSION", [
        ["propose_pattern", "Raise a hypothesis about you outside T3, as a candidate."],
        ["review_pattern_candidates", "Read candidates and their evidence without touching T3."],
        ["resolve_pattern", "Apply your confirm / edit / reject; only the first two reach T3."],
      ]],
      ["MEMORY GARDEN · INSPECT BEFORE MUTATION", [
        ["get_memory_record", "Read one T3 row by id, for citing and checking."],
        ["edit_memory", "Replace a memory with your confirmed wording; history stays."],
        ["archive_memory", "Close a memory without deleting history."],
      ]],
    ] as [string, [string, string][]][],
    trustEyebrow: "Trust boundaries",
    trustTitle: "Local-first should be specific, not vague.",
    trustNote:
      "Default behaviour, optional external processing, and unsupported scope are stated together.",
    trustItems: [
      ["Local by default", "Transcript parsing, Postgres / pgvector, Redis, and retrieval run on your device."],
      ["Hosted extraction is explicit", "A hosted model is optional for training-data generation; no excerpt leaves without --send-to-provider."],
      ["The public demo is synthetic", "Companion Loop contains no personal corpus. Local verification and the public demo are kept distinct."],
      ["Codex native Memories are not merged yet", "Today, Codex connects over MCP. A single source of truth requires an audited migration, not direct edits to ~/.codex/memories/."],
    ] as [string, string][],
    evidenceEyebrow: "Reproducible evidence",
    evidenceTitle: "Only results we can rerun and explain.",
    evidenceNote:
      "Every number comes from eval scripts and results.json in the repository. Small samples stay labelled as pilots; a feature without a safe threshold stays off.",
    evidenceLink: "Inspect eval scripts and raw results",
    finalEyebrow: "Connect your workflow",
    finalTitle: "Next time Codex or Claude Code opens, let it ask: is this still true about you?",
    finalNote:
      "Start the local Memory Core, connect it over MCP, then run one query with memory ids to inspect what it remembers.",
    finalPrimary: "Connect both coding agents",
    finalSecondary: "Open the synthetic demo",
    modelEyebrow: "RECOMMENDED MEMORY MODEL",
    modelTitle: "MindBridge as the source of truth; Codex as an MCP client.",
    modelNote:
      "This is the recommended direction, not a shipped replacement for native Codex Memories. A reviewed one-time import followed by disabling native generation and injection creates one logical source of truth.",
    modelStatus: "RECOMMENDED · ONE MIGRATION REQUIRED",
    modelProsLabel: "What you gain",
    modelConsLabel: "What you accept",
    modelPros: [
      "The same memory ids across Codex, Claude and Cursor",
      "Preference changes retain created_at / valid_at history",
      "Recall, writes, dedup and approvals share one MemoryService",
    ],
    modelCons: [
      "Local MCP, Postgres and Ollama must be available",
      "Existing Codex Memories need a reviewed import and dedup pass",
      "Required rules still belong in AGENTS.md, not only in memory",
    ],
    alternativesTitle: "Two other operating modes",
    alternatives: [
      [
        "Keep both stores",
        "WORKS TODAY",
        "Run native Codex Memories and MindBridge independently. There is no migration and native injection stays available, but duplicates, conflicts and two provenance systems remain.",
      ],
      [
        "One-way import",
        "SAFER TRANSITION",
        "Periodically import generated Codex memories into MindBridge without writing back to Codex files. History is preserved and sync loops are avoided, but two physical stores remain until native memory is disabled.",
      ],
    ] as [string, string, string][],
    modelBoundary:
      "Do not edit, replace or symlink ~/.codex/memories/. It is Codex-managed generated state; MindBridge integrates outside that boundary through an importer and MCP.",
    archTitle: "Architecture",
    archNote:
      "Python · FastAPI · Qwen2.5-3B 4-bit + MLX LoRA · MLX-LM · MCP · Postgres/pgvector · Redis · Docker",
    arch: [
      [
        "Ingest",
        [
          <>
            <code>jsonl</code> parser: incremental reads, split by session
          </>,
          <>
            <code>FastAPI</code> endpoints: sessions, preferences, recall
          </>,
          <>One nightly batch produces that day&apos;s card</>,
        ],
      ],
      [
        "Extraction",
        [
          <>
            <code>Qwen2.5-3B-Instruct-4bit</code> + MLX LoRA on 198 fit / 34 training-side validation / 45 date-isolated holdout pairs
          </>,
          <>Training data generated through a hosted API, then distilled local</>,
          <>
            Inference through local <code>mlx_lm.server</code>, retried only on schema failure
          </>,
        ],
      ],
      [
        "Storage",
        [
          <>
            T1 session buffer · T2 rolling summary · T3 <code>pgvector</code>
          </>,
          <>
            Every record carries <code>created_at</code> and{" "}
            <code>valid_at</code>
          </>,
          <>Cosine dedup before the write keeps one row per preference</>,
        ],
      ],
      [
        "Caching",
        [
          <>In-process LRU + Redis exact-key caching for identical queries</>,
          <>
            Semantic neighbour caching is implemented and measured, but disabled
          </>,
          <>One extra vector search is safer than answering a different question</>,
        ],
      ],
    ] as [string, ReactNode[]][],
    benchEyebrow: "Reproducible metrics",
    benchTitle: (
      <>
        Numbers come from
        <br />
        evals, not copy.
      </>
    ),
    benchNote:
      "Each row maps to a re-runnable script in evals/. Results are written to evals/results.json and this page reads that file. Where no reproducible number exists, the page states the measured conclusion instead of a blank — no number here that I cannot reproduce on request.",
    pending: "in progress",
    cacheVerdict: "tested unsafe · off",
    honesty:
      "Every row maps to a re-runnable script and raw output. The semantic-cache verdict comes from a real measurement: no threshold was safe, so it stays off.",
    bookEyebrow: "Developer preview",
    bookTitle: (
      <>
        Book a
        <br />
        <em>hands-on walkthrough</em>.
      </>
    ),
    bookNote:
      "The local demo now runs: start the data layer and MLX service, point it at your transcripts, watch a card get written and recalled over MCP. Leave an email to book a walkthrough.",
    emailLabel: "Email",
    emailPlaceholder: "you@company.com",
    roleLabel: "Your role (optional)",
    roles: [
      "Pick one",
      "Recruiter / interviewer",
      "Engineer",
      "Researcher",
      "Other",
    ],
    submit: "Book the walkthrough",
    submitting: "Sending…",
    ok: "Got it. I will email this address once the demo is runnable.",
    errEmail: "That email does not look right — mind checking it?",
    mailtoFallback: "Email me instead",
    errServer: "The signup channel is not wired up yet.",
    modes: [
      ["Written for you", "Connect a local agent; stop journalling by hand.", Sparkle],
      ["MCP native", "11 tools, any MCP client.", PlugsConnected],
      ["Local first", "Transcripts and database stay on your machine.", ShieldCheck],
    ] as [string, string, typeof Sparkle][],
    footer: ["Source", "Diary", "Contact"],
  },
};

/* --- pieces ------------------------------------------------------------- */

function GithubButton({ compact = false }: { compact?: boolean }) {
  return (
    <a
      className={`store-button ghost ${compact ? "compact" : ""}`}
      href={GITHUB_URL}
      target="_blank"
      rel="noreferrer"
    >
      <GithubLogo weight="fill" />
      <span>
        <small>github.com</small>
        MindBridge
      </span>
    </a>
  );
}

function CopyPromptButton({
  value,
  idle,
  copied,
}: {
  value: string;
  idle: string;
  copied: string;
}) {
  const [isCopied, setIsCopied] = useState(false);

  async function copyText() {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return;
      } catch {
        // Local HTTP previews may not receive Clipboard API permission.
      }
    }

    const fallback = document.createElement("textarea");
    fallback.value = value;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.appendChild(fallback);
    fallback.select();
    const copiedWithFallback = document.execCommand("copy");
    fallback.remove();
    if (!copiedWithFallback) throw new Error("Clipboard copy failed");
  }

  return (
    <button
      type="button"
      onClick={async () => {
        setIsCopied(true);
        try {
          await copyText();
          window.setTimeout(() => setIsCopied(false), 2200);
        } catch {
          setIsCopied(false);
        }
      }}
    >
      {isCopied ? copied : idle}
    </button>
  );
}

/* --- page --------------------------------------------------------------- */

export function Landing({ locale }: { locale: Locale }) {
  const t = copy[locale];
  const evidenceRows = benchRows.filter((row) =>
    [
      "decayOrdering",
      "supersedeExclusion",
      "extractionJsonAccuracy",
      "localExtractionCostDelta",
      "cacheCostSaving",
    ].includes(row.key),
  );

  useEffect(() => {
    document.documentElement.lang = t.htmlLang;
  }, [t.htmlLang]);

  return (
    <main className="site-page" lang={t.htmlLang}>
      <header className="nav shell">
        <a href="#top" className="brand" aria-label="MindBridge">
          <span className="brand-mark" />
          MindBridge
        </a>
        <nav aria-label={locale === "zh" ? "主导航" : "Main navigation"}>
          <a href="#codex-live">{t.nav[0]}</a>
          <a href="#paths">{t.nav[1]}</a>
          <a href="#metrics">{t.nav[2]}</a>
        </nav>
        <div className="nav-actions">
          <Link className="language" href={locale === "zh" ? "/en" : "/"}>
            {locale === "zh" ? "EN" : "中文"}
          </Link>
          <GithubButton compact />
        </div>
      </header>

      <section id="top" className="hero shell">
        <div className="hero-copy">
          <p className="scribble">{t.heroNote}</p>
          <h1>{t.title}</h1>
          <p className="lede">{t.lede}</p>
          <div className="hero-actions">
            <a className="store-button" href="#codex-live">
              <ArrowRight weight="bold" />
              <span>
                <small>{t.stage}</small>
                {t.demo}
              </span>
            </a>
            <Link
              className="text-link"
              href={locale === "zh" ? "/interview-demo/zh" : "/interview-demo"}
            >
              {t.demoSecondary} <ArrowRight />
            </Link>
          </div>
        </div>

        <div className="hero-showcase">
          <div className="showcase-intro">
            <h2>{t.proofTitle}</h2>
            <p>{t.proofNote}</p>
          </div>
          <div className="phones">
            <div className="phone-slot">
              <div className="phone">
                <div className="speaker" />
                <div className="screen paper">
                  <p className="screen-date">
                    <span>{t.cardDate}</span>
                    <span>{t.cardMeta}</span>
                  </p>
                  <h3>{t.cardTitle}</h3>
                  <ul className="card-list">
                    {t.cardItems.map((item) => (
                      <li key={item}>
                        <Check weight="bold" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="screen-date">
                    <span>{t.cardPillLabel}</span>
                  </p>
                  <div className="pills">
                    {t.cardPills.map((pill) => (
                      <span key={pill}>{pill}</span>
                    ))}
                  </div>
                  <p className="card-scribble">{t.cardScribble}</p>
                  <p className="screen-source">
                    <FileCode weight="fill" />
                    {t.cardSource}
                  </p>
                </div>
              </div>
              <p className="phone-callout">{t.cardCallout}</p>
            </div>

            <div className="phone-slot">
              <div className="phone">
                <div className="speaker" />
                <div className="screen ink">
                  <h3>{t.timelineTitle}</h3>
                  {t.timelineRows.map(([text, meta, weight, faded]) => (
                    <div className={`mem-row ${faded ? "faded" : ""}`} key={text}>
                      <p>{text}</p>
                      <small>{meta}</small>
                      <span className="decay-bar">
                        <i style={{ width: `${weight * 100}%` }} />
                      </span>
                    </div>
                  ))}
                </div>
              </div>
              <p className="phone-callout">{t.timelineCallout}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="codex-live shell" id="codex-live">
        <div className="codex-live-copy">
          <h2>{t.codexTitle}</h2>
          <p>{t.codexNote}</p>
          <a className="text-link codex-guide" href="/install.md" target="_blank">
            {t.codexGuide} <ArrowRight />
          </a>
        </div>
        <div className="codex-terminal">
          <div className="codex-terminal-head">
            <span><PlugsConnected weight="fill" /> Codex + Claude Code × MindBridge</span>
          </div>
          <div className="codex-steps">
            {t.codexSteps.map(([number, step]) => (
              <div key={number}><b>{number}</b><span>{step}</span></div>
            ))}
          </div>
          <div className="codex-install">
            <code>{t.codexInstallPrompt}</code>
            <CopyPromptButton
              value={t.codexInstallPrompt}
              idle={t.codexCopy}
              copied={t.codexCopied}
            />
          </div>
          <div className="codex-install codex-example-prompt">
            <code>{t.codexPrompt}</code>
            <CopyPromptButton
              value={t.codexPrompt}
              idle={t.promptCopy}
              copied={t.promptCopied}
            />
          </div>
          <small>{t.codexBoundary}</small>
        </div>
      </section>

      <section className="codex-usage shell" aria-labelledby="codex-usage-title">
        <div className="how-heading">
          <div>
            <p className="eyebrow">{t.usageEyebrow}</p>
            <h2 id="codex-usage-title">{t.usageTitle}</h2>
          </div>
          <p>{t.usageNote}</p>
        </div>
        <div className="usage-grid">
          {t.usageItems.map(([title, note, prompt], index) => {
            const Icon = [
              Book,
              Path,
              PlugsConnected,
              Sparkle,
              ShieldCheck,
              Check,
            ][index];
            return (
              <article className="usage-card" key={title}>
                <span className="usage-icon"><Icon weight="fill" /></span>
                <h3>{title}</h3>
                <p>{note}</p>
                <div className="usage-prompt">
                  <code>{prompt}</code>
                  <CopyPromptButton
                    value={prompt}
                    idle={t.promptCopy}
                    copied={t.promptCopied}
                  />
                </div>
              </article>
            );
          })}
        </div>
        <div className="tool-ref" id="tools">
          <div className="tool-ref-head">
            <h3>{t.toolRefTitle}</h3>
            <p>{t.toolRefNote}</p>
          </div>
          <div className="tool-ref-grid">
            {t.toolRefGroups.map(([group, tools], index) => (
              <details className="tool-ref-group" key={group} open={index === 0}>
                <summary>{group}</summary>
                <dl>
                  {tools.map(([name, what]) => (
                    <div key={name}>
                      <dt><code>{name}</code></dt>
                      <dd>{what}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="principles shell" aria-labelledby="principles-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t.principlesEyebrow}</p>
            <h2 id="principles-title">{t.principlesTitle}</h2>
          </div>
        </div>
        <div className="principles-grid">
          {t.principles.map(([title, note, label], index) => (
            <article key={title}>
              <span>0{index + 1} · {label}</span>
              <h3>{title}</h3>
              <p>{note}</p>
              <details className="principle-how">
                <summary>{t.principleHowLabel}</summary>
                <p>{t.principleHow[index]}</p>
              </details>
            </article>
          ))}
        </div>
      </section>

      <section id="paths" className="paths shell">
        <div className="how-heading">
          <p className="eyebrow">{t.pathsTitle}</p>
          <p>{t.pathsNote}</p>
        </div>

        <div className="paths-grid">
          <div className="lane-stack">
            {[t.laneA, t.laneB].map((lane, index) => (
              <div className="lane" key={lane.tag}>
                <div className="lane-head">
                  {index === 0 ? <FileCode weight="fill" /> : <PlugsConnected weight="fill" />}
                  <strong>{lane.name}</strong>
                  <em>{lane.tag}</em>
                </div>
                <p>{lane.note}</p>
                <div className="lane-sources">
                  {lane.sources.map((source) => <code key={source}>{source}</code>)}
                  {index === 1 && <code>{t.laneBClients}</code>}
                </div>
                {index === 1 && (
                  <a className="text-link lane-tools-link" href="#tools">
                    {t.laneToolsLink} <ArrowRight />
                  </a>
                )}
              </div>
            ))}
          </div>
          <div className="merge" aria-hidden="true"><Path weight="bold" /></div>
          <div className="store">
            <strong>{t.storeTitle}</strong>
            {t.storeTiers.map(([tier, label]) => (
              <span className="store-tier" key={tier}><b>{tier}</b>{label}</span>
            ))}
          </div>
          <div className="merge" aria-hidden="true"><CaretRight weight="bold" /></div>
          <div className="outputs">
            {t.outputs.map(([title, note, Icon]) => (
              <div className="output" key={title}>
                <div className="lane-head"><Icon weight="fill" /><strong>{title}</strong></div>
                <small>{note}</small>
              </div>
            ))}
          </div>
        </div>
        <div className="path-notes">
          <p className="local-note"><ShieldCheck weight="bold" />{t.localNote}</p>
          <p className="local-note"><Laptop weight="bold" />{t.coverage}</p>
        </div>
      </section>

      <section id="architecture" className="archflow shell" aria-labelledby="arch-title">
        <div className="how-heading">
          <p className="eyebrow">{t.flowEyebrow}</p>
          <p>{t.flowNote}</p>
        </div>
        <h2 id="arch-title" className="archflow-title">{t.flowTitle}</h2>

        <ol className="archflow-stages">
          {t.flowStages.map((stage, stageIndex) => (
            <li className={`archflow-stage ${stage.local ? "is-local" : "is-remote"}`} key={stage.tag}>
              <div className="archflow-stage-head">
                <b>{stage.tag}</b>
                <strong>{stage.name}</strong>
                <span className={`archflow-where ${stage.local ? "" : "remote"}`}>
                  {stage.local ? t.flowLocalTag : t.flowRemoteTag}
                </span>
              </div>
              <p className="archflow-where-detail">{stage.where}</p>
              <ul>
                {stage.items.map((item, itemIndex) => (
                  <li key={item}>
                    <details className="archflow-story">
                      <summary>{item}</summary>
                      <p>{t.flowStories[stageIndex][itemIndex]}</p>
                    </details>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>

        <div className="archflow-consumers">
          {t.flowConsumers.map(([title, note]) => (
            <div className="archflow-consumer" key={title}>
              <strong>{title}</strong>
              <small>{note}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="trust shell" aria-labelledby="trust-title">
        <div className="section-heading split">
          <div>
            <p className="eyebrow">{t.trustEyebrow}</p>
            <h2 id="trust-title">{t.trustTitle}</h2>
          </div>
          <p>{t.trustNote}</p>
        </div>
        <div className="trust-grid">
          {t.trustItems.map(([title, note], index) => (
            <article key={title}>
              {index === 0 ? <ShieldCheck weight="fill" /> : <Warning weight="fill" />}
              <div><h3>{title}</h3><p>{note}</p></div>
            </article>
          ))}
        </div>
      </section>

      <section id="metrics" className="evidence shell">
        <div className="section-heading split">
          <div>
            <p className="eyebrow">{t.evidenceEyebrow}</p>
            <h2>{t.evidenceTitle}</h2>
          </div>
          <p>{t.evidenceNote}</p>
        </div>
        <div className="evidence-grid">
          {evidenceRows.map((row) => (
            <details className={row.key === "cacheCostSaving" ? "evidence-card evidence-off" : "evidence-card"} key={row.key}>
              <summary>
                <strong>
                  {row.key === "cacheCostSaving"
                    ? t.cacheVerdict
                    : results.metrics[row.key] ?? t.pending}
                </strong>
                <span>{row[locale]}</span>
                <small className="evidence-open-label">{t.metricHow}</small>
                <small className="evidence-close-label">{t.metricClose}</small>
              </summary>
              <div className="evidence-how">
                {locale === "zh" ? row.zhHow : row.enHow}
              </div>
            </details>
          ))}
        </div>
        <a className="text-link evidence-link" href={`${GITHUB_URL}/tree/main/evals`} target="_blank" rel="noreferrer">
          {t.evidenceLink} <ArrowRight />
        </a>
      </section>

      <section className="final-cta shell">
        <div>
          <p className="eyebrow">{t.finalEyebrow}</p>
          <h2>{t.finalTitle}</h2>
          <p>{t.finalNote}</p>
        </div>
        <div className="final-actions">
          <a className="store-button" href="/install.md" target="_blank">
            <PlugsConnected weight="fill" />
            <span><small>MindBridge MCP</small>{t.finalPrimary}</span>
          </a>
          <Link className="text-link" href={locale === "zh" ? "/interview-demo/zh" : "/interview-demo"}>
            {t.finalSecondary} <ArrowRight />
          </Link>
        </div>
      </section>

      <footer className="site-footer shell">
        <GithubButton />
        <div>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">
            {t.footer[0]}
          </a>
          <Link href="/demo">{t.footer[1]}</Link>
          <a href={`mailto:${CONTACT_EMAIL}`}>{t.footer[2]}</a>
        </div>
      </footer>
    </main>
  );
}
