# Devin CLI Agent Kind — Surface Audit

*Implemented (2026-10-09): `devin-cli` is registered. Decisions:
[ADR-0070](../adrs/0070-devin-cli-agent-kind.md),
[ADR-0071](../adrs/0071-devin-cli-refuses-config-dir.md),
[ADR-0072](../adrs/0072-shared-agents-agents-directory.md),
[ADR-0073](../adrs/0073-devin-permissions-two-rule-ids.md). Audited 2026-10-08.
This is the per-kind audit that [Multi-Agent Support](multi-agent-support.md)
requires.*

**Devin CLI is Claude Code-shaped in its formats and Cursor-shaped in its
reach.** Its own files use formats OpenACA already parses: the `mcpServers` map,
`SKILL.md`, Claude Code's hook envelope and PascalCase event names, and Claude
Code's and Agent Plugins' plugin manifests. By default it also imports MCP
servers, skills and hooks from six other runtimes. Most of the kind is reuse.
Most of its risk is attribution: what is Devin's *evidence* and what is only its
*composition*.

## At a glance

| | |
|---|---|
| Kind, root node | `devin-cli`, `agent:devin-cli` |
| Front ends | `devin` REPL and `devin -p`; Devin Local, Devin Desktop's default agent, over ACP; ACP editors (JetBrains, Zed, Xcode) via `devin acp` |
| Config root | `$XDG_CONFIG_HOME/devin`, else `~/.config/devin` |
| Data root | `$XDG_DATA_HOME/devin/cli`, else `~/.local/share/devin/cli`; holds the plugin store |
| Imports | Claude Code, Cursor, Windsurf, GitHub Copilot, OpenCode, Zed; all on by default, each gated by `read_config_from` |
| `--config-dir` | Refused, with `root_override_refusal`, as Cursor's is under ADR-0054 |
| Coverage, declared / installed | `partial` / `partial` (ADR-0046) |
| Component types | The existing closed set. No new type, no new ecosystem, no taxonomy ADR |
| Changes to existing code | `hooks.v1.json` whole-file envelope; `.devin-plugin` manifest; JSONC loader for `config.json`; `permissions` split across two rules; `${file:…}` in `_REFERENCE`; `exec` in `EXECUTABLE_TOOLS`; `active_in` per kind; foreign-root labels in the normalizer |
| Audited build | `devin` 3000.11.3, Linux, 2026-10-08 |

