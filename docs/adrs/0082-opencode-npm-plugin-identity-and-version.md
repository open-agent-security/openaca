---
id: 0082
title: Identify an OpenCode npm plugin as plugin/npm/<name>, versioned from its install
status: accepted
date: 2026-11-17
supersedes: null
superseded-by: null
---

## Context

OpenCode loads an npm plugin from a spec in its configuration: `name`,
`name@1.2.3`, `name@^1`, or `@scope/name@tag`. A spec is often not an exact
version. Only an exact version lets an OSV advisory, a `MAL-*` record above
all, match the plugin's PURL.

OpenCode v1.19.5 installs each spec once, into `<cache>/packages/<spec>/`, and
reuses that install (`core/src/npm.ts`). Its documentation names another
directory, `~/.cache/opencode/node_modules/`, which the code does not read. See
[`docs/specs/opencode-agent-kind.md` § Plugins](../specs/opencode-agent-kind.md#plugins).

## Decision

An npm plugin is a `plugin` component with the source ecosystem `npm`. Its
identity is `plugin/npm/<name>`, the role-qualified package form of ADR-0042.
Its version is the `version` in
`<cache>/packages/<spec>/node_modules/<name>/package.json`, the directory that
OpenCode loads. With no install, only an exact version in the spec is a version.

## Alternatives considered

- **The version in the spec** — rejected. `name` and `name@^1` name no version.
  OpenCode does not update an install either, so a cached `@latest` can be older
  than the registry's latest.
- **The directory that the documentation names** — rejected. v1.19.5 does not
  read it. A leftover from an older release there names a version that does not
  run.
- **An occurrence-local `plugin/<name>`, as for a Devin store plugin** —
  rejected. An npm name is a stable source namespace, so the same plugin on two
  machines is one component.
- **A `marketplace` of `npm`** — rejected. A marketplace is the registry a host
  resolves a plugin from by its own rules. Here npm is the source ecosystem,
  which the PURL already carries.

## Consequences

An OpenCode plugin matches OSV npm advisories by PURL, as an npm MCP server does.

The scan reads a cache that OpenCode writes. A layout change in a later release
leaves a plugin with no version, which is a visible gap, not a wrong version.

`mutable_install` does not fire for a plugin. The rule reads launch lines, and a
bare spec reads as an opaque token.

## When to revisit

- When OpenCode changes where it installs plugins.
- When `mutable_install` learns to read a bare package spec.
