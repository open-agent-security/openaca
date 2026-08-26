---
id: 0052
title: Register Cursor as the second agent kind, presence-only for plugins
status: accepted
date: 2026-08-25
supersedes: null
superseded-by: null
---

## Context

ADR-0044 made the agent the BOM root and defined a *kind* as what reads a
composition. One kind shipped — Claude Code — and `tools/agent_kinds/__init__.py`
carries a registry whose comment reserves the second seat. Nothing in that
mechanism had been exercised by a runtime that is not Claude Code, so several of
its properties were asserted rather than demonstrated: that a second config root
works, that one runtime reading another's files resolves cleanly, that coverage
resolving per source matters in practice.

Cursor forces all three. It is widely installed alongside Claude Code, its
composition is almost entirely file-declared, and it reads Claude Code's
subagents, skills, commands, and plugin manifest.

What made this decision answerable now is an implementation audit rather than a
documentation review. Cursor's docs were the starting point; the shipped
implementations of **both** its programs — the desktop app and the separately
distributed CLI — were the authority. Several documented facts did not survive
that check, and each would have produced a wrong parser:

- Subagents are documented as loading from six roots including `.codex/agents/`.
  Neither program references `.codex/agents` at all; both read four roots, two
  behind a settings flag.
- The Claude and Codex compatibility reads are documented as unconditional. They
  sit behind an extensibility flag — which **defaults to on**, so the practical
  effect is close to the documented one but the mechanism is not.
- The plugin manifest is documented as `.cursor-plugin/plugin.json` and required.
  Both programs resolve an ordered candidate list that includes
  `.claude-plugin/plugin.json`, and a manifest-less bundle loads via folder
  discovery.
- `CURSOR_CONFIG_DIR` is documented as relocating a config directory. It moves
  `permissions.json` and the CLI's own config; it does **not** move `mcp.json`,
  skills, subagents, plugins, or hooks.

The audit also settled the question that decides how plugins are modeled. Plugin
enable state is not a file we had failed to find; it is a **server call**. Local
state holds only enabled ids, numeric and nameless. Cursor keeps no install
lockfile of its own and no `enabledPlugins` key in any Cursor-owned settings file.

A first draft of this decision was written from a single macOS endpoint, and that
framing hid real surface area: the CLI's additional roots, Cursor's reads of
Claude Code's install lockfile and settings, multi-root workspace scoping, and
three platform-specific path families. The rewrite states rules that hold on any
installation and marks what remains observation.

Full surface detail: [Cursor Agent Kind](../specs/cursor-agent-kind.md).

## Decision

**Cursor registers as a second agent kind**, `cursor`, singleton, with both
composition sources — installed and declared.

**The desktop app and the `cursor-agent` CLI are one kind, not two.** They share
a config root and read the same MCP, skill, and subagent roots; Cursor's own
documentation calls the CLI *"the same agent with a different interface."* Under
ADR-0044's test — same surface, same schema — that is one kind. The CLI's extra
surfaces are additions to that kind's spec, not evidence of a second one.

In scope: MCP servers, skills, subagents, commands, plugins in both manifest
formats, and plugin-bundled hooks. `permissions.json` ships as a **posture**
surface, not a composition surface — it declares no components, it says which
already-declared servers may run unattended, and modeling it as composition would
double-count every server.

**The first pass is scoped by what makes an ordinary installation wrong**, not by
what is reachable. Cursor has a long tail of rarely-populated roots and transient
states — six further plugin roots, a built-in denylist, cache sentinels, remote
split homes — and each is recorded with the cost of skipping it rather than
built. One exclusion does ship, because it fires everywhere: the vendor
`skills-cursor` root, which otherwise reports a couple of dozen Cursor-authored
skills as user composition on every endpoint.

**A marketplace-installed plugin carries the identity its cache path records.**
A plugin realized from `plugins/cache/<marketplace>/<name>/<sha>/` sets
`extra["marketplace"]` to that segment and takes `plugin/{marketplace}/{name}` —
the same shape and qualifying key Claude Code's marketplace plugins use — which
also restores identity to every skill, hook, command, and agent bundled inside it,
since bundled identity is plugin-private. A **dev-linked** plugin under
`plugins/local/<name>/` sets no `marketplace` and gets no identity: nothing
resolved it from a registry, and `<name>` there is chosen by whoever made the
symlink. This does **not** merge a plugin across kinds and is not intended to —
the same plugin from two registries has two identities, which is correct, because
they are different artifacts at different versions with different content hashes.

