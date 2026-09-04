# Plan 046 — Remote client removal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `openaca` loses the `remote` command group, `tools/remote/`,
`tests/remote/`, `deploy/remote/` and the `httpx` dependency — and loses
nothing else. Afterwards the tree holds no client for any hosted service: no
credential, no API URL, no upload path, no account, no tenant, and no
company hostname. Every other command behaves exactly as it did, with the one
exception this plan states and honours (Task 1).

**Architecture:** Purely subtractive, and ordered so that the tree is green at
every commit. This is the destructive phase of a three-phase migration, and the
two phases before it are preconditions rather than background:

| Phase | Repo | State | Artefact |
|---|---|---|---|
| 1 | this repo | merged, **not yet released** | `docs/plans/045-published-consumption-surfaces.md` — `openaca.core` publishes collection, policy compilation, kind selection and the finding types; `openaca.cli` publishes the Click group |
| 2 | consumer | **unconfirmed from here** | the uploader — client, redaction, spool, orchestrator, `remote sync`, `remote policy compile` — is acquired there, and it mounts openaca's `scan`, `bom` and `policy` commands |
| 3 | this repo | **this plan** | the client leaves |

Neither phase-1 nor phase-2 state is something this repository can assert on its
own: the first is a property of a published artefact, the second of a repository
this plan does not see. Both are checked under *Preconditions* before Task 1.

Phase 1 moved every shared piece out on purpose — `tools/collect.py`,
`tools/atomic_write.py`, `tools/kind_selection.py`, `tools/policy_compile.py`,
`tools/posture/agent_surface.py` — so the deletion has no load-bearing import
to unpick. That was verified against the tree, not assumed: see *Verified
inventory*.

**Spec:** `docs/specs/remote-client-removal.md`.

**ADRs:** one new ADR, **ADR-0063**, superseding ADR-0032, ADR-0050, ADR-0051
and ADR-0061. Not ADR-0024, which is already superseded by ADR-0032 — see
Task 8.

## Preconditions

The spec makes these checkable rather than a matter of scheduling. **Every one
of them gates Task 1**, not Task 5: no task here is independently shippable
before the released surface exists, because the first commit that lands takes a
capability away from a tree that has not yet handed it over.

**The current checkout is not evidence for any of them.** This branch imports
the plan-045 surfaces, but the released artefact is what a consumer pins.
`v0.5.0` (`db5d592`, tagged before this work) is **not** that release: at that
tag `openaca/core/__init__.py` exports neither `collect_installed_agents` nor
`compile_endpoint_policy`, and `openaca/cli.py` does not exist — the commits
publishing them (`6bed159`, `3b15da3`, `f4859e6`) are later. `pyproject.toml`
reading `version = "0.5.0"` and `docs/releases/v0.5.0.md` existing are records
of that earlier release, not of a release carrying phase 1.

- [ ] **A release exists that carries plan 045.** Name it here before starting
  (`vX.Y.Z`, tagged at or after `f4859e6`), and confirm the tag is published
  and the artefact is on the index.
- [ ] **Both imports succeed against the installed wheel from that release**, in
  a clean environment — not against this checkout. `--isolated` isolates the
  *environment*, not `sys.path`: `python -c` puts the working directory first,
  so from the repository root `import openaca` resolves to `./openaca/` and the
  check passes no matter what the wheel contains. Run it from a scratch
  directory outside the repository, with `-P` to drop the working directory, and
  assert what was actually loaded before trusting the imports:

  ```
  cd "$(mktemp -d)" && uv run --isolated --with "openaca==<that version>" python -P -c "
  import openaca, importlib.metadata as md
  assert 'site-packages' in openaca.__file__, openaca.__file__
  assert md.version('openaca') == '<that version>', md.version('openaca')
  from openaca.core import collect_installed_agents, compile_endpoint_policy
  from openaca.cli import main
  "
  ```

  Prove the check can fail before believing it passed: substitute
  `openaca==0.5.0` and the `openaca.core` import must raise `ImportError` on
  `collect_installed_agents`. If it succeeds, the command is loading the
  checkout and neither assertion caught it — fix the invocation before reading
  anything into the result.

- [ ] **Operator confirmation** that a consumer has migrated onto those
  surfaces and can replace collection, policy retrieval and compilation, and
  the mounted CLI behaviour. This is deliberately not verifiable from inside
  this repository — the project does not know what consumes it. It is a human
  sign-off, not a test, and it is the one precondition an agent must stop and
  ask about.
- [ ] **The breaking release's notes are a named handoff, not an assumption.**
  The spec's compatibility story is "the release notes name the last version
  carrying `remote`". Writing them is the release skill's job at tag time
  (*Deferred*), but the version they must name is knowable now: it is the last
  release before this plan lands. Record it here and hand it to whoever cuts
  the release.

  **That version is `v0.5.0`** (`db5d592`), the newest tag that still carries
  `tools/remote/`, `deploy/remote/` and the `remote` command registration in
  `tools/cli.py`.
- [ ] **Capture the behavioural baseline before Task 1 edits anything.** Task 8
  Step 7 diffs against it, and after several commits there is no clean base
  left to capture from. Into a scratch directory outside the repo:
  - `uv run openaca <cmd> --help` for `scan`, `scan repo`, `scan endpoint`,
    `scan bom`, `bom`, `bom repo`, `bom endpoint`, `bom diff`, `lint`,
    `export`, `promote`, `seed`, `triage`, `policy`, `policy validate`,
    `policy compile`, and top-level `openaca --help`.
  - Functional captures, each with its exit code recorded, over fixtures built
    in a `tmp` directory: `scan endpoint --kind claude-code --config-dir <fx>
    --no-color`, the same with `--json`, `bom endpoint --kind claude-code
    --config-dir <fx> --output-dir <out>` plus the emitted document, and
    `policy compile <policy.yaml> --target <fx> --host claude --output <art>`
    plus the artifact it writes. Build the fixture with **no OSV-queryable
    components** — the same property the `smoke-install` job relies on — so the
    capture makes no network call and is reproducible. BOM documents carry no
    timestamp or serial number, so these compare byte-for-byte.

## Constraints

- [ ] **Removal only.** If a task finds it needs to *add* a public API, stop:
  that is a sign this spec was started too early. Report it rather than adding
  one. (No task below needs one — Task 4 Step 3 was the candidate and it turns
  out `safe_pinned_mcp_install_source` is already on `openaca.core`.)
- [ ] **No deprecation shim** for `openaca remote`. No alias, no stub command
  printing a migration message, no `hidden=True` survivor.
- [ ] **No behaviour change** to scan semantics, BOM contents, posture rule
  behaviour, advisory matching, or any output format — **except** the
  `sync to remote: openaca remote sync endpoint` next-action line, which Task 1
  removes because it advertises a command that will not exist. That is the
  single documented exception and the only one.
- [ ] `openaca policy validate|compile` take the same arguments, write the same
  artifact, print the same report and exit with the same code before and after.
  Same for `scan repo`, `scan endpoint`, `scan bom`, `bom repo`, `bom endpoint`,
  `bom diff`, `lint`, `export`, `promote`, `seed`, `triage`, and every overlay
  in `overlays/`.
- [ ] **The tree is green at every commit.** Each task below names what keeps
  the four gates green; a task that cannot be split without a red intermediate
  says so and stays whole (Task 5).
- [ ] **Do not touch the consumer repository.** Retiring the packaged
  `openaca:sync` skill is out of repo and out of this plan (*Deferred*).
- [ ] **ADR bodies are not edited.** Superseding marks frontmatter only; an old
  PR must stay readable against the rules in force when it was opened.
