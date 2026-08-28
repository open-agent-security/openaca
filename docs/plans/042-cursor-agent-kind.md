# Plan 042 — Cursor agent kind

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scan repo`, `scan endpoint`, `bom repo`, `bom endpoint`, and
`remote sync endpoint` each discover and emit a **Cursor** agent alongside the Claude
Code one — its own graph, BOM, coverage verdict, and posture allowlist.

**Architecture:** Repo mode is **parameterised**; endpoint seeding is **forked**. A new
neutral `tools/repo_surface.py` holds frozen descriptors, and `graph_build`'s repo
helpers take `surface: RepoSurface = CLAUDE_CODE_SURFACE` whose values are transcribed
verbatim from today's literals. `_seed_endpoint` is untouched — Cursor brings its own
seed, reached through `cursor.KIND.compose`. `graph_build` still never imports
`agent_kinds` (ADR-0053).

**Tech Stack:** Python 3.11, click, pathspec, pytest, uv. No new dependencies.

**Spec:** `docs/specs/cursor-agent-kind.md`. Read it before starting — especially
[Deliberately out of the first pass](../specs/cursor-agent-kind.md#deliberately-out-of-the-first-pass),
which is the scope boundary this plan implements.
**ADRs:** `0052-cursor-agent-kind.md` (the kind), `0053-repo-surface-descriptor.md`
(the mechanism), `0044`–`0047` (agent-kind mechanism this builds on).

## Context

`tools/agent_kinds/` shipped with one registered kind and a comment reserving the
second seat. Nothing in that mechanism has been exercised by a runtime that is not
Claude Code, so several properties are asserted rather than demonstrated: that a second
config root works, that one runtime reading another's files resolves cleanly, that
coverage resolving per source matters.

Cursor forces all three, and adds one the mechanism has never seen — a surface with no
readable activation state, which is why plugins are presence-only.

Two things make this more than "add a parser". `_seed_endpoint`
(`tools/graph_build.py:334`) is Claude Code by construction, and every repo-mode helper
underneath `descend` (`:691`) hardcodes `.claude`, `.claude-plugin`, and `.mcp.json`.
The first is forked, the second parameterised, for the reasons in ADR-0053.

## Global Constraints

- **Task 1 lands with zero Cursor entries.** The existing Claude Code suite is the
  regression gate, and it only works if the diff contains no new-feature noise.
- **Claude Code's output does not change.** `claude_code.KIND.manifest_patterns` stays
  `tuple(REGISTRY)`; repo-mode `bom-ref`s stay byte-identical. Pin with a golden test.
- **`graph_build` never imports `agent_kinds`.** Kind modules import `graph_build`
  lazily inside `compose`, as `claude_code._compose` already does.
- **The descriptor is data, not behaviour.** A `Callable`-typed field means the design
  has drifted into the strategy object ADR-0053 rejected. `PluginFormat.detect` is the
  one allowed exception ($schema is content, not path shape).
- **Presence-only plugins.** No `enabled`/`active` property is ever emitted for a Cursor
  plugin — absent, not `false`.
- **Cross-reads are composition, never evidence.** A tree with only `.claude/` declares
  no Cursor agent. `.agents/skills/` is the single exception.
- **Scope is the first pass.** Anything in the spec's deferred table stays deferred;
  adding it "while we're here" is out of scope for this plan.
- **One project root per invocation.** `DiscoveryContext.project_root` and every
  `--project`/`--config-dir` pairing stay singular in this plan, even though the spec
  states project roots are per workspace folder and never just the first. A multi-root
  workspace scan sees only the one root it was pointed at; nothing composes across
  workspace folders it was not given. Widening `project_root` to a sequence, and
  resolving precedence across several roots, is follow-up work this plan does not
  implement.
- Default to writing no comments; add one only where the *why* is non-obvious.
- **Every task ends green on**
  `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

## File Structure

