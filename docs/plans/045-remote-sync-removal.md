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
tree contain a half-migrated uploader. The removal commit changes no scanner
behaviour and adds no CLI surface.

**Spec:** `docs/specs/remote-sync-removal.md`

**ADRs:** 0028 (the facade). A new ADR supersedes 0024, 0032, 0050, 0051 and
0061.

## Constraints

- [ ] No command other than `remote` changes behaviour. `scan`, `bom`, `lint`,
  `export`, `promote`, `seed`, `triage` and `policy` produce identical output
  for identical input, before and after.
- [ ] **All four `remote` subcommands go, including `policy compile`.** The
  group is not only an uploader: `remote policy compile` *downloads* the
  organisation policy and compiles it. Both directions are a hosted-service
  client and both leave. Keeping the download would retain `client.py`,
  `config.py`, a credential and `httpx` — every part of what this removes — so
  a partial removal is not a smaller version of this change, it is no version
  of it.
- [ ] **`openaca policy` — the local command — is untouched.** `validate` and
  `compile` over a policy file on disk stay exactly as they are, as do
  `tools/policy.py`, `tools/policy_cli.py` and `openaca.core.policy`. What goes
  is one network call, not the policy language, the evaluator, the risk gates or
  the host compiler.
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
- [ ] **The collection API is one function, one result type, and the finding
  types it carries — four public names.** Nineteen internal symbols would
  otherwise be reachable; none of them is promoted, and neither private counter
  becomes public. If a review of this step is adding names, it has gone wrong.
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
- [ ] `target` becomes an `include_target: bool = True` argument. The uploader
  hardcoded `None` with the comment "the upload names no place" — the right
  decision in the wrong place. Defaulting to `True` leaves CLI behaviour
  unchanged.
- [ ] `openaca/core/collect.py` re-exports `collect_installed_agents` and
  `AgentCollection`; add `PostureFinding`, `ObservationFinding` and `Standards`
  as value types. Update `openaca/core/__init__.py`'s imports and `__all__`.
- [ ] A test asserting the facade exposes **exactly** those names and that
  `Graph`, `AgentInstance`, `DiscoveryContext`, `WarningLog`, `kind_for`,
  `resolve_coverage` and `build_agent_graph` are **not** reachable through
  `openaca.core`. This is the test that stops the surface growing by
  convenience.
- [ ] A test that a requested external scanner which is not installed produces a
  public, typed error rather than an internal exception type.
- [ ] Point `tools/remote/collector.py` at the new location so the uploader keeps
  working until Step 4 deletes it, and the suite stays green at this commit.
- [ ] Four gates + full suite green. Commit.

### Step 4 — Policy compilation through the facade

- [ ] Add a failing test: `openaca.core.compile_endpoint_policy(parse_policy(doc), target=…, dry_run=True)` returns a report, and `render_policy_report(report, "text")` returns the string `openaca policy compile` prints.
- [ ] Promote `compile_endpoint_policy` to `openaca/core/policy.py`. It is already typed and already returns its report as a dict, so this is a re-export — with one fix below.
- [ ] **Replace its `click.UsageError`** for *"output is required unless dry-run"* with `PolicyValidationError`. A CLI exception in a library function makes the caller depend on Click; the command layer translates it into a usage error, as it already does for other policy failures.
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
- [ ] Check nothing in `tools/policy.py` or `tools/policy_cli.py` moves or
  changes. `remote policy compile` imports `parse`, `compile_endpoint_policy`
  and `emit_policy_report` from them; deleting its caller must not disturb the
  callees, which `openaca policy compile` also uses.
- [ ] Remove the import and `add_command` registration in `tools/cli.py:12,39`.
- [ ] Remove the three remote tests and their imports from `tests/test_e2e.py`
  (lines 32-33, 783-784, 1714), and delete
  `test_scan_and_collector_import_the_shared_no_manifests` from
  `tests/test_posture_cursor.py`.
- [ ] Drop `httpx` from `pyproject.toml`; refresh `uv.lock`.
- [ ] Delete `docs/remote-deployment.md`; remove the remote sections from
  `docs/reference/cli.md`, `docs/README.md`, and the `openaca remote configure`
  line in the root `README.md`.
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
  `openaca lint`, `openaca export`, `openaca triage --help`. All succeed.
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
