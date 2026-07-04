# 知识熵管理系统

本文档详细说明 miniOpenClaw 中 Wiki 知识库的完整实现，包括三层架构、8 个管理工具、知识熵治理机制，以及自动化闭环流程。

## 架构总览

Wiki 系统采用 **Karpathy LLM Wiki 三层架构**：

```
Layer 3  Schema & Workflow    templates/ + skills/ + lint rules
Layer 2  Structured Wiki      wiki/ (Markdown + YAML frontmatter, 按实体类型组织)
Layer 1  Raw Sources          raw/sources/ (PDF/MD/BibTeX, SHA-256 去重, append-only)
```

- **Layer 1** — 原始源文件（PDF、MD、BibTeX），注册时计算 SHA-256 哈希，存储在 `raw/sources/`，元数据记录在 `manifest.jsonl`
- **Layer 2** — 结构化 Wiki 页面，每个实体一个 markdown 文件，YAML frontmatter 存储元数据，正文用 `[[wikilink]]` 语法交叉引用
- **Layer 3** — 模板（`templates/tpl-*.md`）定义页面结构，Skills 定义操作流程，Lint 规则保障质量

## 1. 实体类型体系

**文件**: `backend/tools/wiki_engine_tool.py`

Wiki 共支持 **8 种实体类型**，每种映射到一个子目录：

| 实体类型 | 目录 | 含义 | 创建方式 |
|---------|------|------|---------|
| paper | `wiki/papers/` | 论文 | 定时 ingest / 手动 |
| concept | `wiki/concepts/` | 概念 | LLM 实体提取 / backfill |
| method | `wiki/methods/` | 方法 | LLM 实体提取 / backfill |
| dataset | `wiki/datasets/` | 数据集 | LLM 实体提取 / backfill |
| author | `wiki/authors/` | 作者 | 论文元数据 |
| survey | `wiki/surveys/` | 某方向论文汇总 | 标签论文 ≥3 篇时自动创建 |
| comparison | `wiki/comparisons/` | 方法对比 | 人工 / Agent 触发 |
| idea | `wiki/ideas/` | 研究想法 | `/ideate` 工作流 |

每个实体 = 一个 markdown 文件，文件名即 slug。

## 2. Wiki 目录结构

```
backend/wiki/
├── papers/               # 论文页面
├── concepts/             # 概念页面
├── methods/              # 方法页面
├── datasets/             # 数据集页面
├── authors/              # 作者页面
├── surveys/              # Survey 页面（自动生成）
├── comparisons/          # 对比页面（人工触发）
├── ideas/                # 研究想法（/ideate 工作流）
├── graph/                # 自动生成的元文件
│   ├── context_brief.md  # 知识库概览（统计 + 热门主题 + 最新页面）
│   └── open_questions.md # 开放问题汇总（从 Limitations/Open Questions 提取）
├── templates/            # 7 个页面模板 (tpl-*.md)
├── attachments/          # 附件存储
├── .obsidian/            # Obsidian vault 配置
├── index.md              # 自动生成的主索引
├── log.md                # append-only 操作日志
└── tags.yaml             # 16 类研究标签分类体系
```

## 3. 八个 Wiki 工具

**文件**: `backend/tools/wiki_engine_tool.py`

### 3.1 RegisterSourceTool — 源文件注册

**职责**: Layer 1 — 注册原始源文件，SHA-256 去重。

**流程**:
1. 计算文件 SHA-256 哈希
2. 扫描 `manifest.jsonl` 检查是否已注册
3. 未注册则复制到 `raw/sources/`
4. 追加记录到 `manifest.jsonl`（slug, title, authors, year, venue, arxiv_id, doi, hash, ingest_time）

**去重机制**: 相同哈希 → 返回 `already_registered`，不重复处理。

### 3.2 SaveWikiPageTool — 页面创建/更新

**职责**: 创建或更新任意实体类型的 Wiki 页面。

**流程**:
1. 校验 slug（小写字母+连字符，最长 100 字符）
2. 校验 entity_type 在 `ENTITY_TYPES` 中
3. 创建子目录（如不存在）
4. 构建 YAML frontmatter（必填: slug, title, type, created, updated, status, confidence）
5. 如更新，保留原始 `created` 日期
6. 生成 `## Related Pages` 段落（`[[wikilink]]` 语法）
7. 写入文件

