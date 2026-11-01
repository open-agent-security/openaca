# Policy Compiler

Status: proposed

## Marketplace source evidence

Export `openaca:marketplace_source_digest` for a plugin with a valid marketplace source.
Hash the UTF-8 compact JSON array returned by `tools.marketplace.key` with SHA-256.
The digest identifies a source for catalog grouping without exporting its URL or local configuration.
Keep GitHub URL normalization identical to admission matching, including optional `.git` and trailing slashes.
Omit the property for a missing, invalid, or credential-bearing source.
Restore the digest when reading a CycloneDX document.
For installed Claude plugins, resolve undeclared marketplace names through `plugins/known_marketplaces.json`.
Use the exact registration key under the selected Claude configuration root.
An explicit settings registration takes precedence, including unsupported or malformed declarations.
Read the native registry only in endpoint mode.
Keep missing, invalid, credential-bearing, or ref-qualified sources unmatched.
Do not infer an official source from the marketplace name.
Do not use the digest to replace local source evidence during admission evaluation.

## Goal

Let an administrator describe which agent components may be used and which
known risks must block them, then turn that policy into a host-managed
configuration artifact for one endpoint.

The same policy file works locally and with a hosted control plane. OpenACA
owns the policy schema, validation, scan-backed evaluation, and host compiler.

Library callers can use `openaca.core.collect_policy_evidence` without rendering a host settings artifact.
Its `PolicyEvidence` result contains components, advisory records and matches, posture matches, and unmapped posture findings.
Pass those inputs to `apply_risk_gates`.
The native compiler uses the same collection boundary.

Each decision includes `rule_paths` for the rules that determine its result.
Paths use the policy document structure, for example `admission.plugins.blocked[0]`.
List indexes start at zero.

A blocked admission decision names its first matching block rule or the blocked category default.
A risk block names the matching vulnerability or posture gate.
Plugin contents inherit the owning plugin's rule paths.
Consumers can display these paths without repeating policy matching.

