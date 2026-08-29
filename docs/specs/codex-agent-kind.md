# Codex Agent Kind — Surface Audit

Companion to [ADR-0055](../adrs/0055-codex-agent-kind.md) (register the kind,
emit enable state, scope), [ADR-0056](../adrs/0056-codex-root-override.md)
(`$CODEX_HOME` and `--config-dir`), and
[ADR-0057](../adrs/0057-parameterize-endpoint-seeding.md) (endpoint seeding is
descriptor-driven where kinds share a procedure; acquisition forks). The mechanism those decisions plug into is
[Multi-Agent Support](multi-agent-support.md); this document is the per-kind
audit that mechanism requires.

It meets the four requirements at
[Multi-Agent Support § What a kind spec must contain](multi-agent-support.md#what-a-kind-spec-must-contain):
every path the runtime reads including another runtime's ([Files Codex reads
that another runtime owns](#files-codex-reads-that-another-runtime-owns)), an
observability gap per surface ([Coverage](#coverage)), a composition source per
claim (every row in [Surfaces in scope](#surfaces-in-scope) names one), and a
split verdict for anything deferred ([Deliberately out of the first
pass](#deliberately-out-of-the-first-pass), [Not shipping](#not-shipping),
[Out of scope](#out-of-scope)).

**The headline finding: Codex is Claude Code-shaped, not Cursor-shaped.** It
reuses Claude Code's plugin model, hook vocabulary, and root semantics, and reads
no other runtime's config tree. That claim is evidenced in
[Surface audit](#surface-audit).

The reuse is real but partial, and ADR-0057 records where it stops: Codex and
Claude Code **record** the same install facts and **acquire** them differently, so
the branches that differ only in directory names are shared while plugin
acquisition and remote MCP are not.

## What "Codex" means here

One kind, two programs, one config root.

| Program | Distribution | Root |
|---|---|---|
| `codex` CLI | standalone binary, `codex-cli 0.147.0` | `~/.codex` |
| Codex desktop app | ChatGPT.app bundle, ships its own `codex` | `~/.codex` |

The desktop app's `config.toml` on this endpoint names
`CODEX_CLI_PATH = "/Applications/ChatGPT.app/Contents/Resources/codex"` and sets
`CODEX_HOME` to the same `~/.codex` the CLI uses. Under ADR-0044's test — same
surface, same schema — that is one kind. The desktop app adds surfaces
(`computer-use/`, `visualizations/`, dictation history) but none declares
components.

## Evidence standard

Codex's documentation is where each claim started. It is not where any claim
ended, because documentation drifts from behavior and a parser written against
drift reports components the runtime never loads.

Every claim below was checked against the **shipped implementation** — the
standalone Rust binary at
`~/.codex/packages/standalone/releases/0.147.0-aarch64-apple-darwin/bin/codex`,
which retains its string literals, so the paths the program actually reads are
recoverable — and cross-checked against a live endpoint.

**Audited build:** `codex-cli 0.147.0` (`0.147.0-aarch64-apple-darwin`), desktop
app build `26.818.41509`, macOS, 2026-08-27.

That version is the provenance for every implementation-derived claim here: the
plugin manifest candidate order, the absence of project-scoped subagents, the
`.system` skill root, the hook event vocabulary, and the negative findings
below. **None of them is reproducible from this repository** — the evidence is a
shipped binary, not a fixture. Treat each as verified-at-0.147.0 and re-audit on
a major Codex release rather than as a contract Codex owes us.

Where documentation and implementation disagree, the implementation governs, and
the disagreement is marked *(docs disagree)* at the point it applies.

Two reading constraints. This describes a Codex version, not Codex forever;
where a rule rests on implementation rather than documentation the surface
section says so. And the audit ran on one platform: every in-scope surface
resolves beneath `$CODEX_HOME`, which is platform-neutral, so no claim here is
expected to be platform-specific — but none has been checked on Linux or
Windows.

### Reference counts

Claims of absence carry weight only if the probe would have found the thing.
Literal counts in the audited binary, for the surfaces this spec turns on:

| Literal | Refs | Reading |
|---|---|---|
| `config.toml` | 81 | primary config surface |
| `CODEX_HOME` | 60 | root is relocatable |
| `.codex/config.toml` | 9 | **project-scoped config exists** |
| `.codex/skills` | 3 | project-scoped skills exist |
| `.codex-plugin` | 31 | own plugin manifest |
| `.claude-plugin` | 15 | Claude's manifest is read |
| `prefix_rule` | 21 | approval DSL is real |
| `trust_level` | 17 | trust registry is real |
| `managed_config.toml` | 6 | admin-distributed layer exists |
| `.codex/agents` | **0** | ~~no config-declared subagents~~ — **wrong**, see [Subagents](#subagents) |
| `.agents/skills` | **0** | ~~not read by Codex~~ — **wrong**, see below |
| `.claude/skills` | **0** | Claude's content tree not read |
| `.claude/agents` | **0** | Claude's content tree not read |
| `installed_plugins.json` | **0** | Claude's lockfile not read |

**A zero in this table is weak evidence, and twice it was wrong.** A program
that builds a path from components — `home.join(".agents").join("skills")` —
contains no joined literal, so absence here shows only that the path is not
spelled out in one piece. Both disproved rows were caught by review, not by us,
and both were defended with this table. Treat a zero as "not found in this form"
and confirm against the published reference before asserting a surface does not
exist. The remaining zeros have **not** been re-checked that way.

## Surface audit

A **surface** is a place the runtime reads configuration from, not a component
type. Two verdicts per row, because they diverge: **Reuse** is about our
parsers, **Scope** is about this change.

| Surface | Claude Code | Codex | Reuse | Scope |
|---|---|---|---|---|
| MCP servers | `.mcp.json`, inline in settings | `[mcp_servers.<name>]` inline in `config.toml`; adds `enabled`, `startup_timeout_sec`, `tool_timeout_sec`, `bearer_token`, `http_headers` | **New — TOML** | **In** |
| Skills | `.claude/skills/`, bundled | `<root>/skills/`, project `.codex/skills/`, **`.agents/skills/` at repo and `$HOME` scope**, bundled; same `SKILL.md` | Parser as-is | **In** |
| Subagents | `.claude/agents/**/*.md` | `<root>/agents/*.toml` **and** `[agents."<role>"] config_file` in any config layer — TOML either way | **New parser** | **In** |
| Plugins | one manifest format | `.codex-plugin/plugin.json` → `.claude-plugin/plugin.json` | Ordered candidates via `RepoSurface` | **In** |
| Plugin enable state | `enabledPlugins` in settings | `[plugins."<name>@<mkt>"] enabled` | Same model, TOML | **In** |
| Marketplaces | `known_marketplaces.json` | `[marketplaces.<name>]` + `source_type`, `last_revision` | Same model, TOML | **In** |
| Plugin hooks | `hooks/hooks.json` | same envelope, **same PascalCase events** | Parser as-is | **In** |
| Standalone hooks | not a repo-mode surface | `$CODEX_HOME/hooks.json` (user), `<project>/.codex/hooks.json` (project) | Parser as-is | **In** |
| Approval policy | settings `permissions` | `~/.codex/rules/*.rules` — a **DSL**, not JSON | **New — posture** | **In** (posture) |
| Project trust | none | `[projects."<path>"] trust_level` | New — posture | **In** (posture) |
| Commands | `.claude/commands/` | `<root>/prompts/*.md`, invoked as `/prompts:<name>` | — | Deferred — see [Deliberately out of the first pass](#deliberately-out-of-the-first-pass) |
| Instruction files | `CLAUDE.md` (not read) | `AGENTS.md` (55 refs, heavily read) | — | **Out** — not configuration |
| Managed config | none | `managed_config.toml` | — | Deferred (schedule) |

Four corrections against the assumption that a third kind resembles the second:

- **Codex is not a cross-reader.** Cursor reads Claude Code's skills, agents,
  commands, settings, and install lockfile. Codex reads **none** of them — its
  only cross-runtime read is the `.claude-plugin/plugin.json` manifest *format*.
- **Enable state is readable.** Cursor's is a server call, which is the sole
  reason ADR-0052 made plugins presence-only. Codex writes it to `config.toml`.
- **Subagents are TOML, and have two declaration forms.** Not markdown. A role
  is declared either by a file in the agents directory or by an
  `[agents."<role>"]` table naming a `config_file` that may sit anywhere — see
  [Subagents](#subagents).
- **There is no commands surface.** Both other kinds have one; Codex does not.

## Config root

`~/.codex`, relocatable **as a whole** by `$CODEX_HOME` (60 refs). This is the
single largest structural difference from Cursor, whose root is not relocatable
and whose surfaces relocate independently (ADR-0054).

Configuration layers, in the order the binary composes them:

| Layer | Location | Notes |
|---|---|---|
| Managed | `managed_config.toml` | admin-distributed; [not in the first pass](#deliberately-out-of-the-first-pass) |
| User | `$CODEX_HOME/config.toml` | the primary surface |
| Profile | `$CODEX_HOME/<name>.config.toml` | layered on the base user config |
| Project | `<project>/.codex/config.toml` | project-scoped |
| Invocation | `-c key=value`, `--enable`/`--disable` | process-lifetime only, never on disk |

Because one variable moves the whole root, a scan can name exactly one directory
and be complete — which is what makes `--config-dir` honest here.

## CLI surface

`--kind codex` selects the kind; `--config-dir` is **accepted**, not refused.

| Invocation | Resolution |
|---|---|
| `scan endpoint` | every registered kind, each at its own default root |
| `scan endpoint --kind codex` | Codex only, at `$CODEX_HOME` or `~/.codex` |
| `scan endpoint --config-dir DIR --kind codex` | Codex only, rooted at `DIR` |
| `scan endpoint --config-dir DIR` | rejected — ambiguous with three kinds registered |

Repo mode takes no `--kind`: a repository declares whatever it declares, and
discovery decides.

### Why Codex accepts what Cursor refuses

ADR-0054 refused `--config-dir` for Cursor because an installed Cursor's
composition is gathered from three separately-relocated places, so an override
moved only one of them and produced a composition stitched from two homes.

Codex has one root and one variable:

| Group | Where | Moved by `--config-dir`? |
|---|---|---|
| Everything Codex owns | `$CODEX_HOME` | yes |
| Another runtime's tree | — | n/a — Codex reads none |

The refusal in ADR-0054 was therefore **Cursor-specific, not a general rule**.
ADR-0056 records that reading; ADR-0054 stands unchanged for Cursor.

## Surfaces in scope

Throughout: **config root** is `$CODEX_HOME`, else `~/.codex`. The inclusion bar
is *would omitting this make the inventory wrong on an ordinary installation*,
not *is it reachable*.

### Where each surface loads from

| Surface | User root | Project root | Traversal | Accepts | Source |
|---|---|---|---|---|---|
| MCP servers | `config.toml` `[mcp_servers.*]` | `.codex/config.toml` | inline table | stdio + `url`/`streamable_http` | both |
| Skills | `<root>/skills/`, `$HOME/.agents/skills/` | `.codex/skills/`, `.agents/skills/` | recursive | `SKILL.md` per directory | both |
| Subagents | `<root>/agents/*.toml`, plus `[agents.*] config_file` in any layer | via a layer's `[agents.*]` | flat + config-declared | `.toml` | installed |
| Plugins | `<root>/plugins/cache/<mkt>/<name>/<ver>/` | — | per bundle | two manifest formats | installed |
| Plugin hooks | bundled `hooks/hooks.json` | same | per bundle | `{hooks:{Event:[...]}}` | both |
| Standalone hooks | `<root>/hooks.json` | `.codex/hooks.json` | file | same envelope | both |
| Approval policy | `<root>/rules/*.rules` | — | flat | `prefix_rule(...)` DSL | installed |
| Project trust | `config.toml` `[projects.*]` | — | inline table | `trust_level` | installed |

Directories a reader expects and will **not** find: `.codex/agents` *(docs
disagree — Cursor's documentation lists it among Cursor's subagent roots, and
neither Cursor nor Codex references it; the error is upstream of both)*,
`.codex/commands` (no commands surface exists), and `.agents/skills` (Codex does
not read it — see [Files Codex reads that another runtime
owns](#files-codex-reads-that-another-runtime-owns)).

### Hook events

Codex's vocabulary is **PascalCase and shares Claude Code's names**, unlike
Cursor's camelCase, so `hooks_json` parses it unchanged:

`SessionStart` · `SessionEnd` · `PreToolUse` · `PostToolUse` · `PreCompact` ·
`UserPromptSubmit` · `Stop` · `PermissionRequest`

`PermissionRequest` has no Claude Code counterpart. `hooks_json._walk_events`
iterates whatever keys exist with no allowlist, so this needs no parser change.

### Exclusions

Present on disk, deliberately not inventoried.

| Path | Why |
|---|---|
| `<root>/skills/.system/` | Vendor built-ins, marked by a `.codex-system-skills.marker` file. A **sibling** of `skills/`, not a variant spelling. Excluded structurally by the marker, not by a name list |
| `<root>/sessions/`, `logs_*.sqlite`, `dictation-history/` | Session and telemetry state; declares nothing |
| `<root>/packages/standalone/` | Codex's own runtime install, not user composition |

The `.system` root is worth a note beyond this kind. Cursor filters six built-in
Codex skill names via a hardcoded list its own spec flags as drift-prone
([Cursor spec](cursor-agent-kind.md#exclusions)); those six names are exactly the
contents of `skills/.system/`. The marker file is the stable signal. Acting on
that for Cursor is **out of scope here** and belongs in a Cursor change.

### Plugins

Install roots, relative to the config root:

| Root | Holds |
|---|---|
| `plugins/cache/<marketplace>/<name>/<version>/` | marketplace bundles |
| `config.toml` `[marketplaces.<name>]` | registry: `source_type` (`git`\|`local`), `source`, `last_revision` |
| `config.toml` `[plugins."<name>@<marketplace>"]` | **enable state**, `enabled = true`\|`false` |

There is **no `.cache-complete` sentinel** and no separate install lockfile —
both differences from Cursor. The enable map is keyed
`<name>@<marketplace>`, byte-identical in format to Claude Code's
`enabledPlugins` keys.

Manifest resolution is an ordered candidate list:

```
.codex-plugin/plugin.json  →  .claude-plugin/plugin.json
```

On the audited endpoint 13 cached bundles carry `.codex-plugin` and 4 carry
**only** `.claude-plugin` — and all 4 are `enabled = true`, which is what proves
the fallback is real rather than vestigial. A bundle carrying both (e.g.
`superpowers`) has genuinely different content in each: the `.codex-plugin`
manifest adds `skills`, `hooks`, and an `interface` block the Claude one lacks.
A realizer stopping at the first manifest it *finds* rather than the first that
*qualifies* picks wrong.

## Deliberately out of the first pass

Real, reachable, and skipped — with the cost of skipping each.

| Held back | Cost of skipping |
|---|---|
| `managed_config.toml` (admin-distributed layer) | An MDM-managed endpoint under-reports: admin-pinned MCP servers and policy are invisible. Distribution path not yet audited |
| `$CODEX_HOME/<name>.config.toml` profile layering | A scan reports the base config; a session run under a profile may compose differently |
| `-c key=value` invocation overrides | Process-lifetime only, never written to disk. Structurally unobservable, not merely skipped |
| `.rules` beyond `prefix_rule` | The DSL has one verified form; other rule shapes would be reported as unparsed rather than misread |
| Custom prompts (`<root>/prompts/*.md`) | An endpoint using them reports zero commands. OpenAI marks custom prompts **deprecated** in favour of skills, and the audited endpoint has none, so the surface is recorded rather than built — an earlier draft wrongly said no commands surface existed at all |
| `/etc/codex/skills` (admin skills) | An MDM-managed endpoint under-reports shared skills. Same class as `managed_config.toml`: administrator-distributed, path not audited (ADR-0058) |

## Not shipping

Three distinct verdicts. Collapsing them into one "later" list is the error this
section exists to prevent.

### Not configuration: instruction files

**`AGENTS.md`.** Codex reads it heavily — 55 references, more than any surface in
scope. It is still out, for the reason Claude Code's own `CLAUDE.md` is out:
instruction files name nothing, version nothing, and resolve to no artifact, so
there is no component for a BOM to carry and no advisory that could match one.

No registry pattern, parser, or evidence rule in this repository touches an
instruction file for **any** kind today. Following Claude Code's rule keeps that
uniform. This is not a scheduling deferral — it is a different layer. If
instruction content becomes interesting it will be as a prompt-injection
question, and it must land for every kind at once, since `AGENTS.md` and
`CLAUDE.md` are cross-tool by construction.

### Deferred pending a taxonomy ADR

None. Every surface in scope maps into the ADR-0019/0031 closed sets
(`mcp_server`, `plugin`, `skill`, `hook`, `agent`). **No new component type and
no new source ecosystem is required** — a materially smaller ask than Cursor's.

### Deferred by schedule

`managed_config.toml` and profile layering, per [the table
above](#deliberately-out-of-the-first-pass). Both are ordinary work blocked only
on sequencing and one more audit pass.

## Coverage

`openaca:composition_coverage` qualifies the **component graph**, so one
question sets a baseline: *can this scan deterministically identify the agent's
components?* A gap lowers coverage only if it can hide one.

Three consequences, applied to every kind alike:

- **Administrative and policy surfaces do not lower it.** `rules/*.rules` and
  `[projects.*] trust_level` declare no components. They are posture, reported
  under their own rule ids.
- **Identity gaps do not lower it.** An unregistered marketplace costs a plugin
  its cross-BOM identity, not its place in the graph.
- **A readable surface we do not parse does lower it** — until we parse it.

| Source | Baseline |
|---|---|
| `declared` | `complete` |
| `installed` | `complete` |

### What each source reads in full

| Declared surface | Read by |
|---|---|
| `.codex/config.toml` → `[mcp_servers.*]` | `codex_config.parse` |
| `.codex/hooks.json` | `hooks_json.parse_standalone_hooks` |
| `.codex/skills/**/SKILL.md` | `claude_skill.parse` |
| `.codex-plugin/` and `.claude-plugin/` manifests | `claude_plugin.parse` |

Installed adds the plugin cache, `agents/*.toml`, `<root>/skills/`, and both
config layers — the base `config.toml` **and every `<name>.config.toml`
profile**. It also adds `$CODEX_HOME/hooks.json` and, if the project is
trusted, `<project>/.codex/hooks.json` — see below.

### The user-root `hooks.json` sidecar, and why the original audit missed it

The original audit ([ADR-0055](../adrs/0055-codex-agent-kind.md)) scoped the
standalone `hooks.json` envelope to the project only
(`<project>/.codex/hooks.json`), matched against the binary strings that
motivated this kind. Codex's own hooks documentation additionally names
`$CODEX_HOME/hooks.json` as a **user-scope** sidecar, loaded unconditionally
alongside every `config.toml` layer — "if more than one hook source exists,
Codex loads all matching hooks; higher-precedence config layers don't replace
lower-precedence hooks." Endpoint mode read only the inline `[hooks]`
config.toml form, so an endpoint declaring hooks solely via the sidecar had
them silently absent from the graph. Composition now reads both scopes in
endpoint mode, reusing `hooks_json.parse_standalone_hooks` (declared mode
already read the project scope); the project sidecar is trust-gated the same
way the project's `.codex/config.toml` layer is.

This is a scope correction, not a reversal of the ADR's central claim (Codex is
Claude Code-shaped) — recorded here rather than by editing the accepted ADR.

### Subagents

Two declaration forms, and both are real:

| Form | Where the role file lives |
|---|---|
| Directory | `<root>/agents/<role>.toml` |
| Config-declared | wherever `[agents."<role>"] config_file` points |

The published configuration reference defines the second:

> **`config_file`** — "Path to a TOML config layer for that role; relative
> paths resolve from the config file that declares the role."
> — [Configuration Reference](https://developers.openai.com/codex/config-reference)

Two consequences. The **table key is the role identity**, not the referenced
file's own `name` — the key is what selects the role, and the two are free to
disagree. And a `config_file` that does not exist is a **coverage gap**, not an
absent role: the reference says the path "is validated at load time and must
point to an existing file", so Codex itself treats it as an error.

**Correction.** An earlier version of this spec said subagents were
directory-discovered only, on the evidence that the audited binary contains no
`.codex/agents` string literal. That does not follow — a program that builds a
path from components has no such literal, so the absence showed nothing. The
claim was wrong, it made every config-declared role invisible to a scan, and it
was raised twice in review before being checked against the published
reference. Recorded here because the failure was one of method, not detail: an
in-repo document cannot settle a question about a third party's behaviour, which
is the rule this spec's own [Evidence standard](#evidence-standard) states.

### The profile layer, and why it was a real gap

`codex -p <name>` layers `$CODEX_HOME/<name>.config.toml` over the base config,
and it carries the same schema. Verified directly: a fixture root with
`base_server` in `config.toml` and `profile_only_server` in `work.config.toml`
lists both under `codex -p work mcp list`. A scan reading only `config.toml`
missed every server a profile added — a component gap, and the reason this kind
briefly carried `partial`.

Every profile is read rather than an active one, because which profile is
selected is an invocation flag leaving no trace on disk. The union
over-approximates, which is the safe direction.

**This behaviour is version-dependent, and the audited version is the one this
spec follows.** On `codex-cli 0.147.0` a profile's `[mcp_servers.*]` entries do
load: a fixture root declaring `base_only` in `config.toml` and `profile_only`
in `work.config.toml` lists **both** under `codex -p work mcp list`, reproduced
twice. A review round reported the opposite from `codex-cli
0.144.0-alpha.4`, where only the base server appeared. Both observations are
presumably correct for their build; composition follows 0.147.0 because that is
the version this spec's [Evidence standard](#evidence-standard) pins. Re-check
on the next major release — if profile MCP loading was removed rather than
added, this becomes an over-report rather than a correction.

### Candidates that fail the rule, not the evidence

| Candidate | Why it does not lower coverage |
|---|---|
| `rules/*.rules` beyond `prefix_rule` | posture; declares no components |
| `[projects.*] trust_level` | posture; declares no components |
| Server-fetched marketplace state | identity, not enumeration — plugins come from the cache |
| `-c` invocation overrides | process-lifetime, never on disk, and cannot affect what a repo declares |
| Runtime MCP registration | **no evidence it exists**: `registerServer`, `mcp.register`, `addServer`, `dynamic_mcp`, `runtime_mcp` all return zero references in the audited binary. An earlier draft asserted it by carrying the claim over from Cursor, where it is real, without checking it here |

### The one thing that would reopen this

`managed_config.toml` (6 binary references) is an administrator-distributed
layer whose path has not been audited, and none exists on the audited endpoint.
If it turns out to declare components, the honest response is to read it — it is
a file — not to relabel the baseline. That is the same treatment Claude Code's
own managed settings received.

## Identity

Every Codex surface maps into the existing closed sets. No new component type,
no new source ecosystem, no taxonomy ADR.

**Marketplace-installed plugins take `plugin/{marketplace}/{name}`** — the same
shape and qualifying key Claude Code's marketplace plugins use, and for a
stronger reason than Cursor's: the marketplace is not inferred from a cache path
segment but read from `[marketplaces.<name>]`, a registry Codex wrote after
resolving the source. `canonical_component_identity` grants cross-BOM identity to
an identity string with two or more `/`, and this one is backed by a recorded
registry entry.

Bundled identity is plugin-private, so this also restores identity to every
skill and hook inside a realized bundle.

**A local marketplace is the open case.** `[marketplaces.openaca-demo-local]`
has `source_type = "local"` and a `source` pointing at
`~/.claude/plugins/local/openaca-demo` — the same directory Claude Code realizes
its own copy from. Whether that yields one cross-BOM identity or two is a
question this spec raises and does **not** settle; it is the first genuine
same-artifact-two-kinds case in the corpus, and it should be decided with the
identity work rather than inside this kind.

## Files Codex reads that another runtime owns

Requirement #1 of the kind-spec contract, and the reason it exists.

| Path | Owner | Read by Codex as |
|---|---|---|
| `.claude-plugin/plugin.json` | Claude Code | fallback plugin manifest |

**That is the entire list**, and its shortness is the finding. Cursor reads
Claude Code's skills, agents, commands, settings, and install lockfile; Codex
reads a manifest *format* and nothing else. `.claude/skills`, `.claude/agents`,
and `installed_plugins.json` each have zero references in the audited binary.

Two consequences reach back into shipped decisions:

- **ADR-0052's revisit trigger fired, and ADR-0058 resolves it.** That ADR made
  `.agents/skills/` evidence of a *Cursor* agent on the grounds that Cursor was
  the only registered kind that read it, and flagged the claim to revisit "if a
  Codex kind lands." A Codex kind has landed, and **Codex does read
  `.agents/skills`** — both the repo-scope glob (`CODEX_SURFACE`) and the
  `$HOME`-scope endpoint read (`_seed_codex_shared_agent_skills`). An earlier
  draft of this spec asserted the opposite from a zero-reference count for the
  literal string, which does not follow for a path a program builds from
  components — see [Reference counts](#reference-counts). Per ADR-0058,
  `.agents/skills/` is now evidence for **every** kind that reads it: a
  repository declaring only shared skills there declares both a Cursor and a
  Codex agent.
- **Cross-reads are composition, never evidence.** A tree containing only
  `.claude-plugin/plugin.json` declares a Claude Code agent, not a Codex one.

## Posture derives from composition

Posture collectors read the **graph**, not the filesystem. A collector that
re-walks the tree has to re-implement every exclusion composition applies —
`.system` skills, plugin-owned content, disabled servers — and each rule restated
by hand is a rule that can drift from the one composition actually applies.

Two corollaries:

- The declared MCP collector derives its manifests from the refs the graph
  already produced, exactly as Cursor's does.
- A surface that declares no components — `.rules`, `[projects.*]` — has no graph
  representation to derive from and is read directly. That is the exception, and
  it is bounded to the two posture-only surfaces.

## Posture rule applicability

| Rule | Applies | Why |
|---|---|---|
| `insecure_transport` | **Yes** | `[mcp_servers.*]` carries `url` and `streamable_http`; an `http://` remote server is the same real exposure |
| `mutable_install` | **Yes** | MCP branch keys on launch specs (`npx pkg@latest`). Its plugin branch keys on `gitCommitSha`; Codex records `last_revision`, so whether that branch fires needs deciding at implementation rather than assuming |
| `skill_capability` | **Yes** | same `SKILL.md` with `allowed-tools`; the rule gates on `component_type == "skill"` |
| `mcp_auto_approve` | **No** | Codex has no per-server auto-approve field, and its approval surfaces are not about MCP — see below |
| `api_endpoint_override` | **No** | matches literal Anthropic settings keys; Codex has no such surface |

### Two new rule IDs, not a re-pointed one

An earlier form of this spec said `mcp_auto_approve` "re-points" at Codex's
`rules/*.rules` and `[projects.*] trust_level`. That was reasoned by analogy to
Cursor, where `permissions.json` genuinely *is* MCP approval state. It does not
hold here, and the reason matters beyond tidiness.

`rule_id` is a **policy gate key**, not just a report label: `policy_cli` fails a
finding whose `rule_id` is absent from `risk_gates.posture_rule_ids`. So every
finding sharing an id is allowed or denied together, in one decision.

The three surfaces answer different questions:

| Surface | What it permits | Subject |
|---|---|---|
| MCP auto-approve (Cursor, Claude Code) | a **server** runs tools unattended | MCP |
| Codex `rules/*.rules` | a **shell command** runs unattended (`git commit`, `uv run pytest`) | the terminal |
| Codex `[projects.*] trust_level` | an **entire directory** is trusted | the workspace |

Only the first is about MCP. Under one id, a team that vets its MCP servers and
allows `openaca-posture-mcp-auto-approve` would silently also allow every
unattended shell command and every trusted directory — three risks approved by
one decision, with nothing in the policy file showing it.

Codex therefore mints two rule ids of its own:

| Rule | Reads | Reports |
|---|---|---|
| `openaca-posture-command-policy-allow` | `rules/*.rules` | commands permitted to run without approval |
| `openaca-posture-project-trust` | `[projects.*] trust_level` | directories marked trusted |

Both are **posture-only** surfaces that declare no components, so they are read
directly rather than derived from the graph — the documented exception in
[Posture derives from composition](#posture-derives-from-composition).

One implementation note survives from the earlier reading: `mcp_auto_approve`
hardcodes `active_in=["cursor"]`, which is wrong independently of this decision
and should be fixed as a Cursor defect with its own regression gate, not as part
of Codex's work.

## Out of scope

Permanently excluded, not deferred.

- **Session, telemetry, and history state** — `sessions/`, `logs_*.sqlite`,
  `dictation-history/`, `transcription-history.jsonl`. These record what an agent
  did, not what it is composed of.
- **Codex's own runtime install** — `packages/standalone/`. The runtime is not a
  component of the composition it runs.
- **Desktop-only feature state** — `computer-use/`, `visualizations/`, `pets/`.
  None declares a component.
- **`auth.json` and credentials** — never read, never inventoried, never emitted.
