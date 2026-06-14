---
name: ideate
description: Wiki 增强研究构思 — 基于 wiki 知识库的研究想法生成。扫描论文局限性和开放问题，通过结构化路径生成研究想法，过滤后保存为 wiki idea 页面。支持反重复机制（banlist）。
---

# Wiki-Enhanced Research Ideation

基于 wiki 知识库中的论文、方法和概念，生成可执行的研究想法。

## 触发条件

当用户提到以下意图时使用本 Skill：
- "ideate" / "想研究方向" / "有什么可以做的" / "generate research ideas"
- "brainstorm" / "头脑风暴" / "找研究空白"
- "what should I work on" / "有什么 open problem"

**不适用场景**：
- 用户已经有明确研究方向，需要文献调研 → 使用 wiki-ask
- 用户需要导入新论文 → 使用 paper-wiki
- 纯闲聊 → 不使用本 Skill

## 可用工具

| 工具 | 用途 |
|------|------|
| `read_file` | 读取 context_brief.md 和 open_questions.md |
| `read_wiki_page` | 读取论文/方法详情（Limitations、Open Questions） |
| `list_wiki_pages` | 列出现有 ideas（banlist 构建） |
| `query_wiki` | 搜索相关 wiki 页面补充上下文 |
| `save_wiki_page` | 保存生成的 idea 页面 |
| `append_log` | 记录 ideation 操作日志 |

## 核心流程

### Phase 1: Gather Context（收集上下文）

**目标**：建立 ideation 的知识基础，识别已知的研究空白。

#### Step 1.1: 读取全局概览

```
read_file(file_path="wiki/graph/context_brief.md")
```

了解知识库的覆盖范围、研究主题分布、已有论文数量。判断 wiki 是否有足够的知识基础进行有意义的 ideation。

**如果 wiki 总页面 < 10**：警告用户知识库较小，建议先导入更多论文再 ideate。

#### Step 1.2: 读取研究空白地图

```
read_file(file_path="wiki/graph/open_questions.md")
```

获取所有论文的 Open Questions 和所有论文/方法的 Limitations 的聚合列表。这是 ideation 的主要输入。

**如果 open_questions.md 不存在**：执行 `rebuild_index()` 生成它，然后再读取。

#### Step 1.3: 构建 Banlist（反重复列表）

```
list_wiki_pages(entity_type="idea")
```

获取所有已有的 idea 页面。构建 banlist：
- **rejected ideas**：绝对禁止重复。读取每个 rejected idea 的内容，理解为什么被拒绝。
- **proposed/accepted ideas**：避免过于相似，但可以在此基础上改进。
- **abandoned ideas**：可以重新考虑，但需要说明为什么之前的放弃理由不再成立。

#### Step 1.4: 深度阅读 Top 源

从 open_questions.md 中识别出现频率最高的源论文（通常是 gap 最多的论文），读取 2-3 篇最丰富的论文完整页面：

```
read_wiki_page(slug="<top-paper-slug>")
```

重点关注：Limitations、Open Questions、Method 章节。

### Phase 2: Generate Ideas（生成想法）

**目标**：通过 4 条结构化路径生成研究想法。

基于 Phase 1 收集的上下文，按以下 4 条路径各生成 1-2 个想法（总计 4-8 个候选）：

#### Path A: Gap-Driven（从开放问题出发）

从 open_questions.md 中选择一个未被现有 idea 覆盖的 open question，提出直接解决该问题的研究方案。

**思路**：
> 论文 [X] 提出了开放问题："Y"。请提出一个具体的研究方案来回答这个问题。
> 要求：(1) 明确的问题定义 (2) 具体的方法假设 (3) 可行的验证方案 (4) 预期结果。

#### Path B: Incremental（改进现有方法的局限）

从 open_questions.md 中选择一个 method limitation，提出改进方案。

**思路**：
> 方法 [X] 的局限是："Y"。请提出一个改进方案。
> 要求：(1) 解释为什么现有方法有这个局限 (2) 提出具体的改进机制 (3) 说明改进的预期效果 (4) 分析可能的 tradeoff。

#### Path C: Combination（组合两种方法）

从 wiki 中选择两种互补的方法或概念，探索组合的可能性。

**思路**：
> 方法 [A] 擅长 X，方法 [B] 擅长 Y。请探索将两者结合的可能性。
> 要求：(1) 识别两者的互补点 (2) 提出具体的组合方案 (3) 分析组合的理论优势 (4) 设计验证实验。

#### Path D: Cross-Pollination（跨领域迁移）

从 wiki 中选择一个方法，探索将其核心机制迁移到不同应用场景。

**思路**：
> 方法 [X] 在 [领域 A] 中有效，因为其核心机制是 [M]。
> 请探索将 [M] 迁移到 [领域 B] 的可能性。
> 要求：(1) 识别可迁移的核心机制 (2) 分析目标领域的差异 (3) 提出适配方案 (4) 设计验证实验。

