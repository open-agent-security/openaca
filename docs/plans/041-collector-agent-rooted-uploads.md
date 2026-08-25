# Plan 041 — Collector agent-rooted uploads

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `openaca remote sync endpoint` discovers agents and uploads one agent-rooted
`0.5` BOM per agent, instead of one place-rooted `0.4` BOM per sync.

**Architecture:** The local scan path already does this. The collector replaces
`build_graph(config_dir, mode="endpoint")` with the same
`discover_agents` / `build_agent_graph` pair `tools/scan.py` uses, builds one
`EndpointCollection` per agent, and sends one upload per collection against the single
registered asset. Two upload-boundary rules diverge from the scan path and are the
reason this plan exists rather than a one-line call-site swap: the redaction contract
must cover `bom.metadata` before anything writes there, and the upload writes no
`openaca:target` at all.

**Tech Stack:** Python 3.11, click, httpx, pytest, uv, ruff, pyright.

**Spec:** `docs/specs/collector-agent-rooted-uploads.md`
**ADRs:** `docs/adrs/0050-collector-upload-cardinality.md` (one asset, N uploads),
`docs/adrs/0051-redaction-covers-bom-metadata.md` (redaction scope).
The agent model itself is `docs/specs/multi-agent-support.md` — read it for the wire
format; this plan does not restate it.

## Context

Plan 040 migrated `scan` and `bom` to agent-rooted output and carried an explicit
constraint — *the remote collector is not migrated*. That constraint expires here.
Plan 040 is shipped history and is not edited by this plan.

The hosted side is gaining per-agent awareness in parallel. Neither side waits on the
other, there is no compatibility layer, and there is no multi-agent upload guard — see
ADR-0050's rejected alternatives before re-proposing one.

`openaca remote sync endpoint --dry-run` already exists (commit `8f74bff`) and prints
the payloads a sync would send, after redaction and contract enforcement, without
network I/O. It is the before/after instrument for this whole plan.

## Global Constraints

Copied from the spec and ADRs. Every task's requirements implicitly include these.

- **Redaction before writing.** Task 1 lands before Task 2. Task 2 is what starts
  writing agent-derived values into `bom.metadata`; shipping it first opens the hole.
- **The upload writes no `openaca:target`.** Do **not** port `scan endpoint`'s
  `target=str(agent.config_root)` (`tools/scan.py:1317`). The upload passes
  `target=None`. The envelope's `target_locator` stays `endpoint:user-scope`.
- **`openaca:target_type` is not written.** The collector is its last emitter.
- **The document must be graph-backed.** `schema_version` and `metadata.component`
  derive from `target_bom_ref` (`tools/bom.py:90-127`), which is only set on the
  `graph=` path. A collection built without a graph silently emits a legacy `0.4`
  document with no `metadata.component`.
- **One asset, N uploads.** Registration payload, hostname key, and cached `asset_id`
  are unchanged. Nothing is added to the upload envelope.
- **`composition_source` is required and explicit** (`installed` here), never absent.
- **A singleton kind omits `openaca:agent_id`** rather than emitting it empty.
- **`content_hash` is computed after redaction, per payload.**
- **Readers of stored `0.4` documents stay.** `target_info_from_cyclonedx` and
  `component_refs_from_cyclonedx` are untouched — only the writer moves.
- **Default to no comments**; add one only where the *why* is non-obvious.
- **Every task ends green on**
  `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

## File Structure

| File | Responsibility |
|---|---|
| `tools/remote/collector.py` | Agent discovery, one collection per agent, one payload per agent, `metadata` redaction |
| `tools/remote/upload_contract.py` | Contract enforcement extended to `bom.metadata` |
| `tools/remote/cli.py` | Print one upload result per agent |
| `tests/remote/test_redact_payload.py` | Metadata redaction cases |
| `tests/remote/test_upload_contract.py` | Metadata contract-violation cases |
| `tests/remote/test_collect.py` | Per-agent collection and per-agent upload |
| `tests/remote/test_cli.py` | Multi-result output |
| `tests/test_e2e.py` | Cross-layer characterisation of the uploaded shape |
| `docs/reference/cli.md` | `--dry-run` and the upload shape, if the page documents them |

---

## Task 1: Redaction and the upload contract cover `bom.metadata`

Implements ADR-0051. Lands first, before anything writes to metadata.

**Files:**
- Modify: `tools/remote/collector.py` (`_redact_payload_for_remote`, ~line 566)
- Modify: `tools/remote/upload_contract.py` (`_validate_no_absolute_paths`, ~line 90)
- Test: `tests/remote/test_redact_payload.py`, `tests/remote/test_upload_contract.py`

**Interfaces:**
- Consumes: existing `_redact_property_value_for_remote`, `_redact_source_path`,
  `_check_evidence_string_at`.
- Produces: `_redact_property_list(properties, *, config_dir, project) -> None`
  (in-place) in `collector.py`; `_check_openaca_properties(properties, location)` in
  `upload_contract.py`. Task 2 relies on both already covering metadata.

**Scope is three locations, not two.** Per ADR-0051 the boundary is *every string in
`bom.metadata` the collector synthesizes*: `metadata.properties`,
`metadata.component.properties`, and `metadata.component.name`. The name carries no
`openaca:` prefix, so it is covered **by name**, not by the prefix filter — it is
emitted from `AgentInstance.display_name` (`tools/agent_kinds/__init__.py:53-63`), an
unconstrained `str` that is the literal `Claude Code` today only because the one shipped
kind hardcodes it. Omitting it would leave the plan enforcing something narrower than
the spec's invariant claims.

- [ ] **Step 1: Write the failing redaction tests**

```python
# tests/remote/test_redact_payload.py
def test_redacts_absolute_path_in_bom_metadata_properties(tmp_path):
    payload = {
        "bom": {
            "metadata": {
                "properties": [
                    {"name": "openaca:target", "value": str(tmp_path / "skills" / "a.md")}
                ]
            }
        }
    }

    _redact_payload_for_remote(payload, config_dir=tmp_path, project=None)

    assert payload["bom"]["metadata"]["properties"][0]["value"] == "skills/a.md"


