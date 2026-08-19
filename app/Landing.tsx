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
        被取代的记录必须从默认检索里消失，同时在显式要求时仍能取回 ——
        valid_at 关闭而不删除。与 embedder 无关。脚本：
        <code>evals/eval_memory_engine.py</code>
      </>
    ),
    enHow: (
      <>
        A superseded record must vanish from default retrieval yet still return
        when explicitly requested — valid_at closes it without deleting it.
        Embedder-independent. Script: <code>evals/eval_memory_engine.py</code>
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
        Qwen2.5-3B MLX LoRA 在按日期隔离的 45 例 holdout 上，首次回复即通过
        schema 校验的比例（重试不计入）：39/45 = 86.7%，对比 teacher 基线
        37/45 = 82.2%。固定 seed 3407 的本机 pilot；样本 45 例、仅领先 2 例，
        是可复现的初步结果，不构成统计显著。adapter 与训练数据为私有本机产物。
        脚本：<code>train/eval_mlx.py</code>。
      </>
    ),
    enHow: (
      <>
        Share of 45 date-isolated holdout pairs where the Qwen2.5-3B MLX LoRA
        model passes schema validation on its FIRST reply; repairs excluded:
        39/45 = 86.7%, vs a 37/45 = 82.2% teacher baseline. Fixed-seed (3407)
        local pilot — at 45 pairs and a two-case margin it is a reproducible
        preliminary result, not a significance claim. Adapter and training data
        remain private local artifacts. Script: <code>train/eval_mlx.py</code>.
      </>
    ),
  },
  {
    key: "localExtractionCostDelta",
    zh: "本机 MLX 抽取延迟",
    en: "Local MLX extraction latency",
    zhHow: (
      <>
        Qwen2.5-3B-Instruct-4bit 在 Apple silicon 本机跑完 45 例 holdout 共
        845.7s，平均约 18.8s/例。这是延迟实测；在确定可部署 serving target
        前，不把它包装成 API 成本节省。
      </>
    ),
    enHow: (
      <>
        The Qwen2.5-3B-Instruct-4bit MLX service runs the 45-pair holdout on
        Apple silicon in 845.7s, ~18.8s per pair. A measured latency figure —
        not dressed up as an API cost saving until a deployable serving target
        exists.
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
    nav: ["在 Codex 中使用", "它如何记忆", "实验依据"],
    demo: "试试 Companion Loop",
    stage: "2 分钟可点击合成演示",
    heroNote: "你的 AI 记得你，也记得你已经改变。",
    title: (
      <>
        让 AI 记得你，
        <br />
        也记得你已经 <em>改变</em>。
      </>
    ),
    lede: "MindBridge 为 Codex 等 AI 工具提供一层可追溯的长期记忆。每次召回都带着来源和时间；偏好改变时，旧记录不会继续冒充现在的你。Memory Core 默认在本地运行。",
    learn: "在 Codex 中使用",
    proofEyebrow: "先看见产品，再理解架构",
    proofTitle: "一张卡片记录今天；一条时间轴解释变化。",
    proofNote:
      "MindBridge 把零散对话整理成可以回看、召回和审计的记忆，而不是把整段聊天永久塞回上下文。",
    principlesEyebrow: "为什么这种记忆更可信",
    principlesTitle: "每次召回，都回答三个问题。",
    principles: [
      ["它从哪里来？", "返回 memory id 与来源记录，你能追到原始证据。", "SOURCE"],
      ["它何时成立？", "created_at 说明写入时间，valid_at 说明它是否仍有效。", "TIME"],
      ["后来发生了什么？", "新偏好会取代冲突记录；旧历史保留，但不再代表现在。", "CHANGE"],
    ] as [string, string, string][],
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
    pathsTitle: "两条捕获路径",
    pathsNote: "一条不用你动手，一条让模型自己来。两条写进同一个记忆层。",
    laneA: {
      tag: "路径 A",
      status: "已可用",
      built: true,
      name: "被动日志解析",
      note: "不需要你做任何事。增量解析本地结构化日志，按天算出真正发生了什么；写入前先遮蔽疑似密钥。",
      sources: ["~/.claude/projects/**/*.jsonl", "~/.codex/archived_sessions/"],
    },
    laneB: {
      tag: "路径 B",
      status: "已可用",
      built: true,
      name: "主动 MCP 读写",
      note: "挂成标准 MCP server，模型在对话里自己决定何时写入、何时召回。",
      sources: ["get_daily_card", "review_long_term_memory", "temporal_query", "upsert_preference"],
    },
    laneBClients: "Codex · Claude Desktop · Claude Code · Cursor · VS Code",
    storeTitle: "MindBridge 记忆层",
    storeTiers: [
      ["T1", "会话缓冲区 — 今天的原始轮次"],
      ["T2", "滚动摘要 — 按天归档的记忆卡"],
      ["T3", "pgvector — 带时间衰减的长期偏好"],
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
    codexEyebrow: "已接入真实客户端",
    codexTitle: "不只展示页面。MindBridge 已经跑在 Codex 里。",
    codexNote:
      "Codex 通过本地 STDIO MCP 启动 MindBridge，实时调用同一个 MemoryService：查询可以直接执行，长期写入需要用户确认。数据层是本机 Postgres / pgvector、Redis 与 Ollama。",
    codexProof: "2026-08-19 本机验证：新 Codex 会话分别读取真实 T2 日卡 [294] 与 T3 记忆 [329] [328] [327]，全程没有写入。",
    codexSteps: [
      ["01", "把下面的安装指令粘贴进 Codex"],
      ["02", "Codex 完成本地安装后，新开一个会话"],
      ["03", "先分开查看 T2 / T3，再按需确认长期写入"],
    ] as [string, string][],
    codexPrompt: "调用 MindBridge 的 get_daily_card 读取最新 T2，再用 review_long_term_memory 读取最新 3 条 T3。分开回答并标注 ids，不要写入。",
    codexInstallPrompt: "Read https://mindbridge.liangyue.site/install.md to install MindBridge locally and connect it to this Codex client.",
    codexCopy: "复制安装指令",
    codexCopied: "已复制，粘贴进 Codex",
    codexGuide: "查看完整安装与使用指南",
    codexBoundary: "本机验证，不是公网托管服务；需要本地数据层运行。公开 Companion Loop 使用合成数据。",
    usageEyebrow: "在 Codex 里的四步用法",
    usageTitle: "先看今天，再决定什么值得长期记住。",
    usageNote:
      "T2 回答“我今天做了什么”，T3 回答“AI 以后应该怎样理解我”。两个只读入口分开显示；只有最后一步会请求写入确认。",
    usageItems: [
      [
        "01 · 回顾今天（T2）",
        "读取按天生成的事实、行为观察与未完成事项，不把它们自动当成长期性格。",
        "调用 get_daily_card 读取最新 T2 日卡，标注 card id，并总结完成事项和 open threads。",
      ],
      [
        "02 · 审计长期记忆（T3）",
        "完整查看当前和已失效的长期记忆，而不是只看语义搜索命中的几条。",
        "调用 review_long_term_memory 读取最新 10 条 T3，区分 current 与 superseded，并标注 ids。",
      ],
      [
        "03 · 带着记忆工作",
        "只召回与当前任务有关的 T3，让 Codex 使用偏好时同时交代来源。",
        "先用 temporal_query 查询我的写作偏好，引用 memory ids，然后再修改这篇文章。",
      ],
      [
        "04 · 确认后再记住",
        "一次性指令留在当前任务；只有未来仍有用的稳定偏好才进入 T3。",
        "调用 upsert_preference 记住：技术解释默认先给直观例子。写入前先让我确认。",
      ],
    ] as [string, string, string][],
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
    finalTitle: "下一次打开 Codex，让它先问问：你现在还是这样吗？",
    finalNote:
      "按安装指南启动本地 Memory Core、连接 MCP，然后用一条带 memory ids 的查询检查它记住了什么。",
    finalPrimary: "在 Codex 中安装",
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
      ["MCP 原生", "两个 tool，任意客户端接入。", PlugsConnected],
      ["本地优先", "对话与数据库都留在你机器上。", ShieldCheck],
    ] as [string, string, typeof Sparkle][],
    footer: ["源码", "日记界面", "联系"],
  },
  en: {
    htmlLang: "en",
    docTitle: "MindBridge — memory that knows you changed",
    docDesc:
      "A reflective AI companion with a transparent temporal Memory Core: trace what stayed, what changed, and what should no longer define you.",
    nav: ["Use with Codex", "How memory works", "Evidence"],
    demo: "Try the Companion Loop",
    stage: "2-minute interactive synthetic demo",
    heroNote: "Your AI should remember you — and remember that you changed.",
    title: (
      <>
        Give AI a memory
        <br />
        that knows you <em>changed</em>.
      </>
    ),
    lede: "MindBridge gives Codex and other AI tools a traceable long-term memory. Every recall carries its source and date. When a preference changes, an old record no longer pretends to be the current you. The Memory Core runs locally by default.",
    learn: "Use it with Codex",
    proofEyebrow: "See the product before the architecture",
    proofTitle: "One card captures today. One timeline explains change.",
    proofNote:
      "MindBridge turns scattered conversations into memory you can revisit, recall, and audit — without stuffing the full chat history back into every prompt.",
    principlesEyebrow: "What makes this memory trustworthy",
    principlesTitle: "Every recall answers three questions.",
    principles: [
      ["Where did it come from?", "Memory ids and source records lead you back to the evidence.", "SOURCE"],
      ["When was it true?", "created_at records the write; valid_at tells you whether it still applies.", "TIME"],
      ["What changed later?", "A new preference supersedes conflicts. History stays visible without defining the present.", "CHANGE"],
    ] as [string, string, string][],
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
    pathsTitle: "Two capture paths",
    pathsNote:
      "One needs nothing from you. One lets the model do it. Both write to the same store.",
    laneA: {
      tag: "Path A",
      status: "working today",
      built: true,
      name: "Passive log parsing",
      note: "Nothing for you to do. An incremental pass reads the structured logs already on disk and computes what happened, masking suspected secrets before storing.",
      sources: ["~/.claude/projects/**/*.jsonl", "~/.codex/archived_sessions/"],
    },
    laneB: {
      tag: "Path B",
      status: "working today",
      built: true,
      name: "Active MCP read/write",
      note: "Mounted as a standard MCP server, so the model decides mid-conversation when to write and when to recall.",
      sources: ["get_daily_card", "review_long_term_memory", "temporal_query", "upsert_preference"],
    },
    laneBClients: "Codex · Claude Desktop · Claude Code · Cursor · VS Code",
    storeTitle: "MindBridge memory layer",
    storeTiers: [
      ["T1", "Session buffer — today's raw turns"],
      ["T2", "Rolling summary — one card per day"],
      ["T3", "pgvector — long-term, time-decayed"],
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
    codexEyebrow: "VERIFIED IN A REAL CLIENT",
    codexTitle: "Not just a web mockup. MindBridge now runs inside Codex.",
    codexNote:
      "Codex starts MindBridge over local STDIO MCP and calls the same MemoryService in real time: reads can run directly, while durable writes require user confirmation. The data layer is local Postgres / pgvector, Redis and Ollama.",
    codexProof: "Local verification on Aug 19, 2026: a fresh Codex session read real T2 card [294] and T3 memories [329] [328] [327] separately, without writing anything.",
    codexSteps: [
      ["01", "Paste the install instruction below into Codex"],
      ["02", "After local setup finishes, open a fresh session"],
      ["03", "Review T2 and T3 separately, then confirm durable writes only when needed"],
    ] as [string, string][],
    codexPrompt: "Call get_daily_card for the latest T2 card, then review_long_term_memory for the newest three T3 records. Answer in separate sections, cite every id, and do not write.",
    codexInstallPrompt: "Read https://mindbridge.liangyue.site/install.md to install MindBridge locally and connect it to this Codex client.",
    codexCopy: "Copy install instruction",
    codexCopied: "Copied — paste it into Codex",
    codexGuide: "Open the full install and usage guide",
    codexBoundary: "Verified locally, not a public hosted memory service; the local data layer must be running. The public Companion Loop uses synthetic data.",
    usageEyebrow: "FOUR STEPS INSIDE CODEX",
    usageTitle: "Review today before deciding what should last.",
    usageNote:
      "T2 answers “what did I do today?” T3 answers “how should AI understand me later?” The read paths stay separate; only the final step asks for write approval.",
    usageItems: [
      [
        "01 · Review today (T2)",
        "Read facts, observed work and open threads from a day card without turning them into durable traits.",
        "Call get_daily_card for the latest T2 card. Cite the card id and summarize completed work and open threads.",
      ],
      [
        "02 · Audit memory (T3)",
        "List current and superseded long-term records instead of seeing only semantic search hits.",
        "Call review_long_term_memory for the newest ten T3 records. Separate current from superseded and cite every id.",
      ],
      [
        "03 · Work with context",
        "Recall only the T3 records relevant to the current task, with provenance attached.",
        "Use temporal_query for my writing preferences, cite the memory ids, then edit this essay.",
      ],
      [
        "04 · Confirm before saving",
        "Keep one-off instructions in the task; write only stable preferences that should matter again.",
        "Use upsert_preference to remember: start technical explanations with a concrete example. Ask me before writing.",
      ],
    ] as [string, string, string][],
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
    finalTitle: "Next time Codex opens, let it ask: is this still true about you?",
    finalNote:
      "Start the local Memory Core, connect it over MCP, then run one query with memory ids to inspect what it remembers.",
    finalPrimary: "Install for Codex",
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
      ["MCP native", "Two tools, any client.", PlugsConnected],
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

/* --- page --------------------------------------------------------------- */

export function Landing({ locale }: { locale: Locale }) {
  const [installCopied, setInstallCopied] = useState(false);
  const t = copy[locale];
  const evidenceRows = benchRows.filter((row) =>
    [
      "decayOrdering",
      "supersedeExclusion",
      "extractionJsonAccuracy",
      "localExtractionCostDelta",
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
            <Link className="store-button" href={locale === "zh" ? "/interview-demo/zh" : "/interview-demo"}>
              <ArrowRight weight="bold" />
              <span>
                <small>{t.stage}</small>
                {t.demo}
              </span>
            </Link>
            <a className="text-link" href="#codex-live">
              {t.learn} <ArrowRight />
            </a>
          </div>
        </div>

        <div className="hero-showcase">
          <div className="showcase-intro">
            <p className="eyebrow">{t.proofEyebrow}</p>
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
          <p className="eyebrow">{t.codexEyebrow}</p>
          <h2>{t.codexTitle}</h2>
          <p>{t.codexNote}</p>
          <span className="codex-proof">
            <Check weight="bold" />
            {t.codexProof}
          </span>
          <a className="text-link codex-guide" href="/install.md" target="_blank">
            {t.codexGuide} <ArrowRight />
          </a>
        </div>
        <div className="codex-terminal">
          <div className="codex-terminal-head">
            <span><PlugsConnected weight="fill" /> Codex × MindBridge</span>
            <em>{locale === "zh" ? "本地已验证" : "LOCALLY VERIFIED"}</em>
          </div>
          <div className="codex-steps">
            {t.codexSteps.map(([number, step]) => (
              <div key={number}><b>{number}</b><span>{step}</span></div>
            ))}
          </div>
          <div className="codex-install">
            <code>{t.codexInstallPrompt}</code>
            <button
              type="button"
              onClick={async () => {
                await navigator.clipboard.writeText(t.codexInstallPrompt);
                setInstallCopied(true);
                window.setTimeout(() => setInstallCopied(false), 2200);
              }}
            >
              {installCopied ? t.codexCopied : t.codexCopy}
            </button>
          </div>
          <code className="codex-prompt">{t.codexPrompt}</code>
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
            const Icon = [Book, Sparkle, Path, ShieldCheck][index];
            return (
              <article className="usage-card" key={title}>
                <span className="usage-icon"><Icon weight="fill" /></span>
                <h3>{title}</h3>
                <p>{note}</p>
                <code>{prompt}</code>
              </article>
            );
          })}
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
                  <span className={`lane-status ${lane.built ? "built" : ""}`}>
                    {lane.status}
                  </span>
                </div>
                <p>{lane.note}</p>
                <div className="lane-sources">
                  {lane.sources.map((source) => <code key={source}>{source}</code>)}
                  {index === 1 && <code>{t.laneBClients}</code>}
                </div>
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
            <article key={row.key}>
              <strong>{results.metrics[row.key] ?? t.pending}</strong>
              <span>{row[locale]}</span>
            </article>
          ))}
          <article className="evidence-off">
            <strong>{t.cacheVerdict}</strong>
            <span>{locale === "zh" ? "语义近邻缓存" : "Semantic neighbour cache"}</span>
          </article>
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
