# Plan 040 — Multi-agent support

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A BOM describes one *agent* rather than one place, and a scan emits as many
BOMs as agents it discovers — one, today, because Claude Code is the only kind.

**Architecture:** A kind registry (`tools/agent_kinds/`) declares each runtime's
discovery, composition, cardinality, coverage baseline, display label, and posture-rule
allowlist. Discovery returns a **list** of `AgentInstance`; each instance gets its own
graph rooted at itself (`root/claude-code`), and each graph emits one BOM whose
`metadata.component` is the agent. `build_graph(target, mode=...)` gains no third mode —
a parameterised `build_rooted_graph` underneath it serves both the legacy path and the
agent path, so `tools/remote/collector.py` keeps today's behaviour byte-for-byte.

**Tech Stack:** Python 3.11, click, jsonschema, pytest, uv. No new dependencies.

**Spec:** `docs/specs/multi-agent-support.md`. Read it before starting.
ADRs: `docs/adrs/0044-agent-bom-root.md` (the agent is the root),
`0045-agent-identity-keying.md` (`agent_kind`; the (asset, kind, agent id) key; the
`root/` prefix), `0046-agent-coverage.md` (`composition_coverage`).

## Context

Every scan today roots at a *place*. `openaca scan endpoint` resolves one config
directory and `openaca scan repo` walks one tree, each emitting exactly one BOM whose
subject is that place. Both paths hard-code a single runtime: `_seed_endpoint`
(`tools/graph_build.py:306`) is Claude Code by construction, and `REGISTRY`
(`tools/parsers/__init__.py:34`) is a flat pattern list with no runtime tagging.

Two consequences. "Which agent is affected by this vulnerable MCP server?" is
unanswerable — the document can only say *this place has it*. And a second runtime
cannot be added without re-rooting the data model rather than extending it, because the
property that cannot be retrofitted is discovery returning a *list*.

This plan implements the mechanism and migrates the one kind that exists. It adds no
second runtime, but every seam a second runtime needs is built and exercised: discovery
returns a list, emission is per agent, coverage resolves per (kind, source), node-key
labels name the root's owning kind, a kind declares its own manifest patterns and
posture-manifest collectors rather than a Claude-Code-shaped pair being called for
every kind, and `agent_id` discrimination plus multi-document output are proven by a
synthetic test kind. For a Claude-Code-only machine the observable diff is small — same
commands, still one document — but document count becomes a consequence of discovery
rather than an assumption.

## Global Constraints

Copied from the spec; every task's requirements implicitly include these.

- **Schema first.** `schema/openaca-bom.schema.json` currently *requires*
  `openaca:target_type` via an `allOf`/`contains` clause, and `bom_lint` validates
  metadata against it. Until Task 2 lands, every agent BOM fails lint.
- **Removing a property means stopping the write, not the read.**
  `component_refs_from_cyclonedx` (`tools/bom.py:547`) restores `agent_host` and
  `runtime_hosts` from stored `0.4` BOMs, and `target_info_from_cyclonedx`
  (`tools/bom.py:362`) still reads `target_type`. All three readers stay.
- **The remote collector is not migrated.** It keeps `build_graph(mode="endpoint")`,
  `endpoint/` node-key labels, `openaca:target` as the root ref, and — as the last
  emitter — `openaca:target_type: endpoint`, while carrying `schema_version: 0.5`.
- **Place identity never enters a BOM.** Only the categorical
  `openaca:composition_source`. Hostname stays in the registration envelope.
- **`openaca:composition_source` is required and explicit**, one of `installed` or
  `declared`, never absent — a dropped field would turn potential exposure into actual.
- **A singleton kind omits `openaca:agent_id`** rather than emitting it empty.
- **`build_agent_bom` keeps keyword-only arguments after `refs`** so `openaca.core`
  consumers do not break (ADR-0028). New arguments are additive with defaults.
- **Findings stay one flat list.** One SARIF path, one exit code, one `sarif-path`
  output — `action.yml` is untouched. A component loaded by two agents is two
  occurrences and therefore two findings, one per agent.
- **Coverage is `min(baseline, evidence)`.** A kind declares the baseline per
  composition source; an individual scan can only be worse, never better.
- **Every `bom-ref` in the installed path changes once** (`endpoint/<rel>` →
  `claude-code/<rel>`). Expected, documented in release notes, and given no
  version-aware diff shim. Repo-path component `bom-ref`s do **not** change.
- **`metadata.component.name` is the human label**, never an identifier — `Claude Code`
  for the singleton kind. `bom-ref` and `agent_id` are the identifiers.
- Default to writing no comments; add one only where the *why* is non-obvious.
- **Every task ends green on**
  `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

## File Structure

| File | Responsibility |
|---|---|
| `docs/adrs/0047-per-agent-scan-output.md` (new) | Records the output-shape decision: one text card per agent, one machine document per scan |
| `schema/openaca-bom.schema.json` | `0.5`; `target_type` optional-but-validated; conditional rules for the four agent properties |
| `tools/bom_lint.py` | `check_agent_metadata` gated on the `root/` prefix; `openaca:*` property uniqueness; registry-backed `agent_id` self-check |
| `tools/agent_kinds/__init__.py` (new) | Registry types, `REGISTRY`, `discover_agents`, `build_agent_graph`, `resolve_coverage`, `slugify_agent_id`, `output_basenames` |
| `tools/agent_kinds/claude_code.py` (new) | Claude Code's installed + declared discovery, compose, coverage baselines, display label, posture allowlist |
| `tools/active_in.py` (new) | The single definition of "which agents is this component active in" |
| `tools/graph_build.py` | `build_rooted_graph`; root-label parameter on `_make_normalizer` |
| `tools/bom.py` | `AgentBOM` agent fields; agent `metadata.component`; drop two property writes; `agent_info_from_cyclonedx`; `AGENT_ROOT_PREFIX` |
| `tools/bom_cli.py` | Per-agent emission: NDJSON stdout, `--output-dir`, `--output` deprecation |
| `tools/scan.py` | Per-agent scan pipeline for `endpoint`/`repo`/`bom`; NDJSON BOM input; graph-backed prefix |
| `tools/render.py` | One card per agent; `agents[]` in the JSON document |
| `tools/matcher.py`, `tools/finding_output.py`, `tools/sarif.py` | Agent association on findings, carried into JSON and SARIF |
| `tools/posture/__init__.py`, `tools/posture/rules/{mutable_install,skill_capability}.py`, `tools/observations/skillspector.py` | Per-kind rule allowlist; `active_in` re-sourced |
| `tools/parsers/mcp_json.py`, `tools/parsers/claude_install.py` | Stop writing `runtime_hosts` — the agent owns the runtime, not the parser |
| `tests/test_agent_kinds.py` (new) | Registry, discovery, coverage, slug, basename collisions |
| `tests/test_graph_build_agent.py` (new) | Per-agent graph parity and labels |
| `tests/test_bom_cli_agents.py` (new) | Multi-document emission via the synthetic kind |
| `tests/fixtures/agent_kinds.py` (new) | The synthetic test kind |
| `tests/test_e2e.py` | Cross-layer characterisation of the migrated shape |
| `docs/openaca-bom-schema.md`, `docs/reference/cli.md`, `docs/concepts/identities.md`, `docs/concepts/scan-modes.md` | Format reference, CLI surface, keying, what a repo scan now produces |

---

## Task 1: ADR-0047 — per-agent scan output

**Files:**
- Create: `docs/adrs/0047-per-agent-scan-output.md`
- Modify: `docs/adrs/INDEX.md`

The spec says the renderer's card becomes per-agent, and separately that scan's JSON
output is unchanged. With N agents those pull apart, and the resolution is a decision
whose rejected alternative (NDJSON for scan output, mirroring the BOM contract) is
plausible and will be re-suggested. Record it before the code depends on it.

- [ ] **Step 1: Write the ADR**

```markdown
---
id: 0047
title: One text card per agent, one machine document per scan
status: accepted
date: 2026-08-23
supersedes: null
superseded-by: null
---

## Context

ADR-0044 makes a scan emit one BOM per agent, and `docs/specs/multi-agent-support.md`
settles the BOM sink: NDJSON on stdout, one CycloneDX document per line. It also says
the renderer's `host_surface` "becomes per-agent" with "one card per agent", and — in
the same document's backward-compatibility table — that `target.host_surface` in scan
JSON output is unchanged.

Those are consistent for one agent and divergent for many, because a BOM and a findings
report are different kinds of document. A BOM's subject *is* one agent, so one document
per agent is forced. A findings report's subject is a scan: the exit code aggregates
severity across every agent, `to_sarif` writes one file, and the reference Action
contracts on one SARIF path and one exit code.

## Decision

**Text output prints one card per agent.** Each card carries that agent's Target block,
inventory tree, and next actions. With one kind registered this is one card,
structurally unchanged from today — the migration's only permitted diff is inside the
Target block itself (it gains a `coverage` row and adopts the agent's display name in
place of the hardcoded host label), and every other section is unaffected.

**Machine output stays one payload per scan.** `--format json` emits exactly one
document: the findings list stays flat, each finding carrying the agent it belongs
to, and the document gains an `agents[]` array — one entry per discovered agent with
its kind, composition source, coverage, and display label — so an agent with zero
components still appears. `target` is retained and, for a single-agent scan, is
unchanged. An exposure report (`--report exposure`) is built from the same document
and carries `agents[]` for the same reason.

`--format github` is an annotation stream rather than a document, so it gains no
`agents[]` block; the agent travels on each annotated finding. What it does gain is
per-agent attribution — `attribution_for_ref` must resolve against the graph of the
agent the finding belongs to, not a single scan-wide graph.

`stats` stays scan-wide.

## Alternatives considered

- **NDJSON for scan output too**, one report per agent, symmetric with the BOM sink —
  rejected. It splits the flat findings list the spec keeps deliberately, it leaves
  scan-wide totals with nowhere to live, and it breaks `json.load` on the multi-agent
  case for a symmetry between two documents that are not the same kind of thing. The
  BOM sink changed because a single file path genuinely cannot hold N documents; a
  findings report has no such forcing constraint.
- **The agent on each finding and nothing else** — rejected. An agent with no findings
  would then appear nowhere in machine output, losing the installed-but-unconfigured
  agent that ADR-0044's situation 18 exists to represent.
- **One card for all agents, with a per-agent Target block inside it** — rejected as a
  half-measure: the inventory tree and next actions are per-agent too, so the card
  would interleave two agents' components under one heading.

## Consequences

Machine consumers keep a single parseable document and gain a forward-compatible
`agents[]` key; `stats` remains meaningful. Text output grows linearly with agent count,
which is correct for a human surface and is one card while one kind ships.

Cost: two output shapes to reason about — per-agent for BOMs, per-scan for reports.
That asymmetry is inherent to the two document types rather than introduced here.

## When to revisit

If findings ever stop being a flat list — if the exit code or SARIF output becomes
per-agent — the report's subject has become the agent too, and this decision inverts.
```

- [ ] **Step 2: Add the INDEX entry**

Insert into `docs/adrs/INDEX.md`'s Active list, after the ADR-0046 row:

```markdown
- [ADR-0047 — One text card per agent, one machine document per scan](0047-per-agent-scan-output.md): text prints one card per agent while `--format json`/`github` stay a single document with a flat findings list plus an `agents[]` array; rejected NDJSON scan output (splits the flat findings list, orphans scan-wide totals, breaks `json.load`) and agent-on-findings-only (hides a zero-component agent).
```

- [ ] **Step 3: Commit**

```bash
git add docs/adrs/0047-per-agent-scan-output.md docs/adrs/INDEX.md
git commit -m "docs(adr): record per-agent scan output shape"
```

---

## Task 2: Schema and linter accept an agent-rooted BOM

**Files:**
- Modify: `schema/openaca-bom.schema.json`
- Modify: `tools/bom_lint.py`
- Test: `tests/test_bom_lint.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `tools.bom.AGENT_ROOT_PREFIX = "root/"` — added here as a one-line constant
  so this task and Task 3 share one definition (`tools/bom.py` imports nothing from
  `bom_lint` or `agent_kinds`, so there is no cycle);
  `tools.bom_lint.check_agent_metadata(doc: dict[str, Any]) -> list[str]`.

The metadata `allOf` has two `contains` clauses. The `schema_version` one stays (the
property is genuinely required); the `target_type` one must go, because Task 5 stops
writing it. Replace it with per-item conditionals that validate a property *when
present*, which is also how the four new properties are constrained.

- [ ] **Step 1: Write the failing schema tests**

```python
# tests/test_bom_lint.py

def _agent_doc(**overrides):
    """A minimal 0.5 agent-rooted document."""
    props = {
        "openaca:agent_kind": "claude-code",
        "openaca:composition_source": "installed",
        "openaca:composition_coverage": "complete",
    }
    props.update(overrides.pop("metadata_component_props", {}))
    for key in overrides.pop("drop", ()):
        props.pop(key, None)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "tools": [{"vendor": "OpenACA", "name": "openaca"}],
            "properties": [{"name": "openaca:schema_version", "value": "0.5"}],
            "component": {
                "type": "application",
                "bom-ref": overrides.pop("bom_ref", "root/claude-code"),
                "name": "Claude Code",
                "properties": [{"name": k, "value": v} for k, v in props.items()],
            },
        },
        "components": [],
        "dependencies": [{"ref": overrides.pop("dep_ref", "root/claude-code"),
                          "dependsOn": []}],
    }


def test_lint_accepts_agent_rooted_bom(tmp_path):
    path = tmp_path / "agent.cdx.json"
    path.write_text(json.dumps(_agent_doc()), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output


def test_lint_still_accepts_stored_0_4_target_type(tmp_path):
    doc = _agent_doc()
    doc["metadata"]["properties"] = [
        {"name": "openaca:schema_version", "value": "0.4"},
        {"name": "openaca:target_type", "value": "endpoint"},
    ]
    doc["metadata"]["component"] = {
        "type": "application", "bom-ref": "openaca:target", "name": "/home/u/.claude",
        "properties": [{"name": "openaca:component_type", "value": "target"}],
    }
    doc["dependencies"] = [{"ref": "openaca:target", "dependsOn": []}]
    path = tmp_path / "legacy.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output


def test_lint_rejects_bad_composition_source(tmp_path):
    doc = _agent_doc(metadata_component_props={"openaca:composition_source": "sandbox"})
    path = tmp_path / "bad.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "openaca:composition_source" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bom_lint.py -k agent_rooted -v`
Expected: FAIL — schema validation errors that `openaca:target_type` is missing.

- [ ] **Step 3: Rewrite the metadata property rules in the schema**

Replace the whole `metadata.properties` value in `schema/openaca-bom.schema.json` with:

```json
"properties": {
  "type": "array",
  "items": {
    "allOf": [
      {"$ref": "#/$defs/property"},
      {"$ref": "#/$defs/schemaVersionRule"},
      {"$ref": "#/$defs/targetTypeRule"}
    ]
  },
  "allOf": [
    {
      "contains": {
        "type": "object",
        "required": ["name", "value"],
        "properties": {"name": {"const": "openaca:schema_version"}}
      }
    }
  ]
}
```

and add to `$defs`:

```json
"schemaVersionRule": {
  "if": {"properties": {"name": {"const": "openaca:schema_version"}}, "required": ["name"]},
  "then": {"properties": {"value": {"enum": ["0.1", "0.2", "0.3", "0.4", "0.5"]}}}
},
"targetTypeRule": {
  "if": {"properties": {"name": {"const": "openaca:target_type"}}, "required": ["name"]},
  "then": {"properties": {"value": {"enum": ["repo", "endpoint", "bom"]}}}
},
"agentKindRule": {
  "if": {"properties": {"name": {"const": "openaca:agent_kind"}}, "required": ["name"]},
  "then": {"properties": {"value": {"type": "string", "minLength": 1}}}
},
"compositionSourceRule": {
  "if": {"properties": {"name": {"const": "openaca:composition_source"}}, "required": ["name"]},
  "then": {"properties": {"value": {"enum": ["installed", "declared"]}}}
},
"compositionCoverageRule": {
  "if": {"properties": {"name": {"const": "openaca:composition_coverage"}}, "required": ["name"]},
  "then": {"properties": {"value": {"enum": ["unknown", "partial", "complete"]}}}
}
```

The four agent properties live on `metadata.component`, so add a `properties` constraint
to the metadata component object as well — extend the `metadata` object's schema with:

```json
"component": {
  "type": "object",
  "properties": {
    "properties": {
      "type": "array",
      "items": {
        "allOf": [
          {"$ref": "#/$defs/property"},
          {"$ref": "#/$defs/agentKindRule"},
          {"$ref": "#/$defs/compositionSourceRule"},
          {"$ref": "#/$defs/compositionCoverageRule"}
        ]
      }
    }
  }
}
```

- [ ] **Step 4: Add the linter's agent-metadata gate**

Add the shared constant to `tools/bom.py` (nothing else in that file changes until
Task 5):

```python
# The `metadata.component` bom-ref prefix marking an agent-rooted document
# (ADR-0045). Not `agent/`: the closed component-type set already uses that for a
# subagent, and a `startswith` test on this prefix decides whether a stored BOM is
# graph-backed.
AGENT_ROOT_PREFIX = "root/"
```

Then in `tools/bom_lint.py`:

