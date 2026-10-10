# Coverage

OpenACA follows a tiered model loosely analogous to traditional SCA's
`lockfile > manifest > source code` hierarchy.

## Agent kinds

OpenACA registers one composition builder per agent kind. Each kind pins a
coverage baseline per source — `resolve_coverage` floors observed coverage at
that baseline regardless of how clean a given scan's parse is:

### What lowers a baseline

`openaca:composition_coverage` qualifies the **component graph**, so exactly one
question sets a kind's baseline: *can this scan deterministically identify the
agent's components?* Components are the closed set — `mcp_server`, `plugin`,
`skill`, `hook`, `agent`, `command`.

Three consequences, applied to every kind alike:

- **Administrative and policy surfaces do not lower it.** Approval rules,
  permission allowlists, and trust settings declare no components. They are
  posture, reported under their own rule ids, and a scan that cannot read one
  still knows the composition.
- **Identity gaps do not lower it.** Not knowing which registry a plugin came
  from costs it a cross-BOM identity, not its place in the graph.
- **A readable surface we do not parse does lower it** — until we parse it. That
  is a real gap in what the scan delivers, and the honest response is to close
  it rather than label it.

A baseline is argued from a named gap at **that** source. Never inherited from
the other source, and never set conservatively because a kind is new: a
`partial` every kind carries by reflex distinguishes nothing.

| Kind | Declared | Installed |
|---|---|---|
| `claude-code` | `complete` | `complete` |
| `cursor` | `partial` | `partial` |
| `codex` | `complete` | `complete` |
| `devin` | `partial` | `partial` |

Cursor is `partial` at both sources, for different reasons per source:
installed composition is blind to plugin enable state (a server-side call),
runtime MCP registration, and the extensibility flag; declared composition is
narrower still — nothing is installed, so there is no plugin cache and no
runtime registration to read — but still hits the extensibility flag and
unparsed instruction surfaces. See
[`docs/specs/cursor-agent-kind.md`](../specs/cursor-agent-kind.md) for the
full evidence-gap detail.

Codex is `complete` at both sources, and got there by closing a gap rather than
relabelling one. Its profile layer (`$CODEX_HOME/<name>.config.toml`, activated
by `codex -p <name>`) genuinely hid MCP servers — verified by running
`codex -p work mcp list` against a fixture root — so OpenACA now composes every
profile it finds. The remaining candidates fail the rule rather than the
evidence: `rules/*.rules` and `[projects.*] trust_level` declare no components
and report under their own posture rule ids; an unregistered marketplace costs a
plugin its cross-BOM identity, not its place in the graph; and runtime MCP
registration has zero references in the audited binary — an earlier draft
asserted it by carrying the claim over from Cursor, where it is real, without
checking it for Codex. `managed_config.toml` remains unaudited and is the one
thing that would reopen this; being a file, the response would be to read it.
See [`docs/specs/codex-agent-kind.md`](../specs/codex-agent-kind.md).

Claude Code stays `complete`, and now earns it: its administrator-distributed
managed settings (`managed-settings.json` plus `managed-settings.d/*.json`) can
declare `mcpServers` and `hooks`, were explicitly not loaded in V0, and are now
composed. Without that, an MDM-managed endpoint's composition was reported as
complete while missing components it genuinely had.

Cursor is `partial`, and that is the rule doing its job: its third-party
extensibility flag lives in an editor state database rather than a file, so a
scan cannot determine whether the `.claude/*` and `.codex/*` skills it reports
are actually loaded. That gap does not close by parsing.

Devin CLI is `partial` at both sources, for different reasons per source
([ADR-0070](../adrs/0070-devin-cli-agent-kind.md)). Installed: managed org and
personal plugins arrive from Devin's service and are visible only where cached,
and an ACP client can register MCP servers that appear in no file. Declared: a
repository cannot see the user's `read_config_from`, so whether the imported
`.claude/*`, `.cursor/*`, `.windsurf/*` and `.github/skills` load is unknown;
a project-level switch that names an import is honoured. See
[`docs/specs/devin-cli-agent-kind.md`](../specs/devin-cli-agent-kind.md).

## Coverage by agent kind

What each kind reads, what posture it reports, and where it stops. Use this to
see what is and is not covered, kind by kind. The per-kind specs remain the
authority for every cell.

Cell markers:

- **†** marks a root Cursor reads only behind its extensibility flag.
- **‡** marks an import Devin CLI reads only while its `read_config_from`
  switch for that tool is on (the default).