| File | Responsibility |
|---|---|
| `tools/repo_surface.py` (new) | `RepoSurface`, `PluginFormat`, `BundledLayout`; `CLAUDE_CODE_SURFACE`, `CURSOR_SURFACE` |
| `tools/graph_build.py` | `surface=` on repo-mode helpers; `finalize_graph` extraction; `_make_normalizer(extra_roots=)` |
| `tools/graph_build_cursor.py` (new) | Cursor's declared and installed composition |
| `tools/parsers/agent_plugins.py` (new) | Agent Plugins open-standard manifest |
| `tools/parsers/__init__.py` | Three-way registry split; `ManifestPattern` with a content guard; `pathspec`-backed matcher |
| `tools/parsers/claude_plugin_root.py` | Bundled MCP filename becomes a parameter |
| `tools/cursor_subagents.py` (new) | Relative-path precedence resolver — subagents |
| `tools/cursor_commands.py` (new) | Relative-path precedence resolver — commands |
| `tools/agent_kinds/cursor.py` (new) | Discovery, compose, coverage, posture allowlist |
| `tools/agent_kinds/__init__.py` | Register the kind; `DiscoveryContext.kind_id` |
| `tools/posture/__init__.py` | `no_manifests` promoted; Cursor collectors; `permissions.json` resolution |
| `tools/posture/rules/mcp_auto_approve.py` | Branch on manifest shape; read `permissions.json` |
| `tools/scan.py`, `tools/bom_cli.py` | `--kind`; single-walk scan-wide totals |
| `tools/remote/cli.py`, `tools/remote/collector.py` | `--kind` on `remote sync endpoint`; kind-aware endpoint collection |
| `tests/test_repo_surface.py`, `tests/test_cursor_subagents.py`, `tests/test_cursor_commands.py`, `tests/test_graph_build_cursor.py`, `tests/test_parsers/test_agent_plugins.py` (new) | Per-module coverage |
| `tests/test_e2e.py` | Two cross-layer additions, no more |
| `docs/reference/cli.md`, `docs/reference/coverage.md`, `CLAUDE.md` | `--kind`, Cursor coverage row, parser-set line |

---

## Task 1: Parameterise repo mode — no Cursor entries

**Files:** create `tools/repo_surface.py`; modify `tools/graph_build.py`,
`tools/parsers/claude_plugin_root.py`; create `tests/test_repo_surface.py`

This task must be reviewable on its own. If Claude Code's output changes at all, the
refactor is wrong — and that signal is only clean while the diff has no Cursor in it.

- [x] **Step 1: Write the descriptors.** `BundledLayout`, `PluginFormat`, `RepoSurface`
      as frozen dataclasses, per ADR-0053. Import only parser leaves.
- [x] **Step 2: Transcribe `CLAUDE_CODE_SURFACE` verbatim** from `_STANDALONE_MCP_FILENAMES`
      (`:1348`), `_COMMAND_AGENT_SURFACES` (`:1354`), and the `.claude`/`.claude-plugin`/
      `.mcp.json` literals. No behaviour change intended, none permitted.
- [x] **Step 3: Extract `finalize_graph`** — the `build_rooted_graph` tail (name index,
      project-root merge, `_attach_mcp_launch_deps`, `validate`). Cursor's builder calls
      the same function so ADR-0039 launch resolution cannot fork.
- [x] **Step 4: Add `extra_roots` to `_make_normalizer`** (`:138`), matched
      longest-path-first after `project_root` and before `install_root`. Claude passes
      `()`; output must be identical.
- [x] **Step 5: Thread `surface` through** `descend` (`:691`), `_find_plugin_roots`
      (`:825`), `_descend_into_plugin` (`:999`), `_add_project_skills` (`:1029`),
      `_is_project_skill_md` (`:1079`), `_add_repo_standalone_components` (`:1361`),
      `_is_claude_settings_json` (`:1428`), `_command_agent_kind` (`:1437`),
      `_add_bundled_plugin_surfaces` (`:1451`), `_plugin_manifest_data` (`:1509`).
- [x] **Step 6: Parameterise the bundled MCP filename** in
      `claude_plugin_root._parse_default_mcp`. This is the one deliberate crossing of the
      placement/content boundary — ADR-0053 says it should stay the only one.
- [x] **Step 7: Publish the shared construction surface** — public aliases for the
      helpers Cursor's builder needs, so a cross-module private import is never the
      contract.

**Verification:** existing `tests/test_graph_build.py`, `test_graph_build_agent.py`,
`test_scan.py`, `test_e2e.py` pass untouched. New: build the same fixture repo's
Claude graph on `main` and serialize it to a checked-in golden JSON file
*before* starting Task 1's refactor; the new test loads that golden file and
asserts the post-refactor graph serializes identically, so the regression test
is reproducible from the fixture and the golden artifact alone, not from
re-running the pre-refactor code. Also assert
`claude_code.KIND.manifest_patterns == tuple(REGISTRY)` against a frozen
literal.

---

## Task 2: Agent Plugins parser

**Files:** create `tools/parsers/agent_plugins.py`, `tests/test_parsers/test_agent_plugins.py`

- [x] **Step 1: `is_agent_plugins_manifest`** — **full-match** against an
      **allowlist of supported versions**, currently `{"1.0.0"}`, not a free version
      segment. §5.2 of the standard is normative: a client that does not support the
      declared version *"MUST reject the plugin"*, so an accept-any-version regex would
      parse a future 2.0.0 manifest under 1.0.0 semantics. Never match on origin prefix.
      Reject 1.1.0 — it is a Working Draft.