def test_redacts_absolute_path_in_metadata_component_properties(tmp_path):
    """The agent properties land here, and `openaca:agent_id` is not
    constrained by the scanner's schema (ADR-0051)."""
    payload = {
        "bom": {
            "metadata": {
                "component": {
                    "bom-ref": "root/example",
                    "properties": [
                        {"name": "openaca:agent_id", "value": str(tmp_path / "agents" / "a")}
                    ],
                }
            }
        }
    }

    _redact_payload_for_remote(payload, config_dir=tmp_path, project=None)

    assert payload["bom"]["metadata"]["component"]["properties"][0]["value"] == "agents/a"


def test_leaves_non_openaca_metadata_properties_untouched(tmp_path):
    """Scope is what the collector synthesized, not pass-through CycloneDX."""
    payload = {
        "bom": {"metadata": {"properties": [{"name": "vendor:path", "value": "/opt/thing"}]}}
    }

    _redact_payload_for_remote(payload, config_dir=tmp_path, project=None)

    assert payload["bom"]["metadata"]["properties"][0]["value"] == "/opt/thing"


def test_redacts_absolute_path_in_metadata_component_name(tmp_path):
    """The agent display label carries no `openaca:` prefix, so the prefix
    filter cannot reach it. It comes from unconstrained
    `AgentInstance.display_name` (ADR-0051)."""
    payload = {
        "bom": {
            "metadata": {
                "component": {
                    "bom-ref": "root/example",
                    "name": str(tmp_path / "agents" / "payments"),
                }
            }
        }
    }

    _redact_payload_for_remote(payload, config_dir=tmp_path, project=None)

    assert payload["bom"]["metadata"]["component"]["name"] == "agents/payments"


def test_leaves_a_slash_bearing_display_label_untouched(tmp_path):
    """Redacting a label must not mangle a legitimate name. The embedded-path
    rule requires the `/` to follow a non-word character, so `my-org/agent`
    is not a path."""
    payload = {"bom": {"metadata": {"component": {"name": "my-org/agent"}}}}

    _redact_payload_for_remote(payload, config_dir=tmp_path, project=None)

    assert payload["bom"]["metadata"]["component"]["name"] == "my-org/agent"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/remote/test_redact_payload.py -k metadata -v`
Expected: the first, second, and fourth FAIL with the absolute path unchanged (the
third and fifth already pass — nothing touches `metadata` yet, so an untouched value
stays untouched; they are the guards that the new walk does not over-reach).

- [ ] **Step 3: Write the failing contract tests**

```python
# tests/remote/test_upload_contract.py
def test_rejects_absolute_path_in_metadata_component_properties():
    payload = {
        "bom": {
            "metadata": {
                "component": {
                    "properties": [{"name": "openaca:agent_id", "value": "/Users/alex/agents/a"}]
                }
            }
        }
    }

    with pytest.raises(RemoteUploadContractError, match=r"metadata\.component"):
        enforce_remote_upload_contract(payload)


def test_rejects_absolute_path_in_bom_metadata_properties():
    payload = {
        "bom": {"metadata": {"properties": [{"name": "openaca:target", "value": "/Users/alex"}]}}
    }

    with pytest.raises(RemoteUploadContractError, match=r"metadata\.properties"):
        enforce_remote_upload_contract(payload)


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("file:///Users/alex/agents/a", r"file://"),
        ("https://host/agents/a", r"path or query"),
        ("https://user:pass@host", r"userinfo"),
    ],
)
def test_rejects_other_metadata_component_property_violations(value, match):
    """`_check_openaca_properties` shares its full rule set (not just the
    absolute-path check) across every call site, including the two new
    metadata locations."""
    payload = {
        "bom": {
            "metadata": {
                "component": {"properties": [{"name": "openaca:agent_id", "value": value}]}
            }
        }
    }

    with pytest.raises(RemoteUploadContractError, match=match):
        enforce_remote_upload_contract(payload)


def test_rejects_absolute_path_in_metadata_component_name():
    """Not `openaca:`-prefixed, so this proves the enforcer reaches the name
    by name rather than by prefix filter (ADR-0051)."""
    payload = {"bom": {"metadata": {"component": {"name": "/Users/alex/agents/payments"}}}}

    with pytest.raises(RemoteUploadContractError, match=r"metadata\.component\.name"):
        enforce_remote_upload_contract(payload)


def test_accepts_a_slash_bearing_display_label():
    """A legitimate label containing a slash is not a path and must pass."""
    payload = {"bom": {"metadata": {"component": {"name": "my-org/agent"}}}}

    enforce_remote_upload_contract(payload)
```

- [ ] **Step 4: Run them and confirm they fail**

Run: `uv run pytest tests/remote/test_upload_contract.py -k metadata -v`
Expected: FAIL with `DID NOT RAISE`, including the three parametrized cases.

- [ ] **Step 5: Extract the property-list walk in `collector.py` and apply it three times**

In `_redact_payload_for_remote`, replace the inlined `for prop in component.get(...)`
loop with a call to a helper, then call the helper for the two metadata locations.

```python
def _redact_property_list(
    properties: object,
    *,
    config_dir: Path,
    project: Path | None,
) -> None:
    if not isinstance(properties, list):
        return
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        prop_name = prop.get("name")
        if not isinstance(prop_name, str) or not prop_name.startswith("openaca:"):
            continue
        value = prop.get("value")
        if not isinstance(value, str):
            continue
        # openaca:source_manifest feeds the graph occurrence key, so redact it
        # identically to the bom-ref path portion (relativize + out-of-root digest).
        if prop_name == "openaca:source_manifest":
            prop["value"] = _redact_source_path(value, config_dir=config_dir, project=project)
        else:
            prop["value"] = _redact_property_value_for_remote(
                value, config_dir=config_dir, project=project
            )
