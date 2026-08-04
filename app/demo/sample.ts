/**
 * Fallback data for the diary, used only when the backend is unreachable — on
 * the deployed site, or before `docker compose up -d api`. The UI labels it as
 * sample data whenever it renders from here.
 */

export type SampleMemory = {
  id: string;
  zh: string;
  en: string;
  createdAt: string;
  weight: number;
  supersededBy?: string;
};

export type SampleDay = {
  date: string;
  zhSummary: string;
  enSummary: string;
  zhItems: string[];
  enItems: string[];
  wrote: string[];
  zhBehaviour: string;
  enBehaviour: string;
  behaviourMeta: string;
  zhScribble: string;
  enScribble: string;
  turns: { tool: string; text: string }[];
  facts: string[];
  sources: string;
};

export const SAMPLE_MEMORIES: SampleMemory[] = [
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

export const SAMPLE_DAYS: SampleDay[] = [
  {
    date: "2026-08-04",
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
    enBehaviour:
      "No meetings booked this weekend — matches the stored preference.",
    behaviourMeta: "meetings 0 · matches m_0091",
    zhScribble: "休息也是一条记录。",
    enScribble: "Rest is a record too.",
    turns: [
      { tool: "claude-desktop", text: "帮我总结这篇 MemGPT 的核心思路。" },
    ],
    facts: ["活动：阅读，无代码提交 / reading, no commits"],
    sources: "1 session · 1 tool · 8 turns",
  },
];
