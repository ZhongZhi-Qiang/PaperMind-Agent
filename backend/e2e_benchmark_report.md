# PaperMind-Agent 端到端延迟对比报告

**日期**: 2026-07-08
**LLM**: deepseek-ai/DeepSeek-V4-Flash @ SiliconFlow
**模式**: Baseline (所有优化 OFF) → Optimized (所有优化 ON)，每轮前清空 Redis

---

## 总览

| 指标 | Baseline | Optimized | 提升 |
|------|----------|-----------|------|
| TTFT 中位数 | 2,240ms | 1,715ms | **-23%** |
| Total 中位数 | 119,997ms | 5,757ms | **-95%** |
| 改善 / 退化 | - | - | **7/9 改善** |

---

## 按策略分项

### 1. 智能路由 (L0 寒暄)

| Query | Baseline TTFT | Optimized TTFT | Baseline Total | Optimized Total |
|-------|-------------|---------------|---------------|-----------------|
| 你好 | 4,388ms | 933ms | 18,018ms | 1,350ms |
| hello | 2,117ms | 1,377ms | 13,055ms | 1,789ms |
| **中位数** | **3,252ms** | **1,155ms (-64%)** | **15,536ms** | **1,570ms (-90%)** |

### 2. 智能路由 + 剪枝 (L1 知识问答)

| Query | Baseline TTFT | Optimized TTFT | Baseline Total | Optimized Total |
|-------|-------------|---------------|---------------|-----------------|
| Transformer注意力机制 | 2,678ms | 4,162ms | 120,939ms | 6,060ms |
| BERT vs GPT 核心差异 | 2,041ms | 1,639ms | 41,636ms | 6,819ms |
| **中位数** | **2,359ms** | **2,901ms (+23%)** | **81,288ms** | **6,439ms (-92%)** |

### 3. 并行工具 + 全中间件 (L2 复杂任务)

| Query | Baseline TTFT | Optimized TTFT | Baseline Total | Optimized Total |
|-------|-------------|---------------|---------------|-----------------|
| 论文wiki整理 (05378/05382) | 2,108ms | 2,008ms | 120,330ms | 9,561ms |
| 论文PDF解析 | 2,863ms | 2,002ms | 121,000ms | 5,757ms |
| **中位数** | **2,486ms** | **2,005ms (-19%)** | **120,665ms** | **7,659ms (-94%)** |

### 4. Guardian 黑名单短路 + 缓存

| Query | Baseline TTFT | Optimized TTFT | Baseline Total | Optimized Total |
|-------|-------------|---------------|---------------|-----------------|
| 注入攻击 | 3,332ms | 609ms | 8,220ms | 639ms |
| **节省** | **-2,723ms (-82%)** | | **-7,581ms (-92%)** | |

### 5. 语义缓存（重复查询）

| Query | Baseline TTFT | Optimized TTFT | Baseline Total | Optimized Total |
|-------|-------------|---------------|---------------|-----------------|
| 第1次 (miss) | 2,083ms | 2,896ms | 120,740ms | 27,531ms |
| 第2次 (hit) | 2,240ms | 1,715ms | 119,997ms | 3,113ms |
| **第2次加速** | | **-23% TTFT** | | **-97% Total** |

---

## 结论

1. **智能路由 (L0)**: TTFT 降 64%，Total 降 90% — 直接跳过 agent graph 和全部中间件
2. **L1 轻量 agent**: Total 降 92%，TTFT 接近（LLM 波动范围内）
3. **链路剪枝 + 并行工具 (L2)**: Total 降 94%，TTFT 降 19%
4. **Guardian 黑名单短路**: TTFT 降 82% — 纯字符串匹配，跳过 LLM 调用
5. **语义缓存**: 重复查询 Total 降 97%（27s → 3s）

**综合**: 5 项优化叠加后，Total 延迟中位数降低 **95%**（120s → 5.8s）