```

Then inside `_redact_payload_for_remote`, after `ref_map = _redact_bom_refs_in_bom(...)`:

```python
        metadata = bom.get("metadata")
        if isinstance(metadata, dict):
            _redact_property_list(
                metadata.get("properties"), config_dir=config_dir, project=project
            )
            component = metadata.get("component")
            if isinstance(component, dict):
                _redact_property_list(
                    component.get("properties"), config_dir=config_dir, project=project
                )
                # The agent's display label, not an `openaca:*` property — the
                # prefix filter cannot reach it, and `display_name` is an
                # unconstrained str (ADR-0051).
                name = component.get("name")
                if isinstance(name, str):
                    component["name"] = _redact_property_value_for_remote(
                        name, config_dir=config_dir, project=project
                    )
        for component in bom.get("components", []) or []:
            if not isinstance(component, dict):
                continue
            _redact_property_list(
                component.get("properties"), config_dir=config_dir, project=project
            )
```

- [ ] **Step 6: Extend `_validate_no_absolute_paths` the same way**

```python
def _check_openaca_properties(properties: object, location: str) -> None:
    if not isinstance(properties, list):
        return
    for index, prop in enumerate(properties):
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        if not isinstance(name, str) or not name.startswith("openaca:"):
            continue
        value = prop.get("value")
        if not isinstance(value, str):
            continue
        at = f"{location}[{index}].value"
        if _is_absolute_path(value):
            raise RemoteUploadContractError(f"{at} is an absolute path ({name!r})")
        if value.lower().startswith("file://"):
            raise RemoteUploadContractError(f"{at} is a file:// URI (local path) ({name!r})")
        if _is_url_with_path_or_query(value):
            raise RemoteUploadContractError(f"{at} is a URL with a path or query ({name!r})")
        if _is_url_with_userinfo(value):
            raise RemoteUploadContractError(
                f"{at} is a URL with credentials in userinfo ({name!r})"
            )
```

Call it for the two metadata locations and for each component, replacing the existing
inlined component loop:

```python
    bom = payload.get("bom")
    if isinstance(bom, dict):
        metadata = bom.get("metadata")
        if isinstance(metadata, dict):
            _check_openaca_properties(
                metadata.get("properties"), "$.bom.metadata.properties"
            )
            component = metadata.get("component")
            if isinstance(component, dict):
                _check_openaca_properties(
                    component.get("properties"), "$.bom.metadata.component.properties"
                )
                # Covered by name, not by prefix filter — see ADR-0051.
                # `_check_evidence_string_at` already applies the full rule set
                # (absolute path, file://, URL path/query, URL userinfo,
                # embedded Unix path) to a bare string.
                name = component.get("name")
                if isinstance(name, str):
                    _check_evidence_string_at(name, "$.bom.metadata.component.name")
        for c_idx, component in enumerate(bom.get("components", []) or []):
            if not isinstance(component, dict):
                continue
            _check_openaca_properties(
                component.get("properties"), f"$.bom.components[{c_idx}].properties"
            )
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/remote/ -q`
Expected: all pass, including the two new metadata tests and every existing
component-property test (the extraction must not change component behaviour).

- [ ] **Step 8: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
git add tools/remote/collector.py tools/remote/upload_contract.py tests/remote/
git commit -m "fix(remote): redact and validate openaca:* properties in bom metadata"
```

---

## Task 2: One agent-rooted collection per discovered agent

**Files:**
- Modify: `tools/remote/collector.py` (`_collect_endpoint_components` ~line 69,
  `build_endpoint_collection` ~line 90, `EndpointCollection` ~line 51)
- Test: `tests/remote/test_collect.py`

**Interfaces:**
- Consumes: `discover_agents`, `DiscoveryContext`, `AgentInstance`,
  `build_agent_graph`, `resolve_coverage`, `kind_for` from `tools.agent_kinds`; `Edge`,
  `Graph`, `Node` and `_stable_bom_refs` in the test module, for `_graph_for_refs` (Step 5).
- Produces:
  - `EndpointCollection` gains `agent: AgentInstance`.
  - `_agent_refs(agent) -> tuple[Graph, list[ComponentRef]]` — the monkeypatch
    seam replacing `_collect_endpoint_components`, same return shape so existing tests
    port with a renamed target.
  - `build_endpoint_collections(*, config_dir, project, external_scanners=()) -> list[EndpointCollection]`.
  Task 3 consumes `build_endpoint_collections`.

**Note on the 27 existing callers.** `build_endpoint_collection` is called by 27 tests
in `tests/remote/test_collect.py`, nearly all asserting on install-source trimming via
`collection.bom["components"][0]`. They port mechanically:
`build_endpoint_collection(config_dir=tmp_path, project=None)` becomes
`build_endpoint_collections(config_dir=tmp_path, project=None)[0]`, and the monkeypatch
target `tools.remote.collector._collect_endpoint_components` becomes
`tools.remote.collector._agent_refs`. Real discovery runs against `tmp_path` and yields
one Claude Code agent because `tmp_path` is a directory. Two assertions need real
edits, not a rename — see Step 5.

- [ ] **Step 1: Add the endpoint fixture helper to the test module**

Do **not** fabricate a `Graph` for this test. There is no production ref-list graph
constructor, and the document must be graph-backed or `target_bom_ref` is `None` and the
BOM silently emits `0.4` with no `metadata.component` (see Global Constraints). Write
real files and let real discovery and real graph-building run — the pattern already
established in `tests/test_graph_build_agent.py:5-23`. (Step 5 adds a minimal
test-only graph fixture for the pre-existing trimming tests, which fake `_agent_refs`
regardless of what builds the real graph — that is a narrower concession than this one
and does not change what `build_endpoint_collections` itself ever does.)

```python
# tests/remote/test_collect.py
def _endpoint_fixture(root: Path) -> Path:
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
```

