# OpenCode Agent Kind — Surface Audit

*Implemented (2026-11-17): `opencode` is registered. Decisions:
[ADR-0083](../adrs/0083-opencode-agent-kind.md),
[ADR-0084](../adrs/0084-opencode-npm-plugin-identity-and-version.md),
[ADR-0085](../adrs/0085-opencode-permissions-feed-no-posture-rule.md). Audited
2026-11-17. This is the per-kind audit that
[Multi-Agent Support](multi-agent-support.md) requires.*

OpenCode is the open-source coding agent that npm publishes as `opencode-ai`.
One configuration schema holds most of its composition. Many files of that
schema merge into one configuration. MCP servers, npm plugins, agents and
commands come from the merged configuration. More agents, commands, plugins and
skills come from files in each configuration directory. Skills also come from
the `.claude/skills` and `.agents/skills` roots. The file shapes are ones
OpenACA already reads: an MCP map, `SKILL.md`, and Markdown with YAML
frontmatter. The new parts are the merge order and the npm plugin cache.

## At a glance

| | |
|---|---|
| Kind, root node | `opencode`, `agent:opencode` |
| Front ends | `opencode` (the TUI), `opencode run`, `opencode serve`, `opencode acp`. Each loads one configuration through `packages/opencode/src/config/config.ts` |
| Config root | `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode` |
| Plugin cache | `$XDG_CACHE_HOME/opencode/packages/`, else `~/.cache/opencode/packages/` |
| Managed directory | `/etc/opencode` on Linux, `/Library/Application Support/opencode` on macOS |
| Reads another runtime's files | Skills in `.claude/skills` and `.agents/skills`, in the home directory and in the project |
| `--config-dir` | Refused, with `root_override_refusal`, as for Cursor and Devin (ADR-0054) |
| Coverage, declared / installed | `partial` / `partial` (ADR-0046) |
| Component types | The existing closed set: `mcp_server`, `plugin`, `agent`, `command`, `skill`. OpenCode has no hooks surface |
| Changes to existing code | None to a shared parser or rule. The kind adds its own parser module, registry, composer and MCP posture collector |
| Audited source | `packages/opencode` and `packages/core` at tag `v1.19.5`, commit `810c0eb` |

