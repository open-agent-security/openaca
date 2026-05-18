# Claude Code Parser Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining Claude Code inventory gaps for user/project commands, agents, skills, and subagent-scoped executable configuration.

**Architecture:** Extend the existing Claude Code install parser rather than adding a second discovery path. Endpoint mode should enumerate personal and project-scoped filesystem components, while repo mode should discover the same project-scoped components through the parser registry. Subagent-scoped inline MCP servers and hooks should be emitted as child components attributed to the subagent that declares them.

**Tech Stack:** Python 3.11, pytest, existing `ComponentRef` parser model.

---

## Scope

Implement:

- Personal commands from `<install_root>/commands/**/*.md`.
- Personal agents from `<install_root>/agents/**/*.md`.
- Project skills from every `<project_root>/**/.claude/skills/<skill-name>/SKILL.md`.
- Project commands from `<project_root>/.claude/commands/**/*.md`.
- Project agents from `<project_root>/.claude/agents/**/*.md`.
- Repo-mode recursive command/agent/skill discovery for `.claude/` trees.
- Subagent frontmatter `mcpServers` and `hooks` for user/project/repo agents.

Do not implement:

- Managed settings directories.
- `--add-dir` discovery beyond the supplied `project_root`.
- Declarative subagent `skills` relationships. Those preload an existing skill; they do not introduce a new installed artifact.

## Tasks

### Task 1: Recursive Command And Agent Enumeration

**Files:**

- Modify: `tools/parsers/claude_command_agent.py`
- Test: `tests/test_parsers/test_claude_command_agent.py`

- [x] Add a failing test proving nested markdown files under a command directory are discovered, with identity based on the filename/frontmatter name rather than the subdirectory.
- [x] Add a failing test proving nested markdown files under an agent directory are discovered.
- [x] Implement recursive enumeration with deterministic sorted output.
- [x] Verify: `uv run pytest tests/test_parsers/test_claude_command_agent.py -q`.

### Task 2: Endpoint Direct User And Project Components

**Files:**

- Modify: `tools/parsers/claude_install.py`
- Test: `tests/test_parsers/test_claude_install.py`

- [x] Add a failing test for personal direct commands and agents under `<install_root>/commands` and `<install_root>/agents`.
- [x] Add a failing test for project skills, commands, and agents under `<project_root>/.claude`.
- [x] Implement the walks in `_walk_direct_components`.
- [x] Verify: `uv run pytest tests/test_parsers/test_claude_install.py -q`.

### Task 3: Repo-Mode Recursive Project Components

**Files:**

- Modify: `tools/parsers/__init__.py`
- Test: `tests/test_parsers/test_repo_mode_components.py`

- [x] Add failing repo-mode tests for nested `.claude/skills`, `.claude/commands`, and `.claude/agents`.
- [x] Update the registry patterns to discover recursive project component paths without duplicate direct hits.
- [x] Verify: `uv run pytest tests/test_parsers/test_repo_mode_components.py -q`.

### Task 4: Subagent-Scoped MCP Servers And Hooks

**Files:**

- Modify: `tools/parsers/claude_command_agent.py`
- Test: `tests/test_parsers/test_claude_command_agent.py`

- [x] Add a failing test for a user/project subagent with inline `mcpServers` frontmatter.
- [x] Add a failing test for a user/project subagent with inline `hooks` frontmatter.
- [x] Implement frontmatter child-component parsing for non-plugin agents only.
- [x] Verify child refs are attributed to the declaring `claude-agent/<name>` identity.
- [x] Verify: `uv run pytest tests/test_parsers/test_claude_command_agent.py -q`.

### Task 5: Full Verification

**Files:**

- Modify: plan checkboxes as tasks complete.

- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run pyright`.
- [x] Run `uv run pytest -q`.
- [x] Run `uv run openaca lint overlays/`.
- [x] Review `git diff` for scope creep before committing.