- [ ] **Step 2: Write the failing per-agent collection test**

```python
def test_build_endpoint_collections_emits_one_agent_rooted_bom_per_agent(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collections = build_endpoint_collections(config_dir=config_dir, project=None)

    assert len(collections) == 1
    assert collections[0].agent.kind_id == "claude-code"
    metadata = collections[0].bom["metadata"]
    props = {p["name"]: p["value"] for p in metadata["properties"]}
    assert props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in props
    assert "openaca:target" not in props
    assert metadata["component"]["bom-ref"] == "root/claude-code"
    component_props = {p["name"]: p["value"] for p in metadata["component"]["properties"]}
    assert component_props["openaca:agent_kind"] == "claude-code"
    assert component_props["openaca:composition_source"] == "installed"
    assert "openaca:agent_id" not in component_props
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `uv run pytest tests/remote/test_collect.py -k one_agent_rooted -v`
Expected: FAIL with `ImportError: cannot import name 'build_endpoint_collections'`.

- [ ] **Step 4: Implement discovery and per-agent collection**

```python
def _agent_refs(agent: AgentInstance) -> tuple[Graph, list[ComponentRef]]:
    graph = build_agent_graph(agent)
    all_refs = [
        replace(
            node.ref,
            scope=graph.scope_of(node),
            extra={**(node.ref.extra or {}), "bom_ref": node.key},
        )
        for node in graph.nodes.values()
        if node.ref is not None
    ]
    return graph, [r for r in all_refs if r.scope in _AGENT_SCOPES]


def build_endpoint_collections(
    *,
    config_dir: Path,
    project: Path | None,
    external_scanners: tuple[str, ...] = (),
) -> list[EndpointCollection]:
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=config_dir, project_root=project)
    )
    return [
        _build_agent_collection(agent, external_scanners=external_scanners) for agent in agents
    ]


def _build_agent_collection(
    agent: AgentInstance,
    *,
    external_scanners: tuple[str, ...],
) -> EndpointCollection:
    graph, refs = _agent_refs(agent)
    bom = _prepare_remote_bom(
        build_agent_bom(
            refs,
            # Not the scan path's `str(agent.config_root)`: that is an absolute
            # path, correct locally under ADR-0003 and a redaction-contract
            # violation across the upload boundary. The upload names no place.
            target=None,
            source_unit_count=sum(1 for ref in refs if _is_plugin_ref(ref)),
            source_unit_label="active plugin",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(agent.coverage_baseline, evidence_gaps=0),
        ).to_cyclonedx()
    )
    mcp_collector, settings_collector = kind_for(agent.kind_id).installed_posture_collectors or (
        _no_manifests,
        _no_manifests,
    )
    mcp_manifests = mcp_collector(agent.config_root, agent.project_root, refs)
    settings_manifests = settings_collector(agent.config_root, agent.project_root)
    posture_findings = [
        _posture_finding_to_payload(replace(f, agent_kind=agent.kind_id, agent_id=agent.agent_id))
        for f in run_posture_rules(refs, mcp_manifests, settings_manifests)
    ]
    observations, scanner_posture = _collect_scanner_findings(
        refs, external_scanners=external_scanners
    )
    posture_findings.extend(
        _posture_finding_to_payload(replace(f, agent_kind=agent.kind_id, agent_id=agent.agent_id))
        for f in scanner_posture
    )
    return EndpointCollection(
        agent=agent,
        bom=bom,
        posture_findings=posture_findings,
        observations=[
            _observation_to_payload(
                replace(o, agent_kind=agent.kind_id, agent_id=agent.agent_id)
            )
            for o in observations
        ],
        component_count=len(bom.get("components") or []),
    )


def _no_manifests(*_args: object, **_kwargs: object) -> list[tuple[Path, dict]]:
    return []
```

Add `agent: AgentInstance` to `EndpointCollection`. Update the test-only `_collection()`
factory (`tests/remote/test_collect.py`, used by the existing `collect_endpoint` and
dry-run tests) in the same commit — it is the only direct `EndpointCollection(...)`
construction in the suite, so it must gain an `agent` argument now or every test that
calls it breaks immediately, before Task 3 touches anything:

```python
def _collection(
    *, agent_kind: str = "claude-code", bom: dict[str, Any] | None = None
) -> EndpointCollection:
    return EndpointCollection(
        agent=AgentInstance(
            kind_id=agent_kind,
            display_name=agent_kind,
            source="installed",
            root_label=agent_kind,
            coverage_baseline="full",
        ),
        bom=bom
        or {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "components": [],
            "metadata": {"component": {"bom-ref": f"root/{agent_kind}"}},
        },
        posture_findings=[
            {
                "rule_id": "openaca-posture-insecure-transport",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "summary": "Insecure transport",
                "fix": "Use https.",
                "evidence": {"transport": "http", "manifest_path": ".mcp.json"},
            }
        ],
        observations=[],
        component_count=0,
    )
