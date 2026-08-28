# Plan 043 — Codex agent kind

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scan repo`, `scan endpoint`, `bom repo`, `bom endpoint`, and
`remote sync endpoint` each discover and emit a **Codex** agent alongside the Claude
Code and Cursor ones — its own graph, BOM, coverage verdict, and posture allowlist.

**Architecture:** Codex is Claude Code-shaped, so where the two agents' control
flow is genuinely identical, this plan **reuses** rather than forks. Repo mode is
already parameterised (ADR-0053) and Codex supplies a `CODEX_SURFACE` constant.
Endpoint mode splits in two (ADR-0057): its two literal-substitution surfaces
(project skills, direct components) become parameterised and are extracted into
`_seed_shared_endpoint_surfaces`, a standalone function driven entirely by an
`EndpointSurface` descriptor, with Claude Code's values transcribed verbatim into
`CLAUDE_CODE_ENDPOINT` (Task 5). Claude Code's own `_seed_endpoint` calls this
shared function alongside its own active-plugin and remote-MCP acquisition, so
its behavior is unchanged. Codex's other two surfaces — plugin acquisition and
remote MCP — have genuinely different control flow between the two agents
(cache-first traversal vs. enable-map traversal; TOML vs. JSON settings layers),
so Codex brings its own narrow seed for exactly these two, composed together
with a direct call to `_seed_shared_endpoint_surfaces` (never through
`_seed_endpoint`, which would also run Claude's plugin/MCP acquisition) inside
one graph-lifecycle function Task 7 builds and Task 11's
`tools/agent_kinds/codex.py`'s `_compose` calls — the same shape Cursor's
endpoint fork already uses. Two genuinely new parsers are required — Codex's
config is **TOML**, and its subagents are **TOML**. `graph_build` still never
imports `agent_kinds`.

**Tech Stack:** Python 3.11 (`tomllib` is stdlib — **no new dependency**), click,
pathspec, pytest, uv.

**Spec:** `docs/specs/codex-agent-kind.md`. Read it before starting — especially
[Surfaces in scope](../specs/codex-agent-kind.md#surfaces-in-scope) and
[Deliberately out of the first pass](../specs/codex-agent-kind.md#deliberately-out-of-the-first-pass),
which are the scope boundaries this plan implements.
**ADRs:** `0055-codex-agent-kind.md` (the kind), `0056-codex-root-override.md`
(`$CODEX_HOME`), `0057-parameterize-endpoint-seeding.md` (the mechanism),
`0044`–`0047` and `0053` (what this builds on).

## Context

Two kinds ship. Claude Code has a relocatable root, a marketplace registry, and
readable enable state. Cursor has none of those, reads three other runtimes' trees,
and is presence-only because its plugin state is a server call.

Codex sits with Claude Code, and that is the whole design. Its plugin enable map is
keyed byte-identically (`superpowers@claude-plugins-official`), its cache layout is
`plugins/cache/<mkt>/<name>/<ver>/`, its hook events are Claude Code's PascalCase
names, and `$CODEX_HOME` relocates the whole root. It reads **no** other runtime's
config tree.

So the work is not "write a third walker". It is: supply two TOML parsers, supply two
descriptor constants, parameterise the one remaining forked path, and register.

The risk concentrates in **Task 5**. `_seed_endpoint` is Claude Code's most heavily
exercised path, and parameterising it is the only change here that can regress a
shipped kind. That task therefore lands alone, with zero Codex entries, behind a
byte-identical golden-graph gate — the same discipline ADR-0053 used for repo mode.

## Global Constraints

Copied verbatim from the spec and ADRs. Every task's requirements include these.

- **Emit real `enabled` state** on plugins and MCP servers, read from `config.toml`
  (ADR-0055). Absent is not `false`; `false` is `false`. **Everything installed is
  inventoried regardless of enable state.**
- **`root_override_refusal = None`** — Codex accepts `--config-dir` and honours
  `$CODEX_HOME` (ADR-0056).
- **No new component type and no new source ecosystem.** Every surface maps into the
  ADR-0019/0031 closed sets: `mcp_server`, `plugin`, `skill`, `hook`, `agent`.
- **No commands surface.** Codex has none; do not add one.
- **`AGENTS.md` is not a component**, not evidence, and not parsed — same rule Claude
  Code applies to `CLAUDE.md`.
- **Subagents are user-scope only.** `~/.codex/agents/*.toml`. `.codex/agents` has zero
  references in the audited binary; a project cannot declare one.
- **Exclude `<root>/skills/.system/`** structurally, by the presence of
  `.codex-system-skills.marker`, never by a list of skill names.
- **Plugin manifest candidates are ordered**: `.codex-plugin/plugin.json`, then
  `.claude-plugin/plugin.json`. First *qualifying* candidate wins, not first found.
- **Marketplace plugins take `plugin/{marketplace}/{name}`**; the marketplace comes
  from `[marketplaces.*]`, never inferred from a path segment.
- **`graph_build` never imports `agent_kinds`** (ADR-0044). Kind modules import
  builders lazily.
- **Claude Code's and Cursor's output must not change.** Every task that touches a
  shared path runs the full suite; Task 5 additionally runs a golden-graph assertion.

## File Structure

**New:**

| File | Responsibility |
|---|---|
| `tools/parsers/codex_config.py` | Parse `config.toml`: `[mcp_servers.*]`, `[plugins."x@y"]`, `[marketplaces.*]`, `[projects.*]` |
| `tools/parsers/codex_agent.py` | Parse `~/.codex/agents/*.toml` subagents |
| `tools/parsers/codex_rules.py` | Parse the `prefix_rule(...)` approval DSL — posture only |
| `tools/endpoint_surface.py` | `EndpointSurface` descriptor (ADR-0057), neutral module |
| `tools/agent_kinds/codex.py` | The kind: evidence, discovery, config root, `KIND` |
| `tests/test_parsers/test_codex_config.py` | |
| `tests/test_parsers/test_codex_agent.py` | |
| `tests/test_parsers/test_codex_rules.py` | |
| `tools/posture/rules/command_policy_allow.py` | Posture rule: `openaca-posture-command-policy-allow`, reads `rules/*.rules` |
| `tools/posture/rules/project_trust.py` | Posture rule: `openaca-posture-project-trust`, reads `[projects.*] trust_level` |
| `tests/test_endpoint_surface.py` | |
| `tests/test_agent_kinds_codex.py` | |
| `tests/test_posture_codex.py` | Codex's posture collectors (declared/installed MCP, `.rules`, `[projects.*]`) |
| `tests/test_posture_command_policy_allow.py` | |
| `tests/test_posture_project_trust.py` | |

**Modified:**

| File | Change |
|---|---|
| `tools/graph_build.py` | extracts `_seed_shared_endpoint_surfaces(surface: EndpointSurface, ...)` from `_seed_endpoint`'s project-skill/direct-component branches; `_seed_endpoint` calls it, unchanged otherwise; adds `_seed_cache_plugins`, `_seed_codex_mcp_servers`, `build_codex_installed_graph` |
| `tools/repo_surface.py` | `CODEX_SURFACE`, `_CODEX_PLUGIN_FORMAT` |
| `tools/parsers/__init__.py` | `CODEX_MANIFEST_REGISTRY` + wrappers |
| `tools/agent_kinds/__init__.py` | `_registry()` gains `codex`; `AgentKind` gains `extra_installed_posture_collectors` |
| `tools/posture/__init__.py` | Codex collectors (including the two new `.rules`/`[projects.*]` manifest readers); register the two new rule ids in `KNOWN_RULE_IDS`; call both rules unconditionally in `run_posture_rules`, gated on its new `extra_manifests` parameter |
| `tools/scan.py` | `--kind codex` wiring; installed source-unit label/count; `AgentScanPrep.extra_manifests` threaded into `run_posture_rules` |
| `tools/bom_cli.py` | installed source-unit label/count |
| `tools/remote/collector.py` | installed source-unit label/count; `extra_manifests` threaded into `run_posture_rules` |
| `docs/reference/cli.md`, `coverage.md`, `CLAUDE.md` | docs |

---

## Task 1: Codex config parser (`config.toml`)

**Files:** Create `tools/parsers/codex_config.py`, `tests/test_parsers/test_codex_config.py`.

**Produces:** `parse(path) -> list[ComponentRef]` emitting `mcp_server` refs;
`load_config(path) -> CodexConfig` exposing `.mcp_servers`, `.plugins`,
`.marketplaces`, `.projects`. Later tasks consume `load_config`.

- [x] **Step 1: Write failing tests.** A `config.toml` with two `[mcp_servers.*]`
  tables — one stdio (`command`, `args`, `env`), one remote (`url`) — yields two
  `mcp_server` refs. A server with `enabled = false` yields a ref whose
  `extra["enabled"] is False`. A server with no `enabled` key yields
  `extra["enabled"] is True`, **not** an absent key: the spec says the value is
  readable, so it is always stated. **Before writing this test, re-confirm
  against the audited binary** (same evidence method the spec used — string
  literals in `~/.codex/packages/standalone/releases/0.147.0-aarch64-apple-darwin/bin/codex`,
  cross-checked against a live endpoint) that an absent `enabled` key actually
  resolves to enabled at runtime. If the binary shows a different default, the
  parser follows the binary, not this step's text. **If the binary artifact is
  unavailable, or a live endpoint's behavior contradicts the binary's string
  literals, stop and record the disagreement for spec clarification instead of
  picking either default in parser code** — this default is a spec-level claim,
  not an implementation detail this step is free to resolve unilaterally.
- [x] **Step 2: Run and confirm failure** (`ModuleNotFoundError`).
- [x] **Step 3: Implement with `tomllib`.** `tomllib.load` on a binary handle.
  Malformed TOML raises `tomllib.TOMLDecodeError`; let it propagate so
  `parse_repo_registry_counts` records `parse_failed` (mirrors
  `_parse_repo_cursor_agent`'s `strict=True` reasoning).
- [x] **Step 4: Add plugin/marketplace/project extraction.** `[plugins."name@mkt"]`
  splits on the **last** `@` (a plugin name may contain one); `[marketplaces.*]`
  carries `source_type`, `source`, `last_revision`; `[projects."<path>"]` carries
  `trust_level`. These are returned by `load_config`, not emitted as refs here —
  plugins become refs in Task 7, `projects` is posture in Task 10.
- [x] **Step 5: Test the `@` split** with `my@plugin@marketplace`, asserting name
  `my@plugin` and marketplace `marketplace`.
- [x] **Step 6: Run tests, confirm pass. Commit.**

## Task 2: Codex subagent parser (`agents/*.toml`)

**Files:** Create `tools/parsers/codex_agent.py`, `tests/test_parsers/test_codex_agent.py`.

**Consumes:** nothing. **Produces:** `parse(path) -> list[ComponentRef]` emitting one
`agent` ref.

- [x] **Step 1: Write failing tests.** `name = "dummy-probe"`, `description = "..."`,
  `developer_instructions = "..."` yields one `agent` ref named `dummy-probe`. A file
  with no `name` key falls back to the filename stem (parity with
  `claude_command_agent`). Malformed TOML raises.
- [x] **Step 2: Run, confirm failure.**
- [x] **Step 3: Implement.** Reuse `ComponentRef` construction conventions from
  `tools/parsers/claude_command_agent.py`; the ecosystem and component type must match
  what that parser emits for `kind="agent"` so identity is consistent across kinds.
- [x] **Step 4: Run tests, confirm pass. Commit.**

## Task 3: Codex approval-rules parser (`rules/*.rules`)

**Files:** Create `tools/parsers/codex_rules.py`, `tests/test_parsers/test_codex_rules.py`.

**Produces:** `parse_rules(path) -> ParsedRules`, where `PrefixRule` is a
`NamedTuple(pattern: tuple[str, ...], decision: str)` and `ParsedRules` is a
`NamedTuple(rules: list[PrefixRule], unparsed_count: int)` — the one stable
result shape every caller (Task 7's composition-time coverage read, Task 10's
posture-content read) consumes, rather than each reaching for a different
value out of a plain list.

- [x] **Step 1: Define `ParsedRules` first**, before any parsing logic, so
  syntax recognition and coverage accounting are tested against one
  abstraction instead of two ad hoc return values.
- [x] **Step 2: Before writing the regex, confirm the DSL's statement
  granularity against the audited binary or a real `.rules` sample** (same
  evidence method as Task 1 Step 1) — specifically whether a `prefix_rule(...)`
  call is always confined to one physical line, or can span multiple lines,
  carry trailing commas, or share a line with another call or a comment. The
  spec's own evidence for this DSL is a binary string-literal count (`prefix_rule`,
  21 refs), not a decoded sample, so this is not yet established. If the
  artifact is unavailable or does not settle it, do not assume one physical
  line is the unit — proceed per Step 4 below instead of guessing a granularity
  the evidence doesn't support.
- [x] **Step 3: Write failing tests.** `prefix_rule(pattern=["git", "commit"], decision="allow")`
  parses to `ParsedRules([PrefixRule(("git","commit"), "allow")], 0)`. Content
  that does not match the recognised `prefix_rule(...)` shape is **skipped and
  counted**, not raised — the spec records the DSL as incompletely specified, so
  an unknown form must degrade to "unparsed", never to a wrong allow/deny.
- [x] **Step 4: Run and confirm failure** (`ModuleNotFoundError`).
- [x] **Step 5: Implement** with a conservative regex for `prefix_rule(...)` only,
  applied to the **whole file's text**, not iterated per physical line — so a
  real multi-line call (if Step 2 finds one) is matched as one occurrence rather
  than miscounted as several unparsed lines. Do **not** attempt a general
  expression evaluator. Count `unparsed_count` as the number of non-whitespace,
  non-comment regions of the file that do not fall inside a matched
  `prefix_rule(...)` call — not a per-line count — so the result does not
  silently assume a granularity Step 2 could not confirm. If Step 2 could not
  establish clean statement boundaries at all, **do not extract any
  `PrefixRule` matches** — a regex applied to unverified boundaries can slice a
  match out of a comment, a string literal, or the wrong side of a multi-line
  call, and a wrong `PrefixRule` becomes a wrong posture finding in Task 10,
  not just a wrong count. Treat the whole file as one opaque unit instead:
  `ParsedRules.rules` is `[]` and `unparsed_count` is `1` if any non-comment
  content exists, `0` otherwise. Record this fallback explicitly in the
  implementing commit rather than asserting false per-occurrence precision.
  Only when Step 2 establishes verified statement boundaries does this step's
  regex extract `PrefixRule` matches for posture use.
- [x] **Step 6: Test empty files, comment/whitespace-only files, and mixed
  recognised/unrecognised content** against `ParsedRules` — an empty or
  comment-only file yields `ParsedRules([], 0)`, never a nonzero
  `unparsed_count`, so it does not trip Task 7 Step 9's coverage warning.
- [x] **Step 7: Run tests, confirm pass. Commit.**

## Task 4: `CODEX_SURFACE` repo descriptor

**Files:** Modify `tools/repo_surface.py`; extend `tests/test_repo_surface.py`.

**Consumes:** `PluginFormat`, `RepoSurface`, `BundledLayout`.
**Produces:** `CODEX_SURFACE`.

- [x] **Step 1: Write failing test** asserting `CODEX_SURFACE.plugin_formats` is
  exactly `(_CODEX_PLUGIN_FORMAT, CLAUDE_CODE_SURFACE.plugin_formats[0])` **in that
  order**, and that `CLAUDE_CODE_SURFACE` is unchanged.
- [x] **Step 2: Add `_CODEX_PLUGIN_FORMAT`** — `manifest_dir=".codex-plugin"`,
  `manifest_filename="plugin.json"`, `detect=_detect_cursor_native_manifest` (the
  `name`-is-a-string test; rename that predicate to `_detect_named_plugin_manifest`
  since two kinds now use it), `parse=claude_plugin.parse`.
- [x] **Step 3: Add `CODEX_SURFACE`.** `config_dir=".codex"`,
  `skill_config_dirs=(".codex",)`, `excluded_skill_dirs=()` — **`.system` is excluded
  by marker in Task 6, not by name here**. `command_agent_surfaces=()` (no commands
  surface). `standalone_mcp_filenames=()`. `scoped_mcp_rels=()` — Codex's project MCP
  lives in `.codex/config.toml`, not a dedicated file.
  `excludes_plugin_owned_content=True`. `manifest_optional=False`.
- [x] **Step 4: Run the full suite** and confirm Claude Code and Cursor are unchanged.
- [x] **Step 5: Commit.**

## Task 5: Parameterise endpoint seeding — zero Codex entries

**This task is the regression gate and must be green before any Codex endpoint code
exists.** It ships alone (ADR-0057).

**Scope.** Of `_seed_endpoint`'s four seed surfaces, only two are literal
substitutions — same control flow, different strings — and this task covers
exactly those two:

- **Project skills**: the project-scoped skill subdirectory name (e.g.
  `.claude/skills` vs `.codex/skills`) — the one literal in `_seed_endpoint`'s
  project-skill block that today reads `.claude` unconditionally regardless of
  which kind is seeding.
- **Direct components**: install-record location and shape, the
  direct-component subdirectory names, and whether this endpoint seeds
  settings-scoped hooks at all — Codex's hooks are declared-only
  (`.codex/hooks.json`, Task 6); its endpoint mode does not seed them, so this
  is a boolean field, not a literal substitution, but it stays data-only.

Both of these branches are extracted from `_seed_endpoint` into a standalone
function, `_seed_shared_endpoint_surfaces(graph, target, install_root,
project_root, normalize, surface, *, by_scope=None, warnings=None)`, callable
on its own without triggering plugin or remote-MCP acquisition. `_seed_endpoint`
calls it (with `by_scope` computed from `load_settings`, as today); Task 7's
Codex composer calls it directly (with `by_scope=None`, since `surface.seeds_hooks`
is `False` for `CODEX_ENDPOINT` and the hook loop is skipped before `by_scope`
would ever be read). This is what makes the two literal-substitution branches
genuinely reusable — Codex must never call `_seed_endpoint` itself, since that
function also unconditionally runs Claude's plugin and remote-MCP acquisition
(see Step 5).

The other two surfaces — plugin acquisition and remote MCP — differ in
*control flow*, not just literals (Claude Code's `_seed_active_plugins` walks
the enable map and only ever emits a node for a `True` entry; Codex must walk
the cache directory first to inventory disabled bundles too, per ADR-0055; the
same split applies to `_seed_remote_mcps`'s JSON settings layers vs Codex's
`config.toml`). **This task does not define `EndpointSurface` fields for
either of them.** Per ADR-0057, Codex brings its own narrow seed for these
two — Task 7 builds that seed and the composer function that runs it
alongside the two literal-substitution branches, Task 11's
`tools/agent_kinds/codex.py`'s `_compose` calls that composer.

**Files:** Create `tools/endpoint_surface.py`, `tests/test_endpoint_surface.py`;
modify `tools/graph_build.py`.

**Produces:** `EndpointSurface` and `CLAUDE_CODE_ENDPOINT`, consumed by Task 7.

- [x] **Step 1: Write the golden-graph test first.** Build a fixture endpoint
  (`~/.claude`-shaped: one cached plugin, one enabled entry in settings, one remote
  MCP, one project skill), serialise its graph deterministically, and pin it as a
  golden JSON fixture. Mirror the existing `repo-surface-golden` fixture.
- [x] **Step 2: Run it — it must PASS before any refactor**, proving the fixture
  captures current behaviour.
- [x] **Step 3: Define `EndpointSurface`** as a frozen dataclass with exactly the
  three fields named in Scope above (project-skill subdirectory,
  direct-component install-record location/shape, `seeds_hooks: bool`). Where
  Codex has no counterpart for a field, it carries an absence (`None`, `()`).
- [x] **Step 4: Transcribe `CLAUDE_CODE_ENDPOINT` verbatim** from the literals in
  `_seed_endpoint` and `_seed_direct_components` for those fields
  (`seeds_hooks=True`, matching current behaviour).
- [x] **Step 5: Extract `_seed_shared_endpoint_surfaces`** from `_seed_endpoint`'s
  project-skill and direct-component blocks, parameterised by
  `surface: EndpointSurface` — **including** the project-skill block's
  `.claude/skills` literal, which must read the new project-skill-subdirectory
  field rather than stay hardcoded, and the settings-scoped hooks loop inside
  `_seed_direct_components`, which must run only when `surface.seeds_hooks` is
  `True` (and must not require its `by_scope` argument when `False`). Rewrite
  `_seed_endpoint` to call this new function (passing `by_scope` computed from
  `load_settings`, as today) in place of the inline blocks it used to run
  directly, then continue with its own active-plugin and remote-MCP seeding —
  `_seed_active_plugins`/`_seed_remote_mcps`, unconditional, no descriptor
  read — per ADR-0057, Codex's plugin acquisition and remote MCP never dispatch
  through `_seed_endpoint` at all; they are Task 7's own functions, and Task 7
  never calls `_seed_endpoint` itself for exactly this reason. **Give
  `build_rooted_graph` a matching `endpoint_surface: EndpointSurface =
  CLAUDE_CODE_ENDPOINT` keyword parameter**, forwarded to
  `_seed_endpoint(..., surface=endpoint_surface, ...)` in the endpoint branch
  only — the same shape repo mode already uses for its own
  `surface: RepoSurface = CLAUDE_CODE_SURFACE` parameter. This keeps Claude
  Code's own `_compose` (which calls `build_rooted_graph` directly) and every
  existing caller unaffected. **Codex's installed branch does not call
  `build_rooted_graph`** (which would seed through the unmodified
  `_seed_endpoint`, running Claude's plugin/MCP acquisition against Codex's
  config root) **and does not call `_seed_endpoint`** (same reason). It calls
  `_seed_shared_endpoint_surfaces` directly instead. `build_rooted_graph` also
  finalizes internally and returns only a `Graph` — it never exposes the root
  `Node` or `SourceNormalizer` it built, and both are required arguments for
  Task 7's forked seed functions and for `_seed_shared_endpoint_surfaces`
  itself. Calling those seeds after `build_rooted_graph` returns would also
  miss `finalize_graph`'s MCP launch-dependency attachment for their refs and
  drop their warnings, since validation and the warnings copy already ran.
  Task 7 instead builds its own root/graph/normalizer — the same three lines
  `build_rooted_graph`'s endpoint branch uses internally — inside a dedicated
  `build_codex_installed_graph` function, calls
  `_seed_shared_endpoint_surfaces(..., surface=CODEX_ENDPOINT, by_scope=None)`
  directly against them for the two literal-substitution branches, then its
  own two forked seeds against the same root/normalizer, then `finalize_graph`
  exactly once. See Task 7 Step 10.
- [x] **Step 6: Confirm branch-by-branch coverage.** For each of `_seed_endpoint`'s
  four branches: project skills → literal substitution, now in
  `_seed_shared_endpoint_surfaces` (this task); direct components → literal
  substitution plus the `seeds_hooks` gate, now in
  `_seed_shared_endpoint_surfaces` (this task); active plugins → forked, per
  ADR-0057 — Codex never dispatches through `_seed_endpoint` or
  `_seed_shared_endpoint_surfaces` for this branch at all (Task 7's
  `_seed_cache_plugins`, invoked from Task 7's own
  `build_codex_installed_graph`); remote MCP → forked the same way (Task 7's
  `_seed_codex_mcp_servers`).
  **The tier-2 lockfile dependency walk is expected to have no Codex
  counterpart** — it stays Claude-Code-specific behind an absent descriptor
  field rather than being generalised. Record this table in the PR
  description.
- [x] **Step 7: Run the golden-graph test** — must still pass, byte-identical.
- [x] **Step 8: Run the full suite.** Zero Codex entries in this diff.
- [x] **Step 9: Commit.** This task's `EndpointSurface` is a complete, shippable
  unit on its own — nothing in it is contingent on Task 7's forked seed.

## Task 6: Codex declared composition (repo mode)

**Files:** Modify `tools/graph_build.py` only if a gap appears; extend
`tests/test_graph_build.py` or add `tests/test_graph_build_codex.py`.

- [x] **Step 1: Write a failing test.** A fixture repo with `.codex/hooks.json`,
  `.codex/skills/demo/SKILL.md`, and `.codex/config.toml` declaring one MCP server
  composes a graph with three children under the target.
- [x] **Step 2: Run, confirm failure.**
- [x] **Step 3: Drive `descend(surface=CODEX_SURFACE)`.** Codex's repo surface is
  expressible in the existing descriptor, so **prefer reusing `descend` over writing a
  `graph_build_codex.py`.** Write the fork only if a genuine traversal difference
  appears; if it does, record what it was — that is evidence ADR-0053's repo-mode half
  needs revisiting.
- [x] **Step 4: Add project MCP from `.codex/config.toml`**, using Task 1's
  `load_config`.
- [x] **Step 5: Assert no `AGENTS.md` node** appears in the graph for a fixture that
  contains one.
- [x] **Step 6: Run tests, confirm pass. Commit.**

## Task 7: Codex installed composition (endpoint mode)

**Files:** Modify `tools/endpoint_surface.py` (add `CODEX_ENDPOINT`);
`tools/graph_build.py` (add `_seed_cache_plugins`, `_seed_codex_mcp_servers`,
and `build_codex_installed_graph` — the two Codex-specific seed functions this
task builds, plus the one function that owns their lifecycle, calling Task 5's
`_seed_shared_endpoint_surfaces`); `tests/test_graph_build_codex.py`.

This task stays entirely at the `graph_build` level — `tools/agent_kinds/codex.py`
does not exist yet (Task 11 creates it) and no step here depends on it. Task 11's
`_compose` calls `build_codex_installed_graph` — the composer built in this task's
Step 10 — once, the same shape Cursor's endpoint fork already uses
(`tools/agent_kinds/cursor.py`'s `_compose` calling `build_cursor_graph` directly,
which likewise builds its own root/graph/normalizer and finalizes once — see
`tools/graph_build_cursor.py`'s `_build_cursor_installed`).

**Consumes:** Task 5's `EndpointSurface` and `_seed_shared_endpoint_surfaces`,
Task 1's `load_config`.

Per ADR-0057, plugin acquisition and remote MCP are forked, not parameterised:
Claude Code's `_seed_active_plugins` walks the *enable map* and only ever emits
a node for a `True` entry, while Codex must walk the *cache directory* first to
inventory disabled bundles too (ADR-0055) — a different traversal order over a
different source; likewise Codex's MCP servers come from `config.toml` via
Task 1's `load_config` rather than Claude's JSON settings layers. Neither
function is wired into `_seed_endpoint`'s dispatch and neither ever will be —
they are standalone functions in `graph_build.py`, called directly from this
task's own `build_codex_installed_graph` (which Task 11's
`tools/agent_kinds/codex.py`'s `_compose` calls in turn), never through
`EndpointSurface`. `build_codex_installed_graph` never calls `_seed_endpoint`
itself — only Task 5's `_seed_shared_endpoint_surfaces`, which covers exactly
the two literal-substitution branches without also running Claude's
plugin/remote-MCP acquisition against Codex's config root.

- [x] **Step 1: Write failing tests** against a fixture `$CODEX_HOME`: two cached
  bundles (one `.codex-plugin`, one **only** `.claude-plugin`), both enabled; one
  bundle present in the cache but `enabled = false`; one user skill; one
  `skills/.system/` skill; one `agents/x.toml`.
- [x] **Step 2: Add `_seed_cache_plugins(graph, target, config_root, normalize,
  *, warnings=...)`** in `tools/graph_build.py`, next to the existing
  `_seed_active_plugins`, as a standalone function callable directly (not yet
  wired into `_seed_endpoint` or `EndpointSurface`): it walks
  `plugins/cache/<mkt>/<name>/<ver>/` as the traversal root (not the enable
  map), cross-referencing `[plugins."<name>@<mkt>"]` for enable state.
  **Assert the enable contract** by calling this function directly against
  the Step 1 fixture: all three bundles appear as `plugin` nodes; the disabled
  one carries `extra["enabled"] is False`, the others `True`. **A disabled
  plugin is still inventoried** (ADR-0055) — Codex's model differs here from
  Claude Code's, which walks `enabledPlugins` and only ever emits refs for
  entries whose value is `True`; Codex cannot reuse that walk order, which is
  exactly why this function exists as its own traversal rather than a
  parameter to `_seed_active_plugins`.
- [x] **Step 3: Assert `.system` is excluded** — the marker file is the test, not the
  skill's name. Add a second `.system` skill with an innocuous name to prove the
  exclusion is structural.
- [x] **Step 4: Assert `.claude-plugin`-only bundles realize**, proving the ordered
  candidate fallback works end to end. Add the negative-first/valid-second case
  explicitly: a bundle carrying a `.codex-plugin/plugin.json` that fails to
  qualify (e.g. malformed, or missing the required `name` field) alongside a
  qualifying `.claude-plugin/plugin.json` must realize from the second candidate,
  not fall through to "unrealized." A bundle whose *only* candidate is malformed
  records a parse failure instead.
- [x] **Step 5: Marketplace identity from `[marketplaces.*]`.** A plugin
  realized from marketplace `m` named `n` takes `plugin/{m}/{n}` and sets
  `extra["marketplace"] = m` **before** node creation, so bundled children
  inherit the namespace.
- [x] **Step 6: Assert bundled identity cascades** — a skill inside a marketplace
  bundle gets a plugin-private identity, not `None`.
- [x] **Step 7: Reconcile the enable-map and marketplace-registry against the
  cache, explicitly, for every mismatch a real endpoint can show:**
  - A cached bundle with **no** `[plugins."<name>@<mkt>"]` table at all: still
    inventoried (same "absent is not false" rule as Task 1's MCP default),
    `extra["enabled"] is True`, plus a graph warning noting the cache entry has no
    enable-map record.
  - A `[plugins.*]` `enabled` value that is present but **not a boolean**: treat
    as absent (falls back to `True`), plus a graph warning — same shape as
    `claude_install`'s `"enabledPlugins.{plugin_key} must be a boolean"`.
  - A `[plugins.*]` entry naming a plugin **with no matching cache bundle**: not a
    node (there is nothing on disk to inventory), plus a graph warning — same
    shape as `claude_install`'s `"plugin {plugin_key} enabled but missing from
    installed_plugins.json"`.
  - A cached bundle whose path-segment marketplace has **no** `[marketplaces.*]`
    entry: still inventoried (everything installed is inventoried), but **must
    not** set `extra["marketplace"]` or take `plugin/{mkt}/{name}` identity —
    doing so would grant cross-BOM identity from a path segment, exactly what the
    global constraint above rules out. It falls through to
    `canonical_component_identity`'s non-marketplace branches. Add a graph warning
    noting the orphaned marketplace segment. Add a test asserting the resulting
    ref's `extra` has no `marketplace` key and its identity is not
    `plugin/{path-segment}/{name}`.
- [x] **Step 8: Add `_seed_codex_mcp_servers(graph, target, config_root,
  project_root, normalize, *, warnings=...)`** in `tools/graph_build.py`, as a
  second standalone function (not yet wired): reads `<config_root>/config.toml`
  and, when `project_root` is given, `<project_root>/.codex/config.toml`, via
  Task 1's `load_config`. Assert this directly, calling the function against a
  fixture rather than through `_seed_endpoint`.
- [x] **Step 9: Surface `.rules` parse coverage during composition, not only at
  posture time.** `<root>/rules/*.rules` is posture-only (no `ComponentRef`), but
  a Codex installed agent's graph still has a `warnings: list[str]` threaded
  through it, and `scan endpoint`, `bom endpoint`, and `remote sync endpoint` each
  already fold that list into `evidence_gaps` via `resolve_coverage` — the same
  mechanism that already carries non-component warnings like a malformed
  `enabledPlugins` value. During `_seed_cache_plugins`, call Task 3's
  `parse_rules` over every file under `<root>/rules/`, and when the returned
  `ParsedRules.unparsed_count > 0`, append one warning per file (e.g. `f"{path}: {n} unparsed
  rule(s)"`) to that same list — **unconditionally**, not gated behind
  `--include-posture`. This is the only change needed for the gap to reach all
  three commands' coverage verdicts, because all three already consume the same
  warnings list; no changes to `tools/scan.py`, `tools/bom_cli.py`, or
  `tools/remote/collector.py` are required for this specifically. Add a
  graph-level test asserting that an unknown rule form appends exactly this
  warning to `build_codex_installed_graph`'s returned `warnings` list — this
  task stays at the `graph_build` level (see the task preamble) and cannot
  itself invoke `scan endpoint`/`bom endpoint`/`remote sync endpoint`, since
  `tools/agent_kinds/codex.py` does not exist until Task 11 registers the
  kind those commands discover. Task 12 Step 2(d) owns the three-command
  parity proof that this warning actually lowers `coverage`, once composition
  can run through the CLI.
- [x] **Step 10: Add `CODEX_ENDPOINT`** to `tools/endpoint_surface.py`,
  carrying Task 5's project-skill-subdirectory and direct-component fields for
  Codex's values (`.codex/skills`, Codex's direct-component subdirectory
  names, `seeds_hooks=False` — Codex's hooks are declared-only, per Task 6) —
  no acquisition-related fields, since plugin acquisition and remote MCP never
  go through `EndpointSurface` (ADR-0057). **Add
  `build_codex_installed_graph(config_root, project_root, *,
  include_gitignored=False, warnings=None) -> Graph`** to
  `tools/graph_build.py` as the one function that owns the whole
  installed-composition lifecycle, mirroring `build_rooted_graph`'s own
  endpoint branch and `graph_build_cursor.py`'s `_build_cursor_installed`
  rather than calling `build_rooted_graph` itself:
  1. Build `root = Node(key=..., kind="target", ref=None)`,
     `graph = Graph(nodes={root.key: root})`, and
     `normalize = _make_normalizer("endpoint", config_root, config_root,
     project_root, root_label)` — the same three lines `build_rooted_graph`'s
     endpoint branch uses internally, now exposed to this function's own body.
  2. Call `_seed_shared_endpoint_surfaces(graph, root, config_root,
     project_root, normalize, CODEX_ENDPOINT, by_scope=None,
     warnings=graph.warnings)` for the two literal-substitution branches
     (project skills, direct components) — **never call `_seed_endpoint`
     itself here**: `_seed_endpoint` unconditionally also runs Claude's
     active-plugin and remote-MCP acquisition (Task 5 Step 5), which would
     read `config_root` as if it were a Claude install root and produce
     Claude-shaped plugin/MCP nodes on a Codex graph. `by_scope=None` is safe
     because `CODEX_ENDPOINT.seeds_hooks` is `False`, so
     `_seed_shared_endpoint_surfaces` never reads it.
  3. Call `_seed_cache_plugins(graph, root, config_root, normalize,
     warnings=graph.warnings)` and `_seed_codex_mcp_servers(graph, root,
     config_root, project_root, normalize, warnings=graph.warnings)` directly
     against that same `root`/`normalize`, for the two forked branches.
  4. Call `finalize_graph(graph, config_root, normalize,
     project_root=project_root, include_gitignored=include_gitignored,
     attach_include_gitignored=True, root_dir=None, root_spec=None,
     warnings=warnings)` **exactly once**, after every seed above has run —
     so MCP launch-dependency attachment and validation see the complete
     graph, including Codex's own MCP server refs, and every seed's warnings
     reach the caller's `warnings` list through the one copy `finalize_graph`
     already does.

  Prove `_seed_cache_plugins`/`_seed_codex_mcp_servers` combine correctly with
  the parameterised branches by calling `build_codex_installed_graph` once
  against the Step 1 fixture's `config_root` — this single call is what Task
  11's `codex.py`'s `_compose` will make with `agent.config_root`/
  `agent.project_root` in place of the fixture paths. Assert every node from
  all four branches is present in one graph, that a Codex MCP server ref
  resolvable to a project package gets its ADR-0039 launch-dependency child
  (proving `_attach_mcp_launch_deps` ran after the forked seeds, not before),
  and that a warning appended by any one of the four seeds appears exactly
  once in the returned `warnings` list.
- [x] **Step 11: Add a decoy-fixture test.** A `$CODEX_HOME` fixture that
  *also* contains a Claude-shaped `settings.json`, `installed_plugins.json`,
  a standalone `.mcp.json`, a `commands/` directory, and a project
  `.claude/skills/` entry must emit **none** of those as nodes — proving the
  wiring actually dispatched to the Codex functions rather than merely that
  the Codex functions also happen to produce correct output alongside
  Claude's untouched ones.
- [x] **Step 12: Compose the project layer, as a separate fixture from Step
  1's** (which has no `project_root`). Add a fixture project directory with
  `.codex/config.toml` declaring one `[mcp_servers.*]` entry and
  `.codex/skills/demo/SKILL.md`, and call Step 10's
  `build_codex_installed_graph` with that path as `project_root`. Per the spec's endpoint surface
  table, MCP servers and skills are the only two surfaces sourced from
  **both** the user root and the project root in installed mode — project
  hooks (`.codex/hooks.json`) are `declared`-only and must **not** be
  composed here. Assert: the project skill appears as a `skill` child of the
  target (via the project-skill-subdirectory field Task 5 added to
  `EndpointSurface`); the project MCP server appears as a `mcp_server` child
  (via `_seed_codex_mcp_servers`); and a same-named MCP server declared in
  both resolves to the project entry, matching `_seed_endpoint`'s existing
  "project entries take precedence over install-root entries" rule. The
  precedence test must assert both the winning occurrence's full content and
  its `source_manifest` points at the project `config.toml`, not merely that
  one node exists under that name.
- [x] **Step 13: Run tests, confirm pass. Commit.**

## Task 8: Registry entries and parse accounting

**Files:** Modify `tools/parsers/__init__.py`; extend
`tests/test_parsers/test_registry_matcher.py`.

- [x] **Step 1: Write failing tests** asserting `CODEX_MANIFEST_REGISTRY` claims
  `**/.codex/config.toml`, `**/.codex/hooks.json`, `**/.codex/skills/**/SKILL.md`,
  `**/.codex-plugin/plugin.json`, and `**/.claude-plugin/plugin.json` — and that it
  contains **no bare `mcp.json`/`.mcp.json` pattern** (Codex's MCP is inside
  `config.toml`; a bare pattern would recreate the cross-kind collision the per-agent
  model prevents). Assert the message on that test explains why.
- [x] **Step 2: Add the registry** plus `_parse_repo_codex_*` wrappers. Use
  `strict=True` where composition parses strictly, so malformed files register as
  `n_failed` and reach `evidence_gaps`.
- [x] **Step 3: Assert counts mirror composition** — a `.codex/skills` tree that
  composition walks recursively must be counted recursively.
- [x] **Step 4: Assert plugin-owned content is excluded** from Codex's counts, via the
  existing `_entry_claims` chokepoint and `CODEX_SURFACE.excludes_plugin_owned_content`.
- [x] **Step 5: Run tests, confirm pass. Commit.**

## Task 9: Installed source-unit accounting

**Files:** Modify `tools/scan.py`, `tools/bom_cli.py`, `tools/remote/collector.py`,
`tools/render.py`; extend `tests/test_scan.py` (or the equivalent existing suite
for each) and `tests/test_render.py`.

Every installed-agent path labels its plugin count `"active plugin"` and counts it
as `sum(1 for r in refs if _is_plugin_ref(r))` — every plugin ref, full stop. That
label is accurate for Claude Code and Cursor, whose refs only ever exist for
plugins already known to be active. It stops being accurate the moment a kind
inventories disabled plugins too (ADR-0055), which Task 7 now does for Codex: an
endpoint with several disabled plugins would report them all as "active."

- [x] **Step 1: Write a failing test** against synthetic `ComponentRef`s — no
  kind needs to exist or be registered for this: build refs directly with
  `_is_plugin_ref`-satisfying `extra["component_type"] = "plugin"` and
  `extra["enabled"]` set to `True`, `False`, and omitted, and assert the
  count predicate keeps the first and third but drops the second. Retain the
  existing Claude Code and Cursor CLI-level regression assertions (their
  counts must stay unchanged by this task) alongside the new synthetic-ref
  test; neither needs a Codex fixture, since the Codex CLI-level proof
  belongs to Task 12 Step 2(b), which runs after Task 11 registers the kind.
- [x] **Step 2: Extract the shared predicate.** Add
  `_count_active_plugins(refs: list[ComponentRef]) -> int` to
  `tools/scan.py` as `sum(1 for r in refs if _is_plugin_ref(r) and
  r.extra.get("enabled") is not False)`, and call it from all four existing
  sites instead of each inlining its own `sum(1 for r in refs if
  _is_plugin_ref(r))`: `tools/scan.py` has two independent sites — the
  rendered scan-stats `unit_count` and the scan-produced BOM's
  `source_unit_count` — and both must call it directly; `tools/bom_cli.py`
  adds it to its existing `from tools.scan import ...` line, and
  `tools/remote/collector.py` adds a new `from tools.scan import
  _count_active_plugins` (no circular import — `tools/scan.py` does not
  import from `tools/remote`) and drops its own duplicate `_is_plugin_ref`
  in favor of the shared one. For
  Claude Code, every plugin ref already has `enabled` `True` by construction
  (`_walk_active_plugins` never emits a ref for a non-`True` entry), and
  Cursor's plugin refs carry no `enabled` key at all (`is not False` keeps
  them) — so this changes nothing for either shipped kind. Run the full
  Claude Code and Cursor suites and confirm the counts are byte-identical to
  before this change.
- [x] **Step 3: Fix the component-tree header.** `render_inventory_tree`
  (`tools/render.py:1714-1734`) independently selects every plugin ref with
  no `enabled` filter, sets `n_plugins = len(plugins)`, and renders it as
  `"active plugin"` — a third call site that Step 2's `_count_active_plugins`
  doesn't reach, because it feeds a tree-formatting computation, not a
  scan-stats or BOM accounting one. Compute
  `n_active = sum(1 for r in plugins if r.extra.get("enabled") is not False)`
  alongside the existing `n_plugins`. When `n_active == n_plugins` — true for
  every existing Claude Code and Cursor fixture, and for any Codex endpoint
  with no disabled plugin — keep today's line unchanged, byte-identical:
  `"{n_plugins} active plugin(s), ..."`. When `n_active < n_plugins` (only
  reachable once a kind inventories disabled plugins, i.e. Codex), swap the
  plugin segment to `"{n_plugins} plugin(s) ({n_plugins - n_active} disabled),
  ..."`, leaving the direct-component and total-component segments as they
  are. This changes only the aggregate header line; each plugin's own tree
  node keeps rendering its `enabled` state exactly as Task 7 leaves it, so a
  disabled plugin stays visible in the tree, it's just no longer counted as
  active in the summary. Confirm `render_repo_inventory_tree`
  (`tools/render.py:1753-1829`) has no equivalent plugin-count summary line —
  it doesn't — so it needs no matching change. Add two `tests/test_render.py`
  cases: an all-enabled fixture asserting the unchanged `"active plugin"`
  wording (regression, covers the existing Claude Code and Cursor callers),
  and a one-enabled/one-disabled fixture asserting the header reads
  `"2 plugins (1 disabled)"` while both plugin nodes remain present in the
  rendered tree.