- **—** means the kind has no such surface.

### Composition

| | `claude-code` | `codex` | `cursor` | `devin` |
|---|---|---|---|---|
| Config root | `--config-dir`, else `$CLAUDE_CONFIG_DIR`, else `~/.claude` | `--config-dir`, else `$CODEX_HOME`, else `~/.codex` | `~/.cursor` | `$XDG_CONFIG_HOME/devin`, else `~/.config/devin` |
| `--config-dir` | Accepted | Accepted; `.agents` moves with it (ADR-0059) | Refused (ADR-0054) | Refused (ADR-0071) |
| Coverage, declared / installed | `complete` / `complete` | `complete` / `complete` | `partial` / `partial` | `partial` / `partial` |
| What keeps it from `complete` | — | — | Plugin enable state and runtime MCP registration (installed); the extensibility flag (both) | Managed plugins and runtime MCP registration (installed); `read_config_from` (declared) |
| MCP servers | `.mcp.json`, `mcp.json`, `claude_desktop_config.json`, settings `mcpServers`, managed settings | `config.toml` `[mcp_servers]`: user, project, profiles | `.cursor/mcp.json`, `~/.cursor/mcp.json` | `mcp_config.json`: user, project, project-local, merged by name; legacy `config.json` `mcpServers`; Claude Code‡ (`.mcp.json`, settings, `~/.claude.json`, `~/.claude/mcp_servers.json`), Cursor‡ and Windsurf‡ (stable channel) files, each its own occurrence |
| Skills | `.claude/skills/`, plugin skills | `<root>/skills/`, `.codex/skills/`, `.agents/skills/`, `$HOME/.agents/skills/` | `.cursor/skills/`, `.agents/skills/`, `.claude/skills/`†, `.codex/skills/`† | `.devin/skills/`, `.cognition/skills/`, `.agents/skills/`, `<config>/skills/`, `$XDG_CONFIG_HOME/cognition/skills/`, `$HOME/.agents/skills/`; `.claude/skills/`‡, `.windsurf/skills/`‡, `.github/skills/`‡, `~/.codeium/windsurf/skills/`‡, `~/.copilot/skills/`‡ |
| Subagents | `.claude/agents/*.md` | `<root>/agents/*.toml`, `[agents.*] config_file` | `.cursor/agents/`, `.claude/agents/`† | `.devin/agents/`, `.agents/agents/`, `<config>/agents/` |
| Commands | `.claude/commands/*.md` | — | `.cursor/commands/`, `.claude/commands/`† | `.claude/commands/`‡, loaded as skills |
| Plugins | `installed_plugins.json`; `.claude-plugin` | `plugins/cache/`; two manifests | `plugins/local/`, `plugins/cache/`; two manifests, presence-only | Store under `$XDG_DATA_HOME/devin/cli/plugins/`, presence-only from `lock.json`; three manifests in a repo |
| Hooks | settings `hooks`, plugin `hooks/hooks.json`, managed settings | `hooks.json`, `.codex/hooks.json`, plugin hooks | Plugin hooks | `.devin/hooks.v1.json`, config `hooks`, Claude Code settings `hooks`‡, plugin `hooks.json` in a repo |
| Reads another runtime's files | No | `.agents/skills/` only (shared convention) | Claude Code and Codex, behind the extensibility flag | Claude Code, Cursor, Windsurf, Copilot, behind `read_config_from`; OpenCode and Zed deferred |
| Declared evidence includes `.agents/skills/` (ADR-0058) | No | Yes | Yes | Yes, and `.agents/agents/` (ADR-0072) |

### Posture

| Rule (`openaca-posture-…`) | `claude-code` | `codex` | `cursor` | `devin` |
|---|---|---|---|---|
| `insecure-transport` | Yes | Yes | Yes | Yes |
| `mcp-header-credential` | Yes | Yes | Yes | Yes; a path-shaped `${file:…}` reads as a reference |
| `mutable-install-reference` | Yes | Yes | Yes | Yes |
| `skill-executable-tool` | Yes | Yes | Yes | Yes; `exec` counts as executable |
| `mcp-auto-approve` | Yes: `autoApprove` on a server entry | — | Yes: `permissions.json` | Yes: `permissions` `mcp__…` allows, merged across levels (ADR-0073) |
| `command-policy-allow` | — | Yes: `rules/*.rules` | — | Yes: `permissions` `Exec(…)` and `exec` allows, merged across levels (ADR-0073) |
| `project-trust` | — | Yes: `[projects.*] trust_level` | — | — |
| `api-endpoint-override` | Yes | — | — | — |

