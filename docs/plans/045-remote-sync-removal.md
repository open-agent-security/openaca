# Plan 045 — Remote sync removal

**Goal:** OpenACA drops its hosted-service client and loses nothing else, and the
collection logic that client used becomes a public facade function — so a
consumer can obtain the installed agents' composition, posture findings and
observations in one call rather than by importing internals or stitching two
commands together.

**Architecture:** Five additive changes land first and stand on their own —
posture-surface resolution moves into the posture package, three identity
helpers are re-exported through the facade, the collection logic moves from
`tools/remote/collector.py` to `tools/collect.py` with one public entry point in
`openaca.core`, policy compilation joins the facade as a pair of pure functions,
and the existing Click group gains a supported import path. Only then is `tools/remote/` removed, so at no point does the
tree contain a half-migrated uploader. The removal commit adds no CLI surface,
and changes scanner behaviour in exactly one place: `scan`'s next-action list
stops recommending a command the same commit deletes.

**Spec:** `docs/specs/remote-sync-removal.md`

**ADRs:** 0028 (the facade). A new ADR supersedes 0024, 0032, 0050, 0051 and
0061.

## Constraints

- [ ] No command other than `remote` changes behaviour, with one named
  exception: `scan`'s next-action list for an installed agent drops the
  `sync to remote: openaca remote sync endpoint` line (`tools/scan.py:717-723`,
  `_next_actions_for`). Recommending a command this same change deletes is a
  bug the removal introduces if left alone, not a stability guarantee worth
  keeping. `bom`, `lint`, `export`, `promote`, `seed`, `triage` and `policy`
  produce identical output for identical input, before and after.
- [ ] **All four `remote` subcommands go, including `policy compile`.** The
  group is not only an uploader: `remote policy compile` *downloads* the
  organisation policy and compiles it. Both directions are a hosted-service
  client and both leave. Keeping the download would retain `client.py`,
  `config.py`, a credential and `httpx` — every part of what this removes — so
  a partial removal is not a smaller version of this change, it is no version
  of it.
- [ ] **`openaca policy` — the local command — behaves identically.** `validate`
  and `compile` over a policy file on disk produce the same output and exit
  status, before and after, as do `tools/policy.py` and `openaca.core.policy`,
  which are untouched. `tools/policy_cli.py` is not: Step 4 retypes its
  `click.ClickException` raise sites to `PolicyValidationError` /
  `PolicyEvaluationError` and splits `emit_policy_report` into a pure
  `render_policy_report` plus a `click.echo` in the command. Neither change is
  visible from the command line — the command already catches both error types
  and already prints what `render_policy_report` would return. What goes is one
  network call, not the policy language, the evaluator, the risk gates or the
  host compiler.
- [ ] The facade additions are re-exports. No logic is copied into
  `openaca/core/`, and `tools/identity.py` stays the implementation.
- [ ] The posture-manifest move changes no behaviour. Cursor's
  `settings_collector` keeps resolving via `CURSOR_CONFIG_DIR`/`XDG_CONFIG_HOME`
  rather than `--config-dir`; that quirk is documented at the call site and is
  preserved verbatim, not "fixed" en route.
- [ ] Steps 1-5 are additive and leave `tools/remote/` working. The suite passes
  at every commit, including `tests/remote/`, until step 6 deletes it.
- [ ] **This plan adds no CLI surface** — no new command, no new flag. A consumer
  wanting a BOM and its findings together gets both from one facade call in one
  process, so nothing is needed on the command line to serve that.
- [ ] **The collection API is one function, one result type, the two finding
  types it carries, the value type one of those findings holds, and one public
  error type — six public names, not four.** `collect_installed_agents`,
  `AgentCollection`, `PostureFinding`, `ObservationFinding`, `Standards` (the
  value type `PostureFinding.standards` holds — a consumer inspecting or
  constructing a finding needs it in scope, so it is not optional once
  `PostureFinding` is public), and a collection error (see below). Nineteen
  internal symbols would otherwise be reachable; none of them is promoted, and
  neither private counter becomes public. If a review of this step is adding a
  name beyond those six, it has gone wrong.
- [ ] **Findings are returned as `PostureFinding` and `ObservationFinding`, not
  dicts.** The removed uploader mapped them into its server's vocabulary
  (`rule_id`→`finding_id`, `title`→`summary`, `remediation`→`fix`) inside the
  collection step; that mapping belongs to a consumer, not here.