- [x] **Step 4: Run tests, confirm pass. Commit.**

## Task 10: Posture — command policy, project trust, and the pin field

**Files:** Create `tools/posture/rules/command_policy_allow.py`,
`tools/posture/rules/project_trust.py`, `tests/test_posture_codex.py`,
`tests/test_posture_command_policy_allow.py`,
`tests/test_posture_project_trust.py`; modify `tools/posture/__init__.py`,
`tools/agent_kinds/__init__.py`, `tools/scan.py`, `tools/remote/collector.py`.

This task has two genuinely separate concerns: two new, independent posture
rules for Codex's two policy surfaces (10A), and one existing rule's
plugin-branch semantics for Codex's pin field (10C).
`mcp_auto_approve` does **not** apply to Codex (spec: "Posture rule
applicability") — neither `prefix_rule` entries nor `trust_level` name an MCP
server, and `mcp_auto_approve` is not modified or reused by this task. Its
hardcoded `active_in=["cursor"]` is a pre-existing Cursor defect the spec
explicitly scopes out of this plan; it is not touched here.

Both new surfaces are **installed-only**: the spec's Endpoint Surface table
lists `—` under "Declared" for both `rules/*.rules` and
`[projects.*] trust_level`. Neither rule gets a declared collector — a
declared Codex agent's posture prep supplies no manifests for either, and
both checks emit no findings for it by construction (empty input), not by a
special-cased skip. A repo scan must never read `$CODEX_HOME` to populate
either.

**10A — two new rule modules.**

`rule_id` is a policy-gate key (`policy_cli` fails a finding whose `rule_id`
is absent from `risk_gates.posture_rule_ids`), so a command-prefix allow and a
trusted project each need their own id — sharing one would let approving one
concern silently approve the other. The spec names both:
`openaca-posture-command-policy-allow` (reads `rules/*.rules`) and
`openaca-posture-project-trust` (reads `[projects.*] trust_level`).

- [x] **Step 1: Add `tools/posture/rules/command_policy_allow.py`.** `RULE_ID =
  "openaca-posture-command-policy-allow"`. `check_command_policy_allow(manifests)`
  takes a manifest shape carrying the parsed `PrefixRule` list and emits one
  finding per `PrefixRule` whose `decision == "allow"`: `component = {"type":
  "command_policy", "name": " ".join(pattern)}`, title text that reads
  correctly for a command-prefix approval, `component_path=[{"type":
  "command_policy", "name": " ".join(pattern)}]`,
  `standards=Standards(owasp_agentic_top10=["asi03"])` (no `owasp_mcp_top10`
  tag — this is not an MCP-specific exposure), and remediation text
  describing narrowing or removing the command-prefix allow rule.
  `PostureFinding.component` is a display label, not a `ComponentRef`-shaped
  taxonomy field (`api_endpoint_override` already uses `"type":
  "agent_config"`, outside the closed component-type set), so `"command_policy"`
  requires no new component type. Dedup by `(rule_id, component)` — the
  existing dedup key shape.
- [x] **Step 2: Write a failing test** that `prefix_rule(..., decision="allow")`
  produces a finding, `decision="deny"` does not, and an unparsed rule form
  produces neither a false allow nor a false deny (Task 3's `.rules` parser
  already skips-and-counts unparsed forms rather than guessing; this test
  confirms the posture layer inherits that same conservatism instead of
  reinterpreting the skipped content).
- [x] **Step 3: Add `tools/posture/rules/project_trust.py`.** `RULE_ID =
  "openaca-posture-project-trust"`. `check_project_trust(manifests)` takes a
  manifest shape carrying `[projects.*] trust_level` and emits one finding per
  project whose `trust_level == "trusted"`: `component = {"type": "project",
  "name": <project path>}`, title text that reads correctly for project trust,
  the same `owasp_agentic_top10`-only `standards`, and remediation text
  describing narrowing or removing project trust.
- [x] **Step 4: Write a failing test** that `trust_level = "trusted"` produces
  a finding and that an untrusted or absent project does not.
- [x] **Step 5: Add the collectors — installed only.** The existing
  `(mcp_collector, settings_collector)` 2-tuple contract
  (`tools/agent_kinds/__init__.py`'s `PostureCollectors`/
  `InstalledPostureCollectors`) has no room for a third or fourth manifest
  channel — `AgentScanPrep` (`tools/scan.py`) and `_agent_posture_manifests`
  (`tools/remote/collector.py`) both unpack exactly two collectors and thread
  exactly two manifest lists into `run_posture_rules`. Extend the contract
  explicitly rather than overload either existing slot:
  - Add `extra_installed_posture_collectors:
    Mapping[str, InstalledPostureCollector] | None = None` to `AgentKind`
    (`tools/agent_kinds/__init__.py`), keyed by the collector's own rule id.
    `None` (the default) means the kind has no such surfaces — every existing
    kind is unaffected.
  - Add `collect_codex_rules_manifests(config_root, project_root, refs)` and
    `collect_codex_project_trust_manifests(config_root, project_root, refs)`
    to `tools/posture/__init__.py`, each reading its surface directly (the
    documented posture-only exception — spec: "Posture derives from
    composition") rather than deriving from `refs`, since neither surface
    produces a `ComponentRef`. This is a second, independent read from Task 7
    Step 9's composition-time read (which reads only
    `ParsedRules.unparsed_count` for a coverage warning); these read
    `ParsedRules.rules` and `[projects.*]` for actual finding content. The
    duplication is deliberate: one is a coverage signal, the other is finding
    content, and neither can be derived from the other.
  - Add `extra_manifests: dict[str, list[tuple[Path, dict]]]` to
    `AgentScanPrep`. In `_agent_scan_prep`'s installed branch
    (`tools/scan.py`), populate it from
    `kind.extra_installed_posture_collectors` (empty dict if `None`); in the
    declared branch, it is always `{}` — no filesystem read of `$CODEX_HOME`
    happens for a repo scan.
  - Give `run_posture_rules` (`tools/posture/__init__.py`) an
    `extra_manifests: Mapping[str, list[tuple[Path, dict]]] | None = None`
    keyword parameter; call `command_policy_allow.check_command_policy_allow`
    and `project_trust.check_project_trust` against
    `(extra_manifests or {}).get(RULE_ID, [])` for their respective rule ids
    — the same per-argument pattern every existing rule call already uses.
  - Update both call sites that build this dict and pass it through:
    `tools/scan.py`'s `_scan_discovered_agents`
    (`run_posture_rules(refs, prep.manifests, prep.settings_manifests,
    extra_manifests=prep.extra_manifests, ...)`) and
    `tools/remote/collector.py`'s `_build_agent_collection`, which needs its
    own `extra_manifests` lookup added next to `_agent_posture_manifests`
    before its `run_posture_rules` call. **`tools/bom_cli.py` needs no change
    here** — `bom endpoint`/`bom repo` have no `--include-posture` option and
    never call `run_posture_rules` (`scan bom` explicitly rejects
    `--include-posture`), so no posture payload of any kind reaches BOM
    output today.
- [x] **Step 6: Register both rules.** Add `command_policy_allow.RULE_ID` and
  `project_trust.RULE_ID` to `tools/posture/__init__.py`'s `KNOWN_RULE_IDS`,
  and call both `check_*` functions unconditionally inside `run_posture_rules`
  (filtered afterward by `allowed_rules`, exactly like every existing rule).
- [x] **Step 7: Run tests, confirm pass. Commit.**

**10C — `mutable_install`'s plugin branch.**

- [x] **Step 8: Establish `last_revision`'s external semantics before deciding
  `mutable_install`'s plugin branch — this is a fact about Codex, not about
  this repository's code.** `_mutable_install_source_for`
  (`tools/posture/rules/mutable_install.py`) shows how the *existing*,
  Claude-Code-specific check works (`"gitCommitSha" in ref.extra`, an empty
  value means mutable); reading it establishes nothing about whether Codex's
  `[marketplaces.*] last_revision` is a resolved, immutable commit for
  `source_type = "git"`, what — if anything — it means for
  `source_type = "local"`, or whether it can ever be absent for a
  successfully synced marketplace. That is external, third-party behavior and
  needs the same evidence method Task 1 Step 1 and Task 3 Step 2 use — the
  audited binary or a live endpoint, not this repository's own code or prior
  draft text asserting it. **If that evidence establishes the semantics**,
  add a Codex-specific branch to `mutable_install` and pin the behaviour with
  a test covering `source_type = "git"` with a resolved SHA,
  `source_type = "git"` with a missing or branch-like value, and
  `source_type = "local"`. **If the evidence is unavailable or inconclusive,
  do not add a Codex branch to `mutable_install`** — leave Codex plugins
  covered only by the rule's existing, kind-agnostic `install_source` check
  (unaffected by this decision either way), record the `last_revision` branch
  as deferred pending that evidence, and add a test asserting a Codex
  marketplace plugin carrying a `last_revision` value does not spuriously
  fire the rule through the unrelated `gitCommitSha` branch, which Codex refs
  never set.
- [x] **Step 9: Run tests, confirm pass. Commit.**

## Task 11: The Codex kind module and registration

**Files:** Create `tools/agent_kinds/codex.py`, `tests/test_agent_kinds_codex.py`;
modify `tools/agent_kinds/__init__.py`.

- [x] **Step 1: Write failing tests** for `declared_evidence`: a repo with
  `.codex/hooks.json` is evidence; one with only `AGENTS.md` is **not**; one with only
  `.claude-plugin/plugin.json` is **not** (that is a Claude Code declaration);
  a `.codex/skills/*/SKILL.md` bundled inside a realized plugin is **not** (reuse the
  ownership exclusion).
- [x] **Step 2: Write `resolve_config_root`** — `config_dir` if given, else
  `$CODEX_HOME`, else `~/.codex`. **`root_override_refusal = None`** (ADR-0056).
- [x] **Step 3: Test `$CODEX_HOME` is honoured** and that `--config-dir` overrides it.
- [x] **Step 4: Write `discover`** with installed and declared branches, mirroring
  `tools/agent_kinds/cursor.py`. Installed: the root existing is the evidence, and an
  empty root still yields an instance.
- [x] **Step 5: Write `_compose`**, mirroring `claude_code.py`'s shape for the
  declared branch and `cursor.py`'s shape for the installed branch — a single
  delegating call, not an inline sequence. Declared calls
  `build_rooted_graph(agent.scan_root, "repo", root_key=..., root_label=...,
  include_gitignored=..., warnings=..., surface=CODEX_SURFACE)`, exactly as
  `claude_code.py`'s `_compose` does for its own declared branch. Installed
  calls Task 7's `build_codex_installed_graph(agent.config_root,
  agent.project_root, include_gitignored=..., warnings=...)` once — the one
  function that already runs both literal-substitution branches and both
  forked branches (plugin acquisition, remote MCP) against one graph before
  finalizing, exactly parallel to how `cursor.py`'s `_compose` delegates its
  own installed branch to `graph_build_cursor.build_cursor_graph` in one call.
- [x] **Step 6: Set `COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}`.**
  `partial` at `installed` is **not** for Cursor's reason — enable state is readable
  here. It is for the six gaps in the spec's Coverage table, two of which
  (`managed_config.toml`, profile layering) are closable later. Add a test asserting
  both values and referencing that table, so a future change that closes a gap has to
  revisit the baseline deliberately rather than by accident.
  **Revisited as this step intended:** the profile layer that hid MCP servers is
  now composed, so the shipped baseline is `{"installed": "complete", "declared":
  "complete"}` — see `426995d`, `3d6513e`, `6039293` and the reasoning block in
  `tools/agent_kinds/codex.py`.
- [x] **Step 7: Assemble `KIND`** with all fields; `posture_rules` is
  `{insecure_transport, mutable_install, skill_capability,
  command_policy_allow, project_trust}` — **not** `api_endpoint_override` and
  **not** `mcp_auto_approve` (spec: Posture rule applicability — neither of
  Codex's policy surfaces is MCP-specific, and the two new rule ids from
  Task 10A cover them instead).
- [x] **Step 8: Register in `_registry()`** — add the import and the tuple entry.
- [x] **Step 9: Assert three kinds are registered** and that Claude Code's and Cursor's
  `manifest_patterns` are unchanged.
- [x] **Step 10: Run the full suite. Commit.**

## Task 12: CLI, e2e, and docs

**Files:** `tools/scan.py`, `tools/bom_cli.py`, `docs/reference/cli.md`,
`docs/reference/coverage.md`, `CLAUDE.md`, `tests/test_e2e.py`.

- [x] **Step 1: Confirm `--kind codex` works** with and without `--config-dir`; add a
  CLI test asserting `--config-dir --kind codex` is **accepted** (unlike Cursor).
- [x] **Step 2: Add exactly four e2e tests** per the repo's e2e boundary. (a) *Declared,
  three kinds, one repo*: a fixture with `.claude/skills/…`, `.cursor/mcp.json`, and
  `.codex/hooks.json` emits three BOMs with the right `openaca:agent_kind` values.
  (b) *Installed, Codex*: `scan endpoint --kind codex --project PATH` against a
  fixture `$CODEX_HOME` plus a fixture project directory (`.codex/config.toml`
  with one MCP server, `.codex/skills/demo/SKILL.md`) renders an agent card whose
  plugin components carry a real `enabled` property, including one `false`, and
  whose children include both the project MCP server and the project skill
  alongside the user-root ones — proving Task 7 Step 12's project-layer
  composition end to end through the actual CLI flag, not only through
  `graph_build`'s focused tests. With one enabled and one disabled plugin
  bundle in the fixture, assert the CLI's reported `"active plugin"` count is
  **one**, not two, **and** that the rendered agent BOM's
  `openaca:source_unit_count` is also **one** — the rendered scan-stats count
  and the scan-produced BOM count are two independent call sites in
  `tools/scan.py` (Task 9 Step 2), so both need a real-discovery assertion
  here; this is Task 9's `_count_active_plugins` proved through
  real discovery and the registered Codex kind, which Task 9 itself cannot
  exercise before Task 11 registers it. Also assert the rendered component
  tree's header line reads `"2 plugins (1 disabled)"`, not
  `"2 active plugins"`, and that both plugin nodes are still present in the
  tree — this is Task 9 Step 3's renderer fix proved through the same
  real-discovery fixture. (c) *Installed,
  Codex, remote sync*: the same fixture, with a `rules/*.rules` file
  containing a `prefix_rule(..., decision="allow")` and a `[projects.*]`
  entry with `trust_level = "trusted"` added, pushed through
  `remote sync endpoint` preserves the Codex kind, its disabled-plugin
  inventory, and its coverage verdict, and produces both
  `openaca-posture-command-policy-allow` and `openaca-posture-project-trust`
  findings — with neither `mcp_auto_approve` nor `api_endpoint_override` in
  the payload — through the upload payload. This is the one command Task 5
  through Task 11 never exercise directly, and the goal names it.
  (d) *Installed, Codex, coverage parity without posture*: a fixture whose
  `.rules` directory contains an unrecognised rule form emits the same lowered
  `coverage` from `scan endpoint`, `bom endpoint`, and `remote sync endpoint`
  **without** `--include-posture` on any of them — proving Task 7 Step 9's
  composition-time warning, not a posture-only side effect, is what carries the
  gap.
- [x] **Step 3: Add a regression test** that a Codex MCP server with
  `enabled = false` is inventoried (appears as a `mcp_server` node) but produces
  no `insecure_transport` or other active-exposure posture finding — the spec's
  explicit "posture must not re-walk disabled servers" constraint.
- [x] **Step 4: Update the docs** — Codex rows in `docs/reference/cli.md` and
  `coverage.md`, and the parser-set line in `CLAUDE.md`.
- [x] **Step 5: Run the full suite, ruff, and pyright. Commit.**

## Testing

```
uv run pytest tests/ -q
uv run ruff format --check . && uv run ruff check .
uv run pyright
```

**Task 5 is the regression gate** — the golden-graph fixture must be byte-identical
before and after, with zero Codex entries in that diff. If it moves, the
parameterisation is wrong; do not update the golden file to match.

Manual smoke against this machine's real endpoint, which is a strong fixture (932M
`~/.codex`, 5 marketplaces, 13 bundles with `.codex-plugin`, 4 with only
`.claude-plugin`, 61 bundled plugin skills, 2 user skills, 6 trusted projects):

```
uv run openaca scan endpoint --kind codex
uv run openaca bom endpoint --output-dir /tmp/boms && ls /tmp/boms
uv run openaca scan repo .
```

Assert against that endpoint specifically: the 4 `.claude-plugin`-only bundles appear;
no component is sourced from `skills/.system/`; every plugin carries an explicit
`enabled`; and `~/.codex/agents/dummy-probe.toml` appears exactly once as an `agent`.
