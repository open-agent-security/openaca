# Plan 037 — Agent component capability extraction (v1)

> Implements ADR-0041 and `docs/specs/capability-extraction.md`. Give each agent
> component an evidence-backed capability block (declared + curated tiers only),
> keyed by a closed taxonomy, with an explicit coverage marker, and emit it in the
> Agent BOM as component descriptors. Source-analysis / assisted-drafting tiers and
> the exposure-ranking consumer are **out of scope** (separate specs/plans).

**Goal:** A scan populates each `ComponentRef` with a defensible `capabilities`
list + `capability_coverage`, sourced from manifest-declared signals and a curated
`capabilities/<identity>.yaml` corpus, and the Agent BOM emits them as
`openaca:capabilities` / `openaca:capability_coverage` component properties.

**Architecture:** A capability model (`tools/capability.py`) defines the closed
taxonomy and the `Capability` record. Tier-1 extractors read declared signals
(skill `allowed-tools`, hook/command shell, remote-MCP connection). A corpus loader
(`tools/capability_corpus.py`) provides curated capabilities by `openaca:identity`,
or, where a record declares one, by a verified `match_coordinate` (package
coordinate) instead — local config aliases are not trustworthy enough to key a
package-scoped record alone. An orchestrator merges both, sets coverage, and
stores the result on
`ComponentRef.extra` (v1 keeps the frozen dataclass stable; promotion to
first-class fields is a later refactor). The BOM emitter renders the block as
descriptors. Exposure ranking is a downstream consumer, not built here.

**Tech stack:** Python/uv. Gate: `ruff check`, `ruff format --check`, `pyright`,
`pytest`, `openaca lint`.

---

## Task 1: Capability model

**Files:**
- Create: `tools/capability.py`
- Test: `tests/test_capability.py`

- [ ] **Step 1 — failing test.** Assert the taxonomy is closed and a `Capability`
  round-trips to/from a dict.

```python
from tools.capability import Capability, CAPABILITY_NAMES, COVERAGE_LEVELS

def test_taxonomy_is_closed():
    assert CAPABILITY_NAMES == frozenset({
        "file_read", "file_write", "shell_exec",
        "network_egress", "credential_access", "sensitive_data_access",
    })
    assert COVERAGE_LEVELS == ("unknown", "partial", "complete")

def test_capability_roundtrip():
    cap = Capability(
        name="shell_exec", execution_locus="local", method="declared",
        source="openaca", source_version="0.4.0", confidence="high",
        evidence=[{"kind": "manifest_field", "path": "SKILL.md",
                   "field": "allowed-tools", "value": "Bash(*)"}],
    )
    assert Capability.from_dict(cap.to_dict()) == cap
```

- [ ] **Step 2 — run, confirm fail** (module missing).
- [ ] **Step 3 — implement** `tools/capability.py`: a frozen `Capability`
  dataclass with fields `name`, `execution_locus` (`local`|`remote`), `method`
  (`declared`|`curated`|`inferred`), `source`, `source_version`, `confidence`
  (`high`|`medium`|`low`), `evidence: list[dict]`; `to_dict()` / `from_dict()`
  (evidence as a tuple internally for frozen-hashability, list in dict form);
  module constants `CAPABILITY_NAMES` (the frozenset above) and `COVERAGE_LEVELS`.
  Validate `name in CAPABILITY_NAMES` and `execution_locus`/`method`/`confidence`
  in their enums in `__post_init__`; raise `ValueError` otherwise.
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(capability): closed taxonomy + Capability record`

---

## Task 2: Tier-1 declared extractors

**Files:**
- Create: `tools/capability_extract.py`
- Test: `tests/test_capability_extract.py`

Map declared signals to capabilities. Reuse `tools/posture/rules/skill_capability.py`
helpers (`_allowed_tools`, `_executable_tool_base`) — refactor the shared bits into
`tools/capability_extract.py` and have the posture rule import them (don't
duplicate the allowed-tools parsing).

- [ ] **Step 1 — failing tests**, one per component kind:

```python
from tools.component_ref import ComponentRef
from tools.capability_extract import declared_capabilities