The kind-spec contract
([Multi-Agent Support § What a kind spec must contain](multi-agent-support.md#what-a-kind-spec-must-contain)):

| Requirement | Met in |
|---|---|
| 1. Every path the runtime reads, including another runtime's | [Surfaces](#surfaces), [Files another runtime owns](#files-another-runtime-owns) |
| 2. An observability gap per surface | [Coverage](#coverage) |
| 3. A composition source per claim | The Source column of [Where each surface loads from](#where-each-surface-loads-from) |
| 4. A split verdict for anything deferred | [Deferred and out of scope](#deferred-and-out-of-scope) |

## What is and is not this kind

The front ends are one kind under ADR-0044's test (same surface, same schema).
Each one builds its configuration with the same `Config` service.

| Shares the name, not this kind | Why |
|---|---|
| The V2 runtime in `packages/core/src/config.ts` | The `opencode` binary at v1.19.5 does not load it. It reads V2-shaped keys only through `ConfigV2Compat.lower` (`config/v2-compat.ts`), which this spec covers |
| The desktop app and the web console | Not audited |

## Evidence

Every claim comes from OpenCode's source, read and not run.

| Source | What was done |
|---|---|
| OpenCode at `v1.19.5` | Read `packages/opencode/src/` (`config/`, `mcp/`, `plugin/`, `skill/`, `agent/`, `command/`, `tool/`, `permission/`, `project/`) and `packages/core/src/` (`global.ts`, `npm.ts`, `fs-util.ts`, `project.ts`, `v1/config/`). No OpenCode binary was run |
| OpenCode documentation at `v1.19.5` | `packages/web/src/content/docs/`: `config.mdx`, `mcp-servers.mdx`, `plugins.mdx`, `skills.mdx`, `agents.mdx`, `cli.mdx`. Read for intent. Where they disagree with the code, the code governs |
| `xdg-basedir` 5.1.0 | The published `index.js`, at the version that OpenCode's `bun.lock` pins |

- The claims hold at v1.19.5. They are not a contract OpenCode owes us.
  Re-audit at a major release.
- None of it is reproducible from this repository. The evidence is OpenCode's
  source, not a fixture.
- Linux and macOS paths come from the code. Windows is out of scope (ADR-0069).

### Where the documentation and the code disagree

| The documentation says | The code does |
|---|---|
| npm plugins are cached in `~/.cache/opencode/node_modules/` (`plugins.mdx`) | `Npm.add` installs each spec in its own directory, `<cache>/packages/<spec>/node_modules/<name>` (`core/src/npm.ts`) |
| Duplicate npm packages "with the same name and version" load once (`plugins.mdx`) | `deduplicatePluginOrigins` keys on the package name only. The last declaration wins, whatever its version (`config/plugin.ts`) |
| A skill is `skills/*/SKILL.md`, and its `name` must match its directory (`skills.mdx`) | Discovery globs `skills/**/SKILL.md`. A skill needs only a string `name`. No code compares it to the directory (`skill/index.ts`) |
| The project search stops at "the nearest Git directory" (`config.mdx`) | With no Git repository, the worktree is the filesystem root, so the search continues to `/` (`core/src/project.ts`, `project/project.ts`) |
| The `.opencode` directories are one precedence tier (`config.mdx`) | They merge nearest first, so an ancestor's `.opencode/opencode.json` overrides a nearer one (`config/paths.ts`, `config/config.ts`) |

## Robustness bar

**A correct inventory for an ordinary installation**, the bar that the Codex
and Devin specs state.

| Worth blocking on | Deliberately out of scope for now |
|---|---|
| A surface an ordinary installation has, uninventoried | Exhaustive validation of malformed configuration. A malformed file lowers `composition_coverage` and says so |
| A component reported that OpenCode does not load, or a wrong `enabled` state | The combinatorial tail of layer × merge × environment variable |
| A wrong `bom-ref`, identity, version or coverage verdict | Surfaces an ordinary installation does not have, listed in [Deferred and out of scope](#deferred-and-out-of-scope) with the cost of each |
| A host path in a `bom-ref` | |

## Roots

| Root | Resolves to | Moves with |
|---|---|---|
| Config | `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode` | `$XDG_CONFIG_HOME` |
| Plugin cache | `$XDG_CACHE_HOME/opencode`, else `~/.cache/opencode` | `$XDG_CACHE_HOME` |
| Home directories | `~/.opencode`, `~/.claude/skills`, `~/.agents/skills` | Nothing |
| Managed | `/etc/opencode` on Linux, `/Library/Application Support/opencode` on macOS (`config/managed.ts`) | Nothing |
| Data | `$XDG_DATA_HOME/opencode`, with `opencode.db` and `auth.json` | Not read. It holds sessions and credentials, not composition |

`xdg-basedir` uses each variable when it is not empty. A relative value
resolves against the working directory of OpenCode, which a scan cannot know.
For a relative value the scan uses the default, as for Devin.

### `--config-dir` is refused

`--kind opencode` selects the kind. `--config-dir` is refused with
`root_override_refusal`, as Cursor's is under ADR-0054.

OpenCode composes from five places: the config root, the plugin cache on a
different XDG variable, `~/.opencode`, the skill roots of other runtimes in the
home directory, and the managed directory. No single directory names all five.
An override that moves only the config root gives a composition from two homes.
The output cannot show the difference from a correct scan.

## Surfaces

Notation:

- `<config>`, `<cache>` and `<managed>` are the [roots](#roots).
- **Project layers** are `--project` and each ancestor, up to and including the
  first directory that holds `.git`. With no `.git` above it, `--project` is the
  only layer ([Deferred](#out-of-the-first-pass)).
- **Configuration directories** are `<config>`, the `.opencode/` of each project
  layer, and `~/.opencode/` (`ConfigPaths.directories`).
- **Configuration files** are the JSON files in [Precedence](#precedence).

The inclusion bar is *would omitting this make the inventory wrong on an
ordinary installation*, not *is it reachable*.

### Where each surface loads from

| Surface | Project | User | Traversal | Accepts | Source |
|---|---|---|---|---|---|
| Configuration files | `opencode.json`, `opencode.jsonc` in each layer; `.opencode/opencode.json`, `.opencode/opencode.jsonc` | `<config>/config.json`, `<config>/opencode.json`, `<config>/opencode.jsonc`; `~/.opencode/opencode.json(c)`; `<managed>/opencode.json(c)` | per layer | JSON with comments and trailing commas (`ConfigParse.jsonc`) | both |
| MCP servers | `mcp` in configuration files | same | merged by name | `{type: "local", command: [...], environment, enabled}`; `{type: "remote", url, headers, oauth, enabled}`; `{enabled}` alone; the V2 `mcp.servers` envelope, with `disabled` | both |
| Plugins, npm | `plugin` in configuration files | same | one per package name | a spec string, or `[spec, options]` | both. The version comes from `<cache>` when installed |
| Plugins, local | `.opencode/plugin/*.{ts,js}`, `.opencode/plugins/*.{ts,js}`; a path spec in `plugin` | `<config>/plugin(s)/`, `~/.opencode/plugin(s)/` | flat | a script file | both |
| Agents | `agent`, `mode` and V2 `agents` in configuration files; `.opencode/{agent,agents}/**/*.md`; `.opencode/{mode,modes}/*.md` | the same directories under `<config>` and `~/.opencode` | recursive for agents, flat for modes | a map entry, or Markdown with YAML frontmatter | both |
| Commands | `command` and V2 `commands` in configuration files; `.opencode/{command,commands}/**/*.md` | the same directories under `<config>` and `~/.opencode` | recursive | a map entry with `template`, or Markdown | both |
| Skills | `.opencode/{skill,skills}/**/SKILL.md`; `.claude/skills/**/SKILL.md` and `.agents/skills/**/SKILL.md` in each layer | `<config>/{skill,skills}/`, `~/.opencode/{skill,skills}/`, `~/.claude/skills/`, `~/.agents/skills/` | recursive | `SKILL.md` with a string `name` | both |
| Skills, configured | `skills.paths`: `**/SKILL.md` below each path; `skills.urls` | same | recursive | directories; URLs | both. A URL is a [gap](#coverage) |
| Permissions | `permission` and `tools`, top level and per agent | same | merged | rules | not read ([Posture](#posture)) |

The glob for a configuration directory's skills does not match a hidden
directory. The glob for `.claude/skills` and `.agents/skills` does
(`skill/index.ts`, `dot: true`).

### Not there

| A reader expects | Why it is absent |
|---|---|
| Hooks in configuration | The schema has no hooks key. A plugin runs hooks as code: `tool.execute.before`, `tool.execute.after`, `permission.ask` and others (`packages/plugin/src/index.ts`) |
| `~/.cache/opencode/node_modules/` | v1.19.5 does not read it. See [Where the documentation and the code disagree](#where-the-documentation-and-the-code-disagree) |
| The built-in agents and the built-in skill | They are defined in the source. Listed on the BOM root (ADR-0076), never as components. See [Built-ins](#built-ins) |
| The compiled-in authentication plugins | `internalPlugins` in `plugin/index.ts` (Codex, Copilot, GitLab and others). They are part of the executable. ADR-0076's vocabulary is sub-agents and skills, so they are not listed |

### Precedence

`Config.loadInstanceState` merges these sources, lowest first:

1. Remote configuration from `.well-known/opencode`, for each `wellknown` login
   in `auth.json`. Not read.
2. `<config>/config.json`, then `<config>/opencode.json`, then
   `<config>/opencode.jsonc`.
3. `$OPENCODE_CONFIG`. Not read.
4. Project files, farthest layer first. In each layer, `opencode.json`, then
   `opencode.jsonc`.
5. The configuration directories, in `ConfigPaths.directories` order: `<config>`,
   each layer's `.opencode/` nearest first, `~/.opencode/`, then
   `$OPENCODE_CONFIG_DIR` (not read). A `.opencode/` directory gives its
   `opencode.json`, then its `opencode.jsonc`. Then every directory gives its
   Markdown commands, agents and modes, and then its plugin files.
6. `$OPENCODE_CONFIG_CONTENT`, then the active account's organization
   configuration. Not read.
7. `<managed>/opencode.json`, then `<managed>/opencode.jsonc`.
8. macOS managed preferences, `ai.opencode.managed`. Not read.

The merge rules:

- **Configuration merges deep.** An object merges into an object. Any other
  value replaces the earlier one, and an array replaces an array (remeda
  `mergeDeep`).
- **MCP servers merge by name.** A later `{"enabled": false}` switches off a
  server that an earlier file declares.
- **Plugins are one per package name**, or one per file for a local plugin. The
  later declaration wins.
- **Agents merge by name.** A merged `disable: true` removes the agent.

### MCP servers

Each configuration file is read, then lowered as `ConfigV2Compat.lower` lowers
it:

- A flat entry under `mcp` stays as written, unless it carries a V2 key
  (`disabled`, `codemode`, an object `timeout`). Then `enabled` becomes
  `disabled !== true`.
- An `mcp.servers` envelope adds its entries. A flat entry of the same name
  wins. An envelope entry that fails the V2 shape is dropped.
- An `mcp.timeout` object of the V2 timeout shape is a setting, not a server.

After lowering, an entry must be a local server, a remote server, or an
`{"enabled": <bool>}` toggle. Any other entry fails OpenCode's schema, and
OpenCode does not start. The scan records that file as a gap and does not read
it.

The scan then merges the entries of each name in [precedence](#precedence)
order:

- A merged entry with no `type` is not loaded (`MCP.state`, "Ignoring MCP config
  entry without type"). It is not a component.
- Each other name is one component. Its `source_manifest` is the
  highest-precedence file whose entry sets `type`. Its locator is
  `$.mcp.<name>`, or `$.mcp.servers.<name>` for an envelope entry.
- `enabled` is always stated, as for Codex (ADR-0055). A disabled server is
  still configured, and it is one edit from running.
- A local server's `command` array is read as the command and its arguments. A
  remote server's transport is `http`: OpenCode tries streamable HTTP, then SSE.

**The alias is the configured name**, the key under `mcp`. The BOM carries it in
`openaca:component_path`. OpenCode names a tool `sanitize(server)_sanitize(tool)`,
where `sanitize` replaces each character outside `[a-zA-Z0-9_-]` with `_`
(`mcp/catalog.ts`). A consumer that joins a tool call to a server applies the
same function to the alias.

OpenCode replaces `{env:NAME}` and `{file:path}` in the file text before it
parses the file (`config/variable.ts`). The scan reads the text as written.

### Plugins

**An npm spec is installed once and reused.** `resolvePluginTarget` calls
`Npm.add(pkg)`, where `pkg` is `<name>@latest` for a bare name and the spec as
written otherwise. `Npm.add` installs into `<cache>/packages/<pkg>/` and loads
`node_modules/<name>`. When that directory exists, `Npm.add` uses it and does
not install again. So the installed `package.json` gives the version that runs.

- The scan reads `<cache>/packages/<pkg>/node_modules/<name>/package.json`. Its
  `name` and `version` give the component's PURL. For an alias spec,
  `alias@npm:real@1.0.0`, that name is `real`.
- With no install, an exact version in the spec is the version. A range, a tag
  or no version gives no version.
- The identity is `plugin/npm/<name>` (ADR-0084).
- A spec that is not a registry spec (a Git URL, a tarball URL, `file:`) is
  presence-only, with no version.
- OpenCode ignores `opencode-openai-codex-auth` and `opencode-copilot-auth`,
  which are now built in (`DEPRECATED_PLUGIN_PACKAGES`). The scan skips them.

**A local plugin is a script file.** A path spec (`./x.ts`, an absolute path,
`file://`) resolves against the configuration file that declares it. A file in a
`plugin/` or `plugins/` directory loads with no declaration.

- Its name is the file stem. A directory target takes its `package.json` `name`,
  else the directory name. OpenCode names a path plugin by its exported `id`,
  which only running the plugin can read.
- Its identity is occurrence-local.
- One file reached two ways is one plugin.

A configured plugin's `source_manifest` is its configuration file, with the
locator `$.plugin[<index>]`. A discovered plugin file is its own
`source_manifest`, with the locator `$`.

### Agents

- A map entry is named by its key. A Markdown agent is named by its frontmatter
  `name`, else by its path below `agent/` or `agents/` without `.md`. A nested
  file gives a name with a slash, `team/reviewer` (`config/entry-name.ts`).
- The `task` tool looks an agent up by that name, its `subagent_type`. A
  component is named the same way.
- A `mode` entry or a `mode/*.md` file is a primary agent. It is a component of
  type `agent`.
- A definition named for a built-in agent configures that agent. It is still a
  component, because its content decides how the agent runs. The root lists the
  built-in too, and installed wins (ADR-0076).
- A name whose merged definition sets `disable: true` is not loaded. None of its
  definitions is a component.
- A V2 `agents` entry is read where no `agent` entry of the same name exists. Its
  `disabled` is `disable`.

### Commands

- A map entry is named by its key. A Markdown command is named as a Markdown
  agent is.
- OpenCode also lists MCP prompts and skills as commands at run time. They are
  not more components.

### Skills

- A skill is named by its frontmatter `name`, which the `skill` tool looks up. A
  `SKILL.md` with no string `name` does not load. It is not a component.
- A `SKILL.md` whose frontmatter the scan cannot parse is a gap.
- Two `SKILL.md` files can give one name. OpenCode keeps one of them, and which
  one is not deterministic: it adds skills concurrently. The scan records each
  file.
- A `skills.paths` entry starting with `~/` resolves against the home
  directory, and a relative one against `--project`.

## Files another runtime owns

Kind-spec requirement 1.

| Path | Owner | Read by OpenCode as | Gated |
|---|---|---|---|
| `.claude/skills/**/SKILL.md`, `~/.claude/skills/**/SKILL.md` | Claude Code | Skills | `OPENCODE_DISABLE_EXTERNAL_SKILLS`, `OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` |
| `.agents/skills/**/SKILL.md`, `~/.agents/skills/**/SKILL.md` | Cross-tool | Skills | `OPENCODE_DISABLE_EXTERNAL_SKILLS` |

The gates are process environment variables. The scan cannot see the
environment of OpenCode, so it reads both roots ([Coverage](#coverage)).

### Evidence and composition

- **`.claude/skills` is composition, never evidence.** A tree holding only
  `.claude/skills/` declares a Claude Code agent.
- **`.agents/skills` is evidence (ADR-0058).** A repository holding only
  `.agents/skills/` now declares Cursor, Codex, Devin and OpenCode agents: four
  BOMs. That is ADR-0058's stated cost, applied to a fourth reader.
- **A `.opencode/tool/` or `.opencode/tools/` file is not evidence.** The scan
  composes nothing from it ([Deferred](#deferred-pending-a-taxonomy-adr)).
- **Devin CLI reads `opencode.json` as an import.** That import is deferred in
  the Devin spec. For OpenCode the file is evidence.

**OpenCode declared evidence**, each pattern matched at any depth:

- `opencode.json`, `opencode.jsonc`
- `.opencode/{agent,agents}/**/*.md`, `.opencode/{mode,modes}/*.md`
- `.opencode/{command,commands}/**/*.md`
- `.opencode/{plugin,plugins}/*.{ts,js}`
- `.opencode/{skill,skills}/**/SKILL.md`
- `.agents/skills/**/SKILL.md`

Evidence and declared composition use one classifier, so they cannot disagree.

### Declared composition

A repository has no session directory, so the scan composes each directory that
holds OpenCode configuration as its own group: its `opencode.json(c)`, its
`.opencode/opencode.json(c)` and its `.opencode/` files. A group merges as one
layer of [Precedence](#precedence) does. Two groups are separate occurrences.

- A `skills.paths` entry resolves against its group's directory. An entry that
  leaves the repository is not read.
- An npm plugin has no install to read. Only an exact version in the spec gives
  a version.

### Node keys

| Mode | Requirement |
|---|---|
| Declared | None new. A key is relative to the scan root (ADR-0045) |
| Installed | A label for each root outside the project: `opencode/` for `<config>`, `claude-code/` for `~/.claude`, `agents/` for `~/.agents`, `opencode-managed/` for `<managed>`, `home/` for `~/.opencode` and the rest of the home directory, and `opencode-skills-path-<n>/` for a `skills.paths` directory outside every other root. Project files key under `project/`, relative to the top layer |

An npm plugin keys under its configuration file, so no cache path enters a
`bom-ref`.

## Coverage

`partial` at both composition sources (ADR-0046).

| Gap | Affects | Source | Closable by parsing? |
|---|---|---|---|
| A plugin is code. Its `config` hook receives the merged configuration, and its `tool` and `permission.ask` hooks add tools and approvals | every surface | both | **No.** Code-authored composition, the CrewAI case in Multi-Agent Support |
| MCP servers registered at run time: `POST /mcp` (`server/routes/instance/httpapi/handlers/mcp.ts`) and ACP `session/new` `mcpServers` (`acp/service.ts`) | MCP servers | installed | **No.** No file records them |
| Remote configuration: `.well-known/opencode` and the account organization's configuration | every surface | installed | **No.** A server call |
| Process environment: `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_DISABLE_*_SKILLS`, `OPENCODE_PURE` | every surface | installed | **No.** The scan cannot see the environment that OpenCode runs in |
| `skills.urls` | skills | both | **Partly.** OpenCode caches the files under `<cache>/skills/<hash>/`. Deferred. A scan records a gap when a URL is declared |
| macOS managed preferences | every surface | installed | **Partly.** It needs `plutil`. Deferred |

- **Installed is `partial`** because of the first four rows.
- **Declared is `partial`** because of the first row. A repository can ship a
  plugin file, and the scan cannot see what the plugin adds.

## Identity

- **Every surface maps into the closed set.** No new component type and no new
  source ecosystem, so no taxonomy ADR blocks this kind.
- **An MCP server** takes the identity every kind gives it:
  `mcp-server/npm/<package>` for a package launch, `mcp-remote/<host><path>` for
  a remote server.
- **An npm plugin** takes `plugin/npm/<name>` and the PURL
  `pkg:npm/<name>@<version>` (ADR-0084).
- **A local plugin, an agent and a command** are occurrence-local.
- **A skill** takes the identity every kind gives it.

An example installed composition:

| Component | Name | Identity | PURL or version |
|---|---|---|---|
| MCP server `github` (`npx -y @modelcontextprotocol/server-github@2025.4.8`) | `@modelcontextprotocol/server-github` | `mcp-server/npm/@modelcontextprotocol/server-github` | `pkg:npm/%40modelcontextprotocol/server-github@2025.4.8` |
| MCP server `docs` (`https://docs.example.com/mcp`) | `docs` | `mcp-remote/docs.example.com/mcp` | — |
| npm plugin `opencode-wakatime` | `opencode-wakatime` | `plugin/npm/opencode-wakatime` | `pkg:npm/opencode-wakatime@1.2.3`, from the cache |
| Local plugin `.opencode/plugins/notify.ts` | `notify` | — | — |
| Agent `.opencode/agents/review.md` | `review` | — | — |
| Skill `.agents/skills/release/SKILL.md` | `release` | `skill/release` | — |

## Built-ins

OpenCode defines these in its source. An installed BOM lists them on the root
(ADR-0076), never as components.

| | Names | Read from |
|---|---|---|
| Sub-agents | `build`, `compaction`, `explore`, `general`, `plan`, `summary`, `title` | The `native: true` definitions in `packages/opencode/src/agent/agent.ts` |
| Skills | `customize-opencode` | `CUSTOMIZE_OPENCODE_SKILL_NAME` in `packages/opencode/src/skill/index.ts` |

## Posture

Posture rules read composed components, as for every kind.

| Rule | Applies | Why |
|---|---|---|
| `insecure_transport` | **Yes** | A remote server has `url`. An `http://` server is the same exposure |
| `mcp_header_credential` | **Yes** | A remote server has `headers`. OpenCode writes a reference as `{env:NAME}` or `{file:path}`, not `${…}`. The OpenCode collector writes each in the `${…}` form the rule reads, so the rule stays strict for every other kind |
| `mutable_install` | **Yes, for MCP servers** | The MCP branch reads the launch line, which the `command` array gives. It does not fire for a plugin: the rule reads launch lines, and a bare npm spec such as `name@1.2.3` reads as an opaque token |
| `mcp_auto_approve` | **No** (ADR-0085) | OpenCode allows every tool by default. Its `permission` allows are not an opt-in from a confirming default |
| `command_policy_allow` | **No** (ADR-0085) | The same reason, for `bash` |
| `skill_capability` | **No** (ADR-0085) | OpenCode reads only `name` and `description` from a skill. It ignores `allowed-tools`, the field this rule reads |
| `project_trust` | **No** | OpenCode has no trusted-directory concept |
| `api_endpoint_override` | **No** | It matches Claude Code settings keys in a file that OpenCode does not have |

**The MCP posture view is built from the composed servers.** For each server,
the collector reads the files that declare its name, in precedence order. It
merges their `headers` and records the file that sets each header. A finding
names that file, and attaches to the composed server. No header value enters a
component.

**Why the permission rules do not apply.** The default ruleset of every
built-in agent starts with `"*": "allow"` (`agent/agent.ts`). Bash commands and
MCP tools run with no confirmation unless a rule says `ask` or `deny`. A rule
that reports an explicit `allow` reports a change that does nothing. It says
nothing about the default, which is the more permissive state. Reporting that
posture honestly needs a rule about the absence of a confirmation. That is a new
rule id, and it waits for its own ADR.

## Deferred and out of scope

### Out of the first pass

| Surface | Why it waits | Cost of skipping |
|---|---|---|
| Ancestors of a project with no `.git` | OpenCode then searches every ancestor to `/`. A key for a file outside every labelled root would hold a host path | An `opencode.json` or `.opencode/` above a non-Git project is missing |
| `skills.urls` | The cache directory name is a Bun hash of the URL | Fetched skills are missing. The scan records a gap |
| The dependencies of an npm plugin | The install directory holds a `package-lock.json`. Reading it duplicates the plugin's own package | A vulnerable transitive dependency of a plugin is not matched |
| A Git, tarball or `file:` plugin spec | The spec does not give `<name>`. Only the files of the install directory give it | Such a plugin has no version |
| TUI plugins in `tui.json` | They run in the terminal interface, not where tools run | A TUI plugin is missing |
| Policy evidence | `collect_policy_evidence` admits a kind only after its own review | `opencode` is not in `POLICY_EVIDENCE_AGENT_KINDS` |
| `{env:…}` and `{file:…}` in a URL or a command | OpenCode substitutes them before parsing. The scan reads the text as written | Such a server has the identity of its unexpanded text |
| A merged MCP entry that takes fields from two files of different `type` | A tail case of the deep merge | The component's content comes from the merged entry, and its `source_manifest` from the file that sets `type` |
| macOS managed preferences | It needs `plutil` | Components that MDM delivers are missing |

### Deferred pending a taxonomy ADR

| Surface | Why |
|---|---|
| Custom tools, `{tool,tools}/*.{ts,js}` in each configuration directory (`tool/registry.ts`) | The closed set has no `tool` component type |
| LSP servers and formatters, `lsp` and `formatter` | Commands OpenCode runs for the editor. The closed set has no counterpart |

### Not shipping

- **Instruction files**: `AGENTS.md`, `CLAUDE.md`, the `instructions` key. They
  are instructions, not components, the rule every kind follows.
- **Providers and references**: `provider` and `references`. They declare model
  endpoints and directories, not components.

### Out of scope

- **Windows** (ADR-0069).
- **The V2 runtime**, beyond the V2 keys that v1.19.5 lowers.
- **Session and credential state** in `$XDG_DATA_HOME/opencode`.

## Decisions

| # | Decision | Detail | Record |
|---|---|---|---|
| 1 | Register `opencode`, `partial` at both sources; merge MCP servers by name with `enabled` stated; refuse `--config-dir` | [Coverage](#coverage), [MCP servers](#mcp-servers), [Roots](#roots) | [ADR-0083](../adrs/0083-opencode-agent-kind.md) |
| 2 | An npm plugin is `plugin/npm/<name>`, versioned from OpenCode's install | [Plugins](#plugins) | [ADR-0084](../adrs/0084-opencode-npm-plugin-identity-and-version.md) |
| 3 | OpenCode's `permission` feeds no posture rule, and `skill_capability` does not apply | [Posture](#posture) | [ADR-0085](../adrs/0085-opencode-permissions-feed-no-posture-rule.md) |

## Verification

- `tests/test_agent_kinds_opencode.py`: discovery at each source, the evidence
  set, the `--config-dir` refusal and the built-ins.
- `tests/test_graph_build_opencode.py`: precedence, the MCP merge and its
  `enabled` state, V2 lowering, plugin versions from the cache, agent names and
  `disable`, skills, and keys with no host path.
- `tests/test_posture_opencode.py`: the allowlist, header attribution across
  files, and `{env:…}` and `{file:…}` references.
- `tests/test_e2e.py`: `collect_installed_agents(kind_id="opencode")` gives one
  BOM. An npm plugin version from the cache matches an advisory through its
  PURL.
