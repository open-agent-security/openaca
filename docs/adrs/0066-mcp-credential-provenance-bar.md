---
id: 0066
title: Resolve MCP credential provenance per header key within a scope
status: accepted
date: 2026-09-15
supersedes: null
superseded-by: null
---

## Context

ADR-0065 set a detection bar for inline MCP credentials — which fields are
read, that dummy values count, that header values never leave the local
posture pass. It set no bar for *attribution*: which settings file a finding
names, and which component a posture gate denies.

Attribution is a separate problem because agent hosts merge MCP configuration
across precedence scopes. One server's effective definition can draw its URL
from one scope, its `Authorization` header from another, an unrelated sibling
header such as `Accept` from a third, and `autoApprove` from a fourth. Each
combination is a distinct attribution question, and the set of combinations is
combinatorial.

Review of the rule's first implementation produced nine rounds of findings.
Every finding was correct in isolation and each was fixed, but detection was
complete after the first round; the remaining rounds were attribution under
progressively narrower scope splits. The plumbing reached roughly four times
the size of the rule it serves. Without a stated bar, a reviewer has no way to
tell a finding that breaks the product from one that describes an arrangement
no installation has, so the review cannot converge.

## Decision

Credential provenance resolves **per header key, within a single settings
scope**. A finding names the scope that declared the flagged header, and
attaches to the effective component that carries it. `autoApprove` resolves
independently of header ownership, because the two are separate risk signals
that different scopes may own.

Splits finer than a header key are out of scope. A scope that contributes only
an unrelated member of the same `headers` object does not become the owner; a
scope that contributes neither the flagged header nor the effective component
is not consulted.

Detection is above the bar and attribution below it. A configuration shape that
an ordinary installation produces, where a literal credential goes unreported,
is a defect regardless of how narrow its trigger looks. An arrangement where
the credential is reported but names a neighbouring scope is recorded as
deferred, not fixed.

## Alternatives considered

- **Resolve provenance at the server entry**: the original implementation.
  Attributes a credential a lower scope owns to any higher scope that touches
  the same server, so the finding names a file that does not contain the
  credential and a gate can deny the wrong component. Rejected: the finding's
  whole value is telling an administrator which file to edit.

- **Resolve provenance at the `headers` container**: cheaper than per-key, and
  correct whenever a single scope owns the whole container. Rejected because
  adding one unrelated header in a higher scope silently moves ownership of
  every credential below it, and that is an ordinary thing for a host or a
  second administrator to do.

- **Track every contributing scope and report them all**: most faithful to the
  merge, and needs no bar at all. Rejected: a finding that names four files
  tells an administrator less than one that names the file to edit, and the
  reporting shape would have to change for every consumer.

- **State no bar and keep fixing findings as they arrive**: rejected on
  evidence. Nine rounds produced one detection fix and eight attribution fixes,
  with no natural stopping point, while the plumbing grew past the rule.

## Consequences

Review of this rule now weighs findings against a stated boundary rather than
against completeness, so a narrow attribution split can be closed as deferred
with its cost recorded instead of reopening the plumbing.

The cost is real. Under a scope split this ADR places below the bar, a finding
can name a scope adjacent to the one holding the credential. The credential is
still reported and still attached to a component, so the risk surfaces; the
remediation pointer is less precise than it could be. An administrator who
splits one server across several settings files may have to look in more than
one to find the literal value.

Per-key resolution also costs a walk of each scope's raw entry per flagged
header, rather than one container lookup per server. The walk is bounded by the
number of scopes and headers on a single server, so it stays small.

## When to revisit

Revisit when a host gains a configuration surface that merges differently than
settings precedence does — per-key overrides, or a scope that can redefine a
header without declaring it. Revisit also if real reports show administrators
routinely split one server's definition across scopes, which would move those
arrangements from a combinatorial tail to ordinary usage and put them above the
bar.