- [ ] `docs/releases/*.md` and `docs/plans/0NN-*.md` for shipped work are
  historical records and are **not** rewritten, even where they describe
  `openaca remote`.

## Verified inventory

Established by reading the tree on `feat/remote-sync-migration`, not from the
spec's description of it.

**Deleted outright**

| Path | Size | Note |
|---|---|---|
| `tools/remote/__init__.py`, `cli.py`, `client.py`, `collector.py`, `config.py`, `upload_contract.py` | 2,234 lines | the whole package |
| `tests/remote/` (7 files) | 6,037 lines, **242 tests** | `test_cli.py` 27, `test_client.py` 15, `test_collect.py` 82, `test_config.py` 5, `test_deploy_scripts.py` 8, `test_redact_payload.py` 67, `test_upload_contract.py` 38 |
| `deploy/remote/jamf.sh`, `kandji.sh`, `intune-macos.sh` | 3 files | MDM scripts; only job is to configure a token and schedule uploads |
| `docs/remote-deployment.md` | 66 lines | |

**Imports of the package outside it — exactly one in production code**

- `tools/cli.py:12` — `from tools.remote.cli import main as remote_cmd`. The
  only non-test importer in the tree. Confirmed by
  `grep -rn "from tools.remote\|import tools.remote" tools/ openaca/`, which
  returns that line and nothing else.
- `openaca/core/*` references it nowhere, so no facade consumer is affected.
- `tools/scan.py`, `tools/bom_cli.py`, `tools/lint.py`, `tools/export.py`,
  `tools/matcher.py`, `tools/policy*.py`, `tools/triage*.py` import nothing
  from it. Confirmed.

**Test modules outside `tests/remote/` that must change — three, not two**

| File | What | Task |
|---|---|---|
| `tests/test_e2e.py` | module-level imports at lines 32–33; three whole tests (`test_remote_policy_compile_blocks_a_vulnerable_standalone_mcp_server` L733, `test_remote_upload_payload_is_agent_rooted_and_redacted` L1324, `test_e2e_codex_remote_sync_payload_is_acceptable_to_the_hosted_schema` L1702); **and the `_prepare_remote_bom` tail of a fourth test, which is an edit rather than a deletion** (`test_github_and_docker_mcp_refs_survive_identity_lifecycle`, L1178–1186) | 4 |
| `tests/test_collect.py:308` | `from tools.remote.collector import CollectError, build_endpoint_collections` inside `test_the_published_function_raises_scanner_unavailable_and_the_uploader_still_exits_1` | 4 |
| `tests/test_openaca_cli.py:30` | `assert "remote" in openaca.cli.main.commands` — plan 045 left this deliberately, with a comment naming the removal spec as what takes it out | 4 |

`tests/test_render.py:2358,2369` asserts the next-action string but imports
nothing from the package — Task 1. `tests/test_bom.py:1078` mentions the
collector in a docstring only — Task 8.

**CI smoke gates — verified, and they break `main` if left**

Both gates call `openaca remote configure` then assert `remote sync endpoint`
exits 2:

- `.github/workflows/ci.yml`, job `smoke-install`: comment at L89, steps at
  L131–150.
- `scripts/ci-local.sh`, job 2: L113–118.

They are the `smoke-install` job's exercise of the freshly-resolved `httpx`
stack, so removing `httpx` and the command without removing them turns `main`
red on the next push. Task 2 removes them before Task 5 unregisters the group.

**The vendor hostname — six live files carry `api.stacktrace.ai`**

`tools/remote/config.py:8` (`DEFAULT_API_URL`), `deploy/remote/jamf.sh:14`,
`deploy/remote/kandji.sh:4`, `deploy/remote/intune-macos.sh:4`,
`docs/remote-deployment.md:28`, `tests/remote/test_deploy_scripts.py:23`, plus
four assertions in `tests/remote/test_cli.py` (L147, 222, 246, 326). Every one
is inside a path this plan deletes, so no separate scrub is needed — but Task 8
Step 6 greps for the string as the acceptance check rather than trusting that.

**`socket.gethostname()` — the spec's claim holds**

`grep -rn "gethostname" --include="*.py"` returns exactly two hits:
`tools/remote/collector.py:901` and the monkeypatch of it at
`tests/remote/test_collect.py:1162`. Nothing else in the tree calls it. Both
leave.

**`httpx` — used only by the removed package**

Production hits are `tools/remote/{client,cli,collector}.py` only. `uv.lock`
lists `openaca` as httpx's sole dependent — nothing pulls it transitively. OSV
federation (`tools/osv_federation.py`) uses `urllib.request` from the standard
library and stays.

**Docs, config and comments naming the removed surface**

The list is the whole live tree, established by sweeping the removed
subsystem's vocabulary — `openaca remote`, `OPENACA_REMOTE`, `stacktrace`,
`tools.remote`, `uploader`, `upload envelope`, `upload contract`, `upload
boundary`, `upload BOM`, `remote sync`, `hosted service`, `hosted schema`,
`gethostname`, `httpx` — over every `*.py`, `*.md`, `*.toml`, `*.json`, `*.yml`
and `*.sh` outside the deleted paths and the historical records. Task 8 Step 6
re-runs that sweep as the acceptance check.

| File | Line(s) | Task |
|---|---|---|
| `README.md` | 160–172 — an `openaca remote configure` + `remote policy compile` block with its two framing paragraphs. The spec names L165–166, the two commands; the paragraphs above and below them exist only to introduce and qualify those commands, so the block goes whole | 6 |
| `docs/reference/cli.md` | 57 — one clause in the `--kind` sentence | 6 |
| `docs/README.md` | 41 — link to `remote-deployment.md` | 3 |
| `docs/concepts/identities.md` | 18 — sources the agent instance key's asset from "the upload envelope" | 6 |
| `docs/openaca-bom-schema.md` | 19 collector clause; 74 "the upload envelope"; 183 "a neutral literal on upload" | 8 |
| `.claude/skills/release-openaca/SKILL.md` | 286 — post-release step about `deploy/remote/*.sh` | 3 |
| `.claude/skills/release-openaca/SKILL.md` | 283 — "upload redaction" as a re-pin trigger | 3 |
| `.gitleaks.toml` | 1–10 — the whole allowlist exists for `tests/remote/`'s synthetic `ot_*` tokens | 6 |
| `docs/specs/policy-compiler.md` | 161 — `openaca remote policy compile` in the *Compile* invocation block; 220–227 — the paragraph specifying that command's behaviour. The only spec file this plan edits, because the removal spec names it | 6 |
| `tools/bom.py` | 34–36 comment; 143 "(the remote collector)" | 8 |
| `tools/graph_build.py` | 262–264 docstring, 3306 comment | 8 |
| `tools/cli_kind.py` | 3, `tools/kind_selection.py` 3 | 8 |
| `tools/collect.py` | 3–8 module docstring, 62 uploader parenthetical, 145 "the upload BOM", 231 comment | 8 |
| `tests/test_collect.py` | 1, 4, 66, 169, 276 docstrings; 295–305 the split test | 4 |
| `tests/test_core_facade.py` | 105–106 — two absence guards naming symbols that leave the tree | 8 |
| `tests/test_posture_cursor.py` | 78–80 docstring; **its tests all stay** | 8 |
| `tests/test_bom.py` | 643 "the remote-sync payload"; 1078 docstring | 8 |
| `tests/test_e2e.py` | 1743 — the surviving sibling's "upload boundary" docstring | 4 |

