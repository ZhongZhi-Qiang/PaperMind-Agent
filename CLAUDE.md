# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mini-OpenClaw is a local, file-first, auditable AI Agent workbench built on LangChain 1.x `create_agent`. It features structured long-term memory with hybrid retrieval, a prompt-injection defense layer (Guardian), a skill-based capability system, and Langfuse observability integration.

## Architecture

### Backend (FastAPI + LangChain Agent)

**Entry point:** `backend/app.py` — FastAPI app with lifespan that initializes checkpointer, agent manager, memory indexer, and scheduler.

**Request flow:** Frontend → `POST /api/chat` (SSE streaming) → `AgentManager.astream()` → LangChain agent graph (Guardian → Harness security → context offload → summarization → tool calls → Harness review) → streamed response.

**Key layers:**

- `graph/agent.py` — `AgentManager` singleton orchestrates agent creation, streaming, title generation, and history summarization. It selects memory backend (v1/v2/v3) and builds injection context before each turn.
- `graph/agent_factory.py` — `build_agent_config()` + `create_agent_from_config()`. Assembles the agent with middleware chain: Guardian → HarnessSecurity → ContextOffload → Summarization → HarnessReview (order matters).
- `graph/guardian.py` — `GuardianMiddleware` implements `before_agent` hook. Calls a lightweight LLM to classify user messages as "安全"/"危险". On "危险", jumps to end node with block message. Supports `fail-open` and `fail-closed` modes.
- `graph/harness_security.py` — `HarnessSecurityMiddleware` implements `wrap_tool_call` hook. Intercepts tool execution to block sensitive file access (glob patterns), dangerous commands (regex), and custom rules from `config/harness_rules.yaml` (hot-reloadable).
- `graph/harness_review.py` — `HarnessReviewMiddleware` implements `after_agent` hook. Runs LLM-based quality review after each conversation turn: answer quality scoring, hallucination detection, tool call audit.
- `graph/llm.py` — LLM/Embedding factory using OpenAI-compatible APIs. Supports providers: zhipu, bailian, deepseek, openai.
- `graph/checkpointer.py` — Postgres-backed LangGraph checkpointer for multi-turn state persistence.

**Memory system** (`MEMORY_BACKEND` env var: `off` | `v3`):

- `memory_module_v3/` — Four-layer memory pyramid (L0→L1→L2→L3) with auto-capture and auto-recall. Includes `offload/` — Symbolic Short-Term Memory pipeline (L1 summary → L1.5 task judgment → L2 Mermaid generation → L3 progressive compression). Two independent operations in `before_model`: **summary replacement** — replace old `ToolMessage.content` with plain text `[Offloaded Tool Result | node: N2]\nSummary: ...\nresult_ref: refs/xxx.md`; **MMD injection** — insert active `.mmd` Mermaid diagram as a `HumanMessage` wrapped in `<current_task_context>` tags, positioned after the last user message.

**Injection modes** (v3): `tool` (agent calls `search_memory_v3` autonomously), `always` (force-inject every turn, default), `off`.

**Tools:** Registered in `tools/__init__.py`. Core tools: terminal, python_repl, fetch_url, read_file, PDF parser, wiki engine (8 tools in `wiki_engine_tool.py`: RegisterSource, SaveWikiPage, ReadWikiPage, ListWikiPages, RebuildIndex, AppendLog, LintWiki, QueryWiki). Memory tools added conditionally based on backend config.

**arXiv digest system** (daily paper ingestion):
- `service/scheduler.py` — APScheduler cron job, runs at `ARXIV_DIGEST_HOUR` (default 8:00 Asia/Shanghai)
- `service/arxiv_service.py` — Fetches from 5 RSS feeds (cs.AI/CL/MA/IR/RO), two-stage filter (14 broad keywords → semantic ranking against 5 interest topics)
- `service/digest_pipeline.py` — Per-paper pipeline: download PDF → parse → LLM analysis (7 sections) → entity extraction (concepts/methods/datasets) → create wiki pages (5 types) → tag classification (16 tags from `wiki/tags.yaml`) → auto-create surveys (tag with ≥3 papers)
- `service/wechat_notifier.py` — Push daily digest via Enterprise WeChat webhook
- `api/digest.py` — Endpoints: `GET /status`, `GET /test` (dry run), `POST /run` (manual trigger)
- Post-digest lint runs `LintWikiTool(auto_fix=True, backfill=True)` to fix structure and backfill missing entities
- Config: `ARXIV_DIGEST_HOUR`, `ARXIV_DIGEST_ENABLED`, `WECHAT_WEBHOOK_KEY`

**Wiki knowledge management** (file-first, 3-layer architecture):
- Layer 1: `raw/sources/` — Original files (PDF/MD), SHA-256 dedup via `manifest.jsonl`
- Layer 2: `wiki/` — 8 entity types (paper/concept/method/dataset/author/survey/comparison/idea), each entity = 1 markdown file with YAML frontmatter + `[[wikilink]]` cross-references
- Layer 3: `wiki/templates/` (7 tpl-*.md), `wiki/tags.yaml` (16 research domains), lint rules
- `service/wiki_retriever.py` — Hybrid retrieval (BM25 jieba + embedding cosine, RRF fusion k=60)
- Knowledge entropy management: 6 entropy sources (duplication, dangling refs, orphans, staleness, fragmentation, context pollution) with automated lint + backfill governance
- Obsidian-compatible: `.obsidian/` config, graph view with entity type color coding

