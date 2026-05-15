# openaca-posture-missing-remote-auth

**Severity:** low · **Confidence:** medium

A remote MCP endpoint is declared without any visible authentication
material in the manifest.

## What triggers it

For each MCP server entry in `mcpServers` or `servers` that has a `url`
field, the rule looks for any of the following adjacent fields:

- `headers.Authorization` (any case)
- `env` (any populated env block, typically used to pass tokens via
  environment variables the launcher reads)
- `token`
- `apiKey`

If none of those are present, the entry is flagged.

Confidence is **medium**, not high — auth may legitimately live
out-of-band (system keyring, ambient cloud credentials, a proxy that
attaches headers). The rule surfaces "I cannot see any auth in this
manifest" as a prompt to verify, not as an assertion of misconfiguration.

## Why it matters

A remote MCP endpoint with no auth is effectively an open agent action
surface. Anyone who reaches the URL can list and invoke its tools, and
the model talking to it has no way to distinguish authenticated from
unauthenticated callers. Even when access is controlled by network
egress alone, that's a fragile boundary for an agent's tool surface.

| Family | Code |
| --- | --- |
| OWASP App Top 10 | A01:2021 (Broken Access Control), A07:2021 (Identification and Authentication Failures) |
| OWASP Agentic Top 10 | asi03 (Excessive Agency / Lack of Authentication) |
| OWASP MCP Top 10 | mcp07:2025 |

## How to fix

Declare auth in the manifest using one of the supported shapes:

```jsonc
// Bearer token through Authorization header
{
  "mcpServers": {
    "x": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${ENV_TOKEN}"}
    }
  }
}

// Or via env (the MCP launcher reads it):
{
  "mcpServers": {
    "x": {
      "url": "https://example.com/mcp",
      "env": {"OPENACA_TOKEN": "${ENV_TOKEN}"}
    }
  }
}

// Or via a top-level token/apiKey field where the client supports it:
{
  "mcpServers": {
    "x": {"url": "https://example.com/mcp", "token": "${ENV_TOKEN}"}
  }
}
```

## When to suppress

- **Endpoint provides auth out-of-band.** A proxy attaches `Authorization`,
  the host is reachable only over a mutually authenticated tunnel, or
  credentials come from an OS keyring the launcher reads. The manifest
  legitimately has no visible auth, but the deployment is authenticated.
- **Truly public read-only endpoints.** A status-style MCP server with
  no sensitive operations may not require auth. Make the choice
  deliberately — most agent endpoints have at least one tool whose
  output you'd rather not leak.