def _skill(tmp_path, allowed):
    p = tmp_path / "SKILL.md"
    p.write_text(f"---\nname: x\nallowed-tools: {allowed}\n---\n")
    return ComponentRef(name="x", source_manifest=str(p),
                        extra={"component_type": "skill"})

def test_skill_bash_maps_to_shell_exec(tmp_path):
    caps = declared_capabilities(_skill(tmp_path, "Bash(*)"))
    assert {c.name for c in caps} == {"shell_exec"}
    assert caps[0].method == "declared" and caps[0].execution_locus == "local"

def test_skill_write_read_map_to_file_caps(tmp_path):
    caps = declared_capabilities(_skill(tmp_path, "Read, Write"))
    assert {c.name for c in caps} == {"file_read", "file_write"}

def test_remote_mcp_maps_to_egress_and_data(tmp_path):
    ref = ComponentRef(component_identity="mcp-server/x",
        extra={"component_type": "mcp_server", "transport": "sse",
               "install_source": "https://mcp.example.com/mcp"})
    caps = declared_capabilities(ref)
    names = {c.name for c in caps}
    assert names == {"network_egress", "sensitive_data_access"}
    assert all(c.execution_locus == "remote" for c in caps)
    assert any(e.get("field") == "url" for c in caps for e in c.evidence)

def test_unknown_component_declares_nothing(tmp_path):
    assert declared_capabilities(ComponentRef(name="p",
        extra={"component_type": "plugin"})) == []

def test_slash_command_declares_nothing(tmp_path):
    # claude_command_agent.py emits no command/shell string for these refs —
    # must not be mistaken for a hook and mapped to shell_exec.
    assert declared_capabilities(ComponentRef(name="x",
        extra={"scope_owner": None, "component_type": "command"})) == []
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `declared_capabilities(ref) -> list[Capability]`:
  - skill: read frontmatter `allowed-tools`; map tool base → capability:
    `bash`/`shell`→`shell_exec`, `write`/`edit`→`file_write`, `read`→`file_read`,
    `webfetch`/`websearch`→`network_egress`. Evidence: `{kind: manifest_field,
    path, field: "allowed-tools", value: <tool>}`. `execution_locus="local"`.
  - hook (`component_type == "hook"`): the shell command string in
    `ref.extra["command"]` → `shell_exec` (+ `network_egress` if it contains
    `curl`/`wget`/`http`). Evidence: the command string. `local`.
  - slash command / subagent (`component_type` in {`command`,`agent`}):
    **not** mapped here. `tools/parsers/claude_command_agent.py` emits these
    refs with only `scope_owner` + `component_type` in `extra` — there is no
    shell command string to cite as evidence, and the markdown prompt body is
    attacker-influenced content, not a declared signal. Falls through to
    `everything else: []` below. A prompt-content capability source (if ever
    justified) is a separate, deferred surface — not v1.
  - remote MCP (`component_type=="mcp_server"` and `install_source` is an
    `http(s)://` URL, or a URL transport): emit `network_egress` +
    `sensitive_data_access`, `execution_locus="remote"`, evidence
    `{kind: manifest_field, field: "url", value: <url>}`.
  - everything else: `[]`.
  All records `method="declared"`, `source="openaca"`,
  `source_version=<openaca __version__>`, `confidence="high"`.
- [ ] **Step 4 — run, confirm PASS**; run existing
  `tests/test_posture*.py` to confirm the `skill_capability` refactor didn't break.
- [ ] **Step 5 — commit.** `feat(capability): tier-1 declared extractors`

---

## Task 3: Capability corpus schema + a seed entry

**Files:**
- Create: `schema/openaca-capability.schema.json`
- Create: `capabilities/mcp-server-filesystem.yaml` (one seed)
- Test: `tests/test_capability_corpus.py` (schema-validity portion)

- [ ] **Step 1 — failing test.** The seed file validates against the schema and has
  the required shape.