- [ ] Removal drops `httpx` from `pyproject.toml`, and a wheel built afterwards
  runs a scan with `httpx` absent from the environment.
- [ ] ADRs are not edited in place. The superseding ADR is new; the five it
  replaces gain `status: superseded` and `superseded-by:` in frontmatter only.
- [ ] No artifact in this repository refers to any downstream consumer by name.
  OpenACA has no awareness of what consumes it.

## Tasks

### Step 1 — Move posture-surface resolution into the posture package

- [ ] Add a failing test in `tests/test_posture_cursor.py` that imports the
  installed-posture-surface resolver from `tools.posture` rather than from
  `tools.remote.collector`.
- [ ] Move `_agent_posture_manifests` and `_agent_extra_posture_manifests` from
  `tools/remote/collector.py:152-187` into `tools/posture/`, exported under
  names without the leading underscore. Keep `no_manifests` shared and keep the
  Cursor comment with the code it explains.
- [ ] **Keep `tools/agent_kinds` importable from the new home.** Both moved
  functions type-hint `agent: AgentInstance` and call `kind_for(agent.kind_id)`
  at runtime. `tools/posture/` is not a leaf: `tools/agent_kinds/claude_code.py`,
  `codex.py` and `cursor.py` already import from `tools.posture` at module level
  (`tools/agent_kinds/claude_code.py:12`, `codex.py:12`, `cursor.py:12`), and
  `tools/agent_kinds/__init__.py` triggers those three kind-module imports from
  inside `_registry()` (`tools/agent_kinds/__init__.py:203-209`), which the
  module body calls immediately as `REGISTRY: tuple[AgentKind, ...] =
  _registry()` — *before* `kind_for` is defined three lines later
  (`tools/agent_kinds/__init__.py:212`). If the moved functions import
  `kind_for` at module level, that import runs while `tools.agent_kinds` is
  still mid-initialization and `kind_for` does not exist in its namespace yet:
  `import tools.agent_kinds` fails outright with an `ImportError` for
  circular import, breaking every CLI entry point, not only the posture path.
  `AgentInstance` is safe under `from __future__ import annotations` (the
  annotation is never evaluated at runtime), but keep it under
  `TYPE_CHECKING` as well for clarity. Import `kind_for` inside each function
  body instead of at module level — the same lazy pattern `_registry()` itself
  uses for `claude_code`/`codex`/`cursor` and for the same reason.
- [ ] Update `tools/remote/collector.py` and `tests/test_posture_cursor.py` to
  import from the new home.
- [ ] Four gates + full suite green. Commit.

### Step 2 — Re-export the install-source helpers through the facade

- [ ] Add a test asserting `openaca.core` exports
  `is_mcp_package_launch_install_source`, `safe_unpinned_mcp_install_source` and
  `safe_pinned_mcp_install_source`, and that each is the same object as its
  `tools.identity` counterpart — proving a re-export rather than a copy.
- [ ] Add them to `openaca/core/identity.py` and to `openaca/core/__init__.py`'s
  imports and `__all__`.
- [ ] Four gates + full suite green. Commit.

### Step 3 — The collection API

- [ ] Add a failing test: `openaca.core.collect_installed_agents()` against a fixture
  endpoint returns one `AgentCollection` per installed agent, each carrying a
  CycloneDX `bom`, `posture_findings` as `PostureFinding` objects,
  `observations` as `ObservationFinding` objects, a `component_count` and
  `warnings` as plain strings.
- [ ] Move the producer logic from `tools/remote/collector.py` into
  `tools/collect.py`: `build_endpoint_collections`, `_build_agent_collection`,
  `_agent_refs`, `_collect_scanner_findings`, and the collection result type.
  Leave behind everything upload-specific — install-source trimming, the
  payload-vocabulary mapping, and the hardcoded `target=None`.
