# Tool Result: tool

**Ref ID**: `ref_ed2d49da1616`

## Full Output

```
---
name: paper-wiki
description: 学术论文 Wiki Agent — 构建和维护结构化学术知识库。支持论文导入(ingest)、知识查询(query)、健康检查(lint)、增量更新。遵循 Karpathy LLM Wiki 三层架构：Raw Sources → Wiki → Schema。
---

# Academic Paper Wiki Agent

遵循 Karpathy LLM Wiki 设计模式，构建可持续演化、可维护、可追溯的学术论文知识库。

## 架构概览

```
raw/sources/          ← Layer 1: 原始资料（只读，注册后不可修改）
wiki/                 ← Layer 2: 知识库（Agent 维护的结构化 Markdown）
workspace/AGENTS.md                ← Layer 3: Schema 和工作流规范
```

## 触发条件

当用户提到以下意图时使用本 Skill：
- "ingest" / "导入论文" / "添加这篇论文" / 提供了 PDF 或 arXiv 链接
- "query wiki" / "wiki 里有没有关于 X 的内容" / "根据 wiki 回答"
- "lint wiki" / "检查 wiki 健康状态" / "wiki 有没有问题"
- "update" / "更新 wiki" / "重建索引"

## 可用工具

| 工具 | 用途 |
|------|------|
| `pdf_parser` | 解析 PDF 提取文本和元信息 |
| `register_source` | 注册原始资料到 raw/sources/，计算 SHA-256 哈希 |
| `save_wiki_page` | 保存任意类型的 wiki 页面 |
| `read_wiki_page` | 读取 wiki 页面 |
| `list_wiki_pages` | 列出 wiki 页面（可按类型/关键词过滤） |
| `rebuild_index` | 重建 wiki/index.md |
| `append_log` | 追加操作日志到 wiki/log.md |
| `lint_wiki` | 健康检查，返回红/黄/蓝三级问题报告 |
| `query_wiki` | 按关键词搜索 wiki，返回相关页面摘要 |
| `fetch_url` | 抓取 arXiv 页面获取论文信息 |
| `python_repl` | 下载 PDF、数据处理等辅助任务 |

## 核心流程一：Ingest（导入论文）

### 步骤 1：注册原始资料

**PDF 文件：**
```
register_source(file_path="papers/attention.pdf", title="Attention Is All You Need", authors="Vaswani et al.", year=2017, venue="NeurIPS", arxiv_id="1706.03762")
```

**arXiv URL：**
1. 用 `fetch_url` 抓取 arXiv 页面获取元信息
2. 用 `python_repl` 下载 PDF：`https://arxiv.org/pdf/<ID>.pdf`
3. 用 `register_source` 注册下载的 PDF

**检查重复：** 如果 `register_source` 返回 `already_registered`，提示用户是否强制更新。

### 步骤 2：解析论文

```
pdf_parser(file_path="papers/attention.pdf", max_pages=30)
```

返回 JSON：`title`, `authors`, `abstract`, `full_text`, `page_count`

### 步骤 3：分析并提取信息

仔细阅读全文，提取：

1. **论文标题** — 从解析结果获取
2. **作者信息** — 姓名、机构
3. **研究背景** — 所属领域、已有工作
4. **核心问题** — 论文要解决什么
5. **方法概述** — 核心思路和创新点
6. **关键技术细节** — 2-5 个要点
7. **实验结果** — 数据集、基线、指标、具体数字
8. **主要贡献** — Introduction 末尾的贡献列表
9. **局限性** — 作者承认的或可推断的不足
10. **关键概念** — 3-8 个需要解释的专业术语
11. **相关方法** — 对比方法和重要引用
12. **涉及数据集** — 使用的数据集名称和规模

### 步骤 4：生成论文页面

使用 `save_wiki_page` 保存论文详情页：