```python
import json, yaml, jsonschema
from pathlib import Path

def test_seed_entry_validates():
    schema = json.loads(Path("schema/openaca-capability.schema.json").read_text())
    doc = yaml.safe_load(Path("capabilities/mcp-server-filesystem.yaml").read_text())
    jsonschema.validate(doc, schema)          # must not raise
    assert doc["identity"].startswith("mcp-server/")
    assert doc["last_reviewed"] and doc["reviewed_version"]
    assert all(c["name"] for c in doc["capabilities"])
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** the schema: required `identity` (string),
  `last_reviewed` (date string), `reviewed_version` (string), `capabilities` (array
  of `{name (enum = taxonomy), execution_locus (enum), confidence (enum),
  evidence (array)}`); optional `match_coordinate` (string). `match_coordinate`
  is a package coordinate in the same value space `tools.identity.mcp_package_source`
  already derives for MCP servers, canonicalized as `f"{ecosystem}/{package}"`
  (e.g. `npm/@modelcontextprotocol/server-filesystem`) — **not** an alternate
  identity, but a constraint: when present, the record binds to that upstream
  package and is looked up by the coordinate alone (see Task 4), because a local
  MCP config alias (`canonical_component_identity()`'s `mcp-server/<config
  name>`) is user-chosen and not a reliable key for a package-scoped record.
  `version_ranges` is **not** a v1 schema field — enforcing it needs
  ecosystem-aware version comparison the loader can't do generically across
  non-PURL identities (deferred to the tier-3 drift-catch analysis; see ADR-0041
  rule 4). Write the `capabilities/mcp-server-filesystem.yaml` seed with two
  capabilities (`file_read`, `file_write`, `execution_locus: local`), a
  `match_coordinate: "npm/@modelcontextprotocol/server-filesystem"` (so the seed
  applies regardless of the local config alias a real deployment gives this
  server), and a `last_reviewed` / `reviewed_version`, each capability with a
  one-line evidence note.
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(capability): corpus schema + first curated entry`

---

## Task 4: Corpus loader (curated tier), identity-keyed

**Files:**
- Create: `tools/capability_corpus.py`
- Test: `tests/test_capability_corpus.py` (loader portion)

- [ ] **Step 1 — failing test.**

```python
from tools.capability_corpus import load_capability_corpus

def test_lookup_by_match_coordinate_ignores_local_alias():
    # The seed's match_coordinate is the npm package coordinate. A ref that
    # aliases the same server under a different local config name must still
    # get the curated capabilities via the coordinate, not the alias.
    corpus = load_capability_corpus()   # defaults to capabilities/
    caps = corpus.lookup("mcp-server/some-local-alias",
                          match_coordinate="npm/@modelcontextprotocol/server-filesystem")
    assert {c.name for c in caps} >= {"file_read", "file_write"}
    assert all(c.method == "curated" and c.source == "openaca" for c in caps)
    assert any(e.get("kind") == "curated_review" for c in caps for e in c.evidence)

def test_lookup_unknown_identity_returns_empty():
    assert load_capability_corpus().lookup("mcp-server/does-not-exist") == []

def test_constrained_record_not_returned_by_identity_alone():
    # A record with a match_coordinate must never surface via identity alone
    # -- otherwise an unrelated component that happens to reuse the curated
    # identity string as its local config alias would inherit capabilities
    # it was never reviewed for.
    corpus = load_capability_corpus()
    assert corpus.lookup("mcp-server/filesystem") == []
    assert corpus.lookup("mcp-server/filesystem",
                          match_coordinate="npm/some-other-package") == []

def test_lookup_identity_only_for_unconstrained_records(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "identity: mcp-server/x\nlast_reviewed: '2026-07-03'\n"
        "reviewed_version: '1.0'\ncapabilities:\n"
        "  - {name: file_read, execution_locus: local, confidence: high, evidence: []}\n"
    )
    corpus = load_capability_corpus(root=tmp_path)
    assert {c.name for c in corpus.lookup("mcp-server/x")} == {"file_read"}
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `load_capability_corpus(root="capabilities") -> CapabilityCorpus`:
  parse every `*.yaml`. Records that declare `match_coordinate` are indexed by
  that coordinate *only*; records without one are indexed by `identity` *only*.
  `CapabilityCorpus.lookup(identity, match_coordinate=None)` unions a hit from
  the identity-only index (always queried) with a hit from the
  match-coordinate index (queried only when `match_coordinate` is given) — so a
  constrained record can never surface via identity alone, and an unconstrained
  record can never surface via a coordinate. Callers compute `match_coordinate`
  from the ref itself (see Task 6); the corpus never guesses it from the
  record's own `identity` field. Returned capabilities carry `method="curated"`,
  `source="openaca"`, `source_version=<openaca __version__>` — matching the
  declared tier's convention that `source_version` is the version of the
  *asserting source* (the OpenACA release), not the reviewed component. The
  record's `reviewed_version` / `last_reviewed` are preserved instead of being
  dropped or overloaded onto `source_version`: append one evidence entry per
  capability, `{"kind": "curated_review", "reviewed_version": ...,
  "last_reviewed": ...}`, so a downstream report can still flag
  reviewed-version-vs-installed-version drift. Return `[]` on miss.
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(capability): coordinate-constrained curated corpus loader`

