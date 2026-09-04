# Plan 045 — Published consumption surfaces

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A program can ask OpenACA *what this machine is running and what is
wrong with it* through **one** public function, compile a policy it already
holds in memory for this endpoint through the facade, and offer `scan`, `bom`
and `policy` under its own command line — without importing `tools.*` and
without driving a subprocess. No command changes, no flag is added or removed,
and nothing is deleted.

**Architecture:** Purely additive, and layered downward. Collection is the
~150 lines that today sit inside the in-tree uploader; it moves *down* into
`tools/collect.py` (not up into a new abstraction, and not left in
`tools/remote/`, which the companion spec removes), and the uploader is rewired
to call it, keeping the upload-specific parts — install-source trimming, the
payload-vocabulary mapping, the upload-deferred rule filter, `target=None`, and
the `CollectError` exit-code carrier — on its own side of the boundary. Policy compilation moves the same direction: out
of `tools/policy_cli.py` into `tools/policy_compile.py`, with both the command
and the facade importing it from there, so the library surface stops depending
on the command layer. Posture-surface resolution moves out of the uploader into
`tools/posture/`, where it belonged before an uploader happened to need it. The
facade then re-exports; every new `openaca/core/*` module stays a thin
re-export, as ADR-0028 requires. `openaca/cli.py` re-exports the Click group
`tools/cli.py` already builds — one module, one name.

**Spec:** `docs/specs/published-consumption-surfaces.md`.

**ADRs:** 0028, 0049, 0050, 0054, 0061, 0062. **No new ADR.** The spec argues
these additions are already governed by ADR-0028 and this plan agrees: every
new name is a re-export of existing domain logic through the seam ADR-0028
established, under the same explicit no-pre-V0-stability terms, and ADR-0062
already did exactly this for the policy parser and evaluators. The one addition
that is not an `openaca.core` re-export — publishing the Click group — promises
only that the group exists and that three commands are reachable on it by name
(*What this does and does not promise*), which is weaker than what ADR-0028
already grants; an ADR becomes owed the day someone wants to harden that into a
guarantee about a command's options or output, and that is not this plan.

## Constraints

- [x] **Nothing is deleted.** `openaca remote`, `tools/remote/`, the `httpx`
  dependency, `tests/remote/` and every symbol they use still exist and still
  work when this plan is done. A task that seems to require a deletion belongs
  to the companion removal plan, not here.
- [x] **The full suite passes at every commit**, `tests/remote/` included. Where
  a relocation breaks a `monkeypatch.setattr` target, the same commit updates
  the patch target — it does not drop the test.
- [x] **No new command and no new flag.** In particular, no flag that makes
  `openaca scan endpoint` emit the Agent BOM it already builds.
- [x] `openaca policy validate|compile` take the same arguments, write the same
  artifact, print the same report and exit with the same code before and after.
  Same for `openaca scan *`, `bom *`, `lint`, `export`, `promote`, `seed`,
  `triage`, `remote *`.
- [x] **Exactly seven collection names** — five entry points (the function, the
  result type, the two finding types and the one exception the call can raise)
  plus the kind-selection check and its error, which govern the function's own
  two arguments (Task 12) — plus `Standards` as the type a `PostureFinding`
  field already exposes. The nineteen
  internal symbols enumerated in Task 7 are **not** reachable through
  `openaca.core`, and
  `tools/scan.py`'s two private counters **stay private** — the collection
  function calls them itself, which is what stops them being a problem.
- [x] Findings are returned as `PostureFinding` / `ObservationFinding`
  dataclasses. No payload-vocabulary mapping (`rule_id`→`finding_id`,
  `title`→`summary`, `remediation`→`fix`) crosses into the library.
- [x] Each result carries **its own agent's** `config_root`, never the
  `config_dir` argument.
- [x] `include_target` defaults to `True`, so the CLI paths are unchanged and
  the uploader passes `False` explicitly.
- [x] `openaca/core/*` must not import `tools/policy_cli.py`, `tools/bom_cli.py`
  or `tools/cli.py`.
- [x] No `host` parameter on `compile_endpoint_policy`, and a comment where the
  second host compiler would be written saying when that answer expires.

## File structure

**New:**

| File | Responsibility |
|---|---|
| `tools/posture/agent_surface.py` | `agent_posture_manifests`, `agent_extra_posture_manifests` — resolve an installed kind's posture surface |
| `tools/collect.py` | `CollectedAgent`, `collect_installed_agents`, `collect_for_agent`, `ScannerUnavailable` |
| `tools/atomic_write.py` | `write_new_temp_file` — the symlink-safe temp write, below the command layer |
| `tools/policy_compile.py` | `compile_endpoint_policy`, `render_policy_report`, endpoint evaluation, report shaping, artifact write |
| `tools/kind_selection.py` | `validate_kind_selection`, `KindSelectionError` — which `(kind, config_dir)` pairs are legal, below the command layer |
| `openaca/core/collect.py` | Facade re-export of the collection function, result type and `ScannerUnavailable` |
| `openaca/core/findings.py` | Facade re-export of `PostureFinding`, `ObservationFinding`, `Standards` |
| `openaca/core/kind_selection.py` | Facade re-export of the kind-selection check and its error |
| `openaca/cli.py` | Re-export of the `tools.cli:main` Click group |
| `tests/test_collect.py` | Unit tests for `tools/collect.py` |
| `tests/test_policy_compile.py` | Unit tests for the relocated compilation module |
| `tests/test_kind_selection.py` | Direct-function, CLI-parity and live-registry tests for the relocated validation |
| `tests/test_openaca_cli.py` | The Click-group promise, including mounting a command under another group |

**Modified:**