- [x] **Step 2: `validate_manifest`** — a schema-recognized `plugin.json` is not yet a
      plugin. §5.3/§5.5 are normative and specific: `name` is required, 1–64 characters,
      **lowercase** alphanumerics/hyphens/periods only, first and last characters
      alphanumeric, no consecutive hyphens or periods. `My-Plugin` and `-start` are
      invalid. Other permitted fields MUST match their declared types.
      §5.2 splits failures in two, and the split is the point:
      - **Fatal** — any schema violation other than the two below: *"the client MUST
        reject the plugin and MUST NOT discover or execute any of its components."*
      - **Non-fatal** — an unknown top-level field, or a non-object `extensions`:
        *"MUST report and ignore... and MUST continue loading the plugin."*
- [x] **Step 3: `parse` — skills.** **Immediate child directories** of `skills/` holding
      a `SKILL.md`, read only from a manifest that passed Step 2. §7.1: *"Clients MUST
      NOT recursively search deeper descendants"* — the inverse of Cursor's own skill
      roots, which are recursive, so these must not share a walker. Commands, agents,
      hooks, and rules are outside the portable contract and must not be walked even
      when present.
- [x] **Step 4: `parse` — portable MCP.** The bundled `mcp.json` is **not** a plain
      `mcpServers` map and must not go straight to the shared dispatch. §7.2.1: it
      *"MUST be a JSON object containing the required `$schema` and `mcpServers` fields,
      with no other top-level fields"*, and its `$schema` is a **different** URL from the
      manifest's — `.../1.0.0/mcp.schema.json`, whose version must match `plugin.json`'s.
      §7.2.2 makes failure **scoped**, not fatal: on invalid JSON, an unsupported
      version, or a version mismatch with `plugin.json`, *"the client MUST disable MCP
      for that plugin and continue loading other component types."* So a bad `mcp.json`
      costs the servers and keeps the skills. Validate the envelope here, then hand the
      inner `mcpServers` map to the shared dispatch for per-entry parsing and isolation.
- [x] **Step 5: Containment** — `resolve_within` on skill directories, `SKILL.md` files,
      and `mcp.json`, so a symlink escaping the bundle realizes nothing.

**Verification:** `1.0.0` accepted, `1.1.0` and `2.0.0` **rejected**, same-origin
non-schema URLs rejected. Name rules: `My-Plugin`, `-start`, `a--b`, a 65-character
name, and a non-string all reject the plugin with **zero** skills and zero servers.
Fatal-vs-non-fatal: an unknown top-level field and a non-object `extensions` both
**still load** the plugin, while a wrong-typed known field rejects it. Skills: a skill
two levels under `skills/` is **not** found while its immediate-child sibling is;
`parse` ignores a bundled `commands/`. Portable MCP, all scoped to MCP only — the
plugin's skills survive every one: a missing or wrong `mcp.json` `$schema`, an extra
top-level field, a version mismatch against `plugin.json`, and invalid JSON each
disable MCP alone. A single malformed server entry inside an otherwise valid
`mcp.json` drops that entry and keeps its siblings. Symlink escapes realize nothing.

---

## Task 3: Registry split and matcher

**Files:** modify `tools/parsers/__init__.py`, `tools/agent_kinds/__init__.py`

- [x] **Step 1: Three-way split** — `HOST_AGNOSTIC_REGISTRY`,
      `CLAUDE_CODE_MANIFEST_REGISTRY`, `CURSOR_MANIFEST_REGISTRY`, with
      `REGISTRY = [*HOST_AGNOSTIC, *CLAUDE_CODE]` kept as a compat alias so
      `parse_repo`/`parse_repo_grouped` defaults stay byte-identical.
      `HOST_AGNOSTIC` is the five dependency manifests (`package.json`,
      `pyproject.toml`, `package-lock.json`, `uv.lock`, `bun.lock`);
      `CLAUDE_CODE` is everything else in today's `REGISTRY`, unchanged and in
      order. `CURSOR_MANIFEST_REGISTRY` is exactly:

      | Pattern | Parser | Guard |
      |---|---|---|
      | `**/.cursor/mcp.json` | `mcp_json.parse` | — |
      | `**/.cursor/skills/*/SKILL.md` | `claude_skill.parse` | — |
      | `**/.agents/skills/*/SKILL.md` | `claude_skill.parse` | — |
      | `**/.claude/skills/*/SKILL.md` | `claude_skill.parse` | — |
      | `**/.codex/skills/*/SKILL.md` | `claude_skill.parse` | — |
      | `**/.cursor/commands/**/*.md` | command parser | — |
      | `**/.cursor/agents/**/*.md` | agent parser | — |
      | `**/.claude/agents/**/*.md` | agent parser | — |
      | `**/.cursor-plugin/plugin.json` | `claude_plugin.parse` | — |
      | `plugin.json` | `agent_plugins.parse` | **`agent_plugins.is_agent_plugins_manifest`** (Task 2) |

      The last row is the only guarded entry, and the guard is Task 2's function —
      without it a bare `plugin.json` pattern matches every unrelated plugin
      manifest in a tree. Note **no bare `mcp.json`/`.mcp.json`**, per the
      invariant below.
