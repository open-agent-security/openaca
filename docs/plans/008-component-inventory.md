# 008 — Component inventory: declared (repo) and active (fs) agent stack

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenACA see the full Tier-1 declarative agent-stack — not just plugin self-identity. In both `repo` mode (what this app will ship with) and `fs` mode (what's installed and running here), enumerate MCPs, skills, hooks, commands, and agents — with attribution that distinguishes plugin-bundled from bare/settings-scoped components.

**Architecture:** Three new parsers (`claude_skill`, `hooks_json`, `claude_command_agent`) wired into both modes via a shared component-walk helper. `fs` mode extends plan 007's `claude_install.py` to walk each active plugin's `installPath` and to enumerate bare components from `settings_layers.by_scope()` and `~/.claude/skills/`. `repo` mode adds the same parsers to the manifest registry, so a repo declaring `.claude/skills/<name>/SKILL.md` or `.claude/commands/*.md` emits those as inventory components too.

**Tech Stack:** Python (PyYAML for frontmatter, existing parser conventions), pytest. No new runtime deps.

**Depends on:** 007 (CLI split, attribution data model, settings_layers, minimal claude_install, claude-plugin matcher path).

---

## Context

Plan 007 wired `claude-plugin` advisories through the matcher and emitted one ComponentRef per active plugin in `fs` mode. That's only the plugin's *self-identity*. Real attack surface lives inside what the plugin (or the bare settings) declares:

- The plugin's bundled MCP servers (`.mcp.json`, inline `mcpServers`).
- The plugin's bundled skills (`skills/<name>/SKILL.md` per the canonical Agent Skills spec).
- The plugin's bundled hooks (`hooks/hooks.json` and inline `plugin.json.hooks`).
- The plugin's bundled commands and agents (`commands/*.md`, `agents/*.md`).
- Bare MCPs declared in `settings.<scope>.mcpServers` (not via a plugin).
- Bare hooks declared in `settings.<scope>.hooks`.
- Bare skills under `~/.claude/skills/<name>/` (user-installed outside any plugin).
- Project-scoped `.mcp.json` at the repo root.

Codex's framing (see ADR-0006 + Plan 007 context) sharpens what each mode is doing:

- **`repo` mode is application/deployed-agent SCA**: "What will this app ship with?" The parsers run against committed config files. Programmatic SDK configuration (`query({ mcpServers })`, `Agent(tools=[...])`) is invisible — that's Tier-3 SAST-like work deferred to V1.
- **`fs` mode is endpoint agent-stack SCA**: "What's installed and active on this machine?" The walk is lockfile-rooted; active plugin install roots are the boundary; orphaned cache versions don't count.

Both modes use the same parsers and emit the same component types. The difference is *what triggers the walk*: file rglob (repo) vs. resolved `installed_plugins.json` entries (fs). Attribution (`attributed_to`) reflects whether the component was discovered via an active plugin install root or via direct repo/settings declaration.

## File structure

| File | Status | Purpose |
|---|---|---|
| `tools/parsers/claude_skill.py` | Create | SKILL.md YAML frontmatter parser per agentskills.io spec |
| `tools/parsers/hooks_json.py` | Create | Plugin `hooks/hooks.json` + settings `hooks` key parser |
| `tools/parsers/claude_command_agent.py` | Create | Enumerate `commands/*.md` and `agents/*.md`, lightweight frontmatter |
| `tools/parsers/claude_install.py` | Modify | Walk active plugin install roots; bare-component discovery |
| `tools/parsers/claude_plugin.py` | Modify | Add install-resolver sibling function (relative paths from CLAUDE_PLUGIN_ROOT) |
| `tools/parsers/__init__.py` | Modify | Register new parsers in REGISTRY for repo-mode rglob |
| `tools/scan.py` | Modify | Verbose output rendering for the expanded component tree |
| `tests/fixtures/installs/full/` | Create | Fixture install with active plugins + bundled components + bare components |
| `tests/fixtures/repos/declared-components/` | Create | Repo fixture exercising `.claude/skills/`, `.claude/commands/`, etc. |
| `tests/test_parsers/test_claude_skill.py` | Create | SKILL.md edge cases |
| `tests/test_parsers/test_hooks_json.py` | Create | Plugin format vs settings format; identity scopes |
| `tests/test_parsers/test_claude_command_agent.py` | Create | commands/agents enumeration |
| `tests/test_parsers/test_claude_install.py` | Modify | Bundled-component walk; bare components; attribution |
| `tests/test_scan.py` | Modify | E2E for both modes against the new fixtures |
| `tests/test_e2e.py` | Modify | End-to-end matching against fixture advisories |
| `docs/adrs/0007-component-inventory-and-host-adapters.md` | Create | Capture the tiered/adapter model and the `claude-skill`/`claude-hook`/`claude-command`/`claude-agent` ecosystem decisions |
| `docs/adrs/INDEX.md` | Modify | Link new ADR |
| `README.md` | Modify | Expand "What gets scanned" to list new parsers; update tiers status |
| `CONTRIBUTING.md` | Modify | Add `claude-skill`, `claude-hook`, `claude-command`, `claude-agent` to recognized ecosystems |
| `docs/plans/README.md` | Modify | Mark 008 active; 007 done after PR-A merges |

---

## Task 1: SKILL.md parser (claude-skill ecosystem)

**Files:**
- Create: `tools/parsers/claude_skill.py`
- Create: `tests/test_parsers/test_claude_skill.py`
- Create: `tests/fixtures/repos/sample-skill/SKILL.md` (and a few edge-case variants)

Per the canonical Agent Skills spec at agentskills.io/specification, SKILL.md frontmatter has six top-level fields: `name` (required, must match parent dir), `description` (required), `license` (optional), `compatibility` (optional), `metadata` (optional map), `allowed-tools` (optional). No top-level `version` field — versioning is by convention in `metadata.version`.

- [ ] **Step 1: Implement parser**

```python
"""Parse SKILL.md frontmatter per agentskills.io spec.

Identity: claude-skill/<name>[@<metadata.version>]. Used for both bare
skills (~/.claude/skills/<name>/SKILL.md) and bundled skills
(<plugin>/skills/<name>/SKILL.md). Bundled skills carry attributed_to;
bare skills don't.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from tools.component_ref import ComponentRef


def parse(
    skill_md_path: Path, attributed_to: Optional[str] = None
) -> list[ComponentRef]:
    try:
        text = skill_md_path.read_text()
    except OSError:
        return []
    frontmatter = _extract_frontmatter(text)
    if frontmatter is None:
        return []
    name = frontmatter.get("name") or skill_md_path.parent.name
    if not isinstance(name, str) or not name:
        return []
    metadata = frontmatter.get("metadata") or {}
    version = None
    if isinstance(metadata, dict):
        version = metadata.get("version")
        if version is not None and not isinstance(version, str):
            version = None
    identity = f"claude-skill/{name}"
    if version:
        identity = f"{identity}@{version}"
    return [
        ComponentRef(
            ecosystem="claude-skill",
            name=name,
            version=version,
            component_identity=identity,
            source_manifest=str(skill_md_path),
            source_locator="$.frontmatter",
            attributed_to=attributed_to,
        )
    ]


def _extract_frontmatter(text: str) -> Optional[dict]:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None
```

- [ ] **Step 2: Tests** — cover: name+description+metadata.version present; no frontmatter (skip); no name field (fall back to dir name); invalid YAML (skip); top-level array (skip); name mismatch with parent dir (still emit, using frontmatter name); `attributed_to` propagation when passed.

- [ ] **Step 3: Run, commit**

```bash
uv run pytest tests/test_parsers/test_claude_skill.py -q
git add tools/parsers/claude_skill.py tests/test_parsers/test_claude_skill.py tests/fixtures/repos/sample-skill/
git commit -m "feat(parsers): claude-skill ecosystem via SKILL.md frontmatter"
```

---

## Task 2: hooks_json parser (claude-hook ecosystem)

**Files:**
- Create: `tools/parsers/hooks_json.py`
- Create: `tests/test_parsers/test_hooks_json.py`

Two input shapes:

- **Plugin format** at `<plugin-root>/hooks/hooks.json`: `{"description": "...", "hooks": {<EventName>: [<entry>, ...]}}`.
- **Settings format** inside `settings.json`: `{"hooks": {<EventName>: [<entry>, ...]}}` (same inner shape, no description wrapper).

Identity per ADR-0006 follow-up rules (will be captured in ADR-0007):

- Plugin-bundled: `claude-hook/<plugin>/<event>/<index>`.
- Settings-scoped: `claude-hook/settings/<scope>/<event>/<index>` (scope ∈ `user`, `project`, `local`).

`source_locator` carries the JSON path (e.g., `$.hooks.PreToolUse[0]`). Hook `type` (command vs. prompt) and the actual command/prompt string go in `extra`, not identity, so two semantically identical hooks at different indexes are distinct and a command edit at the same index keeps identity stable.

- [ ] **Step 1: Implement two entry points**

```python
def parse_plugin_hooks(
    hooks_json_path: Path, plugin_name: str, attributed_to: str
) -> list[ComponentRef]:
    """Walk a plugin's hooks/hooks.json; emit one ref per (event, index)."""

def parse_settings_hooks(
    settings_path: Path, hooks_block: dict, scope: str
) -> list[ComponentRef]:
    """Walk a settings.json's `hooks` key for a specific scope (user/project/local)."""
```

Both share an internal `_walk_events(hooks_block, locator_prefix, identity_prefix, extra_base)` that iterates `{event: [...]}` and emits ComponentRefs.

- [ ] **Step 2: Tests** — plugin format wrapping; settings format without wrapper; multiple events; multiple indexes per event; `type` and `command` captured in `extra`; identity prefix differentiation for plugin vs settings; per-scope identity for settings; malformed JSON / non-dict `hooks` skipped gracefully.

- [ ] **Step 3: Run, commit**

---

## Task 3: claude_command_agent parser

**Files:**
- Create: `tools/parsers/claude_command_agent.py`
- Create: `tests/test_parsers/test_claude_command_agent.py`

Slash commands and subagents are markdown files under a directory; identity = filename basename (without `.md`). Frontmatter optional; if present and has a `name` field, prefer that over the filename.

- Plugin-bundled commands: `<plugin-root>/commands/*.md` → `claude-command/<plugin>/<name>`.
- Plugin-bundled agents: `<plugin-root>/agents/*.md` → `claude-agent/<plugin>/<name>`.
- Repo-declared (no plugin scope): `<repo>/.claude/commands/*.md` → `claude-command/repo/<name>`.
- Repo-declared agents: `<repo>/.claude/agents/*.md` → `claude-agent/repo/<name>`.

No version field exists for commands/agents; matcher fires only on exact identity (name-only matching). Sufficient for V0 inventory.

- [ ] **Step 1: Implement enumerator** — single function `enumerate_dir(dir_path, identity_kind, identity_scope, attributed_to)` that walks `*.md`, opens each, optionally parses frontmatter for a `name` override, emits one ComponentRef per file.

- [ ] **Step 2: Tests** — flat enumeration; frontmatter name override; missing frontmatter falls back to filename; non-md files skipped; empty dir returns empty list.

- [ ] **Step 3: Run, commit**

---

## Task 4: Extend claude_install.py to walk active plugin install roots

**Files:**
- Modify: `tools/parsers/claude_install.py`
- Modify: `tools/parsers/claude_plugin.py`
- Modify: `tests/test_parsers/test_claude_install.py`

For each active plugin, after emitting the plugin's self-identity ref (plan 007), also walk the plugin's installPath:

1. Read `<installPath>/.claude-plugin/plugin.json` to get `name`, custom paths.
2. For each component category, walk both default paths AND custom paths (Codex's "merged not replaced" rule):
   - **MCPs**: `<installPath>/.mcp.json` (default) + any path in `plugin.json.mcpServers` (string-path form) + inline-dict form. Reuse `mcp_json.parse` and `mcp_json.parse_mcp_servers`.
   - **Skills**: `<installPath>/skills/<skill-name>/SKILL.md` (default).
   - **Hooks**: `<installPath>/hooks/hooks.json` (default file) + `plugin.json.hooks` (inline, same inner shape).
   - **Commands**: `<installPath>/commands/*.md` (default).
   - **Agents**: `<installPath>/agents/*.md` (default).
3. All emitted refs get `attributed_to = "claude-plugin/<name>@<version>"`.

The `claude_plugin.py` install-resolver sibling function (`parse_at_install_root`) handles CLAUDE_PLUGIN_ROOT-relative path resolution: relative paths in `plugin.json` resolve from the `installPath`, not from the manifest's parent dir.

- [ ] Tests cover: bundled MCP + bundled skill + bundled hook + bundled command + bundled agent all emit correctly with `attributed_to` set; the same fixture exercising default + custom path merging.

---

## Task 5: Bare-component discovery in fs mode

**Files:**
- Modify: `tools/parsers/claude_install.py`

Three sources, all included in fs mode:

1. **Bare MCPs**:
   - `settings.<scope>.mcpServers` (dict, per scope via `settings_layers.by_scope()`) → emit via `mcp_json.parse_mcp_servers`, `attributed_to=None`, source_manifest = the scope's settings file.
   - Project-scoped `<project-root>/.mcp.json` (when project_root is set).
   - User-scoped `<install-root>/.mcp.json` if it exists at the install root.

2. **Bare hooks** (per scope, via `settings_layers.by_scope()`):
   - For each scope's `hooks` key, parse via `hooks_json.parse_settings_hooks(...)`. Identity reflects scope of origin. No merging — each scope's hooks emit independently.

3. **Bare skills**:
   - Walk `<install_root>/skills/<name>/` for any directory with a SKILL.md → emit `claude-skill/<name>[@version]` with `attributed_to=None`.

- [ ] Tests cover: bare-MCP from settings + project `.mcp.json`; bare-hook scope identity for user/project/local; bare-skill discovery from `~/.claude/skills/`; empty cases.

---

## Task 6: Wire parsers into repo mode

**Files:**
- Modify: `tools/parsers/__init__.py`

The same parsers run in repo mode via `rglob`-based discovery. Extend `REGISTRY` to include:

```python
REGISTRY: list[tuple[str, ParserFn]] = [
    # ... existing entries ...
    (".claude/skills/*/SKILL.md", _wrap_for_repo(claude_skill.parse)),
    (".claude/commands/*.md", _wrap_command_for_repo),
    (".claude/agents/*.md", _wrap_agent_for_repo),
    # Settings hooks are read via the existing .claude/settings.json entry,
    # but now we route the `hooks` key through hooks_json.parse_settings_hooks
    # in addition to existing enabledPlugins handling.
]
```

`_wrap_for_repo` calls the parser with `attributed_to=None` (repo-mode declarations are not "via a plugin"; they're declared by the repo itself).

For commands/agents in repo mode, identity uses the `claude-command/repo/<name>` and `claude-agent/repo/<name>` scopes — distinguishing repo-declared from plugin-bundled. (Plugin-bundled use `<plugin>` scope, set when walked from inside an active install path in fs mode.)

- [ ] Tests cover: a fixture repo with `.claude/skills/<name>/SKILL.md`, `.claude/commands/foo.md`, `.claude/agents/reviewer.md`, and `.claude/settings.json` with `hooks` → all five appear in `openaca scan repo` output.

---

## Task 7: Refactor claude_plugin.py for install-rooted resolution

**Files:**
- Modify: `tools/parsers/claude_plugin.py`

Current `parse(path)` is repo-mode-friendly: reads a single plugin.json file in isolation. Add a sibling function `parse_at_install_root(install_root, attributed_to)` used by `claude_install.py`:

- Reads `<install_root>/.claude-plugin/plugin.json`.
- Resolves relative paths in `plugin.json` (e.g., `mcpServers: "./.mcp.json"`) from `<install_root>`, not from the manifest's parent dir. This is CLAUDE_PLUGIN_ROOT semantics; differs from plan 007's repo-mode resolution (which uses `manifest.parent.parent` because there's no install-root context).
- All emitted refs get `attributed_to` set to the passed value.

`parse(path)` (repo mode) unchanged.

- [ ] Tests cover: install-rooted resolution differs from manifest-rooted resolution when given a fixture where the two would land at different files.

---

## Task 8: Verbose output for the expanded tree

**Files:**
- Modify: `tools/scan.py`

`fs` mode `-v` should show the per-plugin breakdown:

```
loaded N advisor(y/ies) from advisories
detected install_root=/Users/.../.claude (mode=fs, layered: user + project + local)
resolved 5 active plugin(s):
  supabase@0.1.6 (sha: <short>) → 1 bundled MCP, 0 bundled skills, 0 bundled hooks, 2 commands, 0 agents
  superpowers@5.1.0 (sha: <short>) → 0 bundled MCPs, 12 bundled skills, 1 bundled hook, 0 commands, 0 agents
  ...
bare MCPs (1):
  pkg:npm/@example/foo@1.0 (via settings/user)
bare hooks (3):
  claude-hook/settings/user/PreToolUse/0 (command: ...)
  ...
bare skills (5):
  claude-skill/bootstrap-project
  ...
matched 1 finding(s):
  pkg:npm/@supabase/mcp-server@1.0.4 → CVE-2026-XXXX (high) via claude-plugin/supabase@0.1.6
```

`repo` mode `-v` gets the new components listed alongside existing manifest counts:

```
scanned N manifest(s), M component(s):
  package.json — K components
  .claude/skills/foo/SKILL.md — 1 component
  .claude/commands/bar.md — 1 component
  ...
```

---

## Task 9: ADR-0007 and docs

**Files:**
- Create: `docs/adrs/0007-component-inventory-and-host-adapters.md`
- Modify: `docs/adrs/INDEX.md`, `README.md`, `CONTRIBUTING.md`

ADR-0007 captures:

1. **Tiered scanning model** (Tier 1 declarative manifests → Tier 4 runtime attestation; V0 ships Tier 1-2, V1 adds Tier 3 SDK-aware extraction).
2. **Endpoint vs application SCA** framing for the two scan modes.
3. **`claude-skill`, `claude-hook`, `claude-command`, `claude-agent` ecosystem strings** added to the recognized matcher ecosystems.
4. **Plugin-scope vs settings-scope vs repo-scope** identity disambiguation for hooks/commands/agents.
5. **Host-adapter direction for V1**: framework-specific extractors as conventions emerge (OpenAI Agents SDK, Cursor, Windsurf, Codex CLI).

Add the new ecosystems to `CONTRIBUTING.md`'s recognized-ecosystems list.

---

## Task 10: End-to-end test + full gate

**Files:**
- Modify: `tests/test_e2e.py`

Two new E2E scenarios:

1. **repo mode** against a fixture repo declaring `.claude/skills/<name>/SKILL.md`, a project `.mcp.json`, settings-scope hooks, plus an advisory targeting `claude-skill/<name>` → assert finding fires.
2. **fs mode** against the full fixture install (active plugin with bundled MCP + bundled skill + bundled hook + commands + agents, plus bare components) against an advisory targeting a bundled component → assert finding fires with `attributed_to` set.

Full gate:

```bash
uv run pytest -q
uv run ruff format --check tools/ tests/
uv run ruff check tools/ tests/
uv run pyright tools/ tests/
uv run openaca lint advisories/
```

All green required.

---

## Verification

After PR-B merges, dogfood manually:

```bash
# Fresh OpenACA checkout
cd /Users/vinodkone/workspace/openaca
git pull

# fs mode against your actual ~/.claude install
uv run openaca scan fs --target ~/.claude --advisories advisories -v
# Expected: active plugins with bundled-component counts; bare MCPs / hooks / skills;
# no findings unless corpus has matching plugin/component advisories (it doesn't yet in V0).

# repo mode against an OpenACA-aware project (e.g., this repo itself, which has
# `.claude/settings.json` and similar)
uv run openaca scan repo --target . --advisories advisories -v
```

---

## Self-review checklist

- [ ] **Same parsers wired into both modes** — no code duplication between repo and fs entry points. The wrapping decides `attributed_to` and identity scope; the parser logic is identical.
- [ ] **CLAUDE_PLUGIN_ROOT semantics**: relative paths in `plugin.json` resolve from install root in fs mode, from plugin root (`manifest.parent.parent`) in repo mode. Both tested with explicit fixtures.
- [ ] **Hook identity scopes** correctly differentiate: `claude-hook/<plugin>/...`, `claude-hook/settings/<user|project|local>/...`. No merging of hooks across scopes (each scope emits its own components).
- [ ] **Default + custom path merging** for plugin component discovery — both paths walked, not just one.
- [ ] **Attribution invariants**: bundled components carry `attributed_to = "claude-plugin/<name>@<version>"`; bare and repo-declared components carry `attributed_to = None`.
- [ ] **No regressions** in the existing test suite (PR-A's 190 tests, plus new ones from this plan).
- [ ] **ADR-0007** captures the design choices and the V1 boundary (no SDK-aware code extraction, no OpenAI/Cursor/Windsurf adapters yet).
- [ ] **README and CONTRIBUTING** updated to list the new ecosystems and tier-1 surfaces.