| File | Change |
|---|---|
| `tools/remote/collector.py` | Imports collection from `tools/collect.py` and posture-surface resolution from `tools/posture/agent_surface.py`; keeps the payload mapping, `_prepare_remote_bom`, `_UPLOAD_DEFERRED_RULES`, and keeps owning `CollectError`, which it now raises by wrapping `ScannerUnavailable` |
| `tools/policy_cli.py` | Imports `compile_endpoint_policy` / `render_policy_report`; keeps the `--output` check, the `click.echo`, the `--project` note and the exception translation |
| `tools/bom_cli.py` | `_write_new_temp_file` becomes a re-export of `tools.atomic_write.write_new_temp_file` |
| `tools/cli_kind.py` | Keeps `kind_option` and `require_kind_for_config_dir`; the latter becomes the adapter translating `KindSelectionError` into its `ClickException` |
| `openaca/core/identity.py` | Three install-source re-exports |
| `openaca/core/policy.py` | `compile_endpoint_policy`, `render_policy_report` |
| `openaca/core/__init__.py` | Thirteen new names in the import block and `__all__` |
| `tests/test_posture_cursor.py` | Imports posture-surface resolution from the posture package |
| `tests/remote/test_collect.py`, `tests/remote/test_cli.py` | Patch targets follow the relocated functions |
| `tests/test_core_facade.py` | Identity, findings, collection, policy and kind-selection re-exports are identical objects |
| `tests/test_e2e.py` | One cross-layer collection test |
| `docs/reference/cli.md` | The published import paths |

---

## Task 1: Install-source re-exports on the identity facade

**Files:** Modify `openaca/core/identity.py`, `openaca/core/__init__.py`;
extend `tests/test_core_facade.py`.

The smallest slice, and the one with no relocation in it — land it first so the
facade-identity test pattern is in place before anything moves.

- [x] **Step 1: Write the failing test.** In `tests/test_core_facade.py`, assert
  `core.is_mcp_package_launch_install_source is tools.identity.is_mcp_package_launch_install_source`,
  and the same for `safe_unpinned_mcp_install_source` and
  `safe_pinned_mcp_install_source`. Note in the test that
  `tools/component_ref.py` re-exports `safe_pinned_mcp_install_source` too —
  the facade re-exports from `tools/identity.py`, which is where it is defined,
  so the identity module stays the single implementation.
- [x] **Step 2: Run, confirm `AttributeError`.**
- [x] **Step 3: Add the three names** to `openaca/core/identity.py`'s import,
  its `__all__`, and `openaca/core/__init__.py`'s import block and `__all__`.
  Keep `__all__` alphabetically sorted, as it is today.
- [x] **Step 4: Run the full suite. Commit.**

## Task 2: The finding value types join the facade

**Files:** Create `openaca/core/findings.py`; modify
`openaca/core/__init__.py`; extend `tests/test_core_facade.py`.

**Produces:** `PostureFinding`, `ObservationFinding`, `Standards` on
`openaca.core`. Task 5 returns them; this task publishes them, so the
collection result has somewhere to point.

- [x] **Step 1: Write the failing test** asserting
  `core.PostureFinding is tools.posture.finding.PostureFinding`,
  `core.Standards is tools.posture.finding.Standards`, and
  `core.ObservationFinding is tools.observations.finding.ObservationFinding`.
- [x] **Step 2: Run, confirm failure.**
- [x] **Step 3: Create `openaca/core/findings.py`** as a thin re-export with a
  one-line docstring citing ADR-0028, matching `openaca/core/severity.py`'s
  shape. Record in the docstring *why* these three are safe to publish, in the
  terms the code actually supports:
  - They are `@dataclass(frozen=True)` records whose behaviour is read-only —
    the derived `component_label` and `location` properties on both finding
    types, and `Standards.to_dict`, which drops empty taxonomy lists. Nothing
    mutates, computes over an endpoint, or performs I/O.
  - `frozen=True` here is **shallow**: `Standards`' six taxonomy lists,
    `evidence`, `component`, `active_in` and `component_path` are plain
    `list`/`dict` fields a caller can mutate in place. State that plainly
    rather than claiming deep immutability. The contract published is *OpenACA
    does not hand the same object to two consumers and does not mutate one
    after returning it* — each collection builds its findings fresh — not that
    the nested containers are read-only. A consumer that intends to keep a
    finding beyond the call and mutate it should copy it.
  - `Standards` is not a sixth entry point but the type a `PostureFinding`
    field already exposes and without which the field is unusable.
- [x] **Step 4: Wire `openaca/core/__init__.py`. Run the full suite. Commit.**

## Task 3: Posture-surface resolution moves into the posture package

**Files:** Create `tools/posture/agent_surface.py`; modify
`tools/remote/collector.py`, `tests/test_posture_cursor.py`,
`tests/remote/test_collect.py`.

`_agent_posture_manifests` and `_agent_extra_posture_manifests` resolve a
kind's installed posture surface. They have no relationship to uploading, and
`tests/test_posture_cursor.py` importing one of them *from the collector* is
the standing evidence that they are in the wrong module. This move has no
behaviour change and stands on its own merits regardless of the removal.

- [x] **Step 1: Write the failing test.** In `tests/test_posture_cursor.py`,
  import `agent_posture_manifests` from `tools.posture.agent_surface` instead
  of from `tools.remote.collector`, keeping the existing assertions verbatim.
  Run; confirm `ModuleNotFoundError`.
- [x] **Step 2: Create `tools/posture/agent_surface.py`** with
  `agent_posture_manifests` and `agent_extra_posture_manifests` — the two
  function bodies transcribed unchanged, including the docstring about reading
  *the kind's own* surface rather than a Claude-Code-shaped collector called
  unconditionally, and including the Cursor `permissions.json` comment
  explaining that `--config-dir` does not relocate that surface and that this
  is not a bug to fix at the call site.
  - The leading underscores go: the names now cross a module boundary. They
    stay out of `openaca.core` either way (Task 7 asserts it).
  - **A new submodule, not `tools/posture/__init__.py`.** These functions call
    `kind_for`, and `tools/agent_kinds`'s kind modules import
    `tools.posture` — putting a `tools.agent_kinds` import at the top of the
    posture package's `__init__` risks a cycle. `no_manifests` **stays in
    `tools/posture/__init__.py` and stays shared**; `agent_surface` imports it
    from there.
