---
id: 0068
title: Classify Pi declarations by loading surface before composing packages
status: accepted
date: 2026-09-16
supersedes: null
superseded-by: null
---

## Context

A Pi-bearing `package.json` can describe a package repository or select entry
points inside a native extension directory. Promoting every such file to a
package container gives the same resource two discovery routes. The existing
occurrence guard prevents a duplicate edge, but the selected parent and source
identity then depend on whether another file caused the project pass to run.
An empty project settings file must not change an extension into or out of a
package-private component.

Pi 0.85.1's
[extension discovery](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/package-manager.ts)
reads a native directory's `pi.extensions` entries as extension entry points.
It does not load the manifest's other resource types as an installed package
unless the directory is also selected as a package source.

## Decision

Classify declaration paths by their loading surface before constructing package
rows. `.pi` resource and settings paths and `.agents/skills` belong to their
enclosing project. A manifest within those resources is not an independent
package declaration, and does not suppress discovery of the enclosing project's
resource files. Package registry accounting uses the same classification.

A Pi-bearing manifest outside those surfaces remains a package declaration. Its
own project's resources participate in the existing joint selection pass; nested
package fixture projects and managed install trees remain excluded. Explicit
`packages[]` references still create package rows, including references to a
native resource directory. The existing Pi selector then resolves that overlap
before graph attachment.

This refines the discovery procedure under ADR-0067 without changing the graph
model, the package/resource types, trust semantics, or source identity contracts.

## Alternatives considered

- **Skip a resource when its occurrence key already exists:** the existing guard
  avoids duplicate attachment, but preserves whichever parent ran first and
  leaves an unjustified package container. It cannot decide source semantics.
- **Exclude all manifests below `.pi/extensions`:** loses a valid discovery
  signal when the manifest names an entry point outside the conventional tree,
  and does not address the same classification error in shared skills.
- **Treat every Pi-bearing manifest as a package:** attributes native resources
  to an npm/plugin namespace they were not loaded through, and can invent
  additional resources from fields that native discovery does not consume.
- **Replace Pi's composer with the generic bundle walker:** Pi's per-file
  filters, delta overrides, and native discovery still require a distinct
  selector. The defect is classification before that selector, not evidence
  that its data model should be replaced.

## Consequences

Native resource provenance and parents no longer depend on incidental settings
files. Package containers and advisory identities require package-source
evidence. Tests cover native and explicit package routes, package repositories,
shared skills, CLI inventory, and BOM validation together.

## When to revisit

Revisit if Pi changes native discovery to load whole packages, or introduces
another declaration surface that cannot be classified by the existing project
and package roles. Do not infer that change from a manifest filename alone.
