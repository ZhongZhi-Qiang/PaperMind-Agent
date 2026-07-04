---
name: harness-mode
description: Switch Harness workflow mode (full/hotfix/tweak) and development phase (design/build/fix). Use when the user wants to change the current working mode or phase.
---

# Harness Mode

管理 `.claude/.harness-state`，控制 Harness 的行为策略。

## 支持的命令

### 切换模式

```
/harness-mode full     → 完整检查，所有规则生效
/harness-mode hotfix   → 紧急修复，跳过行数/文件数检查
/harness-mode tweak    → 微调模式，仅 .env 保护
```

### 切换阶段

```
/harness-phase design  → 设计阶段，宽松审查
/harness-phase build   → 构建阶段，正常审查
/harness-phase fix     → 修复阶段，收紧变更范围检查
```

### 查看当前状态

```
/harness-status        → 显示当前阶段和模式
```

## 执行逻辑

当用户输入上述命令时：

1. 读取 `.claude/.harness-state`（不存在则创建默认值 `{"phase":"build","mode":"full"}`）
2. 修改对应的 `mode` 或 `phase` 字段
3. 更新 `since` 为当前时间
4. 写入文件
5. 回显新状态

**模式联动**：
- 切换到 `hotfix` 模式时，自动将 phase 设为 `fix`
- 切换到 `tweak` 模式时，自动将 phase 设为 `design`

## 示例

用户：`/harness-mode hotfix`
→ 写入 `{"phase":"fix","mode":"hotfix","since":"2026-06-01T12:00:00Z"}`
→ 回复：「已切换到热修复模式：阶段=fix, 模式=hotfix。将跳过行数/文件数检查。」

用户：`/harness-status`
→ 回复：「当前 Harness 状态：阶段=build, 模式=full (since 2026-06-01T10:00:00Z)」