### Not covered, for any kind

- **Instruction files**: `CLAUDE.md`, `AGENTS.md`, rules directories. They are
  instructions, not components.
- **Components a server delivers at run time and never writes to a file the
  scan reads**: Cursor's team configuration, MCP servers an editor registers
  over a protocol, and Devin's managed plugins where uncached.
- **Windsurf as its own kind.** Its files appear only where a registered kind
  imports them.

## Tiers

| Tier | What it reads | V0 status |
|---|---|---|
| **1. Declarative manifests** (host-specific) | `.claude/settings.json`, `.claude-plugin/plugin.json`, `mcp.json`, `.mcp.json`, `claude_desktop_config.json`, `installed_plugins.json` in endpoint mode, `SKILL.md`, `hooks/hooks.json`, `.claude/commands/*.md`, `.claude/agents/*.md` | V0 |
| **2. Dependency manifests** (universal) | `package.json`, `pyproject.toml`, lockfiles inside active plugins | V0 |
| **3. SDK-aware code extraction** (host-specific SAST-like) | inline SDK configuration such as `query({ mcpServers: ... })` or `Agent(tools=[...])` | V1 |
| **4. Runtime observation** | live tool invocations or runtime attestation | Not implemented in the OSS scanner today |

## Agent-composition scope

OpenACA is not a replacement for general-purpose SCA. Repo-mode dependency
manifests such as `package.json`, `pyproject.toml`, `package-lock.json`, and
`uv.lock` are classified as agent dependencies only when they belong to an
agent component, such as dependencies of a Claude Code plugin.

Direct dependency manifests in ordinary application repos are general software
dependencies. OpenACA suppresses those from advisory queries and output; scan
them with a general-purpose SCA scanner.

## Supported manifests

The table below is Claude Code's manifest set. Cursor is a second, separately
scoped agent kind — see `docs/specs/cursor-agent-kind.md` for the manifests it
reads.

| Manifest | Detects | Identifier emitted |
|---|---|---|
| `package.json` | npm dependencies when they belong to an agent component | `pkg:npm/<name>@<version>` |
| `pyproject.toml` | PEP 621 deps, optional-deps, PEP 735 dependency-groups when they belong to an agent component | `pkg:pypi/<name>@<version>` |
| `mcp.json`, `.mcp.json`, `claude_desktop_config.json` | MCP server launches via `npx`, `uvx`, `python -m`, local binaries, or remote URLs | package match coordinate when available; graph identity plus install context otherwise |
| `.claude-plugin/plugin.json` | Claude Code plugin identity | plugin graph identity |
| `.claude/settings.json` | enabled plugins, direct `mcpServers`, direct hooks per scope | mixed, depending on entry type |
| `installed_plugins.json` | active endpoint plugins with resolved versions and Git SHAs | plugin graph identity plus source metadata when available |
| `SKILL.md` | agent skills under `.claude/skills/*` or plugin `skills/*` | skill graph identity |
| `hooks/hooks.json` and `settings.json.hooks` | hook entries by event and index | hook graph identity |
| `.claude/commands/*.md` and plugin `commands/*.md` | slash commands | command graph identity |
| `.claude/agents/*.md` and plugin `agents/*.md` | subagents | agent graph identity |

Rows such as plugin, skill, hook, command, and subagent identities are graph
occurrence identities. Match coordinates are separate and exist only when the
manifest or lock entry provides a package, Git, or explicit external
coordinate.

## Advisory matching

OpenACA queries OSV.dev for versioned package and Git refs. Network failures
are fail-soft: OpenACA still reports inventory and parse coverage, but
overlay-backed vulnerability matching needs upstream OSV records.

Unpinned references such as `npx pkg@latest` are inventoried but cannot be
matched against version-specific advisories unless OpenACA can resolve an exact
version from lockfiles or install state.

## Current limitations

OpenACA V0 does not yet see:

- inline or programmatic SDK configuration embedded directly in source code;
- local agent-host state for kinds that are not registered, such as Windsurf
  or VS Code agent-mode config;
- vulnerabilities for local-only or source-less components that do not provide
  a package, Git, or external match coordinate;
- live tool invocation behavior or runtime blocking.

The Agent BOM format is pre-1.0. Field names, identities, and CLI output may
change before the first stable schema release.
