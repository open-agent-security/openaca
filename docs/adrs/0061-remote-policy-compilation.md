---
id: 0061
title: Fetch remote policy documents before local endpoint compilation
status: superseded
date: 2026-08-31
supersedes: null
superseded-by: 0063
---

## Context

Policy documents are organization-wide, but their compiled host restrictions
can differ by endpoint because installed components, advisory results, and
posture findings differ. The existing policy compiler already performs those
endpoint-local steps from a policy file. The remote client already persists a
scoped endpoint credential and service URL for inventory synchronization, but
it had no path to retrieve a policy document for the same endpoint.

The final write into a host-managed settings location is privileged and is
separate from compilation (ADR-0049). Combining remote retrieval with that
write would make one command responsible for both an unprivileged network
operation and a platform-specific deployment action.

## Decision

OpenACA exposes `openaca remote policy compile`. The command reuses the
configured remote URL and endpoint credential to request the current policy
document, validates it through the same policy parser, scans the local target,
evaluates risk gates, and compiles the requested output artifact through the
same compiler as `openaca policy compile`.

The command writes only the caller-selected output artifact. It does not write
to a protected host configuration location, alter the remote policy document,
or keep a local policy cache. If the configured remote is unavailable, rejects
the credential, returns an invalid response or invalid policy, or has no policy
document, the command fails before replacing an existing artifact. Artifact
writes remain atomic.

The policy document is the sole policy input and the fresh endpoint scan is the
sole source of endpoint-specific evidence.

## Alternatives considered

- **Compile endpoint artifacts remotely.** Rejected because the remote service
  does not have the endpoint's current installed composition, advisory
  resolution, or posture evidence. Uploading stale or broader inventory would
  weaken the fresh-evidence rule in ADR-0049.
- **Create a separate policy credential and configuration file.** Rejected
  because policy retrieval is an endpoint operation within the same
  organization boundary as the existing remote client. A second credential
  adds rotation and configuration state without narrowing the command's access.
- **Cache the last policy document for offline compilation.** Rejected because
  a successful result could silently apply a policy that an administrator has
  since changed. Failure preserves the prior artifact and makes the unavailable
  state explicit; an offline policy workflow can remain the existing local-file
  command.
- **Install managed settings from the command.** Rejected because deployment
  needs platform-specific privileges and source-selection verification. The
  caller or a device-management tool retains that final step as ADR-0049
  requires.

## Consequences

One configured remote endpoint can now both synchronize inventory and retrieve
the organization policy without duplicating credentials or creating a second
policy evaluator. The command has the same host and output flags as local
compilation, so the endpoint-specific compiler behavior remains one code path.

Remote retrieval is deliberately fail-closed with respect to artifact updates:
an existing output stays in place when a new expected policy cannot be built.
That avoids replacing a known artifact with an empty or stale result, but an
operator must restore remote connectivity before producing a new artifact.

## When to revisit

Revisit when endpoint delivery gains a verified, host-native installation
mechanism, or when a signed policy cache can expose its revision and freshness
without making a stale policy look current. Do not add a cache merely to make a
network failure exit successfully.