**The rule for a past-tense reference.** A live artifact may record what OpenACA
*used to* do — that history is often why a module is shaped the way it is. What
it may not do is (a) describe the client, the uploader, the upload envelope, the
spool, the redaction pass or the `remote` command as something OpenACA *has*,
(b) name a path or symbol under `tools/remote/`, `tests/remote/` or
`deploy/remote/`, or (c) point a reader at a test or file this plan deletes.
`docs/adrs/*`, `docs/releases/*`, `docs/plans/*` and `docs/specs/*` are
historical records and are exempt **except where the governing spec names an
edit** — which it does exactly once, for `docs/specs/policy-compiler.md`
(*Removal*). A generic mention of a *consumer* exporting or uploading a BOM is
not a reference to the removed client and stays.

That exception is narrow on purpose. A spec records what was decided when it was
written and is not rewritten as a side effect of implementing a later one; the
one file here is different because the governing spec judged it to read as
current documentation of a removed command and put it on the removal list, which
makes editing it an implementation requirement rather than a judgement call.

**Not touched, deliberately:** `docs/adrs/*` bodies, `docs/releases/*`,
`docs/plans/0NN-*` for shipped work, and every `docs/specs/*` file except this
plan's own spec and `policy-compiler.md`. `action.yml` and `SECURITY.md` were
checked and contain **no** remote reference. The `openaca:sync` skill is **not**
in this repository — `.claude/skills/` holds only `openaca-candidate-review` and
`release-openaca`.

`docs/specs/published-consumption-surfaces.md:59` names `openaca remote` among
the commands that must not change, and is not on the removal list. It does not
need to be: the clause reads "still present throughout this spec, and removed
later by `remote-client-removal.md`", which dates itself and points the reader
here. It is left alone.

**False positives — leave alone.** The CI/`ci-local.sh` fixture MCP server is
*named* `"remote"` (`{"mcpServers": {"remote": {...}}}`); that is a fixture key
for an HTTP-transport MCP server, not a hosted-service reference, and it must
survive Task 2 unchanged or the smoke fixture stops exercising what it
exercises. Likewise ADR-0020/0029/0031/0039/0042/0046/0049/0052/0057/0060 use
"remote" to mean *remote MCP server* or *Claude's remote settings* and are
untouched.

## Expected test count

**Baseline: 2386 collected** (`uv run pytest --collect-only -q`, verified on
this branch).

| After | Count | Derivation |
|---|---|---|
| Task 1 | 2386 | one assertion edited inside a surviving test |
| Task 2 | 2386 | shell/YAML only, no collected tests |
| Task 3 | 2378 | −8 (`tests/remote/test_deploy_scripts.py`) |
| Task 4 | 2375 | −3 whole e2e tests; the fourth e2e test, the `test_collect.py` split test and the `test_openaca_cli.py` test are all **edited in place and survive** |
| Task 5 | **2141** | −234 (the remaining six files in `tests/remote/`: 27+15+82+5+67+38) |
| Tasks 6–8 | 2141 | docs, dependency and ADR work only |

**2141 is the number the final gate must print.** Anything else means a test
was lost that this plan did not account for — investigate rather than accept
it. In particular, a count below 2141 after Task 4 means a surviving test was
deleted instead of edited.

---

## Task 1: The next-action line stops advertising a command that will not exist

**Files:** Modify `tools/scan.py`, `tests/test_render.py`, `tests/test_scan.py`.

The single documented behaviour change outside the `remote` group. It goes
first, alone, so a reviewer sees it as its own commit rather than discovering
it inside a deletion diff.

**What keeps the gates green:** the string is a literal appended to a list; no
module imports it and no other test asserts it (`grep -rn "sync to remote"`
returns exactly the two files below plus historical plans). The producer and
its only assertion change in the same commit.

- [ ] **Step 1: Substitute, do not subtract, in `tests/test_render.py`.** The
  test's docstring states two properties — a shared action is deduplicated
  *and* "per-agent actions that genuinely differ survive" — and
  `"sync to remote: openaca remote sync endpoint"` on `card_a` (L2358) is the
  only per-agent-only input in the file. Deleting it and its
  `assert two.count(...) == 1` (L2369) leaves `card_a` and `card_b` with
  identical `next_actions` and the second property untested, so replace rather
  than remove: put a per-agent action that is not a removed command on
  `card_a` — `"include project-local config: openaca scan endpoint --project ."`,
  which the producer really does emit for one agent and not another — and keep
  a `two.count(...) == 1` assertion on it. Run
  `uv run pytest tests/test_render.py -k
  render_text_cards_separate_agents_and_dedupe_next_actions` and confirm both
  assertions still discriminate.
- [ ] **Step 2: Delete the producer.** `tools/scan.py:722` —
  `actions.append("sync to remote: openaca remote sync endpoint")` inside
  `_next_actions_for`. The two surviving appends (`include project-local
  config:` and `emit Agent BOM:`) keep their order and text exactly.
- [ ] **Step 3: Pin the output change at the command boundary.** The renderer
  test exercises `render_text` directly; nothing asserts what `scan endpoint`
  itself prints. Add to the existing
  `test_endpoint_omits_project_by_default_and_emits_note`
  (`tests/test_scan.py:901`, which already asserts both surviving actions are
  present) a single `assert "sync to remote" not in result.output`. That is the
  concrete evidence for the spec's sole permitted output change, and it costs no
  new test — the collected count is unchanged.
- [ ] **Step 4: Check no golden or snapshot fixture carries the line.**
  `grep -rn "sync to remote" tests/ overlays/ docs/reference/` must return
  nothing after this task.
- [ ] **Step 5: Run the four gates. Commit.** Message must say this is the one
  stated behaviour change outside `remote`, and why (the line advertises a
  command being removed).

**Acceptance:** `openaca scan endpoint` prints two next actions, not three, and
a test at the command boundary says so. 2386 tests pass. No other command's
output changes.

## Task 2: The CI smoke gates stop invoking the removed command

**Files:** Modify `.github/workflows/ci.yml`, `scripts/ci-local.sh`.

**This must land before Task 5.** Both gates run `openaca remote configure` and
require `remote sync endpoint` to exit 2. Unregistering the group first turns
`main`'s `smoke-install` job red, and `scripts/ci-local.sh` — the pre-push
hook's CI-parity script — red with it.

**What keeps the gates green:** neither file is executed by the four gate
commands, and the steps removed are the last two in each smoke sequence, so the
preceding steps' `set -eu` semantics are unaffected. `scripts/ci-local.sh` must
be run manually once to prove it still passes end to end (Step 5) — the four
gates will not catch a break here.

- [ ] **Step 1: `.github/workflows/ci.yml`.** Delete the `remote configure +
  sync` block (L131–150): the comment, the `export HOME` / `mkdir` lines that
  exist only for it, the `OPENACA_REMOTE_TOKEN=... remote configure`
  invocation, the `set +e` / `out=$(...)` / `code=$?` / `set -e` sequence and
  both `if` checks. Keep the closing
  `echo "smoke ok: CLI surfaces work against freshly-resolved deps"`.
- [ ] **Step 2: Fix the job's header comment** at L88–90. It currently reads
  "…and the remote sync targets an unreachable port (per linter discipline,
  external APIs must not gate PRs)". The *reason* — the smoke job makes no
  network calls, so a flaky external API cannot gate a PR — still holds and
  must survive; only the clause naming remote sync goes. Reword to say the scan
  fixture has no OSV-queryable components and the job therefore makes no
  network calls.
