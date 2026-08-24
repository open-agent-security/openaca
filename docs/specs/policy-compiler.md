# Policy Compiler

Status: proposed

## Goal

Let an administrator describe which agent components may be used and which
known risks must block them, then turn that policy into a host-managed
configuration file.

The same policy file works locally and with a hosted control plane. OpenACA
owns the policy schema, validation, scan-backed evaluation, and host compiler.
It does not own a long-running enforcement service.

The first compiler target is Claude Code. Other hosts may add compilers only
when OpenACA can produce a precise host-native restriction and verify it.

## Non-goals

The first version does not provide a general rule language, policy scopes,
per-component approvals, exceptions, expiry, hooks, a daemon, or runtime
interception. It does not delete or rewrite agent configuration.

## Policy document

A policy document is YAML or JSON. YAML is the convenient form for a local
file; both encodings represent the same document.

```yaml
version: 1

admission:
  mcp_servers:
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

  standalone_skills:
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
| `mcp_servers` | One exact command array or URL | `allowed` or `blocked` |
| `plugins` | One exact `plugin@marketplace` identifier or canonical marketplace source | `allowed` or `blocked` |
| `standalone_skills` | None in V1 | `allowed` or `blocked` |

Each MCP server and plugin category has an optional `allowed` list, optional
`blocked` list, and required `default`. A target must appear in at most one
list. Validation rejects an overlap rather than assigning surprising
precedence. A component not in either list follows `default`.

`standalone_skills` deliberately has only `default`. Current hosts do not
offer a portable, managed way to block or allow individual standalone skills;
claiming otherwise would make the policy look more precise than it is.

All targets are exact source/configuration values. V1 accepts no component
identity, BOM reference, digest, glob, regular expression, or display-name
match. A policy can therefore be written before the component appears in an
endpoint inventory.

A command target is a non-empty array of literal strings. It matches the full
command and argument array token by token, in order, with case-sensitive string
comparison. The compiler does not resolve executable paths, canonicalize
equivalent flags, or expand environment variables in policy values. The same
array becomes the host-native restriction, so a policy author can see exactly
what is being matched.

A marketplace entry is a plugin admission target, not a fourth component
category. Allowing a marketplace permits the plugins obtained from that
marketplace; it does not separately trust selected contents of a plugin. A
plugin remains the trust boundary for its bundled MCP servers, skills, and
other contents. This policy does not define marketplace trust for standalone
MCP servers or skills.

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

When a finding is on a component contained by a plugin, the compiler blocks the
owning plugin. It must not attempt to allow a plugin while blocking one of its
bundled contents. When a finding is on an MCP server, the compiler blocks that
exact MCP server. A finding on a standalone skill is reported as not
enforceable unless the selected host can express the category-wide skill
restriction.

## Compile

```text
openaca policy validate policy.yaml
openaca policy compile policy.yaml --target ~/.claude --host claude --output managed-settings.json
```

`validate` checks the document only. It exits `0` when valid and `1` when
invalid, writes diagnostics to stderr, and has no success output.

`compile` always validates, scans the supplied target, obtains the advisory
data needed by configured risk gates, evaluates the policy, and writes a
host-specific artifact. There is no separate public `check` command: an
endpoint scan is the evidence-gathering operation.

If no risk gates are configured, compilation does not require advisory
queries. If risk gates are configured and required advisory data cannot be
obtained or evaluated, compilation fails and does not replace a previous
artifact. A fresh admission result must not be combined with stale risk data.
An unidentified or non-queryable component is not evidence that it is clean.

The compiler emits a concise report with the policy result for every discovered
component and, for matches that cannot be represented by the host, an explicit
`not_enforceable` result. Successful compilation never implies that every
matching risk was blocked.

The compiler writes an artifact but does not install it into a protected host
configuration location. A local administrator or device-management tool owns
that final atomic write.

## Claude Code target

The Claude compiler emits a system-managed settings drop-in, intended for a
dedicated file such as `managed-settings.d/50-openaca-policy.json`. It must not
overwrite an organization’s other managed settings.

For this target, the compiler can express:

| Policy item | Claude output |
|---|---|
| MCP admission | exact managed MCP allow/block settings, with the managed-only lock when default is blocked |
| Plugin block | managed plugin disablement for an exact `plugin@marketplace` ID |
| Marketplace admission | managed source restriction for marketplace add, install, update, and refresh |
| Standalone skill block | `strictPluginOnlyCustomization` restricted to direct user/project skills |

Claude does not document a managed plugin allowlist that prevents every
unlisted installed plugin from running. Therefore a plugin policy with
`default: blocked` and allowed-plugin exceptions is evaluated but reported as
`not_enforceable` for Claude V1. Marketplace source restrictions are likewise
prospective controls: they govern marketplace add, install, update, and refresh,
not a claim that an already-installed plugin is disabled. Exact plugin blocks
remain enforceable through managed plugin disablement.

The generated report records the intended artifact path and the limitations of
each result. A file-based drop-in is not a supported enforcement path when a
higher-priority managed Claude configuration source controls the same settings;
that state is reported as a source conflict, not as enforcement.

The exact emitted keys and their verification tests belong with the Claude
compiler implementation. They must be checked against the current Claude Code
managed-settings documentation before release.

## Policy evaluation

For each discovered component, the evaluator applies the following order:

1. Resolve its admission category and exact source target, when one exists.
2. Apply the category’s explicit list or default.
3. Evaluate configured risk gates against the fresh scan findings.
4. If a risk gate matches, change the result to blocked.
5. Map the result to a host-native target. If no exact target exists, preserve
   the finding and report it as not enforceable.

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
normalized equivalent.

## Deferred

- Per-user, repository, environment, or group policies.
- Individual standalone-skill rules.
- Content digests, signatures, globs, regular expressions, and arbitrary
  component selectors.
- Approvals, temporary exceptions, and expiry.
- Hooks and invocation-time policy decisions.
- Automatic installation of generated settings.
- Host compilers beyond Claude Code.
