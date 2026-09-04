# Published Consumption Surfaces

OpenACA's own hosted-service client reaches inside the project for things no
outside consumer can reach: it discovers the installed agents, builds each
one's BOM, runs the posture rules, and compiles a policy for the endpoint. That
work is not upload-specific and it is not the client's — it is OpenACA's, and
ADR-0028 already says a consumer must not reimplement it. Today the only ways
to get at it are importing internals or driving the command line.

**This spec publishes it.** Collection and policy compilation become part of
`openaca.core`, and the existing Click group gains a supported import path so a
consumer can offer OpenACA's commands under its own name.

Everything here is additive. No command changes, no flag is added or removed,
nothing is deleted, and every existing behaviour is preserved — which is what
makes it shippable on its own, before anything depends on it.

**It is the first half of a pair.** The second, `remote-client-removal.md`,
deletes the hosted-service client once a consumer has migrated onto these
surfaces. That ordering is not optional: removing the client before the
surfaces exist would leave the capability unreachable, and removing it before a
consumer has migrated would leave the job undone. This spec must ship, and be
released, first.

## Goal

1. A consumer can collect the installed agents' composition, posture findings
   and observations through **one** public function, in one pass, without
   importing internals.
2. A consumer holding a policy document can compile it for this endpoint
   through the facade, without writing it to a file or driving the command line.
3. A consumer can offer OpenACA's `scan`, `bom` and `policy` under its own
   command line, getting OpenACA's real commands rather than reimplementations.
4. The three install-source safety helpers needed to trim an install source
   without reimplementing OpenACA's identity semantics are available through
   `openaca.core`.
5. Nothing else changes.

## Non-goals

- **Removing anything.** Not the `remote` command group, not `tools/remote/`,
  not the `httpx` dependency. All of that is the removal spec's subject, and
  keeping the two apart is what lets this one ship first.
- **Adding CLI surface.** No new command and no new flag. In particular, no flag
  to make `openaca scan endpoint` emit the Agent BOM it already builds: the
  collection API serves that need in one call.
- **Exposing the machinery behind collection.** The graph, the agent instance,
  the discovery context, the kind registry, the warning log and the coverage
  counters all stay private. See *The collection API*.
- Changing scan semantics, BOM contents, posture rule behaviour, advisory
  matching, or any output format.

## What stays private, and what this must not break

Everything not named in this spec. Concretely, the following must behave
identically before and after: `openaca scan repo`, `openaca scan endpoint`,
`openaca scan bom`, `openaca bom repo`, `openaca bom endpoint`, `openaca bom
diff`, `openaca lint`, `openaca export`, `openaca promote`, `openaca seed`,
`openaca triage`, `openaca policy`, `openaca remote` — still present throughout
this spec, and removed later by `remote-client-removal.md` — and every overlay
in `overlays/`.

`openaca policy` keeps its behaviour exactly: `validate` and `compile` over a
policy file on disk take the same arguments, produce the same output and exit
the same way. That is a promise about behaviour rather than about module
locations, since *The facade must not import the CLI layer* moves the
compilation into a module of its own.

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

One function, one result type, and the two finding types the result carries.

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
    collected.config_root         # Path — this agent's own configuration root
    collected.bom                 # CycloneDX document, as a dict
    collected.posture_findings    # tuple[PostureFinding, ...]
    collected.observations        # tuple[ObservationFinding, ...]
    collected.component_count     # int
    collected.warnings            # tuple[str, ...] — malformed manifests and the like
