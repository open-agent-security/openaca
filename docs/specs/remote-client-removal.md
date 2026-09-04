# Remote Client Removal

OpenACA can upload what it finds to a hosted service: `openaca remote` stores an
API token, uploads an endpoint's Agent BOMs, and fetches an organisation policy
from a server (ADR-0024). **This spec removes all of it.**

The reason is scope. Examining one machine — which agents are installed, what
they are composed of, what is wrong with their configuration — is what OpenACA
is for, and belongs in the open. Being a client for one company's service, with
an account, a credential and that service's payload schema, is not; shipping it
here implies the project has a hosted half.

## Preconditions — this spec ships last

Two things must be true before any task here is started, and neither is
something this spec can bring about:

1. **`published-consumption-surfaces.md` has shipped and been released.** The
   work it publishes — collection, policy compilation, the CLI group — is what
   the removed client did privately. Removing the client first would take the
   capability out of reach along with it.

2. **A consumer has migrated onto those surfaces.** Otherwise nobody can do the
   job the client did, and the removal is a regression rather than a relocation.

Both are checkable rather than a matter of scheduling. The first is an import
that either succeeds or does not:

```
python -c "from openaca.core import collect_installed_agents, compile_endpoint_policy"
python -c "from openaca.cli import main"
```

The second is not verifiable from inside this repository, and deliberately so —
this project does not know what consumes it. It is an operator's confirmation,
not a test.

## Goal

1. `openaca` loses the `remote` command group and everything under
   `tools/remote/`, and loses nothing else.
2. Nothing in the repository refers to a hosted service, holds a credential, or
   names an account.
3. Every other command behaves exactly as it did.

## Non-goals

- Providing an upload path, an upload payload schema, redaction for transport,
  or a credential store. Those belong to whatever consumes OpenACA. This spec
  removes them; it does not relocate them here.
- Preserving `openaca remote` under a deprecation shim. The command is removed
  in the next release; see *Compatibility*.
- **Publishing anything.** The surfaces a consumer needs are the other spec's
  subject and must already exist. If a task here finds it needs to add a public
  API, that is a sign this spec was started too early.
- Changing scan semantics, BOM contents, posture rule behaviour, advisory
  matching, or any output format — with one stated exception under *Removal*.

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

One deliberate exception, stated here so the two halves of this spec do not
contradict each other: `tools/scan.py`'s next-action list ends every endpoint
scan with `sync to remote: openaca remote sync endpoint`, and
`tests/test_render.py` asserts it. That line advertises a command that will not
exist, so it goes with the removal — see *Removal*. It is the only behaviour
change outside the `remote` group, and a reviewer should see it called out
rather than discover it in a diff.

Otherwise, concretely, the following must behave identically before and after:
`openaca scan repo`, `openaca scan endpoint`,
`openaca scan bom`, `openaca bom repo`, `openaca bom endpoint`, `openaca bom
diff`, `openaca lint`, `openaca export`, `openaca promote`, `openaca seed`,
`openaca triage`, `openaca policy`, and every overlay in `overlays/`.

This is verifiable rather than aspirational, because nothing depends on the
remote package:

- `tools/cli.py` is the **only** module outside `tools/remote/` that imports it,
  and only to register the command group.
- `openaca/core` has no reference to it, so no consumer of the facade is
  affected.
- `tools/scan.py`, `tools/bom_cli.py`, `tools/lint.py`, `tools/export.py`,
  `tools/matcher.py`, `tools/policy*.py` and `tools/triage*.py` import nothing
  from it.

Three test modules outside `tests/remote/` reach into the remote package for
convenience and are the only in-repo callers that must change — `test_e2e.py`,
`test_collect.py` and `test_openaca_cli.py`. All three are addressed under
*Removal*.

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

**`openaca policy` keeps its behaviour exactly.** `validate` and `compile` over
a policy file on disk take the same arguments, produce the same output and exit
the same way, before and after.

That is a promise about behaviour, not about which module the code sits in —
*The facade must not import the CLI layer* above moves the compilation out of
`tools/policy_cli.py`, so claiming that file is untouched would contradict it.
What leaves the project is one network call, not the policy language, the
evaluator, the risk gates or the host compiler: a consumer that fetches a policy
document by whatever means can still compile it with `openaca policy compile`.

- `tools/remote/` and `tests/remote/`, which is where all four subcommands
  live.