- [x] **Step 3: Rewire the collector** to import both from the new module and
  delete nothing else. The import is `from tools.posture.agent_surface import
  agent_posture_manifests, agent_extra_posture_manifests`, so the collector
  binds its own module global and `_build_agent_collection` looks that global
  up at call time.
  - **The patch target therefore stays on the collector, renamed.** The ~28
    `monkeypatch.setattr("tools.remote.collector._agent_posture_manifests", …)`
    sites in `tests/remote/test_collect.py` become
    `"tools.remote.collector.agent_posture_manifests"` — the underscore drops,
    the module does not. Patching `tools.posture.agent_surface` instead would
    rebind the definition module's global and leave the collector's own
    binding — the one the call resolves — untouched, so every one of those
    tests would silently stop patching anything.
  - The two *direct imports* near the end of that file (`from
    tools.remote.collector import _agent_posture_manifests, _agent_refs` and
    the `_agent_extra_posture_manifests` import below it) are calls, not
    patches, so they take the new names from `tools.posture.agent_surface`
    directly. `_agent_refs` still comes from the collector until Task 4.
  - Task 5 moves the *call site* into `tools/collect.py`. The patch targets
    move with it **then**, in that commit, and not before.
- [x] **Step 4: Run the full suite, `tests/remote/` included. Commit.**

## Task 4: `collect_for_agent` — the per-agent collection body, below the uploader

**Files:** Create `tools/collect.py`, `tests/test_collect.py`.

**Consumes:** `tools.agent_kinds`, `tools.bom`, `tools.graph`,
`tools.posture`, `tools.posture.agent_surface`, `tools.observations`,
`tools.scan`'s two private counters. **Produces:** `CollectedAgent`,
`collect_for_agent`, `ScannerUnavailable`.

This is the ~150 lines out of `tools/remote/collector.py`
(`_agent_refs`, `_build_agent_collection`, `_collect_scanner_findings`) with the
upload-specific parts left behind. `CollectError` is **not** among them: six of
its seven raise sites are upload concerns and it carries an `exit_code`, so it
stays owned by `tools/remote/collector.py` (Task 5 wraps this module's exception
into it).

- [x] **Step 1: Define `CollectedAgent` first**, before moving any logic, so
  both callers are written against one result shape:

  ```python
  @dataclass(frozen=True)
  class CollectedAgent:
      agent_kind: str
      agent_id: str | None
      config_root: Path
      bom: dict[str, Any]
      posture_findings: tuple[PostureFinding, ...]
      observations: tuple[ObservationFinding, ...]
      component_count: int
      warnings: tuple[str, ...]
  ```

  `config_root` is typed `Path`, not `Path | None`: installed discovery always
  sets it, which is why the uploader can `assert` it today. `AgentInstance`
  nonetheless permits `None`, so raise `ValueError` if a kind ever returns one,
  rather than letting a `None` reach `build_agent_bom` as the string `"None"`.
  `ValueError` deliberately, not a domain error: this is an impossible-invariant
  guard against a bug in a kind module, not an operational failure a caller
  handles, so it must not become a second public error on a surface the spec
  fixes at one. Test it by constructing the instance directly. Leave the
  uploader's own `assert` where it is — this plan adds a guard, it does not
  remove one.
- [x] **Step 2: Write failing tests** against a Claude Code endpoint fixture
  (reuse the `_endpoint_fixture` shape from `tests/remote/test_collect.py`;
  copy it, do not import across the `tests/remote/` boundary this plan's
  companion will delete):
  - `posture_findings` and `observations` are tuples of `PostureFinding` /
    `ObservationFinding` — assert `isinstance`, and assert
    `not isinstance(finding, dict)` explicitly, because a dict-shaped result is
    the specific regression this returns-itself decision exists to prevent.
  - Every finding carries `agent_kind` / `agent_id` stamped from the agent,
    including the ones a scanner produced.
  - `component_count == len(bom["components"])`.
  - `include_target=True` (the default) puts `openaca:target` in the BOM
    metadata properties with `str(agent.config_root)`;
    `include_target=False` omits both `openaca:target` and
    `openaca:target_type` — the same assertion
    `test_build_endpoint_collections_emits_one_agent_rooted_bom_per_agent`
    makes today about the upload BOM.
  - `warnings` carries a malformed-manifest note from a fixture with an
    unparseable manifest, and the coverage verdict in the BOM reflects the gap
    — the `evidence_gaps=_component_gap_count(warnings)` wiring must survive
    the move, or a partially-composed agent is reported as `complete`.
  - **No posture rule is filtered.** A Codex fixture with an approval-shaped
    finding (`openaca-posture-command-policy-allow`) yields that finding.
    `_UPLOAD_DEFERRED_RULES` is an upload-boundary decision about what a
    server can store; the library reports what the scan found, exactly as
    `openaca scan` does.
- [x] **Step 3: Move the bodies.** Transcribe `_agent_refs` (keep its docstring
  — it explains that `warnings` is populated in place and feeds coverage),
  `_collect_scanner_findings`, and the collection half of
  `_build_agent_collection`. Two deliberate differences from the original, and
  no others:
  - `target=str(agent.config_root) if include_target else None`, replacing the
    hardcoded `target=None`. Move the *"the upload names no place"* comment out
    with the argument: it belonged to the caller, not the collection.
  - No `_prepare_remote_bom`, no `_posture_finding_to_payload`, no
    `_observation_to_payload`, no `_UPLOAD_DEFERRED_RULES` — those stay in the
    collector.
  - Keep `_count_active_plugins(refs)` over the **agent-scope** refs. The scan
    path passes all refs to the same counter; do not "fix" that here, because
    matching the scan path would change the source-unit count in the upload
    BOM. Note the divergence in a comment and leave it to a scan-side change.
- [x] **Step 4: Define `ScannerUnavailable`** here — a plain `Exception`
  subclass with no `exit_code` — and raise it where `_collect_scanner_findings`
  raises `CollectError` today: `except SkillSpectorCommandNotFound as exc: raise
  ScannerUnavailable(str(exc)) from exc`. The message is the adapter's, unchanged
  character for character, which is what keeps Task 5's re-wrap producing the
  same command-line output.
  - It is generic rather than SkillSpector-specific because `external_scanners`
    is a scanner-agnostic argument: a second scanner reuses this name instead of
    publishing a second one. `SkillSpectorCommandNotFound` stays internal.
  - Test it directly: `external_scanners=("nvidia-skillspector",)` with the
    command absent raises `ScannerUnavailable`, the message is the adapter's,
    and `exc.__cause__` is the `SkillSpectorCommandNotFound`.
  - It carries no `exit_code`. An exit code is a process's concern; the uploader
    supplies its own when it wraps (Task 5).
