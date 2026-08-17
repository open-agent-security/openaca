# Multi-Host Support — Design

Goal: full Cursor host support across every OpenACA scan surface — MCP
servers, Skills, Plugins (both formats), Subagents, and Commands, in
both repo and endpoint mode — through a host abstraction that makes
each later host a bounded module rather than a cross-cutting change.

## Motivation

OpenACA today has one host wired end-to-end: Claude Code. Config-root
discovery, install-model resolution, manifest parsing, posture rules, and
BOM identity all assume Claude Code's shapes, sometimes explicitly
(`tools/scan.py`'s `$CLAUDE_CONFIG_DIR` resolution) and sometimes by
omission (six call sites hardcode `runtime_hosts=["claude-code"]`; two
posture rules guess host from manifest shape instead of being told).

Cursor is the first new host. Codex and GitHub Copilot follow. The goal is
a seam narrow enough that adding either of those later is a new host
module plus fixtures, not edits scattered through the parser registry,
the posture orchestrator, and the CLI. The parser registry, posture
orchestrator, and CLI genuinely need no per-host edits — they read
`HostAdapter.manifest_registry`/`posture_rule_ids`/`--host` uniformly.
The one place this isn't literally zero-edit: `tools/graph_build.py`'s
MCP/Skill *pattern* dispatch also needs to recognize a host's pattern
*strings*, via a small, centralized allowlist
(`_MCP_REGISTRY_PATTERNS`/`_SKILL_REGISTRY_PATTERNS`) that a genuinely
new pattern shape needs one line added to. Smaller and more centralized
than "scattered through the pipeline," but not literally zero. This
narrower contract — a new
host's own module, plus one centralized allowlist line per new MCP/Skill
pattern string it introduces — is the accepted design. "Module plus
fixtures" in this Motivation and the Goal section below should be read
with that in mind, not as a zero-edit promise.

Cursor's actual surface is wider than "MCP servers only" — it also ships
a Plugins/Marketplace system, Hooks, and Agent Skills (the same
agentskills.io spec Claude Code uses). The surface audit below is
verified against Cursor's current docs, fetched directly rather than
assumed, and cross-referenced against the actual parser code in
`tools/parsers/`.

## Goal

Introduce a host abstraction so that every capability OpenACA has today
for Claude Code — scan repo, scan endpoint, graph build, BOM emit, OSV
matching, posture rules, exposure report, remote sync — works for Cursor,
and so that adding Codex/Copilot later is a new host module plus
fixtures, not edits scattered through the pipeline. (See the Motivation
section above: true for the parser registry, posture orchestrator, and
CLI; the graph's MCP/Skill pattern dispatch needs one centralized
allowlist line per new pattern shape.)

This isn't bounded by Claude Code parity. A component kind Cursor (or a
later host) popularized and Claude Code never had is a roadmap candidate
on its own merits — the goal is the widest agent-component surface across
hosts, not matching whatever Claude Code happens to support. See the
surface audit's Deferred entries below: each states what specifically
blocks it from V1, not "Claude Code doesn't have it."

## Surface audit: what Cursor actually supports

### At a glance

| Component | Claude Code | Cursor | Verdict |
|---|---|---|---|
| MCP servers | Full support | Full support | Reuse |
| Plugins | Full support | Full support, 2 formats | New surface |
| Skills | Full support | Full support, same spec | Reuse |
| Hooks | Full support | Full support, own vocabulary | Partial |
| Commands | Full support, stable | Exists, being deprecated | Reuse |
| Subagents | Full support | Full support + reads Claude's dir | Partial |
| Rules (`.mdc`) | No equivalent | Full support | Deferred |
| `AGENTS.md` | Not read | Read, cross-tool convention | Deferred |
| Extensions | No equivalent | VS Code-fork marketplace | Deferred |

**Reuse** = an existing Claude-side parser transfers with light changes.
**Partial** = the shape transfers but a real divergence needs its own
handling. **New surface** = Cursor's format is genuinely distinct, no
shortcut. **Deferred** = not V1 — the goal is the widest agent-component
surface across hosts, so a surface Claude Code doesn't have is a roadmap
candidate, not a rejection. Each Deferred entry below states the specific
thing blocking it from being a current feature, so promoting it later is
a scoped decision, not a re-litigation.

### MCP servers — Reuse

The closest-aligned surface of the nine.

- **Claude Code**: `~/.claude/.mcp.json`, `<project>/.mcp.json`, inline
  `mcpServers` in any `settings.json` scope, plugin-bundled `.mcp.json`,
  `claude_desktop_config.json`. Per-server fields: `command`, `args`,
  `env`, `url`, `type` (inferred), `disabled`, `autoApprove`.
