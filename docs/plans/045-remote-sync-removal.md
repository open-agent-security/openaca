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
  `CollectionError` (`ValueError` subclass, no `exit_code`) alongside the
  finding types, raised when a requested external scanner is not installed;
  the CLI layer that still needs an exit code catches it and raises
  `click.ClickException` itself.
- [ ] `target` becomes an `include_target: bool = True` argument. The uploader
  hardcoded `None` with the comment "the upload names no place" — the right
  decision in the wrong place. Defaulting to `True` leaves CLI behaviour
  unchanged.
- [ ] `openaca/core/collect.py` re-exports `collect_installed_agents`,
  `AgentCollection`, `PostureFinding`, `ObservationFinding`, `Standards` and
  `CollectionError` — six names. Update `openaca/core/__init__.py`'s imports
  and `__all__`.
- [ ] A test asserting the facade exposes **exactly** those six names and that
  `Graph`, `AgentInstance`, `DiscoveryContext`, `WarningLog`, `kind_for`,
  `resolve_coverage` and `build_agent_graph` are **not** reachable through
  `openaca.core`. This is the test that stops the surface growing by
  convenience.
- [ ] A test that a requested external scanner which is not installed raises
  `CollectionError`, not `CollectError` or another internal exception type.
- [ ] Point `tools/remote/collector.py` at the new location so the uploader keeps
  working until Step 4 deletes it, and the suite stays green at this commit.
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
  `openaca lint`, `openaca export`, `openaca triage --help`. All succeed.
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