### Phase 3: Filter & Save（过滤与保存）

**目标**：从候选想法中筛选最佳的 2-3 个，保存为 wiki idea 页面。

#### Step 3.1: 评分与排名

对每个候选想法，按 3 个维度打分（1-5 分）：

| 维度 | 评判标准 |
|------|----------|
| **Novelty（新颖性）** | 是否与 banlist 中已有想法不同？是否有独特的角度？ |
| **Feasibility（可行性）** | 是否在硕士/博士论文范围内可实现？需要的资源是否现实？ |
| **Relevance（相关性）** | 是否与 wiki 中已有的研究主题相关？是否能利用已有知识？ |

综合分数 = Novelty × 0.4 + Feasibility × 0.35 + Relevance × 0.25

选择综合分数最高的 2-3 个想法进入保存阶段。

#### Step 3.2: 保存入选想法

对每个入选的想法，使用 `save_wiki_page` 保存：

```
save_wiki_page(
    slug="<idea-slug>",
    title="<想法标题>",
    entity_type="idea",
    content="<按模板生成的完整内容>",
    tags="<相关标签>",
    related_pages="<相关的论文/方法 slug>",
    origin_paper="<来源论文 slug>",
    addresses_gap="<解决的研究空白>",
    priority="high" | "medium" | "low",
    generation_path="gap_driven" | "incremental" | "combination" | "cross_pollination",
    status="proposed",
    confidence="medium"
)
```

**Idea 页面内容模板**：

```markdown
## Idea Summary

<!-- 1-3 句话：这个想法是什么，解决什么问题 -->

## Research Gap

<!-- 引用 open_questions.md 中的具体条目 -->

## Proposed Approach

<!-- 具体、可执行的方法描述 -->

### Key Steps

- Step 1
- Step 2
- Step 3

## Feasibility Assessment

### Resources Required

- **Data**: <!-- 需要什么数据集 -->
- **Compute**: <!-- 预估计算需求 -->
- **Time**: <!-- 预估时间 -->

### Risks

- Risk 1
- Risk 2

## Novelty Check

<!-- 与已有想法的区别，为什么这个是新的 -->

## Expected Impact

<!-- 成功的话会怎样？用什么指标衡量？ -->

## Related Work

<!-- 链接相关 wiki 页面 -->
- [[paper-slug]] — 关联说明
- [[method-slug]] — 构建基础

## Status Log

| Date | Status | Notes |
|------|--------|-------|
| <today> | proposed | Initial ideation |
```

#### Step 3.3: 保存被拒想法（反重复）

对于未入选的候选想法，也保存为 wiki 页面，但标记为 rejected：

```
save_wiki_page(
    slug="rejected-<idea-slug>",
    title="[Rejected] <想法标题>",
    entity_type="idea",
    content="## Rejected Reason\n\n<具体拒绝原因>\n\n## Original Idea\n\n<原始想法描述>",
    status="rejected",
    confidence="low"
)
```

这确保未来 ideation 时这些想法会被加入 banlist，避免重复生成。

#### Step 3.4: 记录日志

```
append_log(
    operation="ideate",
    details="Generated <N> ideas via 4 paths. Saved <M> as proposed, <K> as rejected. Top idea: <title>",
    pages_affected="ideas/<slug1>,ideas/<slug2>,..."
)
```

## Idea Status Lifecycle

```
proposed → accepted → (becomes a research project)
proposed → rejected → (stays in banlist)
proposed → abandoned → (can be reconsidered later)
abandoned → proposed → (reconsidered with new context)
```

**状态更新**：用户可以通过手动编辑 idea 页面的 status 字段来推进生命周期。当用户说 "accept this idea" 或 "这个想法不错" 时，将 status 改为 `accepted`。当用户说 "reject" 或 "不行" 时，改为 `rejected` 并添加 failure_reason。

## 边界处理

### Wiki 知识不足

如果 wiki 中论文少于 5 篇或 open_questions.md 中的 gaps 少于 3 条：
1. 告知用户知识库较小，ideation 质量可能有限
2. 基于已有内容仍然生成想法，但标注 confidence: low
3. 建议用户先导入更多相关论文

### Banlist 饱和

如果已有 rejected ideas 超过 20 条：
1. 只读取最近 10 条 rejected ideas 构建 banlist
2. 告知用户 banlist 较大，可能需要清理过时的 rejected ideas

### 跨领域迁移无相关知识

如果 Path D 找不到适合迁移的方法或目标领域：
1. 跳过该路径，不强制生成
2. 在报告中说明："知识库中缺少跨领域迁移的素材"

## 与其他 Skill 的关系

- **paper-wiki**：ideate 依赖 paper-wiki 建立的知识库。wiki 越丰富，ideation 质量越高。
- **wiki-ask**：ideate 生成的想法可以作为 wiki-ask 的查询起点，进一步探索相关文献。
- **web-search**：如果用户想验证某个想法的新颖性，可建议使用 web-search 搜索外部文献。