- **Cursor**: `~/.cursor/mcp.json` (global), `<project>/.cursor/mcp.json`
  (project). Per-server fields: `command`, `args`, `env`, `envFile`
  (stdio-only, Cursor-only), `url`, `headers` (Cursor-only), `auth` OAuth
  object (Cursor-only), `type`. Per Cursor's own docs
  (cursor.com/docs/context/mcp), `type` is inferred/optional in
  practice, matching what real Cursor config examples do — Cursor's
  documentation is internally inconsistent here (a reference table lists
  `type` as required for stdio servers, but the accompanying example
  JSON for a stdio server omits it); treat it as inferred, same as
  Claude's field. Remote entries are `url`-keyed with no documented
  `type` enum (the docs name stdio/SSE/Streamable HTTP as transports
  but never enumerate remote `type` values), so transport is inferred
  from the fields present, never validated against an enum. Global-vs-
  project precedence for a same-named server is undocumented; the
  design reports both as separate occurrences (union/presence) rather
  than asserting a merge or shadow rule the docs don't state.
- **Claude-only**: in-file `disabled` and `autoApprove` — the exact input
  the `mcp-auto-approve` posture rule keys on. Cursor's auto-approval is
  UI toggle state, invisible to a file scan; this is a coverage gap on
  Cursor, not a parser gap.

The MCP parser's launcher-classification logic (npx/uvx/bunx/docker
argv parsing, PURL construction, remote-URL normalization) is
host-agnostic, and both of its entrypoints take `runtime_hosts` — the
whole surface is one parser serving every host, with a
redaction-fixture pass for Cursor's `auth`/`envFile` fields (the
existing forbidden-name pattern in the upload contract already blocks
them by name).

**Endpoint mode**: reuses the same parser — no new parser work.
Cursor's composition reads `~/.cursor/mcp.json` (global) the same way
Claude's reads its settings-scoped `mcpServers`, plus
`<project_root>/.cursor/mcp.json` when a project root is given.

### Plugins — New surface

The surface where Cursor most exceeds an "MCP-only" scope. Cursor's
Marketplace (launched Feb 2026) is a real, install-time plugin system —
but it is two competing formats, and neither matches Claude's.

- **Claude Code**: one format, `.claude-plugin/plugin.json`. Required:
  `name`. Optional: `version`, `mcpServers`, `dependencies[]`, `skills`,
  `hooks`, `commands`, `agents` (path overrides). Install-state tracking:
  `settings.json.enabledPlugins{}` ∩ `plugins/installed_plugins.json`
  lockfile (version, installPath, gitCommitSha, scope — introduced in
  ADR-0007, unchanged by its superseding chain 0007→0018→0019→0031;
  current code in `tools/parsers/claude_install.py`). Worth naming
  directly: ADR-0007 §7 already coined the term "host adapter," but for
  a narrower, deliberately future-gated concept — programmatic/SDK-based
  config (OpenAI Agents SDK, Codex TOML), deferred until Tier-3
  AST-aware extraction exists. This design's `HostAdapter` is a
  different, present-day thing: Tier-1 declarative manifests, which
  ADR-0007 §7 itself already treated as in-scope without needing an
  adapter ("Cursor's MCP config" is listed there as an already-parsed
  declarative surface). Same term, two different scopes — noted here so
  a future reader doesn't conflate this design's adapter with the
  Tier-3-gated one.
- **Cursor**: two formats. **Agent Plugins** is an open, cross-vendor
  standard (agent-plugins.org; steering committee: Amazon, Cursor,
  Microsoft, OpenAI, Vercel — Anthropic is not a listed adopter). Its
  root `plugin.json` schema is closed to 10 top-level fields, of which
  only `$schema` and `name` are required (`version`, `description`,
  `author`, `homepage`, `repository`, `license`, `keywords`, `extensions`
  are all optional) — verified directly against the spec repository
  (`agentplugins/agent-plugins-spec`), not assumed. `$schema` must equal
  `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` (version
  string varies); this is the detection signal. Detection validates the
  complete URL shape — any single version segment, exact
  `/plugin.schema.json` tail — never an origin-prefix match: bare
  `plugin.json` files are checked at any depth, so a loose prefix would
  classify unrelated same-origin documents as plugins. Only **two** bundled
  surfaces are portably standardized across every compliant client:
  **Skills** (`skills/` dir, same `SKILL.md`-per-subdirectory convention
  as everywhere else in this design) and **MCP servers** (`mcp.json` at
  the plugin root). Commands, agents, hooks, and rules are explicitly
  **not** part of the v1 portable contract — the spec's own words: they
  "remain too client-specific for a stable portable contract... until
  their formats converge." A client that wants to bundle those uses its
  own `extensions.<reverse-domain-namespace>` block (e.g. `com.cursor`)
  with an undocumented, client-private shape — not something this
  design parses. **Cursor Plugins** is the native format,
  `.cursor-plugin/plugin.json`, requiring only `name`; optional fields
  include `version`, `author`, `homepage`, `repository`, `license`,
  `keywords[]`, `logo`, `rules`, `agents`, `skills`, `commands`, `hooks`,
  `mcpServers`, and `variables` (a JSON-Schema-declared block of
  user-configurable plugin settings — no Claude equivalent). Default
  bundled-component subdirectories — verified against Cursor's plugin
  reference docs — are `skills/`, `rules/`, `agents/`, `commands/`,
  `hooks/hooks.json`, `mcp.json`: the same defaults
  `tools/parsers/claude_plugin_root.py`'s `walk_plugin_root` already
  assumes for Claude with a single exception — the default bundled MCP
  manifest filename, root `mcp.json` where Claude's walker reads
  `.mcp.json`, so the shared walker takes the default filename as
  format context rather than hardcoding either — which is what makes
  this surface highly reusable rather than a from-scratch parser. Install location:
  `~/.cursor/plugins/local/` (dev-linked, confirmed) or
  marketplace-installed under
  `~/.cursor/plugins/cache/<marketplace>/<name>/<commit-sha>/` (layout
  observed directly and corroborated by cursor/plugins#136, not
  officially documented); enabled/disabled state lives in Cursor's
  VS Code-inherited `state.vscdb`, an undocumented SQLite state database,
  not a readable lockfile or settings key (confirmed by checking —
  Cursor's own community forum explicitly names `state.vscdb` as the
  only place enable state lives, not a JSON file). This has real
  endpoint-mode consequences below.

