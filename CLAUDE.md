# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PaperMind-Agent (originally "Mini-OpenClaw") is a local, file-first, auditable AI Agent workbench built on LangChain 1.x `create_agent`. It features structured long-term memory with hybrid retrieval, a prompt-injection defense layer (Guardian), a skill-based capability system, a daily arXiv digest + on-demand PDF ingest pipeline, and Langfuse observability integration.

## Architecture

### Backend (FastAPI + LangChain Agent)

**Entry point:** `backend/app.py` — FastAPI lifespan initializes checkpointer, `AgentManager`, memory indexer, and arXiv scheduler. Routers: `/api/chat`, `/api/sessions`, `/api/files`, `/api/ingest`, `/api/tokens`, `/api/compress`, `/api/config`, `/api/digest`.

**Request flow:** Frontend → `POST /api/chat` (SSE) → `AgentManager.astream()` → LangChain agent graph (Guardian → Harness security → context offload → summarization → parallel tool calls → Harness review) → streamed response.

**Key layers:**

- `graph/agent.py` — `AgentManager` singleton: agent creation, `astream()`, title generation, history summarization, v3 memory auto-recall + injection context per turn.
- `graph/agent_factory.py` — `build_agent_config()` + `create_agent_from_config()`. Middleware chain (order matters): Guardian → HarnessSecurity → ContextOffload → Summarization → HarnessReview. Patches the tools node for parallel execution *after* `create_agent()`.
- `graph/guardian.py` — `GuardianMiddleware` (`before_agent`): lightweight LLM classifies messages 安全/危险; on 危险 jumps to end node. `fail-open` / `fail-closed` modes.
- `graph/harness_security.py` — `HarnessSecurityMiddleware` (`wrap_tool_call`): blocks sensitive file access (glob), dangerous commands (regex), custom rules from `config/harness_rules.yaml` (hot-reloadable via mtime).
- `graph/harness_review.py` — `HarnessReviewMiddleware` (`after_agent`): LLM review per turn — quality scoring, hallucination detection, tool-call audit.
- `graph/llm.py` — LLM/embedding factory (OpenAI-compatible). `get_llm()` for main, `get_fast_llm()` for Guardian/titles/summarization/offload/L1 extraction (degrades to main when unset). Providers: zhipu, bailian, deepseek, openai.
- `graph/checkpointer.py` — Postgres-backed LangGraph checkpointer for multi-turn state.

**Memory** (`MEMORY_BACKEND`: `off` | `v3`): `memory_module_v3/` is a four-layer pyramid (L0→L1→L2→L3), auto-capture + auto-recall. Hybrid storage — L0 (raw messages) JSON at `l0/{session_id}.json`; L2 (scenes) `.md` at `scenes/`; L3 (persona) `persona.md`; L1 (facts) + pipeline state in PostgreSQL/pgvector. L0 has no embedding (buffer for L1 extraction only). `offload/` is the Symbolic Short-Term Memory pipeline (L1 summary → L1.5 task judgment → L2 Mermaid → L3 progressive compression); in `before_model` it both replaces old `ToolMessage.content` with a plain-text summary+`result_ref`, and injects the active `.mmd` as a `<current_task_context>` HumanMessage after the last user message.

**Injection modes** (v3): `always` (force-inject every turn, default) | `tool` (agent calls `search_memory_v3` autonomously) | `off`.

### Tools

`tools/__init__.py`. Core: terminal, python_repl, fetch_url, read_file, PDF parser, wiki engine (8 tools in `wiki_engine_tool.py`: RegisterSource, SaveWikiPage, ReadWikiPage, ListWikiPages, RebuildIndex, AppendLog, LintWiki, QueryWiki). Memory tools added conditionally on backend config.

### Latency optimization layer

- **Parallel tool execution** (`graph/parallel_tools.py`) — `patch_agent_for_parallel_tools()` runs multiple `tool_calls` via `asyncio.gather` (sum→max), each still routed through `HarnessSecurity` first. Non-destructive: falls back if graph shape differs.
- **Smart routing** (`graph/route_classifier.py`) — `classify_query()` → `RouteTier` L0/L1/L2 (chitchat / knowledge-QA / complex). **Defined, tested, benchmarked, but NOT wired into `astream`** — designed-but-pending per `docs/latency_optimization_summary.md`. Intended mapping if wired: L0 fast-LLM direct (skip all); L1 fast-LLM + recall + read-only tools + Guardian; L2 full pipeline.
- **Embedding + recall cache** (`memory_module_v3/retrieval/service.py`) — L2/L3 stable context cached with a dirty flag (`PipelineManager` invalidates after L2/L3 runs); embeddings via quantized-key Redis (`_emb_cache_get`). Auto-recall splits stable context (inject only on change) from L1 dynamic context (inject every turn).
- **Latency instrumentation** — `astream()` logs `recall_ms` / `first_token_ms` / `ttft_ms` from `_t0/_t1/_t2`. Read these when diagnosing slow turns.