- [x] **Step 2: `ManifestPattern` NamedTuple** with `guard: Callable[[Path], bool] | None`,
      evaluated **before** `n_found` increments, so a bare `plugin.json` pattern does not
      inflate the unit count with every unrelated file. Widen the alias in
      `agent_kinds/__init__.py`; it must stay hashable (`scan.py` caches on it).
- [x] **Step 3: Replace `_registry_pattern_matches`** (`:60`) with compiled
      `pathspec.GitWildMatchPattern`. Re-anchor `.claude-plugin/plugin.json` and
      `.claude/settings.json` as `**/…` — git anchors slashed patterns at the root and
      the hand-rolled code did not.
- [x] **Step 4: `break` on first match** in `parse_repo_grouped` (`:134`), making
      one-file-one-route a property of the walker rather than registry hygiene.

**Invariant to test with the reason in the assertion message:** Cursor's registry must
never contain a bare `mcp.json` or `.mcp.json`. Its direct MCP surface is the
path-scoped `.cursor/mcp.json`; bundle roots are reached only through the plugin route.

**Verification:** a differential test over a fixture path corpus — including
`.claude/skills/a/b/SKILL.md`, `x/.claude-plugin/plugin.json`, `.claude/settings.local.json`,
`a/.claude/commands/x/y.md`, a root-level match with no leading directory, a
directory-only pattern against a same-named file, and a Windows-style
backslash path normalized before matching — asserting old matcher == new
matcher for every pattern, plus one case exercising the new one-file-one-route
`break` where the old code matched more than one pattern per file. Delete the
old function in the same change once green.

---

## Task 4: Precedence resolvers — subagents and commands

**Files:** create `tools/cursor_subagents.py`, `tools/cursor_commands.py`,
`tests/test_cursor_subagents.py`, `tests/test_cursor_commands.py`

Subagents and commands are both keyed by **relative path** under their root, never
frontmatter `name` (which for subagents defaults to the filename anyway) — so
`a/deploy.md` and `b/deploy.md` never collide. Given that, the two surfaces resolve
real collisions in **opposite directions**, so they get two resolvers, not one shared
one: unifying "the precedence walk" gets one of them wrong.

### Subagents — `tools/cursor_subagents.py`

- [x] **Step 1: Per-scope resolution** — every `.cursor/agents/<rel>`; each
      `.claude/agents/<rel>` only when no `.cursor` sibling exists at the same relative
      path (first-wins; `.cursor` over `.claude`).
- [x] **Step 2: Two entry points** — repo mode walks `**/{.cursor,.claude}/agents`
      grouped by `agents_dir.parent.parent`; endpoint mode takes **explicitly named**
      dirs, because endpoint roots are arbitrary paths and must never be reconstructed
      from a basename.
- [x] **Step 3: Containment and isolation** — `resolve_within` on the `agents` dir
      itself, per-file `resolve()` on nested `.md`, and per-file parse isolation so one
      corrupt file costs one subagent.

**Not `.codex/agents`** — neither Cursor program reads it, despite the docs.

**Verification:** override wins; no-override included; symlinked `agents` dir escaping
root dropped; nested `.md` symlink escape dropped; malformed file isolated.

### Commands — `tools/cursor_commands.py`

Commands resolve **last-wins** over team → global → plugin → workspace → personal,
with **user** scope the eventual winner — the inverse of the subagent rule above.

- [x] **Step 1: Ordered-scope resolution** — resolve every scope's
      `.cursor/commands/<rel>` and `.claude/commands/<rel>` in the documented tier order
      and keep the last entry seen per relative path, so a personal-scope file overrides
      a same-path workspace or plugin file rather than coexisting with it.
- [x] **Step 2: Two entry points**, mirroring the subagent resolver — repo mode walks
      both command roots per scope directory grouped by their parent; endpoint mode takes
      explicitly named per-scope dirs.
- [x] **Step 3: Containment and isolation**, mirroring the subagent resolver —
      `resolve_within` on the `commands` dir, per-file `resolve()`, per-file parse
      isolation.

**Verification:** last-wins across two same-relative-path scopes; distinct nested
relative paths from different scopes coexist; symlink escape and malformed-file
isolation cases mirroring the subagent resolver's.

---

## Task 5: Cursor declared composition

**Files:** modify `tools/repo_surface.py`; create `tools/graph_build_cursor.py`,
`tests/test_graph_build_cursor.py`

