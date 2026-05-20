# openaca-posture-mcp-auto-approve

**Severity:** medium · **Confidence:** medium

An MCP server entry enables `autoApprove`.

## What triggers it

For each MCP server entry in `mcpServers`, `servers`, or flat `.mcp.json`
maps, the rule checks `autoApprove`. It flags:

- `autoApprove: true`
- `autoApprove: [...]` when the list is non-empty

It does not flag `autoApprove: false`, an empty list, or disabled servers.

## Why it matters

MCP servers can expose tools that read local context, call remote services, or
change files. Auto-approval reduces the per-use consent boundary for those
tools. That can be intentional for trusted internal servers, but it is risky
when copied from a project, plugin, or skill without review.

| Family | Code |
| --- | --- |
| OWASP Agentic Top 10 | asi03 |
| OWASP MCP Top 10 | mcp07:2025 |

## How to fix

Remove auto-approval or restrict it to the smallest explicit set of trusted
tools.

```jsonc
{
  "mcpServers": {
    "internal": {
      "url": "https://mcp.example.com/mcp",
      "autoApprove": []
    }
  }
}
```

## When to suppress

- Trusted internal MCP servers with reviewed tool boundaries.
- Local-only development servers where auto-approval is intentionally part of
  the workflow.