```

The default `agent_kind="claude-code"` keeps every existing zero-argument `_collection()`
call site unchanged. Task 3's multi-agent tests pass `agent_kind="other"` for the second
collection so it differs in `bom` (and therefore `content_hash`) and carries a distinct
`agent.bom_ref` for failure reporting.

Leave `_collect_endpoint_components`
and `build_endpoint_collection` in place for now — `collect_endpoint` and
`build_endpoint_dry_run_payloads` still call the singular builder until Task 3 rewires
them, and deleting it here would leave the tree red between commits. Task 3 deletes both,
along with `TARGET_LOCATOR_ENDPOINT`'s use as a BOM `target` (the constant itself stays —
Task 3 still sends it as the envelope's `target_locator`), once nothing calls the
singular builder.

`evidence_gaps=0` matches the collector's current behaviour: it collects no graph-build
warnings today. If `build_agent_graph`'s `warnings` list is threaded through later,
pass `len(warnings)` instead.

- [ ] **Step 5: Port the 27 existing callers**

Mechanical for 25 of them:

```bash
# in tests/remote/test_collect.py
#   build_endpoint_collection(  ->  build_endpoint_collections(
#   ...)                        ->  ...)[0]
#   "tools.remote.collector._collect_endpoint_components" -> "tools.remote.collector._agent_refs"
#   lambda *args: (None, [ref])                            -> lambda *args: (_graph_for_refs([ref]), [ref])
```

Two need real edits because they assert the shape this task changes:

- `test_build_endpoint_collection_uses_endpoint_bom_and_posture_engine` asserts
  `collection.bom["metadata"]["properties"][1] == {...}` (index-based) and
  `{"name": "openaca:target", "value": "endpoint:user-scope"} in ...`. Replace both with
  a name-keyed dict assertion that `openaca:target` and `openaca:target_type` are
  **absent** and `openaca:schema_version == "0.5"`.
- Any test asserting `target_type` is present must assert it is absent.

`_agent_refs` is typed `tuple[Graph, list[ComponentRef]]` — not `Graph | None` — because
`build_endpoint_collections` must never emit the graphless, `metadata.component`-less
shape (Global Constraints: "the document must be graph-backed"). The trimming tests keep
faking `_agent_refs`, but with a real minimal graph rather than `None`, so the fake never
produces a shape the public builder is forbidden from returning:

```python
# tests/remote/test_collect.py
def _graph_for_refs(refs: list[ComponentRef]) -> Graph:
    """A minimal graph-backed root for tests that only exercise `_prepare_remote_bom`'s
    trimming behaviour on `bom["components"]`. Reuses `_stable_bom_refs` for the node
    keys so component `bom-ref` values are byte-identical to what these tests already
    assert on — this is a test fixture, not a second bom-ref algorithm."""
    root = Node(key="root/claude-code", kind="target", ref=None)
    nodes: dict[str, Node] = {root.key: root}
    edges: list[Edge] = []
    for key, ref in zip(_stable_bom_refs(refs), refs, strict=True):
        nodes[key] = Node(key=key, kind="plugin", ref=ref)
        edges.append(Edge(parent=root.key, child=key))
    return Graph(nodes=nodes, edges=edges)
```

Import `Edge`, `Graph`, `Node` from `tools.graph` and `_stable_bom_refs` from `tools.bom`.
Then the monkeypatch fakes become `lambda *args: (_graph_for_refs([ref]), [ref])` instead
of `lambda *args: (None, [ref])` — mechanical across all 27 callers, since only the first
tuple element changes. Those tests still assert only on `bom["components"][0]["properties"]`
(unaffected by the switch — component properties come from the ref, not the graph), while
`bom-ref` assertions like `collection.bom["components"][0]["bom-ref"] ==
"mcp-server/npm/@playwright/mcp"` keep passing because `_graph_for_refs` computes the same
key `_stable_bom_refs` would have. Only tests asserting on `metadata.component` need the
real fixture from Step 1.

- [ ] **Step 6: Run the full remote suite**

Run: `uv run pytest tests/remote/ -q`
Expected: all pass.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
git add tools/remote/collector.py tests/remote/test_collect.py
git commit -m "feat(remote): collect one agent-rooted BOM per discovered agent"
```

---

## Task 3: One upload per agent

**Files:**
- Modify: `tools/remote/collector.py` (`collect_endpoint` ~line 146,
  `build_endpoint_dry_run_payloads`)
- Modify: `tools/remote/cli.py` (`endpoint`, `_print_upload_result`, `_dry_run_endpoint`)
- Test: `tests/remote/test_collect.py`, `tests/remote/test_cli.py`

**Interfaces:**
- Consumes: `build_endpoint_collections` from Task 2, `_prepare_upload_payload`.
- Produces: `collect_endpoint(...) -> list[BomUploadResult]`.

- [ ] **Step 1: Write the failing multi-upload test**

Add `RemoteValidationError` to the existing `from tools.remote.client import (...)` import
in `tests/remote/test_collect.py` (`RemoteAuthError` is already imported there).