**Plugins are presence-only.** No `enabled` or `active` property is emitted —
absent, not `false`. A cached bundle proves installation, never activation. A
bundle without its `.cache-complete` sentinel is not inventoried, because Cursor
itself treats it as a cache miss and reinstalls rather than loading it.

**Coverage is `partial` at both sources**, for different reasons per source.

**Files Cursor reads that another runtime owns are composition, never evidence.**
They become nodes in Cursor's graph, keyed identically to their nodes in the
owning kind's graph. A tree containing only `.claude/` declares no Cursor agent.
`.agents/skills/` is the single exception and *is* evidence, because Cursor is
currently the only registered kind that reads it.

**Where documentation and implementation disagree, the implementation governs**,
and the disagreement is recorded in the spec rather than silently resolved.

## Alternatives considered

- **Emit `enabled: false` for cached-but-disabled plugins** — rejected because
  the distinction is not observable offline. The property would be fabricated,
  and a fabricated `false` is worse than an absent field: it reads as a verified
  negative.
- **Infer enable state from cache presence** — rejected on evidence. Disabling a
  plugin retains its cache, so presence and activation are independent. This is
  the specific inference `.cache-complete` invites and does not support.
- **Trust the documented six subagent roots and build a `.codex/agents` surface**
  — rejected because no code path reads it. Shipping a parser for a surface the
  runtime does not load would report components no agent has.
- **Treat `.claude/agents/` or `.claude/skills/` as evidence of a declared Cursor
  agent** — rejected because it emits a phantom, near-empty Cursor BOM for every
  Claude-only repository in existence. Cross-reads answer "what does this agent
  contain", not "does this agent exist" — the same separation
  `_DECLARED_EVIDENCE_PATTERNS` already draws by excluding a bare `mcp.json`.
- **Qualify plugin identity as `plugin/cursor/{name}`** — rejected because
  `canonical_component_identity` grants cross-BOM identity to any identity string
  with two or more `/` characters, with no provenance check. `cursor` is not a
  marketplace, so that form would mint a real cross-BOM identity backed by nothing
  but a self-declared `name`. Rejecting a **fabricated** slug is not the same as
  rejecting the **recorded** one — see the plugin-identity decision above.
- **Withhold `extra["marketplace"]` from Cursor entirely**, on the reasoning that a
  cache-path segment is a path rather than verified install-state — the earlier
  form of this decision, reversed. Measured against the shipped code it does not
  hold: Claude Code's slug is a key in a file Claude Code wrote after resolving
  from a registry, Cursor's is a directory Cursor wrote after resolving from a
  registry. Both are runtime-written records of a marketplace resolution and both
  are equally user-editable; the difference is encoding, not provenance. The cost
  was not cosmetic — bundled identity is plugin-private, so a plugin without one
  zeroes out everything beneath it. On one ordinary endpoint that was 9 plugins
  taking 46 skills, 4 hooks and 7 commands with them.
- **Model `permissions.json` as composition** — rejected because it declares no
  components. Every server it names is already declared in `mcp.json`; emitting
  both doubles the inventory.
- **Exclude `mcp_auto_approve` because Cursor's `mcp.json` has no auto-approve
  field** — rejected, and this reverses an earlier reading in the design. The
  premise is true and the conclusion does not follow: `permissions.json` is
  readable and its `mcpAllowlist`/`autoRun` keys are exactly the posture the rule
  reports. Excluding it under-reports a real, file-declared exposure.
- **Inventory `~/.cursor/skills-cursor/`** — rejected because it is vendor
  built-in content carrying `scope:"builtin"`, managed by Cursor's own sync
  writer, and Cursor's shipped documentation explicitly instructs tools to ignore
  it. It is a sibling of the user skills root, not a variant spelling — an
  installation commonly has the built-in root populated and no user root at all.