Pass `agent_kind="codex"` to collect Codex evidence through the same library boundary.
Pass `agent_kind="cursor"` to collect Cursor evidence.
The default remains `"claude-code"`.
`POLICY_EVIDENCE_AGENT_KINDS` lists the accepted kinds.
Unsupported agent kinds fail before discovery.
Cursor has no relocatable configuration root (ADR-0054).
Omit `target`, or pass `<home>/.cursor`, for Cursor.
Another Cursor `target` fails before discovery.
Pass `agent_kind="devin"` to collect Devin CLI evidence.
[Devin policy evidence](#devin-policy-evidence) gives its sources, its root rule and its gaps.
This selector changes collection only; the managed-settings compiler still targets Claude.
Codex inventory notes do not fail collection.
Actual component gaps fail collection by default.
Runtime callers can pass `allow_partial_inventory=True` to evaluate readable components independently.
The result records incomplete component coverage in `inventory_gaps`.
Callers must report these gaps and apply their uncertainty behavior to unresolved subjects.
Partial inventory does not resolve ambiguous identities or establish complete coverage.
The native managed-settings compiler retains strict collection.
It does not request partial inventory.
The collector passes each registered posture input channel to the rule runner.
Endpoint findings without a component target remain in `unmapped_posture`.
Codex command-approval and project-trust findings have no component target.
Unreadable command-approval text does not establish a component restriction.
It must not prevent independent component admission decisions from updating.
The evidence result does not certify clean endpoint settings or complete command-approval coverage.
The scan command retains the parser's coverage warnings.
Codex profile alternatives remain separate inventory occurrences.
Consumers must preserve ambiguity instead of treating the union as one active launch configuration.

Omit `target`, or pass `None`, to use the selected agent's default configuration discovery.
Pass a path to select an explicit configuration root.
A named root keeps its isolation even when it equals the default pathname.
Pass `project` explicitly to include project configuration.

`vulnerability_unavailable` is `None` when the policy requests no vulnerability gate.
Otherwise, it contains components without supported advisory coordinates.
An empty tuple means that collection found no such coverage gaps.
Incomplete inventory raises `PolicyEvaluationError` unless the caller requests partial inventory.
Advisory collection failures still raise in both modes.
Missing coordinates do not add a vulnerability block in the evaluator.
They do not prove that a component is vulnerability-free.

The result collections are detached tuples.
The caller owns the nested domain objects and must not mutate shared values.
The caller also owns evidence freshness and refresh scheduling.
The library provides no resident enforcement service.

The first compiler target is Claude Code. Other hosts may add compilers only
when OpenACA can produce a precise host-native restriction and verify it.
A host compiler has one narrow contract: given an evaluated endpoint policy, it
emits a host artifact, the component decisions behind it, and the limits on
those decisions. This is a contract, not a compiler plugin framework or a
commitment to additional hosts.

## Non-goals

The first version does not provide a general rule language, policy scopes,
per-component approvals, exceptions, expiry, hooks, a daemon, or runtime
interception. It does not delete or rewrite agent configuration.

## Decisions

| ADR | Decision |
|---|---|
| [0078](../adrs/0078-export-the-vulnerability-gate-matcher.md) | The vulnerability gate matcher is public, the evaluator calls it, and an unevaluable severity raises ([Matching one advisory against the vulnerability gate](#matching-one-advisory-against-the-vulnerability-gate)) |
| [0079](../adrs/0079-devin-store-plugins-are-policy-evidence-gaps.md) | A Devin store plugin is a policy evidence gap until its bundle layout is verified ([Devin policy evidence](#devin-policy-evidence)) |
| [0080](../adrs/0080-compose-devin-store-plugins-from-the-cache.md) | A Devin store plugin of an observed install kind is composed from its cache copy, and a shadowed plugin server stays an occurrence ([Plugins](#plugins)) |
| [0071](../adrs/0071-devin-cli-refuses-config-dir.md) | Devin refuses a relocated configuration root ([Devin policy evidence](#devin-policy-evidence)) |

## Policy document

A policy document is YAML or JSON. YAML is the convenient form for a local
file; both encodings represent the same document.

```yaml
version: 1

admission:
  mcps:
    default: blocked
    allowed:
      - command: ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
      - url: "https://mcp.example.com/mcp"
    blocked:
      - command: ["uvx", "unsafe-mcp"]

  plugins:
    default: allowed
    blocked:
      - plugin: "unreviewed@third-party"
      - marketplace: "https://github.com/example/untrusted-marketplace.git"

  skills:
    default: allowed

risk_gates:
  vulnerabilities:
    severity_at_least: critical
    ids: ["CVE-2026-12345"]
  posture:
    rules: ["openaca-posture-insecure-transport"]
```

`version` is required and starts at `1`.

### Admission

Admission answers: **may this configured component be used?** It has three
component categories:

| Category | Exact targets | Default |
|---|---|---|
| `mcps` | One exact command array or URL | `allowed` or `blocked` |
| `plugins` | One exact `plugin@marketplace` identifier or canonical marketplace source | `allowed` or `blocked` |
| `skills` | None in V1 | `allowed` or `blocked` |

Each MCP server and plugin category has an optional `allowed` list, optional
`blocked` list, and required `default`. A target must appear in at most one
list. Validation rejects an overlap rather than assigning surprising
precedence. A component not in either list follows `default`.

A plugin identifier target and a marketplace target can independently match
the same plugin — for example, an explicit `plugin` entry naming a plugin
whose marketplace is separately listed under `blocked`. These are different
target types, so the single-list overlap check above does not catch this
case. When a plugin's exact identifier and its marketplace source resolve to
different admission results, block takes precedence: a block on either
target blocks the plugin. A plugin admission list therefore cannot be used to
carve an allowed exception out of a blocked marketplace; it can only add
narrower blocks. This keeps the more specific target from ever loosening the
broader one, consistent with compilation only adding restrictions.

Legacy Claude custom slash commands (`commands/*.md`) use the `skills` admission
category. Claude Code 2.1.302 loads them through its skill registry and suppresses
that loader under `strictPluginOnlyCustomization: ["skills"]`. Their evaluator
result must therefore follow the same default as the existing native artifact.
Plugin-bundled commands continue to inherit the owning plugin's decision.

`skills` deliberately has only `default`. It means skills installed outside a
plugin; plugin-bundled skills inherit the plugin’s policy. Current hosts do not
offer a portable, managed way to block or allow individual skills in this
category; claiming otherwise would make the policy look more precise than it is.

All targets are exact source/configuration values. V1 accepts no component
identity, BOM reference, digest, glob, regular expression, or display-name
match. A policy can therefore be written before the component appears in an
endpoint inventory.

A command target is a non-empty array of literal strings. It matches the full
command and argument array token by token, in order, with case-sensitive string
comparison. The compiler does not resolve executable paths, canonicalize
equivalent flags, or expand environment variables in policy values. The same
array becomes the host-native restriction, so a policy author can see exactly
what is being matched. Functionally equivalent invocations are separate targets
and must be listed separately when they need the same policy result.

A marketplace entry is a plugin admission target, not a fourth component
category. Allowing a marketplace permits the plugins obtained from that
marketplace; it does not separately trust selected contents of a plugin. A
plugin remains the trust boundary for its bundled MCP servers, skills, and
other contents. This policy does not define marketplace trust for standalone
MCP servers or skills.

V1 represents a marketplace source as its exact URL without a branch or tag
selector. A registered Claude marketplace source that includes a `ref` cannot
be matched by this document shape, so the compiler does not flatten it into a
broader URL match.

### Risk gates

Risk gates answer: **does a known risk block an otherwise admitted component?**
They are optional. Every configured risk gate blocks; there are no `warn`,
`alert`, or `disabled` outcomes. Omitting a gate disables it.

V1 supports these gates:

| Gate | Match |
|---|---|
| Vulnerability severity | An advisory at or above `severity_at_least` |
| Vulnerability ID | An exact CVE, GHSA, or OSV ID in `ids` |
| Posture | An exact OpenACA posture rule ID in `posture.rules` |

Each configured condition independently blocks. For example, a component with
one listed advisory blocks even if its severity is below the configured
threshold.

A configured vulnerability ID matches an advisory's primary ID or any of its
aliases (for example, a configured CVE ID matches a GHSA-primary record that
lists the CVE as an alias). The evaluator must compare against the full set of
identifiers OSV records for the match, not only the ID surfaced as the
finding's primary advisory ID; comparing the primary ID alone would silently
fail to block an advisory reached under an alternate identifier.

### Matching one advisory against the vulnerability gate

`openaca.core.matches_vulnerability_gate(gate, advisory) -> bool` gives the
vulnerability gate's answer for one OSV-shaped advisory record.
`apply_risk_gates` calls it for each matched advisory. There is no second
implementation. A consumer that watches a gate without blocking calls the same
function, so it sees exactly the advisories that the gate blocks.

The function applies these steps in order:

1. If an entry in `gate.ids` is the advisory's `id` or one of its `aliases`,
   return `True`. Severity is not read.
2. If `gate.severity_at_least` is not set, return `False`.
3. Derive the advisory's severity with `derive_severity_label`. An upstream
   `database_specific.severity` label wins, and `MODERATE` reads as `MEDIUM`.
   Otherwise the first parseable CVSS v3 or v4 vector gives the label.
4. If no label is derived, raise `PolicyEvaluationError`.
5. Return `True` when the label is at or above the floor. The order is `NONE`,
   `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

Step 4 is the fail-closed rule of the evaluator. A matched advisory with no
severity data is not evidence of low risk, so compilation fails and keeps the
previous artifact ([ADR-0064](../adrs/0064-report-vulnerability-coverage-gaps.md)).
The exported function raises the same error. A consumer that must not fail
catches it and decides for itself. An advisory listed by id never raises.

`gate` is a `VulnerabilityGate`. Read it from a parsed policy as
`policy.risk_gates.vulnerabilities`, or build one from the same parameters.
The constructor accepts the floor in any case, as the policy document does,
and stores it in upper case. It raises `PolicyValidationError` for a floor
that the policy document rejects. Build `ids` as a `frozenset`.

`tests/test_policy.py` runs every floor, every way an id can match, and every
severity an advisory can derive to. For each case, it checks that the exported
function and `apply_risk_gates` give the same answer as an independent table.

When a finding is on a component contained by a plugin, the compiler blocks the
owning plugin. It must not attempt to allow a plugin while blocking one of its
bundled contents. When a finding is on an MCP server, the compiler blocks that
exact MCP server. A finding on a skill installed outside a plugin is reported
as not enforceable unless the selected host can express the category-wide skill
restriction.

## Compile

```text
openaca policy validate policy.yaml
openaca policy compile policy.yaml --target ~/.claude --host claude --output managed-settings.json
openaca policy compile policy.yaml --target ~/.claude --host claude --project . --output managed-settings.json
openaca policy compile policy.yaml --target ~/.claude --host claude --dry-run
openaca policy compile policy.yaml --target ~/.claude --host claude --managed-settings-dir /etc/claude-code --dry-run
```

`validate` checks the document only. It exits `0` when valid and `1` when
invalid, writes diagnostics to stderr, and has no success output.

`compile` always validates, scans the supplied target, obtains the advisory
data needed by configured risk gates, evaluates the policy, and writes a
host-specific artifact. There is no separate public `check` command: an
endpoint scan is the evidence-gathering operation.

`--target` is the endpoint's agent configuration location to scan. It is not
the system-managed settings location. `--managed-settings-dir` separately
selects the host directory whose existing managed settings files are inspected
for generated-key collisions; it defaults to Claude's operating-system-specific
system directory. An absent directory has no file-based policy to collide with;
an existing directory or drop-in that cannot be read fails compilation.

`compile` accepts the same `--project` option as `openaca scan endpoint`
(ADR-0017): project-local skills, MCPs, and plugin manifests are scanned only
when `--project` is passed. Without it, compilation scopes to user-level
config only, and a component that exists solely in project-local config is
absent from both admission and risk-gate evaluation — the report is not a
complete endpoint policy in that case. As with `scan endpoint`, omitting
`--project` prints a reminder note rather than failing or defaulting to the
current directory.

### Native Claude MCP inventory

Inventory the native user and project-local MCP maps in `.claude.json`.
An explicit `--config-dir` or `CLAUDE_CONFIG_DIR` selects the file in that directory.
Default discovery selects the home-level file only when no root override applies.
Use `collect_policy_evidence(policy, target=None, project=...)` for that default discovery.
Pass a path for an explicit root.
A named root retains its isolation even when it equals the default pathname.

Keep the exact source file and `claude_mcp_scope` with each observation.
Read only the selected project's local map.
Keep its actual project key as evidence, outside the stable source locator.
Retain those fields in a serialized BOM.
Do not merge or parse an unused alternative global file.
Treat invalid selected maps as incomplete evidence.

Select native local, project `.mcp.json`, and native user entries through one resolver.
Apply precedence by server name: local, then project, then user.
Select the whole entry; do not merge fields across scopes.
Honor the selected project's `disabledMcpServers` entries in the local and user scopes.
A project `.mcp.json` entry uses a different approval list. The disable list does not control it.
Skip invalid entries before selection, so a valid lower scope can supply the server.

Require a command for stdio entries and an explicit supported type for URL entries.
Accept `http`, `streamable-http`, `sse`, and `ws` URL transports.
A URL without a type does not become a policy subject.
Keep valid sibling entries when another entry is invalid.
Malformed configuration containers still produce incomplete evidence.

Derive native and project posture inputs from the selected refs.
Do not reread all project entries as active posture inputs.
The generic MCP parser remains permissive for declared inventory and other hosts.
See [ADR-0077](../adrs/0077-select-claude-mcp-policy-evidence.md) for this boundary.

This resolver selects configured entries; it does not certify a live connection or project approval.
Session-specific overrides, workspace trust, and connector discovery remain outside this selection contract.
Legacy settings-based inventory and plugin selection retain their existing contracts.
Read local-scope header credentials from that scope's own configuration.
Include native entries in component posture checks.
The source paths and project keys were checked against Claude Code 2.1.302 and its native `mcp add` commands.

`--dry-run` performs that same validation, scan, advisory lookup, and policy
evaluation, but writes no artifact. It prints the complete expected policy:
the rendered host settings, every component decision and its reason, and every
`not_enforceable` result. It is the preview an administrator uses before
deploying a policy. A policy-management UI may present the same report from a
recent scan, but must label it with that scan time; only endpoint compilation
uses fresh endpoint evidence.

If no vulnerability gate is configured, compilation does not require advisory
queries. With a vulnerability gate, supported components are checked against
fresh advisory data. A component without a supported advisory lookup coordinate
does not abort compilation or acquire a vulnerability block solely for being
unqueryable. Admission and posture rules still apply to that component.

The report includes `vulnerability_coverage` whenever a vulnerability gate is
configured: `total_components`, `queryable_components`, and `unavailable` entries
with the component label, source manifest, source locator, and reason. Text
reports show the counts and every coverage gap. These counts describe lookup
support, not proof of vulnerability-free software; an admission result of
`allowed` is not a clean bill of health. No coverage field is emitted when
vulnerability checking was not requested. See ADR-0064.

If a supported advisory lookup fails, or a matched advisory's required severity
cannot be evaluated, compilation still fails without replacing a previous
artifact. A fresh admission result must not be combined with stale risk data.

Compilation also fails, without replacing a previous artifact, when a
generated key already exists in another file at the target (see Claude Code
target). Writing into a key another file already governs risks silently
loosening that file's own restriction through the host's merge rules, not just
overwriting it.

The compiler emits a concise report with the policy result for every discovered
component and, for matches that cannot be represented by the host, an explicit
`not_enforceable` result. Successful compilation never implies that every
matching risk was blocked.

The compiler writes an artifact but does not install it into a protected host
configuration location. A local administrator or device-management tool owns
that final atomic write. The artifact and report together are the endpoint's
**expected policy**: the settings OpenACA intends the host to enforce for that
endpoint. The logical policy document is an input to this result, not a second
policy to compare in the user interface.

## Devin policy evidence

`collect_policy_evidence(policy, project=..., agent_kind="devin")` collects the
installed composition of the [Devin CLI kind](devin-cli-agent-kind.md).
The collector adds no Devin parser. It uses the kind's walk, layer merge and
import gates.

### Root

Devin has no relocatable configuration root (ADR-0071).
Omit `target`, or pass the root that Devin reads.
That root is `$XDG_CONFIG_HOME/devin` when the variable holds an absolute path, else `~/.config/devin`.
Another `target` fails with `PolicyEvaluationError` before discovery.
The plugin store root follows `$XDG_DATA_HOME` in the same way.
Pass `project` to include the project layers.

### MCP sources

| Scope | Files | Selection |
|---|---|---|
| User | `<config>/mcp_config.json`; the legacy `mcpServers` key of `<config>/config.json` | Native layer |
| Project | `.devin/mcp_config.json`; the legacy key of `.devin/config.json` | Native layer, in each project layer |
| Project local | `.devin/mcp_config.local.json`; the legacy key of `.devin/config.local.json` | Native layer, in each project layer |
| Imports | `.mcp.json`, `.claude/settings*.json`, `~/.claude.json`, `~/.claude/settings*.json`, `~/.claude/mcp_servers.json`, `.cursor/mcp.json`, `~/.codeium/windsurf/mcp_config.json` | One `read_config_from` switch for each owner. Never merged |

Native layers merge by server name, and the highest layer wins.
The order is project local, then project, then user.
A nearer project layer wins over an ancestor layer.
In one layer, the dedicated file and the legacy key both stay as occurrences.
A `"disabled": true` entry gives no subject.
It also removes the same name from each lower native layer.

An imported server never merges with a native server of the same name.
Devin's documentation does not state their order.
Each stays as an occurrence with its own `source_manifest`.

### Selector

Devin names an MCP tool `mcp__<server>__<tool>` in a `PreToolUse` hook.
Its generic `mcp_call_tool` names the server in `tool_input.server_name`.
Each MCP component keeps the configured server name in the last `component_path` entry.
The consumer matches the hook's server name to that entry.
When two occurrences have the name, the collector does not choose one.
The consumer keeps that ambiguity and applies its uncertainty behavior.
Use `source_manifest` to tell the user, project, local and import scopes apart.

### Plugins

Devin installs plugins at user level only.
The collector reads the plugins in `<data>/plugins/lock.json` (spec: [Devin CLI kind, Store layout](devin-cli-agent-kind.md#store-layout)).
The store layout was observed with Devin CLI 3000.11.234 in two live runs on 2026-10-31.

A store plugin's contents are components when the kind composes its copy.
That is so for a `resolved.kind` of `local`, `git` or `accountUpload` whose cache folder resolves.
The kind finds the one `cache/` folder whose name ends in `-<sha256(identity)[:8]>`, then its `<version_dir>`.
A `--local` copy is a symlink to the source folder, and the kind follows it.
The plugin's MCP servers, skills, agents and hooks then come from the bundle walk of a plugin in a repository.

These MCP declarations loaded in the live runs:

| Declaration | Observed |
|---|---|
| A root `.mcp.json` | Yes |
| An inline `mcpServers` map in `.devin-plugin/plugin.json` | Yes |
| `mcpServers` as one file path | Yes |
| `.claude-plugin/plugin.json` with a root `.mcp.json` | Yes |
| `mcpServers` as a list of paths | No. Composed from Devin's documentation |
| `mcpServers` as `{paths, exclusive}` | No. Composed from Devin's documentation |

A store plugin stays presence-only in these cases:

- Its `resolved.kind` is another value, or is absent.
- No cache folder ends in the hash, or more than one does.
- Its `version_dir` is not one path segment, or the root is not a directory.
- No manifest candidate qualifies at the root.

Each presence-only store plugin adds one entry to `inventory_gaps`.
Strict collection fails when the store holds one.
With `allow_partial_inventory=True`, the result keeps the readable components and the gaps.
A hook call to that plugin's MCP server resolves to no collected subject.
The consumer applies its uncertainty behavior to that call.
See [ADR-0079](../adrs/0079-devin-store-plugins-are-policy-evidence-gaps.md) and [ADR-0080](../adrs/0080-compose-devin-store-plugins-from-the-cache.md).

A plugin server's tool reaches a `PreToolUse` hook as `mcp__<server>__<tool>`.
The name has no plugin prefix, for each install kind and declaration form.
The [Selector](#selector) rule therefore applies unchanged.
The last `component_path` entry holds the configured server name.
The component's `controlled_by_plugin` is true, and its decision follows the owning plugin's decision.
The component's `source_manifest` is the path in the cache.
The hook's `tool_provenance.path` can name the source folder instead, so match by the server name.

Devin drops a plugin server whose name a configuration server or an earlier plugin uses.
The evidence keeps each occurrence of the name.
Only one install order was tested, so the collector does not choose the winner.
The consumer gets more than one subject for the name, and applies its uncertainty behavior.
The hook's `tool_provenance.plugin` holds the plugin's `lock.json` `identity`, and the evidence does not carry it.

### Not collected

These sources have no file that the collector reads, or the Devin CLI kind spec defers them:

- MCP servers and personal plugins from the Devin Cloud account (`read_config_from.cloud`).
- MCP servers that an ACP client passes in `session/new` or `session/load`.
- Team settings MCP allowlists and registries.
- OpenCode and Zed MCP imports.

### Posture

The kind's posture rules apply to the selected native and imported servers.
These rules are `insecure_transport`, `mcp_header_credential`, `mutable_install`,
`skill_capability`, `mcp_auto_approve` and `command_policy_allow`.
`mcp_auto_approve` reads `mcp__…` allow entries in the merged `permissions` lists.
A finding with no component target stays in `unmapped_posture`.

## Claude Code target

The Claude compiler emits an endpoint-specific, system-managed settings
drop-in, intended for a dedicated file such as
`managed-settings.d/50-openaca-policy.json`. It must not overwrite an
organization’s other managed settings. A device-management tool may distribute
that file, but Claude still treats it as a file-based managed source.

For this target, the compiler can express:

| Policy item | Claude output |
|---|---|
| MCP admission | exact managed MCP allow/block settings, with the managed-only lock when default is blocked |
| Plugin block | managed plugin disablement for an exact `plugin@marketplace` ID |
| Marketplace admission | prospective managed source restriction for marketplace add, install, update, and refresh |
| Standalone skill block | `strictPluginOnlyCustomization: ["skills"]` for direct user/project skills |

Claude does not document a managed plugin allowlist that prevents every
unlisted installed plugin from running. Therefore a plugin policy with
`default: blocked` and allowed-plugin exceptions is evaluated but reported as
`not_enforceable` for Claude V1. Marketplace source restrictions are likewise
prospective controls: they govern marketplace add, install, update, and refresh,
not a claim that an already-installed plugin is disabled. Exact plugin blocks
remain enforceable through managed plugin disablement.

When a discovered plugin is blocked because its marketplace is blocked under
block-precedence (see Admission), the discovery pass emits an exact
`plugin@marketplace` disablement for that installed plugin, the same
enforceable mechanism as an explicit plugin block, instead of relying on the
prospective marketplace restriction alone. The marketplace-level restriction by
itself governs only plugins the compiler has not discovered; once a plugin
sourced from that marketplace is discovered, its own exact block is what
disables it.

The standalone-skill setting names only `skills`, so it does not request the
same restriction for agents, hooks, or MCP servers. It still does not govern
skills bundled inside a plugin; those follow the plugin’s admission result.

Claude applies its MCP allowlist to plugin-provided MCP servers as well as
standalone servers. When `mcps.default` is `blocked`, the compiler therefore
adds each observed MCP from an admitted plugin to `allowedMcpServers`. If that
exact command or URL is also globally blocked, Claude cannot preserve both
results; the compiler leaves the global block in place and reports the
plugin-admitted MCP as not enforceable.

### Delivery and verification

[Claude accepts managed policy keys](https://code.claude.com/docs/en/managed-settings)
through remote settings, MDM or OS-level policy, a managed settings file, and
the Windows HKCU registry. Those are separate sources, not delivery aliases:
Claude selects the first source that contains a policy key and ignores the
others for ordinary policy keys. The order is remote settings, MDM or OS-level
policy, managed settings files, then HKCU. File drop-ins merge with the base
file and other file drop-ins, but do not merge with a selected remote or MDM
source. Claude documents a small set of source-exception keys with their own
cross-source behavior; V1 does not rely on them for policy enforcement.

V1 produces a file-based artifact only. It does not write a macOS configuration
profile, a Windows registry policy, or a remote Claude policy. An organization
using MDM to copy the generated file is still using the file-based source.

Risk-gate results can differ between endpoints, so the compiler combines
admission and risk gates into one complete endpoint artifact. It must not split
admission policy into a remote source and risk-gate restrictions into a local
file: a selected remote source would ignore the file. An organization with a
higher-priority Claude policy may still use OpenACA for scanning and reporting,
but the compiler reports the generated file as `mismatch` rather than
claiming native enforcement.

The generated report records the artifact digest, policy document digest, scan
time, intended file path, component decisions, and each limitation. A policy
consumer compares that **expected policy** with the host's read-only observed
state:

| Observed state | Meaning |
|---|---|
| `verified` | Claude selected the file-based source, and the merged file-based configuration — the base file plus every drop-in, combined under Claude's documented merge rules — matches the generated artifact for every generated key. |
| `mismatch` | Claude selected a higher-priority source, dropped a generated setting, or another file drop-in overrode a generated key in the merged file-based configuration. |
| `not_verified` | OpenACA has no host observation, or cannot resolve the merged file-based value for a generated key; compilation and installation alone are not proof of enforcement. |

Selecting the file-based source is not sufficient on its own: file drop-ins
merge with each other, so a same-named key from another drop-in can override
OpenACA's generated value while the file-based source is still selected overall.
`verified` requires comparing the post-merge value, not the source selection
alone.

The reverse direction is also possible. Claude merges `managed-settings.json`
first, then every `managed-settings.d/*.json` file in alphabetical order: a
scalar key takes the later file's value, and a list key unions across files
with duplicates removed. A union on an admission allowlist, such as
`allowedMcpServers`, can only add entries relative to any other drop-in's list
for that key, never remove them. If another drop-in already narrows that key —
for example, a stricter hand-written allowlist — merging OpenACA's own
generated list into it widens the effective allowlist beyond what that other
drop-in states, even though OpenACA's own policy document only adds
restrictions relative to itself. Before writing, `compile` reads the target
host's existing file-based configuration, excluding any prior OpenACA output.
If a generated key already appears in another file, compilation reports the
collision and does not write the artifact; it must not merge speculatively
into a key another file already governs. A compilation that reports a
collision produces no artifact and therefore has no expected policy to verify.
An administrator resolves the collision by consolidating the key into one
file.

For Claude, `/status` reports the selected managed source and `claude doctor`
reports generated settings that Claude dropped. An observed source alone is not
a full dump of Claude's effective configuration. A consumer may show a full
read-only effective policy only when its host adapter can read the selected
source and apply that source's documented merge rules; otherwise it shows the
expected policy and the observed status separately.

The exact emitted keys and their verification tests belong with the Claude
compiler implementation. They must be checked against the current Claude Code
managed-settings documentation before release.

## Policy evaluation

Compilation happens in two passes.

**Admission pass.** For every explicit `allowed`/`blocked` entry in the policy
document, and for each category `default`, the compiler emits the
corresponding host-native restriction directly from the policy document. This
pass does not depend on endpoint discovery: a blocked MCP command that is not
yet installed still becomes a host blocklist entry, a blocked marketplace
still becomes a prospective source restriction, and a `default: blocked`
category still becomes a managed-only lock, whether or not any matching
component is currently discovered. This is what lets a policy precede an
endpoint inventory (see Admission) and lets marketplace restrictions stay
prospective (see Claude Code target).

When the target host cannot express a given admission entry, the admission
pass reports `not_enforceable` for that entry instead of inventing an
unsupported restriction. For example, Claude has no managed plugin allowlist,
so a plugin policy with `default: blocked` and allowed-plugin exceptions is
`not_enforceable` for the `default` and for each `allowed` entry, even though
explicit `blocked` plugin entries remain enforceable through managed plugin
disablement (see Claude Code target).

**Discovery pass.** For each component the scan discovers, the evaluator
applies the following order:

1. Resolve its admission category and exact source target, when one exists.
2. Apply the category’s explicit list or default (from the admission pass).
3. Evaluate configured risk gates against the fresh scan findings.
4. If a risk gate matches, change the result to blocked.
5. Map the result to a host-native target. If no exact target exists, preserve
   the finding and report it as not enforceable.

A risk gate can only add a restriction to a component the compiler has
discovered: unlike an explicit admission entry, risk evidence is inherently
scan-specific. The discovery pass never removes or loosens a restriction the
admission pass already emitted; it can only add narrower blocks on top of it.

Risk gates only add restrictions. No policy result grants access beyond the
host’s own configuration. The evaluator uses Agent BOM occurrences and graph
containment as evidence, but policies never attach to `bom-ref` or
`openaca:identity`: both are observations that may change with scans or be
absent. The durable policy target is the source/configuration value the host
can itself recognize.

The host adapter retains each discovered component’s native configuration
target alongside its `bom-ref`. A risk finding first identifies an occurrence.
If that occurrence belongs to a plugin, the owning plugin is always the target;
plugin containment takes precedence over a child MCP command or URL. Otherwise
the compiler uses the occurrence’s observed command, URL, plugin identifier, or
marketplace source. If any step lacks an exact target, the result is
`not_enforceable`; the compiler must not guess from identity, name, or a
normalized equivalent. A plugin block suppresses all generated child-level
allow output for that plugin, even if an individual child also appears in an
admission allowlist.

## Deferred

- Per-user, repository, environment, or group policies.
- Individual standalone-skill rules.
- Content digests, signatures, globs, regular expressions, and arbitrary
  component selectors.
- Approvals, temporary exceptions, and expiry.
- Hooks and invocation-time policy decisions.
- Automatic installation of generated settings.
- Host compilers beyond Claude Code.