- [ ] **Give the producer-logic tests in `tests/remote/test_collect.py` a home
  before Step 6 deletes that directory.** That file mixes two things: tests of
  the producer logic this step moves (a kind's posture allowlist is honoured,
  `build_agent_graph` warnings downgrade `openaca:composition_coverage` to
  `partial`, one agent-rooted BOM is emitted per installed agent — for example
  `test_build_endpoint_collections_respects_the_kind_posture_allowlist`,
  `test_composition_coverage_reflects_graph_warnings`, and
  `test_build_endpoint_collections_emits_one_agent_rooted_bom_per_agent`), and
  tests of upload mechanics this step does not move (install-source trimming,
  the payload-vocabulary mapping, redaction for transport, the client, the
  pending-payload cache, dry-run remote configuration) — which *Out of scope*
  already says are removed, not relocated. Create `tests/test_collect.py` and
  port the first group there, against `tools.collect`. Porting is not copying:
  any assertion written against the old upload-vocabulary dicts (`finding_id`,
  `summary`, `fix` — e.g. in
  `test_build_endpoint_collection_uploads_external_scanner_findings`) must be
  rewritten against the `PostureFinding` / `ObservationFinding` objects this
  step returns instead, since the dict mapping this step removes is exactly
  what made the old assertion pass. Do not port the second group; deleting
  `tests/remote/` in Step 6 is where that coverage is meant to go, per
  *Non-goals*.
- [ ] **`_collect_scanner_findings` stops printing.** Today
  (`tools/remote/collector.py:251-267`) a skillspector warning goes straight to
  `click.echo(f"warning: {warning}", err=True)` and is dropped from the return
  value — a library function that writes to stderr cannot be composed, the same
  defect Step 4 fixes in `emit_policy_report`. Change its return type to include
  the warnings it collects, and have `_build_agent_collection` fold them into
  that agent's `AgentCollection.warnings` instead of printing. Add a test
  covering this case: an external scanner that runs and reports a warning
  surfaces it in `collected.warnings`, not on stderr.
- [ ] **Retype `CollectError`.** It carries a CLI-only `exit_code`
  (`tools/remote/collector.py:104-107`), the same shape of problem Step 4 fixes
  for `click.UsageError` in `compile_endpoint_policy`. Add a plain
  `CollectionError` (`ValueError` subclass, no `exit_code`), raised when a
  requested external scanner is not installed; the CLI layer that still needs
  an exit code catches it and raises `click.ClickException` itself.
  **`CollectionError` is defined in `tools/cli_kind.py`, not `tools/collect.py`
  — see the dependency-cycle note under the `kind_id`/`config_dir` validation
  task below** — and `tools/collect.py` imports it from there for this case
  and the two below.
- [ ] **Validate `external_scanners` inside the facade, not only in the CLI.**
  Today `click.Choice(["nvidia-skillspector"])` (`tools/scan.py:419`,
  `tools/remote/cli.py:205`) is what rejects an unsupported scanner name — the
  membership check the moved code performs
  (`if "nvidia-skillspector" in external_scanners:`,
  `tools/remote/collector.py:258`) silently no-ops on anything else, including
  a typo. A CLI caller never notices because Click already rejected the value
  before this code runs; a programmatic caller of `collect_installed_agents`
  has no Click in front of it, so a typo would return a normal collection
  while the caller believes the scanner ran. Raise `CollectionError` for an
  unrecognised name in `external_scanners`, not only for a recognised one
  whose executable is missing.
