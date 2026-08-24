---
id: 0049
title: Compile per endpoint and distinguish expected from observed enforcement
status: accepted
date: 2026-08-24
supersedes: null
superseded-by: null
---

## Context

An OpenACA policy is logical: admission names source/configuration values and
risk gates name known vulnerability and posture evidence. Risk gates make the
result endpoint-specific. Two endpoints with the same policy can have different
installed components, advisory results, or posture findings and therefore need
different host restrictions.

[Claude Code accepts managed settings](https://code.claude.com/docs/en/managed-settings)
from several independent sources. It uses the first source with a policy key,
rather than merging ordinary policy keys across sources: remote settings take
precedence over MDM or OS-level policy, which takes precedence over managed
settings files, which take precedence over Windows HKCU. File drop-ins merge
only within the file-based source. Claude documents a small set of
source-exception keys with their own cross-source behavior; the initial
compiler does not rely on them. A generated file can therefore be installed
correctly but ignored by Claude.

The compiler cannot honestly call its output the host's effective policy. A
configuration artifact proves only what OpenACA intended to apply; the host
must select and accept it.

## Decision

Compilation combines admission and risk gates into one complete, per-endpoint
expected policy. The output consists of the rendered host artifact, the
component decisions and reasons, the policy document digest, the artifact
digest, and the scan time. `openaca policy compile --dry-run` produces the same
expected policy without writing an artifact, so an administrator can inspect its
effect before deployment.

The initial Claude target is a dedicated file-based managed-settings drop-in.
An endpoint-management tool may distribute that file, but distributing a file
does not turn it into Claude's MDM or OS-level source. OpenACA does not write
Claude's remote settings, macOS configuration profiles, or Windows registry
policy in V1.

OpenACA records host observations separately from compilation. The reported
enforcement status is `verified` only when the host selected the expected source
and accepted the generated settings; it is `mismatch` when a higher-priority
source was selected or a generated setting was dropped; otherwise it is
`not_verified`. A policy consumer compares expected policy against this
read-only host observation. It must not reconstruct or label a full effective
host policy from compilation alone.

For Claude, `/status` is the source-selection evidence and `claude doctor` is
the invalid-or-dropped-settings evidence. A full effective-policy display is
permitted only for a selected source the host adapter can read and merge using
documented host semantics.

## Alternatives considered

- **Put admission in Claude remote settings and risk-gate output in an endpoint
  file.** Rejected because Claude selects one managed source rather than
  composing these policies. The risk gate could not reliably tighten an
  admission result from the remote source.
- **Upload each endpoint's compiled policy as a server-managed policy.**
  Rejected because endpoint risk results may differ while server-managed policy
  is organization-wide. It would also displace all endpoint-managed policy for
  the affected session.
- **Treat a successful compilation or file write as enforcement.** Rejected
  because a higher-priority source can silently ignore the artifact and the host
  can drop invalid settings. Installation and enforcement are different facts.
- **Build a generic delivery framework for every Claude source now.** Rejected
  because the initial compiler needs one safe, inspectable source. Additional
  delivery adapters need their own platform-specific verification semantics.

## Consequences

The initial path remains small: compile from a fresh endpoint scan, preview
with `--dry-run`, and deploy a dedicated managed-settings file. It also makes
enforcement reporting honest: a consumer can show expected restrictions,
selected source, and mismatch rather than confusing a policy author with a
logical-policy-to-artifact comparison.

Organizations that use a higher-priority Claude source cannot receive the
per-endpoint file artifact as native Claude enforcement in V1. They can still
use scan results and expected-policy reports, while the reported enforcement
state remains a mismatch or not verified.

## When to revisit

Revisit when a supported host exposes a documented, machine-readable effective
policy interface, or when an endpoint delivery mechanism can safely supply
per-endpoint output and independently confirm selection. Do not solve this by
splitting one OpenACA policy across unmanaged host sources.