---

## Task 5: Corpus linter (`openaca lint capabilities/`)

**Files:**
- Modify: the lint CLI entry (`tools/lint*.py` / the `openaca lint` command wiring)
- Test: `tests/test_capability_corpus.py` (lint portion) or the lint test module

- [ ] **Step 1 — failing test.** A malformed entry (bad capability name; missing
  `last_reviewed`) fails lint; the seed passes.

```python
def test_lint_rejects_bad_capability_name(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("identity: mcp-server/x\nlast_reviewed: 2026-07-03\n"
                   "reviewed_version: '1.0'\ncapabilities:\n"
                   "  - {name: not_a_cap, execution_locus: local, confidence: high, evidence: []}\n")
    errors = lint_capability_dir(tmp_path)
    assert any("not_a_cap" in e for e in errors)
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `lint_capability_dir(path)` mirroring overlay lint
  discipline: schema validation, identity-format check, evidence-presence,
  duplicate-identity detection. Wire it into `openaca lint` so
  `openaca lint capabilities/` works.
- [ ] **Step 4 — run, confirm PASS**; run `uv run openaca lint capabilities/`.
- [ ] **Step 5 — commit.** `feat(capability): corpus linter + openaca lint wiring`

---

## Task 6: Extraction orchestrator (merge tiers, set coverage)

**Files:**
- Modify: `tools/capability.py` (add `capabilities_for_ref`)
- Test: `tests/test_capability.py`

- [ ] **Step 1 — failing test.**

```python
from tools.capability import capabilities_for_ref
from tools.capability_corpus import load_capability_corpus

def test_merges_declared_and_curated_sets_partial(tmp_path):
    # a skill that declares Bash + is in the corpus (curated file_read)
    ref = _skill_in_corpus(tmp_path)
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"shell_exec"}
    assert coverage == "partial"

def test_no_signal_is_unknown():
    caps, coverage = capabilities_for_ref(
        ComponentRef(name="p", extra={"component_type": "plugin"}),
        load_capability_corpus())
    assert caps == [] and coverage == "unknown"

