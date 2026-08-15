# 面向学术研究的知识沉淀型 MyClaw 助手 — 项目文档

> **技术栈：** Harness | LangGraph | FastAPI | PostgreSQL (pgvector) | Wiki (Markdown + YAML) | RAG
>
> **项目地址：** [github.com/ZhongZhi-Qiang/PaperMind-Agent](https://github.com/ZhongZhi-Qiang/PaperMind-Agent)

---

## 目录

- [一、项目简介](#一项目简介)
- [二、四层记忆金字塔](#二四层记忆金字塔)
- [三、三层自积累 Wiki 知识库](#三三层自积累-wiki-知识库)
- [四、分层治理与安全审计](#四分层治理与安全审计)
- [五、知识熵管理机制](#五知识熵管理机制)
- [六、技术架构附录](#六技术架构附录)

---

## 一、项目简介

### 1.1 项目定位

面向学术研究场景，以 **Harness** 架构为范式，构建面向学术研究的**知识沉淀型** Agent 助手，解决现有系统知识难积累、长对话易退化、工具调用不可控等问题。

系统设计 **10 个学术研究 Skills** 与 **16 个工具接口**，覆盖论文导入、知识查询、研究构思和经验沉淀等核心流程，使 Agent 具备**知识累积**、**决策可追溯**与**行为可审计**能力。

### 1.2 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        前端 (Next.js 14)                            │
│                        POST /api/chat (SSE)                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                     FastAPI + AgentManager                           │
│                                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ Guardian  │→│ RecallService│→│ PromptBuilder│→│ Agent Loop  │  │
│  │ (安全门控) │  │ (记忆检索)   │  │ (Prompt组装) │  │ (ReAct)     │  │
│  └──────────┘  └──────────────┘  └─────────────┘  └──────┬──────┘  │
│                                                           │          │
│  ┌────────────────────────────────────────────────────────▼──────┐  │
│  │                    Middleware Chain (5 层)                      │  │
│  │  Guardian → HarnessSecurity → ContextOffload → Summarization   │  │
│  │  → HarnessReview                                               │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                    Tools (16 个)                                 │  │
│  │  pdf_parser / register_source / save_wiki_page / read_wiki_page │  │
│  │  query_wiki / list_wiki_pages / rebuild_index / lint_wiki       │  │
│  │  append_log / terminal / python_repl / fetch_url / read_file    │  │
│  │  search_memory_v3 / PDFParser / RegisterSource                  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                    Skills (10 个)                                │  │
│  │  paper-ingest / paper-update / wiki-ask / ideate / wiki-lint    │  │
│  │  paper-wiki / quick-lookup / rag-skill / web-search             │  │
│  │  retry-lesson-capture                                           │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
┌─────────▼──────┐  ┌───────▼────────┐  ┌──────▼───────┐
│  Wiki 知识库    │  │  长期记忆 v3    │  │  短期记忆     │
│  (文件系统)     │  │  PG+文件        │  │  (JSONL+MMD)  │
│  raw/sources/  │  │  l0/*.json     │  │  offload-*.jl │
│  wiki/papers/  │  │  l1_facts      │  │  refs/*.md   │
│  wiki/concepts/│  │  scenes/*.md   │  │  mmds/*.mmd  │
│  wiki/methods/ │  │  persona.md    │  │              │
│  wiki/authors/ │  │  pipeline_state│  │              │
└────────────────┘  └────────────────┘  └──────────────┘
```

### 1.3 核心设计哲学

> **在宏观层面用确定性约束抑制熵增，在微观层面保留推理灵活性，在事后用自反馈机制实现收敛。**

- **Skill-Guided Plan-and-Execute**：声明式任务拆解（确定性路径控制）+ ReAct 执行（灵活推理）
- **三层记忆金字塔**：L0 原始消息 → L1 原子事实 → L2 场景 → L3 人格
- **五层中间件安全链**：Guardian → Security → Offload → Summarization → Review
- **六重幻觉防线**：从价值观（SOUL.md）到底层工具（HarnessReview）的全链路防护
- **自反馈收敛**：retry-lesson-capture + lint auto-fix + ideate banlist 反重复

---

## 二、四层记忆金字塔

### 2.1 架构总览

记忆系统分为两大类：**短时工作记忆**（当前会话内）和**长期记忆**（跨会话持久化）。长期记忆采用四层金字塔架构。

```
┌─────────────────────────────────────────────────┐
│           L3: 长期人格记忆 (Persona)              │
│    从 L2 场景中提取的用户画像和研究偏好            │
│    全局单例，最稳定                               │
├─────────────────────────────────────────────────┤
│           L2: 场景记忆 (Scene)                    │
│    L1 原子事实按主题聚合的结构化叙事               │
│    中期记忆，支持增量刷新                         │
├─────────────────────────────────────────────────┤
│           L1: 原子事实 (Fact)                     │
│    从对话中提取的离散知识单元                      │
│    结构化、可检索，经去重消除冗余                  │
├─────────────────────────────────────────────────┤
│           L0: 原始消息 (Raw Messages)             │
│    每轮对话的完整记录，全量捕获                    │
│    为上层提供原始素材                             │
└─────────────────────────────────────────────────┘
```

长期记忆采用**分层存储**：PostgreSQL 存 L1 事实与流水线状态，L0/L2/L3 为本地文件。

PostgreSQL `memory_v3` schema 下的表：

| 表 | 存储内容 | 写入时机 | 用途 |
|----|---------|---------|------|
| `l1_facts` | 结构化事实（content, type, priority, scene, embedding, tsvector） | 每 N 轮（指数退避） | 检索的核心数据 |
| `pipeline_state` | session 的对话计数、warmup 阈值、缓冲消息 ID | 每轮更新 | 调度触发条件 |

文件存储：L0 → `memory_module_v3/l0/{session_id}.json`；L2 → `memory_module_v3/scenes/*.md` + `_index.json`；L3 → `memory_module_v3/persona.md`。

### 2.2 L0：原始对话捕获

**触发时机：** 每次 LLM 对话结束后自动执行（`agent.py` 的 `astream()` 结束后）。

**流程：**
1. `L0Recorder.capture()` 同步写入用户消息和助手回复到 `memory_module_v3/l0/{session_id}.json` 文件
2. 通知 `PipelineManager.notify_conversation()` 评估是否触发上层提取

**特点：** 全量捕获，不做任何过滤。L0 只做素材缓冲、不含 embedding（向量在 L1 提取时计算）。

```json
{
  "session_id": "sess_research_001",
  "messages": [
    {"msg_id": 0, "role": "user", "content": "...", "ts": "2026-06-08T14:00:00+00:00"},
    {"msg_id": 1, "role": "assistant", "content": "...", "ts": "2026-06-08T14:00:12+00:00"}
  ]
}
```

### 2.3 L1：原子事实提取

**触发条件（满足任一即触发）：**

1. **预热阈值：** 对话轮数 >= `warmup_threshold`（指数退避：1→2→4→8→...→`pipeline_every_n`，默认 5）
2. **空闲超时：** 距上次 L1 提取超过 `l1_idle_timeout`（默认 300 秒）

**提取流程：**

```
Buffered L0 messages
        ↓
   L1Extractor.extract()  ← LLM 提取 SceneSegment[]
        ↓
   L1Deduplicator.dedup() ← 哈希去重 + 语义合并
        ↓
   写入 l1_facts 表
        ↓
   异步计算 embedding
```

**Step 1 — 提取（L1Extractor）：** LLM 从缓冲的 L0 消息中提取 `SceneSegment[]`，每个 segment 包含 `scene_name`（主题）、`message_ids`、`memories`（原子事实列表）。每个 memory 有三个字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| `content` | 1-2 句话的自包含事实 | "用户导入了论文 Attention Is All You Need" |
| `type` | persona / episodic / instruction | `episodic` |
| `priority` | 0-10 优先级 | `7` |

**Step 2 — 去重（L1Deduplicator，两层）：**
1. **内容哈希（确定性、零 LLM）**：content 归一化后哈希，与库中现有事实精确重复 → 直接丢弃
2. **语义合并（LLM，仅对相似候选）**：用 `search_facts` 找 top-3 相似事实，命中才调 LLM 决策：

| 决策 | 含义 | 操作 |
|------|------|------|
| `merge` | 与候选旧事实是同一事实 | 打上 `target_fact_id`，source_msg_ids 取并集，复用旧行 |
| `store` | 全新 / 仅表面相似 | 直接插入 |

LLM 只对"有相似候选"的事实调用；无候选、或 LLM/搜索失败时默认 `store`（宁存重复不丢事实）。

**Step 3 — 写入 `l1_facts`：**

```sql
l1_facts (
    fact_id        BIGSERIAL PRIMARY KEY,
    content        TEXT,           -- 事实内容
    fact_type      TEXT,           -- persona / episodic / instruction
    priority       INT,            -- 优先级
    scene_name     TEXT,           -- 所属场景
    source_msg_ids BIGINT[],       -- 来源 L0 消息 ID（可回溯）
    timestamps     TIMESTAMPTZ[],
    session_id     TEXT,
    embedding      vector(1024),   -- 异步计算
    content_tsv    TSVECTOR        -- 自动生成（GENERATED ALWAYS AS to_tsvector('simple', content)）
)
```

`content_tsv` 使用 PostgreSQL `simple` 分词器以兼容中文。

### 2.4 L2：场景块整合

**触发条件（满足任一）：**

1. **数据量（主）**：自上次 L2 以来新增事实 ≥ `l2_trigger_n_facts`（默认 10），立即触发
2. L1 完成后等待 `l2_delay_after_l1`（默认 120 秒，兜底）
3. 距上次 L2 超过 `l2_max_interval`（默认 3600 秒，兜底）
4. 首次运行（有 L1 但从未跑过 L2）

**流程（SceneExtractor.consolidate）：**

1. 读取所有 L1 事实（最多 1000 条）
2. 读取已有 L2 场景（用于上下文）
3. 调用 LLM 将事实组织为场景块

每个场景块包含 `scene_name`（英文 snake_case 主题名）、`summary`（1-2 句摘要）、`fact_ids`（关联的 L1 事实 ID）、`content`（Markdown 格式的叙事内容）。

场景块写入 `memory_module_v3/scenes/` 目录：每个场景一个 `.md` 文件（YAML frontmatter 存 `scene_name` / `summary` / `fact_ids` / `updated_at`），外加 `_index.json` 导航索引。

```text
scenes/
├── _index.json
├── transformer_architecture.md
└── bert_pretraining.md
```

用 `upsert` 写入（按 `scene_name` 冲突更新），支持增量刷新。

### 2.5 L3：用户画像生成

**触发条件：** 距上次 L3 新增事实 ≥ `l3_trigger_every_n`（默认 50），或距上次 L3 超过 24h（`l3_max_interval_seconds`，兜底）。

**流程（PersonaGenerator.generate）：**

1. 读取所有 L2 场景块
2. 拼接为 Markdown 文本
3. 调用 LLM 生成用户画像，包含四个维度：
   - **Identity**：角色、背景、专业领域
   - **Preferences**：沟通风格、技术偏好、工作流习惯
   - **Goals**：当前目标、项目、兴趣
   - **Context**：正在进行的工作、近期活动、环境
4. 追加 Scene Navigation 部分（到各场景的链接）
5. 存入 `memory_module_v3/persona.md` 文件

L3 是全局单例，所有 session 共享同一个用户画像。

### 2.6 流水线调度器（指数退避预热）

调度器管理 L1→L2→L3 的触发时序，核心是**指数退避预热**机制：

```
会话轮数:  1  2  3  4  5  6  7  8  9  10 ...
warmup:    1  2  4  8  5  5  5  5  5  5  ...  ← 达到 pipeline_every_n 后固定
L1 触发:   ✓  ✓        ✓        ✓
```

新会话开始时 `warmup_threshold=1`，每触发一次 L1 就翻倍（1→2→4→8→...），直到达到 `pipeline_every_n`（默认 5）后固定。新会话快速提取，稳定后降低频率节省 token。

### 2.7 检索（RecallService）

每次用户发消息时，`RecallService` 并行执行三层检索：

```
用户消息 query
    ├── L1 检索：混合搜索原子事实（pgvector + tsvector + RRF）
    ├── L2 获取：返回场景导航列表
    └── L3 获取：返回用户画像
```

**L1 混合检索策略：**

| 策略 | 方法 | 说明 |
|------|------|------|
| `hybrid`（默认） | pgvector 向量搜索 + tsvector 关键词搜索 + RRF 融合 | 两路并行，互补 |
| `embedding` | 仅 pgvector 向量搜索 | 纯语义检索 |
| `keyword` | 仅 tsvector 关键词搜索 | 纯关键词检索 |

**RRF 融合公式（k=60）：**

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

**检索结果注入方式：** 检索结果拆分为两部分注入到消息列表中：

```python
# L3 persona + L2 scene navigation → system message
turn_messages.append({"role": "system", "content": ctx["append_system_context"]})
# L1 facts → assistant message
turn_messages.append({"role": "assistant", "content": ctx["prepend_context"]})
# 用户消息
turn_messages.append({"role": "user", "content": message})
```

最终消息序列为：`[system(系统prompt), system(画像+场景), assistant(相关记忆), user(用户消息)]`

内容截断：每条记忆最大 500 字符，总召回量最大 3000 字符，确保关键证据不被信息洪水稀释。

### 2.8 短时工作记忆

短时工作记忆由三个组件协作，管理当前会话的上下文窗口：

| 组件 | 作用 | 触发条件 | 存储 |
|------|------|---------|------|
| **Checkpointer** | 按 thread_id 持久化 LangGraph 对话状态 | 每次 agent 图执行完毕 | 内存 / PostgreSQL |
| **Summarization** | 压缩旧消息为摘要，释放上下文 | 消息数 >= 50 | 替换 checkpointer 中的消息 |
| **ContextOffload** | 压缩工具结果为语义摘要 + Mermaid 图 | context >= 50% 等三级阈值 | JSONL + refs/*.md + mmds/*.mmd |

**关键设计原则：** 长时记忆的写入（蒸馏/提取）和读取（召回/注入）是**解耦的**。写入发生在响应之后（异步），读取发生在请求之前（同步）。这避免了"读自己刚写的内容"导致的噪声放大问题。

### 2.9 Benchmark 验证

经 PersonaMem Benchmark 验证，四层记忆金字塔设计**准确率提升 10%，Token 用量减少 17%**。

---

## 三、三层自积累 Wiki 知识库

### 3.1 三层架构（Karpathy LLM Wiki 模型）

```
Layer 3  Schema & Workflow    templates/ + skills/ + lint rules
Layer 2  Structured Wiki      wiki/ (Markdown + YAML frontmatter, 8 种实体类型)
Layer 1  Raw Sources          raw/sources/ (PDF/MD/BibTeX, SHA-256 去重, append-only)
```

- **Layer 1** — 原始源文件，SHA-256 哈希去重，`manifest.jsonl` 记录元数据
- **Layer 2** — 结构化 Wiki 页面，YAML frontmatter + `[[wikilink]]` 交叉引用
- **Layer 3** — 模板定义页面结构，Skills 定义操作流程，Lint 规则保障质量

### 3.2 八类学术实体

| 实体类型 | 目录 | 含义 | 创建方式 |
|---------|------|------|---------|
| paper | `wiki/papers/` | 论文 | 定时 ingest / 手动 |
| concept | `wiki/concepts/` | 概念 | LLM 实体提取 / backfill |
| method | `wiki/methods/` | 方法 | LLM 实体提取 / backfill |
| dataset | `wiki/datasets/` | 数据集 | LLM 实体提取 / backfill |
| author | `wiki/authors/` | 作者 | 论文元数据 |
| survey | `wiki/surveys/` | 某方向论文汇总 | 同标签论文 >= 3 篇时自动创建 |
| comparison | `wiki/comparisons/` | 方法对比 | 人工 / Agent 触发 |
| idea | `wiki/ideas/` | 研究想法 | `/ideate` 工作流 |

### 3.3 Wiki 页面结构

每个页面使用 YAML frontmatter + 结构化 Markdown 正文：

```markdown
---
slug: attention-is-all-you-need
title: "Attention Is All You Need"
type: paper
created: 2026-06-08
updated: 2026-06-08
authors: [Vaswani, Shazeer, Parmar, Uszkoreit, Jones, Gomez, Kaiser, Polosukhin]
year: 2017
venue: NeurIPS
arxiv_id: 1706.03762
tags: [transformer, self-attention, sequence-modeling]
related_pages: [concepts/self-attention, concepts/multi-head-attention]
status: high
confidence: high
---

## Background
...

## Core Method
...

## Key Concepts
- [[self-attention]]: Q/K/V 都来自同一序列
- [[multi-head-attention]]: 多个并行的 attention head
```

必填 frontmatter 字段：`slug`, `title`, `type`, `created`, `updated`, `status`, `confidence`。

### 3.4 知识检索（QueryWiki）

三种检索模式：

| 模式 | 引擎 | 说明 |
|------|------|------|
| `bm25` | BM25Okapi + jieba 分词 | 关键词匹配 |
| `semantic` | Embedding 余弦相似度 | 语义检索（阈值 > 0.1）|
| `hybrid`（默认） | BM25 + Embedding + RRF 融合 | 两路互补，k=60 |

索引通过 MD5 指纹（文件名 + mtime）检测变更，仅在变化时重建。

### 3.5 论文导入全流程（paper-ingest, 10 步）

```
Step 1:  register_source        → SHA-256 去重，注册原始文件
Step 2:  pdf_parser             → 提取标题/摘要/作者/全文
Step 3:  LLM 深度分析           → 7 段式分析（Core Problem, Contribution, Method, Concepts, Experiments, Limitations, Related Work）
Step 4:  save_wiki_page (paper) → 生成结构化论文 Wiki 页
Step 5:  save_wiki_page (entity)→ 提取并创建概念/方法/数据集实体页
Step 6:  save_wiki_page (author)→ 创建/更新作者页
Step 7:  自动 Survey 生成       → 同标签论文 ≥ 3 时自动创建 Survey 页
Step 8:  比较页建议             → 不同方法解决同一问题时建议创建 Comparison 页
Step 9:  更新交叉引用           → 维护 Wiki 链接图（正向 + 反向链接）
Step 10: rebuild_index + log    → 重建索引 + 审计日志
```

**正向链接必须同步写入反向链接**：当论文 A 引用概念 B 时，概念 B 的"被引用"列表也必须同步更新——这是 Wiki 引擎的核心不变量。

### 3.6 双向链接机制

- **正向链接**：`related_pages` frontmatter + 正文 `[[wikilink]]`
- **反向链接**：LLM 实体提取创建实体页时，自动给已有实体页追加 `[[paper-slug]]` 反向链接
- **孤立页检测**：lint 检测未被任何 `related_pages` 引用的非 paper 页面
- **悬空引用检测**：lint 检测指向不存在页面的引用

### 3.7 Survey 自动生成

同标签论文达到 3 篇及以上时，系统自动生成综述页：

```
论文 A (tags: [transformer])
论文 B (tags: [transformer])
论文 C (tags: [transformer])
        ↓ 条件触发
survey/transformer-architecture-survey.md
  └── 汇总该方向所有论文，自动聚类
```

### 3.8 知识结晶（Crystallization）

`wiki-ask` Skill 的独特机制——当一次查询综合了 >= 3 个 Wiki 页面并产生新分析时，Agent 主动建议将回答保存为新的 Wiki 页，实现知识的增量沉淀。

### 3.9 每日 arXiv 自动消化

通过 APScheduler 定时任务（默认 8:00 AM, Asia/Shanghai），自动从 arXiv 5 个 RSS feed 获取新论文，经两阶段过滤后执行完整消化流水线：

```
APScheduler (每天 ARXIV_DIGEST_HOUR:00)
  → fetch_arxiv_papers()           # 5 个 RSS feed 并行抓取
  → 关键词粗筛（14 个关键词）        # 快速排除无关论文
  → 语义精排（余弦相似度，阈值 0.3）  # Top 20 最相关论文
  → manifest.jsonl 去重             # 保证幂等
  → run_digest()                   # 逐篇处理：
      → LLM 中文摘要生成
      → PDF 下载 + 解析
      → 7 段式结构化分析
      → 实体提取（概念/方法/数据集）
      → Wiki 页面创建
      → 标签分类（16 类）
      → Survey 条件触发
      → 索引重建 + 日志
  → 企业微信推送                     # Markdown 格式日报
  → Post-Digest Lint               # auto_fix + backfill
```

**5 个 RSS Feed 源：** cs.AI, cs.CL, cs.MA, cs.IR, cs.RO

**双重过滤机制：**

| 过滤层 | 方法 | 作用 |
|--------|------|------|
| 第一层：关键词粗筛 | 14 个关键词文本匹配 | 成本极低，快速排除无关论文 |
| 第二层：语义精排 | Embedding 余弦相似度 × 5 个兴趣主题 | 保证相关性，Top 20 |

### 3.10 Obsidian 集成

Wiki 同时也是一个 Obsidian vault，支持可视化浏览：

- `.obsidian/graph.json` — 图谱配置
- `.obsidian/snippets/entity-colors.css` — 实体类型颜色编码（红=paper, 绿=concept, 蓝=method, 橙=dataset, 紫=author, 青=survey）
- `[[wikilink]]` 语法兼容 Obsidian 的图谱视图和反向链接面板

---

## 四、分层治理与安全审计

### 4.1 五层中间件链

Agent 工具调用经过五层中间件过滤，顺序严格且不可调换：

```
用户输入
    │
    ▼
┌─────────────────────────────────┐
│ ① GuardianMiddleware            │  before_agent
│   → prompt injection 二分类检测  │  可跳转至 end 节点
│   → 失败模式: closed/open       │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ ② HarnessSecurityMiddleware     │  wrap_tool_call
│   → 敏感文件路径拦截（glob）      │
│   → 危险命令拦截（正则）          │
│   → 自定义规则（YAML 热加载）     │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ ③ ContextOffloadMiddleware      │  before_model + wrap_tool_call
│   → 工具结果摘要压缩（L1-L3）     │  渐进式压缩 + Mermaid 注入
│   → 全局工具链 → Mermaid 图      │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ ④ SummarizationMiddleware       │  before_model
│   → 对话历史压缩                 │  50 条触发，保留 20 条
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│ ⑤ HarnessReviewMiddleware       │  after_agent
│   → 质量评分 (1-10)             │
│   → 幻觉风险评估 (low/med/high)  │
│   → 工具使用审计                 │
└─────────────────────────────────┘
    │
    ▼
响应输出
```

**设计约束：**
- 顺序不可调换 — Guardian 必须在最前面（最早拦截），Review 必须在最后面（审查完整对话）
- 每个中间件实现 LangChain `AgentMiddleware` 基类的 7 个钩子子集
- 中间件之间无直接通信，通过 Agent 图的状态传递信息

### 4.2 Guardian：Prompt Injection 防御

**执行时机：** `before_agent`，用户消息进入 Agent 图后第一个执行。

调用轻量级 LLM（默认 gpt-4.1-mini）做二分类，检查四类攻击：

| 攻击类型 | 示例 |
|---------|------|
| Prompt Injection | 忽略指令、角色扮演、泄露 system prompt |
| 敏感信息探测 | 尝试获取 API key、配置 |
| 未授权操作 | 删文件、危险命令 |
| 角色扮演攻击 | DAN 模式、开发者模式 |

**失败模式：**

| 模式 | 上游超时/报错时 | 适用场景 |
|------|---------------|---------|
| `closed`（默认） | 拦截，不执行 | 安全优先 |
| `open` | 放行 | 可用性优先 |

判定为"危险"时，系统直接跳转至终止节点，返回拦截消息，整个 Agent 图被短路。

### 4.3 HarnessSecurity：工具调用安全拦截

**执行时机：** `wrap_tool_call`，每次工具调用前。

**三重检查：**

```python
async def awrap_tool_call(self, request, handler):
    # 1. 受保护文件路径（glob 模式）
    if matches_protected_path(args):  # **/.env, **/*.key, **/.ssh/*
        return ToolMessage(content="[Harness 安全拦截] 访问受保护文件")

    # 2. 危险命令（正则匹配）
    if matches_dangerous_command(args):  # rm -rf, DROP TABLE, /dev/sd
        return ToolMessage(content="[Harness 安全拦截] 危险命令")

    # 3. 自定义规则（YAML 热加载）
    if matches_custom_rules(tool_name, args):
        return ToolMessage(content="[Harness 安全拦截] 违反自定义规则")

    # 通过 → 执行工具
    return await handler(request)
```

**自定义规则（`config/harness_rules.yaml`，热加载，无需重启）：**

```yaml
rules:
  - name: "禁止删除 wiki 页面"
    tool: "terminal"
    command_contains: "rm -rf wiki/"
    action: block
    message: "不允许批量删除 wiki 页面"

  - name: "禁止修改 raw 目录"
    tool: "terminal"
    command_contains: "raw/"
    action: block
    message: "raw/ 目录为只写（append-only），不可修改"
```

### 4.4 ContextOffload：符号化短期记忆

采用 TencentDB Symbolic Short-Term Memory 的四阶段 LLM 管线，将冗长的工具调用结果压缩为高信息密度的语义摘要和 Mermaid 流程图：

```
Tool Call → Buffer(ToolPair)
                ↓ (阈值达到)
           L1: LLM 摘要生成（≤200字符 + 可替换性评分 0-10）
                ↓
        OffloadEntry (JSONL + refs/*.md)
                ↓
           L1.5: 任务生命周期判断（继续/完工/闲聊/新需求）
                ↓
        MMD 文件管理 (创建/激活/清除)
                ↓
           L2: LLM Mermaid 生成（弹性聚合 + 认知墓碑 + 结论导向）
                ↓
        MMD 文件 (mmds/*.mmd)
                ↓
before_model → L3: 渐进式压缩 (按 score)
```

**L1 触发条件（满足任一）：**
- 缓冲区内 ToolPair 数量 >= 4
- 单次工具输出 >= 3000 字符（立即触发）

**L1.5 任务生命周期判断（三步思考链路）：**
1. 剖析最近对话 → 识别意图（继续/完工/闲聊/新需求）
2. 对齐当前 MMD → 评估任务基线
3. 检索历史 MMD → 判断是否延续旧任务

**L2 Mermaid 生成（LLM 作为"任务拓扑架构师"）：**
- 弹性聚合：连续意图相同的工具调用合并为一个节点
- 认知墓碑：死胡同标记为 `blocked` 状态警示节点
- 结论导向：节点 summary 聚焦"得出了什么结论"
- 增量更新：优先使用 `replace_blocks` 局部替换

**L3 渐进式压缩 + MMD 注入（`before_model` 两个独立操作）：**

| 操作 | 方式 | 目的 |
|------|------|------|
| 摘要替换（瘦身） | 将旧 ToolMessage 替换为 L1 摘要文本 | 减少 token 占用 |
| MMD 注入（补视图） | 将活跃任务的 Mermaid 图作为 HumanMessage 注入 | 给 LLM 提供任务全局视图 |

**三级压缩策略：**

| 级别 | context 使用率 | 策略 |
|------|---------------|------|
| 温和压缩 | >= 50% | 按 score 从高到低，将工具结果替换为 L1 摘要 |
| 激进压缩 | >= 85% | 删除最旧的 30% 消息 |
| 紧急截断 | >= 95% | 强制截断到 60% |

**压缩效果示例：**

| 阶段 | 输入 | 输出 | 压缩比 |
|------|------|------|--------|
| 原始工具调用 | — | ~16,000 字符 | 1x |
| L1 摘要 | 16,000 字符 | 4 条摘要 ~600 字符 + score | 27x |
| L2 Mermaid | 4 条摘要 | 2 个节点 ~400 字符 | 40x |

**实测可减少 86% 的上下文占用。**

### 4.5 Summarization：对话历史压缩

**触发条件：** 消息数达到 50 条（`SUMMARIZATION_TRIGGER_MESSAGES`，可配置）
**保留策略：** 保留最近 20 条消息（`SUMMARIZATION_KEEP_MESSAGES`），其余压缩为结构化摘要

压缩后的摘要包含四个结构化部分（中文 prompt 优化）：

```markdown
## 会话目标
用户的主要目标或请求

## 摘要
关键选择、结论、策略，被否决的方案及原因

## 产出物
创建/修改的文件路径和变更描述

## 后续步骤
待完成任务，下一步应该做什么
```

**关键设计：** 摘要仅在对话历史中生效，不影响 session JSON 文件。原始对话通过 `SessionManager` 持久化为 `sessions/*.json` 文件（append-only），长期记忆系统独立从中蒸馏。

### 4.6 HarnessReview：质量审查（闭环）

**执行时机：** Agent 完成后。`after_agent` 的审查逻辑抽离为 `review_conversation()`，由 `AgentManager` 在 `done` 事件后的后台收尾任务统一编排，与记忆捕获串行执行（用户感知延迟为零）。

将完整对话发给 LLM 做四维评估：

```json
{
  "quality_score": 8,
  "hallucination_risk": "low",
  "issues": [],
  "tool_audit": [
    {"tool": "query_wiki", "appropriate": true},
    {"tool": "save_wiki_page", "appropriate": true}
  ],
  "summary": "回答准确引用了 3 个 Wiki 页面，无幻觉风险"
}
```

**闭环机制（审查结果不再只打日志，而是两个实际消费点）：**

1. **记录保存**：`persist_review()` 将每轮审查结果追加到 `reviews/{session_id}.jsonl`（append-only 审计日志，含质量分、幻觉风险、问题列表、工具审计、回复摘录），可被外部监控系统（如 Langfuse）聚合分析。
2. **记忆门控**：当 `hallucination_risk == "high"` 时，该轮 **assistant 回复被挡在可检索记忆层（L1-L3）之外**——L0 原文仍保留（可审计），用户消息仍正常沉淀。由 `HARNESS_REVIEW_BLOCK_MEMORY` 开关控制；`HARNESS_REVIEW_TIMEOUT_MS` 控制 review 等待超时，超时/失败 fail-open 照常捕获（呼应"宁存重复不丢事实"）。

### 4.7 输出依据与证据（Evidence）

**目标：** 让"回答有依据"从 prompt 软约束变成可见的结构化数据，支持审计与追溯。

`agent.py` 在 `astream()` 中收集**知识型工具调用**（`_KNOWLEDGE_TOOLS`：`query_wiki` / `read_wiki_page` / `list_wiki_pages` / `list_source_files` / `search_memory_v3` / `read_file` / `fetch_url`）作为回答证据，在 `done` 事件前通过 SSE 的 `evidence` 事件输出：

```json
{
  "type": "evidence",
  "sources": [
    {"tool": "query_wiki", "query": "注意力机制", "hit": "…检索结果摘要…"},
    {"tool": "read_wiki_page", "query": "wiki/concepts/self-attention.md", "hit": "…页面内容摘要…"}
  ]
}
```

前端将 `sources` 渲染为"回答依据"折叠卡片（工具类型 + 查询参数 + 命中摘要），与"检索到 Memory 片段"卡片（`retrieval` 事件）互补：`retrieval` 展示召回的记忆，`evidence` 展示回答实际依赖的工具依据。

### 4.8 安全与质量测试覆盖

后端测试套件通过 **91 项测试**（`cd backend && python -m pytest`），覆盖：
- 提示词注入拦截（4 类攻击模式）
- 敏感文件保护（glob 模式匹配：`.env`, `*.key`, `.ssh/*` 等）
- 危险命令拦截（`rm -rf`, `DROP TABLE`, `/dev/sd*` 等）
- 自定义规则验证（YAML 热加载 + 生效验证）
- 记忆管线数据一致性（L0 文件存储、L1 去重 merge、L2 增量场景合并、Offload L3 压缩）

---

## 五、知识熵管理机制

### 5.1 熵增问题的本质

在长链推理中，Agent 面临两类熵增：

1. **任务漂移（Task Drift）**：Agent 偏离原始目标，执行无关操作
2. **幻觉（Hallucination）**：Agent 生成与事实不符的内容，尤其是虚构引用

同时，Wiki 知识库随时间推移会产生 6 种熵源：

| 熵源 | 表现 | 治理手段 |
|------|------|---------|
| 重复页面 | 同一篇论文被多次导入 | `register_source` SHA-256 去重 |
| 悬空引用 | 引用了已删除的页面 | `lint_wiki` 定期检查 + auto-fix |
| 孤立页面 | 页面未被任何其他页面引用 | `lint_wiki` 检测 + `backfill` 补全 |
| 过时信息 | 论文结论被后续工作推翻 | `paper-update` skill 更新 |
| 碎片化 | 概念页过多，缺乏整合 | Survey/Comparison 自动创建 |
| 上下文污染 | 错误信息被记忆系统记住 | L1 去重（哈希 + merge/store） |

### 5.2 Wiki Lint 健康检查

三级严重级别 + 6 种检查：

| 级别 | 颜色 | 含义 | 检查项 | 自动修复 |
|------|------|------|--------|---------|
| RED | 红 | 必须修复（结构缺失） | missing_frontmatter, missing_field | ✅ |
| YELLOW | 黄 | 应该修复（一致性问题） | index_mismatch, dangling_reference, invalid_status, invalid_confidence | ✅ |
| BLUE | 蓝 | 建议修复（孤立页） | orphan_page | backfill 模式 |

**Auto-fix（7 种修复）：** 补充缺失 frontmatter、填充缺失必填字段、重置非法 status/confidence、移除悬空引用、重建 index、补充缺失 Related Pages。

**Backfill（缺失实体补全）：** 扫描所有 paper 页面 → LLM 提取缺失的概念/方法/数据集 → 自动创建实体页 → 追加反向链接 → 更新论文 related_pages → 重建索引。

### 5.3 幻觉抑制的六重防线

```
防线 1: SOUL.md "知识诚实" 原则
    → 底层价值观约束，不可覆盖

防线 2: AGENTS.md 引用规范
    → Source vs Agent Judgment + 置信度标注

防线 3: 分析提示词反幻觉指令
    → "Be factual language. If unsure, mark with [confidence: low]"
    → "Do NOT fabricate details not in the paper"

防线 4: Wiki-ask Skill 硬约束
    → Wiki 无结果时必须如实说明，不得伪造引用

防线 5: HarnessReviewMiddleware 事后审查
    → 每轮对话后 LLM 评估 hallucination_risk (low/medium/high)

防线 6: Guardian 前置拦截
    → 阻止 prompt injection 尝试，防止 Agent 被诱导产生幻觉
```

### 5.4 Retry-Lesson-Capture（重试经验捕获）

当任务首次失败、重试成功时，系统自动将"失败原因 → 成功方法"的映射持久化：

```
失败尝试 → 诊断原因 → 成功重试 → 经验捕获
                                    ├── 写入 workspace/MEMORY.md（跨会话持久化）
                                    └── 写入当前 SKILL.md（技能级持久化）
```

下次遇到类似任务时，Agent 会读取这些经验，避免重复犯错。这是**确定性收敛**——系统行为随着使用次数增加而趋向稳定。

### 5.5 Ideate 创意生成约束

`ideate` Skill 将创意生成约束为四条确定性路径：

| 路径 | 策略 | 说明 |
|------|------|------|
| Gap-Driven | 从 `open_questions.md` 出发 | 解决已有论文提出的开放问题 |
| Incremental | 改进现有方法 | 在已有方法上做增量创新 |
| Combination | 组合两个概念/方法 | 跨领域融合 |
| Cross-Pollination | 跨领域迁移 | 从其他领域借鉴思路 |

每条路径生成 1-2 个候选想法，总计 4-8 个。评分公式：`Novelty × 0.4 + Feasibility × 0.35 + Relevance × 0.25`

**Ideation Banlist（禁止列表）**：已保存的想法构成反向约束：
- `rejected`：绝对禁止再次生成
- `proposed`：避免相似度高的重复
- `abandoned`：需要明确理由才能重新考虑

### 5.6 工具注册白名单

`tools/__init__.py` 中显式注册所有可用工具，Agent 只能调用已注册的工具，无法"发明"新工具。这是最强的路径控制手段。

### 5.7 每日消化后自动 Lint

每日 arXiv 消化流水线的最后一步：`LintWikiTool(auto_fix=True, backfill=True)`，确保批量入库后知识图谱的结构完整性。

---

## 六、技术架构附录

### 6.1 系统 Prompt 六层组装

`prompt_builder.py` 按固定顺序组装系统提示词：

```
Layer 6: AGENTS.md      → 详细操作指南（工具协议、引用标准、6 种研究工作流）
Layer 5: MEMORY.md      → 长期记忆（跨会话经验积累）
Layer 4: USER.md        → 用户画像（研究兴趣、学术背景）
Layer 3: IDENTITY.md    → 人格定义（"橘子助手"，沉稳、专业、直接的风格）
Layer 2: SOUL.md        → 核心价值观（知识诚实、可追溯、结构化积累、最小假设、工程思维）
Layer 1: SKILLS_SNAPSHOT → 可用能力清单（XML 格式快照）
```

**关键约束：**
- 每个组件截断至 20,000 字符，防止单一组件垄断上下文窗口
- `SOUL.md` 是最底层约束，不可被上层覆盖
- 变更 workspace 文件无需重启服务，下次请求自动生效

### 6.2 Skill 系统设计

Skill 不是代码，是**结构化的 Prompt 文档**。每个 Skill 是一个目录，包含 `SKILL.md`：

```
skills/
├── paper-ingest/SKILL.md      ← 10 步论文导入工作流
├── paper-update/SKILL.md      ← 论文更新工作流
├── wiki-ask/SKILL.md          ← Wiki 增强问答
├── ideate/SKILL.md            ← 研究灵感生成（4 种路径）
├── wiki-lint/SKILL.md         ← 知识库健康检查
├── quick-lookup/SKILL.md      ← arXiv + Web 快速搜索
├── rag-skill/SKILL.md         ← RAG 检索增强
├── web-search/SKILL.md        ← Web 搜索
├── paper-wiki/SKILL.md        ← 总览 skill
└── retry-lesson-capture/      ← 失败经验捕获
```

**Skill 发现机制：** 启动时扫描 `skills/*/SKILL.md`，解析 YAML frontmatter，生成 `SKILLS_SNAPSHOT.md` 注入 system prompt。LLM 看到 trigger 关键词后，调用 `read_file` 加载完整 SKILL.md。

**设计范式：Skill-Guided Plan-and-Execute** — 上层（Plan）是确定性的（Skill 规定了"做什么"和"用什么工具"），下层（Execute）是灵活的（每步内部 Agent 进行 ReAct 风格的工具调用循环）。

### 6.3 工具调用全链路

```
① 定义层: Python 函数 + @tool 装饰器 / BaseTool 子类
      │    类型注解 + docstring → Pydantic 推断 Schema
      ▼
② Schema 生成层: model_json_schema() → JSON Schema
      ▼
③ API 格式转换: convert_to_openai_tool() → OpenAI function calling 格式
      ▼
④ 模型绑定层: model.bind_tools([tool1, tool2, ...])
      ▼
⑤ API 调用层: HTTP POST → LLM API（tools 参数注入）
      ▼
⑥ LLM 决策层: LLM 分析意图 + 工具 schema → 输出 tool_calls
      ▼
⑦ 安全拦截层: HarnessSecurityMiddleware（glob + 正则 + YAML 规则）
      ▼
⑧ 工具执行层: ToolNode → Pydantic 校验参数 → 执行实际函数 → ToolMessage
      ▼
⑨ 结果回传层: ToolMessage 追加到消息列表 → 回到 ⑤ 下一轮推理 → 直到输出文本
```

**结构化输出（with_structured_output）：** 将 Pydantic schema 伪装成工具，通过 `tool_choice` 强制 LLM 调用，实现 LLM 输出的结构化约束。例如 HarnessReview 的审查报告、Guardian 的安全分类、L1 的记忆提取等。

### 6.4 Session 与 Checkpoint 管理

系统有两套独立的状态持久化机制：

| 机制 | 存储位置 | 内容 | 生命周期 |
|------|----------|------|----------|
| **Session JSON** | `backend/sessions/*.json` | 原始消息历史（append-only） | 永久，手动清理 |
| **LangGraph Checkpointer** | 内存 / PostgreSQL | Agent 图状态（含压缩后的消息） | 会话级或永久 |

**关键特性：**
- Session JSON 是原始记录的 append-only 审计日志，不写入任何压缩摘要
- SummarizationMiddleware 和 ContextOffload 只修改 checkpointer 中的状态，不回写 session JSON
- 后端重启后，Postgres checkpointer 可恢复到最后一次 checkpoint
- 支持 PostgreSQL 连接断线自动重连

### 6.5 关键配置项速查

**长期记忆 v3：**

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `MEMORY_BACKEND` | `off` | 设为 `v3` 启用 |
| `MEMORY_V3_INJECT` | `always` | 注入策略：always / tool / off |
| `MEMORY_V3_RECALL_STRATEGY` | `hybrid` | 检索策略：hybrid / keyword / embedding |
| `MEMORY_V3_PIPELINE_EVERY_N` | `5` | 稳态下每 N 轮触发 L1 |
| `MEMORY_V3_L1_IDLE_TIMEOUT` | `300` | L1 空闲超时（秒） |
| `MEMORY_V3_L2_DELAY_AFTER_L1` | `120` | L1 完成后等待多久触发 L2（秒，兜底） |
| `MEMORY_V3_L2_TRIGGER_N_FACTS` | `10` | 自上次 L2 新增事实数达到多少立即触发 L2 |
| `MEMORY_V3_L3_TRIGGER_EVERY_N` | `50` | 距上次 L3 新增事实达到多少触发 L3 |
| `MEMORY_V3_L3_MAX_INTERVAL` | `86400` | L3 最大间隔兜底（秒，24h） |
| `MEMORY_V3_INJECT_TOP_K` | `5` | 注入的记忆条数 |
| `MEMORY_V3_MAX_TOTAL_RECALL_CHARS` | `3000` | 注入总字符上限 |

**短时记忆：**

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `CHECKPOINTER` | `memory` | 设为 `postgres` 启用持久化 |
| `SUMMARIZATION_ENABLED` | `false` | 启用对话压缩 |
| `SUMMARIZATION_TRIGGER_MESSAGES` | `50` | 触发压缩的消息数 |
| `SUMMARIZATION_KEEP_MESSAGES` | `20` | 压缩后保留的消息数 |

**Harness 审查：**

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HARNESS_REVIEW_ENABLED` | `true` | 是否启用质量审查 |
| `HARNESS_REVIEW_SYNC` | `false` | 同步模式（调试用，阻塞 `done` 事件） |
| `HARNESS_REVIEW_BLOCK_MEMORY` | `true` | 高风险幻觉回答是否阻断进可检索记忆层 |
| `HARNESS_REVIEW_TIMEOUT_MS` | `8000` | review 等待超时（毫秒），超时 fail-open |

**arXiv 消化：**

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ARXIV_DIGEST_HOUR` | `8` | 每天执行时间（Asia/Shanghai） |
| `ARXIV_DIGEST_ENABLED` | `true` | 是否启用定时任务 |
| `WECHAT_WEBHOOK_KEY` | — | 企业微信 Webhook key |

---

> **架构哲学总结：** Agent 不只是回答问题，而是把每次交互都变成知识沉淀。论文不是读完就丢，而是结构化为 wiki 页面；对话不是聊完就忘，而是提取为可检索的原子事实；失败不是白费，而是固化为可复用的教训。