- [x] **Step 5: Leave the external-scanner warning `click.echo` where the code
  puts it**, and comment why: routing it into `warnings` instead would either
  change what `openaca remote sync` prints (`warnings` also carries
  malformed-manifest notes, which are not echoed today) or need a second
  warnings channel the spec does not define. Recorded in *Deferred*.
- [x] **Step 6: Run the new tests and the full suite. Commit.** The collector
  is untouched so far; nothing calls this yet.

## Task 5: `collect_installed_agents`, and the uploader rewired onto it

**Files:** Modify `tools/collect.py`, `tools/remote/collector.py`; extend
`tests/test_collect.py`; modify `tests/remote/test_collect.py`,
`tests/remote/test_cli.py`.

**Produces:** the public `collect_installed_agents`. The uploader keeps
`build_endpoint_collections` and `EndpointCollection` and keeps working.

- [x] **Step 1: Write the failing test** for the public function's signature and
  discovery behaviour: `collect_installed_agents(config_dir=…, project=…,
  kind_id=…, external_scanners=(), include_target=True)`, all keyword-only,
  returning one `CollectedAgent` per discovered agent in discovery order.
  Assert `include_target`'s default is `True` by calling with no argument.
  Pin the two edge behaviours the signature leaves open, both as they behave
  today:
  - **Zero discovered agents returns an empty sequence**, it does not raise.
    "Nothing is installed here" is an answer, and the uploader already treats
    it as one (`if not collections: click.echo("no installed agent found")`).
  - **An unrecognised `external_scanners` entry is ignored**, not rejected:
    `_collect_scanner_findings` tests `if "nvidia-skillspector" in
    external_scanners` and never enumerates the argument. Assert that
    `external_scanners=("no-such-scanner",)` collects normally, so a later
    "tidy-up" that starts raising on unknown names is a visible change rather
    than a silent one.
- [x] **Step 2: Implement** as `discover_agents(DiscoveryContext(
  source="installed", …))` mapped through `collect_for_agent`. `source` is not
  a parameter: this function answers *what is installed here*, and a declared
  (repo) composition is `openaca bom repo`'s question.
- [x] **Step 3: Rewire `build_endpoint_collections`** to
  `discover_agents(...)` plus `collect_for_agent(agent, external_scanners=…,
  include_target=False)`, then build each `EndpointCollection` from the
  result: `_prepare_remote_bom(collected.bom)`, the payload mapping over
  `collected.posture_findings` with the `_UPLOAD_DEFERRED_RULES` filter, the
  payload mapping over `collected.observations`, and `component_count`.
  - **The deferred-rule filter keeps the boundary it has today.** In the
    current collector the filter is applied to `run_posture_rules`' output
    only; the scanner-produced posture findings are appended *after* it and
    are never filtered. `collected.posture_findings` merges the two, so
    filtering the merged sequence would widen the filter to a source it has
    never covered. Restore the boundary explicitly with
    `if not (f.source == "openaca" and f.rule_id in _UPLOAD_DEFERRED_RULES)` —
    `PostureFinding.source` defaults to `"openaca"` for rules `run_posture_rules`
    emits, and the SkillSpector adapter stamps `source="skillspector"` on
    everything it produces. Comment that `_UPLOAD_DEFERRED_RULES` names two
    of OpenACA's *own* approval rules the hosted side cannot model, so the
    predicate is about who emitted the finding as much as about the id.
  - Test it with a scanner posture finding whose `rule_id` is
    `openaca-posture-command-policy-allow` and whose `source` is not
    `"openaca"`, and assert it still reaches the payload. The existing payload
    tests cannot show this: today's SkillSpector adapter only emits the SARIF
    ids in `_POSTURE_RULE_IDS` (`LP1`–`LP4`, `SC1`, `SC5`, `SC6`), which are
    disjoint from the two deferred `openaca-posture-*` ids, so no current
    fixture exercises the overlap. Nothing in the adapter's contract keeps
    them disjoint, and the test is what keeps the widening from happening
    later by accident.
  - Keep discovery in the collector rather than calling
    `collect_installed_agents`, because `EndpointCollection.agent` is an
    `AgentInstance` and the upload loop uses `agent.bom_ref` in its messages
    and `agent.config_root` for redaction. Passing the instance through
    keeps that untouched; deriving `bom_ref` from the result's kind and id
    would be a second implementation of an identity the dataclass owns.
  - `component_count` may be read from `collected.component_count`:
    `_prepare_remote_bom` rewrites component properties and never adds or
    removes a component, so the two counts are equal. Assert that in a test
    rather than asserting it in prose.
  - Findings are stamped with `agent_kind` / `agent_id` inside
    `collect_for_agent` now, so drop the collector's duplicate `replace(...)`
    stamping — same values, applied once. Confirm with the existing payload
    tests rather than by inspection.
- [x] **Step 4: Translate `ScannerUnavailable` into `CollectError` in the
  collector.** `CollectError` keeps its definition, its `exit_code` and its six
  upload raise sites in `tools/remote/collector.py`; the seventh — the scanner
  one — becomes a wrap of the new exception at the `collect_for_agent` call
  site: `except ScannerUnavailable as exc: raise CollectError(str(exc)) from
  exc`. `tools.remote.cli`'s two `except CollectError` blocks, their
  `exit_code` handling and every `tests/remote/` import are untouched, and the
  existing missing-SkillSpector test must pass unchanged — same message, same
  exit code 1 — which is the evidence the boundary moved without the behaviour
  moving.
- [x] **Step 5: Retarget the remaining patches.** The
  `tools.remote.collector._agent_posture_manifests` /
  `_agent_extra_posture_manifests` patch sites from Task 3 now have to name
  `tools.collect`, because that is the module whose global is looked up at call
  time. Patches of `tools.remote.collector.build_endpoint_collections` stay as
  they are — that function still lives there.
- [x] **Step 6: Run the full suite, `tests/remote/` first.** Every upload
  payload test must pass unchanged: that is the evidence the rewiring is
  behaviour-preserving. Then `uv run openaca remote sync endpoint --dry-run`
  (or the existing dry-run payload test) and diff the payload against a
  pre-change capture. **Commit.**

## Task 6: The collection facade, and the config-root leak test

**Files:** Create `openaca/core/collect.py`; modify
`openaca/core/__init__.py`; extend `tests/test_collect.py`,
`tests/test_core_facade.py`, `tests/test_e2e.py`.

