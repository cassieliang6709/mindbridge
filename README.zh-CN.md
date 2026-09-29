# MindBridge

[English README](README.md)

一个由透明的时间型记忆核心（Temporal Memory Core）驱动的反思型 AI 伴侣。它帮助一个人看见：哪些东西一直保留着，哪些已经改变，哪些不应再继续定义自己。它的技术目标不是把一个人压平成没有时间维度的向量片段，而是保留记忆的时间和有效状态，并让来源最终可以被审计。

当前已经实现的后端会解析本地 AI 编程工具的对话日志，生成每日记忆卡，并通过 MCP 把同一套 T1/T2/T3 记忆提供给不同客户端。公开的 Companion Loop 是一个确定性的合成数据产品 Demo，不连接 Cassie 的私人数据库。解析、存储和 MLX 抽取路径都留在用户本机；只有用于生成训练数据的托管模型抽取是显式 opt-in。

[线上网站](https://mindbridge.liangyue.site) ·
[日记 Demo](https://mindbridge.liangyue.site/demo) ·
[面试 Demo](https://mindbridge.liangyue.site/interview-demo/zh)

## 两分钟面试演示

无需登录，直接打开 **[Companion Loop](https://mindbridge.liangyue.site/interview-demo/zh)**（[English](https://mindbridge.liangyue.site/interview-demo)）：

1. 从 Day 01 点到 Day 04，观察产品身份如何从“记忆工具”变成“反思型 AI 伴侣”。
2. 注意：不确定的推断只会成为 candidate，不会被静默写成用户事实；只有用户显式确认后，新身份才会 supersede 旧身份。
3. 在 **Memory Receipt** 中依次询问 MindBridge 现在是什么、发生了什么变化、下一步该做什么。每个回答都会暴露当前与已失效的证据；最后的建议明确标记为系统推断。

页面上所有可见数据都是合成且确定性的。这个页面安全地展示产品行为；仓库里则包含真实的时间记忆、REST、MCP、日志摄取和本地 MLX 实现。

## 两条记忆采集路径

**Path A - 被动日志解析。** 用户不需要主动操作。后台任务读取本地已经存在的结构化日志，提取当天发生的事情。它只适用于会写出这类日志的工具：

- `~/.claude/projects/**/*.jsonl`（Claude Code）
- `~/.codex/sessions/**/rollout-*.jsonl` 与 `~/.codex/archived_sessions/rollout-*.jsonl`（当前和已归档的 Codex CLI 会话）

**Path B - 主动 MCP 读写。** MindBridge 作为标准 MCP server 挂载，提供覆盖全部 review layer 的 `get_daily_review`、分别读取 T2/T3 的工具、需要确认的长期写入，以及 Pattern Candidate 审核闭环。任何 MCP 客户端都可以使用，包括 Codex、Claude Desktop、Claude Code、Cursor 和 VS Code。

ChatGPT 与网页版 Claude 没有开放历史记录 API，因此需要手动导出，不属于自动采集路径的覆盖范围。落地页会明确说明这个边界，而不是暗示所有聊天工具都能自动接入。

## 三层记忆

| 层级 | 保存内容 | 实现 |
| --- | --- | --- |
| T1 | 当天的原始对话轮次 | 进程内 session buffer + Postgres 持久化 |
| T2 | 每天一张结构化记忆卡 | rolling summary |
| T3 | 带时间衰减的长期操作记忆与反思记忆 | Postgres + pgvector |

每条 T3 记录都有 `created_at` 和 `valid_at`。被新偏好替代的旧偏好不会被静默覆盖，而是逐渐退出默认召回。写入前先做余弦相似度检查，使同一偏好尽量保持为同一条记录。

> 当前 provenance 边界：T1 保存稳定的 `source_key`；模型抽取出的长期记忆建议先进入 `memory_candidates`，并保留 `source_summary_id`、evidence、模型、prompt 版本和尝试次数。候选确认后通过 `confirmed_memory_id` 关联 T3，因此可以从已确认记忆反查到候选和 T2 卡片；目前还没有从 T3 直接指向单条 T1 turn 的外键。

T3 有两个明确的 namespace。**Operational** 记忆告诉 Agent 应该如何与用户协作，包括 `coding_style`、`tool_preference`、`behavioral_fact`、`schedule` 和 `other`。**Reflective** 记忆保存用户亲自审核过的表述，例如重复模式、价值观、触发因素、有效策略或身份假设。若没有 `confirmed_by_user=true`，reflective 写入会直接校验失败；自动日志抽取也不直接写 operational T3，而是先放进 Memory Inbox，只有明确保留或编辑后才进入长期记忆。因此，类似 MBTI 的标签可以作为带日期和来源的假设被提出，但不会被静默存成关于一个人的事实。

### Memory Garden：审计与受控修改

MindBridge 为人提供了一组刻意收窄的“Memory Garden”操作：

- `GET /memories/{memory_id}` 按 id 读取一条记忆及其当前衰减状态，让用户精确检查系统现在主张什么、哪些内容已经关闭。
- `PATCH /memories/{memory_id}` 每次只执行一个明确动作：
  - `{"action":"archive"}`：关闭一条记忆，但不创建替代项。
  - `{"action":"edit", "content":"..."}`：创建一条替代记忆，并 supersede 旧记录以保留来源链。

这两个操作都不会破坏历史。关闭的记录仍可查询；替代关系可以通过 `superseded_by` 审计。它表达的是“什么发生了改变”，而不是静默改写关于用户的描述。

T1 还会保存每个 turn 对应的 project、git branch 和 tool names。这使系统能从 Postgres 中按完整自然日重建 day card。如果只用一次增量任务刚刚解析到的 turns 生成卡片，夜间任务就会用不完整的增量覆盖完整卡片，让每次重建后的卡片不断缩水。

## 当前实现状态

前端、日志摄取、记忆服务和本地抽取闭环已经完成。由于数据库和模型只运行在一台 Mac 上，线上部署仍展示 sample data；页面会明确标记 sample 状态，不会把样例伪装成真实数据。

| 模块 | 状态 |
| --- | --- |
| 落地页（`/`）：两条路径、架构、指标表、注册 | 已完成 |
| 日记（`/demo`）：每日卡片、记忆时间线、T1/T2/T3 原始披露 | 已完成 |
| `api/`：FastAPI、三层记忆、衰减检索、去重、查询缓存 | 已完成（M1） |
| `mcp_server/`：通过 stdio 分别 review T2/T3、召回 T3、确认后写入 | 已完成（M3） |
| Pattern Candidate → confirm/edit/reject → reflective T3 receipt | 本地已完成 |
| `get_daily_review`：T2 + 两条 T3 lane + pending candidates | 本地已完成 |
| `evals/eval_memory_engine.py`：衰减、去重和 token benchmark | 已完成 |
| `ingest/`：Claude Code 与 Codex CLI 的 Path A reader | 已完成 |
| `/demo` 接入 API，并带离线 fallback | 已完成 |
| Path A 夜间 scheduler（launchd，opt-in） | 已完成 |
| `/runtime/night-shift`：Celery 任务回执与 Memory Inbox | 本地已完成 |
| PostgreSQL 任务账本 → Redis broker → 幂等 Celery worker | 本地已完成 |
| `train/`：3B 4-bit MLX LoRA 训练与 holdout 评测（M2） | 2026-08-09 本地完成 |
| 本地 MLX HTTP provider → T2 + 待审核 T3 candidates | 本地可用 |
| 语义 query cache 实验（M5） | 已测得不安全，保持关闭 |

`/demo` 现在通过 `/api/diary` 读取后端。API 可访问时，它展示真实卡片、真实 T1 turns 和真实 T3 timeline，banner 变绿。API 不可访问时（线上站点就是这种情况，因为后端只在一台 laptop 上运行），页面 fallback 到 sample data，banner 变成琥珀色，并显示无法访问的 URL。这个区别来自 payload 的 `source` 字段，而不是前端根据一次失败请求猜测，因此 sample row 不会被当成来自 Postgres 的真实记录。

Path A 首先生成可复现的**规则型** day card，包括计数、工具统计、活动时间范围和 git branches。可选的本地 MLX pass 再补充 narrative 并提出 durable preference 建议，同时保留下方的规则事实。每条建议先留在可检索 T3 之外，并记录来源卡片、模型、置信度和尝试次数；用户保留或编辑后才复用 `MemoryService` 写入 T3，避免审核路径与 Agent 各自实现不同的 dedup 行为。

耗时的本地任务统一进入 **Night Shift**。launchd 继续负责定时触发；API 先把工作请求写入 PostgreSQL，只把 job id 发送到 Redis，再由 Celery worker 完成本地抽取或检索回放。late acknowledgement、有上限的指数退避重试和确定性幂等键使重复投递安全；任务状态与结果以 PostgreSQL 为准，而不是依赖 Celery result backend。`/runtime/night-shift` 会展示每次执行回执和待审核候选。

默认 embedder 是**确定性的 hashing fallback**：离线、无需 key、只基于词法。它只负责让整个技术栈在任何环境中启动，并让机械测试能够运行；它不是语义模型，评测代码也拒绝发布在它之上测得的 retrieval-quality 数字。

## 后端快速启动

```bash
cp .env.example .env
.venv/bin/pip install -e .        # 把 mindbridge 和 mindbridge-mcp 安装到 .venv/bin
docker compose up -d db redis     # Postgres+pgvector :5433，Redis :6379
docker compose up -d api worker   # FastAPI :8000 + 后台 worker，OpenAPI /docs
curl localhost:8000/healthz
docker compose run --rm evals     # benchmark -> evals/results.json
```

### Path A：摄取自己的对话日志

```bash
docker compose run --rm ingest                      # dry run，不写入
docker compose run --rm ingest --since 7d           # 摄取最近一周
docker compose run --rm ingest --full               # 从头重新读取
docker compose run --rm ingest --status             # 查看已经读取到哪里
```

transcript 目录以**只读**方式挂载，而且只挂载 transcript 子目录，不会挂载完整的 `~/.claude` 或 `~/.codex`，因为这些目录还包含容器不应访问的凭据。文本写入前会经过 `ingest/redaction.py`，屏蔽常见 key 形状，例如 provider key、bearer token、JWT、`SECRET=` 形式的赋值和 DSN 密码。这只是安全网，不是绝对保证；长得像普通自然语言的秘密可能无法被识别。

日志摄取是增量且幂等的。每个文件有 byte cursor，因此重复运行只读取追加内容；每个 turn 还有 `source_key`，所以即使对已经摄取过的文件执行 `--full` 也不会重复插入。Claude Code 的数据格式还带来两个必须处理的细节：

- **一个 response 会跨多个 record。** 每个 content block 一条记录，并且每条都重复最终的 `message.usage`。如果逐条相加，真实数据中的 token 会被放大 2.5 倍。因此 reader 会把同一 `message.id` 的记录合并成一个 turn，usage 只计算一次。
- **最新一组 record 可能仍在 streaming。** 如果文件最近一分钟内仍在写入，reader 会暂时保留最后一个 group，并把 cursor 停在它之前，避免把半条 response 存进数据库。

默认不保存 tool arguments 和 tool results：它们体积很大，经常重复磁盘上已经存在的文件内容，也是最可能出现 credential 的位置。系统只保留 tool name；`--include-tool-io` 可以显式覆盖这个默认行为。

在 Claude Desktop、Claude Code、Cursor 或 VS Code 中注册 MCP server：

```json
{
  "mcpServers": {
    "mindbridge": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/mindbridge",
      "env": {
        "MINDBRIDGE_DATABASE_URL": "postgresql://mindbridge:mindbridge@localhost:5433/mindbridge"
      }
    }
  }
}
```

仓库为两个本地编程客户端提供了幂等 installer。二者启动相同的 STDIO server，因此会读写同一个本地 Postgres memory store：

```bash
scripts/install-codex-mcp.sh
scripts/install-claude-mcp.sh
codex mcp list
claude mcp list
```

MCP 是主动读写路径；被动 transcript capture 与它分开。`python -m ingest.runner --source all` 会把 Claude Code、当前 Codex 和已归档 Codex JSONL 读进同一张 T1 table，为每一行标记 source，然后重建共享的 T2 cards。源文件保持只读，`source_key` 让重复运行保持幂等。

### 在 Codex 中使用 MindBridge

Codex 支持本地 STDIO MCP server。把 command 指向仓库 virtualenv，并把 server working directory 设为仓库：

```toml
[mcp_servers.mindbridge]
command = "/absolute/path/to/mindbridge/.venv/bin/python"
args = ["-m", "mcp_server.server"]
cwd = "/absolute/path/to/mindbridge"
startup_timeout_sec = 20
tool_timeout_sec = 60

[mcp_servers.mindbridge.tools.temporal_query]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.get_daily_card]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.review_long_term_memory]
approval_mode = "approve"

[mcp_servers.mindbridge.tools.upsert_preference]
approval_mode = "prompt"
```

打开新的 Codex session 前，启动 Postgres、Redis 和配置好的本地 embedder：

```bash
docker compose up -d db redis
ollama list                         # 必须包含 bge-m3
codex mcp list                      # mindbridge 应显示 enabled
```

可以尝试：`调用 MindBridge 的 get_daily_card 读取最新 T2 card，再单独 review 最新五条 T3 memory。引用每张 card 和每条 memory 的 id。` 读取可以直接运行；持久写入需要确认。

2026-08-19，一次全新的 Codex session 通过 `temporal_query` 查询本地 store，返回了三条带 source id 的真实 T3 record。这是本地 integration，不是公开托管的 memory service。修改 MCP 配置后需要重启 Codex 或打开新 session。

2026-08-20，Claude Code 在 user scope 注册并显示连接到同一个 MindBridge STDIO server。随后 dual-source ingest 把 Claude Code、当前 Codex 和 archived Codex 日志读进同一个 Postgres store；数据库中同时存在两类 source label，重复 `source_key` 数为零。一个全新且不持久化的 Claude Code session 随后通过 MCP 调用 `get_daily_review`，分别收到 T2、operational T3、reflective T3 和 Pattern Candidates 四个 section，全程没有调用写工具。

#### Codex 可选 reflection skill

可选的 `mindbridge-reflection` Codex Skill 位于客户端 `~/.codex/skills/mindbridge-reflection`。它是客户端 review policy，不是第十二个 MCP tool：它只教 Codex 如何组合已有的十一个 MindBridge 工具，不会改变 MCP server 或工具数量。

启用后，这个 Skill 会：

- 选择能够回答问题的最窄 review，并保持 T2、operational T3 和 reflective T3 相互分开；
- 要求结论引用 memory/card id 与日期，要求推断同时展示 counter-evidence 和 uncertainty；
- 把 Pattern Candidate 留在 personality conclusion 之外，因为 candidate 不是 personality fact；
- 在执行 `archive`、`edit`、`upsert`、`confirm` 或 `reject` 前展示精确变化，并在当前 session 获取明确确认；
- 当 MindBridge MCP 不可用时，不从 chat memory 中虚构证据。

该 Skill 已通过官方 quick validator，并在验证机器上通过本地 Codex 检查。它尚未进入 public installer，因此上面的 MCP 安装命令不会安装它，这里也不声称存在公开 Skill 安装命令。

验证环境是 MacBook Pro（Apple M1 Pro、8-core、16 GB RAM）、macOS 26.5.2 / arm64、Claude Code 2.1.226、MindBridge virtualenv 内 Python 3.12.13、Docker 29.2.1 与 Ollama 0.32.5。落地页会把这个 verified device 与 Anthropic 官方 minimum requirements 分开描述。

REST 与 MCP 都调用同一个 `MemoryService`，因此 MCP 的 `upsert_preference` 与 HTTP 的 `POST /memories` 不会各自漂移出不同的业务逻辑。

| Endpoint | 层级 | 用途 |
| --- | --- | --- |
| `POST /sessions/{id}/turns` · `GET /sessions/{id}/buffer` | T1 | 追加和读取原始 window |
| `POST /summaries` · `GET /summaries` | T2 | 写入和列出 period cards |
| `POST /memories` | T3 | 对 operational 或已确认 reflective memory 先 dedup 再写入 |
| `GET /memories?namespace=` | T3 | newest-first 列表，可限定 namespace |
| `GET /memories/{memory_id}` | T3 | 读取单条 memory 与当前 decay score |
| `PATCH /memories/{memory_id}` | T3 | Memory Garden 的 archive/edit 操作 |
| `GET /turns?start=&end=` | T1 | 读取时间范围内的原始 turns；timezone 由 caller 决定 |
| `POST /memories/query` | T3 | 带时间衰减的 top-K recall，可限定 namespace |
| `POST /patterns` · `GET /patterns` | Reflection | 在 T3 外创建/review candidates |
| `POST /patterns/{id}/resolve` | Reflection | confirm/edit 后写入 reflective T3，或 reject |
| `GET /daily-review` | Companion | 跨 T2、T3 与 candidates 的统一 review surface |
| `GET /night-shift` | Background | 任务回执、Memory Inbox 与最近一次回放指标 |
| `POST /night-shift/run` | Background | 异步提交缺失卡片抽取与检索回放 |
| `POST /night-shift/candidates/{id}/resolve` | Background | 保留/编辑后写入 T3，或拒绝 |
| `POST /night-shift/jobs/{id}/retry` | Background | 显式重试已终止失败的任务 |

### Pattern Candidate 确定性发现

基于规则的 candidate discovery 可以在完全不调用模型的情况下，把重复出现的 T2 facts 转成可 review 的 Pattern Candidates：

```bash
.venv/bin/python -m scripts.suggest_patterns --since 30d
# 加 --apply 才会把 pending candidates 插入 Postgres
```

scanner 当前从确定性 facts 中提取可重复信号，例如 project touches、tool usage、git branches、source labels 和深夜工作。它与手动 proposal 使用相同安全门槛：至少三条 supporting observations，并且跨至少两个不同日期。

可以用独立 launchd agent 定时运行：

```bash
mindbridge schedule patterns install             # 默认 00:45；只打印，不写入
mindbridge schedule patterns install --confirm
mindbridge schedule patterns status
mindbridge schedule patterns run-now
MINDBRIDGE_PATTERN_SINCE=14d \
MINDBRIDGE_PATTERN_SCAN_LIMIT=180 \
MINDBRIDGE_PATTERN_SUPPORTING=8 \
MINDBRIDGE_PATTERN_DAILY_LIMIT=20 \
MINDBRIDGE_PATTERN_APPLY=1 mindbridge schedule patterns install
```

这些 tuning 会写进 plist environment，因此不带 `--confirm` 的 `install` 也可以用来预览 scheduled run 实际会做什么，再决定是否同意。

job 默认 dry-run；除非显式设置 `MINDBRIDGE_PATTERN_APPLY=1`，否则绝不会使用 `--apply`。只有在看过 dry-run sample、确定愿意创建真实 Pattern Candidate rows 后才应打开它。

本机 2026-08-04 的实测（`--since 7d`）：282 个 transcript 文件中 91 个有新内容，产生 51 个 sessions、3,067 个 T1 turns 和 5 张 T2 day cards，并屏蔽 8 个疑似 secrets。第二次运行只读取 2 个文件；`--full` 重读 3,065 个 turns，实际插入 0 行。

## `mindbridge` CLI

一个 entry point 覆盖 operator 真正常用的四类工作：

```bash
mindbridge doctor                       # 只读检查完整本地闭环
mindbridge ingest --since 3d            # Path A，与 nightly agent 使用同一任务
mindbridge patterns --since 30d         # dry run；--apply 才写 candidates
mindbridge mcp                          # 通过 stdio 提供十一个 tools
mindbridge install claude|codex         # 向客户端注册 MCP server
mindbridge schedule ingest|patterns ... # status | run-now | install | uninstall
mindbridge verify --plan                # 预览 local-loop proof 会启动什么
```

`schedule ... install` 会打印准备写入的精确 plist 然后停止。只有 `--confirm` 才真正写入，因为 LaunchAgent 是对机器的持久修改。`status`、`run-now` 和 preview 不修改配置。

未知 flag 会继续传给 `ingest.runner` 和 `scripts.suggest_patterns`，所以 `mindbridge ingest --full --status` 与 `mindbridge patterns --card-limit 90` 仍然可以访问原模块已有的选项。

`mindbridge-mcp` 是第二个 console script，也是 packaging 存在的原因：它不依赖工作目录或 shell wrapper，并且会在 `Settings` 读取相对路径 `.env` 前切换到 checkout。否则，如果客户端从自己的 cwd 启动 server，系统可能无声地 fallback 到 `hashing` embedder。

十一个 memory tools 刻意没有被复制成 CLI command。记忆的读取与修改应该发生在有对话上下文的 MCP client 中；如果 terminal 再复制一套相同 surface，就需要同时维护两套安全边界。

`scripts/nightly-ingest.sh`、`scripts/nightly-patterns.sh` 和 `scripts/run-mcp.sh` 现在只是这些 command 的一行 shim。它们仍然保留，因为已安装的 LaunchAgent 和已注册的 MCP client 指向这些固定路径，升级时无需重新安装。

`doctor` 每一行都标明自己读取了什么，并且不写 schema、不安装 launchd agent、不执行 compose lifecycle。2026-08-20 本机输出示例：

```text
[ok  ] postgres          T1 24542 turns · T2 674 cards · T3 575 memories · 0 pattern candidates
                         ↳ postgresql://mindbridge:mindbridge@localhost:5433/mindbridge
[ok  ] vector width      vector(768) matches configured dim
[ok  ] ingest freshness  newest T1 turn 2026-08-19 17:38 (0d old)
[FAIL] embeddings        ollama unreachable at http://localhost:11434: ConnectError
                         ↳ ollama serve
[warn] mlx extractor     not serving at http://127.0.0.1:8080/v1 — local extraction unavailable
```

任何一行出现 FAIL 时，命令以 exit code 1 结束，因此可以作为 demo 前的 gate。

## 定时运行 Path A

```bash
mindbridge schedule ingest status              # 是否安装，上次何时运行
mindbridge schedule ingest run-now             # 前台运行一次
mindbridge schedule ingest install             # 打印 plist，不写入
mindbridge schedule ingest install --confirm   # 每晚 23:30 定时
mindbridge schedule ingest uninstall           # 删除 schedule
```

`install --confirm` 会写入 `~/Library/LaunchAgents/com.mindbridge.nightly-ingest.plist` 并注册到 launchd。这是持久修改，因此始终 opt-in，不会作为 build 或 test 的副作用发生；`status`、`run-now` 和不带 `--confirm` 的 `install` 都不改配置。可用 `MINDBRIDGE_INGEST_HOUR` / `MINDBRIDGE_INGEST_MINUTE` 修改运行时间。

`scripts/mindbridge-scheduler.sh` 仍然可用，并会原样转发这些参数。CLI 生成的 plist 与旧 shell script 生成的 plist 解析成相同 dictionary；这个结论通过对本机已安装 agent 分别执行 `plistlib.loads` 比较得到，因此已安装用户不需要重装。

job 可以安全重复运行：turn 按 source record 设 key，day card 从数据库完整重建而不是基于 delta。如果 Docker Desktop 没运行，它会记录一次 skip，保持 cursor 不动并以 0 退出；下次运行会从相同位置继续。日志写入 `~/Library/Logs/mindbridge/ingest.log`，达到 5 MB 时轮转一次。

在已经摄取过的 corpus 上，一次 incremental run 大约需要七秒。

## M2：把一天变成日记

Stage one 可以使用 OpenAI、Gemini 或已有的 Claude Code 登录态生成 validated training pairs。托管路径必须带显式 send flag。Stage two 已可完全在本地运行：`mlx_lm.server` 加载 fine-tuned Qwen2.5-3B 4-bit adapter，`extract.runner --provider mlx` 调用其 OpenAI-compatible endpoint，不需要 API key 或 send flag。

```bash
# 查看精确 prompt 与预计成本；不需要 key，不发送任何内容
docker compose run --rm extract --date 2026-08-04 --dry-run

# 真正抽取：同时需要 key 和显式 send flag
export MINDBRIDGE_OPENAI_API_KEY=...      # 或 MINDBRIDGE_GEMINI_API_KEY
docker compose run --rm extract --missing --limit 5 --send-to-provider

# 或复用 host 上已登录的 Claude Code CLI；不需要 API key
# 必须在 host 运行，因为 Docker image 刻意不包含 CLI credential
uv run --with-requirements requirements.txt python -m extract.runner \
    --date 2026-08-04 --provider claude-cli --send-to-provider

# 本地模型服务：adapter 留在这台 Mac
.venv/bin/mlx_lm.server \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit \
    --adapter-path train/outputs/mlx-adapters \
    --host 127.0.0.1 --port 8080 --max-tokens 1200 --temp 0.2

# 另一个 terminal：transcript → schema JSON → T2 narrative + T3 preferences
.venv/bin/python -m extract.runner --missing --limit 1 --provider mlx

# 累积 schema-compliance 数据
docker compose run --rm extract --stats

# schema 与 repair loop 的离线测试；不需要 key 或网络
docker compose run --rm --no-deps --entrypoint python extract \
    -m extract.test_pipeline
```

**只有托管 provider 会把数据发出本机。** OpenAI、Gemini 和 Claude Code CLI 会传输当天 transcript excerpt，因此必须显式传入 `--send-to-provider`。`--dry-run` 会逐字节打印真正准备发送的内容。`mlx` 默认只访问 `127.0.0.1`，刻意不要求这个 flag。

Compliance 有两种口径，二者不能混淆。`first_attempt_rate` 是模型无需修正就直接返回 schema-valid object 的比例；`eventual_rate` 包含 repair loop。只有第一种口径能与 fine-tuned model 比较，因此面试和简历只能引用第一种。

Validation 不是装饰。`DiaryDraft` 禁止 extra keys，拒绝推断情绪状态的 narrative（例如 “you seemed frustrated”），也拒绝把一次性任务伪装成 durable preference。模型确实会漂移到这三类错误，仅靠 prompt 无法可靠阻止。

### Apple silicon 上的训练与评测

```bash
python -m train.prepare_dataset --report
python -m train.train_mlx \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit
python -m train.eval_mlx \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit \
    --adapter train/outputs/mlx-adapters --seed 3407 \
    --out train/outputs/mlx-adapters/holdout-eval.json
```

数据按 date 做 deterministic split，同一天的 pairs 永远不会横跨 train 与 holdout。`eval_holdout.py` 拒绝发布基于少于 30 个 holdout days 的 compliance rate，因为极小样本的误差可能比数字本身更大，而结果会直接进入公开页面。

### 按 session 生成卡片

如果一天只能产生一张卡，训练集最多每天增长一个 pair，积累速度太慢。ingest 因此还会为每个 session 生成一张 card。在当时的历史快照中，242 张 session cards 对应 56 天，大约把 extraction targets 放大了四倍，而且会随着日常使用持续增长。

```bash
# 默认写 session cards；--no-session-cards 才关闭
docker compose run --rm ingest --since 3d

# 从 T1 重建全部 day/session cards，不重新读取 transcript
docker compose run --rm ingest --rebuild-cards all

# 为 session card 而不是 day card 抽取 prose
.venv/bin/python -m extract.runner --missing --scope session --limit 10 \
    --provider claude-cli --send-to-provider
```

少于 6 turns 的 session 会被跳过：一个问题加一行回答生成的卡片，信息量甚至少于自身 metadata；作为训练数据时只会教模型扩写空洞内容。

day card 与 session card 共用同一张表，因此每次读取都要声明 scope（`/summaries?scope=day|session|all`，默认 `day`）。否则 Diary 的 day list 会静默混入数百条 session rows。

session card 的 input 平均约 1.7k tokens，day card 约 11k，因为前者只读取自身 session 的 turns。

### Prompt 版本管理

每次 attempt 都记录 `PROMPT_VERSION`，`--stats` 会按版本拆分 compliance。prompt 改动会移动指标，因此把多个版本混成一个 rate 会得到一个不代表任何实际版本的数字。最初 46 天使用 `v1`，其 system prompt 没有提到 schema 必填的 `confidence`；仅这一处遗漏就造成 19 次失败中的 17 次。

## 已测 baseline

| 项目 | 数值 | n | 方式 |
| --- | --- | --- | --- |
| Teacher 首次 schema compliance | **84.7%** | 281 | Sonnet via claude-cli；`--stats` |
| Teacher 在 date-isolated local holdout 上的表现 | **82.2%** | 45 | 保存的 first-attempt flags |
| Qwen2.5-3B MLX LoRA pilot | **86.7%** | 45 | seed 3407；不含 repair；`evals/mlx_holdout_seed_3407.json` |

MLX LoRA run 已经完成：198 个 fit rows、34 个 training-side validation rows、45 个按日期隔离的 holdout rows 共同产出了 `train/outputs/mlx-adapters/` 中的 adapter。“Pilot”描述的是证据范围有限，而不是训练尚未执行。

这套方法曾经推翻过自己的早期结论。n=40 时，v2 prompt 得分 80%，低于 v1 的 83%，README 一度写下“改动没有帮助”。当样本增长到 n=235 时，v2 变成 **85%**，高于同一个 83%。原结论本身就是它所警告的小样本误导；应重新执行 `--stats`，不能依赖记忆中的比较。

84.7% 是 teacher 的广义 baseline。45-pair comparison 才是 like-for-like，因为两个模型看到完全相同的 held-out dates。本地 pilot 可复现且有希望，但不代表统计显著优于 teacher：39 条 valid 与 37 条 valid 只差两个 case，而且 13 个 prompt 使用了训练时相同的 4,096-token truncation。

## 本地状态快照

下表来自 2026-08-11 对运行中 store 的读取，不是沿用更早数字。所有内容都可重建，因此它只是 snapshot，不是项目的永久事实。

| 指标 | 数值 |
| --- | --- |
| T1 turns | 13,072 |
| T2 day cards / session cards | 56 / 242 |
| 带 model-written prose 的 cards | 238 |
| T3 open preferences | 329 |
| 已捕获 extraction pairs | 281 |
| MLX adapter | `train/outputs/mlx-adapters/adapters.safetensors` |

一次 rebuild 只 replay 了 narrative、没有 replay preferences，导致 T3 一度只剩一条 preference。2026-08-11 使用以下命令恢复：

```bash
.venv/bin/python -m scripts.replay_extractions --apply
```

命令读取已保存 pairs，把 preferences 重新写入当前 embedder 的正常路径，全程不调用模型。它恢复了 329 条 open preferences，并在 413 次 write 中 merge 85 次；top rows 分别吸收了三个 duplicate，说明 dedup path 确实发生，而不是偶然得到同一个总数。

## 从零重建数据库

Postgres 是可丢弃的。重建所需的 durable source 都在数据库之外：磁盘上的 transcripts，以及 `train/dataset/extraction.jsonl` 中的每次 extraction；后者总是在任何数据库写入之前先落盘。

```bash
# 从 transcript 重建 T1 turns + T2 day/session cards
.venv/bin/python -m ingest.runner --full

# 从 captured dataset 重建 T2 narratives + T3 preferences；不调用模型
.venv/bin/python -m scripts.replay_extractions --apply
```

Replay 不产生模型费用，并能重现 byte-identical prose。它还会用**当前** embedder 和 threshold 重新写 preference，因此最初在非语义 hashing embedder 下存入的 rows，重建后也会按当前配置去重。

## 指标政策

落地页直接读取 `evals/results.json`。每个数字都必须能追溯到对 live database 执行的可重跑 script，因此页面上任何数字都能用一条命令重现来源。

- 四个 engine metrics（`promptTokenReduction`、`dedupAccuracy`、`decayOrdering`、`supersedeExclusion`）来自 `evals/eval_memory_engine.py`。
- `extractionJsonAccuracy`（86.7%）与 `localExtractionCostDelta`（18.8s，即每个 holdout pair 的本地 MLX latency）来自 `evals/mlx_holdout_seed_3407.json` 的 fixed-seed MLX run（`train/eval_mlx.py`）。
- `cacheCostSaving` 是定性结论而非数字：semantic cache 已实测不安全。一个无关的短 query pair 得分 0.9992，高于真实 paraphrase 的 0.9064，因此它保持关闭，而不是发布无法支撑的 savings。

如果一个 metric 没有诚实数字，页面就显示实测结论，不虚构数值。

| Metric | 是否依赖 embedder | Script / 状态 |
| --- | --- | --- |
| Time-decay scoring correctness | 否 | `evals/eval_memory_engine.py` |
| Superseded-record isolation | 否 | `evals/eval_memory_engine.py` |
| Write-time dedup accuracy | 是 | `evals/eval_memory_engine.py` |
| Per-turn prompt token reduction | 是 | `evals/eval_memory_engine.py` |
| Preference-extraction JSON validity | 是 | `train/eval_mlx.py`（已有本地证据） |
| Extraction API cost delta | 是 | 尚未在可部署 serving target 上测量 |
| Semantic cache 节省成本 | 是 | 已测得不安全；保持 null 且 disabled |

“Time-decay scoring correctness”会把相同 content 以多个年龄写入，检查 newest-first ordering，并验证每个 score 与 `cosine · exp(-λ·Δt)` 的误差小于 1e-6。它是在验证公式，不是在证明 retrieval quality；网站指标表也明确写出这一区别。

### 为什么 semantic query cache 保持关闭

Exact-key cache 是安全的并保持启用。semantic-neighbour path 虽已实现，但在 `nomic-embed-text` 下没有通过自己的 acceptance test：

- 一个无关的短 query pair 得到 cosine **0.9992**；
- 唯一真正可缓存的 paraphrase pair 只有 **0.9064**；
- 35 组 same-intent query pairs 中只有 **1 组**召回相同 memory ids；
- 最不差的 threshold 仍产生 **50% false-hit rate**。

正负样本的分布无法被任何 threshold 分开。提高阈值会丢掉真正 paraphrase；降低阈值会把另一个问题的缓存答案返回给当前问题。一次 miss 只多花一次 vector search，而 false hit 会直接返回错误记忆，因此实测结论是 `cache_semantic_enabled=false`，不是人为制造一个 savings 数字。

这并不推翻 0.80 write-dedup threshold。Dedup 比较较长的 stored statements；cache key 是信息量更少的短 queries，属于不同的数据分布，不能共享阈值。

## 已实现架构

```mermaid
flowchart LR
    subgraph SRC["本地 transcripts · 只读"]
        direction TB
        CC["~/.claude/projects/*.jsonl"]
        CX["~/.codex/sessions + archived"]
    end

    subgraph ING["Path A · ingest/"]
        direction TB
        RD["readers<br/>claude_code · codex_cli"]
        RX["redaction.py<br/>屏蔽 secrets"]
        DG["digest.py<br/>source_key"]
        RD --> RX --> DG
    end

    subgraph M2["M2 · extract/"]
        direction TB
        PV["providers.py<br/>claude-cli · mlx · openai · gemini"]
        SC["schemas.py<br/>validate + repair"]
        DS["dataset.py<br/>extraction.jsonl"]
        PV --> SC --> DS
    end

    subgraph STORE["Postgres + pgvector"]
        direction TB
        T1["T1 session_turns"]
        T2["T2 rolling_summaries"]
        T3["T3 memory_vectors<br/>bitemporal"]
        PC["pattern_candidates"]
    end

    subgraph SERVE["Serving"]
        direction TB
        MCP["mcp_server<br/>11 MCP tools"]
        API["api/main.py<br/>FastAPI"]
    end

    EMB["embeddings<br/>bge-m3"]
    LORA["train/<br/>MLX LoRA"]
    CLI["Codex · Claude Code<br/>Cursor · VS Code"]

    CC --> RD
    CX --> RD
    DG --> T1
    T1 -->|nightly| T2
    T2 --> PV
    SC -->|prose| T2
    SC -->|preferences| T3
    DS -.->|teacher rows| LORA
    LORA -.-> PV

    T3 <--> EMB
    T2 & T3 & PC <--> MCP
    T3 --> API
    MCP <-->|写入需要确认| CLI

    classDef remote fill:#3a2c10,stroke:#b4791a,color:#f3e2c4;
    class PV remote;
```

如何阅读这张图：

- **Ingest 是幂等的。** `source_key` 由 session、timestamp 和位置生成，不依赖 turn text，因此即使 reader 改动导致 turn 渲染方式变化，重新解析同一个文件也插入 0 行。
- **只有 provider box 可能离开本机。** `claude-cli` 和 `mlx` 是本地路径；`openai` 与 `gemini` 会发送 excerpt，而且只有显式传入 `--send-to-provider` 才执行。
- **T3 是 bitemporal。** `created_at` 表示何时学到，`valid_at` 表示何时不再有效。Superseded rows 只关闭，不删除。
- **Embedding 不生成文字。** 本地 `bge-m3`（1024-dim，多语言）负责 0.86 write-time cosine dedup 与 retrieval score（`cosine × exp(-0.01 × days)`）；所有 prose 都来自 M2 box。
- **Pattern 留在 T3 外。** `propose_pattern` 写 candidate；只有显式 `resolve_pattern` decision 才能升级。
- **LoRA box 是一个 adapter，不是第二套系统。** Qwen2.5-3B-Instruct-4bit 使用 198 fit rows 微调，另有 34 training-side validation 与 45 date-isolated holdout；它由本机 `mlx_lm.server` 提供服务，只有 schema failure 后才进入 retry。
- **Cache 在 API 旁边，但刻意只开了一半。** bounded in-process LRU 与 Redis exact-key cache 已启用。semantic neighbour matching 已实现但关闭，因为无关短问题的 0.9992 高于真实 paraphrase 的 0.9064，没有安全 threshold。

## Developer-preview 注册

`POST /api/waitlist` 会转发到 `WAITLIST_WEBHOOK_URL`，可指向任何接受 JSON body 的 endpoint，例如 Formspree、Resend、Slack webhook 或 Apps Script。如果变量未设置，route 返回 503，表单 fallback 到预填的 `mailto:` draft，避免地址被静默丢弃。表单还带 honeypot；该字段被填写时页面显示成功但不会发送任何内容。

## 本地运行前端

```bash
npm install
npm run dev
```

打开 [http://localhost:3000](http://localhost:3000)，或打开 [/demo](http://localhost:3000/demo) 查看 Diary。

## 验证

```bash
npm run lint && npm run build
```

### 一条命令证明本地闭环

面试演示或 release 前，可以用一条 command 启动缺失的 local services，通过 private MLX adapter 刷新一个真实 day card，再分别从 REST、Diary route 和实际 MCP stdio client 验证同一套 store：

```bash
mindbridge verify --plan             # 探测所有服务，什么也不启动
mindbridge verify                    # 复用最新的 MLX-written day
mindbridge verify --date 2026-08-08  # 或显式指定一张 T2 day card
```

`--plan` 存在是因为完整运行会加载一个 3B model 并启动两个 web servers。它会打印八个服务中哪些已经存在、哪些需要临时启动。

执行会刷新该日 T2 narrative，但在 extraction 时刻意关闭 preference write，并使用一条已有真实 T3 row 作为只读 recall probe，因此 acceptance test 不会变成持久用户记忆。原本已经运行的服务保持运行；由本次命令启动的服务会在退出时关闭；Postgres 数据始终保留。

失败后的 service logs 也会保留。旧版 `scripts/verify-local-loop.sh` 的 error handler 打印 `see $TMP_DIR` 时已经删除了目录，导致最需要日志的时候链接指向空位置。现在失败时保留 temp directory，成功时才删除。旧 shell script 仍然可用，也仍然接受裸 date 参数。

## 设计

视觉系统来自 1Day landing page：品牌蓝 `#1875ef`、DM Sans 正文、Manrope 标题、Nanum Pen Script 手写强调、相同的 phone mockup 与 paper-on-ink card。页面围绕“两条 capture paths 汇合到 memory layer”这一张连续视觉构建，而不是 hero 加三个 feature blocks。所有用户可见文案集中在按 locale（`zh` / `en`）组织的 `copy` object 中；technical identifiers 在两种语言下都保持英文。

## 技术栈

**前端**：Next.js、React、TypeScript、CSS。  
**后端**：Python 3.12+、FastAPI、asyncpg、Postgres 16 + pgvector、Redis、MCP Python SDK、Docker Compose。  
**本地模型**：Qwen2.5-3B-Instruct-4bit、MLX LoRA、`mlx_lm.server`。  
**Roadmap，尚未交付**：如果本地 pilot 将来需要离开 Apple silicon，再导出到可移植的 CUDA/vLLM serving target。
