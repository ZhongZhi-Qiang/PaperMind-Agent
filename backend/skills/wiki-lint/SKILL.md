---
name: wiki-lint
description: Wiki 健康检查 — 检测结构问题（缺失 frontmatter、悬空引用、孤立页面等），支持自动修复和实体页面回填。
---

# Wiki Lint

对 Wiki 知识库进行健康检查，发现并修复结构问题。

## 触发条件

- "lint wiki" / "检查 wiki 健康状态" / "wiki 有没有问题"
- "修复 wiki" / "auto fix"
- "回填实体页面" / "backfill"

## 可用工具

| 工具 | 用途 |
|------|------|
| `lint_wiki` | 健康检查，返回红/黄/蓝三级问题报告 |
| `read_wiki_page` | 读取 wiki 页面 |
| `list_wiki_pages` | 列出 wiki 页面 |
| `save_wiki_page` | 保存 wiki 页面 |
| `rebuild_index` | 重建 wiki/index.md |
| `append_log` | 追加操作日志 |

## 检查项

| 检查 | 严重度 | 说明 |
|------|--------|------|
| missing_frontmatter | red | 页面缺少 YAML frontmatter |
| missing_field | red | 缺少必填字段 |
| index_mismatch | red | index.md 与实际文件不一致 |
| dangling_reference | yellow | related_pages 指向不存在的页面 |
| invalid_status | yellow | status 值不在允许列表 |
| orphan_page | blue | 页面未被任何其他页面引用 |

## 基本用法

```
lint_wiki(auto_fix=false, backfill=false)
```

## 自动修复

设置 `auto_fix=true` 自动修复以下安全问题：

| 问题类型 | 修复方式 |
|----------|----------|
| missing_frontmatter | 补全默认 frontmatter（slug/title/type/created/updated/status=stub/confidence=low） |
| missing_field | 补全缺失的必填字段默认值 |
| invalid_status | 重置为 `in_progress` |
| invalid_confidence | 重置为 `medium` |
| dangling_reference | 移除指向不存在页面的 related_pages 条目 |
| index_mismatch | 重建 index.md |

**不会自动修复：**
- orphan_page（孤立页面）— 仅报告，需人工决定是否建立关联

## 实体页面回填

设置 `backfill=true` 扫描已有论文页面，补建缺失的实体页面：

1. 扫描所有论文页面，找出缺少 concept/method/dataset 关联的论文
2. 用 LLM 提取关键概念、方法、数据集
3. 创建缺失的 concept/method/dataset 页面
4. 更新论文的 related_pages 关联
5. 对已有实体页面追加反向引用
6. 重建 index.md

```
lint_wiki(backfill=true)
```

**适用场景：** 定时 digest 批量导入论文后，补建缺失的实体页面。

## 日志记录

```
append_log(operation="lint", details="Found 3 issues (1 red, 2 yellow), auto-fixed: rebuilt index.md")
```
