# Posture findings

Posture findings are scanner-emitted configuration-hygiene checks. They're
distinct from vulnerability findings: no CVE lookup, no overlay record, no
OpenACA ID minted. They flag risky agent-composition shapes
(unpinned installs, `http://` MCP endpoints, endpoint overrides, and MCP
auto-approval) that wouldn't surface in a corpus-driven scan.

Posture rules are gated behind `--include-posture`:

```bash
openaca scan repo --target . --include-posture
openaca scan endpoint --include-posture
```

Without the flag, scanner output is strictly vulnerability findings. With
it, a separate "Posture findings (configuration hygiene)" section appears
in text output, posture entries appear in the JSON `findings[]` array with
`finding_type: "posture"`, and SARIF emits each rule as a separate SARIF
rule + result.

Posture findings never affect `--fail-on` exit codes. They are signal,
not gate.

## Why this exists

A repo or endpoint with no matched CVEs returns "no findings" today.
That's accurate, but the typical reaction is "did the tool even do
anything?" Posture rules give first-scan signal independent of the
corpus: even a clean install gets *some* visible output if it has
unpinned MCPs, `http://` endpoints, endpoint overrides, or MCP auto-approval.

## V0 rules

| Rule ID | Title | Severity | Confidence |
| --- | --- | --- | --- |
| [`openaca-posture-mutable-install-reference`](openaca-posture-mutable-install-reference.md) | Component installed from a mutable source reference | low | high |
| [`openaca-posture-insecure-transport`](openaca-posture-insecure-transport.md) | Remote MCP endpoint uses insecure transport | medium | high |
| [`openaca-posture-api-endpoint-override`](openaca-posture-api-endpoint-override.md) | Claude API endpoint is overridden | medium/high | medium |
| [`openaca-posture-mcp-auto-approve`](openaca-posture-mcp-auto-approve.md) | MCP server has auto-approval enabled | medium | medium |

## Standards mapping

Each rule carries a `standards{}` block (CWE / OpenSSF Scorecard / SLSA /
OWASP App / OWASP Agentic / OWASP MCP). The mapping appears in the JSON
and SARIF output and on each rule's documentation page. It exists so
findings route correctly into the consumer's existing dashboards — the
finding shows up as an Agentic-MCP issue *and* lines up with the CWE
their existing tooling tracks.

## Where to put a new rule

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) → "Adding a posture rule".