```python
from tools.bom import AGENT_ROOT_PREFIX

_COMPOSITION_SOURCES = {"installed", "declared"}


def check_agent_metadata(doc: dict[str, Any]) -> list[str]:
    """Invariants for an agent-rooted document.

    The gate is `metadata.component`'s bom-ref prefix (ADR-0045), not
    `openaca:target_type` — which agent BOMs no longer carry.
    """
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return []
    component = metadata.get("component")
    if not isinstance(component, dict):
        return []
    bom_ref = component.get("bom-ref")
    if not isinstance(bom_ref, str) or not bom_ref.startswith(AGENT_ROOT_PREFIX):
        return []

    errors: list[str] = []
    props = _properties_by_name(component)

    agent_kind = props.get("openaca:agent_kind")
    if not agent_kind:
        errors.append("metadata.component: openaca:agent_kind is required on an agent BOM")

    coverage = props.get("openaca:composition_coverage")
    if coverage not in COVERAGE_LEVELS:
        errors.append(
            "metadata.component: openaca:composition_coverage must be one of "
            f"{sorted(COVERAGE_LEVELS)}, got {coverage!r}"
        )

    source = props.get("openaca:composition_source")
    if source not in _COMPOSITION_SOURCES:
        errors.append(
            "metadata.component: openaca:composition_source must be one of "
            f"{sorted(_COMPOSITION_SOURCES)}, got {source!r}"
        )

    if agent_kind:
        agent_id = props.get("openaca:agent_id")
        expected = (
            f"{AGENT_ROOT_PREFIX}{agent_kind}"
            if agent_id is None
            else f"{AGENT_ROOT_PREFIX}{agent_kind}/{agent_id}"
        )
        if bom_ref != expected:
            errors.append(
                f"metadata.component: bom-ref {bom_ref!r} is inconsistent with "
                f"openaca:agent_kind/openaca:agent_id (expected {expected!r})"
            )
    return errors


def _check_duplicate_openaca_properties(component: dict[str, Any], label: str) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for prop in component.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        if not isinstance(name, str) or not name.startswith("openaca:"):
            continue
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return [f"{label}: {name!r} appears more than once" for name in sorted(duplicates)]
```

Wire both into `check_semantics`, after the existing duplicate-`bom-ref` block:

```python
    errors.extend(check_agent_metadata(doc))
    if isinstance(metadata, dict) and isinstance(metadata.get("component"), dict):
        errors.extend(
            _check_duplicate_openaca_properties(metadata["component"], "metadata.component")
        )
    for index, component in enumerate(components):
        errors.extend(_check_duplicate_openaca_properties(component, f"components[{index}]"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bom_lint.py tests/test_schema.py -v`
Expected: PASS, including the stored-`0.4` regression.

- [ ] **Step 6: Add the duplicate-property test and re-run**

```python
def test_lint_rejects_duplicate_openaca_property(tmp_path):
    doc = _agent_doc()
    doc["components"] = [{
        "type": "application", "bom-ref": "claude-code/x#y#skill/x", "name": "x",
        "properties": [
            {"name": "openaca:identity", "value": "skill/x"},
            {"name": "openaca:identity", "value": "skill/x"},
        ],
    }]
    doc["dependencies"].append({"ref": "claude-code/x#y#skill/x", "dependsOn": []})
    path = tmp_path / "dup.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "appears more than once" in result.output
```

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

- [ ] **Step 7: Commit**

```bash
git add schema/openaca-bom.schema.json tools/bom.py tools/bom_lint.py tests/test_bom_lint.py
git commit -m "feat(schema): accept an agent-rooted BOM at schema version 0.5"
```

---

## Task 3: The kind registry, with Claude Code registered

**Files:**
- Create: `tools/agent_kinds/__init__.py`
- Create: `tools/agent_kinds/claude_code.py`
- Modify: `tools/bom_lint.py`
- Test: `tests/test_agent_kinds.py`, `tests/test_bom_lint.py`

**Interfaces:**
- Consumes: `tools.capability.COVERAGE_LEVELS` (`("unknown", "partial", "complete")`,
  already ordered worst-to-best), `tools.graph.Graph`, `tools.parsers.REGISTRY`'s
  `(pattern, parser)` shape (`manifest_patterns` reuses it verbatim so a kind can hand
  its patterns through without translation; Claude Code is the only kind that
  populates it, in Step 4; Task 7 makes `tools.parsers.parse_repo_grouped` accept it).
- Produces:
  - `AgentInstance` (frozen dataclass) with fields `kind_id: str`,
    `display_name: str`, `source: CompositionSource`, `root_label: str`,
    `coverage_baseline: str`, `config_root: Path | None = None`,
    `project_root: Path | None = None`, `scan_root: Path | None = None`,
    `agent_id: str | None = None`; properties `bom_ref -> str`,
    `output_basename -> str`.
  - `AgentKind` (frozen dataclass) with fields `id`, `display_name`, `cardinality`,
    `root_label`, `coverage_baseline: Mapping[str, str]`,
    `discover: Callable[[DiscoveryContext], list[AgentInstance]]`,
    `compose: Callable[..., Graph]`, `posture_rules: frozenset[str] | None = None`,
    `manifest_patterns: tuple[tuple[str, ParserFn], ...] = ()`,
    `posture_manifest_collectors: PostureCollectors | None = None`,
    `installed_posture_collectors: InstalledPostureCollectors | None = None`.
    `__post_init__` rejects a non-`None` `posture_rules` containing an id outside
    `tools.posture.KNOWN_RULE_IDS` (added in Task 8) via a lazy import, so a typo
    fails at kind-construction time instead of silently disabling a rule; the
    Claude Code kind's `posture_rules=None` never triggers the check.
    `manifest_patterns`, `posture_manifest_collectors`, and
    `installed_posture_collectors` are the kind's declared filesystem surface
    (spec "Internals not visible in a BOM" — "its manifest patterns, as
    polymorphic surface variants"): `manifest_patterns` is the flat
    `tools.parsers.REGISTRY`-shaped surface that `tools.parsers.parse_repo_grouped`
    (Task 7) reaches through instead of always reading the global registry, so
    the repo walk backing `bom repo`'s and a declared agent's evidence-gap count
    only counts that kind's own manifests. `posture_manifest_collectors` and
    `installed_posture_collectors` are their declared/installed posture-prep
    counterparts: Task 8 reads whichever one matches the agent's
    `composition_source` instead of calling Claude-Code-shaped collectors for
    every agent regardless of kind, and a synthetic second kind can set all
    three to prove a repo declaring two kinds keeps their manifests, posture
    inputs, and evidence-gap accounting apart. `compose` still wholly owns
    actual graph construction per kind — `manifest_patterns` is reached by the
    flat-registry repo walk, not by `build_rooted_graph`'s descent, which
    remains Claude Code's own composition logic (see Task 4's note on
    `descend`).
  - `DiscoveryContext` (frozen dataclass): `source`, `config_dir: Path | None = None`,
    `project_root: Path | None = None`, `scan_root: Path | None = None`,
    `include_gitignored: bool = False`.
  - `REGISTRY: tuple[AgentKind, ...]`, `kind_for(kind_id) -> AgentKind`,
    `discover_agents(ctx) -> list[AgentInstance]`,
    `build_agent_graph(agent, *, include_gitignored=False, warnings=None) -> Graph`,
    `resolve_coverage(baseline, *, evidence_gaps: int) -> str`,
    `slugify_agent_id(agent_id, *, max_length=64) -> str`,
    `output_basenames(agents: Sequence[AgentInstance]) -> dict[str, str]`.

**Naming note.** `Kind` is already taken in
`tools/parsers/claude_command_agent.py` for the `"command"`/`"agent"` *component* type.
Hence `agent_kinds` / `AgentKind` / `kind_id`, never a bare `Kind`.

**Import direction.** `tools/agent_kinds/*` imports `tools.graph_build`; `graph_build`
never imports `agent_kinds`. That is why `build_agent_graph` lives in the registry
rather than in the graph builder. `tools/agent_kinds/__init__.py` also imports
`tools.component_ref` (for `ParserFn`'s type only), and `claude_code.py` imports
`tools.parsers` and `tools.posture` to populate its own `manifest_patterns`,
`posture_manifest_collectors`, and `installed_posture_collectors` — neither of
those modules imports `agent_kinds` back.

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/test_agent_kinds.py
import pytest

import tools.agent_kinds as agent_kinds
from tools.graph import Graph
from tools.agent_kinds import (
    AgentInstance,
    AgentKind,
    DiscoveryContext,
    discover_agents,
    kind_for,
    output_basenames,
    resolve_coverage,
    slugify_agent_id,
)


def test_claude_code_installed_discovery_yields_one_agent(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    agents = discover_agents(DiscoveryContext(source="installed", config_dir=tmp_path))

    assert len(agents) == 1
    agent = agents[0]
    assert agent.kind_id == "claude-code"
    assert agent.display_name == "Claude Code"
    assert agent.agent_id is None
    assert agent.bom_ref == "root/claude-code"
    assert agent.root_label == "claude-code"
    assert agent.coverage_baseline == "complete"
    assert agent.config_root == tmp_path


def test_installed_agent_with_no_configuration_is_still_an_agent(tmp_path):
    empty_root = tmp_path / ".claude"
    empty_root.mkdir()

    agents = discover_agents(DiscoveryContext(source="installed", config_dir=empty_root))

    assert len(agents) == 1


def test_installed_discovery_yields_nothing_when_the_root_is_absent(tmp_path):
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=tmp_path / "missing")
    )

    assert agents == []


def test_singleton_kind_must_not_carry_an_agent_id():
    with pytest.raises(ValueError, match="singleton"):
        AgentInstance(
            kind_id="claude-code",
            display_name="Claude Code",
            source="installed",
            root_label="claude-code",
            coverage_baseline="complete",
            agent_id="oops",
        ).validate_against(kind_for("claude-code"))


def test_resolve_coverage_never_raises_the_baseline():
    assert resolve_coverage("complete", evidence_gaps=0) == "complete"
    assert resolve_coverage("complete", evidence_gaps=1) == "partial"
    assert resolve_coverage("partial", evidence_gaps=0) == "partial"
    assert resolve_coverage("unknown", evidence_gaps=3) == "unknown"


def test_slugify_agent_id_is_filesystem_safe_and_stable():
    assert slugify_agent_id("researcher") == "researcher"
    assert slugify_agent_id("Payments/Triage") == "payments-triage"
    assert slugify_agent_id("  spaced  name ") == "spaced-name"
    long = slugify_agent_id("x" * 200)
    assert len(long) <= 64
    assert long == slugify_agent_id("x" * 200)


def test_output_basenames_disambiguate_slug_collisions():
    def instance(agent_id):
        return AgentInstance(
            kind_id="synthetic",
            display_name=agent_id,
            source="installed",
            root_label="synthetic",
            coverage_baseline="partial",
            agent_id=agent_id,
        )

    a, b = instance("Payments/Triage"), instance("payments-triage")
    names = output_basenames([a, b])

    assert names[a.bom_ref] != names[b.bom_ref]
    assert all(name.startswith("synthetic--payments-triage") for name in names.values())


def test_discover_agents_rejects_a_duplicate_instance_key(monkeypatch):
    def discover_two_with_the_same_id(ctx):
        return [
            AgentInstance(
                kind_id="synthetic", display_name="Synthetic", source="installed",
                root_label="synthetic", coverage_baseline="partial", agent_id="dup",
            )
            for _ in range(2)
        ]

    broken_kind = AgentKind(
        id="synthetic", display_name="Synthetic", cardinality="many_per_place",
        root_label="synthetic", coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=discover_two_with_the_same_id, compose=lambda agent, **_: Graph(nodes={}),
    )
    monkeypatch.setattr(agent_kinds, "REGISTRY", (broken_kind,))

    with pytest.raises(ValueError, match="duplicate agent instance key"):
        discover_agents(DiscoveryContext(source="installed"))


def test_claude_code_declares_the_full_manifest_registry_as_its_surface():
    """Guards against the kind's declared surface silently narrowing (or
    widening) apart from `tools.parsers.REGISTRY` — the two are meant to be
    the same list by construction, not independently maintained."""
    from tools.agent_kinds import claude_code
    from tools.parsers import REGISTRY

    assert claude_code.KIND.manifest_patterns == tuple(REGISTRY)
    assert claude_code.KIND.posture_manifest_collectors is not None
    assert claude_code.KIND.installed_posture_collectors is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_kinds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.agent_kinds'`.

- [ ] **Step 3: Write the registry module**

```python
# tools/agent_kinds/__init__.py
"""Agent kind registry — the generalisation of the flat manifest registry.

A *kind* is what reads a composition (ADR-0044): two runtimes are the same kind
only if they read the same surface with the same schema. A kind declares its
discovery, composition, cardinality, coverage baseline per composition source,
node-key root label, display label, and posture-rule allowlist. Of that, only the
kind id and the resolved coverage reach a BOM.

Discovery returns a *list* of `AgentInstance`. That is the property that cannot be
retrofitted, so it is a list even while every registered kind is a singleton.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.bom import AGENT_ROOT_PREFIX
from tools.capability import COVERAGE_LEVELS
from tools.component_ref import ComponentRef
from tools.graph import Graph

Cardinality = Literal["singleton", "many_per_place"]
CompositionSource = Literal["installed", "declared"]
# A kind's declared manifest surface (spec: "It declares discovery,
# composition, its manifest patterns ..."). Same shape as
# `tools.parsers.REGISTRY` — (glob pattern, parser function) — so a kind can
# hand its patterns straight through without translation.
ParserFn = Callable[[Path], list[ComponentRef]]
# `collect_mcp_manifests`/`collect_settings_manifests` take
# `(roots, include_gitignored=...)` and callers pass the second by keyword, which
# a positional-only `Callable[[list[Path], bool], ...]` rejects under pyright —
# so this is `...` like its installed counterpart below.
PostureManifestCollector = Callable[..., list[tuple[Path, dict]]]
PostureCollectors = tuple[PostureManifestCollector, PostureManifestCollector]
# An installed agent's collectors take (config_root, project_root[, refs]) rather
# than a root list — the shape `collect_endpoint_mcp_manifests`/
# `collect_endpoint_settings_manifests` already have, so this is typed loosely
# rather than forcing both branches through one signature.
InstalledPostureCollector = Callable[..., list[tuple[Path, dict]]]
InstalledPostureCollectors = tuple[InstalledPostureCollector, InstalledPostureCollector]

COMPOSITION_SOURCES: frozenset[str] = frozenset({"installed", "declared"})

_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class AgentInstance:
    kind_id: str
    display_name: str
    source: CompositionSource
    root_label: str
    coverage_baseline: str
    config_root: Path | None = None
    project_root: Path | None = None
    scan_root: Path | None = None
    agent_id: str | None = None

    @property
    def bom_ref(self) -> str:
        if self.agent_id is None:
            return f"{AGENT_ROOT_PREFIX}{self.kind_id}"
        return f"{AGENT_ROOT_PREFIX}{self.kind_id}/{self.agent_id}"

    @property
    def output_basename(self) -> str:
        if self.agent_id is None:
            return self.kind_id
        return f"{self.kind_id}--{slugify_agent_id(self.agent_id)}"

    def validate_against(self, kind: AgentKind) -> AgentInstance:
        """Cardinality decides whether a discriminator is required or forbidden.

        A singleton emitting one means discovery is wrong (ADR-0045), which is
        worth failing loudly on rather than shipping in a document.
        """
        if kind.cardinality == "singleton" and self.agent_id is not None:
            raise ValueError(
                f"kind {kind.id!r} is singleton; agent_id must be absent, got {self.agent_id!r}"
            )
        if kind.cardinality == "many_per_place" and not self.agent_id:
            raise ValueError(f"kind {kind.id!r} has same-kind multiplicity; agent_id is required")
        return self


@dataclass(frozen=True)
class DiscoveryContext:
    source: CompositionSource
    config_dir: Path | None = None
    project_root: Path | None = None
    scan_root: Path | None = None
    include_gitignored: bool = False


@dataclass(frozen=True)
class AgentKind:
    id: str
    display_name: str
    cardinality: Cardinality
    root_label: str
    coverage_baseline: Mapping[str, str]
    discover: Callable[[DiscoveryContext], list[AgentInstance]]
    compose: Callable[..., Graph]
    posture_rules: frozenset[str] | None = None
    # The kind's repo-tree manifest surface (spec "Internals not visible in a
    # BOM": a kind declares discovery, composition, "its manifest patterns" —
    # polymorphic per kind shape, so a control-plane kind holds no filesystem
    # fields at all). Empty means the kind resolves its own filesystem lookups
    # entirely inside `compose`/`discover` rather than through this shared
    # declaration; Claude Code sets this explicitly (Step 4) to the same
    # `tools.parsers.REGISTRY` it already reads, so nothing observable changes
    # for the one kind that ships.
    manifest_patterns: tuple[tuple[str, ParserFn], ...] = ()
    # (mcp_collector, settings_collector) a *declared* agent's posture prep
    # (Task 8) reads through, instead of `_agent_scan_prep` calling
    # Claude-Code-shaped collectors for every kind unconditionally. `None`
    # means the kind has no filesystem-shaped posture surface.
    posture_manifest_collectors: PostureCollectors | None = None
    # (mcp_collector, settings_collector) an *installed* agent's posture prep
    # reads through — the installed-branch counterpart to
    # `posture_manifest_collectors` above, so a second installed kind is not
    # scanned with Claude Code's endpoint semantics. `None` means the kind has
    # no filesystem-shaped installed posture surface (e.g. a control-plane
    # kind whose installed state lives behind an API, not on disk).
    installed_posture_collectors: InstalledPostureCollectors | None = None

    def __post_init__(self) -> None:
        """A typo in an allowlist would silently disable an intended rule rather
        than error — fail at kind-construction time, against the same rule ids
        `tools.posture` actually runs."""
        if self.posture_rules is None:
            return
        from tools.posture import KNOWN_RULE_IDS

        unknown = self.posture_rules - KNOWN_RULE_IDS
        if unknown:
            raise ValueError(
                f"kind {self.id!r} allowlists unknown posture rule id(s): {sorted(unknown)}"
            )


def _registry() -> tuple[AgentKind, ...]:
    from tools.agent_kinds import claude_code

    return (claude_code.KIND,)


REGISTRY: tuple[AgentKind, ...] = _registry()


def kind_for(kind_id: str) -> AgentKind:
    for kind in REGISTRY:
        if kind.id == kind_id:
            return kind
    raise KeyError(f"unknown agent kind: {kind_id!r}")


def discover_agents(ctx: DiscoveryContext) -> list[AgentInstance]:
    agents: list[AgentInstance] = []
    seen: set[str] = set()
    for kind in REGISTRY:
        for agent in kind.discover(ctx):
            agent = agent.validate_against(kind)
            if agent.bom_ref in seen:
                raise ValueError(
                    f"duplicate agent instance key {agent.bom_ref!r}: "
                    f"kind {kind.id!r} discovery returned the same (kind, agent_id) twice"
                )
            seen.add(agent.bom_ref)
            agents.append(agent)
    return agents


def build_agent_graph(
    agent: AgentInstance,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    return kind_for(agent.kind_id).compose(
        agent, include_gitignored=include_gitignored, warnings=warnings
    )


def resolve_coverage(baseline: str, *, evidence_gaps: int) -> str:
    """`min(baseline, evidence)` (ADR-0046). Evidence never raises coverage."""
    if baseline not in COVERAGE_LEVELS:
        raise ValueError(f"unknown coverage level: {baseline!r}")
    observed = baseline if evidence_gaps == 0 else "partial"
    return min(baseline, observed, key=COVERAGE_LEVELS.index)


def slugify_agent_id(agent_id: str, *, max_length: int = 64) -> str:
    """A filesystem-safe rendering of an agent id, for output filenames only.

    The instance key keeps the raw value; case, Unicode, separators, and length
    are stricter constraints on a filename than on a key.
    """
    folded = unicodedata.normalize("NFKC", agent_id).strip().casefold()
    slug = _SLUG_UNSAFE.sub("-", folded).strip("-._")
    if len(slug) > max_length:
        digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[: max_length - 9].rstrip('-._')}-{digest}"
    return slug or hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:12]


def output_basenames(agents: Sequence[AgentInstance]) -> dict[str, str]:
    """Map each agent's `bom_ref` to a unique output basename.

    Two distinct agent ids can slug identically (`A/B` and `a-b`), and two files
    cannot share a name, so every member of a colliding group is suffixed with a
    digest of its `bom_ref` — deterministic and independent of discovery order.
    """
    by_basename: dict[str, list[AgentInstance]] = {}
    for agent in agents:
        by_basename.setdefault(agent.output_basename, []).append(agent)
    resolved: dict[str, str] = {}
    for basename, group in by_basename.items():
        if len(group) == 1:
            resolved[group[0].bom_ref] = basename
            continue
        for agent in group:
            digest = hashlib.sha256(agent.bom_ref.encode("utf-8")).hexdigest()[:8]
            resolved[agent.bom_ref] = f"{basename}-{digest}"
    return resolved
```