- [x] **Step 1: `CURSOR_SURFACE`** — skill dirs `.cursor`, `.agents`, `.claude`,
      `.codex`; command/agent surfaces `.cursor` + `.claude`; `scoped_mcp_rels`
      `.cursor/mcp.json`; `settings_rel=None`; `excluded_skill_dirs=("skills-cursor",)`;
      both plugin formats.
- [x] **Step 2: `build_cursor_graph(agent, ...)`** — root `Node(key=agent.bom_ref)`,
      normalizer, dispatch on `agent.source`, `finalize_graph`.
- [x] **Step 3: Declared branch** — plugin roots via the ordered candidate list; Agent
      Plugins roots excluded **strictly below** a realized native root; skills across the
      four roots recursively; `.cursor/mcp.json`; commands via the Task 4 command
      resolver over `.cursor/commands/**` and `.claude/commands/**` (`.md`/`.txt`);
      subagents via the Task 4 subagent resolver; `_add_dep_manifest_packages` at the
      scan root.

**Single-parent hazard:** a directory carrying both `.cursor-plugin/plugin.json` and a
root `plugin.json` must realize **once**. Realizing both gives the shared `skills/` and
root `mcp.json` two parents and aborts the scan on `Graph.validate()`.

**Verification:** both formats in one directory realize once; strict-nesting exclusion
keeps `examples/demo/plugin.json` from realizing; skill roots discovered at depth;
`skills-cursor` never inventoried; commands accept `.txt` but not `.mdc`; a directory
carrying a schema-recognized but `validate_manifest`-failing root `plugin.json` **and**
a valid `.claude-plugin/plugin.json` realizes the latter, not zero plugins.

---

## Task 6: Cursor installed composition

**Files:** modify `tools/graph_build_cursor.py`

- [x] **Step 1: Direct surfaces** — skills across the four user roots
      **excluding `skills-cursor`**; commands from `<root>/.cursor/commands` and
      `<root>/.claude/commands` via the Task 4 command resolver; subagents via the
      Task 4 subagent resolver run once per scope.
- [x] **Step 2: MCP merge** — `<root>/mcp.json` (user) and `<project>/.cursor/mcp.json`
      (project), merged **by server name with project winning**, not two path-keyed
      occurrences that happen to coexist. The merge produces one effective server map;
      each surviving entry's node carries the path of the file it actually won from, so
      posture attribution (Task 7) still points at a real file.
- [x] **Step 3: Plugins** — `plugins/local/<name>/` and
      `plugins/cache/<marketplace>/<name>/<sha>/`. Cached bundles are **gated on
      `.cache-complete`**: a zero-byte sentinel written last, and Cursor's only
      cache-reuse check, so a directory holding content without it is one Cursor
      reinstalls rather than loads. Skip those entirely — do not synthesize a
      presence-only ref for them. *(Resolved from a spec/ADR conflict: the spec's
      deferred table wrongly listed this as skipped; ADR-0052 is authoritative and the
      spec has been corrected.)* Manifest-less bundles that **are** complete get a
      synthesized presence-only ref (`extra["manifest"] = "absent"`). Marketplace segment
      goes in `extra["cursor_marketplace_dir"]` — **never** `extra["marketplace"]`, which
      is the cross-BOM identity qualifier.
- [x] **Step 4: Home-scoped roots stay home-scoped.** `~/.agents/skills` and the
      `.claude`/`.codex` compat roots are cross-tool conventions, not Cursor state, so
      `--config-dir` must not relocate them.

**Test-isolation hazard:** a fixture-rooted Cursor endpoint test otherwise reads the
developer's real `~/.claude`. Every endpoint test monkeypatches `Path.home` or the
compat-root resolver.

**Verification:** dev-linked and cached bundles found; a cached bundle **missing
`.cache-complete` is not inventoried** while a complete sibling is; manifest-less bundle synthesizes
one ref with its skills and commands as children; **no plugin carries `enabled`**;
a `~/.claude/agents/x.md` node keys as `claude-code/agents/x.md#…`, not an absolute path;
a same-named server in both `mcp.json` files keeps the project entry only, a
uniquely-named entry in either file survives, and a malformed one-sided file does not
drop the other file's entries.

---

## Task 7: Posture

**Files:** modify `tools/posture/__init__.py`, `tools/posture/rules/mcp_auto_approve.py`,
`tools/scan.py`, `tools/remote/collector.py`

- [x] **Step 1: Promote `_no_manifests`** to `tools.posture.no_manifests` and delete both
      private copies (`tools/scan.py:680`, `tools/remote/collector.py:118`) in favour of
      the shared import — scan, the collector, and Cursor all need it, and importing a
      scan-CLI private is the wrong contract, and leaving the collector's copy in place
      is the same wrong contract by omission.