**Frontmatter 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| slug | string | URL 友好的唯一标识 |
| title | string | 显示标题 |
| type | string | 实体类型 |
| created | date | 创建日期 |
| updated | date | 最后更新日期 |
| status | enum | complete / in_progress / stub |
| confidence | enum | high / medium / low |
| tags | list | 研究标签列表 |
| related_pages | list | 关联页面路径列表 |
| source_count | int | 来源数量 |
| source_hash | string | 源文件 SHA-256 |

Paper 额外字段: `authors`, `year`, `venue`, `arxiv_id`, `doi`。
Idea 额外字段: `origin_paper`, `addresses_gap`, `priority`, `generation_path`。

### 3.3 ReadWikiPageTool — 页面读取

按 slug 读取，可选 entity_type 缩小搜索范围。返回完整 Markdown（含 frontmatter），截断至 15,000 字符。

### 3.4 ListWikiPagesTool — 页面列表

按 entity_type 和 keyword 过滤。keyword 匹配 title 和 tags（大小写不敏感）。返回 JSON 数组。

### 3.5 RebuildIndexTool — 索引重建

生成 3 个文件：

1. **`wiki/index.md`** — 主索引，按实体类型分组，状态图标：
   - `[x]` complete — `[~]` in_progress — `[ ]` stub — `[?]` unknown

2. **`wiki/graph/context_brief.md`** — 知识库概览：
   - 统计数据（总页面数、各类型数量）
   - Top 10 热门研究主题（按 tag 论文数排序）
   - 最近 5 个页面

3. **`wiki/graph/open_questions.md`** — 开放问题汇总：
   - 扫描所有论文的 `## Open Questions` 段落
   - 扫描所有论文和方法的 `## Limitations` 段落
   - 提取子弹点，聚合为统一文档
   - 附来源归属和问题分布统计

### 3.6 AppendLogTool — 操作日志

追加时间戳条目到 `wiki/log.md`，格式：

```markdown
## YYYY-MM-DD HH:MM — <operation_type>
- <details>
  - Affected: `<page_path>`
```

操作类型: `ingest` / `update` / `query` / `lint` / `fix`。严格 append-only。

### 3.7 LintWikiTool — 健康检查与修复

**6 种检查 × 3 个严重级别**:

| 检查项 | 级别 | 说明 |
|--------|------|------|
| `missing_frontmatter` | RED | 页面没有 YAML frontmatter |
| `missing_field` | RED | 必填 frontmatter 字段缺失 |
| `index_mismatch` | YELLOW | 页面存在但未列入 index.md |
| `dangling_reference` | YELLOW | `related_pages` 指向不存在的页面 |
| `invalid_status` | YELLOW | status 值不在合法集合 |
| `invalid_confidence` | YELLOW | confidence 值不在合法集合 |
| `orphan_page` | BLUE | 非 paper 页面未被任何其他页面引用 |

**Auto-fix**（`auto_fix=true`）— 7 种修复：
1. 补充缺失的 frontmatter
2. 填充缺失的必填字段
3. 重置非法 status 为 `in_progress`
4. 重置非法 confidence 为 `medium`
5. 移除悬空引用
6. 重建 index.md
7. 补充缺失的 `## Related Pages` 段落

**Backfill**（`backfill=true`）— 缺失实体补全：
1. 扫描所有 paper 页面，找出缺少 concept/method/dataset 关联的论文
2. 用 LLM 实体提取识别缺失实体
3. 创建不存在的 concept/method/dataset 页面
4. 给已有实体页追加反向链接
5. 更新论文的 `related_pages`
6. 重建索引

### 3.8 QueryWikiTool — 混合检索

**文件**: `backend/service/wiki_retriever.py`

三种检索模式：

| 模式 | 引擎 | 说明 |
|------|------|------|
| `bm25` | BM25Okapi | jieba 分词，关键词匹配 |
| `semantic` | Embedding 余弦相似度 | 阈值 > 0.1 |
| `hybrid`（默认） | BM25 + Embedding | RRF 融合，k=60 |

**索引构建**:
- 扫描所有 wiki `.md` 文件，解析 frontmatter + 正文
- MD5 指纹（文件名 + mtime）检测变更，仅在变化时重建
- jieba 中文分词构建 BM25 索引
- Embedding 模型生成向量索引

**RRF 融合公式**: `score = Σ 1/(60 + rank)`

## 4. 双向链接机制

### 正向链接

存储在两处：
1. **YAML frontmatter** 的 `related_pages` — 路径列表，如 `["concepts/rag", "methods/rrf"]`
2. **正文** 的 `## Related Pages` — `[[bare-slug]]` wikilink 格式

`SaveWikiPageTool` 保存时自动同步两者。