def test_package_mcp_matches_curated_seed_despite_local_alias():
    # Local config alias ("fs") differs from the seed's identity convention
    # ("mcp-server/filesystem"), but the ref's install_source resolves to the
    # same npm package the seed's match_coordinate is scoped to.
    ref = ComponentRef(component_identity="mcp-server/fs",
        extra={"component_type": "mcp_server",
               "install_source": "npx @modelcontextprotocol/server-filesystem"})
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"file_read", "file_write"}
    assert coverage == "partial"
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `capabilities_for_ref(ref, corpus) -> tuple[list[Capability], str]`:
  compute the ref's own match coordinate via
  `tools.identity.mcp_package_source(ref.extra.get("install_source"))` —
  `f"{ecosystem}/{package}"` when it resolves (an npx/uvx/bunx-launched MCP
  server), else `None`. Union of `declared_capabilities(ref)` and
  `corpus.lookup(<ref identity>, match_coordinate=<computed coordinate>)`
  (dedupe by `(name, execution_locus)`, preferring `declared` evidence).
  Reusing `mcp_package_source` (already used for advisory match-coordinate
  matching in `tools/identity.py` / `tools/matcher.py`) instead of re-deriving
  package identity keeps this consistent with how the rest of OpenACA already
  distinguishes a verified upstream coordinate from a user-chosen local alias.
  Coverage: `unknown` if the union is empty, else `partial` (v1 never asserts
  `complete` — we cannot prove exhaustiveness).
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(capability): tier-merge orchestrator + coverage`

---

## Task 7: Annotate refs during BOM construction

**Files:**
- Modify: `tools/bom.py` (`build_agent_bom`, `_build_agent_bom_from_graph`)
- Test: `tests/test_bom.py`, `tests/test_bom_cli.py`

- [ ] **Step 1 — failing test.** Every BOM-producing entry point funnels through
  `build_agent_bom`: `scan repo` / `scan endpoint` / `scan bom`
  (`tools/scan.py`) and `bom repo` / `bom endpoint` (`tools/bom_cli.py`) all
  call it — some with `graph=graph`, one (`scan bom`'s already-flat-BOM
  re-ingest branch) with no graph at all. Annotation must live inside
  `build_agent_bom` itself, not in a scan-only stage: `run_posture_rules` is
  gated behind `--include-posture` and wired only into `tools/scan.py`, so
  annotating "in the same stage as posture" would leave `bom repo` / `bom
  endpoint` — which never call `tools/scan.py` — emitting schema `0.3` with no
  `openaca:capabilities` / `openaca:capability_coverage` properties at all.

```python
def test_build_agent_bom_annotates_plain_refs(tmp_path):
    ref = _skill_with_bash(tmp_path)  # component_type=skill, allowed-tools: Bash
    bom = build_agent_bom([ref], target_type="repo")
    out_ref = bom.components[0].ref
    assert out_ref.extra["capability_coverage"] in {"partial", "complete"}
    assert any(c["name"] == "shell_exec" for c in out_ref.extra["capabilities"])

def test_build_agent_bom_annotates_graph_refs(tmp_path):
    graph = _graph_with_bash_skill(tmp_path)
    bom = build_agent_bom([], target_type="repo", graph=graph)
    skill = next(
        c.ref for c in bom.components if c.ref.extra.get("component_type") == "skill"
    )
    assert skill.extra["capability_coverage"] in {"partial", "complete"}
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `_annotate_capabilities(refs: Iterable[ComponentRef]) -> None`
  in `tools/bom.py`: load the corpus once (`load_capability_corpus()`); for each
  ref **that does not already carry `capability_coverage` in `extra`**, compute
  `caps, coverage = capabilities_for_ref(ref, corpus)`, then write
  `ref.extra["capabilities"] = [c.to_dict() for c in caps]` and
  `ref.extra["capability_coverage"] = coverage` (`ComponentRef` is frozen;
  `extra` is a mutable dict — write into it, matching how other post-parse
  annotations are set). The presence check matters for `scan bom`: Task 8's
  `_extra_from_properties` fix restores `openaca:capabilities` /
  `openaca:capability_coverage` from an ingested BOM into `extra` *before*
  `build_agent_bom` runs, so a ref arriving with `capability_coverage` already
  set carries descriptors produced by whatever corpus/extractor built the
  original BOM (possibly a newer OpenACA release or an external one) — skipping
  re-annotation preserves that evidence instead of silently overwriting it with
  a fresh (and potentially weaker) local recomputation. Call it:
  - in `build_agent_bom`'s non-graph branch, over `refs`, before building
    `components`;
  - in `_build_agent_bom_from_graph`, over the `ref` of every included node,
    before the `replace(node.ref, scope=...)` call, so the scope-replaced copy
    shares the same mutated `extra` dict.
  This makes annotation unconditional for every *un-annotated* BOM component
  (matching ADR-0041 rule 2 — coverage is a component descriptor, not an
  opt-in finding like posture), independent of `--include-posture` and of
  whether the caller goes through `tools/scan.py` or `tools/bom_cli.py` — the
  "already annotated" skip only fires for refs re-ingested from an existing
  BOM (see Step 3 above).
