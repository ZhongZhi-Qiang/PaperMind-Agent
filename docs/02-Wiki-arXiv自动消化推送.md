# arXiv 论文自动积累与推送系统

本文档详细说明 miniOpenClaw 中定时抓取 arXiv 新论文、自动构建 Wiki 知识库并通过企业微信推送日报的完整实现。

## 架构总览

```
APScheduler (每天 ARXIV_DIGEST_HOUR:00 Asia/Shanghai)
  → fetch_arxiv_papers()           # 5 个 RSS feed 抓取 + 双重过滤
  → load_registered_arxiv_ids()    # 读取 manifest.jsonl 去重
  → filter_new_papers()            # 仅保留未收录论文
  → run_digest()                   # 逐篇处理:
      → _generate_chinese_summary()    # LLM: 摘要翻译为学术中文
      → _download_pdf()                # httpx 下载 PDF
      → _parse_pdf()                   # 提取全文
      → _analyze_with_llm()            # LLM: 7 段式结构化分析
      → _extract_entities_with_llm()   # LLM: 提取概念/方法/数据集
      → _create_wiki_pages()           # 写入 wiki/ 目录
      → _classify_paper_tags()         # LLM: 分类到 16 类标签
      → _check_and_create_surveys()    # 标签论文 ≥3 篇时自动生成 survey
      → RebuildIndexTool + AppendLogTool
  → send_daily_digest()            # 企业微信 Webhook 推送
  → run_post_digest_lint()         # LintWikiTool 结构修复 + 缺失实体补全
```

## 1. 定时调度

**文件**: `backend/service/scheduler.py`

使用 APScheduler 的 `AsyncIOScheduler`（异步定时任务调度器，基于 asyncio 事件循环，无需额外线程），时区设置为 `Asia/Shanghai`。

> **调度器**：定时任务的管理器，告诉它"每天 8 点做某件事"，它负责在正确的时间点触发执行。类似 Linux 的 cron，但跑在 Python 进程里，能直接调用 async 函数。

### 调度配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ARXIV_DIGEST_HOUR` | `8` | 每天执行的小时（0-23） |
| `ARXIV_DIGEST_ENABLED` | `true` | 设为 `false`/`0`/`no`/`off` 关闭 |

### 生命周期

- **启动**: `app.py` lifespan 中调用 `start_scheduler()`，创建 cron job（`CronTrigger(hour=hour, minute=0)`），`misfire_grace_time=3600s`

> **Cron job**：按时间规则重复执行的任务。`cron` 是 Linux 的定时任务系统，用类似 `0 8 * * *` 的表达式表示"每天 8:00 执行"。本项目用 `CronTrigger(hour=8, minute=0)` 做同样的事，语法更 Python 化。

> **misfire_grace_time**：错过执行的容忍时间（秒）。调度器因故（进程挂起、系统休眠等）没能准时触发任务时，在 grace_time 窗口内恢复则补执行，超过则跳过本次等下一个周期。设 3600 表示允许迟到最多 1 小时。
- **关闭**: `shutdown_scheduler()` 优雅停止调度器

## 2. 论文抓取

**文件**: `backend/service/arxiv_service.py`

### 数据源

通过 arXiv **RSS feed**（Really Simple Syndication，简易信息聚合，非 arXiv API）获取论文。每个分类有一个 RSS 地址，新论文提交时自动推送记录。订阅 5 个分类：

| Feed | 领域 |
|------|------|
| `rss.arxiv.org/rss/cs.AI` | 人工智能 |
| `rss.arxiv.org/rss/cs.CL` | 计算语言学 |
| `rss.arxiv.org/rss/cs.MA` | 多智能体系统 |
| `rss.arxiv.org/rss/cs.IR` | 信息检索 |
| `rss.arxiv.org/rss/cs.RO` | 机器人学 |

### 抓取流程 (`fetch_arxiv_papers()`)

1. 用 `httpx.Client` 并行请求各 RSS feed（支持 `HTTP_PROXY`/`HTTPS_PROXY` 代理）
2. 解析每个 `<item>` 为 `ArxivPaper` dataclass，提取字段：
   - `arxiv_id`（从 link/guid 正则提取）
   - `title`、`authors`（`dc:creator`）、`abstract`（`<description>` 中 "Abstract:" 之后的文本）
   - `pdf_url`、`categories`、`published_date`
