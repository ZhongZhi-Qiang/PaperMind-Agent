---
name: wiki-ask
description: Wiki 增强问答 — 基于 wiki 知识库的学术问题回答。使用 BM25 + embedding 混合检索，综合多个 wiki 页面回答，带来源引用。支持 Crystallize 沉淀。
---

# Wiki-Enhanced Q&A

基于 wiki 知识库回答学术问题。先检索 wiki，再综合回答，最后可选沉淀。

## 触发条件

当用户提出以下类型问题时使用本 Skill：
- 学术概念问题："什么是 agent memory？"
- 方法对比问题："RAG 和 long-context 哪个更好？"
- 研究综述问题："agent 工具使用有哪些方法？"
- 论文相关问题："ElasticMem 的核心贡献是什么？"
- 研究趋势："最近 agent 安全方面有什么进展？"

**不适用场景**：
- 纯闲聊、非学术问题 → 不使用本 Skill
- 用户明确要求"帮我搜一下 arXiv" → 使用 web-search
- 用户提供了 PDF 要求导入 → 使用 paper-wiki

## 可用工具

| 工具 | 用途 |
|------|------|
| `query_wiki` | 混合检索 wiki（BM25 + embedding + RRF） |
| `read_wiki_page` | 读取 wiki 页面完整内容 |
| `list_wiki_pages` | 列出 wiki 页面 |
| `save_wiki_page` | 保存新页面（Crystallize 时使用） |
| `append_log` | 记录操作日志 |

## 核心流程

### Step 1: 混合检索

```
query_wiki(query="<用户问题>", mode="hybrid", max_pages=10)
```

**模式选择**：
- `mode="hybrid"`（默认）：BM25 关键词 + embedding 语义，RRF 融合排序。适合大多数问题。
- `mode="bm25"`：仅关键词匹配。适合精确术语查询（如 "ElasticMem"）。
- `mode="semantic"`：仅语义检索。适合概念性问题（如 "agent 如何记住用户偏好"）。

### Step 2: 阅读 Top 结果

读取检索结果中最相关的 3-5 个页面的完整内容：

```
read_wiki_page(slug="<slug1>")
read_wiki_page(slug="<slug2>")
read_wiki_page(slug="<slug3>")
```

**选择标准**：
- score 最高的优先
- 类型多样性：尽量覆盖 paper + concept + method
- 如果 top 结果都是 paper，额外读取相关的 concept/method 页面

### Step 3: 综合回答

基于阅读的 wiki 内容综合回答，遵循以下规范：

**引用格式**：
- 论文原文结论：`> Source: [Title](papers/slug.md), Section X`
- Agent 综合判断：`> Agent judgment (confidence: high/medium/low)`
- 概念定义：`> Definition: [Concept](concepts/slug.md)`

**回答结构**：
1. 先给结论（1-2 句话）
2. 再给依据（分点说明，每点带引用）
3. 如有不确定内容，明确标注 confidence
4. 如有多个相关来源，用 `[[slug]]` 交叉引用

**示例回答**：
```markdown
Agent 的长期记忆主要有三种方案：参数化记忆、检索增强记忆、和混合记忆。

1. **参数化记忆**：将知识编码到模型参数中，如通过 fine-tuning 更新。优点是推理快，缺点是更新成本高。
   > Source: [MemoryBank](papers/memorybank.md), Section 2

2. **检索增强记忆（RAG）**：外部存储 + 检索注入，如 ElasticMem 的弹性分层架构。
   > Source: [ElasticMem](papers/elasticmem.md), Section 3

3. **混合方案**：结合参数化和检索，如 MemoryBank 的遗忘曲线 + RAG 混合。
   > Agent judgment (confidence: medium) — based on papers/memorybank, papers/elasticmem

> Agent judgment (confidence: high) — based on wiki 中已收录的 5 篇 memory 相关论文
```

### Step 4: Crystallize（可选沉淀）

如果回答产生了新的分析价值，建议沉淀为 wiki 页面：

**触发条件**：
- 回答综合了 ≥ 3 个 wiki 页面的信息
- 产生了新的对比、分类或趋势分析
- 用户主动要求"把这个整理成 wiki"

**操作**：
1. 告诉用户："这个回答有沉淀价值，是否保存为 wiki 页面？"
2. 如果用户同意：

```
save_wiki_page(
    slug="<concept-or-survey-slug>",
    title="<标题>",
    entity_type="concept" | "survey",
    content="<整理后的 Markdown>",
    tags="<相关标签>",
    related_pages="<引用的页面 slug>",
    status="complete",
    confidence="medium"
)
```

3. 记录日志：

```
append_log(operation="crystallize", details="Q&A crystallized: <title>", pages_affected="concepts/<slug>")
```

**页面类型选择**：
- 单个概念解释 → `concept`
- 多篇论文的主题综述 → `survey`
- 两种方法的对比 → `comparison`

## Wiki 全局概览

检索前可先查看 `wiki/graph/context_brief.md` 了解知识库整体情况：
- 已收录的论文数量和类型分布
- 研究主题分布
- 最近新增的页面

这有助于判断：
- wiki 是否覆盖了用户问题的领域
- 是否需要建议用户先导入相关论文

## 边界处理

### Wiki 无相关内容

如果 `query_wiki` 返回 0 结果或 score 都很低：
1. 告知用户："Wiki 中暂无相关内容。"
2. 建议：是否需要导入相关论文？或使用 web-search 搜索？
3. **不要凭空编造**，除非明确标注为 Agent 推测

### 部分相关

如果结果只有部分相关：
1. 基于已有内容回答能回答的部分
2. 对缺失部分说明："Wiki 中关于 X 的信息较少，以下基于已有论文推断..."
3. 标注 confidence: low

### 多个矛盾来源

如果不同 wiki 页面有矛盾信息：
1. 分别呈现不同观点
2. 标注各自的来源和 confidence
3. 如果有明确的实验对比，以实验结果为准

## 与其他 Skill 的关系

- **paper-wiki**：wiki-ask 依赖 paper-wiki 建立的知识库。如果 wiki 为空，建议用户先用 paper-wiki 导入论文。
- **web-search**：wiki 无结果时，可建议用户使用 web-search 搜索外部资源。
- **rag-skill**：wiki-ask 是 RAG 的一种特殊形式，但专注于 wiki 知识库而非用户文档。