**This is genuinely new work, not reuse — and V1 covers both formats,
not one.** Scoping to only **Cursor Plugins** and deferring **Agent
Plugins** on the reasoning "since Anthropic isn't an adopter, parsing it
doesn't unlock any Claude-side reuse today" would repeat the same
Claude-parity logic already rejected for Rules/`AGENTS.md`/Extensions
above — no more valid here. Nothing blocks parsing both. Detection is
unambiguous, not guessed — each format
has its own manifest location (root `plugin.json` with `$schema` pointing
at `agent-plugins.org` for Agent Plugins, `.cursor-plugin/plugin.json`
for Cursor Plugins) — and both are real, installable via Cursor's
Marketplace today. Agent Plugins' bundle walking is narrower than Cursor
Plugins' — only its `skills/` and `mcp.json` get parsed, per the
portable-contract limit above; its `extensions` block is not walked.

Agent Plugins is worth a second look beyond "cover it too": it's a
genuine cross-vendor standard (Amazon, Microsoft, OpenAI, Vercel sit on
its steering committee), so a parser for it may not belong to the Cursor
host module at all — it could earn the same host-agnostic treatment as
Skills (ADR-0018) if other hosts adopt it. That's not resolved here.

**Identity: both formats reuse Claude's own unqualified `plugin/<name>`
scheme — not a Cursor- or format-specific namespace.** The plausible
alternative — `plugin/cursor/<name>`, "role-qualified per ADR-0042" —
is wrong: tracing the real identity
computation in `tools/identity.py`'s `canonical_component_identity`, a
`plugin` ref only gets a real, cross-BOM `openaca:identity` when
`extra["marketplace"]` is set (from install-state tracking Cursor
doesn't have in repo mode either) *or* when `component_identity` already
has 2+ slashes — and `plugin/cursor/<name>` has exactly 2, which trips
that fallback and grants cross-BOM identity to a component backed by
nothing but a self-declared `name` field. That's precisely what
ADR-0042 forbids ("local aliases and display names never become
cross-BOM identity"), and it would additionally bake host into
identity — forbidden separately by this design's own
host-is-provenance-never-identity rule. The correct scheme costs
nothing: Cursor plugins
(either format) use the exact same `plugin/{name}` string Claude's own
repo-mode plugins already use. With no marketplace info available for
either host in repo mode, `canonical_component_identity` correctly
returns `None` for both — occurrence-local, differentiated only by
`runtime_hosts`/`bom-ref`, exactly like every other surface in this
design. No new code needed in `tools/identity.py`.

**Bundled Rules and `variables` are not walked**, for either plugin
format — Rules has no identity model yet (same content-evidence-kind
gap as the top-level Rules surface below), and `variables` is plugin
configuration metadata, not a discoverable component.

**Endpoint mode: dev-linked and marketplace-cached plugins,
presence-only — never enabled/disabled state.** Endpoint-mode Cursor
Plugin support scans two roots: `~/.cursor/plugins/local/*/`
(dev-linked) and
`~/.cursor/plugins/cache/<marketplace>/<name>/<version>/`
(marketplace-installed; version directories gated on the
`.cache-complete` download sentinel — the layout is observed directly
and community-corroborated (cursor/plugins#136), not officially
documented). Each bundle parses
through its own `.cursor-plugin/plugin.json` (or Agent Plugins' root
`plugin.json`) the same way repo mode does, with the same
native-wins-per-directory precedence; a manifest-less cached bundle
(observed in the official marketplace despite the docs requiring a
manifest) seeds a synthesized presence-only self ref named from its
`<name>` directory segment, marked `extra["manifest"] = "absent"`.
Every entry found is reported with no `enabled`/`active` property,
since that state genuinely isn't observable — it lives in the
undocumented `state.vscdb`, and cache presence explicitly does not
imply enabled (disabling retains the cache, per cursor/plugins#136).
The cache path's `<marketplace>` segment is recorded as non-identity
provenance in `extra["cursor_marketplace_dir"]`, never
`extra["marketplace"]` — a directory name is not verified
install-state, and the `marketplace` key would mint qualified
cross-BOM identity. This is not the same claim endpoint mode makes for
Claude Code (`enabledPlugins ∩ installed_plugins.json`, a real
active/inactive signal); the two are not directly comparable and
downstream consumers/renderers should not conflate "found on disk"
with "confirmed active," matching how `mcp_auto_approve` already skips
Cursor rather than assert a state the config surface doesn't expose.
Bundled `mcp.json` manifests of seeded plugins (both roots) join
Cursor's endpoint posture collection, mirroring how Claude's collector
derives plugin install roots from its refs. Every entry from both
roots also carries `extra["scope"] = "user"` — a location fact
derived from `~/.cursor` being the user config root with no
project-level Cursor plugin install path, not a claim about
enabled-state — so `openaca:plugin_scope` renders and reports the
same way it does for Claude's lockfile-recorded scope.

### Skills — Reuse

The highest-leverage reuse case in the whole comparison, and the one
place the host-agnostic skill-identity call — made in ADR-0018 (May
2026), carried forward through ADR-0019 into the current ADR-0031 (`skill/
<name>`, no host in the identity), for reasons unrelated to Cursor —
already fits. (ADR-0018 itself is `status: superseded`; citing it
directly without the chain would point a future reader at a stale
document even though the conclusion it established still holds.)

- **Claude Code**: `.claude/skills/<name>/SKILL.md` (project),
  `~/.claude/skills/` (personal), plugin-bundled `skills/<name>/`. Spec:
  agentskills.io — `name` (required, ≤64 chars), `description` (required,
  ≤200 chars), `license`, `compatibility`, `metadata` (incl. version),
  `allowed-tools`. Identity: `skill/<name>[@<metadata.version>]`.
- **Cursor**: four discovery roots vs. Claude's two — `.agents/skills/`,
  `.cursor/skills/` (project), `~/.agents/skills/`, `~/.cursor/skills/`
  (global). Same agentskills.io SKILL.md format, plus a Cursor-only
  `paths` frontmatter field (glob-scoped auto-load). `.agents/skills/` is
  **not Cursor-owned** — Codex reads it too.

**Reuse `claude_skill.py` wholesale.** Add discovery roots:
`.cursor/skills/`, `~/.cursor/skills/`, and — independent of whether
Cursor support ships, since it's genuinely cross-tool — `.agents/skills/`
and `~/.agents/skills/`. A skill found only under `.agents/skills/` is
real: Cursor and Codex read it, Claude Code does not. Represent that via
`runtime_hosts`, don't merge it away.

**Endpoint mode**: the `~/.cursor/skills/` and `~/.agents/skills/`
global roots above are the endpoint-mode discovery roots directly —
repo mode's "global" root and endpoint mode's install-root skill
discovery are the same directory, so no separate endpoint-specific
logic is needed beyond calling the already-host-parameterized
`claude_skill.py` from Cursor's `seed_endpoint`, mirroring how Claude's
`_seed_endpoint()` already walks its own install-root `skills/`.

### Hooks — Partial

"Partial" here means two distinct things, one about reuse and one about
scope:

1. **Partial reuse** (the table's verdict): the `{event: [entries]}`
   array shape and the hash-based identity approach transfer from
   Claude's hook parsing, but the event-name vocabulary is a disjoint
   string set (camelCase, with Cursor-only events Claude has no
   equivalent for) — so the shared walk must be format-parameterized
   (vocabulary + identity label as inputs), not reused as-is. It is
   neither a clean Reuse nor a from-scratch New surface.
2. **Partial coverage** (what this design ships): only
   **plugin-bundled** Cursor hooks are in scope — bundled
   `hooks/hooks.json` and inline `plugin.json.hooks`, parsed as part of
   Plugins support. **Standalone** hooks (`<project>/.cursor/hooks.json`
   and the user/enterprise/team scopes below) are deferred as a scope
   decision, consistent with Claude's own coverage: standalone Claude
   hooks aren't a top-level scan surface either, and promoting the
   surface should land for both hosts together (see "Out of scope").

- **Claude Code**: plugin `hooks/hooks.json`, inline
  `plugin.json.hooks`, per-scope `settings.json.hooks`
  (user/project/local/managed). Events (PascalCase): `PreToolUse`,
  `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`. Identity:
  hash of `{type, command, prompt}` → `claude-hook/<kind>:<digest>` — not
  slot-based (ADR-0013).
- **Cursor**: `<project>/.cursor/hooks.json`, committed to repo;
  `~/.cursor/hooks.json` (user); enterprise paths
  (`/Library/Application Support/Cursor/hooks.json` on macOS,
  `/etc/cursor/hooks.json` on Linux) and a dashboard-synced Team scope,
  with documented precedence Enterprise → Team → Project → User
  (cursor.com/docs/agent/hooks).
  Events (camelCase): `preToolUse`, `postToolUse`, `postToolUseFailure`
  (Cursor-only), `beforeSubmitPrompt`, `stop`, plus richer agent-lifecycle
  events (`thinking`, `compaction`, turn-completion) with no direct Claude
  equivalent. Relative command paths resolve from the `hooks.json` file's
  own location — same convention as Claude's plugin `hooks.json`.

The `{event: [entries]}` shape and hash-based identity approach transfer
conceptually, but the event-name vocabulary is a disjoint string set and
needs its own table, not a shared constant. For plugin-bundled Cursor
hooks — the one Cursor hook surface in scope — the shared
walk is format-parameterized rather than Claude-reused: fixtures and
parsing use the camelCase vocabulary, and Cursor-parsed entries carry a
`cursor-hook/<kind>:<digest>` occurrence label beside Claude's
`claude-hook/...`. Both labels are occurrence-local display metadata,
never cross-BOM identity: canonical hook identity is plugin-private
(`tools/identity.py` routes `hook` refs through
`_plugin_private_identity`, which does not read this string). The exact
per-entry field set and the bundled standalone-file wrapper are
verified against Cursor's plugin reference: the standalone file uses
the same `{"hooks": {...}}` envelope as Claude's, and entries carry
`command` (required) and `matcher` (optional) with no `type` field —
absent fields the permissive shared walk already tolerates.

**Independent-of-Cursor bug found along the way**: `insecure_transport.py`
and `mcp_auto_approve.py` each carry a private `_infer_hosts()` that
guesses `["claude-code"]` whenever a manifest has a `mcpServers` key,
instead of reading the host that's already threaded through every other
rule via `ComponentRef.extra["runtime_hosts"]`. Cursor's `mcp.json` also
uses `mcpServers` — today, scanning one through either rule silently
mislabels it. Fix this regardless of sequencing; it's not gated on
anything else in this design.

### Commands — Reuse

Exists on both hosts. Cursor's own product direction is shrinking it
(it ships a `/migrate-to-skills` command steering users toward Skills),
but it's a real, currently-shipping surface, not a rejected one — the
same "widest agent-component surface, not Claude Code parity" posture
this design applies everywhere else.

- **Claude Code**: `.claude/commands/**/*.md` (project),
  `~/.claude/commands/` (personal), plugin-bundled. Identity-only
  matching (`claude-command/[<owner>/]<name>`), no version.
- **Cursor**: `.cursor/commands/*.md` — project-level confirmed
  (`cursor.com/changelog/1-6`: "Commands are stored in
  `.cursor/commands/[command].md`"). **No personal/global path is
  documented anywhere found**, and — unlike Subagents — **no evidence
  Cursor reads `.claude/commands/`** for compatibility; the "for
  compatibility" cross-read is specific to Subagents and Skills in
  Cursor's own docs, not commands. Scope V1 to project-level only:
  `**/.cursor/commands/**/*.md`, matching the monorepo-nesting
  convention already used for every other Cursor pattern in this
  design (no direct evidence against nesting either; see Needs
  Verification).

**Reuse `claude_command_agent.py`'s `parse_file`/`enumerate_dir`
wholesale — but it needs a `runtime_hosts` parameter it doesn't have
today.** Every other reused parser in this design
(`mcp_json.py`, `claude_skill.py`) already accepts `runtime_hosts`;
`claude_command_agent.py` currently has no such parameter at all —
`command`/`agent` refs it emits carry no `runtime_hosts` key in `extra`.
Add it, defaulting to `["claude-code"]` to preserve every existing call
site's output exactly. No cross-host file sharing is documented for
Commands (unlike Subagents), so this is a simple independent-occurrence
pattern: a new `.cursor/commands/*.md` registry entry, pre-bound to
`runtime_hosts=["cursor"]`, dispatched through the same registry-driven
mechanism MCP/Skills already use — no bespoke precedence logic needed.

**Endpoint mode**: project-level only, same reasoning as repo mode —
Cursor's `seed_endpoint` walks `<project_root>/.cursor/commands/` the
same way Claude's endpoint mode already walks
`<project_root>/.claude/commands/`. No install-root/personal-level scan
is added, since no such path is documented; guessing one (e.g.
`~/.cursor/commands/`, following the pattern every other Cursor surface
uses) would be exactly the capability-guessing this design avoids
elsewhere. Revisit if a personal Commands path is ever confirmed.

### Subagents — Partial

The one surface that breaks the registry mechanism's modeling
assumption (one file, one pattern match, one occurrence) — it gets a
dedicated resolver instead. Verified directly against
Cursor's subagents documentation: the compatibility read is
**unconditional**, not version-gated or opt-in, with an explicit
precedence rule.

- **Claude Code**: `.claude/agents/**/*.md` (project), `~/.claude/agents/`
  (personal), plugin-bundled `agents/*.md`. Frontmatter: `name` — OpenACA's
  parser doesn't validate beyond this today. Identity:
  `claude-agent/[<owner>/]<name>`.
- **Cursor**: primary paths `.cursor/agents/*.md` (project),
  `~/.cursor/agents/` (global); compatible-alternative paths
  `.claude/agents/`/`.codex/agents/` (project) and
  `~/.claude/agents/`/`~/.codex/agents/` (global) — confirmed
  unconditional, not gated. **Precedence**: "When multiple locations
  contain subagents with the same name, `.cursor/` takes precedence over
  `.claude/` or `.codex/`." Codex isn't a registered host in this
  project (no `HOSTS["codex"]` entry), so only the Cursor/Claude half of
  this rule is directly actionable here; a Codex-only override is
  invisible to this scan the same way any unregistered host is.
  Frontmatter: `name`, `description`, `model` (inherit/fast/`<id>`),
  `readonly`, `is_background`.

Every other surface in this comparison assumed one config file per host
(two `mcp.json`s, two skill directories). Subagents don't: a repo's
`.claude/agents/*.md` can be loaded by Cursor from the exact same file,
with no `.cursor/agents/` copy ever created — but only when Cursor has
no same-named override of its own. Reuse the file-parsing in
`claude_command_agent.py`, but host detection for this one surface can't
be "which registry entry matched" the way it is everywhere else. The
actual model, precedence-aware:

- For every `.claude/agents/<rel>.md`, check whether a same-**relative-
  path** `.cursor/agents/<rel>.md` exists at the same ancestor level
  (the directory containing both `.claude` and `.cursor`).
  - **No override** → one occurrence,
    `runtime_hosts: ["claude-code", "cursor"]` — Cursor genuinely reads
    this exact file.
  - **Override exists** → two occurrences, each single-host: the
    `.claude/agents/<rel>.md` file gets `runtime_hosts: ["claude-code"]`
    only (Cursor never reads it in this case — its own copy wins), and
    `.cursor/agents/<rel>.md` gets `runtime_hosts: ["cursor"]`.
- A `.cursor/agents/<rel>.md` file with no `.claude/agents/<rel>.md`
  counterpart is unambiguous: `runtime_hosts: ["cursor"]` only.

**Modeling choice, stated explicitly:** "same subagent" is matched by
*relative file path* under the agents directory, not by frontmatter
`name:`. Cursor's docs say "same name" without specifying which; path
identity is the more literal, verifiable reading and the one a human
author would actually produce by creating an override file. A future
name-based collision (different paths, same frontmatter `name:`) is not
modeled and would be treated as two independent files.

This can't be expressed through the `manifest_registry`/
`registry_pattern_matches` mechanism the rest of this design uses — that
matcher classifies one path in isolation, and this needs to inspect a
*sibling* path first. Subagents get a dedicated resolver, on both the
manifest-accounting and graph-placement sides (mirroring the existing
"the two mechanisms must never diverge" principle without going through
the shared pattern matcher).

**Endpoint mode makes this bigger, not smaller.** The compatibility read
applies at both the global level (`~/.cursor/agents/` vs.
`~/.claude/agents/`) and the project level
(`<project_root>/.cursor/agents/` vs. `<project_root>/.claude/agents/`,
since endpoint mode also takes `project_root`) — the same
precedence-aware resolver above, run twice per scan instead of once,
once for each scope.

### Rules, `AGENTS.md`, Extensions — Deferred

Not current features, but not rejected either. Each is real and popular
on its host; what blocks each from V1 is a specific, nameable thing — not
"Claude Code doesn't have it":

- **Rules** (`.cursor/rules/*.mdc`, Cursor-only, no legacy `.cursorrules`
  format confirmed): frontmatter is `alwaysApply`/`description`/`globs` —
  no name, no version, no source, so it doesn't fit the composition
  graph's identity model (name + version + source) as it exists today.
  That's the same shape problem this repo's schema already anticipates:
  `type: exposure` and `type: config` are reserved but rejected in V0 PRs
  pending methodology docs. A future "content evidence" component kind —
  covering Rules, `AGENTS.md`, and Claude's own `CLAUDE.md` alike, since
  none of the three have stable identity — is the natural home once that
  methodology lands. This isn't Cursor-specific and doesn't need solving
  by this design; it's a prerequisite this design should name, not work
  around.
- **`AGENTS.md`**: same shape problem as Rules, and genuinely cross-tool
  (Cursor's own docs cite it as a Rules alternative; Codex and others read
  it too) rather than a Cursor feature. Folds into the same future
  content-evidence kind — not a per-host special case, and not blocked on
  Cursor support specifically.
- **Extensions** (Cursor's VS Code-fork marketplace): a different
  blocker — not identity shape, but relevance. Most extensions are
  general editor tooling (themes, linters) with no agent-supply-chain
  story; a minority genuinely are agent-relevant (chat panels, MCP
  tooling). Flagging "this extension is agent-relevant" without a
  reliable, non-guessed signal violates the no-capability-guessing
  constraint. Future feature, gated on finding that signal (a marketplace
  category, a manifest field) — not gated on host popularity or on
  Claude Code lacking a literal equivalent.

## Architecture: where the seam is

Repo mode and endpoint mode need different seams. Repo mode's discovery
is a registry-driven filesystem walk over the *scanned tree*, so a
host-tagged manifest registry slots in cleanly — that's already the
shape. Endpoint mode is not that shape: a host's endpoint surface is
install-model resolution (settings scopes, install-state lockfiles,
install roots and their precedence) that no filesystem-pattern registry
can express. So the two modes get separate seams: repo mode's registry
dispatch, and endpoint mode's per-host composition functions.

**The host adapter.** Everything that varies by host lives in one
frozen `HostAdapter` record, registered in `tools/hosts.py`: the host
id (the same string carried as `runtime_hosts` provenance and emitted
as `openaca:agent_host`), detection (config root exists on disk,
nothing fancier), config-root resolution (explicit-override aware), the
host-tagged manifest registry (patterns paired with parsers pre-bound
to stamp that host), endpoint posture-manifest collection, the
host's posture-rule allowlist, and an optional endpoint composition
function. The registry dict is the single source every selection,
dispatch, and display path reads; the exact field signatures live in
`tools/hosts.py`.

**Repo mode**: host selection means "does this manifest pattern belong
to a selected host," never "is this host installed on the scanning
machine." A repo containing only `.cursor/mcp.json` scans correctly
with zero dependency on whether `~/.cursor` exists locally; `detect()`
plays no role in repo mode. Discovery is unified through the one
registry on both its sides — manifest accounting and graph placement
resolve parsers by reading `HostAdapter.manifest_registry` through the
same pattern matcher, never hardcoded per-host branches. The
unification is deliberately bounded: it covers surfaces that place
uniformly (a direct child of the target regardless of host — MCP
servers, Skills, Commands, and a plugin's own top-level manifest); a
plugin's *bundled* components are walked programmatically by a
parameterized root-walker, not through the registry, and
placement-varying surfaces stay outside. A genuinely new pattern
*string* costs one line in a small centralized allowlist — smaller and
more centralized than a per-host branch, not zero. Two selected hosts
claiming the identical pattern string is a hard error, never silent
arbitration.

Cursor's config root is `~/.cursor`, with `--config-dir` as the only
override: Cursor documents no variable that relocates the whole root —
`CURSOR_CONFIG_DIR` scopes only the CLI's `cli-config.json`
(cursor.com/docs/cli/reference/configuration) — so honoring it as a
root override would misread its documented meaning, and no env analog
to `$CLAUDE_CONFIG_DIR` exists to honor.

**Endpoint mode**: each host owns a composition function
(`tools/endpoint_seeds/<host>.py`), bound into its adapter; endpoint
graph construction is a loop over an explicit `{host_id: config_root}`
map resolved in the CLI layer, every host's call layering its own
children onto one shared target node. Subagents are the one deliberate
exception, seeded by a single cross-host pass after the per-host loop:
a shared-file Subagent occurrence can span hosts (Cursor reads Claude's
agents directory), so no single host's seed can own it without either
duplicating the node or tagging it with a selection-order-dependent
host list. That pass names each selected host's `agents/` directory
explicitly from the root map — endpoint config roots are arbitrary
paths, so nothing is rediscovered from directory basenames — with
Claude Code's default root supplying Cursor's compatibility read when
Claude Code isn't selected; project scope uses the repo-style
dot-directory resolver, whose convention genuinely holds inside
projects. The graph's root-sensitive stages are multi-root: node-key
normalization labels each root distinctly (`endpoint/` stays Claude
Code's label, preserving existing keys; other hosts get
`endpoint-<host_id>/`; the auxiliary discovery roots get their own
labels), manifest-name indexes stay per host root with `project_root`
entries taking precedence and no cross-host name fallback (a name
present only under another host's root does not resolve), and MCP
launch-dependency resolution uses the root that seeded each node.
`project_root` stays a single cross-host concept — it names a project
directory, not a host. Claude Code's composition keeps its single-host
behavior, with two carve-outs: its `agents/` walks live in the
cross-host pass, and its settings/lockfile machinery is *not*
generalized into a shape other hosts must implement — endpoint mode is
fundamentally different per host, and that difference stays inside each
host's own module.

Cursor's composition covers remote MCPs (global + project), direct
skills (all four roots — see the Skills section), project commands, and
plugins from both on-disk roots in both manifest formats with no
enabled-state property (see the Plugins section). It has no
lockfile-backed install-state to resolve — every surface is a direct
file read, and the one surface that would need install-state (Plugins)
is scoped to presence-only for exactly this reason.

Adding Codex/Copilot later = a new `HostAdapter` instance, its own
composition function if endpoint mode is in scope for it, plus
fixtures. A host with no composition function simply contributes
nothing in endpoint mode until its adapter is filled in —
repo-mode-only support is a valid intermediate state. Nothing in the
graph, matcher, or BOM modules branches on host — confirmed by reading
them.

**Detection & CLI default.** `detect()` = "config root exists on disk,"
uniformly for every host, gating only endpoint mode's default
selection. `openaca scan repo` takes a repeatable `--host`; **omitted
defaults to every registered host** (pattern selection has no
machine-state dependency). `openaca scan endpoint` takes the same
option; **omitted defaults to every *detected* host** (a machine with
only `~/.claude` scans exactly as before Cursor support existed; a
machine with both roots scans both, zero flags). Explicit `--host` for
an undetected host is a hard error — with one carve-out: an explicit
`--config-dir` override supplies the root directly and is accepted
without a `detect()` check (the supplied directory *is* the root).
`--config-dir` is only meaningful when exactly one host is selected;
combined with a multi-host selection it is a hard error, since one
directory can't be two hosts' roots. Text and JSON output both state
which hosts were scanned, per-host, including the endpoint card's
host-surface label and config rows.

## Identity

`openaca:identity` stays host-free — this was already ADR law before this
design started (ADR-0029, carried forward by ADR-0042: `openaca:agent_host`
is "provenance/execution context... not part of `openaca:identity`") and
nothing in the surface audit forces a change. The same npm MCP server
installed under both hosts on one laptop is **one logical component, two
occurrences**: same `openaca:identity` (e.g. `mcp-server/npm/<pkg>`),
different `bom-ref` (different `source_manifest`), each carrying its own
single-element `runtime_hosts`. `_agent_host()` in `tools/bom.py` already
refuses to emit a singular `openaca:agent_host` when `runtime_hosts` has
more than one entry — evidence this occurrence-per-host shape, not a
merged multi-host component, is the intended model. `bom-ref` uniqueness
is unaffected without any host-specific handling, since `source_manifest`
already differs across hosts.

**Subagents are the one confirmed exception.** Because Cursor can load
`.claude/agents/*.md` directly with no second file — confirmed
unconditional, with a same-name-override precedence rule — a single
occurrence legitimately needs `runtime_hosts: ["claude-code", "cursor"]`
when no override exists, or two single-host occurrences when one does.
See the Subagents section above for the full precedence-aware resolver;
this is the multi-host case the rest of the model treats as out of
scope by construction (two separate files → two separate occurrences),
and it stays scoped to Subagents alone — nothing else in this design
has a confirmed cross-host file-sharing behavior.

## Out of scope (deferred, explicitly)

**Hooks (standalone, `.cursor/hooks.json`) stay deferred** — a scope
decision, not an unknown: every location and the precedence order are
documented (see the Hooks section), so nothing blocks promotion except
prioritization. The decision is consistency with Claude's own scope:
standalone Claude hooks aren't a top-level registry entry either today
(they're only reachable via plugin bundling in repo mode, or
`settings.json.hooks` in endpoint mode), so Cursor's standalone
`hooks.json` staying deferred isn't a new gap relative to Claude, just
an unchanged one — and promoting the surface should land for both
hosts together. **Plugin-*bundled* hooks are in scope** as part of
Plugins support (both formats' `walk_plugin_root`-style bundling walks
`hooks/hooks.json`/inline `hooks` for Cursor Plugins; Agent Plugins
doesn't bundle hooks at all per its portable-contract limit).

Also deferred: Rules / `AGENTS.md` / Extensions for V1 (each is a real
future-feature candidate with a named unlock: a content-evidence
component kind for Rules/`AGENTS.md`, a non-guessed relevance signal for
Extensions — not host popularity or Claude Code parity); Codex/Copilot
themselves (future host modules — this design's job is making them
cheap, not building them); any remote consumer's per-host ingest change
(coordinated separately in its own repository, referenced not
implemented here); Cursor's actual global-vs-project
precedence semantics for MCP/Skills/Commands (undocumented — the
design chooses simple union/presence, revisit if Cursor ever documents
merge semantics; Subagents is the one surface with a *confirmed*
precedence rule, see above); Cursor
plugin enabled-state (still unobservable — `state.vscdb`, see the
Plugins section; marketplace-cached plugin *presence* is
now in scope); a personal/global Commands path for Cursor
(undocumented, see the Commands section).

Not deferred: the **Agent Plugins** open standard, and **endpoint
mode** for every surface in scope (MCP, Skills, Plugins, Subagents,
Commands) — both would be easy to mistake for deferrals given how much
of this section they border, so stated explicitly. Endpoint mode's one
remaining limitation is the Plugin enabled-state gap described in the
Plugins section, not a scope exclusion.
