---
name: paper-ingest
description: 论文导入 — 将学术论文导入 Wiki 知识库，自动创建论文页、概念页、方法页、数据集页、作者页，条件触发 Survey 和 Comparison 页面。
---

# Paper Ingest

将学术论文完整导入 Wiki 知识库。遵循 Karpathy LLM Wiki 设计，一次导入自动构建知识图谱。

## 触发条件

- "ingest" / "导入论文" / "添加这篇论文"
- 提供了 PDF 文件或 arXiv 链接
- "帮我把这篇论文加到 wiki"

## 可用工具

| 工具 | 用途 |
|------|------|
| `pdf_parser` | 解析 PDF 提取文本和元信息 |
| `register_source` | 注册原始资料，计算 SHA-256 哈希 |
| `save_wiki_page` | 保存 wiki 页面 |
| `read_wiki_page` | 读取 wiki 页面 |
| `list_wiki_pages` | 列出 wiki 页面 |
| `rebuild_index` | 重建 wiki/index.md |
| `append_log` | 追加操作日志 |
| `query_wiki` | 搜索 wiki（用于 Comparison 检测） |
| `fetch_url` | 抓取 arXiv 页面 |
| `python_repl` | 下载 PDF 等辅助任务 |

## 导入流程

### 步骤 1：注册原始资料

**PDF 文件：**
```
register_source(file_path="papers/attention.pdf", title="Attention Is All You Need", authors="Vaswani et al.", year=2017, venue="NeurIPS", arxiv_id="1706.03762")
```

**arXiv URL：**
1. 用 `fetch_url` 抓取 arXiv 页面获取元信息
2. 用 `python_repl` 下载 PDF：`https://arxiv.org/pdf/<ID>.pdf`
3. 用 `register_source` 注册下载的 PDF

**检查重复：** 如果返回 `already_registered`，提示用户是否强制更新。

### 步骤 2：解析论文

```
pdf_parser(file_path="papers/attention.pdf", max_pages=30)
```

### 步骤 3：分析并提取信息

阅读全文，提取：论文标题、作者信息、研究背景、核心问题、方法概述、关键技术细节、实验结果、主要贡献、局限性、关键概念、相关方法、涉及数据集。

### 步骤 4：生成论文页面

```
save_wiki_page(
    slug="attention-is-all-you-need",
    title="Attention Is All You Need",
    entity_type="paper",
    content="<生成的 Markdown>",
    authors="Ashish Vaswani,Noam Shazeer",
    year=2017, venue="NeurIPS", arxiv_id="1706.03762",
    tags="transformer,attention,NLP",
    related_pages="concepts/self-attention,methods/multi-head-attention",
    source_hash="<hash>", status="complete", confidence="high"
)
```

**论文页面模板：**
```markdown
## Overview
<200-400 字概述>
## Background
<领域背景>
## Core Problem
<核心问题>
## Method
<方法详细描述>
### Key Technical Details
- **<技术点>**: <描述>
## Key Concepts
### <概念>
<定义，链接 [[concept-slug]]>
## Experiments
### Setup / Results / Analysis
## Contributions
## Limitations
## Related Work
<带 [[paper-slug]] 链接>
## Open Questions
```

### 步骤 5：创建实体页面

为论文中的关键概念、方法、数据集创建独立页面：

**概念页：** `save_wiki_page(slug="self-attention", entity_type="concept", ...)`
**方法页：** `save_wiki_page(slug="multi-head-attention", entity_type="method", ...)`
**数据集页：** `save_wiki_page(slug="wmt-2014-en-de", entity_type="dataset", ...)`

### 步骤 6：创建/更新作者页面

对每位作者（最多 5 位）：
1. 用 `list_wiki_pages(entity_type="author", keyword="<姓>")` 检查是否已存在
2. 不存在 → 创建新作者页（status=in_progress）
3. 已存在 → 在 Key Contributions 和 Recent Work 中追加新论文

**slug 格式：** `名-姓` 全小写，如 `geoffrey-hinton`

### 步骤 7：自动创建 Survey（条件触发）

**触发条件：** 同一 tag 的论文 ≥ 3 篇，且该主题尚无 survey。

操作：`list_wiki_pages(entity_type="paper")` → 统计 tags → 创建 `save_wiki_page(entity_type="survey")`

### 步骤 8：建议 Comparison（条件触发）

**触发条件：** 新论文与已有论文在同一问题上使用不同方法。

操作：`query_wiki` 搜索同主题 → 对比方法差异 → **先询问用户** → `save_wiki_page(entity_type="comparison")`

### 步骤 9：更新已有页面

更新受影响的概念页（Key Papers）、方法页（Evaluated By）的交叉引用和 source_count。

### 步骤 10：重建索引和日志

```
rebuild_index()
append_log(operation="ingest", details="...", pages_affected="...")
```

## 故障排查

- **PDF 解析失败** → 确认路径和格式，扫描版需 OCR
- **register_source 返回重复** → hash 相同跳过，不同则确认是否更新
