---
id: 0007
title: Component inventory ecosystems; tiered scanning; identity scopes
status: accepted
date: 2026-05-10
supersedes: null
superseded-by: null
---

## Context

Plan 007 (ADR-0006) established the CLI shape and the `claude-plugin`
ecosystem for advisory matching. It emitted one component per active
plugin but stopped there. Plan 008 expands the inventory to the full
Tier-1 declarative agent stack: MCPs, skills, hooks, commands, and
agents. That adds four new ecosystems and forces design decisions about
how identity scopes (plugin-bundled vs settings-scoped vs repo-declared)
flow through the matcher.

Doing this now matters because: (a) authors need stable ecosystem
strings to file advisories against, and changing them later breaks every
cached corpus consumer; (b) the same parsers run in both `repo` and
`endpoint` modes, so the wrapping decisions ("who declared this?") have to be
consistent across modes from day one; (c) the framing of endpoint vs
application SCA needs to be captured durably so it doesn't keep getting
re-litigated against new parser additions.

## Decision

### 1. Tiered scanning model

OpenACA's surface scope is split into four tiers; V0 ships Tier 1 + 2.

| Tier | Surface | Ships |
|---|---|---|
| 1 | Declarative manifests (plugin.json, settings.json, SKILL.md, hooks.json, command/agent .md, .mcp.json) | V0 |
| 2 | Dependency manifests inside plugins (package-lock.json, uv.lock, package.json, pyproject.toml) | V0 plan 009 |
| 3 | SDK-aware code extraction (`query({mcpServers})`, `Agent(tools=[...])`) | V1 |
| 4 | Runtime attestation (live process state, eBPF, hooked process tree) | Out of scope |

This split is the load-bearing distinction for what `repo` mode can
honestly claim to scan. It IS honest about programmatic
SDK-configuration being invisible until Tier 3 lands.

### 2. Endpoint SCA vs application SCA framing

- **`repo` mode = application SCA**: "What will this app ship with?" Runs
  against committed config in the repo. The Tier-3 gap is real and
  honest: programmatic SDK config is invisible.
- **`endpoint` mode = endpoint agent-stack SCA**: "What's installed and active
  on this machine?" Lockfile-rooted; orphaned cache versions don't count.

Both modes use the same parsers and emit the same ecosystems. The
*difference* is what triggers the walk (file rglob vs resolved
`installed_plugins.json`) and the default `attributed_to` shape.

### 3. New ecosystem strings

Recognized in `affected[*].package.ecosystem`:

| Ecosystem | Range matching | Identity shape |
|---|---|---|
| `claude-skill` | semver via `metadata.version` (string only) | `claude-skill/<name>[@<version>]` |
| `claude-hook` | none (V0); identity-only | `claude-hook/<scope>/<event>/<index>` |
| `claude-command` | none (V0); identity-only | `claude-command/<owner>/<name>` |
| `claude-agent` | none (V0); identity-only | `claude-agent/<owner>/<name>` |

`claude-skill` follows the existing `_match_versioned` path (range-based
matching, identical to npm/PyPI). The other three are identity-only:
matcher fires on exact `component_identity` match against
`database_specific.openaca.component_identity` in the advisory. No range
algebra — these surfaces don't have semantic versioning conventions in
V0.

### 4. Identity-scope disambiguation

For surfaces that can be declared in multiple places, the identity
prefix distinguishes them so the same logical component at different
locations produces distinct inventory items (and findings):

- **Plugin-bundled**: `<eco>/<plugin-name>/<rest>` —
  `attributed_to = "claude-plugin/<plugin>@<version>"`.
- **Settings-scoped (hooks/MCPs)**:
  `claude-hook/settings/<scope>/<event>/<index>` where
  `scope ∈ {user, project, local}`. `attributed_to = None`.
- **Repo-declared (commands/agents/skills in `.claude/`)**:
  `<eco>/repo/<name>`. `attributed_to = None`.

Hooks specifically are NOT merged across scopes. The same logical hook
at user + project + local emits three distinct inventory components.
The rationale: hooks at different scopes have different blast radii
(machine-local vs CI-relevant), and merging would hide that distinction.

