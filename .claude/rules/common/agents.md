# Agent Orchestration

## Available Agents

Located in `.claude/agents/`:

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| planner | Implementation planning | Complex features, refactoring |
| architect | System design | Architectural decisions |
| code-architect | Code-level architecture | Module design, API boundaries |
| tdd-guide | Test-driven development | New features, bug fixes |
| code-reviewer | Code review | After writing code |
| code-simplifier | Code simplification | Refactoring for clarity |
| code-explorer | Codebase exploration | Understanding unfamiliar code |
| security-reviewer | Security analysis | Before commits |
| performance-optimizer | Performance analysis | Latency, memory, throughput |
| build-error-resolver | Fix build errors | When build fails |
| e2e-runner | E2E testing | Critical user flows |
| refactor-cleaner | Dead code cleanup | Code maintenance |
| doc-updater | Documentation | Updating docs |
| python-reviewer | Python code review | Python files |
| fastapi-reviewer | FastAPI patterns | API endpoints, dependencies |
| typescript-reviewer | TypeScript review | TS/TSX files |
| react-reviewer | React patterns | Components, hooks, state |
| database-reviewer | Database review | Queries, migrations, schema |
| harness-optimizer | Harness config | Hook/skill tuning |
| loop-operator | Loop management | Autonomous loop scheduling |
| agent-evaluator | Agent quality | Evaluating agent outputs |
| silent-failure-hunter | Silent failures | Swallowed errors, empty catches |
| pr-test-analyzer | PR test analysis | Test coverage gaps |
| mle-reviewer | ML engineering | Model training, serving |

## Immediate Agent Usage

No user prompt needed:
1. Complex feature requests - Use **planner** agent
2. Code just written/modified - Use **code-reviewer** agent
3. Bug fix or new feature - Use **tdd-guide** agent
4. Architectural decision - Use **architect** agent

## Parallel Task Execution

ALWAYS use parallel Task execution for independent operations:

```markdown
# GOOD: Parallel execution
Launch 3 agents in parallel:
1. Agent 1: Security analysis of auth module
2. Agent 2: Performance review of cache system
3. Agent 3: Type checking of utilities

# BAD: Sequential when unnecessary
First agent 1, then agent 2, then agent 3
```

## Multi-Perspective Analysis

For complex problems, use split role sub-agents:
- Factual reviewer
- Senior engineer
- Security expert
- Consistency reviewer
- Redundancy checker