The kind-spec contract
([Multi-Agent Support § What a kind spec must contain](multi-agent-support.md#what-a-kind-spec-must-contain)):

| Requirement | Met in |
|---|---|
| 1. Every path the runtime reads, including another runtime's | [Surfaces](#surfaces), [Files another runtime owns](#files-another-runtime-owns) |
| 2. An observability gap per surface | [Coverage](#coverage) |
| 3. A composition source per claim | The Source column of [Where each surface loads from](#where-each-surface-loads-from) |
| 4. A split verdict for anything deferred | [Deferred and out of scope](#deferred-and-out-of-scope) |

## What is and is not this kind

The three front ends are one kind under ADR-0044's test (same surface, same
schema): one config root, one data root. Devin Desktop's docs call Devin Local
"the Devin CLI agent harness", and a probe of `devin acp` read the REPL's config
root and wrote its data root.

| Shares the name, not this kind | Why |
|---|---|
| Devin Cloud | Runs on remote VMs; its MCP servers and plugins are configured server-side. No local config root to audit |
| Cascade, Devin Desktop's legacy agent | Reads Windsurf's tree, `~/.codeium/windsurf/`. That is a Windsurf kind, which `CLAUDE.md` schedules for V1 |
| Devin Desktop as an editor | Its settings live under the editor's user-data directory and declare no agent components |

## Evidence

Each claim started in Devin's documentation and was checked against the shipped
implementation:

| Source | What was done |
|---|---|
| The binary | `devin` 3000.11.3, `x86_64-unknown-linux`, commit `9c803229faa4`, downloaded from the vendor's install manifest with its published SHA-256 verified. It retains string literals and its embedded documentation (`share/devin/docs/`), the docs that version ships with |
| The CLI's own path reports | `devin skills paths`, `devin rules paths` and `devin mcp list`, run in an isolated `$HOME`, with and without `$XDG_CONFIG_HOME` |
| An ACP probe | `devin acp` driven with `initialize` and `session/new` against three test MCP servers. No account needed, no model call made |

- Implementation-derived claims hold at 3000.11.3. They are not a contract
  Devin owes us; re-audit on a major release.
- None of it is reproducible from this repository: the evidence is a shipped
  binary, not a fixture.
- Linux only. The docs give Windows paths under `%APPDATA%\devin\` and macOS
  paths identical to Linux; neither was checked.

### Reference counts

Literal counts in the audited binary:

| Literal | Refs | Reading |
|---|---|---|
| `mcp_config.json` | 7 | primary MCP surface |
| `mcp_config.local.json` | 5 | gitignored project MCP layer |
| `config.local.json` | 15 | gitignored project config layer |
| `hooks.v1.json` | 4 | standalone hooks file |
| `.devin-plugin` | 5 | own plugin manifest |
| `.claude-plugin` | 5 | Claude Code's manifest is read |
| `plugin.json` | 11 | includes the Agent Plugins root manifest |
| `SKILL.md` | 9 | skills |
| `AGENT.md` | 2 | directory-form subagents |
| `read_config_from` | 3 | the import toggles |
| `.claude/settings` | 4 | Claude Code settings are read |
| `.claude.json` | 2 | Claude Code user config is read |
| `.cursor/mcp.json` | 1 | Cursor MCP is read |
| `.codeium` | 19 | Windsurf user tree is read |
| `opencode.json` | 2 | OpenCode MCP is read |
| `context_servers` | 1 | Zed MCP is read |
| `XDG_CONFIG_HOME` | 1 | config root is relocatable |
| `installed_plugins.json` | **0** | Claude Code's install lockfile, not read *(docs agree)* |
| `.agents/skills` | **0** | **read nonetheless**: `devin skills paths` prints it |
| `.devin/agents` | **0** | **read nonetheless**: documented, and `AGENT.md` is present |
| `.claude/skills` | **0** | **read nonetheless**: documented import |

**A zero is the weak evidence ADR-0058 warns about.** Three of the four zeros
are surfaces the program demonstrably reads; it builds those paths from
components. Every surface claim here rests on the CLI's own path report or its
bundled docs, never on a zero.

## Robustness bar

**A correct inventory for an ordinary installation**, the bar the Codex spec
states. Hold this kind to it.

| Worth blocking on | Deliberately out of scope for now |
|---|---|
| A surface an ordinary installation has going uninventoried, including an imported Claude Code or Cursor surface Devin loads by default | Exhaustive validation of malformed configuration. A malformed surface lowers `composition_coverage` and says so |
| A component reported that Devin would not load, or attributed to the wrong agent. The imports make this the likeliest failure | The combinatorial tail of layer × import toggle × symlink × gitignore |
| A wrong `bom-ref`, identity or coverage verdict | Surfaces an ordinary installation does not have, listed in [Deferred and out of scope](#deferred-and-out-of-scope) with what skipping each costs |
| A host path leaking across the upload boundary | |

## Roots

| Root | Resolves to | Moves with `$XDG_CONFIG_HOME` |
|---|---|---|
| Config | `$XDG_CONFIG_HOME/devin`, else `~/.config/devin` | Yes. Verified: with it set, `devin skills paths` and `devin mcp list` read nothing under `~/.config/devin` |
| Data, holding the plugin store | `$XDG_DATA_HOME/devin/cli`, else `~/.local/share/devin/cli` | No; `$XDG_DATA_HOME` is a separate variable |
| `$HOME/.agents/skills` | Home | No. Verified: still printed with `$XDG_CONFIG_HOME` pointing elsewhere |
| Imported runtimes' home trees | `~/.claude*`, `~/.codeium`, `~/.copilot`, `~/.config/opencode`, `~/.config/zed` | No |
| Legacy skills | `$XDG_CONFIG_HOME/cognition/skills/`, printed by `devin skills paths`. The binary renamed `cognition/` to `devin/` and left symlinks at the old paths | Yes |
| Legacy MCP servers | The `mcpServers` key of `config.json`. Releases before 3000.3 used it; newer ones migrate it to `mcp_config.json` on startup | Yes |

### `--config-dir` is refused

`--kind devin-cli` selects the kind. `--config-dir` is refused with
`root_override_refusal`, as Cursor's is under ADR-0054.

Codex keeps the override (ADR-0059) by moving its one home-derived companion,
`.agents/skills`, to `<dir>/../.agents`. That is faithful because `.codex` and
`.agents` are siblings on a real endpoint. Devin composes from four independent
groups: the config root, the data root (a different variable),
`$HOME/.agents/skills`, and five other runtimes' home trees. No single directory
names all four. A sibling rule would have to invent where `~/.local/share` sits
relative to `~/.config`, and where `~/.claude` and `~/.codeium` sit relative to
both.

For Devin, as for Cursor, **home is an ingredient, not a default.** A named root
would yield a composition stitched from two homes, indistinguishable from a
correct one. That is the failure ADR-0054 refuses.

## Surfaces

Notation:

- `<config>` and `<data>` are the [roots](#roots).
- **Project roots** are the working directory and its ancestors up to the
  repository root, the first directory holding `.git` or `.jj`; a nested
  `.devin/` takes precedence over an ancestor's (bundled
  `reference/configuration/global-vs-local.mdx`). An installed scan reads
  Devin's own `.devin/`, `.cognition/skills` and `.agents/` surfaces in every
  layer from `--project` up to that root; imports are read from `--project`
  only. With no marker above it, `--project` is the only layer.
- **‡** marks an import gated by `read_config_from`.

The inclusion bar is *would omitting this make the inventory wrong on an ordinary
installation*, not *is it reachable*.

### Where each surface loads from

| Surface | Project roots | User roots | Traversal | Accepts | Source |
|---|---|---|---|---|---|
| MCP servers | `.devin/mcp_config.json`, `.devin/mcp_config.local.json` | `<config>/mcp_config.json` | file | `mcpServers` map: `command`/`args`/`env`, or `url` with `transport` (`http`, `sse`) and `headers`; `disabled` | both |
| MCP servers, legacy | `mcpServers` in `.devin/config.json`, `.devin/config.local.json` | `mcpServers` in `<config>/config.json` | file | same | both |
| MCP servers, imported | `.mcp.json`‡, `.claude/settings*.json`‡, `.cursor/mcp.json`‡ | `~/.claude.json`‡, `~/.claude/settings*.json`‡, `~/.claude/mcp_servers.json`‡, `~/.codeium/<channel>/mcp_config.json`‡ | file | each owner's shape, all `mcpServers` | both |
| Skills | `.devin/skills/`, `.cognition/skills/`, `.agents/skills/`, `.windsurf/skills/`‡, `.claude/skills/`‡, `.github/skills/`‡ | `<config>/skills/`, `$XDG_CONFIG_HOME/cognition/skills/`, `$HOME/.agents/skills/`, `~/.codeium/<channel>/skills/`‡, `~/.copilot/skills/`‡ | per directory | `SKILL.md` | both |
| Commands, as skills | `.claude/commands/**/*.md`‡ | — | recursive | `.md` | both |
| Subagents | `.devin/agents/`, `.agents/agents/` | `<config>/agents/` | flat file or one directory deep | `<name>.md`, or `<name>/AGENT.md` (then `AGENTS.md`, `agent.md`, `agents.md`) | both |
| Plugins | — | `<data>/plugins/` | per bundle | three manifest formats ([Plugins](#plugins)) | installed |
| Plugin contents | — | inside each bundle | per bundle | `skills/`, `agents/`, `hooks.json`, `.mcp.json` | installed |
| Hooks | `.devin/hooks.v1.json`; `hooks` in `.devin/config.json`, `.devin/config.local.json`, `.claude/settings.json`, `.claude/settings.local.json` | `hooks` in `<config>/config.json`, `~/.claude.json`, `~/.claude/settings.json`, `~/.claude/settings.local.json` | file | Claude Code's envelope; in `hooks.v1.json` the envelope is the whole file | both |
| Approval policy | `permissions` in `.devin/config.json`, `.devin/config.local.json` | `permissions` in `<config>/config.json` | merged across levels | `allow`/`deny`/`ask` rules: `Exec(…)`, `Read(…)`, `Write(…)`, `Fetch(…)`, `mcp__<server>__<tool>` | both (scan only) |
| Import switches | `read_config_from` in `.devin/config.json` | `read_config_from` in `<config>/config.json` | file | booleans per tool | both (scan only) |

"Per directory" for skills means `<root>/<name>/SKILL.md`, one level deep, for
Devin's own roots, the shared `.agents/skills` and Windsurf's. Devin's import
reference documents Claude Code's and Copilot's skill imports as
`**/SKILL.md`, so those two are walked recursively.

### Not there

| A reader expects | Why it is absent |
|---|---|
| `.devin/commands` | Devin has no commands surface of its own; Claude Code's commands load as skills |
| `.claude/agents` | The Claude Code import names rules, skills, commands and MCP servers, not subagents |
| `~/.claude/plugins/installed_plugins.json` | Zero references, and the docs agree. Devin reads Claude Code's plugin *manifest* format, not its install state |

### Precedence

- **MCP servers merge by name; the higher layer wins:** team settings
  (server-side policy) › `.devin/mcp_config.local.json` ›
  `.devin/mcp_config.json` › `<config>/mcp_config.json`.
- **An imported server against a same-named native one:** the order is stated
  nowhere and is unverified. A collector must not assume one; it records both
  occurrences, each with its own `source_manifest`.
- **Permission lists merge across levels**; a deny at any level wins over an
  allow at any level.

### Hook events

PascalCase, sharing Claude Code's names: `SessionStart` · `SessionEnd` ·
`PreToolUse` · `PostToolUse` · `PermissionRequest` · `UserPromptSubmit` ·
`Stop` · `PostCompaction`.

`PostCompaction` has no Claude Code counterpart; `PermissionRequest` matches
Codex's. `hooks_json._walk_events` iterates whatever keys exist, so the one
parser change is `.devin/hooks.v1.json`, whose envelope is the whole file rather
than a `hooks` key.

### Plugins

| | |
|---|---|
| Manifest candidates, in order | 1. `.devin-plugin/plugin.json`, new. 2. `.claude-plugin/plugin.json`, whose root `.mcp.json` is honoured. 3. A root `plugin.json` per Agent Plugins 1.0.0; a manifest naming another Agent Plugins version is read best-effort under 1.0.0's rules ("an unrecognized `$schema` version is warned about and the plugin still loads"), while one naming another tool's schema is no plugin. OpenACA already realizes 2 and 3 for Claude Code and Cursor. Devin falls back to 2 only "if there's no `.devin-plugin/plugin.json`": a present but invalid Devin manifest leaves the root with no plugin, and is recorded as a gap and a failed source unit |
| Agent Plugins MCP | Root `.mcp.json`, then root `mcp.json`; `.mcp.json` wins a server-name collision |
| Plugin agents | Devin's custom subagent format (`name`, `description`, `model`, `allowed-tools`/`tools`, `max-nesting`): an agent contributes itself only, never MCP servers or hooks from its frontmatter |
| Manifest `skills` | A path or a list of paths, replacing the default `skills/`; `[]` disables skills. "An invalid entry fails the whole manifest": an absolute, `~` or `..` entry means the `.devin-plugin` manifest does not qualify and the plugin is not realized |
| Manifest `mcpServers` | A file, a list of files read in order, `{"paths": [...], "exclusive": true}`, or an inline map. The root `.mcp.json` is read after declared files unless `exclusive` or a non-empty inline map suppresses it; an empty list or map does not. Unsafe paths are dropped; a field of any other shape disables only MCP. The first source to name a server wins (bundled `extensibility/plugins/overview.mdx`), a `"disabled": true` entry included: Devin skips a disabled server at discovery, after names merge, as the binary shows between config levels |
| Manifest `requiredPlugins` | Each entry is a plugin Devin installs "recursively when the plugin is installed", inventoried by its source: a string (`owner/repo[#path]`, a git URL or a local path), or `github` (`repo`), `url`, `git-subdir` (`url` and `path`), `local` (`path`) or `account-upload` (`bundleId`), the kinds the binary accepts. A GitHub source, in any of the forms Devin gives one identity (`owner/repo`, HTTPS with or without `.git`, `ssh://`, `git@github.com:`), takes the identity every GitHub-sourced component has: `owner/repo`, a commit `sha` as its version, another pin as `git_ref`, a subdirectory as `source_subdirectory`; any other source keeps its pin as `git_ref`. An entry with no usable source is a gap. `optionalPlugins` and `forbiddenPlugins` install nothing |
| Install level | User only |
| Store | `<data>/plugins/`. The binary references `lock.json`, `discovered.json` and a `cache` directory; the probe's `lock.json` has `requirements`, `resolved` and `edges` arrays |
| Bundle layout inside the store | **Unverified.** `devin plugins install` and `devin plugins list` refuse to run without a signed-in account |
| Org and personal plugins | Delivered by Devin's service. The binary names "managed org plugin discovery", "managed personal plugin discovery" and a "managed plugins manifest cache". The scan sees whatever that cache holds; [Coverage](#coverage) records the gap |

## Files another runtime owns

Kind-spec requirement 1. Devin's reach exceeds Cursor's. Every import is on by
default. Its switch is a `read_config_from` boolean (`agents_standard`,
`cursor`, `windsurf`, `claude`, `copilot`, `opencode`, `zed`) in the user config
and the project config.

| Path | Owner | Read by Devin as | Gated |
|---|---|---|---|
| `.mcp.json` | Claude Code | MCP servers | ‡ `claude` |
| `.claude/settings.json`, `.claude/settings.local.json` | Claude Code | MCP servers, hooks | ‡ `claude`, hooks included |
| `~/.claude.json`, `~/.claude/settings*.json`, `~/.claude/mcp_servers.json` | Claude Code | MCP servers, hooks | ‡ `claude`, hooks included |
| `.claude/skills/**/SKILL.md` | Claude Code | Skills | ‡ `claude` |
| `.claude/commands/**/*.md` | Claude Code | Skills | ‡ `claude` |
| `.claude-plugin/plugin.json` | Claude Code | Plugin manifest, second candidate | No |
| `.cursor/mcp.json` | Cursor | MCP servers | ‡ `cursor` |
| `.windsurf/skills/`, `~/.codeium/<channel>/skills/` | Windsurf | Skills | ‡ `windsurf` |
| `~/.codeium/<channel>/mcp_config.json` | Windsurf | MCP servers | ‡ `windsurf` |
| `.github/skills/`, `~/.copilot/skills/` (or `$COPILOT_HOME/skills/`) | GitHub Copilot | Skills | ‡ `copilot` |
| `opencode.json`, `~/.config/opencode/opencode.json` | OpenCode | MCP servers | ‡ `opencode` (deferred) |
| `.zed/settings.json`, `~/.config/zed/settings.json` | Zed | MCP servers | ‡ `zed` (deferred) |
| `.agents/skills/` (project and `$HOME`) | Cross-tool | Skills | No |
| `.agents/agents/` | Cross-tool | Subagents | No |

The hooks gate is settled by Devin's own hooks reference: "Hooks from
`.claude/` paths are loaded when `read_config_from.claude` is enabled". An
earlier draft marked it unverified.

### Evidence and composition

- **Cross-reads are composition, never evidence.** A tree holding only
  `.claude/skills/` declares a Claude Code agent; one holding only
  `.cursor/mcp.json` declares a Cursor agent.
- **`.agents/` is the exception (ADR-0058):** a shared convention directory is
  evidence for every kind that reads it. After this kind registers, a repository
  holding only `.agents/skills/` declares Cursor, Codex and Devin CLI agents:
  three BOMs. That is ADR-0058's stated cost, applied to a third reader.
- **`.agents/agents/` is evidence for Devin CLI alone.** Cursor's spec records
  that `.agents/` holds only skills for Cursor, and Codex declares no such root.
- **`.devin/mcp_config.local.json` and `.devin/config.local.json` are
  composition, not evidence.** They are gitignored by design, so their presence
  in a tree is incidental.
- **A bare `.mcp.json`** stays evidence for no kind, as today.

**Devin CLI declared evidence**, each pattern also matched below any directory
(`*/…`):

- `.devin/mcp_config.json`, `.devin/config.json`, `.devin/hooks.v1.json`
- `.devin/skills/*/SKILL.md`, `.cognition/skills/*/SKILL.md`
- `.devin/agents/*`
- `.devin-plugin/plugin.json`
- `.agents/skills/*/SKILL.md`, `.agents/agents/*`

### Node keys

| Mode | Requirement |
|---|---|
| Declared | None new. A shared file's node key is byte-identical in each graph, because the normalizer is scan-root-relative (ADR-0045) |
| Installed | The normalizer must learn each foreign root's label: `~/.claude`, `~/.codeium`, `~/.copilot`, `~/.config/opencode`, `~/.config/zed`. Otherwise the path falls through to an absolute one and leaks a home directory into a `bom-ref`. Cursor's foreign roots already impose this |

## Coverage

`partial` at both composition sources (ADR-0046), for different reasons.

| Gap | Affects | Source | Closable by parsing? |
|---|---|---|---|
| Managed org and personal plugins | plugins and their contents | installed | **Partly.** A cache exists, but a server call fills it and its layout is unverified |
| MCP servers an ACP client passes in `session/new` | MCP servers | installed | **No.** Runtime registration: the probe connected a server that appears in no file |
| Team-settings MCP allowlists and registries | which servers load | installed | **No.** Server-side policy. It restricts and does not add, so under the baseline rule it is posture, not composition |
| `read_config_from` in the user config | every import | declared | **No.** A repository scan cannot see the user's switches, so whether imported `.claude/*`, `.cursor/*`, `.windsurf/*` and `.github/skills` load is unknown. Cursor's extensibility flag is the same kind of gap |
| OpenCode and Zed imports | MCP servers | both | **Yes**; deferred |

- **Installed is `partial`** because of the first two rows. Parsing more files
  closes neither.
- **Declared is `partial`** because of `read_config_from`. On an installed
  endpoint the switches are readable, so a correct installed scan honours them
  and the gap closes there.

## Identity

- **Every surface maps into the closed set**: `mcp_server`, `plugin`, `skill`,
  `hook`, `command`, `agent`. No new component type and no new source ecosystem,
  so no taxonomy ADR blocks this kind.
- **A store-installed plugin** sets `extra["marketplace"]` only if the store
  records the source it was resolved from, which is unverified until the store
  layout is. Until then it takes the occurrence-local `plugin/{name}` form, with
  no cross-BOM identity. For a self-declared name that is the correct answer,
  for the reason the Cursor spec gives.
- **Claude Code commands** load as skills but keep the `command` type, as in the
  Claude Code BOM. Their bytes are Claude Code's, and the type describes the
  file, not how a consumer invokes it.

## Posture

Posture rules read composed components, as for every kind. The documented
exception is a posture-only surface that declares no components; for Devin that
is `permissions` and `read_config_from`, read directly.

| Rule | Applies | Why |
|---|---|---|
| `insecure_transport` | **Yes** | `mcp_config.json` carries `url`; an `http://` remote server is the same exposure |
| `mcp_header_credential` | **Yes, with one change** | Devin expands `${env:VAR}` and `${file:/path}` in `headers`. `_REFERENCE` recognises `${env:…}` and `${input:…}` but not `${file:…}`, so a file reference reads as a literal credential. Add `file:` with a path-shaped body to the reference pattern |
| `mutable_install` | **Yes** | The MCP branch keys on launch specs. Whether the plugin branch fires depends on the store recording a pinned revision, which is unverified |
| `skill_capability` | **Yes, with one change** | Same `SKILL.md` with `allowed-tools`, but Devin's shell tool is `exec`. `EXECUTABLE_TOOLS` is `{bash, shell}`, so a Devin skill granting `exec` goes unreported. Add `exec` |
| `mcp_auto_approve` | **Yes, via `permissions`** | `mcp__…` allow entries; see below |
| `command_policy_allow` | **Yes, via `permissions`** | `allow` entries `Exec(<prefix>)` approve a command prefix to run unattended, the posture Codex's `prefix_rule` expresses; the bare tool name `exec` approves every command |
| `project_trust` | **No** | Devin has no trusted-directory concept. "Allow for this project" writes ordinary permission rules |
| `api_endpoint_override` | **No** | It matches literal Anthropic settings keys in a file Devin does not have |

**`mcp_auto_approve` repeats Cursor's lesson.** `mcp_config.json` has no
auto-approve field, so a rule reading only that file finds nothing. But
`permissions.allow` entries naming `mcp__<server>__<tool>`, `mcp__<server>__*`
or `mcp__*` are exactly the posture the rule reports. They go under
`mcp_auto_approve`, not a Devin-specific id, because unlike Codex's two surfaces
they are about MCP servers. That is the test the Codex spec applies in
[Two new rule IDs, not a re-pointed one](codex-agent-kind.md#two-new-rule-ids-not-a-re-pointed-one).

**One `permissions` list feeds two rule ids, split by entry:**

| Entry | Reported under |
|---|---|
| `Exec(…)`, `exec` | `command_policy_allow` |
| `mcp__…` | `mcp_auto_approve` |
| `Read(…)`, `Write(…)`, `Fetch(…)` | Nothing. They approve tool classes no posture rule covers |

A team allowing one id therefore does not silently allow the other.

Implementation constraints:

- **Merge before reporting.** The lists merge across levels, and a `deny`
  anywhere suppresses a matching `allow`. Reporting an `allow` its own deny
  overrides over-reports. An `ask` at a higher-precedence level suppresses a
  covering `allow` too: "a lower-level allow cannot override an ask from a
  higher-precedence level". At its own level an allow at least as specific as
  an ask wins. A rule *covers* an allow when it matches everything the allow
  approves: `Exec(git)` covers `Exec(git push)`, `mcp__github__*` covers
  `mcp__github__list_issues`.
- **`config.json` is JSON with comments.** A plain JSON loader drops a file with
  comments, as Cursor's `permissions.json` already taught.
- **`mcp_auto_approve`'s `active_in`** must be set per kind. The Codex spec
  records that it hardcodes `["cursor"]`.

## Deferred and out of scope

### Out of the first pass

| Surface | Why it waits | Cost of skipping |
|---|---|---|
| OpenCode and Zed MCP imports | Two schemas OpenACA does not parse (`mcp` with `environment`/`enabled`; `context_servers`), on few installations | Those servers are missing from a Devin BOM; installed and declared stay `partial` regardless |
| `windsurf-next` and `windsurf-insiders` channels | Devin reads the channel matching its own release; only stable is ordinary | A non-stable build's Windsurf imports are missing |
| `$COPILOT_HOME` relocation | Rare | Copilot user skills under a relocated root are missing |
| Plugin bundle layout inside the store | Needs a signed-in install to observe | Installed plugins are inventoried presence-only, from `lock.json`, until verified |
| `system.json` machine policy | Login and proxy only; declares no components | None for composition |
| Repo-level `requiredPlugins` in `.devin/config.json` | Devin's plugin docs name a repo level of plugin requirements; installing them needs Devin's service | A plugin a repository requires is missing from its declared BOM until installed |
| `projects.<path>.mcpServers` in `~/.claude.json` | Whether Devin imports Claude Code's per-project servers is unverified; only the top-level `mcpServers` is read | A Claude Code local-scope server Devin loads would be missing |
| `hooks.json` envelope inside a plugin bundle | Unverified; the declared reader takes Claude Code's `{"hooks": {...}}` envelope and records any other shape as a gap | A whole-file `hooks.json` in a repository's plugin lowers coverage instead of composing |
| Store `lock.json` entry shape | Unverified. A `resolved` entry yields a plugin only when its `name` (or `identity`) satisfies Devin's documented plugin-name rule; any other entry is a coverage gap | Store plugins with another entry shape are counted as gaps, not inventoried |

### Not shipping

- **Instruction files**: `AGENTS.md`, `AGENTS.local.md`, `AGENT.md`,
  `CLAUDE.md`, `.windsurfrules`, `.devin/rules/`, `.windsurf/rules/`,
  `.cursor/rules/`, `~/.config/devin/AGENTS.md`. They are instructions, not
  components, the rule Claude Code, Cursor and Codex already follow.
- **Windsurf workflows**, deferred pending a relevance signal. The docs state
  they are not imported as skills, so there is nothing to ship.

### Out of scope

- **Devin Cloud**: a separate, server-configured product with no local root.
- **Cascade, and a Windsurf kind.**
- **Windows and macOS verification**: the paths are documented, but only Linux
  was audited.
- **Managed plugins that never reached the cache**: they need the service.

## ADRs this kind needs

Each is a decision a reviewer could plausibly reverse, so each gets a record
before implementation.

| # | Decision | Detail | Record |
|---|---|---|---|
| 1 | Register `devin-cli` | Coverage baseline `partial`/`partial`; the declared evidence set above | [ADR-0070](../adrs/0070-devin-cli-agent-kind.md) |
| 2 | Refuse `--config-dir` | Applies ADR-0054; ADR-0059's companion relocation does not extend to four independent roots | [ADR-0071](../adrs/0071-devin-cli-refuses-config-dir.md) |
| 3 | `.agents/agents/` is evidence for the kinds that read it | ADR-0058's rule applied to a second shared directory, read today by one kind | [ADR-0072](../adrs/0072-shared-agents-agents-directory.md) |
| 4 | One `permissions` list, two posture rule ids, split by entry | Rather than a new Devin-specific id | [ADR-0073](../adrs/0073-devin-permissions-two-rule-ids.md) |
