# openaca-posture-mutable-install-reference

**Severity:** low · **Confidence:** high

A component (MCP server, plugin, skill) is installed from a source
reference that can roll forward without an explicit version change.

## What triggers it

The scanner inspects every component's `install_source` (preserved on
the parsed `ComponentRef.extra`) and runs `is_mutable_reference`
against it. Mutable shapes:

- npx/uvx specs with no version pin: `npx @scope/server-foo`,
  `uvx mcp-server-bar`
- npx/uvx specs pinned to `@latest`: `npx @scope/server-foo@latest`
- npx/uvx specs with a non-exact version: `@scope/foo@1.2`,
  `mcp-bar>=1.0`
- Git refs targeting a branch or tag (not a full 40-char SHA):
  `git+https://host/x/y.git@main`, `git+https://host/x/y.git@v1.0.0`
- Docker image refs without a `@sha256:` digest, even when tagged:
  `ghcr.io/x/y:1.0.0`, `ghcr.io/x/y:latest`, `ghcr.io/x/y`

Immutable shapes (not flagged):

- Exact semver: `==X.Y.Z`, `@X.Y.Z`
- Full commit SHA: `git+https://host/x/y.git@a1b2c3d4e5f6…`
  (40 hex chars)
- Docker digest: `@sha256:<64-hex>`
- Local checked-in paths: `./local-bin`, `/opt/x`, `file://…`

## Why it matters

A mutable install ref is a permanent supply-chain exposure — the code
that runs tomorrow may not be the code that ran yesterday, even if no
manifest changed. For an agent component the blast radius is high:
the package that lands gets prompt access, tool-call output, and (for
MCP servers) the model's invocation context.

The rule's standards mapping captures this layered framing:

| Family | Code |
| --- | --- |
| CWE | CWE-1357 (Reliance on Insufficiently Trustworthy Component) |
| OpenSSF Scorecard | Pinned-Dependencies |
| SLSA | immutable-references |
| OWASP Agentic Top 10 | asi04 (Supply Chain) |
| OWASP MCP Top 10 *(when MCP-shaped)* | mcp04:2025 |

## How to fix

Pin to an exact version, commit SHA, or Docker digest. Per ecosystem:

```jsonc
// npx — exact version
{"command": "npx", "args": ["@scope/server-foo@1.2.3"]}

// uvx — exact version
{"command": "uvx", "args": ["mcp-server-bar==1.2.3"]}

// git ref — full SHA
"git+https://github.com/x/y.git@a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"

// docker — digest, not tag
"ghcr.io/example/mcp@sha256:<64-hex-chars>"
```

## When to suppress

- **Local checked-in components.** Plain paths and `file://` refs are
  out of scope by design and not flagged.
- **Internal-registry installs with controlled republish policy.**
  If your internal registry forbids overwriting a published version,
  a non-pinned spec is functionally pinned. The rule can't know that;
  treat the finding as a confirmation request rather than a defect.