```
save_wiki_page(
    slug="attention-is-all-you-need",
    title="Attention Is All You Need",
    entity_type="paper",
    content="<生成的 Markdown 内容>",
    authors="Ashish Vaswani,Noam Shazeer",
    year=2017,
    venue="NeurIPS",
    arxiv_id="1706.03762",
    tags="transformer,attention,NLP",
    related_pages="concepts/self-attention,methods/multi-head-attention",
    source_hash="<从 register_source 返回的 hash>",
    status="complete",
    confidence="high"
)
```

**论文页面内容模板：**

```markdown
## Overview

<200-400 字概述：背景、问题、方法、主要发现>

## Background

<领域背景，帮助非专业读者理解>

## Core Problem

<核心问题的清晰描述>

## Method

<方法详细描述>

### Key Technical Details

- **<技术点 1>**: <描述>
- **<技术点 2>**: <描述>

## Key Concepts

### <概念 1>
<定义和解释，链接到概念页面: [[concept-slug]]>

## Experiments

### Setup
<数据集、基线、指标>

### Results
<具体数字>

### Analysis
<讨论>

## Contributions

- 贡献 1
- 贡献 2

## Limitations

<局限性>

## Related Work

<相关工作，带 [[paper-slug]] 链接>

## Open Questions

<可进一步探索的问题>
```

### 步骤 5：创建实体页面

为论文中提到的关键概念、方法、数据集创建独立页面：

**概念页面：**
```
save_wiki_page(
    slug="self-attention",
    title="Self-Attention",
    entity_type="concept",
    content="## Definition\n...\n## Intuition\n...\n## How It Works\n...",
    tags="attention,mechanism",
    related_pages="papers/attention-is-all-you-need",
    source_count=1,
    status="complete"
)
```

**方法页面：**
```
save_wiki_page(
    slug="multi-head-attention",
    title="Multi-Head Attention",
    entity_type="method",
    content="## Mechanism\n...\n## Procedure\n...\n## Tradeoff Profile\n...",
    tags="attention,parallel",
    related_pages="concepts/self-attention",
    source_count=1
)
```

**数据集页面：**
```
save_wiki_page(
    slug="wmt-2014-en-de",
    title="WMT 2014 English-German",
    entity_type="dataset",
    content="## Overview\n...\n## Size & Structure\n...",
    tags="translation,NLP"
)
```

### 步骤 6：创建/更新作者页面（自动）

对论文的**每一位作者**，执行以下操作：

1. 用 `list_wiki_pages(entity_type="author", keyword="<作者姓>")` 检查是否已有该作者页面
2. 如果**不存在**，创建新作者页面：

```
save_wiki_page(
    slug="ashish-vaswani",
    title="Ashish Vaswani",
    entity_type="author",
    content="## Profile\n\nAshish Vaswani 是 Google Brain 的研究员，主要研究方向为自然语言处理和深度学习。\n\n## Research Areas\n\n- Natural Language Processing\n- Attention Mechanisms\n- Sequence Modeling\n\n## Key Contributions\n\n- [[papers/attention-is-all-you-need]] — 提出 Transformer 架构\n\n## Recent Work\n\n| 论文 | 年份 | Venue |\n|------|------|-------|\n| [Attention Is All You Need](../papers/attention-is-all-you-need.md) | 2017 | NeurIPS |",
    tags="NLP,attention,transformer",
    related_pages="papers/attention-is-all-you-need",
    status="in_progress",
    confidence="medium"
)
```

3. 如果**已存在**，读取现有页面，在以下位置追加新论文信息：
   - `Key Contributions` 列表中添加新论文链接
   - `Recent Work` 表格中添加新行
   - `related_pages` 中添加新论文
   - 更新 `source_count`（+1）
   - 用 `save_wiki_page` 重新保存（保留原有内容）

**注意事项：**
- slug 格式：`名-姓` 全小写，连字符分隔（如 `geoffrey-hinton`）
- 同名作者去重：先按姓匹配，再核对机构/领域是否一致
- 首次创建时 status 设为 `in_progress`（信息不完整），后续补充后可改为 `complete`