- [ ] **Step 4: Write the Claude Code kind**

`compose` is filled in by Task 4; until then it raises, and no caller reaches it.

```python
# tools/agent_kinds/claude_code.py
"""The Claude Code kind. The only registered kind (ADR-0044)."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
from tools.graph import Graph
from tools.parsers import REGISTRY as _MANIFEST_REGISTRY
from tools.posture import (
    collect_endpoint_mcp_manifests,
    collect_endpoint_settings_manifests,
    collect_mcp_manifests,
    collect_settings_manifests,
)

KIND_ID = "claude-code"
DISPLAY_NAME = "Claude Code"
ROOT_LABEL = "claude-code"


def resolve_config_root(config_dir: Path | None) -> Path:
    """Explicit `--config-dir` wins, then `$CLAUDE_CONFIG_DIR`, then `~/.claude`."""
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.source == "installed":
        return _discover_installed(ctx)
    return _discover_declared(ctx)


def _discover_installed(ctx: DiscoveryContext) -> list[AgentInstance]:
    """The runtime's own config root existing is the evidence (ADR-0044).

    An installed runtime with no configuration is a real agent with zero
    components, so an empty directory still yields an instance here — the
    asymmetry with `declared` is deliberate.
    """
    root = resolve_config_root(ctx.config_dir)
    if not root.is_dir():
        return []
    return [
        AgentInstance(
            kind_id=KIND_ID,
            display_name=DISPLAY_NAME,
            source="installed",
            root_label=ROOT_LABEL,
            coverage_baseline=COVERAGE_BASELINE["installed"],
            config_root=root,
            project_root=ctx.project_root,
        )
    ]


def _discover_declared(ctx: DiscoveryContext) -> list[AgentInstance]:
    """Declared evidence detection lands in Task 6. Returning an empty list keeps
    the declared path provably inert until then rather than half-wired."""
    return []


def _compose(
    agent: AgentInstance,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Graph construction lands in Task 4; nothing calls this yet."""
    raise NotImplementedError("agent graph construction lands in Task 4")


COVERAGE_BASELINE = {"installed": "complete", "declared": "complete"}

KIND = AgentKind(
    id=KIND_ID,
    display_name=DISPLAY_NAME,
    cardinality="singleton",
    root_label=ROOT_LABEL,
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    posture_rules=None,  # None = every rule applies; an allowlist is per-kind
    # The kind's declared manifest surface is today's whole registry and all
    # four posture collectors — byte-identical to what `scan endpoint`/`scan
    # repo` already read, since Claude Code is still the only kind. A future
    # second kind registers its own subset here instead of these module-level
    # functions being called unconditionally for every agent regardless of kind.
    manifest_patterns=tuple(_MANIFEST_REGISTRY),
    posture_manifest_collectors=(collect_mcp_manifests, collect_settings_manifests),
    installed_posture_collectors=(
        collect_endpoint_mcp_manifests,
        collect_endpoint_settings_manifests,
    ),
)
```

- [ ] **Step 5: Wire the registry-backed `agent_id` cardinality check into the linter**

The self-consistency check `check_agent_metadata` added in Task 2 confirms a stored
`agent_id` matches the document's own `bom-ref`, but the spec's discriminator rule is a
cardinality question — "does *this kind* allow more than one agent per place" — that
only the registry can answer (`docs/specs/multi-agent-support.md:527`). That import
could not exist until this task created `tools.agent_kinds`, so the check lands here,
immediately after the registry, rather than in Task 2:

```python
# tools/bom_lint.py
from tools.agent_kinds import REGISTRY


def _kind_cardinality(agent_kind: str) -> str | None:
    """`None` means the kind is unknown to this build's registry — third-party
    kinds this scanner has never registered are not a lint failure; there is
    nothing to check the discriminator against."""
    for kind in REGISTRY:
        if kind.id == agent_kind:
            return kind.cardinality
    return None
```

Extend `check_agent_metadata`, right after the existing `bom-ref` consistency check:

```python
    if agent_kind:
        cardinality = _kind_cardinality(agent_kind)
        if cardinality == "singleton" and agent_id is not None:
            errors.append(
                f"metadata.component: kind {agent_kind!r} is singleton; "
                "openaca:agent_id must be absent"
            )
        elif cardinality == "many_per_place" and not agent_id:
            errors.append(
                f"metadata.component: kind {agent_kind!r} has same-kind multiplicity; "
                "openaca:agent_id is required"
            )
```

Test both directions plus the unknown-kind escape hatch:

```python
def test_lint_rejects_agent_id_on_a_singleton_kind(tmp_path):
    doc = _agent_doc(
        bom_ref="root/claude-code/x",
        metadata_component_props={"openaca:agent_id": "x"},
    )
    path = tmp_path / "singleton.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "is singleton" in result.output


def test_lint_rejects_missing_agent_id_on_a_multiplicity_kind(tmp_path, monkeypatch):
    # Inline stand-in for a many-per-place kind — the shared synthetic-kind
    # fixture in `tests/fixtures/agent_kinds.py` does not exist until Task 7,
    # and this task's test suite must not depend forward on it.
    fake_kind = AgentKind(
        id="synthetic", display_name="Synthetic", cardinality="many_per_place",
        root_label="synthetic", coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=lambda ctx: [], compose=lambda agent, **_: Graph(nodes={}),
    )
    monkeypatch.setattr("tools.bom_lint.REGISTRY", (fake_kind,))
    doc = _agent_doc(
        bom_ref="root/synthetic",
        metadata_component_props={"openaca:agent_kind": "synthetic"},
    )
    path = tmp_path / "missing_id.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 1
    assert "same-kind multiplicity" in result.output


def test_lint_accepts_an_unknown_kind_without_a_cardinality_opinion(tmp_path):
    doc = _agent_doc(
        bom_ref="root/third-party-kind",
        metadata_component_props={"openaca:agent_kind": "third-party-kind"},
    )
    path = tmp_path / "unknown_kind.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["bom", "lint", str(path)])

    assert result.exit_code == 0, result.output
```

`tests/test_bom_lint.py` gains `from tools.agent_kinds import AgentKind` and
`from tools.graph import Graph` alongside its existing imports.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_kinds.py tests/test_bom_lint.py -v`
Expected: PASS. Then
`uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

- [ ] **Step 7: Commit**

```bash
git add tools/agent_kinds tools/bom_lint.py tests/test_agent_kinds.py tests/test_bom_lint.py
git commit -m "feat(agent-kinds): add the kind registry with Claude Code registered"
```

---

## Task 4: One graph per agent, rooted at the agent

**Files:**
- Modify: `tools/graph_build.py`
- Modify: `tools/agent_kinds/claude_code.py`
- Test: `tests/test_graph_build_agent.py`

**Interfaces:**
- Consumes: `AgentInstance` from Task 3.
- Produces: `tools.graph_build.build_rooted_graph(target: Path, mode: str, *,
  root_key: str, root_label: str = "endpoint", project_root: Path | None = None,
  include_gitignored: bool = False, warnings: list[str] | None = None) -> Graph`;
  a working `claude_code._compose`.

The root `Node` keeps `kind="target"`, so `Graph.root`, `Graph.validate`, and
`Graph.scope_of` are untouched — `scope_of`'s "an agent-component ancestor before the
target root" now means "before the agent root". Only the root's *key* changes, from the
fixed `_TARGET_KEY` to the agent's `bom_ref`.

`mode` is neither extended nor removed: `tools/remote/collector.py` calls
`build_graph(config_dir, mode="endpoint")` and must keep `endpoint/` labels.

- [ ] **Step 1: Write the failing parity test**

```python
# tests/test_graph_build_agent.py
from tools.agent_kinds import DiscoveryContext, build_agent_graph, discover_agents
from tools.graph_build import build_graph


def _endpoint_fixture(root):
    skill = root / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    return root


def test_agent_graph_matches_the_legacy_graph_with_relabelled_keys(tmp_path):
    root = _endpoint_fixture(tmp_path / ".claude")
    legacy = build_graph(root, mode="endpoint")
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]

    graph = build_agent_graph(agent)

    assert graph.root.key == "root/claude-code"
    assert graph.root.kind == "target"
    relabelled = {
        k.replace("endpoint/", "claude-code/", 1) if k.startswith("endpoint/") else k
        for k in legacy.nodes
        if k != legacy.root.key
    }
    assert {k for k in graph.nodes if k != graph.root.key} == relabelled
    assert len(graph.edges) == len(legacy.edges)


def test_legacy_endpoint_mode_is_unchanged(tmp_path):
    root = _endpoint_fixture(tmp_path / ".claude")

    legacy = build_graph(root, mode="endpoint")

    assert legacy.root.key == "openaca:target"
    assert any(k.startswith("endpoint/") for k in legacy.nodes)
    assert not any(k.startswith("claude-code/") for k in legacy.nodes)


def test_installed_agent_with_no_configuration_builds_an_empty_graph(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]

    graph = build_agent_graph(agent)

    assert list(graph.nodes) == ["root/claude-code"]
    assert graph.edges == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_graph_build_agent.py -v`
Expected: FAIL — `NotImplementedError: filled in by Task 4`.

- [ ] **Step 3: Parameterise the normalizer's root label**

In `tools/graph_build.py`, change `_make_normalizer`'s signature and its endpoint
branch:

```python
def _make_normalizer(
    mode: str,
    target: Path,
    install_root: Path,
    project_root: Path | None,
    root_label: str = "endpoint",
) -> SourceNormalizer:
```

and in the endpoint `normalize` closure replace `return f"endpoint/{rel}"` with
`return f"{root_label}/{rel}"`. Extend the docstring's endpoint bullet: the label names
the kind that **owns** the root (ADR-0045), which is why a file one runtime
compat-reads from another's config root keys identically in both agents' BOMs. `repo`
mode ignores the label — a repo has one root, so its keys stay bare relative paths.

- [ ] **Step 4: Extract `build_rooted_graph` and make `build_graph` a wrapper**

Rename the existing `build_graph` body to `build_rooted_graph` with the new
keyword-only `root_key` and `root_label`, changing only these two lines:

```python
    root = Node(key=root_key, kind="target", ref=None)
    ...
    normalize = _make_normalizer(mode, Path(target), Path(target), project_root, root_label)
