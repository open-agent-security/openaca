# Coverage

OpenACA follows a tiered model loosely analogous to traditional SCA's
`lockfile > manifest > source code` hierarchy.

| Tier | What it reads | V0 status |
|---|---|---|
| **1. Declarative manifests** (host-specific) | `.claude/settings.json`, `.claude-plugin/plugin.json`, `mcp.json`, `.mcp.json`, `claude_desktop_config.json`, `installed_plugins.json` in endpoint mode, `SKILL.md`, `hooks/hooks.json`, `.claude/commands/*.md`, `.claude/agents/*.md`; Cursor `mcp.json`, `SKILL.md`, `commands/*.md`, `agents/*.md`, `.cursor-plugin/plugin.json`, and Agent Plugins `plugin.json` in repo mode, plus MCP/Skills/Subagents/Commands/dev-linked-and-marketplace-cached-Plugin-presence in endpoint mode | V0 |
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
| `.claude/agents/*.md`, `.cursor/agents/*.md`, and plugin `agents/*.md` | subagents, including Cursor's precedence-aware compatibility read of `.claude/agents/*.md` (a same-relative-path `.cursor/agents/` file overrides it) | agent graph identity |
| `.cursor/mcp.json` (repo mode) | Cursor MCP server launches, same command shapes as `mcp.json` | package match coordinate when available; graph identity plus install context otherwise |
| `.cursor/skills/*/SKILL.md` (repo mode) | Cursor agent skills | skill graph identity |
| `.cursor/commands/**/*.md` (repo mode, project-level only) | Cursor slash commands | command graph identity |
| `.cursor-plugin/plugin.json` (repo mode) | Cursor Plugin identity (native format) | plugin graph identity |
| `plugin.json` with an Agent Plugins `$schema` (repo mode) | Agent Plugins open-standard bundles, scoped to their portable `skills/` and `mcp.json` contract | plugin graph identity plus nested skill/MCP graph identity |

Rows such as plugin, skill, hook, command, and subagent identities are graph
occurrence identities. Match coordinates are separate and exist only when the
manifest or lock entry provides a package, Git, or explicit external
coordinate.

`openaca scan repo` discovers Cursor MCP servers, Skills, Plugins (both the
native `.cursor-plugin` format and the Agent Plugins open standard),
Subagents, and Commands alongside Claude Code's, tagged with
`runtime_hosts: ["cursor"]` and attributed correctly in posture and
vulnerability findings. `openaca scan endpoint`, `openaca bom endpoint`, and
`openaca remote sync endpoint` cover the same Cursor surfaces installed on
this machine, including both dev-linked (`plugins/local/`) and
marketplace-cached (`plugins/cache/<marketplace>/<name>/<version>/`,
presence-only) Plugins, with one named exception: Plugin enabled/disabled
state is not observable and is never reported, per
[ADR-0045](../adrs/0045-cursor-host.md) Decision #7 and
[ADR-0045 Decision #7](../adrs/0045-cursor-host.md). Use
`--host claude-code`, `--host cursor`, or both (comma-separated or repeated)
to scope a scan to specific hosts; see the [CLI Reference](cli.md).

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
- non-Claude agent-host *local machine* state such as Codex CLI endpoint
  installs (Windsurf and VS Code agent-mode config are also not yet covered).
  Cursor endpoint installs are covered — MCP servers, Skills, Subagents,
  Commands, and dev-linked and marketplace-cached Plugin presence — with one
  named exception: Plugin enabled/active state is not observable and is
  never reported, per [ADR-0045](../adrs/0045-cursor-host.md) Decision #7
  and [ADR-0045 Decision #7](../adrs/0045-cursor-host.md);
- vulnerabilities for local-only or source-less components that do not provide
  a package, Git, or external match coordinate;
- live tool invocation behavior or runtime blocking.

The Agent BOM format is pre-1.0. Field names, identities, and CLI output may
change before the first stable schema release.