### 步骤 7：自动检测并创建 Survey 页面（条件触发）

ingest 完成后，检查是否满足创建 survey 的条件：

**触发条件：** wiki 中同一主题（相同 tags）的论文 **≥ 3 篇**时自动触发。

**操作步骤：**

1. 用 `list_wiki_pages(entity_type="paper")` 获取所有论文
2. 统计各 tag 出现的论文数量
3. 如果某个 tag 对应 ≥ 3 篇论文，检查 `wiki/surveys/` 下是否已有该主题的 survey
4. 如果没有，自动创建 survey 页面：

```
save_wiki_page(
    slug="attention-mechanisms-survey",
    title="Attention Mechanisms 综述",
    entity_type="survey",
    content="## Overview\n\n本综述整理了 Wiki 中关于 Attention Mechanisms 的所有论文，归纳研究脉络和关键技术演进。\n\n## Taxonomy\n\n### 基础注意力机制\n- [[concepts/self-attention]] — 自注意力基础\n- [[methods/multi-head-attention]] — 多头注意力\n\n### 变体与改进\n- （根据实际论文补充）\n\n## Timeline\n\n| 年份 | 论文 | 核心贡献 |\n|------|------|----------|\n| 2017 | [Attention Is All You Need](../papers/attention-is-all-you-need.md) | 提出 Transformer |\n\n## Key Methods Comparison\n\n| 方法 | 复杂度 | 优点 | 缺点 |\n|------|--------|------|------|\n| Self-Attention | O(n²) | 并行化、全局视野 | 长序列开销大 |\n\n## Open Problems\n\n- （根据已有论文的 Limitations 和 Open Questions 汇总）\n\n## References\n\n（列出所有相关论文的链接）",
    tags="attention,survey",
    related_pages="papers/attention-is-all-you-need",
    source_count=3,
    status="in_progress",
    confidence="medium"
)
```

5. 如果 survey 已存在，读取并更新：
   - 在 Timeline 表格中添加新论文
   - 在 Taxonomy 中归类新论文
   - 更新 source_count
   - 在 References 中添加新论文链接

**注意：** survey 的 slug 格式为 `<主题关键词>-survey`，如 `transformer-architecture-survey`。

### 步骤 8：自动检测并建议 Comparison 页面（条件触发）

ingest 完成后，检查是否满足创建 comparison 的条件：

**触发条件：** 新导入的论文与 wiki 中已有的论文在**同一问题**上使用了**不同方法**。

**操作步骤：**

1. 用 `query_wiki` 搜索与新论文相同主题的已有论文
2. 读取已有论文的 Method 章节
3. 对比新旧论文的方法差异：
   - 如果方法**本质不同**（如 RNN vs Transformer、CNN vs Attention）→ 建议创建 comparison
   - 如果方法**相似/改进**（如只是参数调整）→ 不创建 comparison
4. 如果满足条件，**先询问用户**是否创建，然后执行：

```
save_wiki_page(
    slug="transformer-vs-rnn",
    title="Transformer vs RNN",
    entity_type="comparison",
    content="## Overview\n\n对比 Transformer 和 RNN 两种序列建模方法的核心差异。\n\n## Dimension-by-Dimension Comparison\n\n| 维度 | Transformer | RNN |\n|------|-------------|-----|\n| 并行化 | 完全并行 | 顺序处理 |\n| 长距离依赖 | 直接建模 | 需要门控机制缓解梯度消失 |\n| 计算复杂度 | O(n²d) | O(nd²) |\n| 内存占用 | O(n²) | O(n) |\n| 位置信息 | 需要位置编码 | 天然包含顺序信息 |\n\n## When to Use Which\n\n- **选 Transformer**：长序列、需要并行化训练、全局依赖重要\n- **选 RNN**：超长序列（内存受限）、流式处理、低延迟推理\n\n## References\n\n- [Attention Is All You Need](../papers/attention-is-all-you-need.md)\n- （已有 RNN 相关论文链接）",
    tags="transformer,RNN,comparison",
    related_pages="papers/attention-is-all-you-need",
    source_count=2,
    status="complete",
    confidence="high"
)
```

