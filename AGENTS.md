# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Auto-syncs to JSONL for version control
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs with git:

- Exports to `.beads/issues.jsonl` after changes (5s debounce)
- Imports from JSONL when newer (e.g., after `git pull`)
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

<!-- END BEADS INTEGRATION -->

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Adding New MCP Tools (prismic-content-mcp)

Use this checklist when adding any new tool.

### 1) Keep architecture consistent

- Put HTTP/business logic in `prismic_content_mcp/prismic.py` on `PrismicService`.
- Put MCP handler wrappers in `prismic_content_mcp/server.py` as `handle_prismic_*`.
- Register tool functions in `create_server()` in `prismic_content_mcp/server.py`.
- Keep handlers thin: validate/route inputs and call service methods.
- Bubble upstream/validation errors clearly; do not add fallback branching.

### 2) Naming and response conventions

- Tool names use `prismic_*` and should be explicit (`prismic_get_*`, `prismic_add_*`, `prismic_upsert_*`).
- Handler names mirror tool names (`handle_prismic_get_types`, etc.).
- Return objects (not raw lists) with stable keys, for example:
  - `{"refs": [...]}`
  - `{"types": [...]}`
  - `{"media": {...}}`
- For list-like outputs, normalize shape in service layer when possible.

### 3) Content API patterns

- Reuse Content API root (`GET /api/v2`) for repository metadata (`refs`, `types`, languages, forms).
- Reuse shared root-fetch helpers rather than duplicating calls.
- For document reads, continue using `get_documents()` and convenience mappings:
  - `type` -> `[[at(document.type,"<type>")]]`
  - merge `type` predicate with `q` if both are present.
- Respect explicit `ref` when supplied; otherwise resolve master ref.
- Document auth expectations clearly: reading non-master refs may require
  `PRISMIC_CONTENT_API_TOKEN` depending on repository API visibility settings.

### 3.1) Migration vs Content readback semantics

- Migration API is write-focused for this MCP (create/update), not the source of
  document reads.
- After migration upserts, read-back is done through Content API using an
  explicit `ref` (typically a release ref from `prismic_get_releases` or
  `prismic_get_refs`).
- Do not imply that successful upsert guarantees immediate master-read
  visibility; call out release/publish dependency in tool and README docs.

### 4) Add tests in both layers

- Handler-level tests: `tests/test_content_tools.py`
  - Verify handler calls the right service method.
  - Verify response key/shape.
- Service-level HTTP behavior tests: `tests/test_content_ref_resolution.py`
  - Mock Content API responses and assert parsing/normalization.
  - Add negative tests for missing/invalid required fields.
- Keep tests deterministic and focused on behavior contracts.

### 5) Update docs every time

- Update `README.md` in all relevant places:
  - `Example Prompts` section
  - `What You Get` summary when capability scope changes
  - `MCP Tools` section with clear description and output shape
- Tool docs should include usage hints and native Prismic mapping details.

### 6) Validate before handoff

- Run test suite:
  - `python3 -m pytest -q`
- If live tests are needed, run explicitly with env flags (see README).
- Confirm no accidental behavior changes in existing tools.

### 7) Typical minimal diff for a new read tool

1. Add `PrismicService` method in `prismic.py`.
2. Add `handle_prismic_*` wrapper in `server.py`.
3. Register `@server.tool(name="prismic_*")` in `create_server()`.
4. Add/extend tests in `test_content_tools.py` and `test_content_ref_resolution.py`.
5. Update README examples + MCP tool docs.