- [ ] **Step 4 — run, confirm PASS.** Add
  `test_bom_cli_repo_emits_capability_properties` to `tests/test_bom_cli.py`,
  invoking the `bom repo` CLI command directly (not `tools/scan.py`) against a
  fixture with a `Bash` skill, asserting the CycloneDX output carries
  `openaca:capabilities` — the regression test for the direct-CLI-path gap.
  Add `test_build_agent_bom_preserves_reingested_capabilities` to
  `tests/test_bom.py`: a ref pre-populated with
  `extra={"capability_coverage": "complete", "capabilities": [...]}` (simulating
  what `_extra_from_properties` restores from an ingested BOM) must come out of
  `build_agent_bom` with that exact `capabilities` list and `"complete"`
  coverage unchanged, not recomputed to `"partial"` by the local corpus/extractor
  — the regression test for the `scan bom` re-ingest gap.
- [ ] **Step 5 — commit.** `feat(bom): annotate refs with capabilities during BOM construction`

---

## Task 8: BOM emission + schema-version bump (amends ADR-0022)

**Files:**
- Modify: `tools/bom.py` (`_component_to_cyclonedx`, `_extra_from_properties`,
  `OPENACA_BOM_SCHEMA_VERSION`)
- Modify: `schema/openaca-bom.schema.json`
- Modify: `docs/openaca-bom-schema.md`
- Test: `tests/test_bom.py`, `tests/test_bom_cli.py`, `tests/test_bom_lint.py`

- [ ] **Step 1 — failing test.**

```python
def test_bom_emits_capability_descriptors():
    ref = ComponentRef(component_identity="mcp-server/x",
        extra={"component_type": "mcp_server",
               "capabilities": [{"name": "shell_exec", "execution_locus": "local",
                                 "method": "curated", "source": "openaca",
                                 "confidence": "high", "evidence": []}],
               "capability_coverage": "partial"})
    doc = build_bom([ref], target_type="repo").to_cyclonedx()
    props = _props(doc["components"][0])
    assert json.loads(props["openaca:capabilities"])[0]["name"] == "shell_exec"
    assert props["openaca:capability_coverage"] == "partial"
    assert _metadata_property(doc, "openaca:schema_version") == "0.3"

def test_bom_emits_coverage_for_uncovered_component():
    # No declared/curated signal: capabilities=[] but coverage must still
    # emit — dropping it here would recreate the silent-empty state
    # ADR-0041 rule 2 forbids.
    ref = ComponentRef(component_identity="plugin/y",
        extra={"component_type": "plugin", "capabilities": [],
               "capability_coverage": "unknown"})
    doc = build_bom([ref], target_type="repo").to_cyclonedx()
    props = _props(doc["components"][0])
    assert json.loads(props["openaca:capabilities"]) == []
    assert props["openaca:capability_coverage"] == "unknown"

def test_bom_roundtrips_capability_properties():
    # scan bom rebuilds refs via component_refs_from_cyclonedx(), which
    # restores extra from properties through an explicit allow-list
    # (_extra_from_properties). Descriptors must survive that round trip or
    # a re-ingested BOM silently loses them.
    ref = ComponentRef(component_identity="mcp-server/x",
        extra={"component_type": "mcp_server",
               "capabilities": [{"name": "shell_exec", "execution_locus": "local",
                                 "method": "curated", "source": "openaca",
                                 "confidence": "high", "evidence": []}],
               "capability_coverage": "partial"})
    doc = build_bom([ref], target_type="repo").to_cyclonedx()
    rebuilt = component_refs_from_cyclonedx(doc)[0]
    assert rebuilt.extra["capabilities"][0]["name"] == "shell_exec"
    assert rebuilt.extra["capability_coverage"] == "partial"
```