```python
def test_collect_endpoint_uploads_one_payload_per_agent(tmp_path, monkeypatch):
    """Same asset_id in every envelope; the agent is named inside the
    document (ADR-0050)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None)

    assert len(results) == 2
    assert [u["asset_id"] for u in uploads] == ["asset-123", "asset-123"]
    assert [u["target_locator"] for u in uploads] == ["endpoint:user-scope"] * 2
    assert uploads[0]["content_hash"] != uploads[1]["content_hash"]


def test_collect_endpoint_caches_only_the_failing_agent(tmp_path, monkeypatch):
    """A network failure on one agent must not discard the others (ADR-0050)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            self.calls = 0

        def upload_bom(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert len(results) == 1
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_attempts_every_agent_by_default_and_names_the_failed_one(
    tmp_path, monkeypatch
):
    """Default mode (neither `--quiet` nor `--allow-offline-cache`) must still
    attempt every discovered agent after an earlier one fails on the network,
    and the raised error must identify which agent(s) it could not upload
    (spec: "reports which ones it could not"; ADR-0050: per-agent independence)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 2  # the second agent was still attempted
    assert excinfo.value.exit_code == 2
    assert "root/claude-code" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_warns_and_returns_empty_when_no_agent_discovered(
    tmp_path, monkeypatch
):
    """Matches `scan endpoint`'s convention (`tools/scan.py`) for the same
    condition, rather than leaving the outcome of zero discovered agents
    unspecified."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr("tools.remote.collector.build_endpoint_collections", lambda **kwargs: [])

    results = collect_endpoint(config_dir=tmp_path, project=None)

    assert results == []


def test_collect_endpoint_attempts_every_agent_after_multiple_network_failures(
    tmp_path, monkeypatch
):
    """Two retryable failures in a three-agent sync must not stop at the
    first or second — every agent is still attempted, and every failure is
    cached and named."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [
            _collection(agent_kind="claude-code"),
            _collection(agent_kind="other"),
            _collection(agent_kind="third"),
        ],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) in (1, 3):
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 3  # every agent was attempted, including after the second failure
    assert excinfo.value.exit_code == 2
    assert "root/claude-code" in str(excinfo.value)
    assert "root/third" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 2


def test_collect_endpoint_continues_past_a_rejected_agent_without_caching_it(
    tmp_path, monkeypatch
):
    """A 422 or 413 rejects one agent's document, not the connection or the
    token — the next agent's document is unrelated and must still be
    attempted, and the rejected one is not cached (`--allow-offline-cache`'s
    own scope is a pending cache file, and retrying an invalid payload
    unchanged would only be rejected again)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                raise RemoteValidationError("document too large for one agent", [])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert len(uploads) == 2  # the second agent was still attempted
    assert excinfo.value.exit_code == 1  # not suppressed by --allow-offline-cache: nothing was cached
    assert "root/claude-code" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert list(pending_dir.glob("pending-bom-*.json")) == []


def test_collect_endpoint_names_both_a_rejected_and_a_cached_agent_together(
    tmp_path, monkeypatch
):
    """A rejection and a network failure in the same sync must not lose one
    of them: the rejected list short-circuiting past the cached list would
    silently drop whichever agent it didn't raise about. `--quiet` is set
    here specifically because it suppresses the per-agent echoes above —
    the final exception is the only place left for either agent's name to
    appear, so it must name both."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [
            _collection(agent_kind="claude-code"),
            _collection(agent_kind="other"),
            _collection(agent_kind="third"),
        ],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                return _upload_result(asset_id=payload["asset_id"])
            if len(uploads) == 2:
                raise RemoteValidationError("document too large for one agent", [])
            raise httpx.ConnectError("down")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None, quiet=True)

    assert len(uploads) == 3  # every agent was attempted despite the rejection
    assert excinfo.value.exit_code == 1  # a rejection is present, so not suppressed
    assert "root/claude-code" not in str(excinfo.value)  # the succeeding agent
    assert "root/other" in str(excinfo.value)  # rejected
    assert "root/third" in str(excinfo.value)  # cached
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_aborts_on_auth_failure_without_attempting_later_agents(
    tmp_path, monkeypatch
):
    """One token authenticates every upload in a sync; a rejected token will
    reject every remaining agent too, so this stays a global, immediate
    abort rather than a per-agent failure (unlike the network/validation
    cases above, which keep attempting the remaining agents)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            raise RemoteAuthError("invalid or revoked token")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 1  # the second agent was never attempted
    assert excinfo.value.exit_code == 1
    assert str(excinfo.value) == "invalid or revoked token"
```

`_collection(agent_kind=...)` is the helper Task 2 already extended; passing a distinct
`agent_kind` for the second collection gives it a distinct `bom` (and therefore
`content_hash`) and a distinct `agent.bom_ref` to assert on in failure messages.

- [ ] **Step 2: Run and confirm they fail**

Run: `uv run pytest tests/remote/test_collect.py -k "per_agent or failing_agent or failed_one or no_agent_discovered or multiple_network_failures or rejected_agent or aborts_on_auth_failure or rejected_and_a_cached_agent" -v`
Expected: FAIL — `collect_endpoint` returns a single `BomUploadResult`, so
`len(results)` raises `TypeError` on every new test.

- [ ] **Step 3: Discover per agent and loop the upload in `collect_endpoint`**

Replace the `if external_scanners: collection = build_endpoint_collection(...)` branch
with a single plural call — `build_endpoint_collections` already defaults
`external_scanners=()`, so the branch is no longer needed:

```python
    collections = build_endpoint_collections(
        config_dir=config_dir, project=project, external_scanners=external_scanners
    )
    if not collections:
        click.echo("no installed agent found", err=True)
```

The echo matches `scan endpoint`'s existing convention for the same condition
(`tools/scan.py`) rather than leaving it unspecified — the upload path stays consistent
with the local scan path here too. `asset_id` resolution (registration) is unchanged and
still runs even when `collections` is empty, so a machine with no discoverable agent yet
still registers.

Add `RemoteAuthError`, `RemotePayloadTooLargeError`, and `RemoteValidationError` to the
existing `from tools.remote.client import (...)` import at the top of `collector.py`
(only `RemoteClientError` and `RemoteServerError` are imported today).

Then replace the single-payload tail with a loop that attempts every collection and
classifies a failure by what it actually tells you about the *other* agents, not just by
HTTP status:

- `RemoteAuthError` is global: the same token authenticates every upload in this loop, so
  a rejected token will reject every remaining agent too. Abort immediately — continuing
  only spends requests to relearn the same fact.
- `RemoteServerError` and `httpx.TransportError` are transient and not agent-specific:
  cache the payload for retry and keep going.
- Every other `RemoteClientError` (`RemoteValidationError`, `RemotePayloadTooLargeError`,
  or an unclassified 4xx) describes this agent's document, not the connection or the
  token — the next agent's document is unrelated and still worth attempting. It is not
  cached: it was rejected as invalid, and retrying it unchanged would only be rejected
  again.

This still matches the spec's "uploads every agent it discovered, or reports which ones
it could not" (`docs/specs/collector-agent-rooted-uploads.md:131`) and ADR-0050's
per-agent independence — auth is the one failure mode that is not actually per-agent, so
treating it as global is what keeps the other two categories' per-agent guarantee
truthful rather than diluting it.