**System prompt assembly:** `service/prompt_builder.py` concatenates: `workspace/SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`, skills snapshot, and (optionally) memory retrieval context. Changes to workspace files take effect on next request without code changes.

**Skills:** `skills/*/SKILL.md` — agent reads snapshot first, then drills into specific skill files on demand.

### Frontend (Next.js 14 + React 18 + TypeScript)

- `src/app/page.tsx` — Main workspace: three-panel layout (sidebar, chat, resizable)
- `src/components/chat/` — ChatPanel, ChatMessage, ChatInput, ThoughtChain (tool call visualization), RetrievalCard
- `src/components/editor/` — InspectorPanel (Monaco editor for files/memory/skills/workspace)
- `src/components/layout/` — Navbar, Sidebar, ResizeHandle
- `src/lib/store.ts` — App state management

Frontend connects to backend at `http://localhost:8002` via SSE streaming.

**SSE event protocol** (emitted by `/api/chat`):
- `token` — incremental content chunk (append to current message)
- `tool_start` — tool invocation begins (name + input)
- `tool_end` — tool invocation complete (output)
- `new_response` — new assistant message segment (multi-turn tool use)
- `done` — stream complete
- `title` — session title generated (refresh session list)
- `retrieval` — memory retrieval results attached to message
- `error` — error occurred