- [x] **Step 1: Write the failing facade test**:
  `core.collect_installed_agents is tools.collect.collect_installed_agents`,
  `core.CollectedAgent is tools.collect.CollectedAgent`, and
  `core.ScannerUnavailable is tools.collect.ScannerUnavailable`. Add the
  error-boundary test alongside it: naming an installed-scanner id whose command
  is absent raises `core.ScannerUnavailable` with the adapter's message out of
  the *published* function, while `build_endpoint_collections` on the same
  fixture raises `CollectError` with the same message and `exit_code == 1`. Both
  halves are the evidence for the split — the library names the failure, the
  uploader keeps its exit code.
- [x] **Step 2: Create `openaca/core/collect.py`** as a thin re-export of all
  three names citing ADR-0028, and wire `openaca/core/__init__.py`.
- [x] **Step 3: Write the multi-kind `config_root` test** — the one that would
  catch the silent leak. Cursor resolves its root to `<home>/.cursor` and
  ignores `config_dir` entirely (ADR-0054), which makes it the honest fixture:
  - Point `Path.home()` at `tmp_path/home` and create `tmp_path/home/.cursor`.
  - Create a Claude Code endpoint at `tmp_path/claude`.
  - Call `collect_installed_agents(config_dir=tmp_path/"claude", project=None)`
    with `kind_id=None`, so both kinds are discovered.
  - Assert the Claude Code result's `config_root == tmp_path/"claude"` **and**
    the Cursor result's `config_root == tmp_path/"home"/".cursor"` — a root the
    `config_dir` argument cannot express.
  - Then assert the leak directly: relativising each result's own BOM component
    source paths against `collected.config_root` succeeds for both agents,
    while relativising against the `config_dir` argument fails for the Cursor
    agent. Name the failure mode in the test docstring: the value that survives
    a failed relativisation is a bare basename rather than an error, so a
    consumer ships a partially-relativised document and nothing raises.
- [x] **Step 4: Add the e2e test** to `tests/test_e2e.py`: one realistic
  endpoint fixture with a vulnerable component, collected through
  `openaca.core.collect_installed_agents`, asserting the BOM contains the
  component and a posture finding is returned as a `PostureFinding`. This is
  the cross-layer promise — discovery, graph, BOM, coverage and posture wired
  together through the published function — and it fails if any one of them
  regresses.
- [x] **Step 5: Run the full suite. Commit.**

## Task 7: The negative test — what collection does *not* publish

**Files:** Extend `tests/test_core_facade.py`.

The surface is defined as much by what is absent as by what is present, and
absence is exactly what rots without a test.

- [x] **Step 1: Write the test.** Assert `not hasattr(core, name)` for all
  nineteen internal symbols, and additionally that each name is absent from
  `core.__all__`:

  | | Symbol | Why it stays in |
  |---|---|---|
  | 1 | `discover_agents` | Discovery is the function's first step |
  | 2 | `DiscoveryContext` | A discovery input |
  | 3 | `AgentInstance` | A discovery intermediate |
  | 4 | `kind_for` | Which kinds exist and how they resolve surfaces |
  | 5 | `REGISTRY` | Same |
  | 6 | `build_agent_graph` | A construction detail of the BOM |
  | 7 | `Graph` | Same |
  | 8 | `WarningLog` | The result carries plain strings |
  | 9 | `resolve_coverage` | Applied to the BOM before it is returned |
  | 10 | `_component_gap_count` | Called internally — **stays private in `tools/scan.py`** |
  | 11 | `_count_active_plugins` | Same |
  | 12 | `run_posture_rules` | An implementation of running posture rules |
  | 13 | `no_manifests` | A collector-pair default for kinds with no surface |
  | 14 | `agent_posture_manifests` | Per-kind posture manifest resolution |
  | 15 | `agent_extra_posture_manifests` | Same |
  | 16 | `collect_skill_observations` | Observation collection |
  | 17 | `collect_skillspector_findings` | Same |
  | 18 | `EndpointCollection` | The uploader's result type, not the library's |
  | 19 | `CollectError` | The uploader's exit-code carrier, not the library's error — `ScannerUnavailable` is the published one |

  The nineteen-symbol count is the spec's; this enumeration is this plan's
  reading of it. If implementation finds a twentieth reachable name that a
  consumer would otherwise assemble by hand, add it to the table rather than
  matching the number.
- [x] **Step 2: Assert the two counters are still spelled with a leading
  underscore** in `tools/scan.py` (`hasattr(tools.scan, "_component_gap_count")`
  and no public alias). The spec's point is that calling them internally is
  what stops them being a problem; a later "tidy-up" that renames them public
  reintroduces it.
- [x] **Step 3: Run, confirm the test passes as written** (it should — this is a
  guard, not a driver). If any name *is* reachable, fix the facade, not the
  test. **Commit.**

## Task 8: The atomic write moves below the command layer

**Files:** Create `tools/atomic_write.py`; modify `tools/bom_cli.py`; extend
`tests/test_bom_cli_agents.py` only if a patch target needs it.

Prerequisite for Task 9: `compile_endpoint_policy` writes its artifact through
`tools/bom_cli.py`'s `_write_new_temp_file`, and that is the transitive
CLI import the spec names.

- [x] **Step 1: Create `tools/atomic_write.py`** with
  `write_new_temp_file(directory, content) -> Path` — the body and the whole
  docstring transcribed verbatim, including the reasoning about
  `tempfile.mkstemp`'s `O_CREAT | O_EXCL` versus a predictable `.tmp` name an
  attacker can pre-plant as a symlink. That docstring is the security argument
  for the function existing; it must not be left behind.
- [x] **Step 2: In `tools/bom_cli.py`, replace the definition with
  `from tools.atomic_write import write_new_temp_file as _write_new_temp_file`.**
  `tests/test_bom_cli_agents.py` patches `tools.bom_cli._write_new_temp_file`
  and `bom_cli`'s own call sites read that module global, so the flaky-write
  tests keep working untouched. Run them specifically and confirm.
- [x] **Step 3: Run the full suite. Commit.**

## Task 9: Policy compilation moves out of the CLI module

