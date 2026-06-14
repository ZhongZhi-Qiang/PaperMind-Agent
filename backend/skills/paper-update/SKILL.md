---
name: paper-update
description: 论文增量更新 — 更新已导入的论文版本，同步更新关联的概念页、方法页和作者页。
---

# Paper Update

更新已导入 Wiki 的论文版本（如 v1 → v2），保留旧结论并标注新内容。

## 触发条件

- "update" / "更新论文" / "更新 wiki"
- 提供了同一论文的新版本 PDF
- "这篇论文有新版本了"

## 可用工具

| 工具 | 用途 |
|------|------|
| `register_source` | 注册原始资料，计算 SHA-256 哈希 |
| `pdf_parser` | 解析 PDF 提取文本和元信息 |
| `read_wiki_page` | 读取 wiki 页面 |
| `save_wiki_page` | 保存 wiki 页面 |
| `list_wiki_pages` | 列出 wiki 页面 |
| `rebuild_index` | 重建 wiki/index.md |
| `append_log` | 追加操作日志 |

## 更新流程

### 步骤 1：检查变化

```
register_source(file_path="papers/attention_v2.pdf", ...)
```

如果返回 `already_registered`，比较 hash：
- **hash 相同** → 提示"论文未变化"，结束
- **hash 不同** → 继续更新流程

### 步骤 2：解析新版本

```
pdf_parser(file_path="papers/attention_v2.pdf", max_pages=30)
```

### 步骤 3：对比差异

1. 用 `read_wiki_page` 读取现有论文页面
2. 对比新旧内容：
   - 新增章节或实验
   - 修正的结论
   - 更新的数据
   - 新增的作者

### 步骤 4：更新论文页面

用 `save_wiki_page` 更新论文页面：
- 保留旧结论，标注为 `[Updated in v2]`
- 新增内容正常写入
- 更新 `source_hash` 为新 hash
- 更新 `updated` 时间戳
- 更新 `version` 字段（如有）

### 步骤 5：更新关联页面

检查并更新受影响的页面：
- **概念页** — 如果概念定义有变化，在 Key Papers 中标注新版本
- **方法页** — 如果方法描述有变化，更新并标注
- **数据集页** — 如果有新的实验数据集
- **作者页** — 如果有新增作者

### 步骤 6：重建索引和日志

```
rebuild_index()
append_log(operation="update", details="Updated: Attention Is All You Need (v1→v2). Changes: added Section 5 experiments.", pages_affected="papers/attention-is-all-you-need")
```

## 注意事项

- 更新时保留历史信息，不要删除旧内容
- 用 `[Updated in vX]` 标注变更点
- 如果变更较大（如方法重写），考虑在 Overview 中说明版本差异
