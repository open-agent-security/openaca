# openaca-posture-api-endpoint-override

**Severity:** medium by default, high with adjacent token/model override ·
**Confidence:** medium

Claude settings override the Anthropic API endpoint.

## What triggers it

The rule reads Claude `settings.json` files and checks top-level settings and
the `env` block for endpoint keys such as `ANTHROPIC_BASE_URL`,
`ANTHROPIC_API_URL`, `apiUrl`, or `base_url`.

The finding is `medium` by default. It escalates to `high` when the same
settings file also overrides a token or model/provider value, because that
matches the more dangerous traffic-hijack shape.

## Why it matters

Endpoint overrides can be legitimate for enterprise gateways or local proxies.
They can also silently route prompts, source code, tool results, and model
responses through an unexpected service when committed into a project.

| Family | Code |
| --- | --- |
| OWASP App Top 10 | A05:2021 (Security Misconfiguration) |
| OWASP Agentic Top 10 | asi04 (Supply Chain) |

## How to fix

Remove project-local endpoint overrides unless the project intentionally
requires them. Prefer user- or organization-managed configuration for approved
gateways so repository clones do not silently change a developer's API route.

```jsonc
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://approved-gateway.example.com"
  }
}
```

## When to suppress

- Approved enterprise gateways or proxies.
- Local development proxies used intentionally by the developer.
- Test fixtures that intentionally model alternate providers.