**Files:** Create `tools/policy_compile.py`, `tests/test_policy_compile.py`;
modify `tools/policy_cli.py`.

**Produces:** `compile_endpoint_policy` (raising `PolicyValidationError` /
`PolicyEvaluationError` for every input and evaluation failure, and standard
`OSError` from the artifact write) and `render_policy_report` (extracted from
`emit_policy_report`), in a module neither the facade nor anything else has to
reach through the command layer to import.

- [x] **Step 1: Capture the current behaviour first.** Before moving anything,
  confirm `tests/test_policy.py`'s `compile` coverage includes **one CLI case
  per exception branch the function has**, since Step 3 retypes all of them
  and these captures are the evidence the command line did not move:
  - the `--output`-without-`--dry-run` usage error and its exit code 2;
  - `no installed agent found at <target>`;
  - the incomplete-inventory failure (a malformed manifest under the target, so
    `build_agent_graph` produces a warning) — with a `vulnerabilities` risk
    gate absent, so the branch under test is the graph one;
  - `vulnerability gates cannot evaluate non-queryable component(s): …`,
    including the `, ...` suffix past three;
  - the OSV-load warning failure from `_load_osv_with_overlays`;
  - the managed-key-collision failure itself, and all **seven** distinct
    failures `_managed_key_collisions` raises — the function's branches, in
    source order: (1) the directory `stat()` failing with an `OSError` that is
    not `FileNotFoundError`, (2) the directory path existing but not being a
    directory, (3) the `managed-settings.d` drop-in `stat()` failing the same
    way, (4) the drop-in path existing but not being a directory, (5) the
    drop-in `glob("*.json")` failing, (6) a settings file that cannot be read,
    decoded or JSON-parsed, and (7) a settings file whose top-level JSON is not
    an object. Branches 1, 3 and 5 need an induced `OSError` (a `monkeypatch` on
    `Path.stat` / `Path.glob`, or an unreadable-mode directory), not a missing
    path — `FileNotFoundError` is the early-return, not a failure;
  - the text report, the `--format json` report, the `--project`-omitted note
    on stderr, and the artifact digest.

  Each case asserts the exact stderr line and the exit code. Add whichever is
  missing **now**, so the move is verified against tests that predate it.
- [x] **Step 2: Create `tools/policy_compile.py`** and move
  `compile_endpoint_policy`, `_evaluate_endpoint`, `_managed_key_collisions`,
  `_write_artifact`, `_report`, `_component_label` and `_OPENACA_FILENAME`
  across unchanged, with their comments — the pinned-`kind_id` comment
  explaining why open discovery would pull in the invoking user's home, the
  incomplete-inventory comment explaining why graph warnings fail the compile,
  and the unmapped-endpoint-posture comment. Import the artifact write from
  `tools.atomic_write`.
- [x] **Step 3: Retype every Click exception the moved code raises.** A module
  below the command layer must not raise the command layer's exception type;
  after the move `tools/policy_compile.py` imports no `click` at all, and
  `PolicyValidationError` / `PolicyEvaluationError` cover every input and
  evaluation failure the function raises deliberately — which is what the
  facade's documentation (Task 13 Step 2) says, alongside the one failure they
  do not cover.
  - **The one they do not cover is the artifact write.** `_write_artifact`
    calls `mkdir`, the temp-file writer and `Path.replace`, and translates
    none of their `OSError`s (an unwritable or read-only `--output` directory
    being the realistic trigger). That stays untranslated deliberately: today
    the `OSError` escapes `openaca policy compile` uncaught, so wrapping it in
    a domain error the command catches would turn a traceback into `Error: …`
    with exit code 1 — a command-line behaviour change this plan forbids. So
    `OSError` is documented as part of the contract rather than converted, and
    a test asserts it: a direct call with an unwritable output directory raises
    `OSError` (not a policy error), and the CLI's behaviour on the same input
    is byte-identical to the pre-change capture.
  - `raise click.UsageError("--output is required unless --dry-run is set")`
    becomes `PolicyValidationError` — the argument combination is invalid
    before anything is evaluated. Leave the command's own identical check in
    place ahead of the call, so a CLI user still gets the `UsageError` and
    exit code 2 from the command.
  - Every other `raise click.ClickException(...)` in the moved bodies becomes
    `PolicyEvaluationError` with **the message unchanged, character for
    character**: `no installed agent found at …`, the joined graph warnings,
    the non-queryable-component message, the joined OSV-load warnings, the
    managed-settings key collision, and all seven
    `_managed_key_collisions` read/shape failures enumerated in Step 1.
  - **This is invisible from both command lines, and that is checkable rather
    than assumed.** `openaca policy compile` already wraps the call in
    `except (PolicyValidationError, PolicyEvaluationError) as exc: raise
    click.ClickException(str(exc))`, and `openaca remote sync policy`
    (`tools/remote/cli.py`) wraps its own call in the same two types alongside
    `RemoteClientError`. `click.ClickException` exits 1 and prints
    `Error: <message>`; re-wrapping the domain error produces the identical
    line and the identical code. The `UsageError` path is the one exception,
    and the command's pre-check is why it keeps exit code 2.
  - Test each branch twice: a direct `compile_endpoint_policy` call asserting
    the domain type and the message, and the Step 1 CLI case asserting the
    same stderr line and exit code still come out of `openaca policy compile`.
    Run the `remote sync policy` tests too — that caller must be shown
    unchanged, not assumed unchanged.
- [x] **Step 4: Split the report emitter.** `render_policy_report(report,
  output_format) -> str` returns exactly what `emit_policy_report` prints,
  newline-for-newline including the leading blank line before `Components:`.
  `emit_policy_report` stays in `tools/policy_cli.py` as
  `click.echo(render_policy_report(...))`. Test the two formats' strings, and
  test that the command's stdout is byte-identical to the pre-change capture.
  - `render_policy_report` lives in `tools/policy_compile.py`, not the CLI
    module: it is the third promoted name and the facade must not import the
    command layer to reach it.
- [x] **Step 5: Add the `host` expiry note** at the `compile_policy(policy,
  decisions)` call — the point where a second host compiler would be
  dispatched. State: `--host` is `Choice(["claude"])`, a gate on input rather
  than a key that selects anything, so the compilation takes no `host`
  argument; the day a second host compiler lands it becomes a dispatch key and
  this function needs the argument, because otherwise a programmatic caller
  silently gets Claude's format whatever it asked for. Do **not** add the
  parameter now. Do not "fix" the `--host claude` / `--kind claude-code`
  spelling inconsistency either; it is noted and unchanged.