- [ ] **Step 3: `scripts/ci-local.sh`.** Delete L113–118 — the
  `HOME="$SMOKE/home"` line, the `remote configure`, the `remote sync endpoint`
  capture, the exit-2 assertion and the `Traceback` grep. Note that the
  `grep -q Traceback <<<"$out" && fail ...` line is the last command before the
  success `printf`; deleting it changes the script's exit status contribution,
  so confirm the script still ends by printing its green line.
- [ ] **Step 4: Do not touch the `.mcp.json` fixture** in either file. Its MCP
  server is *named* `"remote"` and that is a fixture key for an HTTP-transport
  server, not a reference to the removed subsystem. Removing or renaming it
  changes what the smoke target exercises.
- [ ] **Step 5: Run `bash scripts/ci-local.sh` end to end.** It must build the
  wheel, install with deps resolved fresh, run `--version`, `scan repo` and
  `bom repo`, and print its success line. This is the only proof the edit is
  correct; the four gates do not execute it.
- [ ] **Step 6: Run the four gates. Commit.**

**Acceptance:** `scripts/ci-local.sh` passes. Neither file contains
`openaca remote`. The `smoke-install` job still exercises `--version`,
`scan repo` and `bom repo` against a freshly-resolved dependency set — the
reason the job exists.

## Task 3: The MDM deployment scripts and their documentation leave

**Files:** Delete `deploy/remote/jamf.sh`, `deploy/remote/kandji.sh`,
`deploy/remote/intune-macos.sh`, `tests/remote/test_deploy_scripts.py`,
`docs/remote-deployment.md`. Modify `docs/README.md`,
`.claude/skills/release-openaca/SKILL.md`.

Self-contained: the scripts install a token and schedule uploads, nothing
imports them, and their only test lives beside them.

**What keeps the gates green:** `tests/remote/test_deploy_scripts.py` is the
sole reader of those files and is deleted in the same commit. It imports
nothing from `tools/remote/`, so it can go before the package does. No
production module references `deploy/`.

- [ ] **Step 1: Delete the three scripts and `tests/remote/test_deploy_scripts.py`.**
  `deploy/remote/` should then be empty; remove the directory. Check whether
  `deploy/` has other contents before removing the parent — `find deploy -type f`
  currently lists only these three.
- [ ] **Step 2: Delete `docs/remote-deployment.md`** and its link in
  `docs/README.md:41` (`- [Remote Deployment](remote-deployment.md)`). Confirm
  no other doc links to it: `grep -rn "remote-deployment" --exclude-dir=.git .`
  must return nothing outside `docs/plans/` and the spec.
- [ ] **Step 3: `.claude/skills/release-openaca/SKILL.md`.** Two edits, both
  confined to the in-repo half of what the file says.
  - L286, the "Served install scripts" post-release item: remove the
    `deploy/remote/*.sh` clause and the parenthetical about the b6→b7
    `fleet`→`remote` rename window. **Keep the item.** Its other referent is
    the site's `collect.sh`, which lives outside this repository — this plan
    cannot check whether that artifact still exists, and deleting the item on
    an assumption would drop a live release step. Removing only the clause
    makes no claim about the external artifact either way.
  - L283, the "Downstream consumers pinning `openaca==<old-version>`" item:
    "the release changed BOM identity shapes or upload redaction" names a
    redaction pass that leaves with the client. Drop the `or upload redaction`
    clause; the BOM-identity trigger is the one that survives.
- [ ] **Step 4: Run the four gates. Commit.**

**Acceptance:** 2378 tests pass (−8). `deploy/` and `docs/remote-deployment.md`
are gone. `grep -rn "api.stacktrace.ai"` now returns only `tools/remote/config.py`
and `tests/remote/test_cli.py`.

## Task 4: The three test modules outside `tests/remote/` stop reaching in

**Files:** Modify `tests/test_e2e.py`, `tests/test_collect.py`,
`tests/test_openaca_cli.py`.

After this task nothing outside `tools/remote/`, `tests/remote/` and
`tools/cli.py` touches the package, which is what makes Task 5 a clean cut.

**What keeps the gates green:** every edit here removes a reference to code
that still exists, so the suite passes before and after. Nothing is deleted
that these tests were the only cover for — Step 3 checks that explicitly.

- [ ] **Step 1: `tests/test_e2e.py` — delete the two module-level imports**
  (L32–33): `from tools.remote.collector import _prepare_remote_bom,
  build_endpoint_dry_run_payloads` and
  `from tools.remote.upload_contract import enforce_remote_upload_contract`.