3. 跳过交叉列表论文（`announce_type` 含 "cross"）
4. 按 `arxiv_id` 去重，合并同一论文出现在多个 feed 中的 categories

### 双重过滤

#### 第一层：关键词粗筛 (`_broad_keyword_filter()`)

对标题+摘要进行文本匹配，命中以下 14 个关键词之一即通过：

```
agent, llm, language model, memory, tool, planning, reasoning,
multi-agent, embodied, benchmark, safety, knowledge, retrieval, world model
```

成本极低，快速排除无关论文。

#### 第二层：语义精排 (`_rank_by_similarity()`)

1. 用项目配置的 embedding 模型计算论文（标题 + 摘要前 500 字符）与 5 个兴趣主题的余弦相似度
2. 取每个论文在所有主题中的最高相似度
3. 过滤掉低于 `min_similarity`（默认 0.3）的论文
4. 按相似度降序排列，截取前 `max_results`（默认 20）篇

**5 个兴趣主题**（硬编码于 `arxiv_service.py`）：

1. Agent 记忆与知识增强，LLM Agent 长期记忆
2. 多智能体协作与协调
3. Agent 工具使用与具身智能
4. Agent 感知与推理架构
5. Agent 评测基准与安全

## 3. 去重机制

**文件**: `backend/raw/sources/manifest.jsonl`

- 每行是一条 JSON 记录，包含 `arxiv_id` 字段
- `load_registered_arxiv_ids()` 读取所有已收录 ID
- `filter_new_papers()` 过滤掉已存在的论文
- 保证流水线幂等：重复运行不会重复处理

## 4. 论文处理流水线

**文件**: `backend/service/digest_pipeline.py`

对每篇新论文依次执行以下步骤：

### 4.1 中文摘要生成 (`_generate_chinese_summary()`)

- **Prompt**: `CHINESE_SUMMARY_PROMPT`
- **输入**: 论文标题 + 英文摘要
- **输出**: 精炼的学术中文摘要（保留英文技术术语）
- **用途**: 企业微信推送内容

### 4.2 PDF 下载与解析

- **下载**: `httpx` 从 `arxiv.org/pdf/` 下载到临时目录
- **解析**: `tools/pdf_parser_tool.py` 提取全文文本

### 4.3 结构化分析 (`_analyze_with_llm()`)

- **Prompt**: `ANALYSIS_PROMPT`
- **输入**: 标题、作者、摘要、全文摘录（最多 30,000 字符）
- **输出**: 7 段式结构化分析，每段 2-5 句：

| 段落 | 内容 |
|------|------|
| Core Problem | 研究问题 |
| Key Contribution | 核心贡献 |
| Method | 方法描述 |
| Key Concepts | 关键概念 |
| Experiments | 实验结果 |
| Limitations | 局限性 |
| Related Work | 相关工作 |

### 4.4 实体提取 (`_extract_entities_with_llm()`)

- **Prompt**: `ENTITY_EXTRACTION_PROMPT`
- **输入**: 标题 + 分析结果
- **输出**: JSON 结构

```json
{
  "concepts": [{"name": "", "slug": "", "definition": "", "tags": []}],
  "methods": [{"name": "", "slug": "", "mechanism": "", "tags": []}],
  "datasets": [{"name": "", "slug": "", "description": "", "tags": []}]
}
```

- 提取 2-5 个概念、1-3 个方法、0-2 个数据集

### 4.5 Wiki 页面创建 (`_create_wiki_pages()`)

在 `backend/wiki/` 下创建 markdown 文件：

| 目录 | 内容 | Frontmatter 字段 |
|------|------|------------------|
| `wiki/papers/{slug}.md` | 论文主页面 | slug, title, type="paper", authors, year, arxiv_id, tags, related_pages, source_hash, status, confidence |
| `wiki/concepts/{slug}.md` | 概念实体页 | — |
| `wiki/methods/{slug}.md` | 方法实体页 | — |
| `wiki/datasets/{slug}.md` | 数据集实体页 | — |
| `wiki/authors/{slug}.md` | 作者页（每篇最多 5 位） | — |

### 4.6 标签分类 (`_classify_paper_tags()`)

- **Prompt**: `TAG_CLASSIFY_PROMPT`
- **输入**: 标题、摘要、分析摘录、标签分类列表
- **输出**: 1-3 个标签 ID（JSON 数组）
- **分类体系**: `backend/wiki/tags.yaml`，共 16 类：