- [x] **Step 2: Cursor collectors.** Declared: a path-and-content walk over `**/.cursor/mcp.json`
      plus every plugin root, resolved through the same ordered manifest-candidate list Task 5's
      declared composition uses — `.cursor-plugin/plugin.json` → `.claude-plugin/plugin.json` →
      schema-detected root `plugin.json`, first candidate that parses, passes the Task 2
      `validate_manifest` boundary where it applies (the Agent Plugins candidate only —
      the other two formats have no equivalent standard to validate against), and names
      the plugin wins. A schema-recognized root `plugin.json` that fails validation does
      not win; resolution continues to the next candidate in order, exactly as if that
      file were absent. This is not a shorter, separately hand-rolled list. A plugin
      bundled only as `.claude-plugin/plugin.json` is still a manifest Cursor reads, so
      its bundled MCP servers must reach posture the same way they reach composition.
      Honouring `include_gitignored` exactly as `collect_mcp_manifests` (`:180`) does.
      Installed: derived **from the refs the graph already produced**, never a directory
      walk.
- [x] **Step 3: Resolve `permissions.json` before it reaches the rule.** Read the user
      file (relocatable via `CURSOR_CONFIG_DIR`/`XDG_CONFIG_HOME`, at most one remote
      plus one local) and the project-root-to-folder chain, and produce one effective
      view: the user and project `permissions.json` files **concatenate field by field**
      — both contribute, neither replaces the other. Parse as **JSONC**, not JSON;
      comments and trailing commas are documented as supported and a plain loader drops
      valid files.

- [x] **Step 4: `mcp_auto_approve` branches on manifest shape.** Claude Code's
      per-server `autoApprove` and Cursor's `mcpAllowlist`/`autoRun` are the same posture
      in different files.

**The merge concatenates.** Both `permissions.json` files contribute field by field;
neither replaces the other. Cursor's reference: *"When both exist, Cursor concatenates
the arrays inside every field. Per-user and per-repo entries combine; one does not
replace the other."* Treating either file as authoritative drops the other's entries —
and for a security scanner that means missing an auto-approved server.

A prior draft of this plan and the spec said user scope was first-wins per field. That
was over-generalized from one sampled code path; the bundle holds more than one
permissions mechanism, so the documented contract governs. Corrected in both documents.

**Parse as JSONC**, not JSON. Comments and trailing commas are documented as supported,
and a plain `json.loads` silently drops a valid file.

**Scope: `permissions.json` only.** The CLI's `cli-config.json` carries its own
`permissions.allow`/`deny` token list with a **different schema**, and
`<project>/.cursor/cli.json` carries project-scope permissions for the CLI alone. Those
are a separate approval system, not another location for this one, and merging them
would invent entries in both directions. This task implements the shared
`permissions.json` model that both programs read; the CLI-private system is out of scope
for the first pass and recorded as such in the spec. Do not let the shared collector
read either CLI file.

**Allowlist:** `insecure_transport`, `mutable_install`, `skill_capability`,
`mcp_auto_approve`. Not `api_endpoint_override` — it matches literal Anthropic settings
keys in a file Cursor does not have.

**Verification:** `CURSOR_CONFIG_DIR` wins over `XDG_CONFIG_HOME` when both are set;
`XDG_CONFIG_HOME` resolves to `<xdg>/cursor` when `CURSOR_CONFIG_DIR` is unset; falls
back to `~/.cursor` when neither is set; **an `mcpAllowlist` entry present in only the
user file and another present in only the project file both survive into the effective
view**, and neither file's entries are dropped when both declare the field; a
`permissions.json` containing comments and a trailing comma parses rather than being
skipped as malformed; a repo plugin represented only by `.claude-plugin/plugin.json` with
an insecure bundled MCP URL surfaces `insecure_transport` through the declared
collector; a directory carrying both a native and a Claude-format manifest reports
posture for the first-winning candidate only, not shadowed content from both.

---

## Task 8: The Cursor kind module

**Files:** create `tools/agent_kinds/cursor.py`; modify `tools/agent_kinds/__init__.py`,
`tests/test_agent_kinds.py`

- [x] **Step 1: `KIND`** — `id="cursor"`, singleton, `root_label="cursor"`,
      `COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}`.
- [x] **Step 2: `resolve_config_root`** — `--config-dir`, else `<home>/.cursor`. **No env
      var**; `CURSOR_CONFIG_DIR` scopes only `permissions.json` and the CLI's own config.
- [x] **Step 3: Declared evidence** — Cursor-**owned** surfaces only: `.cursor/mcp.json`,
      `.cursor/skills/*/SKILL.md`, `.agents/skills/*/SKILL.md`, `.cursor/commands/*`,
      `.cursor/agents/*`, `.cursor-plugin/plugin.json`, schema-detected root `plugin.json`.
      **Not** `.claude/*` (a phantom Cursor BOM for every Claude-only repo) and **not** a
      bare `mcp.json`. Promote `claude_code._matches_evidence` to a shared helper.

