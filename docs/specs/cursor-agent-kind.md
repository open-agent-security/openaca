# Cursor Agent Kind — Surface Audit

Companion ADRs: [0052](../adrs/0052-cursor-agent-kind.md) (the kind),
[0053](../adrs/0053-repo-surface-descriptor.md) (repo-mode mechanism),
[0054](../adrs/0054-per-kind-root-override.md) (root override as a per-kind
capability).

Mechanism lives in [Multi-Agent Support](multi-agent-support.md). This document
is the per-kind spec that mechanism requires — Cursor's own config paths,
manifest shapes, and precedence rules — and it is written so a future
implementer of a third kind can copy its shape without reading Cursor material.

It meets the four requirements at
[Multi-Agent Support § What a kind spec must contain](multi-agent-support.md#what-a-kind-spec-must-contain):
every path the runtime reads including another runtime's, an observability gap
per surface, a composition source per claim, and a split verdict for anything
deferred.

## What "Cursor" means here

Cursor ships **two programs that share one configuration tree**:

| | Program | Distribution |
|---|---|---|
| Desktop app | a VS Code fork | its own installer |
| CLI | `agent`, with `cursor-agent` as a legacy alias — the on-disk binary is always named `cursor-agent` | a separate download; installing the desktop app does **not** install it |

They are **one kind**, not two. Cursor's own deployment documentation states:
*"Whether running in the desktop app or as a standalone CLI, Cursor agents have
the same security controls... The CLI is the same agent with a different
interface."* Both resolve their configuration from the same root, read the same
`mcp.json`, the same skill roots, and the same subagent roots. Under ADR-0044's
test — same surface, same schema — that is one kind.

The two are **not identical**, and the differences are surface additions rather
than divergences. Each is marked *desktop only* or *CLI only* at the point it
appears. Neither program's surface is treated as the whole.

## Evidence standard

Cursor's documentation is where each claim started. It is not where any claim
ended, because documentation drifts from behavior and a parser written against
drift reports components the runtime never loads.

Every claim was therefore checked against the **shipped implementation of both
programs** — the desktop application bundle and the CLI package, whose JavaScript
is minified but retains its string literals, so the paths each program actually
reads are recoverable — and cross-checked against installed endpoints.

**Audited builds:** desktop **3.16.17** (VS Code 1.128.0 base), CLI package
**2026.08.11-e8db854**, on macOS, 2026-08-25/26.

These versions are the provenance for every implementation-derived claim below —
command tier order, traversal depth limits, the extensibility flag and its
default, the plugin cache layout and its completion sentinel, the split-home
behavior of remote windows, and the per-surface relocation variables. **None of
them is reproducible from this repository**: the evidence is a proprietary bundle,
not a fixture, so a reviewer who wants to re-check them needs those builds in hand.
Treat each as verified-at-that-version and re-audit when Cursor ships a major
change, rather than as a contract Cursor owes us.

**Where documentation and implementation disagree, the implementation governs.**
Several documented facts did not survive that check, and the ones that matter are
marked *(docs disagree)* at the point they apply — so a future reader who checks
Cursor's docs, finds a contradiction, and reaches to "correct" this spec knows the
difference was deliberate.

Two constraints on reading this document as a whole:

- **It describes a Cursor version, not Cursor forever.** Several surfaces here —
  the plugin cache layout, its completion sentinel, the team-managed hook
  directory — are undocumented implementation detail that can change without a
  release note. Where a rule rests on implementation rather than documentation,
  the surface section says so.
- **Nothing here assumes a platform.** Every surface in scope is home- or
  project-relative; see [Platforms](#platforms).

## Surface audit

A **surface** is anywhere a runtime reads configuration from. Most surfaces here
yield components — the closed set of `mcp_server`, `plugin`, `skill`, `hook`,
`command`, `agent` — but several yield none and are listed anyway, because "what
does this runtime read" is the question a kind spec has to answer, and a surface
that yields no component still has to be understood well enough to be
deliberately skipped. Rows that yield no component say so in **Scope**.

Two verdict columns, not one. **Reuse** is about our parsers; **Scope** is about
what ships. They diverge often enough that collapsing them hides real work — a
surface can share Claude Code's file format exactly and still need its own walk.

| Surface | Claude Code | Cursor | Reuse | Scope |
|---|---|---|---|---|
| MCP servers | `.mcp.json`, inline in settings | `.cursor/mcp.json`, same `mcpServers` shape; adds `envFile`/`headers`/`auth`, drops `disabled` | Parser as-is | **In** |
| ↳ approval state | `autoApprove`, a field on the server entry | a separate `permissions.json` — see [Precedence](#precedence) | New collector, same rule | **In** — posture |
| Skills | `.claude/skills/`, bundled | Same `SKILL.md` spec; four root names × two scopes, recursive, adds `paths`; a vendor root and a built-in denylist excluded | Parser + new roots | **In** |
| Subagents | `.claude/agents/` | `.cursor/agents/` **and Claude Code's**, gated. No `.codex/agents/`, and **no `.agents/agents/`** — `.agents/` holds skills only | Parser + precedence resolver | **In** |
| Commands | `.claude/commands/`, project + user | `.cursor/commands/` **and Claude Code's**; narrower extensions, **inverted** precedence | Parser + own walk | **In** |
| Plugins | One manifest format, own install lockfile | **Two** manifest formats via an ordered candidate list; two install roots in the first pass, six more later | New realization path | **In** |
| Plugin hooks | `hooks/hooks.json` | Same envelope, camelCase events, richer entries | Parser as-is | **In** |
| CLI config | No equivalent | `cli-config.json`, `<project>/.cursor/cli.json` | — | **Out** — yields no components; its `permissions` list is a **separate** approval system from `permissions.json`, not another location for it |
| Standalone hooks | Not a repo-mode surface | `.cursor/hooks.json`, four scopes, two remote; also reads Claude Code's `settings.json` | — | Deferred — schedule |
| Instruction files | `CLAUDE.md` | `.cursor/rules/**/*.mdc`, `.cursorrules`, `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` | — | **Out** — not configuration, yields no components |
| Extensions | No equivalent | VS Code-fork marketplace | — | Deferred — relevance signal |
| `sandbox.json` | No equivalent | Execution sandbox policy | — | Out — yields no components, no matching rule |

## Config root

The config root is `.cursor` under the user's home directory on every platform.
There is **no whole-root relocation variable** and no analogue to
`$CLAUDE_CONFIG_DIR`.

What exists instead is **per-surface relocation**, which is the rule that
matters:

| Surface | Honors | Falls back to |
|---|---|---|
| `permissions.json` | `CURSOR_CONFIG_DIR`, then `XDG_CONFIG_HOME` (as `<xdg>/cursor`) | `<home>/.cursor` |
| CLI `cli-config.json` | `CURSOR_CONFIG_DIR`, then `XDG_CONFIG_HOME` | `<home>/.cursor` |
| `projects/` | `CURSOR_DATA_DIR` | `<home>/.cursor` |
| `sandbox-policies/` | `CURSOR_SANDBOX_POLICY_DIR` | `<home>/.cursor` |
| **Everything else** — `mcp.json`, skills, subagents, commands, plugins, `hooks.json`, `sandbox.json` | nothing | `<home>/.cursor` |

*(Docs disagree — they describe `CURSOR_CONFIG_DIR` as relocating a config
directory, and `XDG_CONFIG_HOME` as Linux-only; it is honored everywhere.)* A
scanner that treats `CURSOR_CONFIG_DIR` as a config-root override will look in
the wrong place for every composition surface. A scanner that ignores it will
miss a relocated `permissions.json`. Both halves of that rule are load-bearing.

**Home is not always one directory.** In a remote window — SSH, WSL, or a dev
container — the desktop app resolves two different homes: MCP user config and
`permissions.json` resolve against the **remote** home, while commands,
subagents, and skills resolve against the **local** one. A scan of either machine
alone sees part of the composition.

Endpoint roots are arbitrary paths: a root is never reconstructed from a
directory basename. Cursor's root is **not** relocatable by an OpenACA flag —
see [CLI surface](#cli-surface).

## CLI surface

Cursor's config model decides what a scan invocation can honestly name. This
section is that consequence, stated as the CLI contract.

### Where a kind is selected

`--kind` is a knob on the endpoint commands — `scan endpoint`, `bom endpoint`,
`remote sync endpoint` — and on no others.

Repo mode has no kind selector. It fans out over every registered kind, so a
repository whose only Cursor-owned evidence is `.agents/skills/` yields a Cursor
agent alongside whatever else the tree declares. That asymmetry is mechanism
rather than oversight: repo mode is parameterised over a surface descriptor while
endpoint mode is forked per kind ([ADR-0053](../adrs/0053-repo-surface-descriptor.md)),
and only endpoint mode has a single root that two kinds could both claim.

### Selection semantics

| Invocation | Resolution |
|---|---|
| neither flag | every installed kind whose own default root exists |
| `--kind X` | X only. If X's default root is absent, **zero** agents for X — never a fallback to another kind's root |
| `--config-dir` without `--kind` | rejected. Ambiguity is never silently arbitrated |
| `--kind` naming no registered kind | rejected, listing the kinds that exist |
| `--kind cursor --config-dir …` | rejected — see below |

`--kind` is optional. It becomes mandatory only alongside `--config-dir`.

### Cursor's root is not relocatable

An installed Cursor's composition is gathered from **three** places, not one:

| | Where | Moved by `--config-dir`? |
|---|---|---|
| 1 | Cursor's own root — `mcp.json`, `skills/`, `commands/`, `agents/`, `plugins/` | yes |
| 2 | `permissions.json` | no — [its own axis](#config-root): `CURSOR_CONFIG_DIR`, then `XDG_CONFIG_HOME` |
| 3 | [Another runtime's skill roots](#files-cursor-reads-that-another-runtime-owns) — `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills` | no — they were never under Cursor's root |

A flag naming a root could move only the first. Group 2 answers to a different
rule that the flag is deliberately not part of, and group 3 sits outside the root
entirely, so there is nothing for the flag to move. An override that names a root
and gets one group of three is not an override — it is a scan stitched from two
homes, reported as one agent, with nothing in the output distinguishing it from a
correct one.

So the general rule: **a root override is honest only for a kind where naming a
root fully specifies the target.** Claude Code qualifies — its runtime relocates
the whole root via `$CLAUDE_CONFIG_DIR`, and once a root is named nothing in its
composition consults the home directory again. Cursor does not, and the
difference is structural rather than tidiness: *for Claude Code home is a
default; for Cursor home is an ingredient.* Two of Cursor's three groups live
there by Cursor's own design, so replacing the `.cursor` path does not replace
them.

Cursor therefore declares no relocatable root, and `--config-dir` alongside
`--kind cursor` is rejected. An installed Cursor scan always resolves
`<home>/.cursor` and the invoking user's home.

**The unserved need this leaves.** Scanning a Cursor tree that is not the
invoking user's home — a copied config, a mounted image, a CI cache, another
account on a shared machine — is unsupported. Serving it correctly means one
override that moves every home-derived group together and settles its precedence
against `CURSOR_CONFIG_DIR`. That is a design, not a flag, and it is not in this
pass; recorded here so a future implementer starts from the shape rather than
from the half-fitting flag.

### What the invocation produces

Text output prints one card per discovered agent. Four things about Cursor's card
follow from this spec rather than from the invocation:

- **`coverage` reads `partial` at both composition sources and cannot read
  anything else.** `partial` is the declared ceiling and observed evidence only
  lowers it ([Coverage](#coverage) enumerates what is unseen). The user-facing
  consequence: the component count will not match Cursor's own installed view,
  and the smaller number is not derivable offline.
- **Posture output is kind-dependent by design.** `api_endpoint_override` never
  reports for Cursor — see [Posture rule applicability](#posture-rule-applicability).
  A rule absent from a Cursor card is a declared inapplicability, not a gap in
  rule coverage.
- **Per-agent BOM output is named by kind**, so a Cursor document lands beside a
  Claude Code one rather than overwriting it. Cursor is a singleton kind, so its
  agent reference carries no agent-id segment.
- **A dry-run remote sync reflects kind selection identically to a scan.** The
  rules above are one contract across all three endpoint commands, not three
  similar ones.

### What the CLI cannot express

Two coverage gaps are CLI-shaped rather than surface-shaped, which is why they
appear here and not under [Out of scope](#out-of-scope):

- **One project root per invocation.** The project flag is singular, while
  Cursor's project roots are per workspace folder and never just the first. A
  multi-root workspace scan sees only the root it was pointed at.
- **Split-home remote windows.** One invocation reads one filesystem; no
  combination of flags reaches the other host. The gap is
  [out of scope](#out-of-scope) rather than unaddressed.

## Surfaces in scope

Throughout: **config root** is `.cursor` under the user's home directory
([relocation rules](#config-root)); **project roots are per workspace folder**,
never just the first; **†** marks a root behind
[the extensibility flag](#the-extensibility-flag), which defaults to on.

**This section is the first pass, and it is deliberately not everything Cursor
does.** The bar for inclusion is *would omitting this make the inventory wrong on
an ordinary installation* — not *is this reachable*. Cursor has a long tail of
rarely-populated roots and transient states; handling them all before shipping
anything would trade a correct common case for a complete uncommon one.
Everything held back is recorded in
[Deliberately out of the first pass](#deliberately-out-of-the-first-pass) with
what it costs, so the boundary is a decision rather than an oversight.

### Where each surface loads from

| Surface | Project roots | User roots | Traversal | Accepts |
|---|---|---|---|---|
| MCP servers | `.cursor/mcp.json` | `<root>/mcp.json` | file | `mcpServers` map |
| Skills | `.cursor/skills/`, `.agents/skills/`, `.claude/skills/`†, `.codex/skills/`† | same four under `<root>/` | recursive | `SKILL.md` per directory |
| Subagents | `.cursor/agents/`, `.claude/agents/`† | same two | recursive, depth 10 | `.md` `.mdc` `.markdown` |
| Commands | `.cursor/commands/`, `.claude/commands/`† | same two | recursive, depth 10 | `.md` `.txt` |
| Plugins | — | `plugins/local/`, `plugins/cache/` | per bundle | two manifest formats |
| Plugin hooks | bundled `hooks/hooks.json` | same | per bundle | `{version, hooks{}}` |
| Approval policy | `.cursor/permissions.json` | `<root>/permissions.json` | two files, concatenated | JSONC; allowlists, instructions |

Commands carry two caveats. Their paths are **documented nowhere** — no reference
page exists, and the roots above come from the implementation. And absence proves
nothing, because the directories are created lazily on first use.

Directories a reader expects and will **not** find: `.codex/agents` *(docs
disagree — they list it among six subagent roots, but neither program references
it; `.codex/skills` is real, and the subagents table looks copied from the skills
table)*, `.agents/agents` and `.agents/commands` (`.agents/` holds skills only),
and any Cursor-owned plugin install lockfile.

### Precedence

First, what counts as *the same thing*: both subagents and commands are keyed by
**relative path under their root**, not by any frontmatter `name` — which for
subagents defaults to the filename anyway. So `a/deploy.md` and `b/deploy.md` are
two commands, and a name collision between them is not a collision at all.

Given that, every surface resolves real conflicts differently, and two resolve
them in **opposite directions**. Any future attempt to unify "the precedence
walk" gets one wrong.

| Surface | Rule | Winner |
|---|---|---|
| MCP servers | merge, by server name | **project** |
| Subagents | first-wins; scope outranks directory order | **project**, then `.cursor` over `.claude` |
| Commands | last-wins, over team → global → plugin → workspace → personal | **user** |
| Plugins | ordered manifest candidates, first that parses and names the plugin | `.cursor-plugin` |
| Approval | arrays **concatenate** across the user and project files, field by field | both contribute |

**Approval is the trap, and not in the direction an earlier draft claimed.** A
scanner that treats the project file as authoritative drops the user file's
entries; one that treats either as winning drops the other's. Cursor's reference
is explicit: *"When both exist, Cursor concatenates the arrays inside every field.
Per-user and per-repo entries combine; one does not replace the other."*

An earlier version of this spec described user scope as first-wins per field. That
was over-generalized from a single sampled code path: the bundle contains **more
than one** permissions mechanism, and a grep finds both first-wins and
concatenating shapes near `mcpAllowlist`. Since the implementation does not
resolve to one answer, the documented contract governs — and it is also the safe
direction for a security scanner, because a superset of auto-approved entries
over-reports rather than misses one.

**`permissions.json` is JSONC**, not JSON — comments and trailing commas are
documented as supported, and the bundle carries JSONC parsing. A plain JSON loader
silently drops valid files.

### Exclusions

Two, both cheap enough that skipping them buys nothing.

`<root>/skills-cursor/` holds Cursor's
**vendor built-in skills** — Cursor's own documentation says *"Ignore anything in
~/.cursor/skills-cursor... managed automatically by the system."* It is a
**sibling** of `skills/`, not a variant spelling, and it is commonly populated
while the user root is empty. Inventorying it reports a couple of dozen vendor
skills as user composition on every Cursor endpoint scanned.

A plugin bundle **without its `.cache-complete` sentinel** is not inventoried.
Cursor treats such a directory as a cache miss and reinstalls rather than loading
it, so its contents are not composition. The state is transient and rare — it
would fail this section's own frequency bar — but the check is one file test, and
the alternative is reporting components the runtime will discard.

### Plugins

Two install roots, both under the config root:

| Root | Holds |
|---|---|
| `plugins/local/<name>/` | dev-linked plugins; symlinks followed |
| `plugins/cache/<marketplace>/<name>/<git-sha>/` | marketplace bundles, gated on `.cache-complete` |

Cursor has six further plugin roots, including two under Claude Code's tree;
they are rarer and land later — see
[Deliberately out of the first pass](#deliberately-out-of-the-first-pass).

**Manifest resolution.** Two formats, one ordered candidate list:

```
.cursor-plugin/plugin.json  →  .claude-plugin/plugin.json  →  plugin.json
```

First candidate that parses and names the plugin wins — not a merge. That Cursor
reads Claude Code's manifest is not incidental: multi-runtime plugins commonly
ship several of these side by side with the same name and version.

| Format | Manifest | Required | Bundled contract |
|---|---|---|---|
| Cursor Plugins | `.cursor-plugin/plugin.json` | `name` (`author` is an object) | everything below |
| Agent Plugins | root `plugin.json`, `$schema`-identified | `$schema`, `name` | **immediate children of `skills/` + root `mcp.json` only** |

The Agent Plugins spec excludes commands, hooks, agents, and rules as *"too
client-specific for a stable portable contract."* Client-private content sits under
`extensions.<reverse-domain>` and is not parsed.

Two **normative MUSTs** that a permissive reading would violate, and that differ
from how Cursor treats its own surfaces:

- **Unsupported versions are rejected, not parsed.** §5.2: *"If a client does not
  support the declared Agent Plugins version or an explicitly recognized
  compatible version, it MUST reject the plugin and SHOULD report the unsupported
  version."* So the `$schema` test is an **allowlist of supported versions**, not a
  URL-shape match — matching any version segment would parse a future 2.0.0
  manifest under 1.0.0 semantics. 1.0.0 is the released version; 1.1.0 is a
  Working Draft and is not supported.
- **The bundled `mcp.json` carries its own `$schema`.** §7.2.1: it *"MUST be a
  JSON object containing the required `$schema` and `mcpServers` fields, with no
  other top-level fields"*, and that `$schema` is `mcp.schema.json` — a
  **different** identifier from the manifest's, whose version must match it.
  §7.2.2 scopes failure to MCP alone: an invalid or mismatched `mcp.json` means
  *"the client MUST disable MCP for that plugin and continue loading other
  component types"*, so the bundle's skills still load.
- **Skill discovery is one level deep.** §7.1: *"Each immediate child directory
  containing a path named exactly `SKILL.md`... Clients MUST NOT recursively
  search deeper descendants."* This is the **opposite** of Cursor's own skill
  roots, which are walked recursively — the two must not share a walker.

**Folder discovery** fills in what a manifest does not name: `skills/`,
`agents/`, `hooks/hooks.json`, `commands/`, `rules/`, a root `SKILL.md`, and a
root `.mcp.json` **or** `mcp.json`. A manifest field **replaces** discovery for
that type — `"skills": "./custom-skills"` means `skills/` is not also scanned. A
manifest-less bundle is discovered entirely this way, which is common enough that
treating the manifest as required would drop real plugins *(docs disagree — they
call the manifest required)*.

**Bundled hooks** use the `{version, hooks{}}` envelope with camelCase events,
disjoint from Claude Code's PascalCase. Entries carry `command` (required) plus
`type`, `timeout`, `loop_limit`, `failClosed`, and `matcher`. **`matcher` is
`string | object`** — Cursor's field table and its own examples disagree — so
parse permissively and let the identity digest degrade rather than rejecting.

**Presence only.** No `enabled` or `active` property is emitted — absent, not
`false`. Enable state is a server call, so a cached bundle proves installation
and never activation.

## The extensibility flag

Cursor's reads of `.claude/*` and `.codex/*` sit behind one flag, shown in
settings as a "Rules, Skills, Subagents" toggle. **It defaults to on**, and every
read site treats unset as enabled.

It is not a config file — it lives in the editor's state database, whose location
varies by platform, profile, and portable mode. So a filesystem scan cannot
confirm it, and **scanning the gated roots is the right over-approximation**:
correct on a default installation, and at worst listing a component the runtime
does not load.

## Platforms

**No surface in scope needs platform branching.** Every one resolves under the
config root or a workspace folder, so a home-relative path covers macOS, Linux,
and Windows alike. The relocation variables in [Config root](#config-root) are
the only thing that moves any of them, and they are platform-independent.

Platform-specific paths exist in Cursor, but all of them sit outside this kind:
enterprise `hooks.json` (three system locations, needed when [standalone
hooks](#deferred-by-schedule) land), the editor state database holding
[the extensibility flag](#the-extensibility-flag), and the CLI binary itself.
Their locations are recorded in those sections rather than here, because a
reader only needs them alongside the surface that would use them.

One asymmetry to know if that changes: on Linux the editor state directory
honors `XDG_CONFIG_HOME` while the `~/.cursor` agent tree does not, so on a
machine with it set the two live in different trees.

## Deliberately out of the first pass

Real, and none of it changes the inventory on an ordinary installation. Each row
says what it costs to skip, so the boundary can be revisited with evidence rather
than re-argued.

| Held back | Cost of skipping |
|---|---|
| Codex built-in skill denylist — six names Cursor filters out of `.codex/skills` | Over-reports those six, and only for someone who also uses Codex. The list is hardcoded in Cursor's source and will drift, so a stale copy is its own wrong answer |
| Six further plugin roots: `plugins/marketplaces/`, `plugins/github-plugins.json`, `<workspace>/.cursor/settings.json` → `plugins.<key>`, `~/.claude/plugins/marketplaces/`, `~/.claude/plugins/installed_plugins.json`, a read-only temp cache | Misses plugins installed by those routes. The Claude Code install manifest is the one worth doing next: entries name **arbitrary absolute paths**, so it cannot be found by walking directories, and Cursor also reads `enabledPlugins` from Claude Code's settings — meaning the two kinds' plugin sets are not independent |
| The "first *qualifying* manifest" rule — a candidate that parses but declares neither components nor metadata falls through to the next | A bundle whose `.cursor-plugin/plugin.json` is metadata-only realizes from the wrong manifest |
| Sibling plugin versions | Cursor prunes to a keep-list after each load, so extra version directories are rare and transient |
| CLI config: `cli-config.json`, `<project>/.cursor/cli.json` | Nothing — they declare no components. Worth knowing only because `cli-config.json`'s `permissions.allow`/`deny` is a **different schema** from `permissions.json` and the two must never be merged |
| Remote windows splitting local and remote homes | A scan of either machine is partial. Unfixable from one host, so this is a coverage gap rather than deferred work |
| Admin-distributed configuration | Enterprise and team hooks, MDM-dropped `hooks.json` / `permissions.json`, Linux `policy.json`, and server-fetched team rules, commands, marketplaces, and MCP servers. On disk these are indistinguishable from user-authored files, so any finding phrased as a user's choice may misattribute — team plugins can be marked **Required**, which Cursor documents as *"always installed and cannot be uninstalled"* |

## Not shipping

Three different verdicts, and the distinction decides what unblocks each: one is
out of this layer entirely, one waits on a signal that does not exist yet, one
waits only on scheduling.

### Not configuration: instruction files

**Rules (`.mdc`), `.cursorrules`, `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`.**

Cursor reads all of these, so they are recorded here — but they are *instructions
given to a model*, not configuration declaring components. They name nothing,
version nothing, and resolve to no artifact, so there is no component for a BOM
to carry and no advisory that could match one.

That is a different verdict from the deferrals below: not "later", but **not this
layer**. If instruction content becomes interesting it will be as a content
evidence surface — a prompt-injection question, not a composition one — and it
would have to land for every kind at once, since `AGENTS.md` and `CLAUDE.md` are
cross-tool by construction.

### Deferred pending a relevance signal

**VS Code extensions.** The blocker is relevance, not identity. Cursor is a
VS Code fork with a full extension marketplace, and most extensions are themes
and linters. There is no non-guessed signal — a marketplace category, a manifest
field — that separates agent-relevant extensions from the rest, and inventorying
all of them would bury the components that matter.

### Deferred by schedule

**Standalone `.cursor/hooks.json`.** A genuinely separate surface with four
scopes resolving enterprise → team → project → user. Two are not locally
discoverable, and enterprise is the one place in this spec where a path is
platform-specific:

| Scope | Path |
|---|---|
| Enterprise | `/Library/Application Support/Cursor/hooks.json` (macOS), `/etc/cursor/hooks.json` (Linux), `C:\ProgramData\Cursor\hooks.json` (Windows) |
| Team | dashboard-synced, materialized under `managed/` in the config root |
| Project | `<workspace>/.cursor/hooks.json` |
| User | `<config root>/hooks.json` |

In a remote window the enterprise path is chosen by the **remote** machine's
operating system, not the local one.

It is deferred rather than dropped because Claude Code's standalone hooks are not
a repo-mode surface either; the two should land together so the shared walk is
designed once. Plugin-bundled hooks ship now because they come free with the
existing plugin descent.

## Coverage

**`partial` at both composition sources**, per
[ADR-0046](../adrs/0046-agent-coverage.md). `resolve_coverage` computes
`min(baseline, observed)`, so `partial` is the floor and evidence gaps cannot
lift it.

The two sources are `partial` for **different reasons**, which is the point of
resolving coverage per source rather than per kind. What Cursor loads that a scan
cannot see:

| Gap | Affects | Source | Closable by parsing? |
|---|---|---|---|
| Plugin enable state | plugins | installed | **No** — a server call |
| Runtime MCP registration | MCP servers | installed | **No** — no file written |
| Built-in MCP servers | MCP servers | installed | **No** — attributed to a path they are absent from |
| Manual per-server MCP disable | MCP servers | installed | **No** — UI state |
| Team rules, commands, marketplaces, MCP servers | several | both | **No** — server-fetched |
| The extensibility flag | skills, subagents, commands | both | **No** — editor state database, not a config file |
| The remote half of a split-home session | MCP, approval | installed | **No** — another host |
| Rules, `AGENTS.md`, `CLAUDE.md` | instructions | both | **Yes** — but [not this layer](#not-configuration-instruction-files) |

Declared is narrower only because nothing is installed — no plugin cache, no
runtime registration — not because a repository is better observed.

Only the last row closes by writing more scanner, and it is out of scope by
choice rather than by opacity. Everything above it is a network call or state
outside the filesystem a scan walks. An installed-but-disabled plugin is **not**
distinguishable offline from an installed-and-enabled one, which is what makes
plugins presence-only.

The MCP gap is routinely large: an endpoint declaring two servers in its user
file was observed loading thirteen.

## Identity

A **marketplace-installed** plugin — realized from
`plugins/cache/<marketplace>/<name>/<sha>/` — sets `extra["marketplace"]` to that
segment and takes `plugin/{marketplace}/{name}`, the same shape and qualifying key
Claude Code's marketplace plugins use. Because bundled identity is plugin-private,
this is also what gives every skill, hook, command, and agent inside the bundle an
identity; a plugin without one zeroes out its whole subtree.

The marketplace field must be on the ref **before** the plugin node is created:
`_add_child` finalizes identity on insert and children inherit the parent's
`_identity_namespace`, so stamping it afterwards leaves the subtree already
finalized against a namespace-less parent.

A **dev-linked** plugin under `plugins/local/<name>/` sets no `marketplace` and
gets no identity — nothing resolved it from a registry, and the directory name is
chosen by whoever made the symlink. Same answer Claude Code gives a repo-declared
plugin.

Neither form is ever `plugin/cursor/{name}`.

`canonical_component_identity` grants cross-BOM identity to any
`component_identity` carrying two or more `/` characters, with no provenance
check. `plugin/cursor/foo` has exactly two, so a host-qualified prefix would mint
a real cross-BOM identity backed by nothing but a self-declared `name` field.
`plugin/foo` returns `None`, which is correct: occurrence-local, differentiated
by `bom-ref`.

The marketplace directory segment is recorded as `extra["cursor_marketplace_dir"]`
and **never** as `extra["marketplace"]`, which is the qualifying key for verified
install-state.

Every Cursor surface maps into the existing closed sets: `mcp_server`, `plugin`,
`skill`, `hook`, `command`, `agent`. **No new component type and no new source
ecosystem are needed**, so no taxonomy ADR blocks this kind.

Two cosmetic artifacts are accepted rather than fixed. A `.cursor/commands/foo.md`
gets identity prefix `claude-command/`, and a Cursor bundled hook gets
`claude-hook/`. Both strings are occurrence-local display metadata that identity
resolution never reads; renaming either is a breaking cross-BOM identity change
affecting every existing BOM, for no functional gain.

## Files Cursor reads that another runtime owns

Requirement #1 of the kind-spec contract, and the reason it exists.

Cursor's interoperation with Claude Code is **substantially wider than either
project documents**, and it is not limited to content files — it extends to
Claude Code's own install and settings state.

| Path | Owner | Read by Cursor as | Gated |
|---|---|---|---|
| `.claude/agents/**` | Claude Code | Subagents | Yes |
| `.claude/skills/**` | Claude Code | Skills | Yes |
| `.claude/commands/**` | Claude Code | Commands | Yes |
| `.claude-plugin/plugin.json` | Claude Code | Plugin manifest, second candidate | No |
| `~/.claude/plugins/installed_plugins.json` | Claude Code | Plugin installs, at **arbitrary absolute paths** | Yes + second gate |
| `~/.claude/plugins/marketplaces/` | Claude Code | Marketplaces, imported | Yes + second gate |
| `.claude/settings.json`, `settings.local.json` | Claude Code | `enabledPlugins`, and hooks (CLI) | Yes |
| `.codex/skills/**` | Codex | Skills, minus a built-in denylist | Yes |
| `.agents/skills/**` | Cross-tool | Skills | No |
| `AGENTS.md` | Cross-tool | Instructions (CLI) | Deferred |
| `CLAUDE.md`, `CLAUDE.local.md` | Claude Code | Instructions (CLI) | Deferred |

Two rows carry consequences beyond their own surface. The **install lockfile**
means a Claude-installed plugin Cursor loads can live *anywhere on disk* — the
manifest names an absolute path per entry, so directory enumeration under
`~/.claude` is not sufficient. And **`enabledPlugins` in Claude Code's settings**
means Cursor's plugin composition depends on a file Claude Code owns, so the two
agents' plugin sets are not independent.

Under one-graph-per-agent these are not a merge problem. The same file is a node
in the Claude Code graph and a node in the Cursor graph, and in declared mode the
node key is byte-identical in both because the normalizer is scan-root-relative
with no root label — the property
[ADR-0045](../adrs/0045-agent-identity-keying.md) anticipates. In installed mode
the normalizer must learn the foreign root's label, or the path falls through to
an absolute path that is machine-specific and leaks a home directory into a
`bom-ref`.

Cross-reads are **composition, never evidence.** A tree containing only
`.claude/agents/` declares a Claude Code agent, not a Cursor one; treating a
compat-read path as evidence would emit a phantom near-empty Cursor BOM for every
Claude-only repository. `.agents/skills/` is the exception and *is* evidence,
because Cursor is currently the only registered kind that reads it — a claim to
revisit if a Codex kind lands.

## Posture derives from composition

**A posture collector reports what the composition graph composed. It does not
walk the filesystem to find out.**

This is an invariant, not a preference, and it is the one every kind inherits.
Cursor's composition applies a long list of exclusions — realized plugin
subtrees are closed to the direct walk, an Agent Plugins root nested under a
realized native root never realizes, a bundle whose manifest yields no self-ref
realizes nothing, gitignored candidates are dropped before selection, and the
portable format reads only a root `mcp.json`. A collector that re-walks has to
restate every one of them, and **each rule it misses reports a finding against a
component the agent never loads.**

That failure mode is not hypothetical: it produced a finding in nearly every
round of review on this kind, each one a different un-mirrored rule, because the
supply of rules to mirror is the whole composition builder. Deriving from the
graph makes the divergence unrepresentable — exclusions are applied once, by
composition, and posture inherits them by construction.

The rule has one honest exception. `permissions.json` declares no components, so
it appears in no ref and cannot be derived; it still walks. But it takes the one
thing it cannot derive — which subtrees composition already claimed — from the
graph's own `plugin` refs rather than recomputing them from a manifest walk. A
surface that yields no components may walk; it may not re-derive what
composition already decided.

Two corollaries worth stating, because both were learned the expensive way:

- **"Qualified" and "realized" are different sets, and only the second confers
  ownership.** A manifest that qualifies for discovery but produces no self-ref
  owns no subtree, excludes nothing, and contributes no posture surface.
- **Ignore filtering belongs before precedence selection, not after.** Filtering
  a winner after the fact drops it without reconsidering the candidate it beat,
  so an ignored higher-precedence file silently hides a valid lower-precedence
  one. The predicate must also compare like with like: a resolved path against
  an unresolved root fails its containment test and silently returns "not
  ignored" — the permissive direction.

## Posture rule applicability

| Rule | Applies | Why |
|---|---|---|
| `insecure_transport` | Yes | `.cursor/mcp.json` carries `url`; `http://` is the same exposure |
| `mutable_install` | Yes | Its MCP branch keys on launch specs. Its plugin branch keys on `gitCommitSha`, which only Claude Code's install lockfile sets, so Cursor's presence-only plugin refs correctly never trigger it |
| `skill_capability` | Yes | Same `SKILL.md` with `allowed-tools` |
| `mcp_auto_approve` | Yes — via `permissions.json` | See below |
| `api_endpoint_override` | No | Matches literal Anthropic settings keys in a settings file Cursor does not have |

`mcp_auto_approve` deserves its reasoning recorded, because the obvious
conclusion is wrong. Cursor's `mcp.json` has no auto-approve field, so a rule
reading only that file finds nothing, and an earlier draft of this spec excluded
the rule on exactly that basis. But `permissions.json` is readable and its
`mcpAllowlist` and `autoRun` keys are precisely the posture the rule exists to
report. Excluding it would have under-reported a real, file-declared exposure.

Three implementation constraints follow.

The rule reads Claude Code's settings shape today, so it must branch on manifest
shape rather than assume one.

The merge **concatenates** — both files contribute, neither replaces the other,
and the file is JSONC. Treating either as authoritative drops the other's entries,
and a plain JSON loader drops files with comments.

The user-scope file is **relocatable** by `CURSOR_CONFIG_DIR` or
`XDG_CONFIG_HOME` even though nothing else in Cursor's composition is. A
collector that derives it from the config root the rest of the scan uses will
miss a relocated file.

## Out of scope

- **Server-fetched configuration** — team rules, commands, marketplaces, MCP
  servers, and hooks distributed from a dashboard. These have no local path to
  read, and inferring them from a local artifact would assert configuration this
  scan cannot see. Their *materialized* form under the config root is in scope;
  see [Deliberately out of the first pass](#deliberately-out-of-the-first-pass).
- **Reconstructing the remote half of a remote window.** A scan sees one
  machine's filesystem. Where an SSH or dev-container session splits home
  directories, a scan of either side is honestly partial, and stitching the two
  would require reaching a host this scan has no access to.
- **`sandbox.json` and `sandbox-policies/`.** A real execution-policy surface,
  but it constrains the process rather than declaring components or MCP approval,
  so it fits neither the composition graph nor an existing posture rule. Revisit
  alongside a sandbox-posture rule.
- **`projects/`, `ai-tracking/`, `worktrees/`, and the VS Code `extensions/`
  directory** under the config root. None declares agent components; extensions
  are deferred for [relevance](#deferred-pending-a-relevance-signal) rather than
  being out of scope permanently.