- [ ] **Validate `kind_id` and the `config_dir`/`kind_id` pairing inside the
  facade, not only in the CLI.** `discover_agents`
  (`tools/agent_kinds/__init__.py:219-234`) silently returns an empty list for
  an unknown `kind_id` — it just never matches a registry entry — and
  `build_endpoint_collections` calls it directly, bypassing
  `require_kind_for_config_dir` (`tools/cli_kind.py:34-63`), which is what
  today rejects an unknown kind, requires `config_dir` to be paired with a
  `kind_id`, and rejects a `config_dir` override for a kind whose
  `root_override_refusal` is set (Cursor: `tools/agent_kinds/cursor.py:134-141,228`;
  ADR-0054). Cursor's `resolve_config_root` accepts and ignores `config_dir`
  (`tools/agent_kinds/cursor.py:143-157`), so a caller passing
  `kind_id="cursor", config_dir=<override>` gets no error at all — it silently
  scans the real Cursor home instead of the location it asked for. A CLI
  caller never hits this because `require_kind_for_config_dir` already ran;
  a programmatic caller of `collect_installed_agents` has no Click in front of
  it and gets either an empty collection (unknown kind) or the wrong location
  scanned (refusing kind + override), with no signal either happened. Extract
  the three checks `require_kind_for_config_dir` performs into a plain
  function raising `CollectionError`, and make `require_kind_for_config_dir`
  a thin wrapper that calls it and re-raises as `click.ClickException` with
  the same message it produces today — so `scan`'s and `bom`'s existing error
  text is unchanged, and the facade gets the same validation without a Click
  dependency. `build_endpoint_collections` calls the plain validator before
  calling `discover_agents`.
  **Both the plain validator and `CollectionError` are defined in
  `tools/cli_kind.py` itself, not moved into `tools/collect.py`.**
  `tools/collect.py` already imports `_component_gap_count` and
  `_count_active_plugins` from `tools/scan.py` (the move task above), and
  `tools/scan.py` already imports `require_kind_for_config_dir` from
  `tools/cli_kind.py` (`tools/scan.py:68`). Defining the plain validator in
  `tools/collect.py` instead would force `require_kind_for_config_dir`'s thin
  wrapper to import it from there, closing a cycle:
  `cli_kind → collect → scan → cli_kind`. `tools/cli_kind.py` today imports
  only `tools.agent_kinds` (`tools/cli_kind.py:21`), which depends on neither
  `tools.scan` nor `tools.collect`, so keeping the plain validator and
  `CollectionError` there keeps the graph acyclic —
  `collect → cli_kind → agent_kinds` and `scan → cli_kind → agent_kinds`, with
  no edge back to either. `tools/collect.py` imports both names from
  `tools.cli_kind`.
- [ ] Three tests for these cases: an unknown `kind_id` raises `CollectionError`
  rather than silently returning an empty collection; `config_dir` without
  `kind_id` raises `CollectionError`; and `config_dir` with `kind_id="cursor"`
  raises `CollectionError` rather than silently scanning the real Cursor home.
  A fourth test asserts `scan --config-dir … ` and `bom --config-dir …`'s
  error text is byte-identical before and after this refactor.
- [ ] `target` becomes an `include_target: bool = True` argument. The uploader
  hardcoded `None` with the comment "the upload names no place" — the right
  decision in the wrong place. Defaulting to `True` leaves CLI behaviour
  unchanged.
- [ ] **The two interim callers inside `tools/remote/` pass
  `include_target=False` explicitly.** `collect_endpoint`
  (`tools/remote/collector.py:288`) and `build_endpoint_dry_run_payloads`
  (`tools/remote/collector.py:410`) both call `build_endpoint_collections`
  today with no `target` argument at all, because the omission is currently
  hardcoded two calls deeper, inside `_build_agent_collection`
  (`tools/remote/collector.py:204`, the `target=None` this task removes).
  Once `include_target` defaults to `True` at the top of that call chain,
  these two untouched call sites start including the local config root in
  every uploaded and dry-run BOM unless they are updated to pass
  `include_target=False` in the same commit — the exact redaction-contract
  regression the comment at the old hardcode exists to prevent, and the one
  this step's own constraint ("the suite stays green at this commit,"
  `tests/remote/` included) depends on `tests/remote/test_collect.py`
  actually asserting `openaca:target`'s absence to catch. Make it a task
  rather than leaving it to be found by a red test.
- [ ] `openaca/core/collect.py` re-exports `collect_installed_agents`,
  `AgentCollection`, `PostureFinding`, `ObservationFinding`, `Standards` and
  `CollectionError` — six names, and nothing else. Add those six to
  `openaca/core/__init__.py`'s imports and `__all__`, alongside — not instead
  of — the BOM, matching, policy, severity and OSV names `__all__` already
  carries; this step is additive to the existing facade, which loses nothing.
- [ ] Two tests, not one, because `openaca.core` already has an established
  surface this step must not shrink or make ambiguous. First: a test asserting
  `openaca.core.collect` — the module this step adds — exposes **exactly**
  those six names. Second: a test asserting `openaca.core.__all__` gained
  those six names and that `Graph`, `AgentInstance`, `DiscoveryContext`,
  `WarningLog`, `kind_for`, `resolve_coverage` and `build_agent_graph` are
  **not** reachable through `openaca.core`, without asserting an exact count
  against the pre-existing names. This is the test that stops the surface
  growing by convenience, scoped to the module it actually governs.
- [ ] Two tests for the scanner-error path: a requested external scanner that
  is recognised but not installed raises `CollectionError`, not `CollectError`
  or another internal exception type; and a scanner name `external_scanners`
  does not recognise (a typo, not `"nvidia-skillspector"`) also raises
  `CollectionError`, rather than being silently dropped.
