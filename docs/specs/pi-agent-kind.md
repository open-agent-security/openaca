# Pi Agent Kind — Surface Audit

*Implemented (2026-11-17): `pi` is registered. Decisions:
[ADR-0081](../adrs/0081-pi-agent-kind.md) and
[ADR-0082](../adrs/0082-pi-extensions-and-packages-are-plugins.md). This is the
per-kind audit that [Multi-Agent Support](multi-agent-support.md) requires.*

**Pi is Claude Code-shaped in its MCP and skill formats and npm-shaped in its
distribution.** Its `mcp.json` uses the `mcpServers` map, and its skills use
`SKILL.md`. Its executable surface is different. An extension is a TypeScript
module that runs inside the Pi process, and a package is an npm or git
distribution of extensions, skills and prompt templates. A Pi package installed
from npm has an exact version on disk, so it is the surface that lets OSV
advisories match.

## At a glance

| | |
|---|---|
| Kind, root node | `pi`, `root/pi` |
| Program | `pi`, from the npm package `@earendil-works/pi-coding-agent` (formerly `@mariozechner/pi-coding-agent`) |
| Agent directory | `$PI_CODING_AGENT_DIR`, else `~/.pi/agent` |
| Project directory | `<project>/.pi`, loaded only for a trusted project |
| Imports | None. Pi reads the cross-tool `.agents/skills` convention, and no other runtime's files |
| `--config-dir` | Refused, with `root_override_refusal` |
| Coverage, declared / installed | `partial` / `partial` (ADR-0046) |
| Component types | The existing closed set. An extension and a package are each a `plugin` (ADR-0082). A prompt template is a `command` |
| Changes to existing code | `npm:` specs in `is_mutable_reference`; bare `$NAME` and whole-value `!command` header values in `mcp_header_credential` |
| Audited release | `@earendil-works/pi-coding-agent` 1.5.2, with `@earendil-works/pi-mcp` 1.5.2 |

