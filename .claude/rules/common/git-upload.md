# Git Upload Standards

> This file defines rules for staging, committing, and pushing code to prevent accidents and maintain repo hygiene.

## Pre-Commit Checklist (MANDATORY)

Before ANY `git add` or `git commit`:

- [ ] Run `git diff` and `git diff --staged` — review every line being committed
- [ ] No hardcoded secrets (API keys, passwords, tokens, webhook URLs)
- [ ] No `.env`, `.env.local`, or any file matching `**/config/.env`
- [ ] No `node_modules/`, `.venv/`, `__pycache__/`, or other dependency/build artifacts
- [ ] No large binary files (>5MB) without explicit user approval
- [ ] No debug statements left behind (`console.log`, `print()`, `debugger`, `breakpoint()`)
- [ ] No IDE-specific files (`.vscode/`, `.idea/`, `*.code-workspace`)

## Staging Rules

### Prefer Explicit Add

```bash
# GOOD: stage specific files
git add backend/api/chat.py backend/services/rag.py

# BAD: stage everything blindly
git add .
git add -A
```

Only use `git add .` or `git add -A` after running `git status` and confirming every untracked file is intended.

### Never Stage These

| Pattern | Reason |
|---------|--------|
| `.env`, `**/.env`, `**/.env.local` | Secrets leak to history permanently |
| `**/node_modules/` | Dependency bloat, use lockfile instead |
| `**/.venv/`, `**/__pycache__/` | Local environment, not portable |
| `*.log` | Transient, often contains sensitive data |
| `backend/sessions/*.json` | User session data, PII risk |
| `backend/storage/*` | Runtime data, not source |
| `*.pyc`, `*.pyo`, `*.pyd` | Compiled bytecode |

### Verify .gitignore Coverage

When adding new config or data files, check if `.gitignore` already covers them. If not, add the pattern to `.gitignore` first, then commit the `.gitignore` change separately.

## Commit Granularity

- One logical change per commit — do not bundle unrelated changes
- If you fixed a bug AND refactored a function, that is two commits
- If a commit message needs "and" or "also", split it

```
# WRONG: bundled changes
fix: fix auth bug and refactor utils and update docs

# RIGHT: separate commits
fix: prevent token expiry race condition in auth middleware
refactor: extract retry logic into shared utility
docs: update API endpoint documentation
```

## Branch Naming

```
<type>/<short-description>

feat/arxiv-digest-pipeline
fix/guardian-fail-open-crash
refactor/memory-v3-retrieval
hotfix/session-checkpointer-reconnect
```

Types: `feat`, `fix`, `refactor`, `hotfix`, `docs`, `test`, `chore`

## Push Rules

### Never Force Push to Shared Branches

```bash
# FORBIDDEN on main, develop, release/*
git push --force
git push --force-with-lease

# ALLOWED on personal feature branches only
git push --force-with-lease origin feat/my-branch
```

### Always Use -u for New Branches

```bash
git push -u origin feat/my-branch
```

### Pre-Push Verification

Before `git push`:

1. Run tests if project has them (`pytest`, `npm test`)
2. Check `git log --oneline -5` — confirm commit messages are clean
3. Check `git status` — no unstaged changes left behind
4. Verify branch target is correct (`git branch --show-current`)

## Sensitive File Protocol

If a secret is accidentally committed:

1. **STOP** — do not push if not yet pushed
2. Rotate the exposed secret immediately
3. Use `git reset HEAD~1` to undo the commit (local only)
4. Add the file pattern to `.gitignore`
5. If already pushed, force-push after fix is safe only on personal branches; for shared branches, create a follow-up commit that removes the secret and open a security incident

## Large File Policy

Files >5MB require explicit user approval before staging:

- PDFs, images, model weights, datasets
- Use Git LFS for tracked large files when approved
- Never commit `node_modules/`, `.venv/`, or build outputs

## Commit Message Reference

See [git-workflow.md](./git-workflow.md) for the conventional commits format (`feat:`, `fix:`, etc.).