### arXiv digest + PDF ingest (share digest internals)

- **arXiv digest** (daily): `service/scheduler.py` (APScheduler at `ARXIV_DIGEST_HOUR`), `service/arxiv_service.py` (5 RSS feeds cs.AI/CL/MA/IR/RO → 14-keyword broad filter → semantic ranking against 5 interest topics), `service/digest_pipeline.py` (per-paper: download → parse → LLM analysis (7 sections) → entity extraction → create wiki pages (5 types) → 16-tag classification from `wiki/tags.yaml` → auto-surveys for tags with ≥3 papers), `service/wechat_notifier.py` (Enterprise WeChat push), `api/digest.py` (`GET /status`, `GET /test` dry-run, `POST /run`). Post-digest lint runs `LintWikiTool(auto_fix=True, backfill=True)`. Exposes reusable `_analyze_with_llm` / `_extract_entities_with_llm` / `_create_wiki_pages`.
- **PDF ingest** (on-demand upload): `api/ingest.py` `POST /api/ingest/pdf` (multipart, or `url`/`title`, 50 MB cap, SSE stream). `service/ingest_service.py` `process_pdf_upload()` runs the digest stages on one user PDF. Parser fallback: MinerU first (only when `pdf_url` *and* `MINERU_TOKEN` given; `tools/mineru_client.py`), else PyMuPDF (`tools/pdf_parser_tool._parse_pdf`); chosen `source` is reported in progress. SSE stages: `parsing` → `parsing_done` → `analysis` → `entities` → `wiki_pages` → `index` → `done` (`paper_slug`, `entity_slugs`, `wiki_path`). Temp files cleaned in the generator's `finally`.

### Wiki knowledge management (file-first, 3 layers)

Layer 1 `raw/sources/` — original PDF/MD, SHA-256 dedup via `manifest.jsonl`. Layer 2 `wiki/` — 8 entity types (paper/concept/method/dataset/author/survey/comparison/idea), each entity = 1 markdown file (YAML frontmatter + `[[wikilink]]` cross-refs). Layer 3 `wiki/templates/` (7 `tpl-*.md`), `wiki/tags.yaml` (16 domains), lint rules. `service/wiki_retriever.py` — hybrid retrieval (BM25 jieba + embedding cosine, RRF k=60). Knowledge-entropy governance: 6 sources (duplication, dangling refs, orphans, staleness, fragmentation, context pollution) with automated lint + backfill. Obsidian-compatible (`.obsidian/`, graph view).

**System prompt assembly:** `service/prompt_builder.py` concatenates `workspace/SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`, skills snapshot, optional memory context — changes take effect next request, no code change.

**Skills:** `skills/*/SKILL.md` — agent reads the snapshot first, drills into specific files on demand.

### Frontend (Next.js 14 + React 18 + TypeScript)

`src/app/page.tsx` — three-panel workspace (sidebar/chat/resizable). `src/components/chat/` (ChatPanel, ChatMessage, ChatInput, ThoughtChain tool-call viz, RetrievalCard), `editor/` (InspectorPanel Monaco for files/memory/skills/workspace), `layout/` (Navbar, Sidebar, ResizeHandle). `src/lib/store.ts` — state. Connects to backend at `http://localhost:8002` via SSE.

**SSE event protocol** (`/api/chat`): `token` (append), `tool_start`/`tool_end` (name+input/output), `new_response` (assistant segment for multi-turn tool use), `done`, `title` (session title), `retrieval` (memory results), `error`.

## Development Commands

```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8002 --reload

# Frontend (port 7788)
cd frontend && npm install && npm run dev

# Tests (from backend/)
pytest tests/ -v
pytest tests/test_route_classifier.py           # Smart-routing L0/L1/L2
pytest tests/test_harness_security.py           # 34 security-rule tests
pytest tests/test_guardian.py                   # Guardian
pytest tests/test_harness_review.py             # review middleware (11)
pytest tests/test_agent_guardian_integration.py  # Agent + Guardian

# Latency benchmark (from backend/)
python scripts/benchmark_optimizations.py        # classify_query, parallel vs serial tools

# Memory evaluation
python eval_memory.py                            # from project root
cd backend && python eval_persona_memory.py      # results: eval_results_*.json, eval_v3_report.md
```

## Configuration

Copy `backend/config/.env.example` to `backend/config/.env`. Runtime flags in `backend/config/config.json` (modified via `/api/config`). Provider aliases (`config.py`): `glm`/`zhipuai`/`bigmodel` → `zhipu`; `aliyun`/`dashscope`/`qwen` → `bailian`; `siliconflow` → `deepseek`.

