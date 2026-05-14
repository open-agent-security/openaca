---
id: 0013
title: Separate component identity from observation location
status: accepted
date: 2026-05-14
supersedes: null
superseded-by: null
---

## Context

ASVE scanners emit `ComponentRef` objects for both package-backed
components and agent-native components that do not have package URLs. Early
V0 work used `component_identity` for two different purposes: the logical
identity of a component and the inventory slot where the scanner observed
it. That is visible in hook identities such as
`claude-hook/settings/project/PreToolUse/0`.

That slot-based identity is useful for scan evidence, but it is not a
component identity. The same hook command can be used in `PreToolUse` and
`PostToolUse`, or in user and project settings. Those are different
observations of the same logical component, not different components.
MCP package refs already separate these ideas: package identity comes from
ecosystem/name/version, while `source_manifest` and `source_locator` record
where the scanner found it.

## Decision

`ComponentRef.component_identity` identifies the logical non-package
component. It must not include the manifest path, settings scope, event
slot, array index, or other observation-location details. Observation
details live in `source_manifest`, `source_locator`, `attributed_to`, and
`extra`.

Package-backed components should prefer `ecosystem`, `name`, and `version`
over `component_identity`. Non-package identities remain available for
inventory display and non-OSV matching, but they are logical identities:

- MCP binary launches identify the binary command, not the config file that
  declared it.
- Unpinned MCP package launches identify the package and launch family, not
  the manifest location.
- Claude plugins identify the plugin name and version when known, not the
  installed-plugins lockfile slot.
- Claude skills identify the skill name and version when known, not the
  directory where the scanner found it.
- Claude commands and agents identify their owner/name pair when owner is a
  real package or plugin boundary. Repo-local commands and agents are local
  inventory items and should carry observation location outside identity.
- Claude hooks identify the invoked hook payload, not settings scope,
  event, or index.

## Alternatives considered

- **Keep slot identity for hooks**: rejected because it makes location part
  of component identity. It also means moving the same hook command between
  `PreToolUse` and `PostToolUse` creates a new component even though the
  underlying executable or prompt did not change.
- **Move every non-package component to package-style name/version fields**:
  rejected because several agent-native surfaces do not have stable version
  conventions. Forcing package-like fields would hide the distinction
  between logical identity and versioned release identity.
- **Store both `component_identity` and `observation_identity` on
  `ComponentRef`**: rejected for MVP because `source_manifest`,
  `source_locator`, `attributed_to`, and `extra` already carry the
  observation evidence without expanding the shared data model.

## Consequences

Scanner inventory output may need to combine logical identity with
observation details for readability. For example, a hook leaf can show the
hook command plus event/scope details from `extra`, rather than relying on a
slot-shaped identity string.

Tests that asserted slot-shaped hook identities must be updated. This is a
scanner inventory behavior change, not an overlay schema change.

## When to revisit

Revisit if downstream consumers need a stable, opaque observation ID in
addition to logical component identity. If that need appears, add a separate
field rather than overloading `component_identity` again.