- [ ] **Step 2 — run, confirm fail** (also fails the existing `== "0.2"` assertions).
- [ ] **Step 3 — implement:**
  - In `_component_to_cyclonedx`, key the emission on whether
    `capability_coverage` is present in `extra` (i.e. the capability-annotation
    pass ran on this ref) — **not** on whether `capabilities` is non-empty.
    Append `{"name": "openaca:capabilities", "value": json.dumps(extra["capabilities"])}`
    and `{"name": "openaca:capability_coverage", "value": extra["capability_coverage"]}`
    (JSON-encoded value, matching the `openaca:source_provenance` precedent) even
    when `capabilities` is `[]` and coverage is `unknown`.
  - In `_extra_from_properties`, restore both properties back into `extra`:
    `openaca:capabilities` parsed with `json.loads` into `extra["capabilities"]`
    (matching the `openaca:runtime_hosts`/`openaca:source` JSON-property
    precedent), `openaca:capability_coverage` copied verbatim into
    `extra["capability_coverage"]`. Without this, `scan bom` silently drops
    both descriptors on re-ingest (the parser only restores properties in its
    existing allow-list).
  - Bump `OPENACA_BOM_SCHEMA_VERSION = "0.3"`.
  - In `schema/openaca-bom.schema.json`: add `openaca:capabilities` /
    `openaca:capability_coverage` to the allowed property names; change the
    metadata `openaca:schema_version` value constraint to `{"enum": ["0.1","0.2","0.3"]}`
    (lint accepts prior versions; emitter produces `0.3`).
  - Update `docs/openaca-bom-schema.md`: current version `0.3`; add the two
    properties to the property table with a one-line "component descriptor, not a
    finding (ADR-0041)" note; bump the example.
  - Update the `0.2` assertions/fixtures in `test_bom.py` / `test_bom_cli.py` /
    `test_bom_lint.py` to `0.3` (same ripple as the 0.1→0.2 bump).
- [ ] **Step 4 — run, confirm PASS** across `test_bom*.py`.
- [ ] **Step 5 — commit.** `feat(bom): emit capability descriptors; schema 0.2 -> 0.3 (ADR-0041)`

---

## Task 9: End-to-end

**Files:** `tests/test_e2e.py`

- [ ] **Step 1 — failing e2e.** A fixture repo with (a) a skill declaring `Bash`
  and (b) an MCP server whose identity has a curated capability entry produces an
  Agent BOM where both components carry `openaca:capabilities` with the right
  `method` (`declared` vs `curated`), `execution_locus`, and a `partial`
  `openaca:capability_coverage`.
- [ ] **Step 2 — run, confirm fail today.**
- [ ] **Step 3 — confirm PASS** after Tasks 1-8 (add a `capabilities/` seed entry
  for the fixture MCP identity if needed).
- [ ] **Step 4 — commit.** `test(e2e): capability descriptors in the Agent BOM`

---

## Self-review checklist (before PR)

- [ ] Capability names limited to the closed taxonomy everywhere; adding one would
      require an ADR (per ADR-0041).
- [ ] No component ever emits a silently-empty capability list to mean "none":
      absent signal → `capability_coverage: unknown`.
- [ ] `method` (`declared`/`curated`) and `source` are separate fields (ADR-0035);
      no source-analysis or model-generated claims emitted (deferred tiers).
- [ ] Exposure scores/rankings/cards are **not** added to the BOM (ADR-0022
      composition-only boundary; ADR-0041 amendment covers descriptors only).
- [ ] `capabilities/` is a distinct corpus from `overlays/`; records are keyed by
      `openaca:identity` and version-independent.
- [ ] `uv run openaca lint capabilities/` passes; full gate green
      (`ruff check` · `ruff format --check` · `pyright` · `pytest` · `openaca lint`).