- [ ] **The interim `tools/remote/collector.py` needs its own
  `_build_agent_collection`, not a bare pointer at the moved one.** The
  version moving to `tools/collect.py` returns an untrimmed `bom` and typed
  `PostureFinding`/`ObservationFinding` objects — that is the point of the
  facade. But until Step 6 deletes `tools/remote/`, `collect_endpoint` and
  `build_endpoint_dry_run_payloads` still need the old `EndpointCollection`
  shape: a `bom` with install-source arguments trimmed, and findings as
  upload-vocabulary dicts (`rule_id`→`finding_id`, `title`→`summary`). Handing
  them the new return value directly would either crash on the dict-shaped
  upload contract or ship untrimmed install-source arguments — including
  secrets such as `--token` — to the remote server. Replace
  `_build_agent_collection` in `tools/remote/collector.py` with a thin
  wrapper: call the moved function with `include_target=False`, then apply
  `_prepare_remote_bom` to its `bom` and `_posture_finding_to_payload` /
  `_observation_to_payload` to its findings, exactly as today's function does
  inline. **Also carry forward the `_UPLOAD_DEFERRED_RULES` filter at this
  boundary, not inside the moved function.** Today's `_build_agent_collection`
  excludes `command_policy_allow`/`project_trust` findings
  (`tools/remote/collector.py:220-230`) before payload-izing them — that
  filter is upload-schema-specific (*Out of scope* already says these two
  rules are "ordinary scanner output; only the uploader treated them
  specially"), so it must **not** move into `tools/collect.py`'s version, or
  every facade consumer would silently lose two posture rules for a hosted-side
  storage limitation that is not theirs. The interim wrapper in
  `tools/remote/collector.py` applies the `_UPLOAD_DEFERRED_RULES` filter
  itself, to the combined `posture_findings` list, so `tests/remote/`'s
  existing assertions about deferred rules keep passing until Step 6 deletes
  the constant along with the rest of the uploader.
- [ ] Point the rest of `tools/remote/collector.py` at the new location —
  `build_endpoint_collections`, `_agent_refs`, `_collect_scanner_findings` and
  the collection result type import from `tools.collect` rather than being
  redefined — so the uploader keeps working until Step 4 deletes it, and the
  suite stays green at this commit.
- [ ] Four gates + full suite green. Commit.

### Step 4 — Policy compilation through the facade

- [ ] Add a failing test: `openaca.core.compile_endpoint_policy(parse_policy(doc), target=…, dry_run=True)` returns a report, and `render_policy_report(report, "text")` returns the string `openaca policy compile` prints.
- [ ] Promote `compile_endpoint_policy` to `openaca/core/policy.py`. It is already typed and already returns its report as a dict, so this is a re-export — with one fix below.
- [ ] **Retype every `click.ClickException` reachable from `compile_endpoint_policy`'s call graph, not only the `--output` `UsageError`.** `_evaluate_endpoint` (`tools/policy_cli.py:183,202,212,218` — no installed agent, an incomplete graph, a non-queryable component, an OSV federation warning) and `_managed_key_collisions` (`tools/policy_cli.py:133,264,268,276,281,289,301` — a key collision, an unreadable managed-settings directory or file) all raise `click.ClickException` today, and every one is on the path a programmatic caller of `compile_endpoint_policy` hits, not only the argument check at the top. A CLI exception in a library function makes the caller depend on Click. Split by kind: the `--output` check and the managed-settings failures (collision, unreadable directory/file, malformed JSON) become `PolicyValidationError`; the endpoint-evaluation failures (no agent found, incomplete graph, non-queryable component, OSV warnings) become `PolicyEvaluationError`. The command's `except (PolicyValidationError, PolicyEvaluationError)` at `tools/policy_cli.py:95-96` already catches both, so no change is needed there — only the `raise` sites move off `click`.
- [ ] **Split `emit_policy_report`.** It prints, so it cannot be promoted as it stands — a library that writes to stdout cannot be composed. Extract a pure `render_policy_report(report, output_format) -> str` and leave the `click.echo` of its result in the command.
- [ ] Assert `openaca policy compile` behaves identically after both changes — same output, same exit status, same usage error when `--output` is omitted without `--dry-run`. The retype is in fact invisible from the command line, since the command performs that same check itself before calling the function.
- [ ] Do **not** add a `host` parameter to the facade, and record why with its expiry. `--host` is a different axis from `--kind`: `--kind` scopes discovery to an installed agent kind, while `--host` names whose settings format the artifact is compiled into. It does not reach `compile_endpoint_policy` because there is no dispatch to reach — the Claude compiler is called unconditionally inside the compilation path, so `Choice(["claude"])` gates the input rather than selecting anything. Adding a facade argument now would invent a contract the compiler does not have.
- [ ] **Leave a note where the second host compiler will be written**, in the compilation path itself: when a second one lands, `--host` becomes a dispatch key and the facade needs the argument, or a programmatic caller silently gets Claude's format whatever it asked for. That is the moment this decision expires, and it should not be discovered by a caller.
- [ ] Leave two behaviours in the command, where they belong: the note printed when `--project` was omitted, and the translation of `PolicyValidationError` / `PolicyEvaluationError` into a Click exception. A programmatic caller prints its own note and catches the domain errors.
- [ ] Add both names to `openaca/core/policy.py` and `openaca/core/__init__.py`'s `__all__`.
- [ ] Four gates + full suite green. Commit.

### Step 5 — Publish the CLI group

- [ ] Add a failing test: `from openaca.cli import main` yields a `click.Group`,
  and `main.commands` contains `scan`, `bom` and `policy`.
- [ ] Create `openaca/cli.py` re-exporting `tools.cli:main`. One module, one
  name. Leave the console script pointing at `tools.cli:main` so nothing about
  running `openaca` changes.
- [ ] A test that mounting one of those command objects under a *different*
  Click group and invoking `--help` renders the other group's program name —
  the property that makes this useful to a consumer, and the one that would be
  silently lost if a command were ever rebuilt to hardcode its usage string.
- [ ] Document in `docs/reference/cli.md` what the name promises: that the group
  exists and those three commands are on it. Not their option sets, not their
  output formats — those remain the CLI's contract with its users, which is
  looser than a library API and must not be mistaken for one.
- [ ] Four gates + full suite green. Commit.

### Step 6 — Remove the hosted-service client

- [ ] Write the superseding ADR (`docs/adrs/0063-remote-sync-removal.md`):
  OpenACA ships no upload path. Record what it replaces and why the alternative
  — keeping a generic upload interface — was rejected. Set `status: superseded`
  and `superseded-by: 0063` in the frontmatter of 0024, 0032, 0050, 0051 and
  0061. Update `docs/adrs/INDEX.md`.
- [ ] Delete `tools/remote/`, `tests/remote/`, `deploy/remote/`. That removes
  all four subcommands — `configure`, `status`, `sync endpoint` and
  `policy compile` — since `remote/cli.py` holds them all.
- [ ] Check this step itself moves or changes nothing further in
  `tools/policy.py` or `tools/policy_cli.py` — only Step 4 touched them.
  `remote policy compile` imports `parse`, `compile_endpoint_policy` and (after
  Step 4) `render_policy_report` from them; deleting its caller must not
  disturb the callees, which `openaca policy compile` also uses.
- [ ] Remove the import and `add_command` registration in `tools/cli.py:12,39`.
- [ ] Remove the three remote tests and their imports from `tests/test_e2e.py`
  (lines 32-33, 783-784, 1714), and delete
  `test_scan_and_collector_import_the_shared_no_manifests` from
  `tests/test_posture_cursor.py`.
- [ ] **`test_github_and_docker_mcp_refs_survive_identity_lifecycle`
  (`tests/test_e2e.py:1126-1186`) is a fourth caller, not one of the three
  named above.** Its first five assertions (BOM round-trip, rendering, OSV
  federation, lines 1126-1176) exercise ordinary scan behaviour and import
  nothing from `tools.remote`. Its last block (lines 1178-1186) calls
  `_prepare_remote_bom` to assert that upload-specific install-source trimming
  redacts secrets — the same trimming Step 3 explicitly leaves behind and this
  step deletes with `tools/remote/`, and which "Out of scope" already says is
  removed, not relocated. Deleting only the imports at lines 32-33 without
  touching this test leaves a `NameError` at line 1178. Delete lines 1178-1186
  and the now-dangling `prepared = ` setup; keep the rest of the test and its
  `_props_by_name` helper (`tests/test_e2e.py:1189-1190`), which two other
  tests still use.
- [ ] Drop `httpx` from `pyproject.toml`; refresh `uv.lock`.
- [ ] Delete `docs/remote-deployment.md`; remove the remote sections from
  `docs/reference/cli.md` and `docs/README.md`, and — in the root `README.md`
  — the whole "When a remote policy is configured..." paragraph and its shell
  block (`README.md:160-172`), not just the `openaca remote configure` line.
  That block also runs `openaca remote policy compile`; leaving either survives
  the removal it documents.
- [ ] **`docs/specs/policy-compiler.md` documents the deleted command too, and
  it is not in the list above.** Line 161 gives
  `openaca remote policy compile --target ~/.claude --host claude --output …`
  as a runnable example alongside the surviving local `compile` invocations,
  and lines 220-227 describe its behaviour in detail (it "reads the existing
  remote configuration from `openaca remote configure`, fetches the current
  policy document..."). This is the spec for the command this step keeps —
  `openaca policy compile` — so leaving either in place documents a sibling
  command that no longer exists right next to the one that still does. Delete
  line 161 and the paragraph at lines 220-227.
- [ ] **`docs/specs/cursor-agent-kind.md` still lists `remote sync endpoint`
  as a live endpoint command.** Line 147 names it alongside `scan endpoint`
  and `bom endpoint` as one of the three commands `--kind` applies to, and the
  bullet at line 223 ("A dry-run remote sync reflects kind selection
  identically to a scan. The rules above are one contract across all three
  endpoint commands...") describes its dry-run behaviour as part of that same
  three-command contract. This spec stays active — it still governs
  `scan endpoint`/`bom endpoint` — so it is edited, not deleted: drop
  `remote sync endpoint` from the line-147 list (two commands remain) and
  delete the line-223 bullet.
- [ ] **`docs/specs/collector-agent-rooted-uploads.md` still frames
  `remote sync endpoint` as current behaviour** — its before/after table
  (line 17) and its verification section (`openaca remote sync endpoint
  --dry-run`, line 147) both describe the command in the present tense. Unlike
  `docs/remote-deployment.md`, this one cannot simply be deleted: ADR-0050 and
  ADR-0051 both cite it as their design record (`docs/adrs/0050-…md`,
  `docs/adrs/0051-…md`), and `docs/specs/multi-agent-support.md` links to it
  twice for the agent-rooted wire-format rationale that outlives this removal.
  Add a one-line note under the title marking the document historical — the
  command it describes stopped existing as of this plan's ADR-0063 — without
  rewriting the migration narrative itself, which is what those other three
  documents still point to.
- [ ] **`docs/specs/codex-agent-kind.md` names the hosted product this removal
  disconnects from, not just a deleted command.** Its "Approval posture is
  scanned, not uploaded" section (lines 181-191) says the two approval rules
  "do not yet reach OpenACA Cloud," are "held back at the upload boundary by
  `_UPLOAD_DEFERRED_RULES` (`tools/remote/collector.py`)," and links the
  hosted side's tracking issue. `_UPLOAD_DEFERRED_RULES` and the module it
  lives in are deleted by this step, and naming "OpenACA Cloud" and its issue
  tracker here already conflicts with this plan's own constraint that no
  artifact in this repository names a downstream consumer. Rewrite the section
  to say plainly that both rules are ordinary scan output with no upload
  boundary to be held back from — `openaca scan endpoint --kind codex
  --include-posture` already reports both unconditionally, and after this
  removal there is no other path for them to be filtered from. Remove the
  `_UPLOAD_DEFERRED_RULES`/OpenACA Cloud/tracking-issue sentences entirely
  rather than updating them in place.
- [ ] **`docs/specs/multi-agent-support.md:765` cites `tools/remote/cli.py` as
  one of three surviving copies of the duplicated endpoint config-dir
  resolver**, alongside `tools/scan.py` and the Claude Code kind's own
  `config_root`. After this step only two copies remain. Update "Three copies
  remain" to "Two copies remain" and drop the `tools/remote/cli.py` mention;
  the rest of that bullet's point (the duplication is deferred, not
  consolidated) still holds for the two that stay.
- [ ] **`docs/specs/default-taxonomy-surfacing.md`'s "Out Of Scope" section
  presents the deleted upload contract as a live, deliberately-untouched
  concept.** Its "Remote upload payload" bullet (lines 230-234) says
  `_finding_taxonomies` at `tools/remote/collector.py:808` "is a different
  concept" from what that spec changed, and that widening it "would widen the
  upload contract in `tools/remote/upload_contract.py` and belongs to its own
  decision" — both paths this step deletes. That spec is otherwise unaffected
  and stays active (it governs taxonomy rendering in `scan`, untouched here),
  so edit rather than delete: reword the bullet to say the upload payload no
  longer exists as of this removal, so there is nothing left to widen, rather
  than describing it as a deferred decision on code that used to be there.
- [ ] **Remove the stale next-action from `scan`.** `tools/scan.py:722`
  (`_next_actions_for`) unconditionally appends
  `"sync to remote: openaca remote sync endpoint"` to every installed-agent
  scan's next-action list; left in place it recommends a command that no
  longer exists. Delete the line. `tests/test_render.py:2349-2372`
  (`test_render_text_cards_separate_agents_and_dedupe_next_actions`) uses that
  same string as an arbitrary example next-action to test dedup rendering —
  it is not asserting `_next_actions_for`'s real output, so it does not break,
  but update its fixture string to something that isn't a deleted command, so
  the test doesn't read as evidence the command still exists.
- [ ] **Remove the remote smoke gates, or CI fails on the removal commit.**
  `.github/workflows/ci.yml` runs `openaca remote configure` and
  `openaca remote sync endpoint` against an unreachable port and asserts exit 2
  with no traceback; `scripts/ci-local.sh` does the same. Both invoke a command
  that will not exist. Delete those blocks in the same commit as the removal —
  they are a gate, not documentation, so leaving them for a follow-up breaks
  `main`.
- [ ] Note for whoever picks up the downstream side: that smoke test is worth
  keeping wherever the capability ends up. Asserting a clean exit code and no
  traceback on an unreachable endpoint is a better first test than most, and it
  is cheap.
- [ ] Add a test asserting `openaca --help` lists no `remote` command, so the
  removal cannot be silently undone by a later merge.
- [ ] Four gates + full suite green. Commit.

### Step 7 — Prove the scanner is whole

- [ ] `uv build`, install the wheel into a clean venv **without** `httpx`, and
  run: `openaca scan endpoint --format json`, `openaca bom endpoint`,
  `openaca scan repo --target tests/fixtures/repos/repo-surface-golden`,
  `openaca lint overlays/`, `openaca export`, `openaca triage --help`. All
  succeed. `target` is a required `click.argument` on `lint`
  (`tools/lint.py:228-229`), so a bare `openaca lint` fails with a usage error
  before it exercises anything — this must point at a real corpus path.
- [ ] `openaca scan endpoint`'s output contains no occurrence of `remote`, since
  Step 6 removed the one next-action that named it.
- [ ] And specifically that local policy still works end to end, since it shares
  code with the deleted command: `openaca policy validate <file>` and
  `openaca policy compile <file> --target … --host claude --dry-run`.
- [ ] And that `collect_installed_agents` still works from the built wheel, since
  it is now public API and its implementation moved in Step 3.
- [ ] And that `from openaca.cli import main` works from the built wheel, with
  `scan`, `bom` and `policy` on the group.
- [ ] And that `compile_endpoint_policy` and `render_policy_report` are importable
  from `openaca.core` in that wheel, since the local `policy` command now shares
  them.
- [ ] `python -c "import httpx"` fails in that venv, proving the dependency drop
  is real and nothing imports it at runtime.
- [ ] Release notes for the next version: the `remote` command group is removed,
  naming the last release that carried it. State the scope reason; name no
  downstream consumer.

## Out of scope

- Any upload path, payload schema, transport redaction, retry policy or
  credential store. Removed, not relocated.
- A deprecation shim for `openaca remote`. Removal is immediate and breaking.
- **Any new CLI surface**, in particular a flag to emit the Agent BOM that
  `scan endpoint` already builds and discards. Useful to a consumer, defensible
  on its own merits, and therefore its own change — a removal that also adds a
  feature is two changes wearing one review.
- The `openaca:sync` skill, which lives in the marketplace plugin repository and
  must be retired there. Tracked separately; this plan cannot land it.
- The two posture rules in the removed uploader's deferred set. They are ordinary
  scanner output; only the uploader treated them specially.
