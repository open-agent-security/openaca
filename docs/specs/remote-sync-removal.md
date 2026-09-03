# Remote Sync Removal

OpenACA can upload what it finds to a hosted service today: `openaca remote`
stores an API token, uploads an endpoint's Agent BOMs, and fetches an
organisation policy from a server (ADR-0024). **This spec removes that, and
publishes what it was built on.**

The purpose is to separate the analysis from its delivery. Examining one
machine — which agents are installed, what they are composed of, what is wrong
with their configuration — is what OpenACA is for, and belongs in the open.
Being a client for one company's service, with an account, a credential and that
service's payload schema, is not; shipping it here implies the project has a
hosted half.

Removing the client on its own would take a capability out of reach along with
it, because most of what it did was not server-specific. It discovered the
installed agents, built each one's BOM, ran the posture rules, and compiled a
policy for the endpoint — work that is OpenACA's, and that ADR-0028 already says
a consumer must not reimplement. Today that work is reachable only by importing
internals. So the same change that removes the client **publishes what the
client was built on**: collection and policy compilation through
`openaca.core`, and a supported import path for the CLI group.

Afterwards OpenACA does one thing, and can be built on while doing it — examine
a machine and report what it finds, to a file, to stdout, or to a program
calling it directly. Whoever wants to send those findings somewhere owns that
part.

This spec covers three things: what stays, what becomes public, and what goes.

## Goal

1. `openaca` loses the `remote` command group and everything under
   `tools/remote/`, and loses nothing else.
2. A consumer can collect the installed agents' composition, posture findings
   and observations through **one** public function, in one pass, without
   importing internals.
3. A consumer holding a policy document can compile it for this endpoint through
   the facade, without writing it to a file first or driving the command line.
4. The three install-source safety helpers a consumer needs in order to trim an
   install source without reimplementing OpenACA's identity semantics are
   available through `openaca.core`.

## Non-goals

- Providing an upload path, an upload payload schema, redaction for transport,
  or a credential store. Those belong to whatever consumes OpenACA, not to
  OpenACA. This spec removes them; it does not relocate them here.
- Preserving `openaca remote` under a deprecation shim. The command is removed
  in the next release; see *Compatibility*.
- Changing scan semantics, BOM contents, posture rule behaviour, advisory
  matching, or any output format.
- **Adding CLI surface.** No new command and no new flag. In particular, no flag
  to make `openaca scan endpoint` emit the Agent BOM it already builds: the
  collection API below serves that need in one call, and a shell-only consumer
  wanting it can argue for it on its own.
- **Exposing the machinery behind collection.** The graph, the agent instance,
  the discovery context, the kind registry, the warning log and the coverage
  counters all stay private. See *The collection API*.

## What "local" means here, precisely

After this change OpenACA holds **no client for any hosted service**: no
credential, no API URL, no upload path, no notion of an account or a tenant. It
does not learn the machine's host name — `socket.gethostname()` leaves with the
collector, and nothing else in the tree calls it.

The point is concrete rather than theoretical, and `main` now shows why:
`tools/remote/config.py` defaults its upload target to one company's hostname,
and the three MDM scripts and their tests carry the same value. An open-source
scanner whose default is a particular vendor's API is exactly the "hosted half"
this removes — and every file carrying that value is on the removal list below,
so afterwards the tree names no commercial service at all.

It is not, and should not become, a program that makes no network calls.
`tools/osv_federation.py` queries OSV.dev at scan time, and that is not a
residue of the removed subsystem — it is central to what a vulnerability scanner
is, it reads a public advisory database, and it sends nothing about the machine.
`tools/seed/llm.py` calls a model API, and is a maintainer tool for building
review candidates rather than anything a scan touches.

The distinction is worth stating because "OpenACA is local" read too literally
would license deleting OSV querying, which would leave a scanner that cannot
match advisories. The rule is *no hosted-service client and no account*, not
*no sockets*.

## What OpenACA keeps

Everything except the remote client.

The test applied to each module is: **does it exist for the sake of a local
analysis, or for the sake of a server?** Nothing local leaves.