**注意：**
- comparison 一定是**两种不同方法**的对比，不是同一方法的不同版本
- slug 格式为 `<方法a>-vs-<方法b>`
- comparison 页面的 confidence 默认设为 `high`（因为是基于已收录论文的客观对比）

### 步骤 9：更新已有页面

检查 wiki 中是否已有相关页面。如果有：
- 读取现有页面内容
- 在相关章节补充新的交叉引用
- 更新 `related_pages` 字段
- 更新 `source_count`
- 用 `save_wiki_page` 重新保存

**特别注意：** 此步骤需更新以下所有受影响的页面：
- 论文中引用的已有概念页 → 在 `Key Papers` 中添加新论文
- 论文中引用的已有方法页 → 在 `Evaluated By` 中添加新论文
- 论文的作者页 → 已在步骤 6 处理
- 论文所在主题的 survey 页 → 已在步骤 7 处理

### 步骤 10：重建索引和记录日志

```
rebuild_index()
append_log(operation="ingest", details="Ingested: Attention Is All You Need (Vaswani et al., 2017). Created: 1 paper, 3 concepts, 2 methods, 1 dataset, 2 author pages. Updated: survey/attention-mechanisms-survey.", pages_affected="papers/attention-is-all-you-need,concepts/self-attention,concepts/positional-encoding,methods/multi-head-attention,methods/scaled-dot-product-attention,datasets/wmt-2014-en-de,authors/ashish-vaswani,authors/noam-shazeer,surveys/attention-mechanisms-survey")
```

## 核心流程二：Query（知识查询）

当用户提出学术问题时，优先查询 wiki 而非原始资料：

### 步骤 1：搜索 Wiki

```
query_wiki(query="self-attention mechanism in transformers", max_pages=10)
```

### 步骤 2：阅读相关页面

根据搜索结果，用 `read_wiki_page` 读取最相关的 3-5 个页面。

### 步骤 3：综合回答

- 基于 wiki 内容综合回答
- 用 `[[slug]]` 格式引用来源页面
- 区分"论文原文结论"和"Agent 综合判断"
- 对不确定内容标记 `confidence`

### 步骤 4：沉淀新知识（可选）

如果回答产生了新的分析、对比或综述价值：
- 建议用户创建新的 survey 或 comparison 页面
- 如果用户同意，用 `save_wiki_page` 保存
- 用 `append_log` 记录

## 核心流程三：Lint（健康检查）

```
lint_wiki(auto_fix=false)
```

### 检查项

| 检查 | 严重度 | 说明 |
|------|--------|------|
| missing_frontmatter | red | 页面缺少 YAML frontmatter |
| missing_field | red | 缺少必填字段 |
| index_mismatch | red | index.md 与实际文件不一致 |
| dangling_reference | yellow | related_pages 指向不存在的页面 |
| invalid_status | yellow | status 值不在允许列表 |
| orphan_page | blue | 页面未被任何其他页面引用 |

### 自动修复

设置 `auto_fix=true` 会自动修复安全问题：
- 重建 index.md

### 日志记录

```
append_log(operation="lint", details="Found 3 issues (1 red, 2 yellow), auto-fixed: rebuilt index.md")
```

## 核心流程四：Update（增量更新）

当用户要求更新已导入的论文：

### 步骤 1：检查变化

```
register_source(file_path="papers/attention_v2.pdf", ...)
```

如果返回 `already_registered`，比较 hash：
- hash 相同 → 提示"论文未变化"
- hash 不同 → 继续更新流程

### 步骤 2：增量更新

1. 重新解析论文
2. 对比新旧内容差异
3. 更新论文页面，保留旧结论并标注新结论
4. 更新受影响的概念页、方法页
5. 重建索引

### 步骤 3：记录变更

```
append_log(operation="update", details="Updated: Attention Is All Y
```