`quiet` and `allow_offline_cache` keep their current, narrower scope
(`--allow-offline-cache`'s own help text: "Exit zero when upload fails after writing a
pending cache file") — they control only whether the *cached*-failure aggregate is
raised, never whether a rejected (uncached) failure is raised, and never which agents are
attempted:

```python
    results: list[BomUploadResult] = []
    cached_failed_agents: list[str] = []
    rejected_failed_agents: list[str] = []
    for collection in collections:
        payload = _prepare_upload_payload(
            asset_id=asset_id,
            collection=collection,
            config_dir=config_dir,
            project=project,
        )
        try:
            results.append(client.upload_bom(payload))
        except RemoteAuthError as exc:
            raise CollectError(str(exc)) from exc
        except (RemoteServerError, httpx.TransportError) as exc:
            path = _write_pending_payload(payload)
            cached_failed_agents.append(collection.agent.bom_ref)
            if not quiet:
                click.echo(
                    f"saved to {path}; upload failed for {collection.agent.bom_ref} (network)",
                    err=True,
                )
        except RemoteClientError as exc:
            rejected_failed_agents.append(f"{collection.agent.bom_ref} ({exc})")
            if not quiet:
                click.echo(f"upload rejected for {collection.agent.bom_ref}: {exc}", err=True)
    if rejected_failed_agents or (cached_failed_agents and not (quiet or allow_offline_cache)):
        messages = []
        if rejected_failed_agents:
            messages.append(f"upload rejected for: {'; '.join(rejected_failed_agents)}")
        if cached_failed_agents:
            messages.append(
                f"upload failed for: {', '.join(cached_failed_agents)} (network); cached for retry"
            )
        raise CollectError(
            "; ".join(messages),
            exit_code=1 if rejected_failed_agents else 2,
        )
    return results
```

A rejected-agent failure always raises (default `CollectError` exit code 1, matching
today's single-shot `RemoteClientError` behavior) even when `--quiet` or
`--allow-offline-cache` is set — `quiet` only suppresses the per-agent echo above, not the
final exit code, exactly as it already does for the pre-existing single-upload
`RemoteClientError` path. The two categories share one final diagnostic rather than the
rejection short-circuiting before the cached list is ever read: a rejected agent and a
cached agent can both go unmentioned anywhere else (the per-agent echoes above are the
only other place either is named, and `--quiet` suppresses those), so the raised message
is the last chance to name every failed agent, not just the one that happens to raise
first. When only cached failures occur and `quiet`/`allow_offline_cache` is set, nothing
raises, exactly as before — those flags still gate only the cached category, never the
rejected one. Either way, the cached failures stay written to the pending cache and are
retried on the next sync regardless of which message surfaces this run.

- [ ] **Step 4: Update the CLI to print one block per result**

```python
def _print_upload_result(result) -> None:
    click.echo(f"Uploaded BOM: {result.bom_id}")
    ...


def _print_upload_results(results) -> None:
    for index, result in enumerate(results):
        if index:
            click.echo("")
        _print_upload_result(result)
```

Call `_print_upload_results(results)` from `endpoint`.

- [ ] **Step 5: Point the dry run at the plural builder**

In `build_endpoint_dry_run_payloads`, replace the single
`build_endpoint_collection(...)` call with `build_endpoint_collections(...)` and return
one prepared payload per collection. The NDJSON printer in `_dry_run_endpoint` already
loops and needs no change.

- [ ] **Step 6: Port every existing test that assumed a singular collection, then delete
  the singular builder**

Steps 3 and 5 just removed the last production callers of `build_endpoint_collection`
and `_collect_endpoint_components`. Before deleting them, port every existing test in
`tests/remote/test_collect.py` and `tests/remote/test_cli.py` that still targets the
singular shape — both the ones asserting on `collect_endpoint`'s return value and the
ones monkeypatching the singular builder to control what a `collect_endpoint`/dry-run
test sees:

```bash
# in tests/remote/test_collect.py and tests/remote/test_cli.py
#   result = collect_endpoint(...)          ->  results = collect_endpoint(...)
#   result.<attr>                            ->  results[0].<attr>
#   "tools.remote.collector.build_endpoint_collection" -> "tools.remote.collector.build_endpoint_collections"
#   lambda **kwargs: _collection(...)        ->  lambda **kwargs: [_collection(...)]
#   _fake_collection (used directly as the target) -> lambda **kwargs: [_fake_collection(**kwargs)]
```

Mechanical throughout — no test in either file asserts on more than one collection
except the ones Step 1 added, which already target the plural API. Once every caller is
ported, delete `_collect_endpoint_components`, `build_endpoint_collection`, and
`TARGET_LOCATOR_ENDPOINT`'s use as a BOM `target` (the constant itself stays — it is
still sent as the envelope's `target_locator`, just never written into `bom.metadata`
after Task 1).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: 1602+ pass.

- [ ] **Step 8: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
git add tools/remote/ tests/remote/
git commit -m "feat(remote): upload one BOM per agent against one registered asset"
```

---

## Task 4: End-to-end characterisation and docs

**Files:**
- Modify: `tests/test_e2e.py`
- Modify: `docs/reference/cli.md` (only if it documents the upload shape or `--dry-run`)

**Interfaces:** consumes the finished collector; produces no new API.

Per `CLAUDE.md`, this test belongs in `tests/test_e2e.py` because it fails if the
collector, the agent registry, the BOM emitter, or the redaction layer regresses.

- [ ] **Step 1: Write the failing cross-layer test**

```python
# tests/test_e2e.py
def test_remote_upload_payload_is_agent_rooted_and_redacted(tmp_path):
    """The uploaded document is agent-rooted, names no place, and carries no
    absolute path — asserted by running the real contract enforcer over it
    rather than by a bespoke walk."""
    config_dir = tmp_path / ".claude"
    (config_dir / "skills" / "demo").mkdir(parents=True)
    (config_dir / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n", encoding="utf-8"
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=config_dir, project=None)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["target_locator"] == "endpoint:user-scope"
    metadata = payload["bom"]["metadata"]
    props = {p["name"]: p["value"] for p in metadata["properties"]}
    assert props["openaca:schema_version"] == "0.5"
    assert "openaca:target" not in props
    assert "openaca:target_type" not in props
    component = metadata["component"]
    assert component["bom-ref"] == "root/claude-code"
    agent_props = {p["name"]: p["value"] for p in component["properties"]}
    assert agent_props["openaca:agent_kind"] == "claude-code"
    assert agent_props["openaca:composition_source"] == "installed"
    assert "openaca:agent_id" not in agent_props
    enforce_remote_upload_contract(payload)  # raises if any absolute path survived
    # The enforcer's scope is itself part of what this plan changes, so calling
    # it alone would be self-referential. Assert the synthesized metadata
    # strings directly, independent of the enforcer's own idea of its scope.
    synthesized = [component["name"], *(p["value"] for p in component["properties"])]
    synthesized += [p["value"] for p in metadata["properties"]]
    assert not [s for s in synthesized if s.startswith("/") or s.startswith("file://")]
    assert str(tmp_path) not in json.dumps(payload)
```

- [ ] **Step 2: Run and confirm it fails before Tasks 1-3, passes after**

Run: `uv run pytest tests/test_e2e.py -k agent_rooted_and_redacted -v`
Expected: PASS once Tasks 1-3 are merged. If it fails, the failure names which layer
regressed.

- [ ] **Step 3: Verify against the real machine**

```bash
uv run openaca remote sync endpoint --dry-run | jq -r '
  (.bom.metadata.component["bom-ref"]),
  (.bom.metadata.properties[] | select(.name=="openaca:schema_version") | .value),
  ([.bom.metadata.properties[].name] | join(","))'
```

Expected: `root/claude-code`, `0.5`, and a property list containing neither
`openaca:target` nor `openaca:target_type`.

- [ ] **Step 4: Update the CLI reference page if it documents the upload shape**

Check `docs/reference/cli.md` for `remote sync endpoint`. Document `--dry-run` and the
one-upload-per-agent behaviour if the page covers that surface; skip if it does not.

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py docs/
git commit -m "test(e2e): characterise the agent-rooted upload payload"
```

---

## Verification

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

Then the real before/after, which no fixture can substitute for:

```bash
uv run openaca remote sync endpoint --dry-run > after.json
# Before this plan, the same command printed 0.4 / openaca:target / target_type: endpoint.
python3 -c "
import json; p=json.load(open('after.json'))
m=p['bom']['metadata']; props={x['name']:x['value'] for x in m['properties']}
assert props['openaca:schema_version']=='0.5', props
assert 'openaca:target' not in props and 'openaca:target_type' not in props, props
assert m['component']['bom-ref']=='root/claude-code', m['component']
print('ok — agent-rooted, no place named')
"
```

**Manual, externally coordinated — not a locally reproducible gate.** Finally, a real
sync against a development hosted instance, confirming the row lands with
`root_bom_ref = "root/claude-code"` and the console still resolves current state. This
depends on hosted-side behavior that is out of this repository's scope (see Out of
scope), so it is a useful manual check before shipping, not a step `uv run pytest` or
this plan's own verification commands can enforce.