### 反向链接

- LLM 实体提取创建实体页时，自动给已有实体页追加 `[[paper-slug]]` 反向链接
- backfill 模式也会补充反向链接

### 孤立页与悬空引用检测

- **孤立页**: lint 检测未被任何 `related_pages` 引用的非 paper 页面（BLUE）
- **悬空引用**: lint 检测指向不存在页面的 `related_pages` 条目（YELLOW）

## 5. 标签分类体系

**文件**: `backend/wiki/tags.yaml`

16 个预定义研究领域，每个标签包含 id、label、keywords：

| 标签 ID | 领域 | 关键词示例 |
|---------|------|-----------|
| agent-architecture | Agent 架构 | agent, ReAct, plan-execute, tool-use |
| memory-systems | 记忆系统 | memory, long-term memory, context compression |
| retrieval-augmented | 检索增强 | RAG, retrieval, hybrid search |
| multi-agent | 多智能体 | multi-agent, coordination, collaboration |
| self-evolution | 自我进化 | self-improvement, adaptation, online learning |
| reasoning-planning | 推理规划 | reasoning, planning, chain-of-thought |
| evaluation-benchmark | 评测基准 | benchmark, evaluation, metrics |
| safety-alignment | 安全对齐 | safety, alignment, hallucination |
| training-optimization | 训练优化 | RLHF, fine-tuning, distillation |
| knowledge-management | 知识管理 | knowledge graph, ontology |
| nlp-tasks | NLP 任务 | summarization, translation, question-answering |
| efficiency-scalability | 效率与扩展 | efficiency, latency, quantization |
| web-interaction | Web 交互 | web agent, GUI agent, browser |
| code-generation | 代码生成 | code generation, programming |
| robotics-embodied | 机器人/具身 | robotics, embodied, manipulation |
| finance-domain | 金融领域 | finance, trading, market |

**用途**: 论文分类、Survey 触发条件、语义检索辅助。

## 6. 页面模板

**目录**: `backend/wiki/templates/`

| 模板 | 类型 | 核心段落 |
|------|------|---------|
| `tpl-paper.md` | paper | Overview, Core Problem, Method, Key Concepts, Experiments, Limitations, Open Questions |
| `tpl-concept.md` | concept | Definition, Intuition, How It Works, Key Papers, Variants, Applications, Open Problems |
| `tpl-method.md` | method | Mechanism, Procedure, Assumptions, Tradeoff Profile, Limitations, Evaluated By |
| `tpl-dataset.md` | dataset | Overview, Size & Structure, Splits, Evaluation Metrics, Leaderboard, Known Issues |
| `tpl-author.md` | author | Profile, Research Areas, Key Contributions, Recent Work |
| `tpl-survey.md` | survey | Overview, Taxonomy, Timeline, Key Methods Comparison, Open Problems, References |
| `tpl-comparison.md` | comparison | Overview, Dimension-by-Dimension Comparison, When to Use Which, References |

模板使用 `{{title}}`、`{{date}}` 占位符，正文用 `[[wikilink]]` 语法交叉引用。

## 7. 知识熵管理

### 什么是知识熵

知识熵指知识库随时间推移产生的质量退化。系统识别了 **6 种熵源**：

| 熵源 | 表现 | 治理手段 |
|------|------|---------|
| 重复页面 | 同一篇论文被多次导入 | `register_source` SHA-256 去重 |
| 悬空引用 | 引用了已删除的页面 | `lint_wiki` 定期检查 |
| 孤立页面 | 页面未被任何其他页面引用 | `lint_wiki` 检测 + `backfill` 补全 |
| 过时信息 | 论文结论被后续工作推翻 | `paper-update` skill 更新 |
| 碎片化 | 概念页过多，缺乏整合 | 条件触发 survey/comparison 自动创建 |
| 上下文污染 | 错误信息被记忆系统记住 | L1 去重（store/update/merge/skip） |

### 治理工具

| 工具 | 作用 | 触发方式 |
|------|------|---------|
| `lint_wiki` | 三级健康检查 + 自动修复 | 定时 / 手动 |
| `rebuild_index` | 重建索引和元文件 | 每次 ingest 后自动 |
| `append_log` | 记录操作审计日志 | 每次变更自动 |
| `paper-update` | 论文版本更新 | 手动 / Agent |
| `retry-lesson-capture` | 捕获失败→成功的经验 | 重试成功后 |

### 三级严重级别