```

### Why the result carries each agent's own configuration root

`config_root` is per result, not the `config_dir` argument, and the difference
is a correctness one rather than a convenience.

A consumer that relativises paths — anything shipping a BOM off the machine has
to — needs the root the paths are relative *to*. On a machine running one agent
kind, the argument and the result agree and the distinction is invisible. On a
machine running two, the argument can be at most one kind's root, so paths
belonging to the other kind match neither the config-root nor the project branch
and fall back to a bare basename.

That failure is silent and it is the wrong kind of silent: the value that
survives is a filename rather than an error, so the consumer ships a
partially-relativised document and nothing raises. OpenACA's own collector
carries a comment saying exactly this at the point where it passes each
collection's own root instead of the outer argument.

So the root travels with the result that needs it. A consumer that ignores it
gets the same behaviour it has today; a consumer that relativises has the only
value that is correct for every agent on the machine.

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

That is nineteen symbols a consumer would otherwise reach for, reduced to five
public names — and zero private ones promoted.

### The finding types are returned as themselves

`posture_findings` and `observations` are `PostureFinding` and
`ObservationFinding`, not dictionaries.

This matters because the removed uploader converted them to *its server's*
payload vocabulary — `rule_id` became `finding_id`, `title` became `summary`,
`remediation` became `fix` — inside the collection step. That mapping belongs to
whatever consumes the findings, not to OpenACA. Returning the dataclasses keeps
OpenACA's vocabulary OpenACA's, and lets a consumer map to its own.

`PostureFinding`, `ObservationFinding` and `Standards` therefore join the facade
as value types. With the error type below, that makes the collection surface
**five entry points** — the function, the result type, the two finding types a
caller reads, and the one exception the call can raise — plus `Standards`, which
is not a sixth entry point but the type a `PostureFinding` field already
exposes, and would be unusable without.

They are `frozen=True` dataclasses whose behaviour is a label property, a
`location` property, and `to_dict` on `Standards`. The freeze is shallow: their
nested lists and dictionaries stay mutable, so a caller that mutates one mutates
the record. That is accepted rather than overlooked — deep-freezing them would
change types the scanner and the renderer already share — and it is the reason
they are documented as records to read rather than as immutable values.

### One public operation needs one public error

`external_scanners` is a public argument, and naming a scanner that is not
installed is its expected failure. A caller that cannot name that failure's type
has to catch `Exception`, which defeats the point of not importing internals.

So the collection module raises **`ScannerUnavailable`**, and the facade
publishes it.

The obvious-looking alternative is to publish the `CollectError` the code raises
there today, and it is wrong on three counts. Six of its seven raise sites are
upload concerns — remote not configured, asset registration, client failure,
spool flush — so publishing it would publish a type mostly about a thing this
library does not do. It carries an `exit_code`, which is a process's concern and
not a library's.

And publishing it settles the wrong ownership. Relocating it into the collection
module so the facade can re-export it makes a collection module own the type its
six upload raise sites need, which inverts the dependency this spec exists to
straighten. Leaving it in the uploader and re-exporting it *from* there puts the
facade back on a module the removal phase deletes. There is no placement where
`CollectError` is both publishable and correctly owned, because it is not one
error — it is the uploader's exit-code carrier.

`ScannerUnavailable` lives with the collection code, which survives. The
uploader catches it and re-wraps it into `CollectError(str(exc))` exactly as it
wraps the adapter's exception today, so the command's message and its exit code
are unchanged. And it is generic where the adapter's own
`SkillSpectorCommandNotFound` is not: a second scanner reuses it instead of
adding a second published name to a deliberately scanner-agnostic argument.

### Selecting one kind is part of the collection API

`collect_installed_agents` takes a kind and a config root, so the rules
governing which pairs are legal are part of that call, not decoration around it.
Three rules exist today, all in `tools/cli_kind.py`:

- an unknown kind is an error naming the known kinds,
- `--config-dir` without a kind is an error, because with more than one kind
  installed a bare root cannot say whose it is,
- and a kind may refuse a root override outright (ADR-0054 — Cursor resolves
  `<home>/.cursor` and ignores the override).

A consumer that cannot reach these does not merely lose error messages, it gets
different behaviour: an unknown kind silently collects nothing, and a refused
override is silently ignored so a *different directory than the caller named* is
read. Both are indistinguishable from success at the call site.

So the facade publishes **`validate_kind_selection(kind, config_dir)`**, raising
**`KindSelectionError`** with the message the CLI shows today. The validation
moves below the command layer; `tools/cli_kind.py` calls it and translates the
domain error into the `ClickException` it raises now, so every existing message
and exit code is unchanged.

Publishing the check rather than the facts it checks — the kind ids, each kind's
refusal — is deliberate. Facts would let a consumer rebuild the validation and
phrase its own errors, and the two wordings would then drift apart while both
claimed to describe the same rule. The check is the thing consumers actually
need identical.

That takes the facade to seven names for collection. It is a widening, and it is
the widening ADR-0001 in the consuming repo prescribes: a gap gets closed
upstream rather than worked around downstream by copying internals.

### The facade must not import the CLI layer

`compile_endpoint_policy` and the report renderer currently live in
`tools/policy_cli.py`. Re-exporting them from there would make
`openaca/core/policy.py` import a CLI module, and transitively
`tools/bom_cli.py` for a private helper — a library surface depending on two
command-line modules, which is the dependency pointing the wrong way.

So the compilation moves to a non-CLI module of its own, and both
`tools/policy_cli.py` and `openaca/core/policy.py` import it from there.
`openaca policy compile` keeps behaving identically.

**What that achieves, stated as something checkable.** Importing `openaca.core`
must not import `tools/policy_cli.py`, `tools/bom_cli.py` or `tools/cli.py`. A
test asserts those three by name, and today none of them is reachable, so this
is a property to keep rather than one to win.

**And one it does not achieve.** The compilation and the collection both need
private helpers that live in `tools/scan.py`, which is also where the `scan`
command group is defined — so the facade will import that module. Splitting the
scanner's domain helpers out of the module that declares its command is a
worthwhile change and a larger one than this belongs to; it is recorded in
*Deferred* rather than smuggled in. The rule is therefore "the facade does not
import the command modules it has no need of", not "the facade imports no module
that declares a command".

(This is about module layering, not about `click`: `openaca.core` already pulls
`click` in through another path today, so that is not the thing being fixed.)

### Where the implementation lives

In OpenACA, as it does now — it does not follow the uploader out.

It does not stay in `tools/remote/` — that package is the hosted-service client
and is on its way out under the companion spec, so collection must not depend on
it surviving. It moves to `tools/collect.py`: the same ~150 lines, with the upload-specific parts left
behind (the install-source trimming, the payload-vocabulary mapping, and the
hardcoded `target=None`, which becomes the `include_target` argument).
`openaca/core/collect.py` re-exports the function, as the other facade modules
do.

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
report returned as a dict — with one wart: it raises `click.UsageError` for
*"output is required unless dry-run"*. That is a CLI exception in a function
that is about to stop being CLI-only, so it becomes a `PolicyValidationError`,
and the command layer turns that into a usage error as it already does for other
policy failures.

`emit_policy_report` cannot be promoted as it stands, because it *prints* — it
is a presentation function, and a library that writes to stdout is a library a
caller cannot compose. It splits: a pure `render_policy_report(report, format)
-> str`, plus the `click.echo` of its result, which stays in the command.

Both changes leave `openaca policy compile` behaving identically. The exception
retype is in fact invisible from the command line, because the command performs
the same `--output` check itself before calling the function — so the usage
error a CLI user sees is raised by the command, not by the code being changed.

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

## Deferred

| Deferred | Cost of skipping |
|---|---|
| Relocating advisory matching or rendering into the facade | A consumer wanting findings rendered as OpenACA renders them has to drive the CLI. Out of scope: this spec publishes what the client used, not everything a consumer might want. |
| A stability guarantee on the new surfaces | ADR-0028 holds pre-V0 that `openaca.core` has no back-compat promise, and these additions inherit that. A consumer pins a version and upgrades deliberately. |
| Splitting the scanner's private helpers out of `tools/scan.py` | The facade imports a module that also declares the `scan` command group, so a library consumer loads the command's module. Harmless today — nothing executes — but it is the reason the no-CLI-import rule is stated as three named modules rather than as an absolute. |

## Robustness bar

This aims to get right: that no existing command changes behaviour; that the
new surfaces are the minimum a consumer needs and expose no internal type; and
that a consumer using them gets the same results the in-tree client gets.

It defers: back-compat guarantees, and any surface beyond what the client used.

A finding that an existing command's output, arguments or exit code changed is
above the bar however small. A finding that some further internal would also be
useful to publish is below it.

## References

- ADR-0028 — the `openaca.core` consumption facade, and the rule that a
  consumer must not reimplement domain semantics
- `docs/specs/remote-client-removal.md` — the second half of this pair