- [x] **Step 6: Rewire `tools/policy_cli.py`** to import both names from
  `tools/policy_compile.py`. The command keeps its `--output` check, its
  `except (PolicyValidationError, PolicyEvaluationError)` → `ClickException`
  translation, its `click.echo`, and its `--project`-omitted note — those are
  the command's, not the compilation's. `tools/remote/cli.py` imports
  `compile_endpoint_policy` and `emit_policy_report` *from
  `tools.policy_cli`*; both names must still be reachable there, so the
  rewire is an import, not a deletion.
- [x] **Step 7: Run the full suite plus a manual
  `uv run openaca policy compile` against a fixture**, diffing stdout, the
  written artifact and the exit code against a pre-change capture. **Commit.**

## Task 10: Policy compilation on the facade, and the layering guard

**Files:** Modify `openaca/core/policy.py`, `openaca/core/__init__.py`; extend
`tests/test_core_facade.py`.

- [x] **Step 1: Write the failing test**:
  `core.compile_endpoint_policy is tools.policy_compile.compile_endpoint_policy`
  and `core.render_policy_report is tools.policy_compile.render_policy_report`.
- [x] **Step 2: Write the layering test.** Import `openaca.core` in a
  subprocess (`sys.executable -c`, so `sys.modules` is clean) and assert
  `tools.policy_cli`, `tools.bom_cli` and `tools.cli` are **not** in
  `sys.modules`. Note in the test what it is not claiming: `click` itself is
  already imported through another facade path, and the spec says so
  explicitly — this guards module layering, not the dependency.
- [x] **Step 3: Add the two names** to `openaca/core/policy.py` and
  `openaca/core/__init__.py`.
- [x] **Step 4: Record the residual dependency** in `openaca/core/policy.py`'s
  docstring: the compilation still imports four private helpers from
  `tools/scan.py`, which also defines the `scan` command group, so importing
  the facade does import that module. `tools/scan.py` is a hybrid domain/CLI
  module and prising those helpers out is a scan-side change, not this plan's.
  See *Deferred*.
- [x] **Step 5: Run the full suite. Commit.**

## Task 11: The Click group as a published import

**Files:** Create `openaca/cli.py`, `tests/test_openaca_cli.py`.

- [x] **Step 1: Write the failing tests.**
  - `openaca.cli.main is tools.cli.main`, and `isinstance(main, click.Group)`.
  - `set(main.commands) >= {"scan", "bom", "policy"}` — and `remote` is still
    there too, asserted as part of *nothing is deleted* rather than as a
    promise.
  - **The program-name test:** build a fresh `click.Group()`, add
    `main.commands["scan"]` to it under some other name, invoke it through
    `CliRunner` with `prog_name="othertool"` and `--help`, and assert the usage
    line names `othertool` — Click builds a usage line from the invocation, not
    from where a command was defined. That is the whole mechanism a consumer
    offering OpenACA's commands under its own name relies on, and it is worth a
    test precisely because it is a Click behaviour rather than an OpenACA one.
  - Assert nothing about any command's options or output. Those are the CLI's
    contract to its users — looser than a library API by design, so that adding
    a flag is not a break.
- [x] **Step 2: Create `openaca/cli.py`** — `from tools.cli import main` plus
  `__all__ = ["main"]`, and a docstring stating exactly what is promised (the
  group exists; `scan`, `bom` and `policy` are reachable on it by those names)
  and what is not (any command's internals, option set or output format, and
  any obligation to keep a given command in the group — a consumer registering
  a name that later disappears gets an import-time or lookup-time failure,
  which is the right moment to find out).
- [x] **Step 3: Leave `[project.scripts]` pointing at `tools.cli:main`.**
  Running `openaca` must not change. Assert the entry point still resolves.
- [x] **Step 4: Run the full suite. Commit.**

## Task 12: Kind-selection validation moves below the command layer

**Files:** Create `tools/kind_selection.py`, `openaca/core/kind_selection.py`,
`tests/test_kind_selection.py`; modify `tools/cli_kind.py`,
`openaca/core/__init__.py`; extend `tests/test_core_facade.py`.

**Produces:** `validate_kind_selection(kind, config_dir)` raising
`KindSelectionError`, and the two names on the facade. Same shape as Task 9: a
domain module that imports no `click`, the command layer retyping the domain
error, and the facade re-exporting from below the CLI rather than through it.

`collect_installed_agents` takes a kind and a config root, so the rules
governing which pairs are legal are part of that call rather than decoration
around the three commands. A consumer that cannot reach them does not merely
lose error messages, it gets different behaviour: an unknown kind silently
collects nothing, and a refused root override is silently ignored so a
*different directory than the caller named* is read. Both are indistinguishable
from success at the call site.

- [x] **Step 1: Capture the current behaviour first.** Before moving anything,
  invoke `openaca scan endpoint` through `CliRunner` for each of the three
  branches — an unknown kind, a `--config-dir` with no `--kind`, and
  `--kind cursor --config-dir …` (the real refusing kind, ADR-0054) — and
  record the exact `result.output` and `result.exit_code`. Those captures
  become the literals the parity tests assert, so the parity claim is evidence
  rather than assertion. All three exit 1 and print `Error: <message>`.
- [x] **Step 2: Create `tools/kind_selection.py`** with `KindSelectionError`
  and `validate_kind_selection(kind, config_dir) -> None` — the three branch
  bodies transcribed with the messages **character for character**, including
  the flag spellings (`--config-dir`, `--kind`) they name, which stay as they
  are because the CLI's wording is the wording being published.
  - **The registry is read *through* `tools.agent_kinds`, not imported from
    it**, and the comment saying why moves with the code: `from
    tools.agent_kinds import REGISTRY` would freeze the choice list and the
    validation at the module's import time, ahead of any test that swaps in a
    synthetic registry via `monkeypatch.setattr`. Breaking this makes tests
    pass while validating against a frozen registry, so a test asserts the
    property directly (Step 5).
  - The module imports no `click`.
