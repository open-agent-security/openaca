---
id: 0082
title: A Pi extension and a Pi package are each a plugin
status: accepted
date: 2026-11-17
supersedes: null
superseded-by: null
---

## Context

Pi has two surfaces with no counterpart by name in the closed component-type
set (ADR-0019, ADR-0031). An **extension** is a TypeScript module that Pi loads
into its own process. It can register tools, commands and MCP servers, and it
can block or change any tool call. A **package** is an npm or git distribution
of extensions, skills and prompt templates, declared in `settings.json`.

The multi-agent spec requires a taxonomy decision before a parser is written
for a surface with no counterpart. A type is a policy key: policy admission
acts on `mcps`, `plugins` and `skills`.

## Decision

Both are `plugin` components. A package is a plugin that contains the
extensions, skills and prompt templates it ships. An npm package takes the
`npm` ecosystem with its installed version, and a GitHub package takes the
`github` ecosystem with its checked-out commit, so each one matches OSV
advisories. Its `openaca:identity` is `plugin/npm/<name>` or
`plugin/git/<host>/<path>`. An extension has no identity of its own.

## Alternatives considered

- **A new `extension` component type** — rejected. The closed set exists so
  that every consumer, policy admission included, knows every type. An
  extension meets what `plugin` means: a unit of code a user installs into the
  agent, which adds tools and behaviour. A new type would leave it outside
  plugin admission until each consumer learned it.
- **An extension is a `hook`** — rejected. A hook is a declared binding from an
  event to a command. An extension also adds tools, commands, providers and MCP
  servers, and policy admission has no hook category.
- **A package is a `package` component** — rejected. That type means a software
  dependency of an agent component. Its scope rules suppress a package with no
  agent-component ancestor, which would hide the package the user installed.
- **Report only a package's contents, not the package** — rejected. The
  package is the unit with a version, and the version is what OSV matches.

## Consequences

A plugin admission policy acts on Pi extensions and packages. A BOM reader
cannot tell an extension from a package by type. The nesting tells them apart:
a package has children, and its `openaca:install_source` names an `npm:` or git
source.

## When to revisit

- The closed set gains a type for in-process code modules for another kind.
- Policy admission gains categories finer than `plugins`.