Registration in `_registry()` is Task 9's last step, not this task's — see the ordering
note there. `KIND` is fully built and unit-tested here without being live yet.

**Verification:** `cursor.KIND` in isolation — discovery on a `.claude`-only fixture
yields no Cursor agent; discovery on a `.cursor/mcp.json` fixture yields one Cursor
agent; the posture allowlist validates against `KNOWN_RULE_IDS` at import.

---

## Task 9: `--kind`, endpoint propagation, the counting regressions, and registration

**Files:** modify `tools/scan.py`, `tools/bom_cli.py`, `tools/agent_kinds/__init__.py`,
`tools/remote/cli.py`, `tools/remote/collector.py`; modify
`tests/test_agent_kinds.py`, remote CLI/collector tests

With two installed kinds, `--config-dir` alone is ambiguous — both would claim the same
directory. Each kind already knows its own root, so `--kind` is the primary knob.
Cursor is registered as this task's **last** step, once `kind_id` filtering and
`--kind` validation exist on every endpoint command — registering it any earlier would
change ordinary `scan endpoint`/`bom endpoint`/`remote sync endpoint` invocations (no
`--kind` given) before there is a way to select or validate a kind.

- [x] **Step 1: `DiscoveryContext.kind_id`**; `discover_agents` skips non-matching kinds.
- [x] **Amended after ADR-0054 (root override is a per-kind capability).** A
      root override is granted only to a kind for which naming a root fully
      specifies the target. `AgentKind.root_override_refusal` carries the reason a
      kind refuses; `cli_kind` surfaces it verbatim. Cursor refuses — its
      composition draws on three home-derived groups and a flag moves one, so the
      result is stitched from two homes and indistinguishable in the output from a
      correct scan. Cursor's `resolve_config_root` therefore always returns
      `<home>/.cursor`, and tests needing a hermetic root fake home instead.
      Foreign-tree Cursor scanning is explicitly unserved; the coherent
      "treat this directory as home" override is deferred to its own design.
- [x] **Step 2: `--kind` on `scan endpoint`, `bom endpoint`, `remote sync endpoint`.**
      `--config-dir` **requires** `--kind` — a hard error, never silent arbitration.
      Update its help text, which still says `$CLAUDE_CONFIG_DIR`. Default (no `--kind`,
      no `--config-dir`) discovers every installed kind whose own default root exists;
      an explicit `--kind` whose default root does not exist reports zero agents for that
      kind rather than falling back to another kind's root; an explicit `--config-dir`
      is scoped to the selected `--kind` only — it is never treated as available to a
      compatibility root the same kind reads under a different variable (e.g. Cursor's
      `--config-dir` never relocates `~/.agents/skills` or the `.claude`/`.codex` compat
      roots — see Task 6 Step 4).
- [x] **Step 3: Propagate `--kind` into `remote sync endpoint`'s discovery.**
      `tools/remote/cli.py`'s `endpoint` command resolves `--config-dir` through
      `_resolve_endpoint_config_dir` (`:206`), which is Claude-specific
      (`CLAUDE_CONFIG_DIR`, else `~/.claude`), and passes no kind selection into
      `build_endpoint_collections`. Add `--kind` to `sync endpoint` with the same
      `--config-dir`-requires-`--kind` rule as Step 2; when `--kind` is absent, resolve
      every installed kind's own default root instead of only Claude's. Thread `kind_id`
      through `tools/remote/collector.py`'s `build_endpoint_collections` into
      `DiscoveryContext`, replacing the eager Claude-only root resolution so remote
      collection reaches the same per-kind selection endpoint scan already has.
- [x] **Step 4: Fix the redaction root contract for multi-kind default discovery.**
      `_prepare_upload_payload` (`tools/remote/collector.py:296-335`),
      `_redact_payload_for_remote` (`:734-838`), and `_relativize_path_for_remote`
      (`:421-468`) all redact every collection's absolute paths against one outer
      `config_dir` — the same value reused for every agent in `collect_endpoint`'s loop
      (`:250-259`) and every payload in `build_endpoint_dry_run_payloads` (`:338-359`).
      Once Step 3 makes default discovery return both a Claude Code and a Cursor
      `EndpointCollection` from one invocation, that single `config_dir` can be at most
      one kind's root — the other kind's paths miss both the `config_dir` and `project`
      branches in `_relativize_path_for_remote` and silently fall back to a bare
      basename, losing provenance for one kind on every default-discovery upload. Redact
      each collection against **its own** `collection.agent.config_root` — `AgentInstance`
      already carries this per agent (`tools/agent_kinds/__init__.py:60`) — never the
      CLI's outer `config_dir`. `build_endpoint_collections`'s `config_dir: Path`
      parameter becomes `config_dir: Path | None`, paired with `kind_id: str | None`,
      matching Step 2's discovery contract; `collect_endpoint` and
      `build_endpoint_dry_run_payloads` pass each `collection.agent.config_root` into
      that collection's own `_prepare_upload_payload` call instead of the outer
      `config_dir`. An explicit `--kind --config-dir` invocation is unaffected — there is
      exactly one collection and its `config_root` already equals the outer
      `config_dir`. Cover dry-run and upload-path output for both an explicit `--kind`
      and default two-kind discovery.