### 5. Mode-specific scope filtering

`repo` mode excludes `local` scope for both bare-component walks and
multi-entry install-entry selection. `settings.local.json` is
machine-local and not CI-relevant; including it would produce findings
that depend on the developer's machine state.

### 6. Path resolution: CLAUDE_PLUGIN_ROOT semantics

Relative paths inside `plugin.json` (notably `mcpServers: "./.mcp.json"`)
resolve from the plugin root, not from `plugin.json`'s parent directory.
In repo mode, the plugin root is `manifest.parent.parent` (since the
manifest lives at `<plugin-root>/.claude-plugin/plugin.json`). In endpoint
mode, the plugin root is the active `installPath`. Both modes reject
absolute paths and `..`-traversal that escapes the root.

### 7. V1: host adapters

OpenACA's parsers are biased toward declarative agent stacks
(Claude Code, Claude Desktop, Cursor's MCP config). Frameworks that
configure programmatically (OpenAI Agents SDK, Codex CLI's TOML for
non-MCP servers, runtime tool registration) are deferred to V1 host
adapters as their conventions stabilize. The Tier-3 SDK-aware extraction
is the gate — once we can find configurable surfaces in source via AST,
the host-adapter direction follows naturally.

## Alternatives considered

- **Single `claude-component` ecosystem**: lump skills/hooks/commands/agents into one
  ecosystem and discriminate via identity prefix. Rejected — collapses
  range-matching into identity-matching for `claude-skill` (which has a
  conventional version), and confuses corpus authors who'd see one
  ecosystem with wildly different identity shapes.

- **Cross-scope hook merging**: merge hooks across scopes the way
  `enabledPlugins` is merged. Rejected — hooks at different scopes have
  different blast radii, and merging hides scope-of-origin (which is
  load-bearing for "is this CI-relevant or machine-local?").

- **Repo-mode walks plugin install paths via rglob**: have `repo` mode
  read `installed_plugins.json` if it exists in the target. Rejected —
  `repo` mode's contract is "what's committed in this repo," and an
  installed_plugins.json in a repo would be a configuration mistake
  (the file is machine-state, not source). Mixing the two confuses what
  each mode is doing.

- **Manifest registry doing directory enumeration**: have the registry
  call `enumerate_dir` for `.claude/commands/` and `.claude/agents/`
  directories. Rejected — the registry is built around per-file parsers
  fed by `rglob`. Per-file `parse_file` emitter on
  `claude_command_agent` matches the existing pattern and avoids two
  walking styles in the same registry.

## Consequences

**Enables:**

- Advisory authors get four new ecosystem strings to target. Identity-only
  matching for hooks/commands/agents (V0) is honest about the lack of
  semantic versioning conventions for these surfaces.
- Endpoint vs application framing is documented; new parsers slot into
  whichever mode (or both) without re-litigating scope.
- Plan 009 (lockfile transitive scanning) can build on this without
  touching the inventory layer; attribution data already flows.

**Costs:**

- Three new identity-shape conventions (plugin-bundled / settings-scoped /
  repo-declared) to remember. Mitigated by the consistent `<eco>/<owner>/<name>` pattern.
- Hook identity is now `claude-hook/<owner>/<event>/<index>`. Index
  shifting (rare — config edits at the same slot) is acknowledged: the
  unit of identity is the *slot*, not the *command*, so config edits
  at the same index keep identity stable.

**Watch:**

- If real plugins start using `metadata.version` heavily for non-skill
  surfaces, we may add version-range matching to commands/agents in V1.
- If hook scope merging ever becomes the dominant pattern (it isn't
  today), the no-merge decision needs revisiting.

## When to revisit

- A real ecosystem emerges with semantic versioning for hooks/commands/agents
  (then range matching needs to be added to those ecosystems).
- Codex CLI's TOML conventions or OpenAI Agents SDK config stabilize
  enough to write declarative parsers for — the host-adapter direction
  becomes concrete.
- We hit a Tier-3 (SDK-aware) advisory that we can't represent in the
  current schema; that's the trigger for V1 host adapters.
