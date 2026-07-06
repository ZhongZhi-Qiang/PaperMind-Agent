# PaperMind-Agent 延迟优化总结

> 基准 commit: `090c8047df1880dba77febc64d9a5f75d770b6ea`
>
> 优化日期: 2026-07-06 ~ 2026-07-07

---

## 改动总览

| 文件 | 状态 | 所属策略 |
|------|------|---------|
| `config/config.py` | 修改 | 全部 |
| `config/.env` | 修改 | 全部 |
| `graph/agent.py` | 修改 | 路由、异步 |
| `graph/agent_factory.py` | 修改 | 路由、并行 |
| `graph/guardian.py` | 修改 | 缓存、剪枝 |
| `graph/harness_review.py` | 修改 | 异步、剪枝 |
| `graph/llm.py` | 修改 | 路由 |
| `graph/parallel_tools.py` | **新增** | 并行 |
| `graph/route_classifier.py` | **新增** | 路由 |
| `storage/redis_client.py` | 修改 | 缓存 |
| `memory_module_v3/retrieval/service.py` | 修改 | 缓存 |
| `memory_module_v3/storage/schema.sql` | 修改 | 缓存 |
| `requirements.txt` | 修改 | 缓存 |
| `scripts/benchmark_latency.py` | **新增** | 测试 |
| `tests/test_route_classifier.py` | **新增** | 测试 |

**总计**: 12 个修改文件 + 3 个新增文件，+645 行，-61 行

---

## 一、缓存（Caching）

### 1.1 Guardian 关键词缓存

**匹配策略**: jieba TF-IDF 提取关键词（BM25 风格），md5 hash 作为 Redis key

```
用户输入 → jieba.analyse.extract_tags() → 排序拼接 → md5 → "pm:guardian:{hash}"
  → Redis GET（sync）→ 命中则跳过 LLM 分类
```

- 文件: `graph/guardian.py:342`, `storage/redis_client.py:200`
- TTL: 3600s（1 小时）
- 类型: Redis string（sync 客户端）

### 1.2 Embedding 去重缓存

**匹配策略**: 量化 embedding key（LSH），`round(emb[i], 3)` → md5，cosine ≥ 0.984 落在同一 bucket

```
embedding API → [0.123, -0.456, ...] → round(x, 3) → repr → md5 → "pm:emb:{hash}"
  → Redis GET → 命中则返回已缓存的标准 embedding（跳过 API 调用）
```

- 文件: `memory_module_v3/retrieval/service.py:66-95`
- TTL: 300s（5 分钟）
- 类型: Redis string（async 客户端）

### 1.3 Recall 结果缓存

**匹配策略**: 同量化 embedding key，同一 bucket 的查询共享检索结果

```
计算 embedding → quantize → "pm:recall:{hash}"
  → Redis GET → 命中则直接返回 L1 facts + L2 scenes + L3 persona
  → 跳过 pgvector 检索 + BM25 全文检索 + RRF 融合
```