| 级别 | 颜色 | 含义 | 自动修复 |
|------|------|------|---------|
| RED | 红 | 必须修复（结构缺失） | ✅ |
| YELLOW | 黄 | 应该修复（一致性问题） | ✅ |
| BLUE | 蓝 | 建议修复（孤立页） | backfill 模式 |

### 自动化熵减

定时任务每次 ingest 后自动执行 `LintWikiTool(auto_fix=True, backfill=True)`：

1. 修复结构问题（缺失 frontmatter、悬空引用、非法状态值）
2. 补全缺失实体页面（LLM 提取 + 创建 + 反向链接）
3. 重建索引

## 8. 自动化闭环流程

### 每日定时任务

```
APScheduler (每天 ARXIV_DIGEST_HOUR:00 Asia/Shanghai)
  → fetch_arxiv_papers()           # RSS 抓取 + 双重过滤
  → filter_new_papers()            # manifest.jsonl 去重
  → run_digest()                   # 逐篇处理:
      → _create_wiki_pages()       # 创建 paper + concept + method + dataset + author
      → _check_and_create_surveys() # 标签论文 ≥3 篇时创建 survey
      → RebuildIndexTool            # 重建索引
      → AppendLogTool               # 记录日志
  → send_daily_digest()            # 企业微信推送
  → run_post_digest_lint()         # LintWikiTool(auto_fix + backfill)
```

### Obsidian 集成

Wiki 同时也是一个 Obsidian vault，支持可视化浏览：

- `.obsidian/graph.json` — 图谱配置
- `.obsidian/snippets/entity-colors.css` — 实体类型颜色编码：
  - 红色 = paper — 绿色 = concept — 蓝色 = method
  - 橙色 = dataset — 紫色 = author — 青色 = survey
- `[[wikilink]]` 语法兼容 Obsidian 的图谱视图和反向链接面板

## 9. Skills 工作流

| Skill | 职责 | 触发条件 |
|-------|------|---------|
| `paper-ingest` | 10 步论文收录流程 | 用户提供论文 |
| `paper-update` | 论文版本增量更新 | 论文有新版本 |
| `wiki-ask` | Wiki 增强问答（检索 + 引用） | 用户提问 |
| `wiki-lint` | 健康检查文档 | 手动触发 |
| `ideate` | 研究想法生成（4 种路径） | 用户要求 |
| `retry-lesson-capture` | 失败经验捕获 | 重试成功后 |
| `paper-wiki` | 总览 skill，链接上述子 skill | 架构参考 |

### paper-ingest 流程（10 步）

1. `register_source` — 注册源文件
2. PDF 解析 — 提取全文
3. LLM 分析 — 7 段式结构化分析
4. 创建 paper 页面
5. LLM 实体提取 — concepts / methods / datasets
6. 创建实体页面 + 反向链接
7. 创建/更新 author 页面
8. 条件触发 survey（标签论文 ≥3）和 comparison（同主题不同方法）
9. 更新交叉引用
10. 重建索引 + 记录日志

### ideate 工作流（4 种路径）

| 路径 | 策略 | 说明 |
|------|------|------|
| Gap-Driven | 从 open_questions.md 出发 | 解决已有论文提出的开放问题 |
| Incremental | 改进现有方法 | 在已有方法上做增量创新 |
| Combination | 组合两个概念/方法 | 跨领域融合 |
| Cross-Pollination | 跨领域迁移 | 从其他领域借鉴思路 |

**评分**: `score = Novelty × 0.4 + Feasibility × 0.35 + Relevance × 0.25`

**去重**: 维护 idea banlist（rejected=禁止、proposed=避免相似、abandoned=可重新考虑）

## 10. 关键设计决策

| 决策 | 原因 |
|------|------|
| 文件即实体（1 实体 = 1 markdown 文件） | 可读、可审计、git 友好、无数据库依赖 |
| YAML frontmatter + 正文 | 结构化数据与人类可读内容共存 |
| `[[wikilink]]` 语法 | 兼容 Obsidian 图谱视图 |
| SHA-256 去重 | 防止重复导入，支持版本检测 |
| 三级严重级别 | 区分必须修复、应该修复、建议修复 |
| 自动 backfill | 每次 ingest 后补全缺失实体，减少人工维护 |
| append-only 日志 | 完整审计轨迹，不可篡改 |
| RRF 混合检索 | 关键词精确匹配 + 语义模糊匹配互补 |
| Survey 条件触发 | 论文积累到一定量后自动聚类，避免过早创建空页 |
| Obsidian 兼容 | 支持可视化浏览，降低使用门槛 |