| Var | Purpose |
|-----|---------|
| `LLM_PROVIDER` | zhipu / bailian / deepseek / openai |
| `MEMORY_BACKEND` | off / v3 |
| `MEMORY_V3_INJECT` | always (default) / tool / off |
| `MEMORY_V3_RECALL_STRATEGY` | hybrid (default) / keyword / embedding |
| `MEMORY_V3_PIPELINE_EVERY_N`, `_L1_IDLE_TIMEOUT`, `_L2_DELAY_AFTER_L1`, `_L2_MAX_INTERVAL`, `_L3_TRIGGER_EVERY_N` | L1/L2/L3 cadence |
| `MEMORY_V3_DENSE_TOP_K`, `_KEYWORD_TOP_K`, `_FINAL_TOP_K`, `_INJECT_TOP_K` | recall limits |
| `GUARDIAN_ENABLED` / `GUARDIAN_FAIL_MODE` / `GUARDIAN_TIMEOUT_MS` | injection pre-filter, closed/open, 5000ms |
| `SUMMARIZATION_ENABLED` / `_TRIGGER_MESSAGES` / `_KEEP_MESSAGES` | compression on, trigger 50, keep 20 |
| `CHECKPOINTER` | `postgres` for state persistence |
| `MINERU_TOKEN` (+ `_BASE_URL`/`_MODEL`/`_POLL_INTERVAL`/`_MAX_WAIT`) | enable MinerU parser in ingest; else PyMuPDF |
| `ARXIV_DIGEST_ENABLED` / `ARXIV_DIGEST_HOUR` / `WECHAT_WEBHOOK_KEY` | daily digest |
| `LANGFUSE_SECRET_KEY` / `PUBLIC_KEY` / `BASE_URL` | optional tracing |
| `HARNESS_ENABLED` / `HARNESS_SECURITY_ENABLED` / `HARNESS_REVIEW_ENABLED` / `HARNESS_RULES_PATH` | Harness master + sub-switches (all default true), rules YAML path |

## Key Design Patterns

- **Middleware chain** — Guardian(`before_agent`) → HarnessSecurity(`wrap_tool_call`) → ContextOffload(`before_model`,`wrap_tool_call`) → Summarization(`before_model`) → HarnessReview(`after_agent`). Each implements a subset of LangChain `AgentMiddleware`'s hooks (`before_agent` / `before_model` / `after_model` / `after_agent` / `wrap_model_call` / `wrap_tool_call`).
- **File-as-memory** — `sessions/*.json`, L0 `l0/{session}.json`, L2 `scenes/*.md`, L3 `persona.md`.
- **Idempotent distillation** — after each turn, a background task distills only new exchanges (deterministic `exchange_id`); uses `DISTILL_*`/fast LLM.
- **Checkpointer reconnect** — `chat.py` catches recoverable Postgres errors, reconnects, retries once.
- **Harness rules hot-reload** — `harness_rules.yaml` cached with mtime check; edits apply on next tool call, no restart.
- **CI** — `.github/workflows/harness-check.yml` runs `node scripts/check.mjs` (lint + type-check) and `npm test` (vitest) on push/PR.
- **`.claude/` harness removed from repo** (commit `4cef8ed`) — no `.claude/hooks/*.mjs` or tracked `settings.json`; only `.claude/settings.local.json` (local perms). Don't reference deleted hook scripts.

## Documentation

`docs/` (Chinese-named) holds detailed walkthroughs — `00-总览-PaperMind-Agent-项目文档.md`, `01-记忆-四层金字塔详解.md` / `-场景举例.md` / `-源码级实现走读.md`, `02-Wiki-arXiv自动消化推送.md` / `-三层架构与熵管理.md`, `03-治理-ContextOffload管线实例.md` / `-中间件链与会话管理.md`, `04-参考-全链路流程例子解构.md` / `-工具调用原理.md` / `-项目概要.md`. `docs/latency_optimization_summary.md` documents the latency layer — read alongside `graph/route_classifier.py`, `parallel_tools.py`, `memory_module_v3/retrieval/service.py`, `graph/llm.py`.

## Scripts & Infrastructure

`scripts/{check,init,upgrade,gc-scan}.mjs`. Requires Python 3.10+, Node 18+, PostgreSQL+pgvector (L1 facts, pipeline state, optional checkpointer); optional Langfuse.

# 行为准则（Karpathy 原则）

- **Think Before Coding** — 假设必须说清楚，不确定就问；有多个方案列出，不默默选一个；有更简单的方法就说出来。
- **消除信息差** — 用户描述有歧义或缺失时先追问再动手；即使指令看似完整也多想一步（逻辑漏洞、被忽略的前提）；质疑要带证据（问题 + 替代方案）；"就这样做"不意味着对——双方可能有你看不到的盲区。
- **讨论与执行分离** — 讨论阶段只分析、提问、列方案，不改文件；不自己判断"讨论够了"——问出口才算数；用户明确同意后才动手，一次只做一件事。
- **Simplicity First** — 不多写一行没被要求的代码；不加不需要的抽象/配置/灵活性；200 行能缩成 50 行就重写。
- **Surgical Changes** — 只动必须动的代码，不顺手"改善"无关代码；不重构没坏的东西；每行改动都应能追溯到用户请求。
- **Goal-Driven Execution** — 每个任务转成可验证的目标；多步骤先列计划再动手。

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