| 标签 ID | 领域 |
|---------|------|
| agent-architecture | Agent 架构 |
| memory-systems | 记忆系统 |
| retrieval-augmented | 检索增强 |
| multi-agent | 多智能体 |
| self-evolution | 自我进化 |
| reasoning-planning | 推理规划 |
| evaluation-benchmark | 评测基准 |
| safety-alignment | 安全对齐 |
| training-optimization | 训练优化 |
| knowledge-management | 知识管理 |
| nlp-tasks | NLP 任务 |
| efficiency-scalability | 效率与扩展 |
| web-interaction | Web 交互 |
| code-generation | 代码生成 |
| robotics-embodied | 机器人/具身 |
| finance-domain | 金融领域 |

### 4.7 Survey 自动生成 (`_check_and_create_surveys()`)

- 当某个标签下的论文数量 ≥ 3 篇时，自动创建 `wiki/surveys/{tag}.md`
- 汇总该方向的所有论文

### 4.8 索引重建与日志

- `RebuildIndexTool`: 重建 wiki 索引
- `AppendLogTool`: 记录到 `wiki/log.md`

## 5. 企业微信推送

**文件**: `backend/service/wechat_notifier.py`

### 配置

| 环境变量 | 说明 |
|---------|------|
| `WECHAT_WEBHOOK_KEY` | 企业微信群机器人 Webhook key（必填） |

Webhook URL: `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}`

### 消息格式

使用 markdown 格式推送，每篇论文一条消息：

```markdown
## Agent+Memory 日报 (2026-06-12)
> 共发现 **15** 篇新论文，已收录 **12** 篇到 Wiki

**Paper Title**
- 作者: Author1, Author2, Author3 et al.
- 分类: cs.AI, cs.CL
- 中文摘要: 这是一篇关于...
- [arXiv页面](https://arxiv.org/abs/xxxx.xxxxx) | [PDF下载](https://arxiv.org/pdf/xxxx.xxxxx)
```

### 推送逻辑

- `build_messages()`: 构建消息列表（1 条 header + N 条论文）
- `send_daily_digest()`: 逐条发送
- `send_error_notification()`: 流水线异常时推送错误通知（截取前 300 字符）

## 6. API 端点

**文件**: `backend/api/digest.py`

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/digest/status` | GET | 查看调度器状态、已注册 job 列表、下次执行时间 |
| `/api/digest/test` | GET | 试运行：抓取 + 过滤，返回新论文数量和详情，不执行处理 |
| `/api/digest/run` | POST | 手动触发完整流水线（异步后台任务） |

## 7. 后处理

流水线主流程完成后，执行 `run_post_digest_lint()`：

- 调用 `LintWikiTool(auto_fix=True, backfill=True)`
- 自动修复 wiki 结构问题
- 补全缺失的实体页面（如论文引用了尚未创建的概念页）

## 8. 完整配置清单

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ARXIV_DIGEST_HOUR` | `8` | 每天执行时间（小时，Asia/Shanghai） |
| `ARXIV_DIGEST_ENABLED` | `true` | 是否启用定时任务 |
| `WECHAT_WEBHOOK_KEY` | — | 企业微信 Webhook key |
| `HTTP_PROXY` | — | HTTP 代理（RSS 抓取 + PDF 下载） |
| `HTTPS_PROXY` | — | HTTPS 代理 |
| `LLM_PROVIDER` | — | 主 LLM 提供商（用于分析/提取/分类/翻译） |
| `EMBEDDING_PROVIDER` | — | Embedding 提供商（用于语义排序） |

## 9. 关键设计决策

| 决策 | 原因 |
|------|------|
| RSS feed 而非 arXiv API | RSS 按日期组织，适合每日增量抓取；API 适合搜索但不适合定时轮询 |
| 两阶段过滤（关键词 + 语义） | 关键词粗筛成本极低，快速排除大量无关论文；语义精排保证相关性 |
| manifest.jsonl 去重 | 简单高效，无需数据库；JSONL 格式便于追加和审计 |
| 中文摘要单独生成 | 推送面向中文用户，但 wiki 内容保持英文以保证学术准确性 |
| Survey 自动生成 | 随着论文积累，自动聚类形成综述页，减少人工整理工作 |
| 后处理 lint | 流水线可能因 LLM 输出不稳定导致结构问题，lint 兜底修复 |
