---
id: 0080
title: Compose a Devin store plugin from its cache copy
status: accepted
date: 2026-10-31
supersedes: null
superseded-by: null
amends: [0070, 0079]
---

## Context

ADR-0070 made Devin store plugins presence-only, because the store layout was not observed.
ADR-0079 then made each store plugin an inventory gap in Devin policy evidence.
Two live runs of Devin CLI 3000.11.234 on 2026-10-31 observed the store with a signed-in account.
They covered `--local`, git-subdir, personal-upload and account-wide-upload installs.

Each `lock.json` `resolved[]` entry has an `identity`, a `resolved.kind` and a `version_dir`.
The plugin root is `cache/<key>/<version_dir>`, and `<key>` ends in `-<sha256(identity)[:8]>`.
A `--local` root is a symlink to the source folder, and the other kinds are real copies.
The root has the layout of a plugin in a repository.
A plugin server's tool reaches a hook as `mcp__<server>__<tool>`, with no plugin prefix.
Devin drops a plugin server whose name a configuration server or an earlier plugin uses.
One install order was tested.

The detail is in [the Devin CLI kind spec](../specs/devin-cli-agent-kind.md#store-layout)
and [the policy compiler spec](../specs/policy-compiler.md#plugins).

## Decision

1. For a `resolved.kind` of `local`, `git` or `accountUpload`, the Devin composer finds the one cache folder that ends in the identity's hash. It realizes the `<version_dir>` root with the bundle walk of a plugin in a repository. The scan and BOM then show the plugin's contents. This amends ADR-0070's presence-only rule for these kinds.
2. Another `resolved.kind`, or a root that does not resolve, keeps the presence-only plugin. ADR-0079's gap applies to it, and to it only.
3. A realized store plugin's files key under `devin-plugin/<name>`, never under a path.
4. A plugin server that Devin shadows by name stays an occurrence. The collector does not apply the observed order.

## Alternatives considered

- **Keep every store plugin a gap until a marketplace install is observed:** rejected because the three observed kinds cover the installs an ordinary endpoint has. A gap for each of them fails strict collection on most endpoints.
- **Match the cache folder by its full sanitized name:** rejected because the truncation length is seen in one case only. The hash suffix identifies the folder without it.
- **Compose only the MCP servers of a store plugin:** rejected because the kind already parses the skills, agents and hooks of a plugin root. A second, smaller walk would drift from the declared one.
- **Key a store plugin's files by their path under the data root:** rejected because the folder name and the symlink target spell the source path. That leaks a home directory into a `bom-ref`. A source inside `--project` also gives one node two parents.
- **Drop a shadowed plugin server, as Devin does:** rejected because one install order was tested and `lock.json` records no install order field. A wrong drop hides a server that Devin loads.
- **Record the presence-only plugin as a gap in the graph:** rejected for the reason ADR-0079 gives.

## Consequences

A Devin endpoint BOM now lists the contents of most store plugins.
Strict Devin collection succeeds on an endpoint whose store plugins all resolve.
A hook call to a shadowed plugin server matches more than one subject.
The consumer then applies its uncertainty behavior, as for an imported and a native server of one name.
A store plugin keeps the identity its manifest gives, because the composer does not read the install source in `lock.json`.

## When to revisit

- A new `resolved.kind` is observed: add it to the composed kinds.
- More install orders show which plugin server Devin keeps: consider applying the order.
- The composer reads the install source in `lock.json`: set the plugin's identity and `mutable_install` pin from it.
