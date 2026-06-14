---
name: paper-wiki
description: 学术论文 Wiki 知识库 — 架构概览、页面类型、引用规范、故障排查。具体流程见 paper-ingest、paper-update、wiki-lint、wiki-ask 四个子 Skill。
---

# Academic Paper Wiki

遵循 Karpathy LLM Wiki 设计模式，构建可持续演化、可维护、可追溯的学术论文知识库。

## 子 Skills

| Skill | 功能 | 触发词 |
|-------|------|--------|
| `paper-ingest` | 导入新论文，自动创建实体页面 | "ingest"、"导入论文"、提供 PDF/arXiv 链接 |
| `paper-update` | 增量更新已有论文版本 | "update"、"更新论文" |
| `wiki-lint` | 健康检查 + 自动修复 + 实体回填 | "lint"、"检查 wiki" |
| `wiki-ask` | 知识查询 | "query wiki"、"wiki 里有没有关于 X" |

## 架构

```
raw/sources/          ← Layer 1: 原始资料（只读，注册后不可修改）
wiki/                 ← Layer 2: 知识库（Agent 维护的结构化 Markdown）
workspace/AGENTS.md   ← Layer 3: Schema 和工作流规范
```

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
| `lint_wiki` | 健康检查 |
| `query_wiki` | 搜索 wiki |
| `fetch_url` | 抓取 arXiv 页面 |
| `python_repl` | 下载 PDF 等辅助任务 |

## 页面类型速查

| 类型 | 目录 | 核心章节 |
|------|------|----------|
| paper | wiki/papers/ | Overview, Background, Core Problem, Method, Experiments, Contributions, Limitations |
| concept | wiki/concepts/ | Definition, Intuition, How It Works, Key Papers, Variants, Applications |
| method | wiki/methods/ | Mechanism, Procedure, Assumptions, Tradeoff Profile, Limitations |
| dataset | wiki/datasets/ | Overview, Size & Structure, Splits, Evaluation Metrics, Leaderboard |
| author | wiki/authors/ | Profile, Research Areas, Key Contributions, Recent Work |
| survey | wiki/surveys/ | Overview, Taxonomy, Timeline, Key Methods Comparison, Open Problems |
| comparison | wiki/comparisons/ | Overview, Dimension-by-Dimension Comparison, When to Use Which |

## 引用规范

### 作者原文结论
```markdown
Self-attention reduces sequential computation to O(1) steps.
> Source: [Attention Is All You Need](papers/attention-is-all-you-need.md), Section 3
```

### Agent 综合判断
```markdown
Transformer 的并行化优势使其在长序列任务上显著优于 RNN。
> Agent judgment (confidence: high) — based on papers/attention-is-all-you-need, methods/transformer
```

### 不确定内容
```markdown
该方法可能在低资源语言上效果有限。（confidence: low — 缺少直接实验证据）
```

## 故障排查

- **PDF 解析失败** → 确认文件路径和格式。扫描版 PDF 需要 OCR。
- **register_source 返回重复** → hash 相同则跳过，不同则确认是否更新。
- **lint 发现红色问题** → 优先修复 missing_frontmatter 和 index_mismatch。
- **query 结果为空** → wiki 中可能还没有相关内容，建议先 ingest 相关论文。
