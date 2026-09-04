---
id: 0063
title: Remove the hosted-service client from OpenACA
status: accepted
date: 2026-09-04
supersedes: [0032, 0050, 0051, 0061]
amends: 0045
superseded-by: null
---

## Context

OpenACA shipped a client for one hosted service. `tools/remote/` stored an API
token on disk, uploaded an endpoint's Agent BOMs, fetched an organisation policy
over `GET /api/v1/policy`, and defaulted its API URL to one company's hostname —
a value repeated in three MDM deployment scripts and their documentation.

Examining one machine — which agents are installed, what they are composed of,
what is wrong with their configuration — is what OpenACA is for, and belongs in
the open. Being a client for a particular service's account and payload schema
is not. Shipping one here implied the project had a hosted half: an OSS scanner
whose default upload target is a named vendor's API is not a neutral,
vendor-agnostic overlay layer, whatever its README says.

The trigger for deciding it now rather than later is that the capability has
somewhere else to live. `published-consumption-surfaces.md` published
collection, policy compilation, kind selection and the finding types on
`openaca.core`, and the Click group on `openaca.cli` (ADR-0028), specifically so
that whatever wants to upload can do the job outside this repository. Before
that surface existed, removing the client would have taken the capability out of
reach along with it.

## Decision

The client is removed outright, in the same release, with no deprecation window.
`openaca remote configure|status|sync endpoint|policy compile`, `tools/remote/`,
`tests/remote/`, `deploy/remote/` and the `httpx` dependency all leave. The
capability relocated rather than vanished: a consumer reaches it through
`openaca.core` and `openaca.cli` rather than through in-tree code.

Afterwards the tree holds no client for any hosted service — no credential, no
API URL, no upload path, no account, no tenant, no company hostname — and
`socket.gethostname()` is not called anywhere in it. This is a rule about
hosted-service clients and accounts, not about sockets: OSV federation
(`tools/osv_federation.py`) queries a public advisory database over
`urllib.request`, sends nothing about the machine, and is central to what a
vulnerability scanner is. It stays, as does `tools/seed/llm.py`, a maintainer
tool no scan touches.

## Alternatives considered

- **Keep `remote policy compile` and drop only the uploader**: it is the one
  subcommand someone would think to keep, because it downloads rather than
  uploads — rejected because keeping it means keeping `client.py`, `config.py`,
  a stored credential and `httpx`, which is every part of what this removes. A
  removal that keeps the download is not a smaller version of this change; it is
  no version of it. The policy language, the evaluator, the risk gates and the
  host compiler all stay, so a consumer that obtains a policy document by any
  means still compiles it with `openaca policy compile`.
- **Ship a deprecation shim for one release**: an alias, or a stub command
  printing a migration message — rejected because the capability moved rather
  than vanished, the release notes name the last version carrying it, and a
  command that answers but cannot sync is worse guidance than a missing command.
  `openaca remote` now fails the way any unknown command fails, with Click's own
  error on exit 2.
- **Change the default API URL to a placeholder and keep the client**: rejected
  because the objection is to shipping a hosted-service client at all, not to
  which host it happens to default to. A placeholder would remove the vendor
  name and leave the credential store, the upload path and the account.
- **Delete `collector.py` entirely**: rejected because roughly 150 of its lines
  are producer glue over local discovery and the scanner, which is local
  analysis by the same test everything else is judged by.
  `published-consumption-surfaces.md` moved that half down into
  `tools/collect.py` first, precisely so this removal could not take it.

## Consequences

A breaking change to the CLI surface, released as such. A user relying on remote
sync pins the last release that carries it, which the release notes name, so the
upgrade path is discoverable from the changelog rather than from a failing
command.

`httpx` leaves the dependency set — it had no importer outside the removed
package, and `uv.lock` recorded `openaca` as its sole dependent, so nothing
loses it transitively. An OSS scanner carrying an HTTP client it never calls is
a cost its consumers pay for nothing.

The refusal-to-send gate (`upload_contract.py`) leaves **with** the send it
gated, so no half of a two-sided check is left behind: there is no longer a
payload leaving this tree for anything to validate, and whatever consumes
OpenACA keeps whatever validation its own transport needs.

`tools/graph_build.py::build_graph`, the legacy place-rooted graph, loses its
only production caller and becomes reachable from tests alone. It is marked as
such in its docstring rather than deleted: removing it is a change this decision
did not ask for, and it would cascade into `tools/bom.py`'s schema-0.4 branch,
which stays live through graphless `build_agent_bom(refs, …)` calls.

The packaged `openaca:sync` skill lives in the marketplace repository, invokes
`openaca remote sync endpoint`, and will fail against a released OpenACA. It
must be retired there; this repository cannot land that change.

**ADR-0024 is deliberately not superseded here.** It was already superseded by
ADR-0032 and its frontmatter points there. Repointing it at this ADR would
overwrite a correct pointer with a less precise one and break the chain a reader
follows backwards. It is transitively covered: four ADRs are superseded by this
one, not five.

## What this amends in ADR-0045

ADR-0045 keys an agent on **(asset, kind, agent id)** and sources the asset half
from *"the registration envelope's asset external id"*. There is no registration
envelope any more — it left with the client — so that clause names a source this
project no longer has, and a reader following ADR-0045 forward would arrive
nowhere.

The decision it records is unchanged and still right: the key still spans two
layers, the document still says what sort of agent it is, and the composition
source is still deliberately outside the key. What changes is only where the
asset half comes from. **A consumer supplies it.** Whatever uploads a BOM knows
which place it came from, and that has always been the layer that knows —
previously it was this project's own client, and now it is somebody else's.

ADR-0045's reasoning for keeping place identity out of the document survives
intact, and is worth restating because it is easy to lose with the transport
that carried it: a hostname, cloud account or cluster identifier is exactly what
a redaction pass strips from BOM content. Place identity travelling beside the
document rather than inside it is a property of the design, not an artefact of
how this project used to upload.

This is an amendment, not a supersession. ADR-0045 stays accepted, its body is
untouched, and it carries no reverse pointer — the repository's convention, set
by ADR-0041 amending ADR-0022 and ADR-0043 amending ADR-0016. The removal spec's
count of four superseded ADRs is also unaffected: amending is not superseding.

## When to revisit

If OpenACA ever decides to operate a hosted service of its own as part of the
open-source project, rather than as something a consumer builds on top of it.
That would be a decision about project scope first and about this client second,
so it would need its own ADR either way — and the surfaces on `openaca.core`
mean the client would be built against the same public API a consumer uses,
rather than reaching into `tools.*` as this one did.
