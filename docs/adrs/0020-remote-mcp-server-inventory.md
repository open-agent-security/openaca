---
id: 0020
title: Remote MCP server inventory via mcp-remote identity namespace
status: superseded
date: 2026-05-20
supersedes: null
superseded-by: 0029
---

## Context

V0's MCP parser (`tools/parsers/mcp_json.py`) is explicit in its header:

> V0 detection scope is package-pinned stdio servers (npx/uvx + binary
> fallback) so OpenACA can alias upstream CVE/GHSA records via PURL.
> URL/HTTP transports, secret-surface fields (`env`/`headers`/`oauth`),
> and TOML configs are out of V0 scope.

Round-1 beta evidence flagged this as a concrete coverage gap: a tester
ran `openaca scan endpoint` and reported "0 MCP servers" while having
HTTP/SSE MCPs configured in Claude Code. The scanner silently dropped
the URL-bearing entries; only stdio entries surfaced in inventory. The
posture rule `openaca-posture-insecure-transport` already detects these
entries for its specific check, so detection logic exists in
posture-rule space — what's missing is emitting URL-bearing entries as
inventory `ComponentRef`s.

This decision is enabled by ADR-0019, which separates source ecosystems
from component types. Remote MCPs fit ADR-0019's source-less pattern:
an HTTP endpoint URL has no source registry mapping (no npm/PyPI/etc.),
so `ecosystem` stays unset and the ref carries `component_type:
mcp_server` plus a logical `component_identity` for overlay matching.

## Decision

Extend `parse_mcp_servers` to emit `ComponentRef`s for entries that
declare a remote transport (a `url` field, with or without an explicit
`type`). Remote refs follow ADR-0019's source-less shape:

```python
ComponentRef(
    ecosystem=None,
    name=None,
    version=None,
    component_identity="mcp-remote/<normalized-host-path>",
    source_manifest=<settings.json | .mcp.json>,
    source_locator="$.mcpServers.<server_name>",
    extra={
        "component_type": "mcp_server",
        "transport": <"http" | "sse" | "streamableHttp">,
        "url": <original-url>,
        "runtime_hosts": [...],
        "declared_by": {...},
        "component_path": [...],
        "install_source": <original-url>,
    },
)
```

### Identity namespace: `mcp-remote/`

`mcp-remote/` names the identity *namespace*, not the component type.
It parallels the existing `mcp-stdio/...` namespace that prefixes
stdio MCPs by launch mechanism (`mcp-stdio/npx-unpinned`,
`mcp-stdio/uvx-unpinned`, `mcp-stdio/binary`). The namespace describes
the identity *shape* (URL-coordinates vs. package-launch-coordinates);
both stdio and remote MCPs carry the same `component_type: mcp_server`
in `extra`. This avoids duplicating type information inside the
identity string while preserving collision-free namespacing in the
match key.

### URL normalization for the identity portion

The host/path portion of the identity is the URL normalized as
follows:

| Rule | Example |
|---|---|
| Strip scheme | `https://x.com/mcp` → `x.com/mcp` |
| Strip query / fragment | `https://x.com/mcp?v=1#tag` → `x.com/mcp` |
| Strip default ports `:443` / `:80` | `https://x.com:443/mcp` → `x.com/mcp` |
| Keep non-default ports | `http://x.com:8080/mcp` → `x.com:8080/mcp` |
| Empty path → `/` | `https://x.com` → `x.com/` |
| Lowercase host (path case preserved) | `https://X.com/MCP` → `x.com/MCP` |
| Strip credentials | `https://u:p@x.com/mcp` → `x.com/mcp` |

The original URL is preserved verbatim in `extra.url` for display,
posture-rule input, and any future re-derivation.

### Transport classification

The `extra.transport` field records the user's declared transport:
- `"sse"` when `"type": "sse"`
- `"streamableHttp"` when `"type": "streamableHttp"`
- `"http"` when `"type": "http"` is set, or as the default when `url`
  is present without an explicit `type`

Transport is metadata. The identity does not vary by transport: two
entries pointing at the same `host/path` over different transports
are treated as the same logical component for matching purposes.

### Matching

Per ADR-0019 matching order step 3: refs with no source identity match
only overlay records that explicitly target
`database_specific.openaca.component_identity`. Remote MCPs do not
federate to OSV (no PURL exists for an HTTP endpoint). Coverage is
OpenACA overlays only — same model as skills, hooks, and source-less
plugins.

### Skip conditions

- `entry.disabled == True` (matches the stdio convention).
- URL contains interpolation (e.g. `${HOST}`) — the parser cannot
  normalize without env resolution; same conservative skip the
  package paths apply.
- URL is malformed (no parseable host).

When `url` and `command` are both present in one entry, `url` wins
and the entry is treated as remote. The MCP spec doesn't permit
this combination; favoring `url` matches how Claude Code's runtime
resolves the ambiguity.

## Alternatives Considered

- **Full URL as identity** (e.g. `mcp-remote/https://x.com:443/mcp?v=1`).
  Rejected: fragile to scheme/port/query churn; identities for the
  same logical endpoint diverge across configurations.

- **Hostname-only identity** (e.g. `mcp-remote/x.com`). Rejected: too
  coarse; cannot distinguish `x.com/mcp-a` from `x.com/mcp-b` on the
  same host. Advisory authors targeting a specific MCP service lose
  precision.

- **`mcp-server/` prefix in the identity** (matching component type).
  Rejected: duplicates `component_type: mcp_server` already in
  `extra`. The prefix should name the identity *namespace*, not
  repeat the type — paralleling the existing `mcp-stdio/` precedent.

- **Invent a `pkg:mcp/...` PURL ecosystem** to plug into OSV. Rejected:
  introduces a non-standard PURL ecosystem; OSV has no support; the
  source-less pattern from ADR-0019 covers the use case cleanly
  without inventing infrastructure no one else recognizes.

- **Wait for the MCP ecosystem to publish a canonical identity
  scheme.** Rejected as the sole V0 strategy: the ecosystem hasn't
  produced one and round-2 beta testers need inventory coverage now.
  `mcp-remote/` can coexist with a future canonical scheme; overlays
  targeting either identity will match.

## Consequences

- Inventory output lists remote MCPs alongside stdio MCPs under
  `component_type: mcp_server`, with `source.status: unknown` per
  ADR-0019.
- The `openaca-posture-insecure-transport` rule continues to fire on
  these entries unchanged (its detection is independent); the posture
  finding's component now corresponds to a real inventory entry.
- Round-2 beta testers with HTTP/SSE MCPs see them in scan output,
  closing the round-1 "0 MCP servers" interpretability gap.
- `mcp-remote/<host>/<path>` is stable enough for overlay authors to
  target a specific endpoint (e.g. "the MCP service at
  `mcp.asana.com/sse` has a known authentication bypass").
- No OSV federation for remote MCPs: corpus-only, same as skills.
- TOML configs and `env`/`headers`/`oauth` secret-surface extraction
  remain out of V0 scope.

## When to Revisit

- If a canonical identity scheme for MCP servers emerges (from the
  MCP project, OSV, or a registry), reconsider whether
  `mcp-remote/<host>/<path>` should be retained, deprecated, or
  aliased.
- If beta evidence shows that overlay authors consistently want
  hostname-only granularity for advisories, revisit normalization.
- If interpolation-bearing URLs become common in tester configs,
  decide whether to resolve interpolation at scan time or persist
  the skip behavior.