- [ ] **Step 2: Delete the three whole tests.** The boundaries below are exact,
  and each ends at the blank lines before the next `def` — **delete by named
  test, and re-derive the boundary with
  `grep -n "^def " tests/test_e2e.py` rather than trusting a line number that
  has drifted.** Nothing between a named end and the next `def` belongs to the
  deleted test.
  - `test_remote_policy_compile_blocks_a_vulnerable_standalone_mcp_server`
    (L733–L811; the next test starts at L814) — the ADR-0061 remote variant.
    **Read its non-remote sibling first** (`test_policy_compile_blocks_a_
    vulnerable_standalone_mcp_server`, L659, which its docstring calls "the test
    above"). That sibling must survive and must still assert that a vulnerable
    standalone MCP server is blocked; if the remote variant carries an assertion
    the file-based one lacks, move the assertion into the sibling rather than
    losing it. Note the removed test's `FakeClient` and
    `monkeypatch.setattr("tools.remote.cli.…")` lines go with it.
  - `test_remote_upload_payload_is_agent_rooted_and_redacted` (L1324–L1359; the
    next test starts at L1362) — its subject is the upload contract, which
    leaves entirely. No relocation.
  - `test_e2e_codex_remote_sync_payload_is_acceptable_to_the_hosted_schema`
    (**L1702–L1739 and no further**; the next test starts at L1741) — its
    subject is the hosted payload schema, and it is the only test in that run
    that touches the removed package. **Everything from L1741 on is surviving
    local coverage and must not be swept up with it**: approval posture
    reporting locally (L1741), the disabled-MCP inventory/exposure split
    (L1759, which is where the `[mcp_servers.off_remote]` fixture and the
    URL-identity comment at L1777 actually live), enabled insecure transport
    (L1783), declared insecure transport (L1807) and profile-only project trust
    (L1826). The deleted test builds no fixture of its own beyond
    `_codex_home_with_approvals` (L1688) — **which stays**, because the
    surviving L1741 test calls it. Nothing here needs replacement coverage.
  - Its docstring names "the sibling test below" as the other half of an
    upload-boundary deferral. That sibling survives; its own docstring is
    rewritten in Step 6.
- [ ] **Step 3: Edit the fourth e2e test** —
  `test_github_and_docker_mcp_refs_survive_identity_lifecycle` (~L1123). Delete
  its tail from `prepared = _prepare_remote_bom(bom)` (L1178) through the four
  `install_source` assertions (L1186), and the now-unused `_props_by_name`
  helper **only if** nothing else in the file calls it — `grep -n
  "_props_by_name" tests/test_e2e.py` before deleting. The rest of the test —
  BOM round-trip, purl derivation, OSV query filtering, inventory rendering —
  is untouched and is the test's actual subject. Also update the section comment
  above it (L1123: "…and remote upload").
  - **The trimming assertions are not lost, and no API needs adding.**
    `_prepare_remote_bom` is the collector's wrapper; the trimming itself is
    `tools/identity.py`'s `safe_pinned_mcp_install_source` /
    `safe_unpinned_mcp_install_source`, which stay, are already published on
    `openaca.core` (`openaca/core/__init__.py` `__all__`), and are covered by
    `tests/test_component_ref.py` and `tests/test_core_facade.py`. Confirm that
    coverage by reading those two files before deleting the tail. **This was
    the one candidate for a "needs a new public API" stop, and it is not one.**
- [ ] **Step 4: `tests/test_collect.py`** —
  `test_the_published_function_raises_scanner_unavailable_and_the_uploader_still_exits_1`
  (L~300). **Rewrite in place; do not delete.** Its library half — that
  `core.collect_installed_agents` raises `ScannerUnavailable` carrying the
  scanner's message when `external_scanners` names a missing command — is a
  property of surviving code and the only test of it in this file. Remove the
  `from tools.remote.collector import CollectError, build_endpoint_collections`
  import (L308) and the `pytest.raises(CollectError)` half, rename the test to
  describe what is left (e.g.
  `test_the_published_function_raises_scanner_unavailable`), and rewrite the
  docstring and the `# --- the error boundary…` section comment above it, which
  currently describe a split against an uploader that no longer exists.
- [ ] **Step 5: `tests/test_openaca_cli.py:28–30`** — delete
  `assert "remote" in openaca.cli.main.commands` and its two-line comment. Plan
  045 wrote that comment naming this spec as what removes it, so this is the
  anchor the earlier phase left. The
  `assert set(openaca.cli.main.commands) >= {"scan", "bom", "policy"}` above it
  stays exactly as it is — it is the published promise.
- [ ] **Step 6: The surviving docstrings in these two modules stop describing a
  live uploader.** Each of these is on a test that passes unchanged; the text is
  what goes stale. Docstring and comment edits only — no assertion moves.
  - `tests/test_e2e.py:1741–1744` —
    `test_e2e_codex_approval_posture_still_reports_locally` opens "The other
    half of the deferral: held back from the upload, never from the scan." Both
    the other half and the upload leave. Rewrite it to state what it actually
    checks: Codex's two approval rules fire in a local endpoint scan.
  - `tests/test_collect.py:1` and `:4` — the module docstring calls this "the
    collection API below the uploader" and explains the copied fixtures by
    saying `tests/remote/test_collect.py` "is on its way out". After Task 5 the
    module it names is gone; say the fixtures are this module's own.
  - `tests/test_collect.py:66` — "the removed uploader mapped `rule_id` to
    `finding_id`…" is already past tense and stays: it is the accurate reason
    the returns-itself decision exists.
  - `tests/test_collect.py:169` — "The same assertion the upload BOM test
    makes" points at `test_remote_upload_payload_is_agent_rooted_and_redacted`,
    deleted in Step 2. Restate the property (a consumer shipping the document
    elsewhere must not carry an absolute local path) without the cross-reference.
  - `tests/test_collect.py:276` — "the uploader already treats it as one
    (`no installed agent found`)" cites removed behaviour as present. Keep the
    property, drop the citation.
- [ ] **Step 7: Prove the cut.**
  `grep -rn "tools\.remote" tests/ tools/ openaca/ | grep -v "^tests/remote/" | grep -v "^tools/remote/"`
  must return exactly one line: `tools/cli.py:12`.
- [ ] **Step 8: Run the four gates. Commit.**

**Acceptance:** 2375 tests pass (−3). `tools/cli.py:12` is the only remaining
reference to the package outside it. No surviving test lost an assertion about
surviving code, and no surviving test's docstring describes the uploader as
live.

## Task 5: The command group, the package and its tests leave together

**Files:** Modify `tools/cli.py`. Delete `tools/remote/` and `tests/remote/`.

**This task cannot be split, and that is the point.**
`tests/remote/test_cli.py` invokes the group through the top-level CLI —
`from tools.cli import main as openaca_main` then
`CliRunner().invoke(openaca_main, ["remote", …])` at 20+ call sites. So:

- unregistering first ⇒ every `tests/remote/test_cli.py` invocation returns
  exit 2 ⇒ red;
- deleting `tools/remote/` first ⇒ `tools/cli.py` raises `ImportError` at import
  ⇒ every test in the suite red;
- deleting `tests/remote/` first ⇒ green, but leaves a commit where 2,234 lines
  of shipped code have zero coverage.

One commit removing all three is the only ordering with no red intermediate,
and it is smaller than the alternative's blast radius. Take it whole.

**What keeps the gates green:** Tasks 1–4 removed every reference from outside
the deleted set, and Step 5 re-proves it with a grep before the gates run.

- [ ] **Step 1: `tools/cli.py`** — delete three lines: the import
  (`from tools.remote.cli import main as remote_cmd`, L12), the
  `remote_cmd.short_help = "Configure remote endpoint services."` assignment,
  and `main.add_command(remote_cmd, name="remote")`. Nothing else in that file
  changes; the remaining eight commands keep their registration order and their
  `short_help` text exactly.
- [ ] **Step 2: `git rm -r tools/remote tests/remote`.** All 6 modules and the
  6 test files still there — Task 3 already took `test_deploy_scripts.py`.
  Remove any `__pycache__` left behind.
- [ ] **Step 3: No shim.** Do not add a hidden command, an alias, a stub that
  prints a migration message, or an entry in a "removed commands" table
  rendered by the CLI. `openaca remote` must fail the way any unknown command
  fails: Click's own "No such command 'remote'." on exit 2.
- [ ] **Step 4: Check the help output.** `uv run openaca --help` lists eight
  commands — `scan`, `triage`, `bom`, `lint`, `export`, `promote`, `seed`,
  `policy` — and no `remote`. `uv run openaca remote --help` exits 2 with
  Click's unknown-command error and no traceback.
- [ ] **Step 5: Prove nothing dangles.**
  `grep -rn "tools\.remote\|tools/remote" tools/ openaca/ tests/ scripts/ .github/`
  must return nothing.
- [ ] **Step 6: Run the four gates. Commit.** The message is the one a future
  reader will find first — say what left, that the capability relocated rather
  than vanished, and that there is deliberately no deprecation window.

**Acceptance:** **2141 tests pass (−234).** `openaca --help` shows no `remote`.
`tools/` and `tests/` contain no `remote` package. Every other command's
`--help` output is byte-identical to before (Task 8 Step 7 checks this against
the baseline captured under *Preconditions*).

## Task 6: The last references to a hosted service leave the tree

**Files:** Modify `README.md`, `docs/reference/cli.md`, `.gitleaks.toml`,
`docs/concepts/identities.md`, `docs/specs/policy-compiler.md`.

The user-facing and conceptual documentation. Goal 2 — *nothing in the
repository refers to a hosted service, holds a credential, or names an
account* — is not met until they are done. Four of the five are on the spec's
removal list; `docs/concepts/identities.md` is not, and is here because it
defines a live identity key in terms of an envelope that leaves.

**What keeps the gates green:** documentation and lint configuration only; no
collected test reads any of them.

- [ ] **Step 1: `README.md:160–172`.** Delete the paragraph beginning "When a
  remote policy is configured, the equivalent endpoint command fetches…"
  (L160–162), the bash block that follows (L164–168: `openaca remote configure
  --token "$OPENACA_REMOTE_TOKEN"`, `openaca remote policy compile …`, the
  `sudo install …`), and the paragraph after it (L170–172: "It does not install
  the artifact. If the remote has no policy, cannot be reached, or returns an
  invalid policy…"). Keep the *file-based* `openaca policy validate` /
  `openaca policy compile` block above it untouched — it is the surviving path
  and the spec promises it is unchanged. The paragraph introducing that block
  already says OpenACA writes the artifact and an administrator installs it, so
  nothing true about surviving behaviour is lost with the deleted sentence; the
  remainder of it is about a remote that has no policy or cannot be reached.
- [ ] **Step 2: `docs/concepts/identities.md:18`.** The agent instance-key table
  sources the `asset` part from "the upload envelope; for a document read off
  disk, wherever the file came from". The envelope leaves, and this is live
  conceptual documentation of a surviving identity model, not a historical
  record. Rewrite the cell so the asset is sourced from wherever the document
  was collected or read — the second clause, generalised — with no envelope.
  Do not touch the key itself: asset, `openaca:agent_kind`, `openaca:agent_id`
  is ADR-0045's key and is unchanged by this plan.
- [ ] **Step 3: `docs/reference/cli.md:57`.** Rewrite
  "`scan endpoint`, `bom endpoint`, and `remote sync endpoint` all take `--kind`…"
  as "`scan endpoint` and `bom endpoint` both take `--kind`…". Adjust
  "all"→"both" so the sentence still reads. Nothing else in that file mentions
  the removed surface — verified.
- [ ] **Step 4: `.gitleaks.toml`.** Its single `[[allowlists]]` block exists
  only for `tests/remote/`'s synthetic `ot_*` tokens and its `paths` entry now
  points at a directory that does not exist. Delete the block and the
  two-line header comment explaining it, leaving `[extend] useDefault = true`.
  If that leaves the file with nothing but `[extend] useDefault = true`,
  keep the file — deleting it changes gitleaks' behaviour from "default rules"
  to "no config", which is a scanning change this plan has no business making.
  Confirm no workflow passes `--config` pointing at it
  (`grep -rn gitleaks .github/ scripts/` currently returns nothing, so it is
  consumed by a repository-level integration rather than by CI).
- [ ] **Step 5: `docs/specs/policy-compiler.md` — the one spec file this plan
  edits, and only its command-related material.** The spec's removal list names
  it because it documents `openaca remote policy compile` as *the* endpoint
  path, which reads as current documentation of a command that will not exist.
  Two deletions, both re-derived with `grep -n -i remote docs/specs/policy-compiler.md`
  rather than trusted as line numbers:
  - The `openaca remote policy compile --target ~/.claude --host claude --output
    /tmp/50-openaca-policy.json` line in the *Compile* invocation block (L161).
    The five `openaca policy validate|compile` lines above it stay in order and
    unedited — they are the surviving path the spec promises is unchanged.
  - The whole paragraph specifying that command (L220–227), from "`openaca
    remote policy compile` reads the existing remote configuration…" through
    "…compilation fails before replacing an existing artifact.", plus one of the
    blank lines around it so the *expected policy* paragraph above and the
    `## Claude Code target` heading below are separated by exactly one.
  - **Select by meaning, not by count.** Every other `remote` in that file means
    *Claude's remote settings* — the precedence discussion of remote settings,
    MDM, managed settings files and file drop-ins — which is an unrelated
    concept about a different product's configuration and is untouched. Read
    each remaining hit before leaving it; do not delete one because a count says
    so, and do not keep one because a count says so.
  - Nothing else in the file changes. The *Compile* prose describing `validate`
    and `compile`, the risk-gate semantics, the artifact-and-report definition
    and the Claude target section are all about surviving behaviour.
- [ ] **Step 6: Sweep.**
  `grep -rn -i "api.stacktrace.ai\|OPENACA_REMOTE\|openaca remote" --exclude-dir=.git .`
  must return hits only in `docs/plans/`, `docs/adrs/`, `docs/releases/` and
  `docs/specs/` — the historical records this plan does not rewrite —
  **with `docs/specs/policy-compiler.md` the one exception, which must now be
  clean**. Step 5 is what makes that true; a hit there is the edit not having
  landed, not an exempt record. The two remaining spec hits outside this plan's
  own spec are `collector-agent-rooted-uploads.md:147` and
  `published-consumption-surfaces.md:59`, both left alone.
- [ ] **Step 7: Run the four gates. Commit.**

**Acceptance:** No live file (code, config, README, reference or concept doc)
names a hosted service, a credential, an account or the vendor hostname.
`docs/specs/policy-compiler.md` documents `openaca policy compile` as the only
endpoint path and no longer mentions the removed command, while its Claude
remote-settings precedence discussion is intact.

## Task 7: `httpx` leaves the dependency set

**Files:** Modify `pyproject.toml`, `uv.lock`.

**What keeps the gates green:** nothing imports `httpx` after Task 5 —
verified: its only importers were `tools/remote/{client,cli,collector}.py` and
`tests/remote/`. `uv.lock` records `openaca` as its sole dependent, so no
transitive consumer loses it.

- [ ] **Step 1: Confirm before removing.**
  `grep -rn "httpx" --include="*.py" .` must return nothing. If it returns
  anything, stop — a module survived Task 5 that should not have.
- [ ] **Step 2: Delete `"httpx>=0.28.1,<1.0.dev0",`** from `pyproject.toml`'s
  `[project] dependencies`. Leave the other eight in place and in order.
- [ ] **Step 3: `uv lock`** and commit the regenerated `uv.lock`. Review the
  diff: it should drop `httpx` and whatever `httpx` alone pulled in (`httpcore`,
  `h11`, `anyio`/`sniffio` if nothing else needs them) and change nothing else.
  A version bump to an unrelated package in this diff means the lock was stale
  — regenerate on a clean tree or pin it back.
- [ ] **Step 4: Prove the wheel works without it.** `bash scripts/ci-local.sh`
  — it builds the wheel, installs it with deps resolved fresh, and runs
  `--version`, `scan repo` and `bom repo`. This is the spec's robustness-bar
  item "the scanner works from a built wheel with `httpx` absent", and the four
  gates run against the locked dev env, so they do not test it.
- [ ] **Step 5: Run the four gates. Commit.**

**Acceptance:** `httpx` appears nowhere in `pyproject.toml` or `uv.lock`.
`scripts/ci-local.sh` passes. 2141 tests pass.

## Task 8: ADRs, stale comments, and the final gate

**Files:** Create `docs/adrs/0063-remove-the-hosted-service-client.md`; modify
`docs/adrs/0032-remote-cli-namespace.md`,
`docs/adrs/0050-collector-upload-cardinality.md`,
`docs/adrs/0051-redaction-covers-bom-metadata.md`,
`docs/adrs/0061-remote-policy-compilation.md`, `docs/adrs/INDEX.md`,
`docs/openaca-bom-schema.md`, `tools/bom.py`, `tools/graph_build.py`,
`tools/cli_kind.py`, `tools/kind_selection.py`, `tools/collect.py`,
`tests/test_bom.py`, `tests/test_core_facade.py`, `tests/test_posture_cursor.py`,
`docs/plans/README.md`.

- [ ] **Step 1: Write ADR-0063.** `0063` is the next free id (`0062` is the
  highest present). Use `docs/adrs/TEMPLATE.md`. Frontmatter:
  `supersedes: [0032, 0050, 0051, 0061]`. The bracketed-list form is
  established — `docs/adrs/0009-overlay-only-v0.md` carries
  `supersedes: [0003, 0004, 0008]` — so this invents no syntax.
  - **Context:** OpenACA shipped a client for one hosted service — a stored API
    token, an upload path, an organisation-policy fetch, and a default API URL
    naming one company. Examining one machine is what OpenACA is for and
    belongs in the open; being a client for a particular service's account and
    payload schema is not, and shipping it here implies the project has a
    hosted half.
  - **Decision:** the client is removed outright, in the same release, with no
    deprecation window. The capability relocated to the consumer, which reaches
    it through `openaca.core` and `openaca.cli` (ADR-0028) rather than through
    in-tree code.
  - **Alternatives considered**, each with why it was rejected — these are the
    part that stops the decision being re-litigated:
    - *Keep `remote policy compile` and drop only the uploader.* It is the one
      subcommand someone would think to keep, because it downloads rather than
      uploads. Rejected: keeping it means keeping `client.py`, `config.py`, a
      credential and `httpx`, which is every part of what this removes. A
      removal that keeps the download is not a smaller version of this change;
      it is no version of it. The policy language, evaluator, risk gates and
      host compiler all stay, so a consumer that fetches a document by any
      means still compiles it with `openaca policy compile`.
    - *Ship a deprecation shim for one release.* Rejected: the capability moved
      rather than vanished, the release notes name the last version carrying
      it, and a shim that cannot actually sync is worse guidance than a missing
      command.
    - *Change the default API URL to a placeholder and keep the client.* Rejected:
      the objection is to shipping a hosted-service client at all, not to which
      host it defaults to.
    - *Delete `collector.py` entirely.* Rejected: ~150 of its lines are producer
      glue over local discovery and the scanner. Plan 045 moved that half down
      into `tools/collect.py` first, precisely so this removal could not take it.
  - **Consequences:** a breaking CLI change; `httpx` leaves the dependency set;
    `socket.gethostname()` is no longer called anywhere in the tree; the
    packaged `openaca:sync` skill in the marketplace repository invokes a
    command that no longer exists and must be retired there. The
    refusal-to-send gate (`upload_contract.py`) leaves **with** the send it
    gated, so no half of a two-sided check is left behind: there is no longer a
    payload leaving this tree for anything to validate, and whatever consumes
    OpenACA keeps whatever validation its own transport needs.
  - Record explicitly that **ADR-0024 is not superseded here**: it was already
    superseded by ADR-0032 and marking it again would clobber a correct
    pointer. It is transitively covered.
- [ ] **Step 2: Mark the four superseded.** In each of `0032`, `0050`, `0051`,
  `0061`: `status: accepted` → `status: superseded` and
  `superseded-by: null` → `superseded-by: 0063`. **Frontmatter only — do not
  edit a single line of any body.** Leave `0024` exactly as it is.
- [ ] **Step 3: `docs/adrs/INDEX.md`.** Add the ADR-0063 entry in the index's
  own style (title link plus a one-liner naming the decision and the rejected
  alternatives). Update the four superseded entries the way the index already
  marks superseded ADRs — read the ADR-0001/0002/0003 entries at the tail for
  the established phrasing before writing.
- [ ] **Step 4: Stale comments and docstrings.** Each of these describes the
  removed module as a live caller. Comment-only edits; no behaviour changes.
  - `tools/graph_build.py:262–264` — `build_graph`'s docstring says "Legacy
    place-rooted graph. Retained for `tools/remote/collector.py`…". After Task 5
    it has **no production caller** and is exercised only by
    `tests/test_graph_build.py` and `tests/test_graph_build_agent.py`. Rewrite
    the docstring to say so plainly and to name it as dead production code
    pending a decision. **Do not delete the function** — see *Deferred*.
  - `tools/graph_build.py:3306` — comment citing `remote sync endpoint` as a
    folder of evidence gaps.
  - `tools/bom.py:34–36` — the `_LEGACY_PLACE_ROOTED_SCHEMA_VERSION` comment
    cites "the remote collector's still-place-rooted graph" as one of two ways
    to reach the 0.4 branch. **The other way — a graphless
    `build_agent_bom(refs, …)` call — still happens**, so the branch stays live
    and the constant stays. Delete only the collector clause; keep the
    graphless clause and the reasoning about a version-aware consumer.
  - `tests/test_bom.py:1078` — `test_legacy_place_rooted_bom_keeps_schema_version_0_4`'s
    docstring opens "The remote collector (`tools/remote/collector.py`) still
    builds a graph-backed BOM without `agent_kind`…". The test constructs its
    own `Graph` and does not import the collector, so it passes unchanged;
    rewrite the docstring to describe the shape it tests rather than the caller
    that used to produce it. The test survives.
  - `tools/cli_kind.py:3`, `tools/kind_selection.py:3` — both docstrings open
    "`scan endpoint`, `bom endpoint` and `remote sync endpoint` all resolve…".
    Drop the third command; keep the sentence's meaning.
  - `tools/bom.py:143` — `_metadata_component`'s "Legacy place-rooted document
    (the remote collector)." names the branch by a caller that leaves. Name it
    by its input instead: a graph-backed document with no `agent_kind`.
  - `tools/collect.py:3–8` — the module docstring's first sentence, "what
    OpenACA's own hosted-service client **used to** do inline", is already past
    tense and stays: it is the accurate history of why this module exists. The
    sentences after it are not — "it lives below the uploader so that removing
    the uploader cannot take it with it" and "the upload-specific parts stay on
    the uploader's side of the boundary" describe a boundary with a live module
    on the other side. Rewrite them in the past tense the first sentence already
    uses, keeping the list of what did not come down with the collection half.
  - `tools/collect.py:62` — "(the uploader wraps this into its own
    `CollectError` to keep its exit code unchanged)" is a live parenthetical
    about removed code. The sentence it qualifies — an exit code is a process's
    concern, and a caller that needs one supplies its own — stands without it.
  - `tools/collect.py:145` — "would change the source-unit count in the upload
    BOM" cites the removed BOM. Say the document this function produces.
    L138–142 above it ("a consumer shipping it elsewhere must not carry an
    absolute path off the machine") is generic and consumer-owned; it stays.
  - `tools/collect.py:231` — the comment "`openaca remote sync` prints —
    `warnings` also carries…" names a live command and must be reworded to
    describe the behaviour without the command name.
  - `tests/test_core_facade.py:105–106` — the `_INTERNAL_TO_COLLECTION` absence
    guard's last two entries are `EndpointCollection` and `CollectError`,
    described as "the uploader's". Both symbols leave the tree with
    `tools/remote/collector.py`, so the guard against them leaking onto the
    facade can no longer fail: delete the two entries, and change the test
    docstring's "Nineteen symbols" to seventeen. `ScannerUnavailable` stays
    published and asserted, as it is today.
  - `tests/test_posture_cursor.py:78–80` — the docstring says posture-surface
    resolution "moved out of the uploader" and "the uploader reaches it through
    `agent_posture_manifests`". The first half is history and stays; the second
    describes a live importer that leaves. **The test itself is untouched** —
    it asserts a property of `tools/scan.py` and `tools/posture/agent_surface.py`
    and all of this module's tests stay.
  - `tests/test_bom.py:643` — "so `openaca bom endpoint` and the remote-sync
    payload serialized a disabled Codex plugin indistinguishably" describes a
    fixed bug in the past tense, but names a payload that will not exist. Keep
    the `openaca bom endpoint` half and drop the payload clause.
  - `docs/openaca-bom-schema.md:19` — same edit as `tools/bom.py:34`: drop the
    collector clause, keep the graphless one.
  - `docs/openaca-bom-schema.md:74` — "the asset (which place) comes from the
    upload envelope" defines the live instance key in terms of the removed
    envelope. Reword as `docs/concepts/identities.md:18` is reworded in Task 6,
    so the two documents keep saying the same thing about the same key.
  - `docs/openaca-bom-schema.md:183` — "`openaca:target` … a path locally, a
    neutral literal on upload". `tools/bom.py:101–102` emits the property only
    when a target was given, so a place-free document has **no**
    `openaca:target` rather than a neutral one. Say that: a path when the caller
    asked for one, absent when it asked for a document that names no place.
- [ ] **Step 5: `docs/plans/README.md`.** Add the row
  `| 046 | [Remote client removal](046-remote-client-removal.md) | ✅ Done | 045 |`
  after the 045 row, matching the existing column format. Check the table's
  "Fleet upload" row in the acceptance-criteria section near the tail — it
  reads "Upload preparation preserves safe source coordinates and drops raw
  argv", which describes a removed subsystem; strike the row.
- [ ] **Step 6: Final acceptance sweep.** All of these must hold:
  - **Names of the removed subsystem.**
    `grep -rn -i "stacktrace\|OPENACA_REMOTE\|openaca remote\|tools\.remote\|tools/remote\|deploy/remote\|gethostname\|httpx" --exclude-dir=.git .`
    returns hits **only** under `docs/plans/`, `docs/specs/`, `docs/adrs/` and
    `docs/releases/`, **and none at all in `docs/specs/policy-compiler.md`** —
    the one file in those directories the removal spec put on its list, cleaned
    in Task 6 Step 5. This one is a clean pass/fail: every hit outside those
    four directories, and every hit inside that one file, is a residue.
  - **Vocabulary of the removed subsystem**, which the name grep does not
    catch:
    `grep -rn -i "uploader\|upload envelope\|upload contract\|upload boundary\|upload BOM\|upload redaction\|remote sync\|remote-sync\|hosted service\|hosted-service\|hosted schema\|_UPLOAD_DEFERRED\|EndpointCollection\|CollectError\|_prepare_remote_bom\|build_endpoint_dry_run_payloads\|build_endpoint_collections" --exclude-dir=.git .`
    over the same live tree. **This one is not pass/fail on hit count** —
    classify every hit against the past-tense rule in *Verified inventory*.
    Hits that stay: a generic *consumer* exporting or uploading a BOM (e.g. the
    release skill's "consumers that ingest uploaded BOMs"), a sentence recording
    what OpenACA used to do, and "remote" meaning a remote MCP server or
    Claude's remote settings. Hits that must go: anything describing the client,
    the uploader, the envelope, the spool, the redaction pass or the `remote`
    command as something OpenACA has, and any surviving name of a deleted path,
    symbol or test. Do not accept a hit as generic without reading its line.
  - `uv run openaca --help` lists eight commands and no `remote`.
  - `uv run openaca remote sync endpoint` exits 2 with Click's unknown-command
    message and no traceback.
  - `uv run python -c "import openaca.core, openaca.cli"` exits 0.
- [ ] **Step 7: Diff against the pre-Task-1 baseline** captured under
  *Preconditions*. Two kinds of evidence, and they prove different things — do
  not let the first stand in for the second.
  - **Help surface.** Re-capture `--help` for every command in the baseline list
    and `diff`. **Every one must be byte-identical**, and the top-level
    `openaca --help` must differ by exactly the removed `remote` line. This
    proves arguments and help text are unchanged; it proves nothing about what
    the commands do.
  - **Behaviour.** Re-run the baseline's functional captures — `scan endpoint`
    text and `--json`, `bom endpoint` and its emitted document, `policy compile`
    and its written artifact — against the same fixtures, and `diff` output,
    exit code and artifact for each. Only `scan endpoint`'s *text* output may
    differ, by exactly the removed next-action line — the machine formats ignore
    `next_actions` entirely (ADR-0047), so `scan endpoint --json` and
    **everything else must be byte-identical**. This is the evidence for the
    spec's promise that surviving commands take the same arguments, write the
    same artifact, print the same report and exit the same way, and it is the
    check most likely to be skipped — do not skip it.
- [ ] **Step 8: Final gate.** The four commands CI runs, in CI's order:
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`,
  `uv run pytest`. Then `bash scripts/ci-local.sh`. `pytest` must report
  **2141 passed**; any other number is investigated, not absorbed.
- [ ] **Step 9: Commit.**

**Acceptance:** ADR-0063 exists and four ADRs point at it with unedited bodies.
2141 tests pass. All four gates plus `scripts/ci-local.sh` are green. Every
surviving command's `--help` is byte-identical to its pre-removal capture, and
so is every functional capture except `scan endpoint`'s text output, which is
short one next-action line.

---

## Deferred

Recorded with the cost of skipping, per the spec's robustness bar. None is
implemented by this plan, and none should be re-raised in review as a finding.

| Deferred | Cost of skipping |
|---|---|
| A deprecation window for `openaca remote` | A user on the current release upgrades into a missing command. Deliberate, and the spec's own choice: the capability moved rather than vanished, and the release notes name the last version carrying it. |
| Retiring the packaged `openaca:sync` skill | It invokes `openaca remote sync endpoint` and will fail against a released OpenACA. It lives in the marketplace repository and cannot be landed from here. Verified absent from this repo — `.claude/skills/` holds only `openaca-candidate-review` and `release-openaca`. |
| `tools/graph_build.py::build_graph` (the legacy place-rooted graph) becomes production-dead | Its only production caller was the collector; after Task 5 it is reached only by `tests/test_graph_build.py` and `tests/test_graph_build_agent.py`. Deleting it is a removal this spec did not ask for, and it would cascade into `tools/bom.py`'s 0.4 branch and ~40 tests. Task 8 Step 4 marks it dead in its docstring instead. Cost: a reader finds a function with tests and no callers, and a future refactor pays to work out whether it is load-bearing. |
| `tools/bom.py`'s `_LEGACY_PLACE_ROOTED_SCHEMA_VERSION` branch stays | Still reachable via graphless `build_agent_bom(refs, …)` calls, so it is not dead — only one of its two entry paths is gone. Removing it would be a BOM-output change, which the spec forbids. |
| Writing the release notes that name the last version carrying `remote` | The spec's compatibility story depends on them, but release notes are the release skill's job at tag time, not a plan task. Deferred as *writing*, not as *deciding*: the version they must name is the last release before this plan lands, recorded under *Preconditions* and handed to whoever cuts the release. Cost: if the release ships without them, the upgrade path is discoverable only from a failing command. |
| A test asserting `openaca remote` exits 2 | Click's unknown-command behaviour is Click's, not OpenACA's, and testing it would be testing the framework. Task 5 Step 4 checks it by hand once. Cost: nothing guards against a future shim being reintroduced by accident. |
| `docs/releases/v0.1.0b7.md`, `v0.1.0b8.md`, `v0.5.0.md` still document `openaca remote` | They are historical records of what shipped in those versions and are accurate as such. Rewriting them would falsify the changelog the compatibility story points users at. |

## Out of scope

- **Adding any public API.** The spec's non-goal, and the plan's hard
  constraint. Task 4 Step 3 was the one place this could have bitten; it does
  not, because `safe_pinned_mcp_install_source` and
  `safe_unpinned_mcp_install_source` are already published on `openaca.core`.
  If a task turns out to need a new name, stop and report — it means this spec
  was started too early.
- **Relocating the uploader, the redaction pass, the spool, the envelope, the
  content hash or the payload vocabulary anywhere in this repository.** They
  belong to whatever consumes OpenACA. This plan removes them.
- **Deleting OSV federation.** `tools/osv_federation.py` queries OSV.dev at scan
  time over `urllib.request`. It is a public advisory database, it sends nothing
  about the machine, and it is central to what a vulnerability scanner is. "No
  hosted-service client and no account" is the rule, not "no sockets".
  `tools/seed/llm.py` likewise stays — a maintainer tool no scan touches.
- **Any change in the consumer repository.**
- **Staging the removal more gently.** Below the spec's bar by its own words;
  the release notes carry it.