| Stays | Why it is local |
|---|---|
| Discovery, graph building, BOM construction, posture rules, observations, advisory matching, policy language and host compilation | This is the analysis. It answers a question about this machine and needs no server to do it |
| The ~150 lines of `collector.py` that call discovery and the scanner | Producer glue over the above; `openaca bom endpoint` and `openaca scan endpoint` already expose the same thing |
| `tools/identity.py`'s install-source safety helpers | Identity semantics, which the facade owns (ADR-0028). A consumer that reimplements them diverges |
| OSV federation | Public advisory data, sending nothing about the machine |

| Leaves | Why it is not local |
|---|---|
| `client.py`, `config.py` | A credential, an API URL and an HTTP client for one hosted service |
| `remote policy compile` | It *downloads* the organisation policy over the network. One call, `GET /api/v1/policy` — the policy language, evaluator, risk gates and host compiler all stay |
| The redaction pass | Exists *only* because of upload. Local output is deliberately unredacted, because analysing a host means naming its paths |
| `upload_contract.py` | A refusal-to-send gate. Meaningless without a send |
| The envelope, content hash, spool | The shape and delivery of a request |
| Install-source *trimming* | A privacy measure for transport; the identity helpers it calls stay |
| Finding serialisers (`rule_id`→`finding_id`, and so on) | A server's payload vocabulary, not OpenACA's |
| `deploy/remote/` | Scripts whose only job is to configure a token and schedule uploads |

The split is uneven — roughly 1,670 lines leave and 150 stay — and that is the
right shape rather than a warning sign. `collector.py` was two programs in one
file: a small producer that asks the scanner questions, and a large client that
talks to a server. Only the second is being relocated.

Concretely, the following must behave identically before and after:
`openaca scan repo`, `openaca scan endpoint`,
`openaca scan bom`, `openaca bom repo`, `openaca bom endpoint`, `openaca bom
diff`, `openaca lint`, `openaca export`, `openaca promote`, `openaca seed`,
`openaca triage`, `openaca policy`, and every overlay in `overlays/`. One
narrow exception: `scan endpoint`'s next-action list currently recommends
`openaca remote sync endpoint` (`tools/scan.py:717-723`,
`_next_actions_for`). That line is not preserved — recommending a command this
same change deletes would be a new defect, not stability — and it is the only
byte of `scan` output this removal touches.

This is verifiable rather than aspirational, because nothing depends on the
remote package:

- `tools/cli.py` is the **only** module outside `tools/remote/` that imports it,
  and only to register the command group.
- `openaca/core` has no reference to it, so no consumer of the facade is
  affected.
- `tools/scan.py`, `tools/bom_cli.py`, `tools/lint.py`, `tools/export.py`,
  `tools/matcher.py`, `tools/policy*.py` and `tools/triage*.py` import nothing
  from it.

Two test modules reach into the remote package for convenience and are the only
in-repo callers that must change; both are addressed under *Relocations* and
*Removal*.

## Facade additions

`openaca.core.identity` gains three re-exports:

- `is_mcp_package_launch_install_source`
- `safe_unpinned_mcp_install_source`
- `safe_pinned_mcp_install_source`

These answer "is this install source a package launch, and what is the safe
reduced form of it" — identity semantics, which ADR-0028 already assigns to the
facade, and which a consumer must not reimplement. A consumer that trims an
install source by hand will diverge from OpenACA's notion of a safe package name
the first time either side changes, which is the precise failure ADR-0028 was
written against.

No behaviour changes; `tools/identity.py` remains the implementation.

## The collection API

One function, one result type, the two finding types the result carries, the
value type one of those findings holds, and one public error.

```python
from openaca.core import collect_installed_agents

for collected in collect_installed_agents(
    config_dir=None,        # default per kind when omitted
    project=None,           # layer a project's local configuration in
    kind_id=None,           # all installed kinds when omitted
    external_scanners=(),   # optional third-party scanners to run
    include_target=True,    # False omits the local config root from the BOM
):
    collected.agent_kind          # str
    collected.agent_id            # str | None
    collected.bom                 # CycloneDX document, as a dict
    collected.posture_findings    # tuple[PostureFinding, ...]
    collected.observations        # tuple[ObservationFinding, ...]
    collected.component_count     # int
    collected.warnings            # tuple[str, ...] — malformed manifests and the like
```