## Invariants to protect

Run an adversarial pass against these before marking the plan complete; add a test or
state explicitly why each was verified another way.

- The uploaded BOM writes no `openaca:target`; the envelope's `target_locator` is
  unchanged. **Needs a test that fails against a verbatim port of the scan call site.**
- No absolute path reaches the wire from `metadata` or `metadata.component`.
- `openaca:composition_source` is present and explicit; a singleton omits
  `openaca:agent_id` rather than emitting it empty.
- The emitted document is graph-backed — `schema_version` is `0.5`, not silently `0.4`.
- `content_hash` is computed after redaction, per payload.
- One registration, N uploads, one shared `asset_id`.
- A network failure on one agent does not discard the others.
- Stored `0.4` readers are untouched.
- `--dry-run` performs no network I/O, including registration.

## Out of scope

- **Hosted-side re-rooting.** Built in parallel; not coordinated through this plan.
- **Any second agent kind.** N is 1 until one ships; the shape is what changes.
- **A multi-agent upload guard or `/api/v1/me` capability negotiation.** Rejected in
  ADR-0050 — read it before re-proposing.
- **Retiring `build_graph`'s `mode` parameter.** Task 3 removes its last live caller
  (`_collect_endpoint_components`), which makes the removal possible. Doing it is a
  separate cleanup with its own blast radius.
- **Editing plan 040.** Shipped history; its collector constraint was true when written.

## Self-review — spec coverage

| Spec section | Task |
|---|---|
| Discovery replaces the mode string | Task 2 |
| Posture and observations resolve per agent | Task 2 |
| One asset, N uploads | Task 3 |
| The privacy contract extends to BOM metadata | Task 1 |
| The upload writes no target | Task 2 (emitter), Task 4 (characterisation) |
| Invariants to protect | Invariants section, exercised across Tasks 1-4 |
| Verification (`--dry-run` before/after) | Task 4 Step 3, Verification section |

**Placeholder scan:** no TBDs, no "add error handling", no "similar to Task N", no
referenced-but-undefined symbols. Every code step carries the code.

**Type consistency check:** `build_endpoint_collections` returns
`list[EndpointCollection]` in Task 2 and is consumed under that name in Task 3 and the
dry-run path; `_agent_refs` returns `tuple[Graph, list[ComponentRef]]` in Task 2 and is
monkeypatched with that shape in the ported trimming tests;
`collect_endpoint` returns `list[BomUploadResult]` in Task 3 and the CLI's
`_print_upload_results` consumes a list. `EndpointCollection.agent` is added in Task 2,
where the test-only `_collection()` factory is updated in the same commit so the field is
never optional; Task 3 reads `collection.agent.bom_ref` for per-agent failure reporting.
The singular `build_endpoint_collection`/`_collect_endpoint_components` stay in place
through Task 2 and are only deleted in Task 3, once their last two production callers
(`collect_endpoint`, `build_endpoint_dry_run_payloads`) are switched to the plural form —
so no task commits with a dangling call to a function it just deleted.