- [x] **Step 5: Fix double-counted shared manifests.** `scan.py:843-850` keys scan-wide
      totals on `(scan_root, kind.manifest_patterns)`, so two kinds count one
      `package.json` twice. Per-agent coverage keeps its own subset; scan-wide totals come
      from one walk per root over the union. `bom_cli.py:363` has the same bug.
- [x] **Step 6: Decide duplicate findings on shared components.** A
      `.claude/agents/reviewer.md` is reachable from two agents, so
      `scan.py:851-861` yields the same finding twice. Today no file is reachable from two
      agents. Argued correct — two agents genuinely both expose it — but it needs a test,
      not a discovery in review.
- [x] **Step 7: Register Cursor** in `_registry()` (`tools/agent_kinds/__init__.py:146`),
      now that Steps 1–4 make it selectable and correctly redacted and Steps 5–6 make its
      counts and findings correct once two kinds coexist.

**Verification (in addition to each step's own):** unknown `--kind` value is a hard
error on all three commands; `--config-dir` without `--kind` is a hard error on all
three commands; `--kind` without `--config-dir` resolves that kind's default root;
default discovery with no flags at all finds both Claude Code and Cursor when both
config roots exist; `remote sync endpoint --dry-run` and a real upload both reflect
`--kind` selection identically to `scan endpoint`; with both `~/.claude` and
`~/.cursor` present and no `--kind` given, each collection's dry-run and upload
payload preserves a root-relative path under its own agent's root — neither kind's
paths leak the other's root, and neither degrades to the basename fallback that only
applies to genuinely out-of-root paths. Existing tests invoking
`--config-dir` without a kind on any of the three commands are updated to pass
`--kind claude-code` alongside it — the plan's compatibility surface for those callers
is that `--config-dir` alone stops working once a kind is ambiguous, not that it keeps
silently defaulting to Claude Code.

---

## Task 10: Documentation

**Files:** `docs/reference/cli.md`, `docs/reference/coverage.md`, `CLAUDE.md`,
`docs/plans/README.md`

- [x] **Step 1:** `--kind` on the three endpoint commands; corrected `--config-dir` help.
- [x] **Step 2:** Cursor's `partial` row in the coverage reference.
- [x] **Step 3:** Amend the parser-set line in `CLAUDE.md` — V0 scope said Cursor
      manifests were V1.
- [x] **Step 4:** Add 042 to the plan index.

---

## Testing

Module tests carry the detail. Per the e2e boundary in `CLAUDE.md`, `tests/test_e2e.py`
gets exactly **two** additions — the one-screen tests that fail if any of discovery,
composition, registry, or emission regresses.

- [x] **Declared, two kinds, one repo.** Fixture with `.claude/skills/…`,
      `.cursor/mcp.json`, and a shared `.claude/agents/reviewer.md`, with every manifest
      well-formed so neither kind's walk hits a parse failure. `openaca bom repo` emits
      two documents; assert both `openaca:agent_kind` values, and `complete` for Claude
      Code versus `partial` for Cursor — not because the fixture is harder to parse (it
      isn't; parse failures are zero for both), but because Claude Code's declared
      `COVERAGE_BASELINE` is `complete` (Task 8's registration is unaffected — this
      exercises the existing Claude Code kind) while Cursor's is `partial` per Task 8
      Step 1, and `resolve_coverage` floors at the baseline regardless of observed
      evidence gaps. Also assert that `reviewer.md`'s `bom-ref` is **identical in both**
      — the product promise of the whole cross-read design.
- [x] **Installed, two kinds, one machine.** Fixture `HOME` with `~/.claude` and
      `~/.cursor` (one cached plugin, one MCP, both well-formed). `openaca scan endpoint`
      renders two agent cards; assert the Cursor plugin carries **no** `enabled`
      property, and that a `~/.claude/agents/x.md` node keys as
      `claude-code/agents/x.md#…`. Parametrize this same test (still one e2e addition,
      not a third) over a second fixture state that adds one malformed Cursor-only file
      (e.g. an unparseable `.cursor-plugin/plugin.json`): asserts Claude Code's coverage
      is unaffected while Cursor's drops, proving the two kinds' per-agent coverage does
      not share a failure count even though Task 9 Step 5 makes them share scan-wide
      totals.

Do not port bulk fixtures from unmerged work; most of it tested a host-selection
mechanism that no longer exists.
