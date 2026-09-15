# MCP authentication header contains a literal credential

`openaca-posture-mcp-header-credential` — medium severity, high confidence.

## What triggers it

A remote MCP server has a nonempty literal value in one of these headers
(case-insensitive): `Authorization`, `Proxy-Authorization`, `Authz`, `X-Api-Key`,
`Api-Key`, or `X-Auth-Token`. Both JSON `headers` and Codex TOML `http_headers`
are checked through repository and endpoint scans. Disabled servers and stdio
servers are excluded, matching the existing transport-posture scope.

```json
{
  "mcpServers": {
    "demo": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer dummy-token"}
    }
  }
}
```

Run `openaca scan repo --target . --include-posture`. There is one finding per
server, listing the affected fields, never their values. Dummy tokens trigger:
the claim is inline credential storage, not a valid or compromised credential.
Like other posture findings, this does not affect `--fail-on` exit codes.

## Why it matters

MCP configurations can be committed, copied, or shared. Inline credentials
travel with them, even when HTTPS protects the connection to the server.

| Standard | Mapping |
| --- | --- |
| CWE | CWE-798: Use of Hard-coded Credentials |

## How to fix

Use the host's OAuth or environment-backed authentication support. For Claude
Code's JSON configuration:

```json
"headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
```

For Codex TOML, use `bearer_token_env_var = "MCP_TOKEN"` or `env_http_headers`
instead of a literal `http_headers` value. `http_headers` is static: putting
`${MCP_TOKEN}` there does not make it an environment-backed header.

JSON references such as `${TOKEN}`, `${env:TOKEN}`, and `${input:token}` are
treated as indirect, including after `Bearer` or `Basic`. An empty fallback
(`${TOKEN:-}`) is indirect; a literal fallback (`${TOKEN:-secret}`) is flagged.
The rule does not resolve variables, validate their syntax for every host,
contact servers, or verify credentials. Configure references your host supports.

Rotate the credential if the configuration was exposed. A local finding alone
does not establish that exposure occurred.

## When to suppress or accept

A deliberately nonfunctional test fixture may be acceptable. Review it as a
fixture rather than assuming an arbitrary dummy-looking string cannot be a
credential. Generic `env` values, custom authentication header names outside
the list above, OAuth storage, and credentials embedded in launch commands are
not covered by this rule.

The finding names the settings scope that declared the flagged header. When one
server's definition spans several scopes, that scope may not be the only file
involved — ADR-0066 resolves provenance per header key within a scope and
places finer splits below its bar, so look in the other scopes contributing to
the same server if the named file does not hold the literal value.

References: [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp#environment-variable-expansion-in-mcpjson),
[Codex MCP configuration](https://developers.openai.com/codex/mcp/).