### Why one function rather than the pieces

Assembling this today takes seven internal symbols — discovery, the kind
registry, graph building, BOM building, coverage resolution, posture running,
observation collection — plus two private counters, and requires knowing the
order to call them in and which of their arguments matter. A consumer given
those pieces is being handed a procedure to re-derive, and any two consumers
will get subtly different BOMs.

A consumer does not want the pieces. It wants *what this machine is running,
and what is wrong with it*. That is one question, so it is one function.

### What stays private, and why that is the point

None of the following is exposed, and none needs to be:

| Stays internal | Why the consumer never sees it |
|---|---|
| `Graph`, `build_agent_graph` | A construction detail of the BOM |
| `AgentInstance`, `DiscoveryContext` | Discovery inputs and intermediates |
| `kind_for`, the kind registry | Which kinds exist and how they resolve their surfaces |
| `WarningLog` | The result carries plain strings instead |
| `resolve_coverage` | Applied to the BOM before it is returned |
| `_component_gap_count`, `_count_active_plugins` | Feed the BOM's coverage and source-unit count. **Called internally, so the two private symbols stop being a problem rather than becoming public** |
| Posture manifest resolution per kind | An implementation of running posture rules |

That is nineteen symbols a consumer would otherwise reach for, reduced to six
public names — `collect_installed_agents`, `AgentCollection`,
`PostureFinding`, `ObservationFinding`, `Standards` (the value type
`PostureFinding.standards` holds — required once `PostureFinding` itself is
public) and `CollectionError` (below) — and zero private ones promoted.

