# Refactoring & Architecture Change Rules

> Rules for safe refactoring, extracted from real incidents.

## Dependency Analysis Before Refactoring

Before modifying any module's interface, storage backend, or data flow:

1. **Trace all consumers**: `grep` for every import, method call, and type reference
2. **Map the call chain**: from entry point (e.g., `agent.py`) down to the leaf module
3. **Check for transitive dependencies**: A imports B which imports C — changing C may break A
4. **List all files to modify**: write it down before touching any code
5. **Verify no dead code masks the issue**: unused imports or methods may hide real dependencies

```bash
# Find all consumers of a module
grep -r "from.*module_name import" --include="*.py" .
grep -r "module_name\." --include="*.py" .
```

**Why:** Changing L0 from PostgreSQL to file-based storage required updating 6 files (l0_repo, l0_recorder, l1_extractor, pipeline/manager, agent.py, schema.sql). Missing any one would cause a runtime crash.

**How to apply:** When the task involves changing a module's storage backend, interface, or constructor signature, always run the grep analysis first and list all affected files.

## Documentation Sync on Architecture Changes

When changing architecture (storage backend, data flow, API surface):

1. **Identify affected docs**: `grep` in `docs/` for the module/class/table name
2. **Update in the same session**: do not defer doc updates to "later"
3. **Update all layers**: overview tables, detailed walkthroughs, code examples, and config tables
4. **Verify no stale references**: search for old class names, old table names, old file paths

```bash
# Find docs referencing a module
grep -r "old_class_name\|old_table_name" docs/ --include="*.md"
```

**Why:** After changing L0 to file-based storage, docs still described `l0_messages` PostgreSQL table, `embedding` column, and `kv_store` for L3 persona — all stale.

**How to apply:** After any architecture change, search `docs/` for the old names and update every occurrence.

## .gitignore Verification

After adding new dependencies, build outputs, or data directories:

1. **Run `git status`** — check for unexpected untracked files
2. **Run `git check-ignore <path>`** — verify new paths are covered
3. **Add missing patterns immediately** — do not commit first and fix later

```bash
# Verify a path is ignored
git check-ignore node_modules/
git check-ignore frontend/.next/
```

**Why:** `node_modules/` was not in `.gitignore` because only `frontend/node_modules/` was listed. A `git add .` would have committed hundreds of MB of dependencies.

**How to apply:** After `npm install`, `pip install`, or creating any new directory that should not be tracked, immediately verify it is in `.gitignore`.
