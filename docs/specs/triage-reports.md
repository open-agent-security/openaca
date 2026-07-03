# Triage And Exposure Reports

## Goal

OpenACA scan commands collect evidence: inventory, Agent BOM composition,
vulnerability findings, posture findings, observations, and source metadata.
Users also need a decision artifact that answers which agent components should
be reviewed or fixed first.

This spec defines the first triage/report layer over scan evidence. It does not
add new detection rules. It turns existing scan facts into component-centric
exposure summaries and forwardable reports.

## Terms

- **Scan**: evidence collection and matching. A scan answers "what is present,
  what matched, and where did it come from?"
- **Triage**: interpretation over scan evidence. Triage answers "which
  component matters first, why, and what action should the user take?"
- **Exposure report**: a triage output intended for a human reviewer or
  security/platform lead. It groups evidence by component, ranks the components,
  explains the composition path, and recommends one action per item.

## Command Model

The common path remains one command:

```bash
openaca scan endpoint --report exposure --output openaca-exposure-report.md
```

That command runs a normal endpoint scan and immediately renders an exposure
report from the resulting scan evidence.

The composable path is two commands:

```bash
openaca scan endpoint --format json > openaca-scan.json
openaca triage openaca-scan.json --report exposure --output openaca-exposure-report.md
```

`openaca triage` consumes a scan artifact. It does not read endpoint or repo
state directly in V1, and it does not query advisory sources independently.

## V1 Scope

V1 supports single-target triage:

- endpoint scan JSON from `openaca scan endpoint`;
- repo scan JSON from `openaca scan repo`;
- BOM scan JSON from `openaca scan bom`, if the scan output contains enough
  component-path and finding evidence.

V1 emits:

- `text`: terminal-oriented component grouping;
- `markdown`: forwardable exposure assessment report;
- `json`: machine-readable triage cards for Cloud and plugin consumers.

The Claude Code plugin should call the same CLI path rather than implementing a
separate report generator.

## Triage Card

A triage card is component-centric. It represents one risky component and the
evidence that made it worth reviewing.

Minimum fields:

- `component_id`: stable identifier from scan output;
- `component_label`: human label;
- `component_type`: plugin, MCP server, skill, hook, command, agent, or package;
- `rank`: integer position in this report;
- `priority`: `critical`, `high`, `medium`, `low`, or `info`;
- `confidence`: `high`, `medium`, or `low`;
- `action`: one of `remove`, `pin`, `upgrade`, `approve`, `replace`, `accept`,
  or `review`;
- `composition_path`: target-to-component path from scan output;
- `evidence`: vulnerability, posture, and observation references;
- `why_it_matters`: short generated explanation using only available evidence;
- `scope_limits`: caveats relevant to this card.

The card may include `capabilities` when scan evidence supports them. V1 must
label capability facts by provenance:

- `scanner-derived`: directly produced by an OpenACA parser or posture rule;
- `advisory-derived`: produced by OSV/GHSA/CVE/MAL advisory metadata;
- `external-scanner-derived`: produced by an explicit external scanner;
- `analyst-added`: added manually by a reviewer outside the scanner.

V1 CLI-generated reports should avoid `analyst-added` facts unless the user
provides an overlay/input file for them. A concierge assessment may add those
facts after local review, but they are not scanner claims.

## Ranking

Vulnerability severity alone is not the triage rank. Severity answers how bad a
specific finding is. Exposure ranking answers which component should be handled
first in this agent stack.

V1 ranking is deterministic and explainable. Inputs may include:

- maximum normalized severity among evidence attached to the component;
- finding type: vulnerability, posture, observation;
- active endpoint status, when known;
- component lineage: direct target child vs under plugin/skill/MCP;
- agent-relevant posture, such as mutable installs or insecure transport;
- fix/action availability, such as known fixed version;
- confidence.

V1 should start conservative. If capability facts are not available, the rank
must not pretend they are. Later capability extraction can improve ranking
without changing the command boundary.

## Markdown Report Shape

The report is intentionally short:

1. Target and scan scope.
2. Summary counts.
3. Top five triage cards.
4. Scope limitations.
5. Suggested next scan or review step.

The report is not a replacement for JSON or SARIF. It is a human-facing summary
that points back to scan evidence.

## Non-Goals

V1 does not:

- prove runtime exploitability;
- monitor runtime behavior;
- enforce or block agent execution;
- aggregate multiple machines;
- upload source code;
- replace SARIF, GitHub annotations, or raw scan JSON;
- create OpenACA advisory records.

Fleet/Cloud aggregation can reuse triage cards later, but fleet blast radius and
history are not part of the local V1 report.