- 文件: `memory_module_v3/retrieval/service.py:99-120, 149-164`
- TTL: 600s（10 分钟）
- 类型: Redis string (JSON, async 客户端）

### 1.4 L2/L3 Dirty-Flag 内存缓存

L2 scenes 和 L3 persona 从 Markdown 文件读取后缓存在进程内存中，仅当 PipelineManager 触发 `invalidate_cache()` 时才重新读取。

- 文件: `memory_module_v3/retrieval/service.py:44-53`
- TTL: 无固定值，dirty flag 驱动失效

### 1.5 三级缓存链路

```
用户请求
  │
  ├─ Layer 1: Guardian 关键词缓存 (Redis sync)
  ├─ Layer 2: Embedding 量化缓存 (Redis async)
  ├─ Layer 3: Recall 结果缓存 (Redis async)
  └─ Layer 4: L2/L3 内存缓存 (dirty flag)
```

每个层级 miss 后才降级到下一层（Redis 不可用时缓存自动降级为空，不影响业务）。

---

## 二、上下文压缩（Context Compression）

### 2.1 Auto-Capture 异步化（Fire-and-Forget）

memory v3 自动提取（L0 → L1）改为后台任务，不阻塞 `done` 事件返回给用户。

- 文件: `graph/agent.py:228-239`
- 机制: `_spawn_background_task()` 强引用追踪 + `done_callback` 自清理，防 GC
- 开关: `MEMORY_V3_ASYNC_CAPTURE=true`

### 2.2 HarnessReview 异步化

对话结束后的质量审查改为后台执行，不阻塞用户看到回复。

- 文件: `graph/harness_review.py:257-267`
- 开关: `HARNESS_REVIEW_SYNC=false`（默认异步）

### 2.3 Summarization 阈值调优

对话压缩触发消息数从默认 50 调整为 25，保留最近 10 条。

- 开关: `SUMMARIZATION_TRIGGER_MESSAGES=25`, `SUMMARIZATION_KEEP_MESSAGES=10`

---

## 三、智能路由（Smart Routing）

### 3.1 三级分类器

纯规则匹配（< 1ms），无 LLM 调用：

| 级别 | 示例 | 处理路径 |
|------|------|---------|
| L0 寒暄 | "你好", "谢谢", "hello" | fast LLM 直接回复，无 memory recall，无工具，无中间件 |
| L1 知识问答 | "什么是注意力机制？" | fast LLM + memory recall + 只读工具（ReadFile/FetchURL/QueryWiki/search_memory 等）+ Guardian only |
| L2 复杂任务 | "帮我复现这篇论文的代码" | 全链路：主 LLM + 所有工具 + 全部中间件 |

- 文件: `graph/route_classifier.py`（新增）
- 文件: `graph/agent.py:90-150`

### 3.2 Fast LLM 路由

Guardian、标题生成、memory 蒸馏、context offload 等轻量任务可使用独立的小模型（`FAST_LLM_*` 配置），未配置时自动降级为主 LLM。

- 文件: `graph/llm.py`（新增 `get_fast_llm()`）

---

## 四、并行工具调用（Parallel Tool Execution）

### 4.1 ParallelToolNode

当 LLM 一次返回 ≥2 个 `tool_calls` 时，使用 `asyncio.gather()` 并发执行：

```
LLM 返回: [read_file("A.py"), fetch_url("https://..."), search_memory("transformer")]
  → asyncio.gather(A, B, C)
  → 耗时 = max(t_A, t_B, t_C)，非 t_A + t_B + t_C
```

- 文件: `graph/parallel_tools.py`（新增）
- 文件: `graph/agent_factory.py:224-227`（注入到 graph）

### 4.2 安全兼容

并行执行 bypass 了默认 ToolNode 的 `wrap_tool_call` hook，所以在 ParallelToolNode 内部显式复用 HarnessSecurity 的安全检查函数（`check_protected_path` / `check_dangerous_command` / `check_custom_rules`）。

- 仅 1 个 tool_call 时委托默认串行 ToolNode（零开销）
- 遇到未知工具时自动降级为默认路径

---

## 五、链路剪枝（Chain Pruning）

### 5.1 Guardian 剪枝

同一 session 连续 N 次判定为 safe 后，跳过后续 Guardian 检查。

- 文件: `graph/guardian.py:120-142`
- 机制: agent state 中维护 `consecutive_safe_turns` 计数器，每次 safe +1，触发 block 重置为 0
- 阈值: `GUARDIAN_PRUNING_SAFE_THRESHOLD=3`

### 5.2 Guardian 黑名单规则短路

已知注入攻击模式（"ignore previous", "developer mode", "jailbreak" 等）通过子串匹配直接拦截，**< 1ms**，完全不调 LLM。

- 文件: `graph/guardian.py:22-31, 41-47`

### 5.3 HarnessReview 剪枝

响应 < 50 字符且无工具调用时跳过 Review。

- 文件: `graph/harness_review.py:222-229`
- 阈值: `HARNESS_REVIEW_MIN_RESPONSE_CHARS=50`

### 5.4 HarnessSecurity

无需改动——该中间件的 `wrap_tool_call` hook 仅在 LLM 发起工具调用时才触发，无工具调用时开销为零。

---

## 新增配置项汇总

```env
# ===== Redis 缓存后端 =====
REDIS_URL=redis://localhost:6379/0
REDIS_GUARDIAN_CACHE_TTL=3600
REDIS_EMBED_CACHE_TTL=300
REDIS_RECALL_CACHE_TTL=600

# ===== Day 1 优化开关 =====
GUARDIAN_RULE_SHORTCIRCUIT_ENABLED=true
GUARDIAN_CACHE_ENABLED=true
MEMORY_V3_ASYNC_CAPTURE=true
HARNESS_REVIEW_SYNC=false

# ===== Fast LLM 路由 =====
FAST_LLM_PROVIDER=
FAST_LLM_MODEL=
FAST_LLM_API_KEY=
FAST_LLM_BASE_URL=

# ===== Day 5: 智能路由 + 并行工具 + 链路剪枝 =====
SMART_ROUTING_ENABLED=true
PARALLEL_TOOL_CALLS_ENABLED=true
GUARDIAN_PRUNING_ENABLED=true
GUARDIAN_PRUNING_SAFE_THRESHOLD=3
HARNESS_PRUNING_ENABLED=true
HARNESS_REVIEW_MIN_RESPONSE_CHARS=50

# ===== 上下文压缩 =====
SUMMARIZATION_TRIGGER_MESSAGES=25
SUMMARIZATION_KEEP_MESSAGES=10
```

---

## 延迟优化效果估算

| 场景 | 优化前 | 优化后 | 节省 |
|------|--------|--------|------|
| 首次请求 (冷启动) | Guardian LLM + embedding API + pgvector | 相同（必须走全链路） | — |
| 相同话题第二次请求 | Guardian LLM + embedding API + pgvector + BM25 + RRF | Guardian cache + Recall cache 命中 → **< 10ms** 额外延迟 | ~500-800ms |
| 黑名单攻击 | Guardian LLM → block (~800ms) | 子串匹配 → block (**< 1ms**) | ~800ms |
| L0 寒暄 ("你好") | 全链路 + memory recall + 所有工具 | fast LLM 直接回复（无 recall/无工具/无中间件） | ~300-500ms |
| L1 知识问答 | 全链路 + 所有工具 | fast LLM + 只读工具 + Guardian only | ~200-400ms |
| 多工具调用 (3 tools) | t_A + t_B + t_C | max(t_A, t_B, t_C) | ~2× 工具耗时 |
| 对话结束 | HarnessReview 阻塞 → 返回 | 立即返回（Review 后台跑） | ~500ms |
| Guardian 连续 safe | 每次调 Guardian LLM | 连续 3 次 safe 后跳过 | ~200ms × N |