```

Then:

```python
def build_graph(
    target: Path,
    mode: str,
    project_root: Path | None = None,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Legacy place-rooted graph. Retained for `tools/remote/collector.py`, whose
    upload contract keeps `endpoint/` labels and the `openaca:target` root ref
    until the collector is migrated to agent discovery."""
    return build_rooted_graph(
        target,
        mode,
        root_key=_TARGET_KEY,
        root_label="endpoint",
        project_root=project_root,
        include_gitignored=include_gitignored,
        warnings=warnings,
    )
```

- [ ] **Step 5: Implement `claude_code._compose`**

```python
def _compose(
    agent: AgentInstance,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    from tools.graph_build import build_rooted_graph

    if agent.source == "installed":
        assert agent.config_root is not None
        return build_rooted_graph(
            agent.config_root,
            "endpoint",
            root_key=agent.bom_ref,
            root_label=agent.root_label,
            project_root=agent.project_root,
            include_gitignored=include_gitignored,
            warnings=warnings,
        )
    assert agent.scan_root is not None
    return build_rooted_graph(
        agent.scan_root,
        "repo",
        root_key=agent.bom_ref,
        root_label=agent.root_label,
        include_gitignored=include_gitignored,
        warnings=warnings,
    )
```

The local import keeps the one-way dependency (`agent_kinds` → `graph_build`).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_graph_build_agent.py tests/test_graph_build.py -v`
Expected: PASS. Then the full gate.

- [ ] **Step 7: Commit**

```bash
git add tools/graph_build.py tools/agent_kinds/claude_code.py tests/test_graph_build_agent.py
git commit -m "feat(graph): build one graph per agent with owner-named root labels"
```

---

## Task 5: The agent becomes `metadata.component`

**Files:**
- Modify: `tools/bom.py`
- Modify: `tools/bom_cli.py`, `tools/scan.py` (call sites and the graph-backed gate)
- Test: `tests/test_bom.py`, `tests/test_scan.py`

**Interfaces:**
- Consumes: `AgentInstance.bom_ref`, `resolve_coverage` from Task 3;
  `build_agent_graph` from Task 4.
- Produces: `build_agent_bom(..., agent_kind: str | None = None, agent_id: str | None = None,
  agent_name: str | None = None, composition_source: str | None = None,
  composition_coverage: str | None = None)`;
  `tools.bom.AgentInfo` (frozen dataclass: `kind`, `agent_id`, `source`, `coverage`,
  `name`) and `agent_info_from_cyclonedx(doc) -> AgentInfo | None`.

- [ ] **Step 1: Write the failing emission test**

```python
# tests/test_bom.py
from tools.agent_kinds import DiscoveryContext, build_agent_graph, discover_agents
from tools.bom import agent_info_from_cyclonedx, build_agent_bom


def test_agent_bom_metadata_component_is_the_agent(tmp_path):
    root = tmp_path / ".claude"
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\n", encoding="utf-8"
    )
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]
    graph = build_agent_graph(agent)

    doc = build_agent_bom(
        [],
        graph=graph,
        target=str(root),
        agent_kind=agent.kind_id,
        agent_name=agent.display_name,
        composition_source=agent.source,
        composition_coverage="complete",
    ).to_cyclonedx()

    component = doc["metadata"]["component"]
    assert component["bom-ref"] == "root/claude-code"
    assert component["name"] == "Claude Code"
    props = {p["name"]: p["value"] for p in component["properties"]}
    assert props == {
        "openaca:agent_kind": "claude-code",
        "openaca:composition_source": "installed",
        "openaca:composition_coverage": "complete",
    }
    metadata_props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert metadata_props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in metadata_props
    assert any(k.startswith("claude-code/") for k in
               [c["bom-ref"] for c in doc["components"]])


def test_agent_bom_stops_writing_agent_host_and_runtime_hosts(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]
    doc = build_agent_bom(
        [], graph=build_agent_graph(agent), agent_kind="claude-code",
        agent_name="Claude Code", composition_source="installed",
        composition_coverage="complete",
    ).to_cyclonedx()

    names = {p["name"] for c in doc["components"] for p in c.get("properties", [])}
    assert "openaca:agent_host" not in names
    assert "openaca:runtime_hosts" not in names


def test_agent_info_round_trips():
    doc = {"metadata": {"component": {"bom-ref": "root/claude-code", "name": "Claude Code",
        "properties": [
            {"name": "openaca:agent_kind", "value": "claude-code"},
            {"name": "openaca:composition_source", "value": "installed"},
            {"name": "openaca:composition_coverage", "value": "complete"}]}}}

    info = agent_info_from_cyclonedx(doc)

    assert info is not None
    assert (info.kind, info.agent_id, info.source, info.coverage) == (
        "claude-code", None, "installed", "complete")


def test_agent_info_is_none_for_a_stored_0_4_document():
    doc = {"metadata": {"component": {"bom-ref": "openaca:target", "name": "/home/u/.claude"}}}

    assert agent_info_from_cyclonedx(doc) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bom.py -k agent -v`
Expected: FAIL — `build_agent_bom() got an unexpected keyword argument 'agent_kind'`.

- [ ] **Step 3: Add the agent fields and emission to `tools/bom.py`**

Bump the version constant (`AGENT_ROOT_PREFIX` already landed in Task 2):

```python
OPENACA_BOM_SCHEMA_VERSION = "0.5"
```

On `AgentBOM`, make `target_type` optional and add the agent fields:

```python
@dataclass(frozen=True)
class AgentBOM:
    components: list[BOMComponent]
    edges: list[BOMEdge]
    target_type: str | None = None
    target: str | None = None
    source_unit_count: int | None = None
    source_unit_label: str | None = None
    target_bom_ref: str | None = None
    agent_kind: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    composition_source: str | None = None
    composition_coverage: str | None = None
```

In `to_cyclonedx`, guard the `target_type` property and build the agent metadata
component:

```python
        metadata_properties = [
            {"name": "openaca:schema_version", "value": OPENACA_BOM_SCHEMA_VERSION},
        ]
        if self.target_type is not None:
            metadata_properties.append(
                {"name": "openaca:target_type", "value": self.target_type}
            )
```

and replace the `metadata["component"]` block with:

```python
        if self.target_bom_ref is not None:
            metadata["component"] = self._metadata_component()
```

```python
    def _metadata_component(self) -> dict[str, Any]:
        assert self.target_bom_ref is not None
        if self.agent_kind is None:
            # Legacy place-rooted document (the remote collector).
            return {
                "type": "application",
                "bom-ref": self.target_bom_ref,
                "name": self.target or self.target_bom_ref,
                "properties": [{"name": "openaca:component_type", "value": "target"}],
            }
        properties = [{"name": "openaca:agent_kind", "value": self.agent_kind}]
        if self.agent_id is not None:
            properties.append({"name": "openaca:agent_id", "value": self.agent_id})
        properties.append(
            {"name": "openaca:composition_source", "value": self.composition_source or ""}
        )
        properties.append(
            {"name": "openaca:composition_coverage", "value": self.composition_coverage or ""}
        )
        return {
            "type": "application",
            "bom-ref": self.target_bom_ref,
            "name": self.agent_name or self.agent_kind,
            "properties": properties,
        }
```

Thread the five new keyword-only arguments through `build_agent_bom` and
`_build_agent_bom_from_graph` (defaults `None`, passed straight into `AgentBOM`), and
change `build_agent_bom`'s `target_type` parameter to `str | None = None`.

Delete the two property writes in `_component_properties` and the now-unused
`_agent_host` helper:

```python
    _append_prop(props, "openaca:agent_host", _agent_host(ref))          # delete
    _append_json_prop(props, "openaca:runtime_hosts", (ref.extra or {}).get("runtime_hosts"))  # delete
```

Keep `_extra_from_properties`' restoration of both — stored `0.4` documents still carry
them.

- [ ] **Step 4: Add the agent-metadata reader**

```python
@dataclass(frozen=True)
class AgentInfo:
    kind: str
    agent_id: str | None
    source: str | None
    coverage: str | None
    name: str | None


def agent_info_from_cyclonedx(doc: dict[str, Any]) -> AgentInfo | None:
    """Agent metadata from an agent-rooted document, else None.

    The `root/` bom-ref prefix is the document-shape signal (ADR-0045), which is
    what `openaca:target_type` used to answer.
    """
    metadata = doc.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(component, dict):
        return None
    bom_ref = component.get("bom-ref")
    if not isinstance(bom_ref, str) or not bom_ref.startswith(AGENT_ROOT_PREFIX):
        return None
    props = _properties_by_name(component)
    kind = props.get("openaca:agent_kind")
    if not kind:
        return None
    name = component.get("name")
    return AgentInfo(
        kind=kind,
        agent_id=props.get("openaca:agent_id"),
        source=props.get("openaca:composition_source"),
        coverage=props.get("openaca:composition_coverage"),
        name=name if isinstance(name, str) else None,
    )
```

- [ ] **Step 5: Re-key the graph-backed gate and the `scan bom` selectors**

In `tools/scan.py`:

```python
def _is_graph_backed_bom(doc: dict[str, object]) -> bool:
    """Does this BOM encode the OpenACA composition graph (vs. a flat BOM)?

    True for the legacy logical target key and for any agent-rooted document.
    ADR-0045 makes this prefix load-bearing: misreading it silently drops
    agent-dependency findings.
    """
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return False
    component = metadata.get("component")
    if not isinstance(component, dict):
        return False
    ref = component.get("bom-ref")
    if not isinstance(ref, str):
        return False
    return ref == _TARGET_KEY or ref.startswith(AGENT_ROOT_PREFIX)
```

`_render_bom_inventory_tree` takes `declared: bool` instead of `target_type: str | None`,
and the `scan bom` command computes it as:

```python
    agent_info = agent_info_from_cyclonedx(doc)
    target_type, target = target_info_from_cyclonedx(doc)
    declared = (
        agent_info.source == "declared" if agent_info is not None else target_type == "repo"
    )
```

The card's "original target" row prefers the agent when present:

```python
    if agent_info is not None:
        bom_rows.append(("agent", f"{agent_info.kind} ({agent_info.source})"))
        bom_rows.append(("coverage", agent_info.coverage or "unknown"))
    elif target_type:
        orig = f"{target_type} {target}".strip() if target else target_type
        bom_rows.append(("original target", orig))
```

Existing `build_agent_bom(..., target_type="bom")` calls inside `scan bom` keep their
argument: that document is never emitted, only used to project refs.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bom.py tests/test_scan.py tests/test_bom_lint.py tests/remote -v`
Expected: PASS. The collector's tests must be untouched — if any fail, `build_graph`'s
wrapper is wrong, not the test.

- [ ] **Step 7: Commit**

```bash
git add tools/bom.py tools/scan.py tests/test_bom.py tests/test_scan.py
git commit -m "feat(bom): root the document at the agent; stop writing three properties"
```

---

## Task 6: Declared discovery — `repo` scans become agent-rooted

**Files:**
- Modify: `tools/agent_kinds/claude_code.py`
- Test: `tests/test_agent_kinds.py`

**Interfaces:**
- Consumes: `DiscoveryContext.scan_root`, `include_gitignored`.
- Produces: `claude_code.declared_evidence(scan_root: Path, *,
  include_gitignored: bool = False) -> Path | None`; a working `_discover_declared`.

Evidence for `declared` is **a file the kind owns** (ADR-0044) — an empty directory is
not evidence, because Git does not preserve one. Nested dot-directories are more
*surfaces of one agent*, not more agents, because the kind is singleton.

**Which files count.** Evidence is the specific set of paths Claude Code is known to
read — `.claude/settings.json`, `.claude/settings.local.json`,
`.claude/skills/*/SKILL.md`, `.claude/commands/*`, `.claude/agents/*`, and
`.claude-plugin/plugin.json` — plus the project `.mcp.json`, which is Claude Code's
project MCP file, so it counts. This is narrower than "any file under `.claude/`":
content files with no recognized role (a stray `.claude/CLAUDE.md`, a cache directory
a future Claude Code version might write) are not evidence on their own, because
`declared_evidence` proves *composition surfaces exist*, not merely that a `.claude/`
directory exists — the same reasoning that already excludes an empty directory. A bare
`mcp.json` does **not** count: the spec calls it a manifest no kind owns exclusively, so
on its own it declares no agent — though once an agent *is* declared, it is a component
of that agent exactly as today.

- [ ] **Step 1: Write the failing declared-discovery tests**

```python
# tests/test_agent_kinds.py
from tools.agent_kinds import DiscoveryContext, discover_agents
from tools.agent_kinds.claude_code import declared_evidence


def test_declared_discovery_finds_an_agent_from_an_owned_file(tmp_path):
    skill = tmp_path / "apps" / "web" / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert len(agents) == 1
    assert agents[0].source == "declared"
    assert agents[0].bom_ref == "root/claude-code"
    assert agents[0].scan_root == tmp_path


def test_nested_dot_directories_are_one_agent(tmp_path):
    for app in ("web", "api"):
        d = tmp_path / "apps" / app / ".claude"
        d.mkdir(parents=True)
        (d / "settings.json").write_text("{}", encoding="utf-8")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert len(agents) == 1


def test_a_repo_of_ordinary_manifests_declares_no_agent(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    assert declared_evidence(tmp_path) is None
    assert discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path)) == []


def test_an_empty_claude_directory_is_not_evidence(tmp_path):
    (tmp_path / ".claude").mkdir()

    assert declared_evidence(tmp_path) is None


def test_project_mcp_json_is_evidence(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    assert declared_evidence(tmp_path) is not None


def test_an_unrecognized_file_under_claude_is_not_evidence(tmp_path):
    """`.claude/` holding *some* file is not by itself proof of a composition
    surface — only the enumerated, recognized ones are."""
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "CLAUDE.md").write_text("# notes", encoding="utf-8")

    assert declared_evidence(tmp_path) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_kinds.py -k declared -v`
Expected: FAIL with `ImportError: cannot import name 'declared_evidence'`.

- [ ] **Step 3: Implement declared discovery**

```python
# tools/agent_kinds/claude_code.py
from fnmatch import fnmatch

from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec

# Files Claude Code owns. Evidence of a *declared* agent is one of these
# existing (ADR-0044); a bare `mcp.json` is excluded because no kind owns it
# exclusively, so on its own it declares no agent. Deliberately narrower than
# every file under `.claude/`: this list is recognized composition surfaces,
# not arbitrary content (a `.claude/CLAUDE.md` alone is not evidence). It is
# not required to match `tools/parsers/__init__.py`'s composition patterns —
# evidence answers "does an agent exist", composition answers "what does it
# contain" — but the entries here name the same surfaces that module parses.
_DECLARED_EVIDENCE_PATTERNS: tuple[str, ...] = (
    ".mcp.json",
    "*/.mcp.json",
    ".claude/settings.json",
    "*/.claude/settings.json",
    ".claude/settings.local.json",
    "*/.claude/settings.local.json",
    ".claude/skills/*/SKILL.md",
    "*/.claude/skills/*/SKILL.md",
    ".claude/commands/*",
    "*/.claude/commands/*",
    ".claude/agents/*",
    "*/.claude/agents/*",
    ".claude-plugin/plugin.json",
    "*/.claude-plugin/plugin.json",
)


def _matches_evidence(rel: str) -> bool:
    for pattern in _DECLARED_EVIDENCE_PATTERNS:
        if fnmatch(rel, pattern):
            return True
        # `*/` in the patterns above matches one segment; a declaration may sit
        # at any depth, so also test every suffix of the path.
        if pattern.startswith("*/") and fnmatch(rel, f"*/{pattern}"):
            return True
    return False


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Claude Code agent, else None.

    The walk is the same gitignore-aware walk the repo scan uses, so evidence and
    composition never disagree about what is in scope. A declaration inside an
    ignored directory is invisible to both unless `--include-gitignored` is set.
    """
    spec = None if include_gitignored else load_gitignore_spec(scan_root)
    for path in iter_unignored_files(scan_root, spec):
        try:
            rel = path.relative_to(scan_root).as_posix()
        except ValueError:
            continue
        if _matches_evidence(rel):
            return path
    return None


def _discover_declared(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.scan_root is None:
        return []
    if declared_evidence(ctx.scan_root, include_gitignored=ctx.include_gitignored) is None:
        return []
    return [
        AgentInstance(
            kind_id=KIND_ID,
            display_name=DISPLAY_NAME,
            source="declared",
            root_label=ROOT_LABEL,
            coverage_baseline=COVERAGE_BASELINE["declared"],
            scan_root=ctx.scan_root,
        )
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_kinds.py -v`
Expected: PASS. Then the full gate.

- [ ] **Step 5: Commit**

```bash
git add tools/agent_kinds/claude_code.py tests/test_agent_kinds.py
git commit -m "feat(agent-kinds): discover declared Claude Code agents in a repo tree"
```

---

## Task 7: Per-agent BOM emission — NDJSON and `--output-dir`

**Files:**
- Modify: `tools/bom_cli.py`, `tools/parsers/__init__.py`
- Create: `tests/fixtures/agent_kinds.py`
- Test: `tests/test_bom_cli_agents.py`, `tests/test_bom_cli.py`, `tests/test_parsers.py`

**Interfaces:**
- Consumes: `discover_agents`, `build_agent_graph`, `output_basenames`,
  `resolve_coverage`, `kind_for` (Task 3), `build_agent_bom` agent arguments
  (Task 5), declared discovery (Task 6).
- Produces: `tools.bom_cli.emit_bom_documents(documents: list[tuple[str, dict]], *,
  output_path: Path | None, output_dir: Path | None) -> None` — pairs are
  `(basename, cyclonedx_document)`;
  `tests.fixtures.agent_kinds.register_synthetic_kind(monkeypatch, *, agent_ids)`;
  `tools.parsers.parse_repo_grouped(root, include_gitignored=False, *,
  registry=REGISTRY)` gains the keyword-only `registry` parameter (default
  unchanged, so every existing caller is byte-identical) so a caller can walk a
  root against one kind's own `manifest_patterns` instead of always the global
  flat registry — this is "the flat manifest registry splits per kind, reached
  through a surface" (spec, "What changes in the scanner").

`bom repo` calls `parse_repo_grouped(target, include_gitignored=include_gitignored,
registry=kind_for(agent.kind_id).manifest_patterns)` for its `n_found`/evidence-gap
count, so a repo declaring two different kinds counts each kind's own manifests
rather than the union. Since Claude Code's `manifest_patterns` is `tuple(REGISTRY)`
(Task 3 Step 4), this is byte-identical while Claude Code is the only kind.

Governing rule from the spec: *a consumer must never need to know the agent count in
advance to parse the output.* So no conditional shape — stdout is always NDJSON, one
document per line, and one agent is a single line that stays valid JSON.

- [ ] **Step 1: Write the synthetic test kind**

```python
# tests/fixtures/agent_kinds.py
"""A test-only kind. Nothing shipping returns more than one agent, so the
multi-document paths are only reachable through this.

It declares itself through the same registry API a real kind uses; a change that
re-specialises a path for Claude Code therefore breaks it.
"""

from __future__ import annotations

from pathlib import Path

import tools.agent_kinds as agent_kinds
from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
from tools.graph import Graph, Node

SYNTHETIC_ID = "synthetic"


def register_synthetic_kind(monkeypatch, *, agent_ids: list[str]) -> AgentKind:
    def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
        return [
            AgentInstance(
                kind_id=SYNTHETIC_ID,
                display_name=f"Synthetic {agent_id}",
                source=ctx.source,
                root_label=SYNTHETIC_ID,
                coverage_baseline="partial",
                config_root=ctx.config_dir or Path("."),
                scan_root=ctx.scan_root,
                agent_id=agent_id,
            )
            for agent_id in agent_ids
        ]

    def compose(agent, *, include_gitignored=False, warnings=None) -> Graph:
        root = Node(key=agent.bom_ref, kind="target", ref=None)
        return Graph(nodes={root.key: root})

    kind = AgentKind(
        id=SYNTHETIC_ID,
        display_name="Synthetic",
        cardinality="many_per_place",
        root_label=SYNTHETIC_ID,
        coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=discover,
        compose=compose,
    )
    monkeypatch.setattr(agent_kinds, "REGISTRY", (kind,))
    return kind
```

- [ ] **Step 2: Write the failing emission tests**

```python
# tests/test_bom_cli_agents.py
import json

from click.testing import CliRunner

from tests.fixtures.agent_kinds import register_synthetic_kind
from tools.bom_cli import main as bom_main


def test_single_agent_stdout_is_one_json_line(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["endpoint", "--config-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["metadata"]["component"]["bom-ref"] == "root/claude-code"


def test_many_agents_stream_as_ndjson(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer", "critic"])

    result = CliRunner().invoke(bom_main, ["endpoint", "--config-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    docs = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert [d["metadata"]["component"]["bom-ref"] for d in docs] == [
        "root/synthetic/researcher", "root/synthetic/writer", "root/synthetic/critic"]
    props = {p["name"]: p["value"] for p in docs[0]["metadata"]["component"]["properties"]}
    assert props["openaca:agent_id"] == "researcher"


def test_output_dir_writes_one_file_per_agent(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    out = tmp_path / "boms"

    result = CliRunner().invoke(
        bom_main, ["endpoint", "--config-dir", str(tmp_path), "--output-dir", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == [
        "synthetic--researcher.cdx.json", "synthetic--writer.cdx.json"]


def test_output_dir_drops_a_stale_file_when_fewer_agents_resolve(monkeypatch, tmp_path):
    """A consumer reading `--output-dir` after a rerun must not see an agent
    that no longer resolves — the directory holds this run's `*.cdx.json`
    set, not every set ever written to it. A non-`.cdx.json` file the user
    placed there is left alone."""
    out = tmp_path / "boms"
    register_synthetic_kind(monkeypatch, agent_ids=["researcher", "writer"])
    CliRunner().invoke(bom_main, ["endpoint", "--config-dir", str(tmp_path), "--output-dir", str(out)])
    (out / "notes.txt").write_text("kept", encoding="utf-8")

    register_synthetic_kind(monkeypatch, agent_ids=["writer"])
    result = CliRunner().invoke(
        bom_main, ["endpoint", "--config-dir", str(tmp_path), "--output-dir", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == ["notes.txt", "synthetic--writer.cdx.json"]
    assert (out / "notes.txt").read_text(encoding="utf-8") == "kept"


def test_output_errors_only_when_more_than_one_agent_resolves(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])

    result = CliRunner().invoke(
        bom_main,
        ["endpoint", "--config-dir", str(tmp_path), "--output", str(tmp_path / "one.json")],
    )

    assert result.exit_code != 0
    assert "--output-dir" in result.output


def test_repo_with_no_declaration_emits_no_document(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bom_cli_agents.py -v`
Expected: FAIL — `no such option: --output-dir`, and stdout still `indent=2`.

- [ ] **Step 4: Implement the sink and rewrite the two commands**

```python
_output_dir_option = click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write one CycloneDX Agent BOM per agent into this directory.",
)


def emit_bom_documents(
    documents: list[tuple[str, dict]],
    *,
    output_path: Path | None,
    output_dir: Path | None,
) -> None:
    """One document per agent. stdout is NDJSON so a consumer never needs to know
    the agent count in advance; `--output-dir` is one file per agent.

    `--output` is deprecated rather than removed: it keeps working for a single
    agent and errors only when one path genuinely cannot hold the result.

    `--output-dir` owns the `*.cdx.json` namespace inside that directory, not the
    directory itself: a rerun that resolves fewer agents than the previous one
    (an agent removed, a kind's discovery narrowed) must not leave that agent's
    stale file behind for a consumer to misread as still current. Every existing
    `*.cdx.json` in the directory is cleared before this run's set is written;
    anything else a user placed there (a different extension, a subdirectory) is
    untouched, since the tool never claimed that namespace.
    """
    if output_dir is not None and output_path is not None:
        raise click.ClickException("--output and --output-dir are mutually exclusive")
    if output_dir is not None:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            for stale in output_dir.glob("*.cdx.json"):
                stale.unlink()
        except OSError as exc:
            raise click.ClickException(f"failed to prepare {output_dir}: {exc}") from exc
        for basename, document in documents:
            path = output_dir / f"{basename}.cdx.json"
            try:
                path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")
            except OSError as exc:
                raise click.ClickException(f"failed to write BOM to {path}: {exc}") from exc
        return
    if output_path is not None:
        if len(documents) > 1:
            raise click.ClickException(
                f"{len(documents)} agents resolved; --output holds one document. "
                "Use --output-dir instead."
            )
        if not documents:
            return
        try:
            output_path.write_text(
                f"{json.dumps(documents[0][1], indent=2)}\n", encoding="utf-8"
            )
        except OSError as exc:
            raise click.ClickException(f"failed to write BOM to {output_path}: {exc}") from exc
        return
    for _, document in documents:
        click.echo(json.dumps(document, separators=(",", ":")))
```

`bom endpoint` becomes:

```python
def endpoint(config_dir, project, output_path, output_dir):
    """Generate one Agent BOM per installed agent."""
    ctx = DiscoveryContext(
        source="installed", config_dir=config_dir, project_root=project
    )
    agents = discover_agents(ctx)
    if not agents:
        click.echo("no installed agent found", err=True)
        return
    basenames = output_basenames(agents)
    documents: list[tuple[str, dict]] = []
    for agent in agents:
        warnings: list[str] = []
        graph = build_agent_graph(agent, warnings=warnings)
        for w in warnings:
            click.echo(f"warning: {w}", err=True)
        refs = _refs_from_graph(graph)
        bom = build_agent_bom(
            _filter_agent_scope_refs(refs),
            target=str(agent.config_root),
            source_unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
            source_unit_label="active plugin",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings)
            ),
        )
        documents.append((basenames[agent.bom_ref], bom.to_cyclonedx()))
    emit_bom_documents(documents, output_path=output_path, output_dir=output_dir)
```

`bom repo` mirrors it with `DiscoveryContext(source="declared", scan_root=target,
include_gitignored=include_gitignored)`, `source_unit_label="manifest"`,
`source_unit_count=n_found` and `parse_groups` from
`parse_repo_grouped(target, include_gitignored=include_gitignored,
registry=kind_for(agent.kind_id).manifest_patterns)`,
`evidence_gaps=n_found - len(parse_groups)`, and the no-agent branch echoing
`f"{target} declares no agent"` on stderr before returning. Passing the agent's own
`manifest_patterns` (rather than the module-level default) is what makes "the flat
manifest registry splits per kind, reached through a surface" true for `bom repo`,
not just declared: a many-per-place kind returning several declared agents over one
root calls `parse_repo_grouped` once per agent, so this loop mirrors Task 8's
`repo_parse_cache` and memoizes the walk per `(scan_root, manifest_patterns)` pair
rather than re-walking for every agent that shares a root and a kind.

In `tools/parsers/__init__.py`, `parse_repo_grouped` gains the keyword-only
`registry: Sequence[tuple[str, ParserFn]] = REGISTRY` parameter and iterates that
parameter instead of the module-level `REGISTRY` name directly inside its loop
(`for pattern, parser in registry:`). No existing caller passes `registry`, so
every current call site — the legacy `repo` command, `tools/graph_build.py`'s own
uses of the module, and this task's own default before `kind_for` is threaded in —
is byte-identical.

`--config-dir`/`--project` keep their options; `_resolve_endpoint_config_dir` in
`bom_cli.py` is deleted in favour of `claude_code.resolve_config_root`, reached through
discovery. (Consolidating the *other* two copies is out of scope.)

```python
# tests/test_parsers.py
def test_parse_repo_grouped_reads_only_the_given_registry(tmp_path):
    """The `registry` keyword is what makes "the flat manifest registry
    splits per kind, reached through a surface" true — a caller can walk the
    same tree against a subset of REGISTRY without REGISTRY itself changing."""
    from tools.parsers import parse_repo_grouped
    from tools.parsers.mcp_json import parse as parse_mcp
    from tools.parsers.package_json import parse as parse_package

    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")

    mcp_only, n_mcp = parse_repo_grouped(tmp_path, registry=((".mcp.json", parse_mcp),))
    pkg_only, n_pkg = parse_repo_grouped(tmp_path, registry=(("package.json", parse_package),))

    assert [p.name for p, _ in mcp_only] == [".mcp.json"] and n_mcp == 1
    assert [p.name for p, _ in pkg_only] == ["package.json"] and n_pkg == 1


def test_parse_repo_grouped_default_registry_is_unchanged(tmp_path):
    """No `registry` argument still walks the full global registry — every
    existing caller (the legacy `repo` command, `bom repo` before this task,
    `tools/graph_build.py`) is byte-identical."""
    from tools.parsers import REGISTRY, parse_repo_grouped

    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")

    grouped, n_found = parse_repo_grouped(tmp_path)
    default_grouped, default_n_found = parse_repo_grouped(tmp_path, registry=REGISTRY)

    assert grouped == default_grouped and n_found == default_n_found
```

```python
# tests/test_bom_cli_agents.py (continued)
def test_bom_repo_reads_the_agent_s_own_manifest_registry(tmp_path, monkeypatch):
    """`bom repo` must walk each agent's own `manifest_patterns`, not always the
    global registry — otherwise a repo declaring two different kinds counts one
    kind's manifests against the other's evidence gaps."""
    from dataclasses import replace

    import tools.agent_kinds as agent_kinds
    import tools.parsers
    from tests.fixtures.agent_kinds import register_synthetic_kind
    from tools.parsers.mcp_json import parse as parse_mcp

    kind = register_synthetic_kind(monkeypatch, agent_ids=["a"])
    # `AgentKind` is frozen — build the surface-bearing kind directly rather
    # than mutating the fixture's instance, then re-register it.
    kind = replace(kind, manifest_patterns=((".mcp.json", parse_mcp),))
    monkeypatch.setattr(agent_kinds, "REGISTRY", (kind,))

    seen_registries = []
    real_parse_repo_grouped = tools.parsers.parse_repo_grouped

    def spy(root, include_gitignored=False, *, registry=tools.parsers.REGISTRY):
        seen_registries.append(registry)
        return real_parse_repo_grouped(root, include_gitignored=include_gitignored, registry=registry)

    monkeypatch.setattr("tools.bom_cli.parse_repo_grouped", spy)

    CliRunner().invoke(cli, ["bom", "repo", "--target", str(tmp_path)])

    assert seen_registries == [kind.manifest_patterns]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bom_cli_agents.py tests/test_bom_cli.py tests/test_parsers.py -v`
Expected: PASS. Then the full gate.

- [ ] **Step 6: Commit**

```bash
git add tools/bom_cli.py tools/parsers/__init__.py tests/fixtures/agent_kinds.py \
        tests/test_bom_cli_agents.py tests/test_bom_cli.py tests/test_parsers.py
git commit -m "feat(cli): emit one Agent BOM per agent, NDJSON to stdout"
```

`scan bom`'s NDJSON input lands in Task 8, once `render_json`'s `agents` argument and the
per-agent card loop it renders through exist — reading multiple documents has nowhere to
put the plurality it discovers until then.

---

## Task 8: Per-agent scan pipeline, cards, and `agents[]`

**Files:**
- Modify: `tools/scan.py`, `tools/render.py`, `tools/matcher.py`,
  `tools/finding_output.py`, `tools/sarif.py`, `tools/posture/__init__.py`
- Test: `tests/test_scan.py`, `tests/test_render.py`, `tests/test_sarif.py`,
  `tests/test_posture_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 3–7.
- Produces:
  - `tools.matcher.Finding` gains `agent_kind: str | None = None` and
    `agent_id: str | None = None` (additive, defaulted — `Finding` is exported via
    `openaca.core`).
  - `tools.render.AgentSummary` (frozen dataclass: `kind: str | None`, `agent_id`,
    `source`, `coverage`, `host_surface` — `kind` is `None` only for a stored `0.4`
    document read back by `scan bom`, which carries no agent metadata at all).
  - `render_text(..., cards: list[AgentCard])` and
    `render_json(..., agents: list[AgentSummary])`, where
    `AgentCard` bundles `target: RenderTarget`, `inventory_tree: str | None`,
    `next_actions: list[str]`, `graph: Graph | None`, `findings: list[Finding]`,
    `posture_findings: list[PostureFinding] | None`, and
    `observations: list[ObservationFinding]` — the last two are that agent's own
    slice, not the scan-wide lists, so each posture/observation row renders exactly
    once, under its own agent's card.
  - `PostureFinding` and `ObservationFinding` gain `agent_kind: str | None = None` and
    `agent_id: str | None = None` (additive, defaulted), mirroring `Finding`.
  - `run_posture_rules(refs, manifests, settings_manifests=None, *,
    allowed_rules: frozenset[str] | None = None, agent_kind: str | None = None,
    agent_id: str | None = None)`.
  - `_collect_scanner_findings(refs, *, external_scanners, skillspector_progress=None,
    agent_kind: str | None = None, agent_id: str | None = None)`.
  - `tools.scan.AgentScanPrep` (frozen dataclass: `manifests`,
    `settings_manifests`, `target_rows`, `next_actions`, `unit_count`,
    `unit_label`, `parse_failed`) — the per-source differences `_agent_scan_prep`
    resolves. A dataclass rather than a tuple because `unit_count`/`unit_label`
    join `parse_failed` here: the repo walk that produced `ScanStats.unit_count`
    moved inside this function, so it has to come back out.
  - `tools.finding_output.graph_for(finding, graph=None, graphs=None) -> Graph | None`
    and a `graphs: Mapping[tuple[str | None, str | None], Graph] | None` keyword on
    `render_json`, `render_github`, `to_sarif`, `_scan_json_document`, and `_emit`.
    A finding's lineage comes from *its own* agent's graph; with N agents there is
    no single scan-wide graph to derive `introduction_path` from, and looking a
    finding up in another agent's graph yields a wrong or empty path. Keyed on
    `(agent_kind, agent_id)`, falling back to the scan-wide `graph` — so the
    one-agent case is byte-identical to today.

Per ADR-0047: text prints one card per agent; `--format json`/`github` emit one
document with a flat findings list plus `agents[]`.

**One OSV corpus for the whole scan.** Federation is network work, so collect the union
of every agent's refs, fetch once, then match per agent against the shared corpus.
Matching is per agent because attribution and lineage come from that agent's own graph.

- [ ] **Step 1: Write the failing pipeline tests**

```python
# tests/test_scan.py
def test_scan_json_carries_agents_and_per_finding_agent(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        scan_main, ["endpoint", "--config-dir", str(root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)          # ONE document, still json.load-able
    assert doc["agents"] == [{
        "kind": "claude-code", "agent_id": None, "source": "installed",
        "coverage": "complete", "host_surface": "Claude Code"}]
    assert doc["target"]["host_surface"] == "Claude Code"
    for finding in doc["findings"]:
        assert finding["agent"] == {"kind": "claude-code", "agent_id": None}


def test_scan_reports_a_zero_component_agent(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()

    result = CliRunner().invoke(
        scan_main, ["endpoint", "--config-dir", str(root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["findings"] == []
    assert len(doc["agents"]) == 1          # the agent exists even with nothing configured


def test_scan_prints_one_card_per_agent(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])

    result = CliRunner().invoke(scan_main, ["endpoint", "--config-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.output.count("host surface: Synthetic a") == 1
    assert result.output.count("host surface: Synthetic b") == 1


def test_scan_repo_with_no_declaration_reports_no_agent(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "declares no agent" in result.output + result.stderr


def test_scan_repo_reports_declared_agent_with_repo_shaped_target(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        '{"permissions": {"allow": ["Bash(git:*)"]}}', encoding="utf-8"
    )
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        scan_main, ["repo", "--target", str(tmp_path), "--include-posture", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["agents"] == [{
        "kind": "claude-code", "agent_id": None, "source": "declared",
        "coverage": "complete", "host_surface": "Claude Code"}]
    assert doc["target"]["rows"] == [
        {"label": "path", "value": str(tmp_path)}, {"label": "coverage", "value": "complete"}
    ]
    assert doc["stats"]["parse_failed"] == 0
    for finding in doc["findings"]:
        assert finding.get("agent") == {"kind": "claude-code", "agent_id": None}
    assert any(f["finding_type"] == "posture" for f in doc["findings"])


def test_scan_repo_downgrades_coverage_on_a_repo_parse_failure(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("not json", encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["repo", "--target", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["agents"][0]["coverage"] == "partial"
    assert doc["stats"]["parse_failed"] == 1


def test_scan_endpoint_still_prints_federation_warnings(tmp_path):
    # The shared federation fetch replaces one `_load_osv_with_overlays` call per
    # agent with one call per scan, but the existing `warning: ...` stderr line for
    # every returned federation warning must still appear — same pattern as
    # `test_endpoint_reports_a_matched_advisory` above, patched to return a warning.
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([], ["osv.dev: rate limited, retrying"], 0, {}),
    ):
        result = CliRunner().invoke(scan_main, ["endpoint", "--config-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "warning: osv.dev: rate limited, retrying" in result.output + result.stderr


def test_scan_repo_counts_a_shared_parse_failure_once_across_same_kind_agents(
    monkeypatch, tmp_path
):
    # Two declared agents of the same synthetic kind share one `scan_root`. One
    # malformed manifest in that root must contribute exactly one parse failure
    # scan-wide, not one per agent that happens to be discovered there.
    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("not json", encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["repo", "--target", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert len(doc["agents"]) == 2
    assert doc["stats"]["parse_failed"] == 1
    assert all(agent["coverage"] == "partial" for agent in doc["agents"])


def test_scan_repo_text_keeps_the_manifest_grouped_inventory_tree(tmp_path):
    # `scan repo` renders `render_repo_inventory_tree` — grouped by manifest,
    # rooted at the scanned path — not the endpoint composition tree. The agent
    # loop must select the renderer per composition source; using one renderer
    # for every card would silently change this output.
    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["repo", "--target", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert ".claude/skills/deploy/SKILL.md" in result.output


def test_scan_repo_stats_keep_the_manifest_unit_count(tmp_path):
    # The walk that produced `unit_count` moved into `_agent_scan_prep`; it must
    # still reach `ScanStats`, or the summary line loses its manifest count.
    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    result = CliRunner().invoke(
        scan_main, ["repo", "--target", str(tmp_path), "--format", "json"]
    )

    doc = json.loads(result.output)
    assert doc["stats"]["unit_label"] == "manifest"
    assert doc["stats"]["unit_count"] >= 1


def test_scan_endpoint_stats_keep_the_active_plugin_unit_label(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        scan_main, ["endpoint", "--config-dir", str(tmp_path), "--format", "json"]
    )

    doc = json.loads(result.output)
    assert doc["stats"]["unit_label"] == "active plugin"


def test_exposure_report_carries_agents(tmp_path):
    # An exposure report is a machine document (ADR-0047), so a zero-component
    # agent must appear in it too.
    root = tmp_path / ".claude"
    root.mkdir()

    result = CliRunner().invoke(
        scan_main,
        ["endpoint", "--config-dir", str(root), "--report", "exposure", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["agents"]) == 1
```

The exposure-report assertion above reads whatever key `_emit_triage_report` writes
the scan document under; match the existing report shape rather than assuming a
top-level `agents` if that command nests it.

```python
# tests/test_finding_output.py
def test_graph_for_prefers_the_finding_s_own_agent_graph():
    from tools.finding_output import graph_for

    a = Graph(nodes={"root/k/a": Node(key="root/k/a", kind="target", ref=None)})
    b = Graph(nodes={"root/k/b": Node(key="root/k/b", kind="target", ref=None)})
    finding = Finding(advisory_id="X", component=ComponentRef(ecosystem="npm", name="x"),
                      confidence="high", agent_kind="k", agent_id="b")

    assert graph_for(finding, a, {("k", "a"): a, ("k", "b"): b}) is b
    assert graph_for(finding, a, None) is a
    assert graph_for(finding, None, {}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scan.py -k "agents or one_card or no_declaration" -v`
Expected: FAIL — `KeyError: 'agents'`.

- [ ] **Step 3: Add the agent association to findings**

In `tools/matcher.py`, extend the dataclass and stamp it in `match`:

```python
@dataclass(frozen=True)
class Finding:
    advisory_id: str
    component: ComponentRef
    confidence: str
    reason: str = ""
    agent_kind: str | None = None
    agent_id: str | None = None


def match(
    refs: list[ComponentRef],
    advisories: list[dict[str, Any]],
    *,
    graph: Graph | None = None,
    agent_kind: str | None = None,
    agent_id: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for ref in refs:
        findings.extend(_match_one(ref, advisories))
    if agent_kind is None:
        return findings
    return [replace(f, agent_kind=agent_kind, agent_id=agent_id) for f in findings]
```

`replace` comes from `dataclasses`, which `matcher.py` does not import today — add it.

In `tools/finding_output.py`, add to `finding_to_output`:

```python
    entry["agent"] = {"kind": finding.agent_kind, "agent_id": finding.agent_id}
```

and in `tools/sarif.py`'s `_properties_for`, beside the existing `active_in` block:

```python
    agent = output.get("agent")
    if isinstance(agent, dict) and agent.get("kind"):
        props["agent_kind"] = agent["kind"]
        if agent.get("agent_id"):
            props["agent_id"] = agent["agent_id"]
```

`PostureFinding` and `ObservationFinding` gain the same two optional fields and the same
`agent` key in `posture_to_output` / `observation_to_output`, so every row in the flat
list answers "which agent". Both fields are stamped by the same producers that already
carry `agent_kind` today — `run_posture_rules` and `_collect_scanner_findings` — not
just `agent_kind` alone, since a many-per-place kind needs `agent_id` to tell rows from
`a` and `b` apart.

- [ ] **Step 4: Add `agents[]` and per-agent cards to the renderers**

```python
# tools/render.py
@dataclass(frozen=True)
class AgentSummary:
    kind: str | None
    agent_id: str | None
    source: str
    coverage: str
    host_surface: str

    def to_json(self) -> dict[str, str | None]:
        return {
            "kind": self.kind, "agent_id": self.agent_id, "source": self.source,
            "coverage": self.coverage, "host_surface": self.host_surface,
        }


@dataclass
class AgentCard:
    target: RenderTarget
    findings: list[Finding] = field(default_factory=list)
    posture_findings: list[PostureFinding] | None = None
    observations: list[ObservationFinding] = field(default_factory=list)
    inventory_tree: str | None = None
    next_actions: list[str] = field(default_factory=list)
    graph: Graph | None = None
```

Posture and observation rows belong to exactly one agent's card, the same as advisory
findings — the aggregate `posture_findings`/`observations` lists `render_json` and the
SARIF/JSON exporters consume stay scan-wide (attribution travels on each row via
`agent_kind`/`agent_id`), but the *text* renderer must not print the aggregate twice or
attribute it to nobody, so each card carries only its own slice.

`render_json` gains `agents: list[AgentSummary] | None = None` plus the
`graphs` keyword described under Step 5's tail (so each finding's lineage resolves
against its own agent's graph) and, after the `target` block:

```python
    if agents is not None:
        document["agents"] = [a.to_json() for a in agents]
```

`render_text` gains `cards: list[AgentCard] | None = None`. When supplied it renders the
Target/Inventory/Findings/Posture/Observations sections once per card — each card's own
`posture_findings`/`observations`, not the scan-wide lists, so a component flagged in
two agents produces two attributed rows instead of one duplicated pair — using that
card's own graph for attribution, and prints one scan-wide Summary/Next footer after all
cards using the aggregate `findings`/`posture_findings`/`observations` counts. `_render_text_card`
gains an `include_summary: bool = True` keyword; the per-card loop passes
`include_summary=False` and calls the existing summary-rendering tail once, standalone,
after the loop. With one card the output is unchanged from today outside the Target
block — which is exactly the diff the golden fixture in Task 10 permits, and everything
else in that diff is a regression.

- [ ] **Step 5: Restructure `scan endpoint` and `scan repo` around the agent loop**

`scan endpoint` and `scan repo` discover a different agent shape (an installed agent
has `config_root`/`project_root`; a declared agent has only `scan_root`, per Task 6's
`_discover_declared`), so each command prepares its own manifests, target row, and
next actions before the two commands share the same match/federate/render body:

```python
@dataclass(frozen=True)
class AgentScanPrep:
    manifests: list[tuple[Path, dict]]
    settings_manifests: list[tuple[Path, dict]]
    target_rows: list[tuple[str, str]]
    next_actions: list[str]
    unit_count: int
    unit_label: str
    parse_failed: int


def _agent_scan_prep(
    agent: AgentInstance,
    kind: AgentKind,
    refs: list[ComponentRef],
    *,
    include_gitignored: bool = False,
    repo_parse_cache: dict[tuple[Path, tuple], tuple[int, int]],
) -> AgentScanPrep:
    """Posture manifests, target rows, next actions, and the scanned-unit counts —
    everything that differs between an installed and a declared agent. Matching,
    federation, and card assembly are identical for both and stay in the shared loop
    below.

    `unit_count`/`unit_label` come back with `parse_failed` because the repo walk
    that produced `ScanStats(unit_count=n_found, unit_label="manifest")` for
    `scan repo` now happens in here: the pre-agent-loop command computed `n_found`
    once for the whole target, and the walk is per (root, kind) after this task.
    The installed branch reports the plugin count under `"active plugin"`, exactly
    as `scan endpoint` did.

    `repo_parse_cache` memoizes `parse_repo_grouped`'s `(n_found, n_failed)` per
    `(scan_root, manifest_patterns)`: a many-per-place kind can return several
    declared agents over the *same* root, and walking the repo again for each one
    would multiply one malformed manifest into as many failures as there are
    agents. Keying on `manifest_patterns` (not just the root) means two
    *different* kinds declared over the same root each get their own count
    instead of sharing — or inflating — one kind's walk. The cache is created
    once per command invocation, so the walk still runs once per distinct
    `(root, patterns)` pair."""
    if agent.source == "installed":
        # Read through the *kind's own* installed posture surface (Task 3), not a
        # Claude-Code-shaped collector called unconditionally for every installed
        # agent — a second installed kind must not be scanned with Claude Code's
        # endpoint semantics. `None` (a kind with no filesystem-shaped installed
        # posture surface) yields nothing rather than falling back to Claude Code's.
        mcp_collector, settings_collector = kind.installed_posture_collectors or (
            lambda *_a, **_k: [],
            lambda *_a, **_k: [],
        )
        manifests = mcp_collector(agent.config_root, agent.project_root, refs)
        settings_manifests = settings_collector(agent.config_root, agent.project_root)
        rows = [
            ("config", str(agent.config_root)),
            (
                "project",
                str(agent.project_root) if agent.project_root is not None else "not included",
            ),
        ]
        return AgentScanPrep(
            manifests=manifests,
            settings_manifests=settings_manifests,
            target_rows=rows,
            next_actions=_next_actions_for(agent),
            unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
            unit_label="active plugin",
            parse_failed=0,
        )

    # declared: no install state to disambiguate — walk the scan root directly,
    # exactly as `scan repo` does today, so parse failures still count.
    assert agent.scan_root is not None
    # Read through the *kind's own* posture surface (Task 3), not a
    # Claude-Code-shaped collector called unconditionally for every declared
    # agent — a repo declaring two kinds must not have one kind's manifests
    # attributed to the other's card. `None` (a kind with no filesystem-shaped
    # posture surface) yields nothing rather than falling back to Claude Code's.
    mcp_collector, settings_collector = kind.posture_manifest_collectors or (
        lambda *_a, **_k: [],
        lambda *_a, **_k: [],
    )
    manifests = mcp_collector([agent.scan_root], include_gitignored=include_gitignored)
    settings_manifests = settings_collector(
        [agent.scan_root], include_gitignored=include_gitignored
    )
    cache_key = (agent.scan_root, kind.manifest_patterns)
    if cache_key not in repo_parse_cache:
        # kind.manifest_patterns, not the module-level REGISTRY: a repo declaring
        # two different kinds must not have one kind's manifests count toward the
        # other's evidence gaps (spec: "the flat manifest registry splits per
        # kind, reached through a surface").
        parse_groups, n_found = parse_repo_grouped(
            agent.scan_root, include_gitignored=include_gitignored, registry=kind.manifest_patterns
        )
        repo_parse_cache[cache_key] = (n_found, n_found - len(parse_groups))
    n_found, n_failed = repo_parse_cache[cache_key]
    return AgentScanPrep(
        manifests=manifests,
        settings_manifests=settings_manifests,
        target_rows=[("path", str(agent.scan_root))],
        next_actions=[
            f"emit Agent BOM: openaca bom repo --target {agent.scan_root} "
            "--output openaca-bom.json",
        ],
        unit_count=n_found,
        unit_label="manifest",
        parse_failed=n_failed,
    )
```

**The inventory tree stays per source too.** `scan repo` renders a
*manifest-grouped* tree today — `render_repo_inventory_tree(target,
_group_refs_for_repo_tree(all_refs), findings, ...)` (`tools/scan.py:797`) — while
`scan endpoint` renders the composition tree `render_inventory_tree(refs, findings,
...)`. Building every card with `render_inventory_tree` would silently change
`scan repo`'s text output, which is not on the permitted-diff list. Selection
belongs beside the card, not in `_agent_scan_prep`, because the grouping is
derived from the agent's own *unfiltered* graph refs rather than from the walk:

```python
def _agent_inventory_tree(
    agent: AgentInstance,
    all_refs: list[ComponentRef],
    refs: list[ComponentRef],
    findings: list[Finding],
    *,
    use_color: bool,
    use_unicode: bool,
    graph: Graph | None,
) -> str | None:
    """Same two renderers, same inputs, as before the agent loop — only the
    selection moved. Declared keeps `scan repo`'s manifest-grouped tree
    (grouped from the unfiltered graph refs, exactly as today); installed keeps
    the endpoint composition tree."""
    if agent.source == "declared":
        assert agent.scan_root is not None
        grouped = _group_refs_for_repo_tree(all_refs)
        if not grouped:
            return None
        return render_repo_inventory_tree(
            agent.scan_root, grouped, findings,
            use_color=use_color, use_unicode=use_unicode, graph=graph,
        )
    return render_inventory_tree(
        refs, findings, use_color=use_color, use_unicode=use_unicode, graph=graph
    )
```

`built` therefore carries the unfiltered `all_refs` alongside the filtered `refs`
(`(agent, graph, all_refs, refs, warnings)`), since `scan repo`'s tree grouping
reads the unfiltered set today and dropping software-dependency refs from it would
be a second silent output change.

`scan endpoint` discovers and builds exactly as before Task 8. The single federation
pass keeps printing every `fed_warnings` entry to stderr immediately after
`_stamp_source`, exactly as each command does today (`tools/scan.py:767`,
`tools/scan.py:989`, `tools/scan.py:1228`) — moving to one shared fetch changes how many
times that loop runs (once per scan instead of once per command invocation, unchanged
since each invocation already scans one target), not whether it runs:

```python
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=config_dir, project_root=project)
    )
    if not agents:
        click.echo("no installed agent found", err=True)
        return

    built = []
    for agent in agents:
        if not is_text or verbose:
            # Today's one-line scan-scope preamble (`tools/scan.py:967`), now once
            # per discovered agent — identical output for the single-agent case.
            click.echo(
                f"detected config_dir={agent.config_root}, project={project_note} "
                "(mode=endpoint)",
                err=True,
            )
        warnings: list[str] = []
        graph = build_agent_graph(agent, warnings=warnings)
        agent_all_refs = _refs_from_graph(graph)
        refs = build_agent_bom(
            _filter_agent_scope_refs(agent_all_refs),
            target=str(agent.config_root),
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings)
            ),
        ).component_refs()
        built.append((agent, graph, agent_all_refs, refs, warnings))

    # One federation pass for the whole scan; matching stays per agent because
    # attribution comes from that agent's own graph.
    union_refs = [ref for _, _, _, refs, _ in built for ref in refs]
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        union_refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)

    findings: list[Finding] = []
    posture_findings: list[PostureFinding] = []
    observations: list[ObservationFinding] = []
    cards: list[AgentCard] = []
    summaries: list[AgentSummary] = []
    graphs: dict[tuple[str | None, str | None], Graph] = {}
    total_parse_failed = 0
    total_unit_count = 0
    unit_label = ""
    repo_parse_cache: dict[tuple[Path, tuple], tuple[int, int]] = {}
    counted_repo_roots: set[tuple[Path, tuple]] = set()
    for agent, graph, agent_all_refs, refs, warnings in built:
        graphs[(agent.kind_id, agent.agent_id)] = graph
        kind = kind_for(agent.kind_id)
        agent_findings = match(
            refs, corpus, graph=graph, agent_kind=agent.kind_id, agent_id=agent.agent_id
        )
        findings.extend(agent_findings)

        agent_observations, scanner_posture = _collect_scanner_findings(
            refs,
            external_scanners=external_scanners,
            skillspector_progress=_skillspector_progress_reporter(output_format),
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
        )
        observations.extend(agent_observations)

        prep = _agent_scan_prep(
            agent, kind, refs,
            include_gitignored=include_gitignored, repo_parse_cache=repo_parse_cache,
        )
        n_failed = prep.parse_failed
        unit_label = prep.unit_label
        # `n_failed` is per-(root, kind) (via `repo_parse_cache`), not per-agent —
        # several same-kind declared agents can share a root, so the scan-wide
        # total only takes that pair's count once, while each agent's own
        # coverage below still uses the full (shared) count for its root. Keying
        # on the kind's `manifest_patterns` too (not just the root) means two
        # *different* kinds sharing a root each contribute their own count
        # instead of one clobbering the other.
        repo_root_key = None if agent.scan_root is None else (agent.scan_root, kind.manifest_patterns)
        # `unit_count` is deduplicated on the same key for the same reason: two
        # declared agents over one root scanned one manifest set, not two.
        if repo_root_key is None or repo_root_key not in counted_repo_roots:
            total_parse_failed += n_failed
            total_unit_count += prep.unit_count
            if repo_root_key is not None:
                counted_repo_roots.add(repo_root_key)

        agent_posture: list[PostureFinding] | None = None
        if include_posture:
            agent_posture = list(scanner_posture) + run_posture_rules(
                refs,
                prep.manifests,
                prep.settings_manifests,
                allowed_rules=kind.posture_rules,
                agent_kind=agent.kind_id,
                agent_id=agent.agent_id,
            )
            posture_findings.extend(agent_posture)

        coverage = resolve_coverage(
            agent.coverage_baseline, evidence_gaps=len(warnings) + n_failed
        )
        summaries.append(
            AgentSummary(
                kind=agent.kind_id,
                agent_id=agent.agent_id,
                source=agent.source,
                coverage=coverage,
                host_surface=agent.display_name,
            )
        )
        cards.append(
            AgentCard(
                target=RenderTarget(
                    host_surface=agent.display_name,
                    rows=[*prep.target_rows, ("coverage", coverage)],
                ),
                findings=agent_findings,
                posture_findings=agent_posture,
                observations=agent_observations,
                inventory_tree=_agent_inventory_tree(
                    agent,
                    agent_all_refs,
                    refs,
                    agent_findings,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=graph,
                )
                if is_text
                else None,
                next_actions=prep.next_actions,
                graph=graph,
            )
        )
```

`scan repo` discovers with `DiscoveryContext(source="declared", scan_root=target,
include_gitignored=include_gitignored)` in place of the endpoint context above, echoes
`f"{target} declares no agent"` on stderr instead of "no installed agent found" when
`discover_agents` returns `[]` (this is `test_scan_repo_with_no_declaration_reports_no_agent`,
already passing today), and otherwise runs the identical `built`/federation/per-agent
loop — `_agent_scan_prep` is exactly the seam that keeps the loop body common while
each command supplies its own manifests, target row, and next actions.

`_agent_scan_prep`'s declared branch sources its parse-failure count from
`parse_repo_grouped(agent.scan_root, ..., registry=kind.manifest_patterns)` the same
way `bom repo` already does for its own `evidence_gaps` (Task 7 Step 4:
`evidence_gaps=n_found - len(parse_groups)`), so `ScanStats.parse_failed` and
`resolve_coverage`'s evidence-gap count stay correct for `scan repo` after this
rewrite. Because a many-per-place kind can return several declared agents over the
same `scan_root`, `repo_parse_cache` and `counted_repo_roots` key on
`(scan_root, kind.manifest_patterns)` rather than the root alone: agents that share
both a root and a kind walk it exactly once and contribute the resulting failures to
the scan-wide total once, while two *different* kinds declared over the same root
each get their own walk and their own contribution instead of one clobbering the
other. `total_parse_failed` then replaces the flat `n_failed` the pre-agent-loop
`repo` command computed once for the whole target, and feeds
`ScanStats(parse_failed=total_parse_failed)` below.

`_agent_scan_prep` also takes the resolved `kind` (already computed in the loop above
for the posture-rule allowlist) and reads its declared-branch manifests through
`kind.posture_manifest_collectors`, and its installed-branch manifests through
`kind.installed_posture_collectors`, rather than calling `collect_mcp_manifests`/
`collect_settings_manifests`/`collect_endpoint_mcp_manifests`/
`collect_endpoint_settings_manifests` directly — those module-level imports move from
`tools/scan.py` into `tools/agent_kinds/claude_code.py`, which is now their only
caller. `scan.py` no longer imports them.

`_collect_scanner_findings` gains `agent_kind` and `agent_id` keywords, forwards
`agent_kind` to SkillSpector (Task 9), and stamps both onto every observation and
scanner-sourced posture finding it returns before handing them back to the caller:

```python
def _collect_scanner_findings(
    refs: list[ComponentRef],
    *,
    external_scanners: tuple[str, ...],
    skillspector_progress: SkillSpectorProgressCallback | None = None,
    agent_kind: str | None = None,
    agent_id: str | None = None,
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    ...
    if agent_kind is not None:
        observations = [replace(o, agent_kind=agent_kind, agent_id=agent_id) for o in observations]
        posture_findings = [
            replace(p, agent_kind=agent_kind, agent_id=agent_id) for p in posture_findings
        ]
    return observations, posture_findings
```

`_next_actions_for(agent)` (used by `_agent_scan_prep`'s installed branch) returns
today's endpoint list with `--config-dir` filled in from the agent, so the "include
project-local config" and "emit Agent BOM" lines stay; the declared branch returns its
own `bom repo` next action instead, since none of the endpoint-only lines apply to a
declared agent.

`RenderTarget(host_surface=agent.display_name, rows=[...])` replaces the hardcoded
`"Claude Code"` at `tools/scan.py:1015` for `scan endpoint`, and the hardcoded
`"repository"`/`("path", ...)` card at `tools/scan.py:806` for `scan repo`. For Claude
Code the value is the same literal, so `target.host_surface` in JSON output is
unchanged — which is what the spec's compatibility table requires.

**After the loop — one scan-wide tail, threaded with `agents` and `graphs`.**
`ScanStats` becomes
`ScanStats(unit_count=total_unit_count, unit_label=unit_label,
component_count=len(union_refs), parse_failed=total_parse_failed,
sources=_collect_corpus_sources(corpus))`. The two commands still count different
units, and each still counts them the way it did before this task — plugin count
under `"active plugin"` for `scan endpoint`, manifest count under `"manifest"` for
`scan repo` — but both values now come out of `_agent_scan_prep` rather than being
computed inline, because the walk that produced them moved into the agent loop.
With one agent every number is identical to today's.

Four scan-wide tail concerns, none of which the pre-agent-loop code had to make a
choice about:

- **`_emit` and `_scan_json_document` take `cards`, `agents=summaries`, and
  `graphs`.** `_emit`'s single `target`/`inventory_tree`/`next_actions` triple is
  replaced by `cards` (it only ever forwarded them to `render_text`), and the
  scan-wide `graph` argument stays as the fallback for `graphs`.
- **`report_kind == "exposure"` goes through the same threading.** Both commands
  build the exposure report from `_scan_json_document(...)`, so it takes
  `agents=summaries` and `graphs` too, and `target=cards[0].target` in place of the
  removed `card_target`. Per ADR-0047 an exposure report is a machine document, so
  it carries `agents[]` for the same reason `--format json` does — a zero-component
  agent must not vanish from it.
- **`to_sarif` takes `graphs`.** One SARIF file for the scan (unchanged), but each
  result's lineage resolves against its own agent's graph.
- **Verbose and `_stderr_summary` stay scan-wide, sourced from the aggregates.**
  `scan repo`'s verbose "scanned N manifest(s), M component(s)" line and its
  no-manifests / none-parsed branches read `total_unit_count`,
  `len(union_refs)`, and `total_parse_failed`; the machine-format verbose
  inventory tree is emitted once per card via `_agent_inventory_tree`, so a
  single-agent `--format json -v` run is unchanged. The "found N manifest file(s)
  but none parsed successfully" branch keys on `total_unit_count` and
  `total_parse_failed` rather than the removed per-target `parse_groups`.

`tools/finding_output.py` gains the resolver both machine renderers use:

```python
def graph_for(
    finding: object,
    graph: Graph | None = None,
    graphs: Mapping[tuple[str | None, str | None], Graph] | None = None,
) -> Graph | None:
    """A finding's lineage comes from its own agent's graph.

    `graphs` is empty or absent for `scan bom` on a stored document and for any
    caller that has one graph, in which case this is exactly today's behaviour.
    """
    if graphs:
        key = (getattr(finding, "agent_kind", None), getattr(finding, "agent_id", None))
        found = graphs.get(key)
        if found is not None:
            return found
    return graph
```

`render_json`, `render_github`, and `to_sarif` call it wherever they currently pass
their scan-wide `graph` into `finding_to_output` / `posture_to_output` /
`observation_to_output`. `render_text` needs none of this — `AgentCard.graph`
already carries the right graph per card.

- [ ] **Step 6: Add NDJSON input to `scan bom`**

A user who runs `openaca bom endpoint > bom.json` now has an NDJSON file, so the
consumer of that file must read it.

```python
# tools/scan.py
def _load_bom_documents(path: Path, raw: str) -> list[dict]:
    """One JSON object, or NDJSON with one document per line."""
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        documents = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise click.ClickException(f"{path}:{number}: invalid JSON — {exc}") from exc
            if not isinstance(parsed, dict):
                raise click.ClickException(
                    f"{path}:{number}: BOM must be a JSON object, got "
                    f"{type(parsed).__name__}"
                )
            documents.append(parsed)
        if not documents:
            raise click.ClickException(f"{path}: no BOM documents found")
        return documents
    if not isinstance(doc, dict):
        raise click.ClickException(
            f"{path}: BOM must be a JSON object, got {type(doc).__name__}"
        )
    return [doc]
```

`scan bom` builds one `(AgentSummary, AgentCard)` per document, reusing each
document's own `_is_graph_backed_bom`/`agent_info_from_cyclonedx` reads (Task 5 Step
5) instead of the discovery-built `agent`/`graph`/`refs` tuples Step 5 above produces
for `endpoint`/`repo` — a stored document has no `AgentInstance` to discover, only
whatever `agent_info_from_cyclonedx` can read back off it:

```python
    for doc in _load_bom_documents(input_path, raw):
        agent_info = agent_info_from_cyclonedx(doc)
        if _is_graph_backed_bom(doc):
            graph = graph_from_cyclonedx(doc)
            refs = build_agent_bom(
                _filter_agent_scope_refs(_refs_from_graph(graph)),
                target_type="bom", target=str(input_path), graph=graph,
            ).component_refs()
        else:
            graph = None
            refs = build_agent_bom(
                _filter_agent_scope_refs(component_refs_from_cyclonedx(doc)),
                target_type="bom", target=str(input_path),
            ).component_refs()
        docs_built.append((doc, agent_info, graph, refs))
```

`docs_built` carries the source `doc` alongside each entry — the second loop below reads
legacy target metadata per document, and a bare loop variable from the first loop would
not survive past it (each entry needs its own document, not whichever one the first loop
last saw).

A stored `0.4` document (or any pre-agent-metadata flat BOM) has no `metadata.component`
carrying `openaca:agent_kind`, so `agent_info_from_cyclonedx(doc)` returns `None` for it
— that is precisely the compatibility signal Task 5 Step 5 already established for the
single-document card. The per-document loop below carries that `None` through to a
legacy `AgentSummary`/`AgentCard` rather than inventing agent metadata that was never
in the document (which would misrepresent the BOM) or rejecting the document (which
would break the compatibility the spec requires for either stored version):

```python
    for doc, agent_info, graph, refs in docs_built:
        agent_findings = match(
            refs, corpus, graph=graph,
            agent_kind=agent_info.kind if agent_info else None,
            agent_id=agent_info.agent_id if agent_info else None,
        )
        findings.extend(agent_findings)
        if agent_info is not None:
            rows = [("agent", f"{agent_info.kind} ({agent_info.source or 'unknown'})"),
                    ("coverage", agent_info.coverage or "unknown")]
            summaries.append(AgentSummary(
                kind=agent_info.kind, agent_id=agent_info.agent_id,
                source=agent_info.source or "bom",
                coverage=agent_info.coverage or "unknown",
                host_surface=agent_info.name or agent_info.kind,
            ))
        else:
            target_type, target = target_info_from_cyclonedx(doc)
            orig = f"{target_type} {target}".strip() if target_type and target else "unknown"
            rows = [("original target", orig)]
            summaries.append(AgentSummary(
                kind=None, agent_id=None, source="bom",
                coverage="unknown", host_surface="stored BOM",
            ))
        cards.append(AgentCard(
            target=RenderTarget(host_surface=summaries[-1].host_surface, rows=rows),
            findings=agent_findings, graph=graph,
        ))
```

`posture_findings`/`observations` stay empty for every `scan bom` card — `scan bom`
never runs posture (the command already rejects `--include-posture`) and never runs
the observation scanners, on either document shape, so there is nothing new to carry
per agent here.

Tests:

```python
def test_scan_bom_reads_ndjson_from_bom_endpoint(monkeypatch, tmp_path):
    register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])
    ndjson = CliRunner().invoke(bom_main, ["endpoint", "--config-dir", str(tmp_path)]).output
    path = tmp_path / "boms.ndjson"
    path.write_text(ndjson, encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["bom", "--input", str(path), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["agents"]) == 2


def test_scan_bom_keeps_each_legacy_document_s_own_target(tmp_path):
    # Two stored 0.4 documents with distinct `openaca:target` values — each card
    # must report the target embedded in *its own* document, not whichever
    # document the loading loop last read.
    def _legacy_doc(target: str) -> dict:
        return {
            "bomFormat": "CycloneDX", "specVersion": "1.5",
            "metadata": {"properties": [
                {"name": "openaca:target_type", "value": "endpoint"},
                {"name": "openaca:target", "value": target},
            ]},
            "components": [],
        }

    path = tmp_path / "boms.ndjson"
    path.write_text(
        json.dumps(_legacy_doc("/one")) + "\n" + json.dumps(_legacy_doc("/two")) + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(scan_main, ["bom", "--input", str(path)])

    assert result.exit_code == 0, result.output
    assert "endpoint /one" in result.output
    assert "endpoint /two" in result.output
    # The bug this guards against renders both cards from the *last* document
    # read, so "endpoint /one" would be missing entirely.


def test_scan_bom_rejects_a_non_object_ndjson_line(tmp_path):
    path = tmp_path / "boms.ndjson"
    path.write_text('{"bomFormat": "CycloneDX", "components": []}\n[1, 2]\n', encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["bom", "--input", str(path), "--format", "json"])

    assert result.exit_code != 0
    assert "boms.ndjson:2" in result.output + str(result.exception)


def test_scan_bom_renders_a_stored_0_4_document_without_agent_metadata(tmp_path):
    # A pre-agent-metadata flat CycloneDX BOM: no metadata.component at all,
    # matching what `openaca bom endpoint` emitted before this feature.
    doc = {
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "components": [{
            "type": "library", "bom-ref": "npm:@x/gh@1.0.0", "name": "@x/gh",
            "version": "1.0.0", "purl": "pkg:npm/%40x/gh@1.0.0",
            "properties": [{"name": "openaca:scope", "value": "software-dependency"}],
        }],
    }
    path = tmp_path / "legacy.cdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = CliRunner().invoke(scan_main, ["bom", "--input", str(path), "--format", "json"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["agents"] == [{
        "kind": None, "agent_id": None, "source": "bom",
        "coverage": "unknown", "host_surface": "stored BOM",
    }]
```

- [ ] **Step 7: Add the posture-rule allowlist**

Export the set of rule ids a kind's allowlist is checked against, so a typo can't
silently disable an intended rule:

```python
# tools/posture/__init__.py
KNOWN_RULE_IDS: frozenset[str] = frozenset({
    mutable_install.RULE_ID,
    insecure_transport.RULE_ID,
    mcp_auto_approve.RULE_ID,
    api_endpoint_override.RULE_ID,
    skill_capability.RULE_ID,
})
```

```python
def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
    settings_manifests: list[tuple[Path, dict]] | None = None,
    *,
    allowed_rules: frozenset[str] | None = None,
    agent_kind: str | None = None,
    agent_id: str | None = None,
) -> list[PostureFinding]:
    """Rule *reach* is structural — an agent's graph holds only its own manifests —
    but *applicability* is declared, because a settings key can mean something
    different, or nothing, in another runtime."""
    ...
    findings = [f for f in findings if allowed_rules is None or f.rule_id in allowed_rules]
    findings = [replace(f, agent_kind=agent_kind, agent_id=agent_id) for f in findings]
    return [_attach_bom_ref(finding, refs) for finding in findings]
```

Test:

```python
def test_posture_rules_respect_a_kind_allowlist(tmp_path):
    refs = [ComponentRef(ecosystem="generic-skill", name="s",
                         extra={"component_type": "skill", "install_source": "local-path"})]

    all_rules = run_posture_rules(refs, [])
    narrowed = run_posture_rules(refs, [], allowed_rules=frozenset())

    assert all_rules != []
    assert narrowed == []


def test_a_kind_cannot_allowlist_an_unknown_posture_rule():
    with pytest.raises(ValueError, match="unknown posture rule"):
        AgentKind(
            id="synthetic", display_name="Synthetic", cardinality="singleton",
            root_label="synthetic", coverage_baseline={"installed": "complete", "declared": "complete"},
            discover=lambda ctx: [], compose=lambda agent, **_: Graph(nodes={}),
            posture_rules=frozenset({"not-a-real-rule-id"}),
        )


def test_posture_rules_stamp_agent_id_for_same_kind_agents():
    """Two agents of the same kind must produce distinguishable posture rows."""
    refs = [ComponentRef(ecosystem="generic-skill", name="s",
                         extra={"component_type": "skill", "install_source": "local-path"})]

    a = run_posture_rules(refs, [], agent_kind="synthetic", agent_id="a")
    b = run_posture_rules(refs, [], agent_kind="synthetic", agent_id="b")

    assert [f.agent_id for f in a] == ["a"] * len(a) and a
    assert [f.agent_id for f in b] == ["b"] * len(b) and b


def test_agent_scan_prep_reads_only_its_own_kind_s_posture_collectors(tmp_path):
    """A repo declaring two *different* kinds must not have one kind's posture
    manifests attributed to the other's card. `_agent_scan_prep`'s declared
    branch routes through `kind.posture_manifest_collectors` (Task 3) instead
    of calling one Claude-Code-shaped collector pair for every kind."""
    from tools.scan import _agent_scan_prep

    marker_a = (tmp_path / "a.json", {"marker": "a"})
    marker_b = (tmp_path / "b.json", {"marker": "b"})

    def kind_with(mcp_result, settings_result, kind_id):
        return AgentKind(
            id=kind_id, display_name=kind_id, cardinality="singleton",
            root_label=kind_id, coverage_baseline={"installed": "partial", "declared": "partial"},
            discover=lambda ctx: [], compose=lambda agent, **_: Graph(nodes={}),
            posture_manifest_collectors=(
                lambda *_a, **_k: mcp_result, lambda *_a, **_k: settings_result,
            ),
        )

    kind_a, kind_b = kind_with([marker_a], [], "kind-a"), kind_with([], [marker_b], "kind-b")
    agent_a = AgentInstance(kind_id="kind-a", display_name="Kind A", source="declared",
                             root_label="kind-a", coverage_baseline="partial", scan_root=tmp_path)
    agent_b = AgentInstance(kind_id="kind-b", display_name="Kind B", source="declared",
                             root_label="kind-b", coverage_baseline="partial", scan_root=tmp_path)

    prep_a = _agent_scan_prep(agent_a, kind_a, [], repo_parse_cache={})
    prep_b = _agent_scan_prep(agent_b, kind_b, [], repo_parse_cache={})

    assert prep_a.manifests == [marker_a] and prep_a.settings_manifests == []
    assert prep_b.manifests == [] and prep_b.settings_manifests == [marker_b]


def test_agent_scan_prep_reads_only_its_own_kind_s_installed_posture_collectors(tmp_path):
    """The installed-branch counterpart to the test above: a second installed
    kind must not be scanned with Claude Code's endpoint-shaped collectors.
    `_agent_scan_prep`'s installed branch routes through
    `kind.installed_posture_collectors` instead of calling
    `collect_endpoint_mcp_manifests`/`collect_endpoint_settings_manifests`
    unconditionally for every installed agent."""
    from tools.scan import _agent_scan_prep

    marker_a = (tmp_path / "a.json", {"marker": "a"})
    marker_b = (tmp_path / "b.json", {"marker": "b"})

    def kind_with(mcp_result, settings_result, kind_id):
        return AgentKind(
            id=kind_id, display_name=kind_id, cardinality="singleton",
            root_label=kind_id, coverage_baseline={"installed": "partial", "declared": "partial"},
            discover=lambda ctx: [], compose=lambda agent, **_: Graph(nodes={}),
            installed_posture_collectors=(
                lambda *_a, **_k: mcp_result, lambda *_a, **_k: settings_result,
            ),
        )

    kind_a, kind_b = kind_with([marker_a], [], "kind-a"), kind_with([], [marker_b], "kind-b")
    agent_a = AgentInstance(kind_id="kind-a", display_name="Kind A", source="installed",
                             root_label="kind-a", coverage_baseline="partial", config_root=tmp_path)
    agent_b = AgentInstance(kind_id="kind-b", display_name="Kind B", source="installed",
                             root_label="kind-b", coverage_baseline="partial", config_root=tmp_path)

    prep_a = _agent_scan_prep(agent_a, kind_a, [], repo_parse_cache={})
    prep_b = _agent_scan_prep(agent_b, kind_b, [], repo_parse_cache={})

    assert prep_a.manifests == [marker_a] and prep_a.settings_manifests == []
    assert prep_b.manifests == [] and prep_b.settings_manifests == [marker_b]


def test_agent_scan_prep_counts_each_kind_s_own_manifest_registry_separately(tmp_path):
    """The declared branch's parse-failure count must key on the kind's own
    `manifest_patterns`, not the global registry — two different kinds
    declared over the same root must not have one kind's manifest count
    (or parse failures) attributed to the other."""
    from tools.parsers.mcp_json import parse as parse_mcp
    from tools.parsers.package_json import parse as parse_package
    from tools.scan import _agent_scan_prep

    (tmp_path / ".mcp.json").write_text("not valid json", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")

    def kind_with(patterns, kind_id):
        return AgentKind(
            id=kind_id, display_name=kind_id, cardinality="singleton",
            root_label=kind_id, coverage_baseline={"installed": "partial", "declared": "partial"},
            discover=lambda ctx: [], compose=lambda agent, **_: Graph(nodes={}),
            manifest_patterns=patterns,
        )

    # kind-a's only manifest pattern is the malformed .mcp.json; kind-b's is the
    # well-formed package.json.
    kind_a = kind_with(((".mcp.json", parse_mcp),), "kind-a")
    kind_b = kind_with((("package.json", parse_package),), "kind-b")
    agent_a = AgentInstance(kind_id="kind-a", display_name="Kind A", source="declared",
                             root_label="kind-a", coverage_baseline="partial", scan_root=tmp_path)
    agent_b = AgentInstance(kind_id="kind-b", display_name="Kind B", source="declared",
                             root_label="kind-b", coverage_baseline="partial", scan_root=tmp_path)

    cache: dict = {}
    prep_a = _agent_scan_prep(agent_a, kind_a, [], repo_parse_cache=cache)
    prep_b = _agent_scan_prep(agent_b, kind_b, [], repo_parse_cache=cache)

    assert prep_a.parse_failed == 1  # kind-a's own malformed .mcp.json
    assert prep_a.unit_count == 1 and prep_a.unit_label == "manifest"
    # kind-b's package.json parses fine; kind-a's failure doesn't leak in.
    assert prep_b.parse_failed == 0
    assert prep_b.unit_count == 1
```

`tests/test_scan.py` gains the same-kind proof at the scan-pipeline level: with
`register_synthetic_kind(monkeypatch, agent_ids=["a", "b"])` and skill refs seeded on
both agents' config roots (via a `monkeypatch` on `collect_endpoint_mcp_manifests`/the
skill-audit collector, since the synthetic kind's own `compose` returns an empty graph),
assert that `doc["posture_findings"]`/`doc["observations"]` in `--format json` output
each carry `{"kind": "synthetic", "agent_id": "a"}` and `{"kind": "synthetic",
"agent_id": "b"}` respectively — not just `--format json`'s existing per-advisory-finding
assertion. `tests/test_render.py` gains a text-mode counterpart: build two `AgentCard`s
by hand, one with a posture finding and one with an observation finding, call
`render_text(..., cards=[card_a, card_b])`, and assert each finding's text renders
exactly once, under its own agent's Target block, not under the other's and not twice.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scan.py tests/test_render.py tests/test_sarif.py tests/test_finding_output.py tests/test_posture_integration.py -v`
Expected: PASS. Then the full gate.

- [ ] **Step 9: Commit**

```bash
git add tools/scan.py tools/render.py tools/matcher.py tools/finding_output.py \
        tools/sarif.py tools/posture tests
git commit -m "feat(scan): scan per agent; carry the agent through findings and output"
```

---

## Task 9: Parsers stop knowing the runtime; `active_in` comes from the agent

**Files:**
- Create: `tools/active_in.py`
- Modify: `tools/finding_output.py`, `tools/posture/rules/mutable_install.py`,
  `tools/posture/rules/skill_capability.py`, `tools/observations/skillspector.py`
- Modify: `tools/parsers/mcp_json.py`, `tools/parsers/claude_install.py`,
  `tools/graph_build.py:445`
- Test: `tests/test_finding_output.py`, `tests/test_posture_mutable_install.py`,
  `tests/test_posture_skill_capability.py`, `tests/test_skillspector_observations.py`,
  `tests/test_parsers/`

**Interfaces:**
- Consumes: `Finding.agent_kind` and `PostureFinding.agent_kind` from Task 8.
- Produces: `tools.active_in.active_in(ref: ComponentRef, *,
  agent_kind: str | None = None) -> list[str]`.

`runtime_hosts` had exactly four readers, all of them the "active in" field. They
re-source from the agent through one helper so the field has a single definition, and the
stored-`0.4` fallback keeps `scan bom` correct on old documents.

- [ ] **Step 1: Write the failing helper tests**

```python
# tests/test_finding_output.py
from tools.active_in import active_in


def test_active_in_prefers_the_scanning_agent():
    ref = ComponentRef(ecosystem="npm", name="x", extra={"runtime_hosts": ["stale"]})

    assert active_in(ref, agent_kind="claude-code") == ["claude-code"]


def test_active_in_falls_back_to_a_stored_0_4_document():
    ref = ComponentRef(ecosystem="npm", name="x", extra={"runtime_hosts": ["claude-code"]})

    assert active_in(ref) == ["claude-code"]


def test_active_in_is_empty_when_nothing_says():
    assert active_in(ComponentRef(ecosystem="npm", name="x")) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_finding_output.py -k active_in -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.active_in'`.

- [ ] **Step 3: Write the helper**

```python
# tools/active_in.py
"""Which agents a component is active in — one definition, four readers.

The agent doing the scanning is the answer (ADR-0044: the BOM's subject carries
what `openaca:runtime_hosts` used to). The stored-property fallback exists only
for `0.4` documents read back off disk, whose emitter has been removed.
"""

from __future__ import annotations

from tools.component_ref import ComponentRef


def active_in(ref: ComponentRef, *, agent_kind: str | None = None) -> list[str]:
    if agent_kind:
        return [agent_kind]
    stored = (ref.extra or {}).get("runtime_hosts")
    if isinstance(stored, list):
        return [value for value in stored if isinstance(value, str)]
    return []
```

- [ ] **Step 4: Re-source the four readers**

- `tools/finding_output.py:108` — delete `_active_in_for` and call
  `active_in(ref, agent_kind=finding.agent_kind)`.
- `tools/posture/rules/mutable_install.py:104` and
  `tools/posture/rules/skill_capability.py:96` — replace each local
  `runtime_hosts` block with `active_in(ref, agent_kind=agent_kind)`, threading
  `agent_kind` from `run_posture_rules`.
- `tools/observations/skillspector.py:501` — same replacement, threading `agent_kind`
  from `_collect_scanner_findings`.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_finding_output.py tests/test_posture_mutable_install.py tests/test_posture_skill_capability.py tests/test_skillspector_observations.py -v`
Expected: PASS. Then the full gate.

- [ ] **Step 6: Stop the parsers writing `runtime_hosts` at all**

The spec's scanner change *"parsers stop knowing the runtime; the agent owns that
fact"*. With the emitter gone (Task 5) and every reader re-sourced (Step 4), the field
is dead weight that still hard-codes `"claude-code"` inside three parsers.

Remove the `runtime_hosts` parameter from `mcp_json.parse_mcp_servers`,
`_mcp_ref_extra`, and the remote-server helper; delete the three call sites at
`tools/parsers/mcp_json.py:794`, `:802`, `:815`; delete
`extra["runtime_hosts"]` at `tools/parsers/claude_install.py:200` and `:569`; delete
`"runtime_hosts": ["claude-code"]` at `tools/graph_build.py:445`.

**One deliberate semantic change to record, not hide.** Today a VS Code `servers`
block, or a bare flat server map, is parsed with `runtime_hosts=[]` — "host cannot be
inferred from this file shape" — so it renders `active_in: []`. Under the agent model
the file is in the scanning agent's composition, exactly as the spec's *one kind may
read another's files* rule requires, so it renders `active_in: ["claude-code"]`. That
is a stronger and correct claim: the agent, not the file shape, decides.

```python
# tests/test_parsers/test_mcp_json.py
def test_vs_code_servers_block_no_longer_carries_runtime_hosts(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"servers": {"gh": {"command": "npx", "args": ["@x/gh"]}}}',
                    encoding="utf-8")

    refs = mcp_json.parse(path)

    assert refs
    assert "runtime_hosts" not in (refs[0].extra or {})
```

- [ ] **Step 7: Run the parser and integration suites**

Run: `uv run pytest tests/test_parsers tests/test_graph_build.py tests/test_e2e.py -v`
Expected: PASS. Any test asserting `runtime_hosts` on a *freshly parsed* ref is
updated; any test asserting it on a ref restored from a stored `0.4` BOM must still
pass unchanged — that reader is retained.

- [ ] **Step 8: Commit**

```bash
git add tools/active_in.py tools/finding_output.py tools/posture tools/observations \
        tools/parsers tools/graph_build.py tests
git commit -m "refactor(findings): source active_in from the agent; parsers drop runtime_hosts"
```

---

## Task 10: End-to-end characterisation, golden files, and docs

**Files:**
- Modify: `tests/test_e2e.py`, `tests/fixtures/reports/card-endpoint.txt`
- Modify: `tests/remote/test_collect.py` (one added assertion)
- Modify: `docs/openaca-bom-schema.md`, `docs/reference/cli.md`,
  `docs/concepts/identities.md`, `docs/concepts/scan-modes.md`,
  `docs/plans/README.md`
- Create: `docs/releases/` note entry (follow the existing file naming in that directory)

**Interfaces:** consumes everything; produces no new API.

- [ ] **Step 1: Write the cross-layer characterisation test**

```python
# tests/test_e2e.py
def test_endpoint_scan_emits_the_migrated_agent_document(tmp_path):
    """The spec's `Migrating Claude Code` diff table, asserted row by row.
    Anything else in this document's diff is a regression."""
    root = tmp_path / ".claude"
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )

    out = CliRunner().invoke(bom_main, ["endpoint", "--config-dir", str(root)])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.output.strip())

    metadata_props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert metadata_props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in metadata_props

    component = doc["metadata"]["component"]
    assert component["bom-ref"] == "root/claude-code"
    assert component["name"] == "Claude Code"
    assert {p["name"] for p in component["properties"]} == {
        "openaca:agent_kind", "openaca:composition_source", "openaca:composition_coverage"}

    refs = [c["bom-ref"] for c in doc["components"]]
    assert refs and all(
        r.startswith("claude-code/") or r.startswith("project/") for r in refs
    )
    names = {p["name"] for c in doc["components"] for p in c.get("properties", [])}
    assert {"openaca:agent_host", "openaca:runtime_hosts"} & names == set()

    lint_path = tmp_path / "agent.cdx.json"
    lint_path.write_text(json.dumps(doc), encoding="utf-8")
    lint = CliRunner().invoke(openaca_main, ["bom", "lint", str(lint_path)])
    assert lint.exit_code == 0, lint.output


def test_declared_repo_scan_keeps_component_bom_refs(tmp_path):
    """Repo node keys are bare paths under one root, so only the root ref moves."""
    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    doc = json.loads(
        CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)]).output.strip()
    )

    assert doc["metadata"]["component"]["bom-ref"] == "root/claude-code"
    props = {p["name"]: p["value"] for p in doc["metadata"]["component"]["properties"]}
    assert props["openaca:composition_source"] == "declared"
    assert any(c["bom-ref"].startswith(".claude/skills/deploy/") for c in doc["components"])
```

- [ ] **Step 2: Add the forward guard on the upload contract**

The collector is not migrated, but an agent id is new content in a document, and the
spec asks for it to be tested against the redaction contract rather than assumed safe.
`build_agent_bom`'s flat (`graph=None`) path never sets `target_bom_ref`, so the agent
properties would never reach `metadata.component` for that call shape — agent metadata
only exists on a graph-rooted document (Task 5), so the test must build one, not pass
`graph=None` and assume the fields ride along:

```python
# tests/remote/test_collect.py
from tools.graph import Graph, Node


def test_upload_contract_accepts_an_agent_rooted_document():
    root = Node(key="root/synthetic/payments-triage", kind="target", ref=None)
    graph = Graph(nodes={root.key: root})

    doc = build_agent_bom(
        [], target_type=None, graph=graph, agent_kind="synthetic",
        agent_id="payments-triage", agent_name="payments-triage",
        composition_source="installed", composition_coverage="partial",
    ).to_cyclonedx()

    props = {p["name"]: p["value"] for p in doc["metadata"]["component"]["properties"]}
    assert doc["metadata"]["component"]["bom-ref"] == "root/synthetic/payments-triage"
    assert props["openaca:agent_kind"] == "synthetic"
    assert props["openaca:agent_id"] == "payments-triage"

    enforce_remote_upload_contract({"bom": doc})   # must not raise
```

- [ ] **Step 3: Run the e2e suite**

Run: `uv run pytest tests/test_e2e.py tests/remote -v`
Expected: PASS.

- [ ] **Step 4: Recapture the golden card and review it**

Run: `uv run pytest tests/test_render.py -k card_endpoint -v`

If it fails, recapture `tests/fixtures/reports/card-endpoint.txt` and diff it. The only
permitted change is inside the Target block. **Anything else in that diff is a
regression** — investigate rather than accept.

- [ ] **Step 5: Update the docs**

- `docs/openaca-bom-schema.md`: `schema_version` `0.5`; the four
  `metadata.component` properties with their value sets; `openaca:target_type`,
  `openaca:agent_host`, and `openaca:runtime_hosts` moved to a "read for stored
  documents, no longer written" section; a worked agent-document example.
- `docs/reference/cli.md`: NDJSON stdout for `bom endpoint`/`bom repo`;
  `--output-dir` owns the `*.cdx.json` files in that directory and clears stale
  ones from a prior run with fewer agents, rather than only ever adding; `--output`
  deprecated with the multi-agent error; the no-agent-found exit-0 message;
  `scan bom` accepting NDJSON; `agents[]` in `--format json`.
- `docs/concepts/identities.md`: the (asset, kind, agent id) instance key, the `root/`
  prefix and why it is not `agent/`, and that `openaca:identity` is unchanged.
- `docs/concepts/scan-modes.md`: `endpoint` yields `installed`, `repo` yields
  `declared`; a repo declaring nothing produces no document.
- Release note: every `bom-ref` in the installed path is renamed once, so the first
  diff after upgrade reports every component removed and re-added; uploads are
  unaffected because the collector still emits `endpoint/` keys.

- [ ] **Step 6: Set the plan index row**

In `docs/plans/README.md`, add the 040 row and set the status to ✅ Done.

- [ ] **Step 7: Final gate and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
git add docs tests
git commit -m "docs: bring the BOM reference, CLI docs, and concepts current for agent-rooted BOMs"
```

---

## Verification

Per task: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

By hand before opening the PR:

```bash
# One document, agent-rooted, still parseable as a single JSON object.
uv run openaca bom endpoint | jq '.metadata.component'
#   → bom-ref "root/claude-code", name "Claude Code", three properties

uv run openaca bom endpoint | jq -e '
  (.metadata.properties | map(.name) | index("openaca:target_type") | not)
  and ([.components[].properties[]?.name] | index("openaca:runtime_hosts") | not)
  and ([.components[]."bom-ref"] | all(startswith("claude-code/") or startswith("project/")))'

uv run openaca bom endpoint --output-dir /tmp/boms && ls /tmp/boms   # claude-code.cdx.json
uv run openaca bom lint /tmp/boms/claude-code.cdx.json               # ok
uv run openaca bom lint <a stored 0.4 BOM>                           # still ok

uv run openaca scan endpoint                                         # one card
uv run openaca scan endpoint --format json | jq '.agents, (.findings[0].agent)'
uv run openaca scan bom --input /tmp/boms/claude-code.cdx.json       # graph-backed path

# Declared discovery.
uv run openaca bom repo --target <repo with .claude/>                # one declared BOM
uv run openaca bom repo --target <repo with only package.json>       # nothing, exit 0
echo $?                                                              # 0

# The legacy upload path is untouched.
uv run pytest tests/remote -v
uv run python -c "
from pathlib import Path; from tools.remote.collector import build_endpoint_collection
bom = build_endpoint_collection(Path.home()/'.claude', None).bom
print(bom['metadata']['component']['bom-ref'])
print([p for p in bom['metadata']['properties'] if 'target_type' in p['name']])
print([c['bom-ref'] for c in bom['components']][:3])"
#   → openaca:target, target_type endpoint, endpoint/... keys
```

## Out of scope

Deliberately excluded, per the spec:

- **A second runtime.** No new parser. The multi-agent paths ship exercised, but only
  the synthetic test kind reaches them.
- **Surface sets as declarative data.** The `.claude` path literals in `graph_build.py`
  stay literals; generalising them is what a second kind forces.
- **Migrating the remote collector to agent discovery**, and its fail-closed guard
  against uploading more than one agent BOM. The collector cannot produce more than one
  document while it uses the legacy path, so the guard has nothing to guard yet;
  resolving latest-BOM-per-asset on the hosted side gates the *second* kind.
- **Removing `build_graph`'s `mode` parameter** — the collector is a live caller.
- **Diff pairing across many BOMs.** The key is (asset, kind, agent id); the diff
  primitive stays singular and no caller needs pairing while one document per scan is
  emitted.
- **Consolidating the endpoint config-dir resolvers.** `bom_cli.py`'s copy goes, but
  `tools/scan.py` and `tools/remote/cli.py` keep theirs. Today's rule stands: an
  explicit `--config-dir` is valid only when exactly one agent resolves.
- **Publishing `composition_coverage` reasons** — ADR-0046 defers the vocabulary
  because it has a known hole.
- **A kind with no config root** (control-plane) and **`agent_id` canonicalisation
  beyond the filename slug**: both are gated on the first kind of that shape.
  `AgentInstance.config_root` is already `Path | None` so the first is not blocked by
  this design.

## Self-review — spec coverage

| Spec section | Task |
|---|---|
| BOM attribute changes: Remove ×3 | 5 |
| BOM attribute changes: Add ×4 | 5 |
| BOM attribute changes: Edit (`target`, `schema_version`, root labels, `name`) | 4, 5 |
| Required invariants (six rows) | 2 (five as schema/linter), 3 (`agent_id` cardinality) |
| Why `target_type` goes; both readers re-keyed | 5 |
| Model: kind and composition source independent | 3 |
| Model: what proves an agent exists (installed vs declared asymmetry) | 3, 6 |
| Model: why `agent_id` exists; singleton omits it | 3, 7 |
| Coverage: `min(baseline, evidence)`, per source | 3, 7, 8 |
| Privacy boundary (agent id vs redaction contract) | 10 |
| One kind may read another's files (owner-named labels) | 4 |
| Internals not visible in a BOM (posture allowlist) | 3, 8 |
| Recommendation B: one graph per agent, agent as single target root | 4 |
| Scanner change: parsers stop knowing the runtime | 9 |
| Scanner change: the flat manifest registry splits per kind, reached through a surface | 3, 7, 8 |
| Scanner change: findings gain an agent association; `host_surface` per agent | 8 |
| Scanner change: one federation pass, matching per agent | 8 |
| Scanner change: subcommand names unchanged; `mode` retained; resolvers not consolidated | none — no task renames or removes them |
| Emitting many documents: NDJSON, `--output-dir`, `--output` deprecation | 7 |
| Migrating Claude Code, steps 1–7 | 2, 3, 4, 5, 7, 8, 10 |
| Backward compatibility: safe-by-inspection rows | 5, 9, 10 |
| Backward compatibility: breaks once (`bom-ref` rename) | 4, 10 |
| Backward compatibility: uploads keep today's contract | Global constraint, verified in 10 |