- The `sync to remote: openaca remote sync endpoint` next-action line in
  `tools/scan.py`, and the assertion on it in `tests/test_render.py`. It
  advertises a command that no longer exists.
- The command-group registration in `tools/cli.py`.
- `deploy/remote/` — the MDM deployment scripts and the scheduled-sync agent
  they install exist only to run the removed command.
- `httpx` leaves `pyproject.toml`. It is used only by `tools/remote/`; OSV
  federation uses `urllib.request` from the standard library. An OSS scanner
  carrying an HTTP client it does not use is a dependency its consumers pay for
  and nothing needs.
- The remote touch points in three test modules outside `tests/remote/` —
  `tests/test_e2e.py`, `tests/test_collect.py` and `tests/test_openaca_cli.py`.
  In `test_e2e.py` that is three whole tests to delete plus a **fourth** touch
  point that is an *edit*, not a deletion: the `_prepare_remote_bom` tail of
  `test_github_and_docker_mcp_refs_survive_identity_lifecycle`, whose subject is
  identity and which survives with that tail removed.

  **`tests/test_posture_cursor.py` keeps all of its tests.** An earlier draft of
  this spec listed `test_scan_and_collector_import_the_shared_no_manifests` for
  deletion "because it asserts a property of a module that will not exist". No
  test of that name exists: `published-consumption-surfaces.md` moved posture
  resolution into `tools/posture/agent_surface.py` and renamed it
  `test_scan_and_agent_surface_import_the_shared_no_manifests`. It now asserts a
  property of surviving code, and deleting it would silently drop real coverage.
- `docs/remote-deployment.md`, and the one `openaca remote` clause in
  `docs/reference/cli.md` — a clause, not a section.
- **`README.md` lines 165-166**, which run `openaca remote configure --token`
  and `openaca remote policy compile`. Leaving them would fail this spec's own
  Goal 2 in the most visible file in the repository.
- **`.gitleaks.toml`**, whose sole allowlist entry exists to whitelist the
  synthetic `ot_*` credentials under `tests/remote/`. With that directory gone
  the allowlist describes nothing, and a secrets-scanner exemption that outlives
  its reason is exactly the kind of thing nobody revisits.
- **`docs/specs/policy-compiler.md`**, at line 161 and lines 220-226, which
  document `openaca remote policy compile` as *the* endpoint path — its
  invocation, that it reads the configuration written by `openaca remote
  configure`, and its behaviour when the remote is unavailable. Goal 2 says
  nothing in the repository refers to a hosted service, and a spec describing a
  removed command as the way to do the job is a reference of the most misleading
  kind: it reads as current documentation.

  The other six `remote` matches in that file mean *Claude's remote settings*
  precedence, an unrelated concept that stays. Edit the five, leave the six.

ADR-0032, ADR-0050, ADR-0051 and ADR-0061 all decide questions about a subsystem
that no longer exists. ADRs are immutable, so a new ADR supersedes them and each
is marked `status: superseded` with `superseded-by:` in its frontmatter. Their
bodies are not edited — an old PR must stay readable against the rules in force
when it was opened.

**ADR-0024 is deliberately not in that list.** It is already superseded, by
ADR-0032, and its frontmatter already points there. Repointing it at the new ADR
would overwrite a correct pointer with a less precise one and break the chain a
reader follows backwards. Four ADRs are superseded here, not five.

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
| A deprecation window for `openaca remote` | A user on the current release upgrades into a missing command. Deliberate: the capability moved rather than vanished, and the release notes name the last version carrying it. |
| Retiring the packaged sync skill | It invokes a command that will not exist, so it fails against a released OpenACA. It lives in another repository and cannot be landed from here. |

## Robustness bar

This aims to get right: that no command other than `remote` changes behaviour,
with the single documented exception; that nothing referring to a hosted service
survives; and that the scanner works from a built wheel with `httpx` absent.

It defers: deprecation shims, and anything in another repository.

A finding that some other command's behaviour changed is above the bar however
small. A finding that the removal could have been staged more gently is below
it — the release notes carry that.

## References

- `docs/specs/published-consumption-surfaces.md` — the first half of this pair,
  which must ship and be released before this one starts
- ADR-0028 — the `openaca.core` consumption facade
- ADR-0032, ADR-0050, ADR-0051, ADR-0061 — superseded by this work
- ADR-0024 — already superseded by ADR-0032, and left pointing there