## Development Commands

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# Start API (from backend/ directory)
uvicorn app:app --host 0.0.0.0 --port 8002 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev       # Starts on port 7788 (see package.json)
```

### Tests

```bash
cd backend
pytest tests/ -v
pytest tests/test_smoke.py              # Single test file
pytest tests/test_guardian.py           # Guardian-specific tests
pytest tests/test_harness_security.py   # Harness security rules (34 tests)
pytest tests/test_harness_review.py     # Harness review middleware (11 tests)
pytest tests/test_agent_guardian_integration.py  # Agent + Guardian integration
```

## Configuration

**Environment:** Copy `backend/config/.env.example` to `backend/config/.env`. Required keys: `LLM_PROVIDER` + provider-specific API key, `EMBEDDING_PROVIDER` + embedding API key.

**Runtime config:** `backend/config/config.json` — runtime flags like `rag_mode`. Modified via `/api/config` endpoints.

**Provider aliases** (in `config.py`): `glm`/`zhipuai`/`bigmodel` → `zhipu`; `aliyun`/`dashscope`/`qwen` → `bailian`; `siliconflow` → `deepseek`. Use any alias in `LLM_PROVIDER` or `EMBEDDING_PROVIDER`.

**Key env vars:**

| Var | Purpose |
|-----|---------|
| `LLM_PROVIDER` | zhipu / bailian / deepseek / openai |
| `MEMORY_BACKEND` | off / v3 |
| `MEMORY_V3_INJECT` | always / tool / off (default: always) |
| `GUARDIAN_ENABLED` | true/false — prompt injection pre-filter |
| `GUARDIAN_FAIL_MODE` | closed (block on error) / open (allow on error) |
| `SUMMARIZATION_ENABLED` | Enable conversation compression middleware |
| `CHECKPOINTER` | Set to `postgres` for Postgres-backed state persistence |
| `LANGFUSE_SECRET_KEY/PUBLIC_KEY/BASE_URL` | Optional Langfuse tracing |
| `HARNESS_ENABLED` | Master switch for Harness system (default true) |
| `HARNESS_SECURITY_ENABLED` | Tool-level security interception (default true) |
| `HARNESS_REVIEW_ENABLED` | Post-conversation auto-review (default true) |
| `HARNESS_RULES_PATH` | Path to security rules YAML (default `config/harness_rules.yaml`) |

## Key Design Patterns

- **Middleware chain:** Guardian (`before_agent`) → HarnessSecurity (`wrap_tool_call`) → ContextOffload (`before_model`, `wrap_tool_call`) → Summarization (`before_model`) → HarnessReview (`after_agent`). Each middleware implements a subset of 7 available hooks from LangChain's `AgentMiddleware` base class.
- **File-as-memory:** Sessions persist as `backend/sessions/*.json`.
- **Idempotent distillation:** After each chat turn, background task distills new exchanges only (deterministic exchange_id). Uses `DISTILL_*` model if configured, otherwise main LLM.
- **Provider aliasing:** `config.py` maps aliases (e.g., `glm`→`zhipu`, `aliyun`→`bailian`, `dashscope`→`bailian`) for flexible env var configuration.
- **Checkpointer reconnect:** `chat.py` catches recoverable Postgres connection errors and retries once after reconnecting.
- **Harness rules hot-reload:** `config/harness_rules.yaml` is cached in memory with file mtime check. Editing the YAML takes effect on the next tool call without restarting the backend.
- **CI harness check:** `.github/harness-check.yml` runs on PR/push — validates harness rules YAML schema and runs harness-specific tests.
- **Claude Code hooks:** `.claude/hooks/*.mjs` implement the automated review loop. `pre-tool-check.mjs` intercepts dangerous operations (`.env` protection, dangerous commands). `session-context.mjs` injects git status on session start. `session-review.mjs` generates a review report to `.claude/reviews/` on stop. `pre-compact.mjs` preserves critical context before compaction. Hooks are configured in `.claude/settings.json`. **Path convention:** hook commands use `node .claude/hooks/...` (relative to `miniOpenClaw-main/` working directory) since `settings.json` and hooks both live under `miniOpenClaw-main/.claude/`.

## Documentation

`docs/` contains detailed architecture walkthroughs beyond what CLAUDE.md covers:
- `memory_system.md` — Full memory system overview (v2 vs v3, all config vars)
- `memory_v3_implementation_walkthrough.md` — Step-by-step v3 pipeline code walkthrough
- `offload_pipeline_example.md` — End-to-end example: tool call → L1 summary → L2 Mermaid → L3 compression
- `academic_agent_deep_dive.md` — Agent architecture deep dive
- `arxiv_digest_system.md` — arXiv paper ingestion pipeline: RSS fetching, dual filtering, LLM analysis, wiki page creation, WeChat push
- `knowledge_entropy_management.md` — Wiki knowledge management: 8 tools, entity types, entropy governance, automated lint/backfill

## Evaluation

```bash
# Memory system evaluation (run from project root)
python eval_memory.py

# Persona memory evaluation (run from backend/)
cd backend && python eval_persona_memory.py
```

Results saved as `eval_results_*.json`. See `eval_v3_report.md` for baseline comparison.

## Scripts

- `scripts/check.mjs` — Pre-commit checks (lint, type-check)
- `scripts/init.mjs` — Project initialization (dependencies, config)
- `scripts/upgrade.mjs` — Dependency upgrade helper

## Infrastructure Requirements

- Python 3.10+
- Node.js 18+
- PostgreSQL with pgvector extension (for memory v2/v3 and optional checkpointer)
- Optional: Langfuse (has its own Postgres — can share with pgvector image)

# 行为准则（Karpathy 原则）

## Think Before Coding
- 假设必须说清楚，不确定就问
- 有多个方案时列出，不要默默选一个
- 有更简单的方法就说出来

## 消除信息差
- **追问**：用户描述有歧义或缺失关键信息时，先追问再动手
- **质疑**：即使指令看似完整，也多想一步——有没有逻辑漏洞？有没有被忽略的前提？
- 质疑要带证据：说出你观察到的问题 + 给出替代方案
- 用户说"就这样做"不意味着就是对的——双方可能存在你看不到的盲区

## 讨论与执行分离
- 讨论阶段只分析、提问、列方案，不修改文件
- 不要自己判断"讨论已经够了"——问出口才算数
- 用户明确同意执行后才动手，一次只做一件事

## Simplicity First
- 不多写一行没被要求的代码
- 不加不需要的抽象、配置、灵活性
- 如果写了 200 行但能缩成 50 行，重写

## Surgical Changes
- 只动必须动的代码，不顺手"改善"无关代码
- 不重构没坏的东西
- 每行改动的代码都应能追溯到用户请求

## Goal-Driven Execution
- 每个任务转成可验证的目标
- 多步骤任务先列计划再动手

# 全局约定

- **规则放 CLAUDE.md，工作流放 Skills**
- 涉及文件操作先问用户意图
- 每次对话只给 AI 看需要的内容，避免无关上下文稀释注意力

# 自动审查闭环

- SessionStart 自动注入 git 状态
- PreToolUse 自动拦截危险操作（.env 保护、危险命令）
- Stop 自动生成审查报告至 .claude/reviews/（按日期累积）
- 下次 SessionStart 自动加载最近几次审查记录

# 成熟度路线图

| 级别 | 名称 | 标志 | 状态 |
|:---:|---|----|----|
| L0 | 裸用 | 没有 CLAUDE.md | — |
| L1 | 规则层 | 有 CLAUDE.md + 行为准则 | — |
| **L2** | **反馈回路** | **PreToolUse + SessionStart + Stop 已激活** | **← 当前** |
| L3 | 自动修正 | 加上 PostToolUse 后自动格式化 | 取消 settings.json 中 PostToolUse 注释即可 |
| L4 | 自治系统 | Agent 定期扫描代码/文档一致性，自动发起修复 PR | — |

# Skill 路由

根据项目技术栈和任务类型，推荐以下 Skill：

| 任务类型 | Skill | 触发条件 |
|---------|-------|---------|
| Harness 管理 | harness-init / harness-mode | 用户要求调整 Harness 配置 |
| 前端组件开发 | frontend-design | 涉及 .tsx / .jsx 文件修改 |
| 代码简化 | simplify | 重构或清理代码后 |
| 调试 | systematic-debugging | 遇到 bug 或测试失败 |
| 代码审查 | requesting-code-review | 完成功能开发后 |

> Agent 在遇到对应任务时，应优先调用路由表中的 Skill。