- **Ship Rules, `AGENTS.md`, and `CLAUDE.md` with this kind** — rejected because
  they are not configuration. Cursor reads all of them, but they are instructions
  given to a model: they name nothing, version nothing, and resolve to no
  artifact, so there is no component for a BOM to carry and no advisory that
  could match one. This is a different verdict from a scheduling deferral — not
  "later", but not this layer. If instruction content becomes interesting it will
  be as a prompt-injection question rather than a composition one, and it would
  have to land for every kind at once, since `AGENTS.md` and `CLAUDE.md` are
  cross-tool by construction.
- **Treat the CLI as a separate kind** — rejected. It shares the config root and
  reads the same MCP, skill, and subagent roots; its extra surfaces are additions,
  not a different composition. Splitting would emit two BOMs for one agent's
  configuration and double-count every shared component.
- **Scope this kind to the desktop app and defer the CLI** — rejected for the
  same reason, plus a practical one: a repository declaring `.cursor/` config
  gives no signal about which program will read it, so a desktop-only kind would
  under-report on every declared scan.

## Consequences

**Enables.** A second config root, a second declared-evidence set, and the first
real exercise of one file appearing in two agents' graphs. It also separates a
posture rule from the file it happened to read: MCP approval state is a per-server
field for Claude Code and a standalone file for Cursor, so `mcp_auto_approve`
stops being coupled to one manifest shape.

**Costs.** Cursor plugin inventory is systematically an over-approximation:
every cached bundle is reported whether or not it is enabled. Users comparing
OpenACA's count against Cursor's own "installed" view will see a discrepancy, and
the honest answer is that the smaller number is not derivable offline.

Two surfaces resolve conflicts in **opposite directions** — subagents are
first-wins with project beating user; commands are last-wins with user beating
project. Both are the runtime's real behavior. Any future refactor that unifies
"the precedence walk" across surfaces will get one of them wrong.

Scanning the gated `.claude`/`.codex` roots is a deliberate over-approximation:
the gate has no file representation, so we cannot know whether those components
load. Under-reporting was judged worse than over-reporting for a security tool,
but it means a Cursor BOM can list a skill the runtime never reads.

**Watch for.** Cursor ships fast. The marketplace cache layout and the
`.cache-complete` sentinel are undocumented — verified by reading the
implementation and confirmed on a real endpoint, but not contractual. If the
layout changes, plugin discovery silently returns zero: failing safe, but
silently. A canary test asserting a non-empty plugin set on a fixture endpoint is
the cheap guard.

The Codex built-in skill names Cursor filters are a hardcoded list in its source,
including a shipped typo (`opneai-docs`). That list will drift, and a stale copy
over-reports rather than under-reports — the safer failure direction, but still
wrong.

Cursor's reads of Claude Code's tree are wider than either project documents and
include Claude Code's **install lockfile and settings**, not just content files.
That means the two kinds' plugin sets are not independent: a change to Claude
Code's `enabledPlugins` alters Cursor's composition. Watch for this when
reasoning about why two agents on one machine disagree.

## When to revisit

- **If the desktop app and CLI surfaces diverge materially.** They are one kind
  today because they share a config root and read the same composition roots. If
  a future version gives either its own root, or if their read sets stop
  overlapping, ADR-0044's same-surface test stops returning "one kind" and this
  decision needs reopening.
- **If Cursor exposes plugin enable state in a readable file.** Presence-only
  stops being the honest ceiling, the installed coverage justification changes,
  and an `enabled` property becomes emittable.
- **When remote-window scanning is addressed.** A split-home session puts MCP and
  permissions on the remote host while subagents, skills, and commands stay
  local, so a scan of either machine is structurally partial. This is a coverage
  gap today; making it exact requires reaching a second host, which is a scanner
  capability question rather than a Cursor one.
- **If OpenACA gains a content-evidence surface** — a place to record what
  instructions an agent is given, distinct from what components it loads. Rules,
  `AGENTS.md`, and `CLAUDE.md` would belong to it, and it would need to cover
  every kind at once rather than arriving per kind.
- **If a Codex kind registers.** `.agents/skills/` stops being unambiguous
  evidence of a Cursor agent, and the exception carved out above needs revisiting.
- **If the third-party extensibility gate becomes file-readable.** The
  over-approximation above can become exact.
