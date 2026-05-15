# openaca-posture-insecure-transport

**Severity:** medium · **Confidence:** high

A remote MCP endpoint is configured over `http://` (not `https://`).

## What triggers it

For each MCP server entry in `mcpServers` or `servers`, the rule reads
`entry["url"]`. If the URL string starts with `http://`, the entry is
flagged. Stdio servers (no URL field) are out of scope.

## Why it matters

MCP carries prompts, tool calls, and tool output. Over plain HTTP, all
of that traffic is observable to any network intermediary and tamperable
without detection. For an MCP server the traffic includes the model's
context window contents and the tool's return data — both
sensitive by default.

| Family | Code |
| --- | --- |
| OWASP App Top 10 | A02:2021 (Cryptographic Failures) |
| OWASP Agentic Top 10 | asi04 (Supply Chain) |
| OWASP MCP Top 10 | mcp04:2025 |

No CWE is forced here — there isn't a clean fit, and forcing one would
mislead consumers.

## How to fix

Change the endpoint URL from `http://` to `https://`. If the server is
hosted on a public domain, fronting it with TLS is a one-line change in
most reverse proxies (Caddy, nginx, Cloudflare).

```jsonc
{
  "mcpServers": {
    "weather": {
      "type": "sse",
      "url": "https://weather.example.com/mcp"
    }
  }
}
```

## When to suppress

- **Loopback endpoints** (`http://localhost`, `http://127.0.0.1`).
  The V0 rule does not exempt these, but in practice loopback traffic
  doesn't traverse a network. If you operate a local MCP server, the
  finding is informational only.
- **Air-gapped or controlled network segments** where HTTPS isn't
  available and traffic is isolated by other means. This is the
  user's call, not the rule's.