Those six are `openaca/core/collect.py`'s entire surface — a test asserts that
module exposes exactly them, and nothing else. `openaca.core` itself already
exports an established BOM, matching, policy, severity and OSV surface
(`openaca/core/__init__.py`'s `__all__`); this step adds the six collection
names to that `__all__` alongside what is already there, rather than
replacing it, and a second test asserts the addition without asserting an
exact count against names this step did not touch.

### The finding types are returned as themselves

`posture_findings` and `observations` are `PostureFinding` and
`ObservationFinding`, not dictionaries.

This matters because the removed uploader converted them to *its server's*
payload vocabulary — `rule_id` became `finding_id`, `title` became `summary`,
`remediation` became `fix` — inside the collection step. That mapping belongs to
whatever consumes the findings, not to OpenACA. Returning the dataclasses keeps
OpenACA's vocabulary OpenACA's, and lets a consumer map to its own.

`PostureFinding`, `ObservationFinding` and `Standards` therefore join the facade
as value types. They already are frozen-ish domain records with no behaviour
beyond a label property, which is what makes them safe to publish.

### Scanner warnings are returned, not printed

`_collect_scanner_findings` — one of the functions this moves verbatim from
`tools/remote/collector.py` — currently sends each skillspector warning
straight to `click.echo(f"warning: {warning}", err=True)`
(`tools/remote/collector.py:265-266`) instead of returning it. Moved as-is, the
facade function would still write to stderr on every call, and
`collected.warnings` would omit exactly the diagnostics an external scanner
produces — the same defect *Two CLI concerns come out of the library on the
way* fixes for `emit_policy_report` below, in the sibling function this one
sits next to.

`_collect_scanner_findings` returns its warnings instead, and
`_build_agent_collection` folds them into that agent's
`AgentCollection.warnings` alongside the malformed-manifest warnings already
there. Nothing prints on the library path; a CLI caller that wants them on
stderr echoes `collected.warnings` itself.

### A collection failure is a public, typed error

`CollectError` (`tools/remote/collector.py:104-107`), raised today when a
requested external scanner is not installed, carries an `exit_code` — a CLI
concern baked into what would otherwise be a plain domain exception, the same
shape of problem `compile_endpoint_policy`'s `click.UsageError` has below. It
is not promoted as-is.

A `CollectionError` (`ValueError` subclass, no `exit_code`) joins the facade
for this case. A CLI caller that still needs a specific exit code catches it
and raises its own `click.ClickException`.

It also covers a name in `external_scanners` that OpenACA does not recognise.
Today that validation belongs to Click alone —
`click.Choice(["nvidia-skillspector"])` (`tools/scan.py:419`,
`tools/remote/cli.py:205`) rejects anything else before the collection code
ever runs it; the membership check inside that code
(`if "nvidia-skillspector" in external_scanners:`,
`tools/remote/collector.py:258`) silently ignores any other string, typo
included. A CLI caller never notices, because Click already stopped a bad
value. `collect_installed_agents` has no Click in front of it, so without this
check a typo in `external_scanners` would return an ordinary collection while
the caller believed the scanner had run. `CollectionError` covers both an
unrecognised name and a recognised one that is not installed.

### Discovery inputs are validated inside the facade too

`CollectionError` also covers `kind_id` and the `config_dir`/`kind_id`
pairing, for the same reason it covers `external_scanners`: today that
validation belongs to Click alone.

`build_endpoint_collections` calls `discover_agents`
(`tools/agent_kinds/__init__.py:219-234`) directly. `discover_agents` treats
`kind_id` as a filter over the registry — `if ctx.kind_id is not None and
kind.id != ctx.kind_id: continue` — so an unknown `kind_id` matches nothing
and returns an empty list, not an error. The check that turns an unknown kind,
a bare `config_dir` with no `kind_id`, or a `config_dir` override for a kind
that refuses one into an error is `require_kind_for_config_dir`
(`tools/cli_kind.py:34-63`), and it runs only in the three CLI commands that
call it (`scan endpoint`, `bom endpoint`, `remote sync endpoint`) — never in
the collector.

The third case is silent in a way the first two are not. Cursor's
`root_override_refusal` is set (`tools/agent_kinds/cursor.py:134-141,228`,
ADR-0054), but its `resolve_config_root` accepts `config_dir` as a parameter
and ignores it (`tools/agent_kinds/cursor.py:143-157`) — the refusal is
enforced by the CLI check rejecting the flag combination before discovery
ever runs, not by discovery itself refusing the argument. Call
`collect_installed_agents(kind_id="cursor", config_dir=Path("/somewhere/else"))`
directly and nothing rejects it: Cursor's discovery resolves the root from
`CURSOR_CONFIG_DIR`/`XDG_CONFIG_HOME`/home as it always does, silently
scanning the real Cursor installation instead of the location the caller
named. A CLI caller never reaches this because `require_kind_for_config_dir`
already ran; a programmatic caller has no Click in front of it and gets back
a collection from the wrong place with nothing to say so.

The three checks `require_kind_for_config_dir` performs move into a plain
function raising `CollectionError`; `require_kind_for_config_dir` becomes a
thin wrapper that calls it and translates the exception into a
`click.ClickException` carrying the same message it raises today, so `scan`'s
and `bom`'s error text does not change. `build_endpoint_collections` calls the
plain validator before calling `discover_agents`.

Both the plain validator and `CollectionError` itself are defined in
`tools/cli_kind.py`, not in `tools/collect.py` alongside `build_endpoint_collections`.
`tools/collect.py` already imports `_component_gap_count` and
`_count_active_plugins` from `tools/scan.py` (see below), and `tools/scan.py`
already imports `require_kind_for_config_dir` from `tools/cli_kind.py`
(`tools/scan.py:68`). Putting the plain validator in `tools/collect.py` would
force `require_kind_for_config_dir`'s thin wrapper to import it from there,
closing a cycle: `cli_kind → collect → scan → cli_kind`. `tools/cli_kind.py`
imports only `tools.agent_kinds` today (`tools/cli_kind.py:21`), which has no
dependency on `tools.scan` or `tools.collect`, so leaving the plain validator
and `CollectionError` there keeps the graph acyclic —
`collect → cli_kind → agent_kinds` and `scan → cli_kind → agent_kinds`.
`tools/collect.py` imports both from `tools.cli_kind`, and
`openaca/core/collect.py` re-exports `CollectionError` from `tools.collect`
like the other five names.

### Where the implementation lives

In OpenACA, as it does now — it does not follow the uploader out.

It cannot stay in `tools/remote/`, since that directory is deleted, so it moves
to `tools/collect.py`: the same ~150 lines, with the upload-specific parts left
behind (the install-source trimming, the payload-vocabulary mapping, and the
hardcoded `target=None`, which becomes the `include_target` argument).
`openaca/core/collect.py` re-exports the function, as the other facade modules
do.

Until `tools/remote/` is deleted, its two callers of `build_endpoint_collections`
— `collect_endpoint` (`tools/remote/collector.py:288`) and
`build_endpoint_dry_run_payloads` (`tools/remote/collector.py:410`) — must pass
`include_target=False` explicitly. Today they call it with no `target`
argument at all, because the omission is hardcoded two calls deeper inside
`_build_agent_collection`; once `include_target` defaults to `True` at the top
of the call chain, leaving those two call sites unchanged would start writing
the local config root into every uploaded and dry-run BOM — the redaction
regression the old hardcode exists to prevent.

Its tests move too, in kind rather than in whole. `tests/remote/test_collect.py`
tests both the producer logic moving here (a kind's posture allowlist honoured,
a graph warning downgrading `openaca:composition_coverage`, one agent-rooted
BOM per installed agent) and upload mechanics that are not moving (install-source
trimming, the payload-vocabulary mapping, the client, the pending-payload
cache). Only the first group has anywhere to live once `tests/remote/` is
deleted below, so it moves to `tests/test_collect.py` against `tools.collect` —
adapted, not copied verbatim, where an assertion depends on the dict vocabulary
this step removes (`finding_id`, `summary`, `fix`) rather than on the
`PostureFinding` / `ObservationFinding` objects it returns instead.

### `include_target`

A BOM's target names where it was collected from — an absolute path on this
machine. A consumer keeping the BOM locally wants it; a consumer shipping it
elsewhere must not carry it. The removed uploader hardcoded `target=None` with
the comment *"the upload names no place"*, which was the right decision made in
the wrong place. It becomes an argument, defaulting to `True` so the CLI's
behaviour is unchanged.

## Policy compilation, programmatically

Compiling a policy for one endpoint is the second thing the removed client did,
and like collection it is not upload-specific: fetch a policy document from
wherever, evaluate this endpoint against it, write the host artifact. The
`openaca policy compile` command already does exactly that over a file on disk.
What it lacks is a way to do it from Python when the document did not come from
a file.

Three names, of which one already exists:

```python
from openaca.core import parse_policy, compile_endpoint_policy, render_policy_report

policy = parse_policy(document)                    # already public
report = compile_endpoint_policy(                  # promoted
    policy, target=…, project=…, output=…,
    managed_settings_dir=…, dry_run=…,
)
print(render_policy_report(report, "text"))        # new, extracted
```

### Two CLI concerns come out of the library on the way

`compile_endpoint_policy` is already a well-shaped function — typed arguments, a
report returned as a dict — with one wart, in more places than its own body.
It raises `click.UsageError` itself for *"output is required unless
dry-run"*, and everything it calls does the same: `_evaluate_endpoint`
(`tools/policy_cli.py:183,202,212,218` — no installed agent found, an
incomplete graph, a non-queryable component under a vulnerability gate, an OSV
federation warning) and `_managed_key_collisions`
(`tools/policy_cli.py:133,264,268,276,281,289,301` — a managed-settings key
collision, an unreadable managed-settings directory or file, a malformed
managed-settings JSON file) all raise `click.ClickException`. Every one of
those is on the path a programmatic caller of `compile_endpoint_policy` hits,
not only the argument check at the top — retyping the top-level `UsageError`
alone would leave the function still throwing Click exceptions from three
levels down.

Every one of these becomes a domain error instead: the `--output` check and
the managed-settings failures become `PolicyValidationError`; the
endpoint-evaluation failures become `PolicyEvaluationError`. `compile`'s
`except (PolicyValidationError, PolicyEvaluationError)` clause
(`tools/policy_cli.py:95-96`) already catches both, so the command needs no
change beyond that — only the `raise` sites move off `click`.

`emit_policy_report` cannot be promoted as it stands, because it *prints* — it
is a presentation function, and a library that writes to stdout is a library a
caller cannot compose. It splits: a pure `render_policy_report(report, format)
-> str`, plus the `click.echo` of its result, which stays in the command.

Both changes leave `openaca policy compile` behaving identically. The exception
retype is in fact invisible from the command line, because the command performs
the same `--output` check itself before calling the function — so the usage
error a CLI user sees is raised by the command, not by the code being changed,
and every other retyped failure is still caught by the command's existing
`except` clause and turned back into a `ClickException` there.

### These three names are sufficient, which is worth checking rather than assuming

Two details of the existing command decide it.

**`--host` selects a compiler, and today there is one.** It is a different axis
from `--kind`, which scopes *discovery* to an installed agent kind
(`claude-code`, `cursor`, `codex`) and is validated against the kind registry.
`--host` names *whose settings format the artifact is compiled into*, and the
policy compiler spec holds that to a higher bar than registering a kind: a host
compiler may be added "only when OpenACA can produce a precise host-native
restriction and verify it".

It does not reach `compile_endpoint_policy` because there is no dispatch to
reach — the Claude compiler is called unconditionally inside the compilation
path, and `Choice(["claude"])` is a gate on the input rather than a key that
selects anything. So the facade needs no host argument **today**, and a caller
loses nothing by not having one.

**That answer expires when a second host compiler lands.** At that point
`--host` becomes a dispatch key, and the facade needs the argument — otherwise a
programmatic caller silently gets Claude's format whatever it asked for. Whoever
adds the second compiler owns that change; it is recorded here so it is not
discovered by a caller instead.

(One cosmetic inconsistency, noted and not changed: the values are spelled
`--host claude` but `--kind claude-code`.)

**Two things the command does are the command's, not the compilation's.** It
prints a note when `--project` was omitted, because an operator who omits it is
silently getting a narrower compile; and it translates
`PolicyValidationError` / `PolicyEvaluationError` into a Click exception. A
programmatic caller wanting the first must print its own, and wanting the second
must catch the domain errors — which is the correct division, but only obvious
once stated.

### Why not let a consumer drive the command object instead

It could: the CLI group is published below, so a caller can invoke
`policy compile` with an argument list in its own process. That works, and it is
the wrong tool for a caller that already holds a policy document in memory.

It would mean constructing flag strings for arguments a function takes directly,
which is untyped where the function is typed — a renamed parameter becomes a
runtime failure instead of a type error at upgrade time. The clean division is
that **the CLI group is for offering OpenACA's commands to a person, and
`openaca.core` is for a program calling OpenACA.** Compiling a policy from a
document a program just fetched is the second of those.

## The CLI group as public API

`openaca/cli.py` re-exports the existing Click group.

```python
from openaca.cli import main   # the same group `tools.cli:main` defines
```

That is the whole change: one module, one name, no new command and no new flag.
The console script keeps pointing at `tools.cli:main`, so nothing about running
`openaca` changes.

### What it is for

A consumer offering OpenACA's analysis under its own command line registers the
command objects it wants in its own Click group. It then has OpenACA's real
commands — the same options, the same output, the same exit codes — rendered
under its own program name, because Click builds a usage line from the
invocation rather than from where a command was defined.

This is the surface that makes that possible without either reimplementing a
command or importing `tools.cli`, which is internal and out of contract
(ADR-0028). What a command *does* after collection is substantial — OSV query
planning, matching, severity normalisation, overlay merging, and rendering to
five formats across `render.py`, `scan.py`, `triage.py`, `sarif.py` and
`finding_output.py` — and none of it needs publishing for a consumer to reuse
the command whole.

### What this does and does not promise

It promises that `openaca.cli.main` is a `click.Group`, and that `scan`, `bom`
and `policy` are reachable on it by those names. A test asserts exactly that,
and nothing more.

It does **not** promise the internal structure of any command, its option set,
or its output format. Those are the CLI's contract to its users, which is a
different and looser thing than a library API — a flag can be added, and a
consumer that merely re-registers the command inherits it for free rather than
breaking.

It does not oblige OpenACA to keep any particular command in the group either.
A consumer registering a name that later disappears gets an import-time or
lookup-time failure, which is the right moment to find out.

## Relocations

`_agent_posture_manifests` and `_agent_extra_posture_manifests` currently live in
`tools/remote/collector.py`. They resolve a kind's installed posture surface —
posture logic, with no relationship to uploading. `tests/test_posture_cursor.py`
already imports one of them *from the collector* in order to test Cursor posture
behaviour, which is the evidence that they are in the wrong module.

They move to `tools/posture/`. `no_manifests` stays where it is and stays
shared. This is a move with no behaviour change, and it is worth doing on its own
merits regardless of the removal: it puts posture-surface resolution in the
posture package and lets a posture test import from the posture package.

## Removal

The `remote` group is four subcommands, and all four go: `configure`, `status`,
`sync endpoint`, and `policy compile`.

The last of those is worth naming rather than leaving to be inferred from a
deleted directory, because it is the one someone would think to keep. It is not
an uploader — it fetches the organisation's policy and compiles it locally — so
"remove the uploader" reads as though it survives. It does not, for a plain
reason: keeping it means keeping `client.py`, `config.py`, a credential and
`httpx`, which is every part of what this removes. A removal that keeps the
download is not a smaller version of this change; it is no version of it.

**`openaca policy` behaves identically.** Its `validate` and `compile` over a
policy file on disk produce the same output and exit status, before and after,
as do `tools/policy.py` and `openaca.core.policy`, which are unaffected by this
spec. `tools/policy_cli.py` is not untouched — the facade work above retypes
its `click.ClickException` raise sites to `PolicyValidationError` /
`PolicyEvaluationError` and splits `emit_policy_report` into a pure
`render_policy_report` plus a `click.echo` in the command — but neither change
is visible from the command line, since `compile` already catches both error
types and already prints what `render_policy_report` returns. What leaves is
one network call, not the policy language, the evaluator, the risk gates or the
host compiler — a consumer that fetches a policy document by whatever means can
still compile it with `openaca policy compile`.

- `tools/remote/` and `tests/remote/`, which is where all four subcommands
  live.
- The command-group registration in `tools/cli.py`.
- `deploy/remote/` — the MDM deployment scripts and the scheduled-sync agent
  they install exist only to run the removed command.
- `httpx` leaves `pyproject.toml`. It is used only by `tools/remote/`; OSV
  federation uses `urllib.request` from the standard library. An OSS scanner
  carrying an HTTP client it does not use is a dependency its consumers pay for
  and nothing needs.
- The three remote end-to-end tests in `tests/test_e2e.py`, and
  `test_scan_and_collector_import_the_shared_no_manifests` in
  `tests/test_posture_cursor.py`, which asserts a property of a module that will
  not exist.
- The last six lines of `test_github_and_docker_mcp_refs_survive_identity_lifecycle`
  (`tests/test_e2e.py:1178-1186`), a fourth caller of `tools.remote` this test is
  not one of the three above. Its earlier assertions — BOM round-trip,
  rendering, OSV federation — exercise ordinary scan behaviour and survive; its
  last block calls `_prepare_remote_bom` to assert that upload-specific
  install-source trimming redacts secrets — transport redaction, which
  *Non-goals* already says this spec removes rather than relocates. Its
  `_props_by_name` helper stays: two other tests still use it.
- `docs/remote-deployment.md`, and the remote sections of
  `docs/reference/cli.md` and `docs/README.md`.
- The `openaca remote policy compile` example (`docs/specs/policy-compiler.md:161`)
  and the paragraph describing its behaviour
  (`docs/specs/policy-compiler.md:220-227`). That spec documents the command
  this removal keeps, `openaca policy compile`; left in place, it documents a
  sibling command that no longer exists in the same breath as one that still
  does.
- `remote sync endpoint` from the line naming it alongside `scan endpoint` and
  `bom endpoint` as one of the three commands `--kind` applies to
  (`docs/specs/cursor-agent-kind.md:147`, two remain), and the bullet
  describing its dry-run behaviour as part of that three-command contract
  (`docs/specs/cursor-agent-kind.md:223`). That spec stays active for
  `scan endpoint`/`bom endpoint`, so it is edited, not deleted.
- The present-tense framing in `docs/specs/collector-agent-rooted-uploads.md`
  — its before/after table (line 17) and its verification section naming
  `openaca remote sync endpoint --dry-run` (line 147) — both describe a
  command this removal deletes as current. That document is not deleted like
  `docs/remote-deployment.md`: ADR-0050 and ADR-0051 cite it as their design
  record, and `docs/specs/multi-agent-support.md` links to it twice for the
  agent-rooted wire-format rationale, which outlives this removal. It instead
  gets a one-line note under the title marking it historical as of ADR-0063,
  without rewriting the migration narrative those other documents point to.
- The whole "When a remote policy is configured..." paragraph and shell block
  in the root `README.md` (`README.md:160-172`) — not only its
  `openaca remote configure` line. The block also runs
  `openaca remote policy compile`, so trimming just the `configure` line would
  leave a documented invocation of a command this removal deletes.
- The `"sync to remote: openaca remote sync endpoint"` next-action
  `tools/scan.py:722` (`_next_actions_for`) appends to every installed-agent
  scan. It is scan's one output-changing edit in this removal — see
  *What OpenACA keeps* above — because the alternative is a scanner that
  recommends a command it no longer has.
- The remote smoke gates in `.github/workflows/ci.yml` and
  `scripts/ci-local.sh`, which invoke `openaca remote configure` and
  `openaca remote sync endpoint` against an unreachable port and assert a
  clean exit. They must go in the same commit as the removal, not a
  follow-up — both invoke a command that will not exist, so leaving either
  breaks `main`.

ADR-0024, ADR-0032, ADR-0050, ADR-0051 and ADR-0061 all decide questions about a
subsystem that no longer exists. ADRs are immutable, so a new ADR supersedes
them and each is marked `status: superseded` with `superseded-by:` in its
frontmatter. Their bodies are not edited — an old PR must stay readable against
the rules in force when it was opened.

## Compatibility

`openaca remote configure|status|sync endpoint` and `openaca remote policy
compile` stop existing in the next release. There is no deprecation window.

A user relying on remote sync should pin the last release that carries it. The
release notes name that version, so the upgrade path is discoverable from the
changelog rather than from a failing command.

The removal is a breaking change to the CLI surface and is released as such.

## Out of repo

The `openaca:sync` skill is packaged in the marketplace plugin, not in this
repository. It invokes `openaca remote sync endpoint` and must be retired in that
repository, or it will fail against a released OpenACA. That change is tracked
separately; this spec cannot land it.

## Deferred

| Deferred | Cost of skipping |
|---|---|
| A flag to emit the BOM `scan endpoint` already builds | A consumer needing both the BOM and the findings runs two commands and scans the machine twice, and cannot join the findings to the document on `bom-ref`, since ADR-0042 scopes a ref to occurrences within one BOM. Real, and a separate change. |
| Documenting the scan JSON finding shape as a stability contract | A consumer joining on `finding_output.py`'s field names has no contract to pin. Real, but pre-existing and unchanged by this spec; the CLI reference documents the fields without promising them. |
| Removing `_UPLOAD_DEFERRED_RULES`' two posture rules from the deferred set | None here. Those rules are emitted by the scanner as normal; only the removed uploader treated them specially. |

## Robustness bar

This spec aims to get right: that no command other than `remote` changes
behaviour, and that the facade additions are re-exports with no
reimplementation.

It defers: providing any transport, retry, credential or redaction affordance to
consumers, and preserving the removed CLI surface in any form.

## References

- ADR-0028 — `openaca.core` consumption facade
- ADR-0042 — `bom-ref` for occurrences, `openaca:identity` for cross-BOM joins
- ADR-0024, ADR-0032, ADR-0050, ADR-0051, ADR-0061 — superseded by this work