- [x] **Step 3: Reduce `tools/cli_kind.py` to the adapter.**
  `require_kind_for_config_dir` calls `validate_kind_selection` and translates
  `KindSelectionError` into the `click.ClickException` it raises today,
  `from None` as before. `kind_option` stays. The three callers
  (`tools/scan.py`, `tools/bom_cli.py`, `tools/remote/cli.py`) are untouched.
- [x] **Step 4: Add the facade module and wire it.**
  `openaca/core/kind_selection.py` re-exports both names, as the other facade
  modules do, and `openaca/core/__init__.py` gains them in the import block and
  `__all__`. The docstring says why the *check* is published and the facts it
  checks are not: kind ids and each kind's refusal would let a consumer rebuild
  the validation and phrase its own errors, and the two wordings would drift
  while both claimed to describe the same rule. `REGISTRY` and `kind_for` stay
  on Task 7's absent list.
- [x] **Step 5: Tests.**
  - Direct-function tests for all three branches, asserting the whole message:
    the unknown kind lists the known kinds, the bare config root is refused,
    and Cursor's refusal names its own reason.
  - **The live-registry test:** `monkeypatch.setattr(agent_kinds, "REGISTRY",
    …)` with two synthetic kinds, one refusing, and assert the unknown-kind
    message lists the synthetic ids and the refusing kind's own reason comes
    back. Importing the names instead of reading them through the module makes
    this test validate the real registry while appearing to pass.
  - CLI-parity tests asserting `result.output` and `result.exit_code` equal the
    Step 1 captures for all three branches.
  - Facade tests: both names are identical objects to
    `tools.kind_selection`'s, and the error is catchable by name.
- [x] **Step 6: Confirm the layering guard still passes.**
  `test_importing_the_facade_does_not_import_the_command_modules` must stay
  green — the new facade module reaches `tools/kind_selection.py`, never
  `tools/cli_kind.py`.
- [x] **Step 7: Re-run the Step 1 capture and diff it against the original.**
  Byte-identical, or the move is not done. Run the full suite. **Commit.**

## Task 13: Docs and the final gate

**Files:** Modify `docs/reference/cli.md`, `docs/plans/README.md`.

- [x] **Step 1: Document the published import paths** in
  `docs/reference/cli.md`: the collection function with its arguments, result
  fields and the one exception it raises — `ScannerUnavailable`, when
  `external_scanners` names a scanner whose command is not installed, which is
  the only failure the call has that a caller is expected to handle —
  `parse_policy` → `compile_endpoint_policy` →
  `render_policy_report`, `validate_kind_selection` and `KindSelectionError`
  (the rules governing the collection call's own two arguments — publishing the
  check rather than the kind ids is what keeps a consumer's wording from
  drifting from OpenACA's), the three install-source helpers, and
  `openaca.cli.main` with the register-under-your-own-group pattern. Say once,
  plainly, that `openaca.core` is for a program calling OpenACA and the CLI
  group is for offering OpenACA's commands to a person; a caller holding a
  policy document in memory wants the first, because constructing flag strings
  for arguments a function takes directly turns a renamed parameter into a
  runtime failure instead of a type error at upgrade time.
- [x] **Step 2: Note the division of labour** the spec makes explicit: a
  programmatic caller wanting the `--project`-omitted note prints its own, and
  wanting the CLI's error behaviour catches `PolicyValidationError` /
  `PolicyEvaluationError` itself. Say plainly that writing the artifact can also
  raise `OSError`, untranslated, so a caller passing an `output` path handles it
  the way it handles any other write.
- [x] **Step 3: Flip this plan's row in `docs/plans/README.md` to ✅ Done.**
- [x] **Step 4: Final gate.** The four commands CI runs, in CI's order —
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`,
  `uv run pytest` (the full suite, `tests/remote/` included) — plus a diff
  review confirming that no command's arguments, output or exit code changed
  and that nothing was deleted.

---

## Deferred

Recorded with the cost of skipping, per the spec's robustness bar. None of
these is implemented by this plan, and none should be re-raised in review as a
finding.

| Deferred | Cost of skipping |
|---|---|
| External-scanner warnings still reach stderr via `click.echo` from inside the collection module | A library caller sees a line on stderr it did not ask for. Routing it into `warnings` instead would change what `openaca remote sync` prints, since `warnings` also carries malformed-manifest notes that are not echoed today; doing it properly needs a second warnings channel the spec does not define. |
| `openaca.core.policy` transitively imports `tools/scan.py`, which defines the `scan` command group | The spec's stated fix removes the `tools/policy_cli.py` and `tools/bom_cli.py` dependencies, and Task 10 guards exactly those. `tools/scan.py` remains, so importing the facade still imports a module that builds a Click group. Prising `_agent_scan_prep`, `_filter_agent_scope_refs`, `_load_osv_with_overlays` and `_refs_from_graph` out of it is a scan-side refactor with its own regression surface. |
| No stability guarantee on any new surface | ADR-0028 holds pre-V0 that `openaca.core` has no back-compat promise, and these additions inherit it. A consumer pins a version and upgrades deliberately. |
| Advisory matching and rendering are not on the facade | A consumer wanting findings rendered as OpenACA renders them drives the CLI. This plan publishes what the in-tree client used, not everything a consumer might want. |

## Out of scope

- **Removing anything.** `openaca remote`, `tools/remote/`, `tests/remote/` and
  the `httpx` dependency all still exist and still work when this plan is done.
  Their removal is `docs/specs/remote-client-removal.md`'s subject and a
  separate plan; keeping the two apart is what lets this one ship and be
  released first. If a task appears to require a deletion, it belongs there.
- **New CLI surface.** No command, no flag — including no flag to make
  `openaca scan endpoint` emit the Agent BOM it already builds. The collection
  API serves that in one call.
- **Publishing the machinery behind collection.** The graph, the agent
  instance, the discovery context, the kind registry, the warning log and the
  coverage counters stay internal (Task 7).
- **A `host` parameter on `compile_endpoint_policy`**, and a second host
  compiler. Task 9 Step 5 leaves the note; whoever adds the compiler owns the
  argument.
- Changing scan semantics, BOM contents, posture rule behaviour, advisory
  matching, or any output format.
- The `--host claude` / `--kind claude-code` spelling inconsistency.
- Migrating any consumer onto these surfaces.