The kind-spec contract
([Multi-Agent Support § What a kind spec must contain](multi-agent-support.md#what-a-kind-spec-must-contain)):

| Requirement | Met in |
|---|---|
| 1. Every path the runtime reads, including another runtime's | [Surfaces](#surfaces), [Files another runtime owns](#files-another-runtime-owns) |
| 2. An observability gap per surface | [Coverage](#coverage) |
| 3. A composition source per claim | The Source column of [Where each surface loads from](#where-each-surface-loads-from) |
| 4. A split verdict for anything deferred | [Deferred and out of scope](#deferred-and-out-of-scope) |

## What is and is not this kind

One program reads one agent directory and one project directory. The SDK
embeds the same loader, and an SDK caller can pass its own `agentDir`. That is
a program built on Pi, not an installed Pi agent, so it is out of scope.

| Shares the name, not this kind | Why |
|---|---|
| A program built on the Pi SDK | It chooses its own agent directory and its own extensions in code |
| Pi's built-in extensions (`builtin:mcp`, `builtin:llama.cpp`, `builtin:codemode`, `builtin:tool-search`) | They are part of the program. Nothing installs or removes them as files |

Pi has no built-in sub-agents and no built-in skills. Its README states that it
"skips features like sub-agents". The BOM root therefore lists no
`openaca:builtin_agents` and no `openaca:builtin_skills` (ADR-0076).

## Evidence

Every claim comes from Pi 1.5.2's shipped npm package. The package was read. It
was not run, because this audit could not run agent binaries. The package holds
the shipped documentation under `docs/` and the compiled JavaScript under
`dist/`.

| Source | What was read |
|---|---|
| `docs/` | `configuration.md`, `settings.md`, `mcp.md`, `skills.md`, `extensions.md`, `packages.md`, `prompt-templates.md`, `security.md`, `environment-variables.md`, `cli.md` |
| `dist/config.js` | The agent directory: `getAgentDir()`, `CONFIG_DIR_NAME` |
| `dist/core/package-manager.js` | Package sources, install paths, versions, and the auto-discovery of extensions, skills and prompt templates |
| `dist/core/settings-manager.js`, `dist/core/pi-manifest.js` | The two `settings.json` scopes and the `pi` key of a `package.json` |
| `dist/core/skills.js`, `dist/core/prompt-templates.js` | Which skill and template files load, and their names |
| `dist/core/trust-manager.js`, `dist/core/project-trust.js` | Project trust |
| `dist/extensions/mcp/config.js`, `pi-mcp/dist/config.js` | `mcp.json` validation and the merge of the two scopes |
| `dist/core/resolve-config-value.js` | How a header value is expanded |
| `dist/core/mcp-servers.js` | Servers that an extension registers at run time |

**Where the docs and the code disagree, the code governs.** These are the
differences that this audit found:

| The docs say | The code does |
|---|---|
| User servers are in `~/.pi/agent/mcp.json`; personal packages go to `~/.pi/agent/settings.json` | Both are under the agent directory, so both move with `$PI_CODING_AGENT_DIR` |
| Skill directories "are discovered recursively" | The walk stops at a directory that holds `SKILL.md`. It skips names that start with `.`, `node_modules`, and paths that a `.gitignore`, `.ignore` or `.fdignore` in the skill root excludes |
| Extension discovery "goes one level deep" | The CLI first checks the extensions directory itself. If it holds `index.ts`, `index.js` or a `package.json` with `pi.extensions`, only those entries load |
| npm packages are "installed under the Pi npm directory" | A user package missing from `<agent dir>/npm/node_modules/` is looked for under the global npm root, a legacy location |
| Project `.agents/skills` stops "at the repository root when one exists" | The repository root is the nearest ancestor that holds `.git`. With none, the walk goes to the file system root |

- The claims hold at 1.5.2. They are not a contract Pi owes OpenACA. Re-audit
  on a major release.
- None of it is reproducible from this repository. The evidence is a shipped
  package, not a fixture.
- Linux paths only. Windows is deferred (ADR-0069).

## Robustness bar

**A correct inventory for an ordinary installation**, the bar the Codex and
Devin specs state.

| Worth blocking on | Deliberately out of scope for now |
|---|---|
| A surface an ordinary installation has going uninventoried: `mcp.json`, settings packages, the extensions, skills and prompts directories | Exhaustive validation of malformed configuration. A malformed file lowers `composition_coverage` and says so |
| An npm package without its installed version, or a git package without its commit, when the install is on disk | The settings filter patterns that narrow which resources load ([Deferred](#out-of-the-first-pass)) |
| A wrong `bom-ref`, identity or coverage verdict | Overlap and name-collision tails: the same file through two routes, two skills with one name |
| A host path leaking across the upload boundary | |

## Roots

| Root | Resolves to | Moves with `$PI_CODING_AGENT_DIR` |
|---|---|---|
| Agent directory | `$PI_CODING_AGENT_DIR` with a leading `~` expanded, else `~/.pi/agent` | Yes |
| Project directory | `<project>/.pi`. Pi reads the working directory only, never an ancestor's `.pi` | No |
| User shared skills | `$HOME/.agents/skills` | No |
| Project shared skills | `<dir>/.agents/skills` for the project and each ancestor, up to the repository root | No |

### `--config-dir` is refused

`--kind pi` selects the kind. `--config-dir` is refused with
`root_override_refusal`, as Cursor's and Devin's are under ADR-0054.

Pi reads `$HOME/.agents/skills` whatever the agent directory is, and a user npm
package can come from the global npm root. A named directory moves neither.
`$PI_CODING_AGENT_DIR` is honoured, so a relocated agent directory is scanned
the way Pi reads it: from that directory, plus `$HOME/.agents/skills`.

## Surfaces

Notation:

- `<agent>` is the agent directory and `<project>` is `--project`.
- **Trusted** marks a surface that loads only for a trusted project
  ([Project trust](#project-trust)).
- A settings entry is a plain path in the `extensions`, `skills` or `prompts`
  array of a `settings.json`. A user entry resolves from `<agent>`, a project
  entry from `<project>/.pi`. A leading `~` names the home directory.

### Where each surface loads from

| Surface | Project | User | Traversal | Accepts | Source |
|---|---|---|---|---|---|
| MCP servers | `.pi/mcp.json` (trusted) | `<agent>/mcp.json` | file | `mcpServers` map: `command`/`args`/`env`/`cwd`, or `url`/`headers`/`oauth`; `type`, `enabled`, `exposure` | both |
| Packages | `packages` in `.pi/settings.json` (trusted) | `packages` in `<agent>/settings.json` | per entry | `npm:`, git and local sources ([Packages](#packages)) | both |
| Extensions | `.pi/extensions/` (trusted), settings entries | `<agent>/extensions/`, settings entries | one level | `*.ts`, `*.js`, `<dir>/index.ts`, `<dir>/index.js`, `<dir>/package.json` with `pi.extensions` | both |
| Skills | `.pi/skills/` (trusted), `.agents/skills/` in the project and its ancestors (trusted), settings entries | `<agent>/skills/`, `$HOME/.agents/skills/`, settings entries | recursive, stopping at a `SKILL.md` | `<dir>/SKILL.md` with a `description` | both |
| Prompt templates | `.pi/prompts/` (trusted), settings entries | `<agent>/prompts/`, settings entries | direct children; a settings directory recursively | `*.md` | both |
| Project trust | — | `<agent>/trust.json`, `defaultProjectTrust` in `<agent>/settings.json` | file | `{path: true \| false \| null}` | installed (scan only) |

### Project trust

Pi loads `.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions`, `.pi/skills`,
`.pi/prompts` and the project `.agents/skills` directories only after the
project is trusted. An installed scan reads the trust decision as Pi does:

1. The nearest decision in `<agent>/trust.json` for `<project>` or an ancestor
   applies. A key is a canonical directory path. A `null` value is no decision.
2. With no decision, `defaultProjectTrust` in `<agent>/settings.json` applies.
3. The composer skips the project layer only when the decision is `false`, or
   when there is no decision and the default is `"never"`.

With no decision and the default `"ask"`, an interactive run asks and saves the
answer, and a run without a terminal skips the project. The composer composes
the project in this case. It reports what a trusted run loads, and records a
note. A command-line `--approve` or `--no-approve` and an extension's
`project_trust` handler decide at run time and leave nothing on disk.

A declared scan does not apply trust. A repository's `.pi/` declares what a
trusted run loads, as a Codex repository's `.codex/config.toml` does.

### MCP servers

`mcp.json` is strict JSON. A byte-order mark is allowed. The two files merge by
server name:

- A project entry replaces a user entry of the same name.
- A project entry with no `command`, `url` or `type` is an override. It changes
  only `enabled`, `exposure` and `toolExposure` of the user entry of that name.
  An override with no user entry, or with another key, is not loaded.
- A name must match `^[A-Za-z0-9_-]+$`. Two names that differ only in `-` and
  `_` clash, and the second is not loaded.
- `type` is optional. When present it is `stdio`, `http` or `streamable-http`.
  `sse` is rejected. A URL server needs an `http` or `https` URL. A stdio server
  needs a string `command`.
- A project URL server with `auth` is not loaded. `auth` sends a provider's
  credential, and only the user file can choose where it goes.

An entry Pi does not load is not a component. The composer records a note for
it. A server with `enabled: false` stays in the inventory with
`openaca:enabled` `false`, because `/mcp` can turn it back on. Every other
server carries `openaca:enabled` `true`.

**The component's name in the walk is the configured name**, the key under
`mcpServers`. The last `mcp_server` entry of `openaca:component_path` carries
it, as for every kind. A call names its server by this name. An override keeps
the user entry's `source_manifest`, because the user file holds the server's
command or URL and its headers.

### Packages

A `packages` entry is a string or an object with a `source` field. Pi parses the
source as follows:

| Source | Example | Install location | Identity |
|---|---|---|---|
| npm | `npm:@scope/tools@1.2.0` | user: `<agent>/npm/node_modules/<name>`; project: `<project>/.pi/npm/node_modules/<name>` | the package name |
| git | `git:github.com/owner/repo@v1`, `git:git@host:owner/repo`, `https://…`, `ssh://…` | user: `<agent>/git/<host>/<path>`; project: `<project>/.pi/git/<host>/<path>` | `<host>/<path>`, without the ref |
| local | `./tools`, `~/tools`, `/abs/tools` | the path itself, from the settings file's directory | the resolved path |

A source that is not `npm:` and does not name a git URL is a local path. A
project entry replaces a user entry with the same identity. A project object
with `autoload: false` filters the user entry instead, so the user entry is the
one component.

**Versions.** Pi installs an npm package into an npm project at
`<agent>/npm/` and reads its version from the installed `package.json`. The
composer does the same, and falls back to the declared version only when it is
an exact semantic version. A git package's version is the commit that
`.git/HEAD` names in the checkout. A declared ref that is not a full commit
becomes `openaca:git_ref`.

**Contents.** A package's `package.json` may list resources under `pi`
(`extensions`, `skills`, `prompts`, `themes`), as paths or globs. With no such
list, Pi reads the conventional `extensions/`, `skills/` and `prompts/`
directories. A listed directory is walked recursively for skills and prompts and
one level for extensions. A glob does not match a path segment that starts with
`.`. A local source that names a file is one extension.

A package whose install is not on disk is presence-only. Pi installs it at the
next start, so its contents are unknown, and the composer records a gap. A
local source that does not exist is no component, because Pi skips it and
installs nothing.

### Extensions

The CLI discovers extensions in a directory in this order:

1. If the directory holds a `package.json` whose `pi.extensions` lists paths
   that exist, those paths load and nothing else does.
2. Else, if it holds `index.ts` or `index.js`, that file loads and nothing else
   does.
3. Else, each child loads: a `*.ts` or `*.js` file, or a subdirectory resolved
   by steps 1 and 2. Names that start with `.` and `node_modules` are skipped.

An extension's name is its entry file without `.ts` or `.js`. An `index` entry
takes its directory's name, as Pi's own startup listing shows it.

### Skills

A skill root is walked recursively. A directory that holds `SKILL.md` is one
skill, and the walk does not go below it. Names that start with `.` and
`node_modules` are skipped. Pi does not load a `SKILL.md` whose frontmatter has
no non-empty `description`, so the composer skips it. The name is the
frontmatter `name`, else the directory's name.

### Prompt templates

A prompt template is a `/` command. Its name is the file name without `.md`.
Pi ignores a frontmatter `name`, so the composer does too. The `.pi/prompts/`
and `<agent>/prompts/` directories load their direct `.md` children only.

### Not there

| A reader expects | Why it is absent |
|---|---|
| Hooks | Pi has no hook file. An extension subscribes to events in code |
| Sub-agents | Pi has none, built in or configurable |
| A plugin manifest such as `.claude-plugin/plugin.json` | Pi does not read one. A package is an npm or git directory |
| Approval rules | Pi does not ask for approval before a tool call. A permission gate is an extension |

## Files another runtime owns

Kind-spec requirement 1. Pi reads no other runtime's configuration. The one
shared surface is the cross-tool `.agents/skills` convention.

| Path | Owner | Read by Pi as | Gated |
|---|---|---|---|
| `$HOME/.agents/skills/` | Cross-tool | Skills | No |
| `.agents/skills/` in the project and its ancestors | Cross-tool | Skills | Project trust |

`AGENTS.md`, `AGENTS.override.md` and `CLAUDE.md` load as context files. They
are instructions, not components, the rule every kind follows.

### Evidence and composition

**Pi declared evidence** is a Pi-owned file that loads, at any depth:

- `.pi/settings.json`, `.pi/mcp.json`
- `.pi/extensions/<name>.ts`, `.pi/extensions/<name>.js`, and
  `.pi/extensions/<dir>/index.ts`, `index.js` or `package.json`
- `.pi/skills/**/SKILL.md`, `.pi/prompts/<name>.md`
- `.agents/skills/**/SKILL.md` (ADR-0058)

A segment that starts with `.` or is `node_modules` below `skills/` disqualifies
a `SKILL.md`, as Pi's walk skips it. `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` and
`.pi/themes/` are not evidence. They are instructions and display settings, not
components.

A repository that holds only `.agents/skills/` now declares Cursor, Codex, Devin
and Pi agents: four BOMs. That is ADR-0058's stated cost, applied to a fourth
reader.

### Node keys

| Mode | Requirement |
|---|---|
| Declared | None new. A key is relative to the scan root |
| Installed | The agent directory keys under `pi/`, the project under `project/`, `$HOME/.agents` under `agents/`, and any other path under home under `home/`. The project label starts at the repository root, so an ancestor's `.agents/skills` keys under `project/` too. An npm or git package's files key under its install location, which is inside the agent or project directory |

A local package outside every labelled root keys by its absolute path, the
normalizer's last resort for every kind.

## Coverage

`partial` at both composition sources (ADR-0046), for one shared reason and
several per-source ones.

| Gap | Affects | Source | Closable by parsing? |
|---|---|---|---|
| An extension calls `pi.registerMcpServer()`, or returns skill and prompt paths from `resources_discover` | MCP servers, skills, prompts | both | **No.** Runtime registration in code. The multi-agent spec rule applies: a kind that permits code-authored composition cannot claim `complete` |
| `-e`, `--skill`, `--prompt-template`, `--no-extensions`, `--no-mcp`, `--approve` | any | installed | **No.** Invocation flags leave nothing on disk |
| A declared npm or git package with no install on disk | package contents | installed | **No.** Pi installs it at the next start. Recorded as a gap in the scan |
| A user npm package installed under the global npm root | package contents | installed | **Partly.** Locating the root needs `npm root -g`. Recorded as a gap in the scan |
| An npm or git package declared in a repository | package contents | declared | **No.** Installing needs the network. Recorded as a gap in the scan |
| A settings entry or a local package outside the repository | resources | declared | **No.** A repository scan does not read the machine. Recorded as a gap in the scan |

- **Both sources are `partial`** because of the first row. An extension is
  ordinary Pi configuration, and an extension can add servers and skills that
  no file records.
- The other rows are per-scan checks. Each records a gap only when the scan
  meets it.

## Identity

- **Every surface maps into the closed set**: `mcp_server`, `plugin`, `skill`,
  `command`. No new component type and no new source ecosystem.
- **An extension is a `plugin`** (ADR-0082). It has no identity of its own.
  Inside a package its `openaca:component_path` starts with the package.
- **A package is a `plugin`** (ADR-0082):

| Source | `name` | `purl` | `openaca:identity` |
|---|---|---|---|
| npm | `@scope/tools` | `pkg:npm/%40scope/tools@1.2.0` | `plugin/npm/@scope/tools` |
| git on `github.com` | `owner/repo` | `pkg:github/owner/repo@<commit>` | `plugin/git/github.com/owner/repo` |
| git elsewhere | `<host>/<path>` | none | `plugin/git/<host>/<path>` |
| local | `package.json` `name`, else the directory's name | none | none |

- A skill or prompt template inside a package takes the package's private
  namespace, `skill/plugin/npm/@scope/tools/<name>`, as a bundled skill does
  in a Claude Code plugin.
- `openaca:install_source` holds the declaration: `npm:<spec>` for npm and
  `git+https://<host>/<path>[@<ref>]` for git. A local package has none.
- `openaca:plugin_scope` is `user` or `project`, the settings file that
  declared the package.

## Posture

Posture rules read composed components, as for every kind. The exception is
project trust, a posture-only surface that declares no components.

| Rule | Applies | Why |
|---|---|---|
| `insecure_transport` | **Yes** | `mcp.json` carries `url`, and Pi accepts `http://` |
| `mcp_header_credential` | **Yes, with one change** | Pi expands `${NAME}` and bare `$NAME`, and runs a whole value that starts with `!` as a shell command. `_REFERENCE` recognises `${NAME}` only, so the change adds bare `$NAME` and a leading `!` as references |
| `mutable_install` | **Yes, with one change** | The MCP branch keys on launch specs. A package keys on its declaration: an `npm:` spec without an exact version, or a git source without a full commit, is mutable. `is_mutable_reference` gains the `npm:` scheme |
| `skill_capability` | **Yes** | The same `SKILL.md` with `allowed-tools`. Pi's shell tool is `bash`. Pi 1.5.2 does not read `allowed-tools` itself; the rule reports the declaration, as for Codex and Cursor |
| `project_trust` | **Yes** | `<agent>/trust.json` records trusted directories. A trusted directory loads its extensions, which run as code in the Pi process |
| `mcp_auto_approve` | **No** | Pi has no approval surface |
| `command_policy_allow` | **No** | Pi has no command allowlist |
| `api_endpoint_override` | **No** | It matches literal Anthropic settings keys in a file Pi does not have |

`insecure_transport` and `mcp_header_credential` derive their manifests from
the graph's `mcp_server` refs, as Cursor's, Codex's and Devin's do. A server
with `enabled: false` is reported by neither, as for Codex.

## Deferred and out of scope

### Out of the first pass

| Surface | Why it waits | Cost of skipping |
|---|---|---|
| Settings filter patterns (`!glob`, `+path`, `-path`), `pi config` toggles, and package filter objects | Pi applies `minimatch` globs, exact paths and per-package deltas in a fixed order. A partial port would be wrong in silent ways | A resource the user turned off is reported. No resource carries `openaca:enabled`, so the BOM does not claim it is on |
| `-builtin:mcp` in `extensions`, and an extension that replaces `/mcp` | Rare. It turns off `mcp.json` as a whole | The servers in `mcp.json` are reported though Pi does not connect them |
| Standalone Markdown skills (`<root>/<name>.md` with a `description`) | Pi's docs prefer the `SKILL.md` form | Such a skill is missing from the BOM |
| Two skills, extensions or templates with one name | Pi keeps the first by precedence and warns. The tail is not ordinary | Both are reported |
| The `.gitignore`, `.ignore` and `.fdignore` files inside a skill, extension or prompt root | Rare in an agent directory | A file one of them excludes is reported |
| Transitive dependencies of an npm package, recorded in `<agent>/npm/package-lock.json` | Attributing a hoisted dependency to its package needs the lockfile graph | A vulnerable dependency of an npm package does not match. The package's own `package.json` dependencies are reported, as for a plugin of another kind |
| A repository that is itself a Pi package (`package.json` with a `pi` key) | A `pi` key is one field in a shared file, and the conventional directory names are generic | Scanning a Pi package's source repository emits no Pi BOM |

### Not shipping

- **Instruction and display files**: `SYSTEM.md`, `APPEND_SYSTEM.md`,
  `AGENTS.md`, `CLAUDE.md`, themes and keybindings. They are not components.
- **`models.json` and `auth.json`**: model endpoints and credentials. They
  declare no components. `auth.json` holds secrets the scan does not read.

### Out of scope

- **A program built on the Pi SDK**, which sets its own agent directory and
  extensions in code.
- **Windows** (ADR-0069).
- **Policy evidence.** `collect_policy_evidence` does not accept `pi` yet.

## Decisions

| # | Decision | Detail | Record |
|---|---|---|---|
| 1 | Register `pi`: singleton, `partial` at both sources, the declared evidence set, refuse `--config-dir`, gate the installed project layer on trust | [Roots](#roots), [Project trust](#project-trust), [Coverage](#coverage), [Evidence and composition](#evidence-and-composition) | [ADR-0081](../adrs/0081-pi-agent-kind.md) |
| 2 | An extension and a package are each a `plugin` | [Identity](#identity) | [ADR-0082](../adrs/0082-pi-extensions-and-packages-are-plugins.md) |

## Verification

| Invariant | Test |
|---|---|
| Evidence is Pi-owned files that load, and `.agents/skills` | `tests/test_agent_kinds_pi.py` |
| The installed agent directory honours `$PI_CODING_AGENT_DIR`, and `--config-dir` is refused | `tests/test_agent_kinds_pi.py` |
| `mcp.json` merges by name, overrides keep the user source, invalid entries are not components, and the alias is the configured name | `tests/test_graph_build_pi.py` |
| Packages carry the installed npm version, the git commit, the scope and the declaration; contents nest under the package | `tests/test_graph_build_pi.py` |
| The project layer follows `trust.json` and `defaultProjectTrust` | `tests/test_graph_build_pi.py` |
| Installed keys carry no home path | `tests/test_graph_build_pi.py`, `tests/test_e2e.py` |
| Every allowlisted rule fires on a Pi-owned surface, and the two rule changes hold | `tests/test_posture_pi.py` |
| An installed Pi agent reaches advisory matching, posture and an emitted BOM | `tests/test_e2e.py` |

The behaviour of a running Pi is unverified. Every claim is read from the
shipped code.

## Open questions

None.
