# OWASP MCP Top 10 2025

Sources:
- https://owasp.org/www-project-mcp-top-10/
- https://github.com/OWASP/www-project-mcp-top-10/tree/main/2025
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP01-2025-Token-Mismanagement-and-Secret-Exposure.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP02-2025%E2%80%93Privilege-Escalation-via-Scope-Creep.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP03-2025%E2%80%93Tool-Poisoning.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP04-2025%E2%80%93Software-Supply-Chain-Attacks%26Dependency-Tampering.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP05-2025%E2%80%93Command-Injection%26Execution.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP06-2025%E2%80%93Intent-Flow-Subversion.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP07-2025%E2%80%93Insufficient-Authentication%26Authorization.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP08-2025%E2%80%93Lack-of-Audit-and-Telemetry.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP09-2025%E2%80%93Shadow-MCP-Servers.md
- https://github.com/OWASP/www-project-mcp-top-10/blob/main/2025/MCP10-2025%E2%80%93ContextInjection%26OverSharing.md

Accessed: 2026-05-13

Use this for Model Context Protocol-specific risk. Prefer it when the
record concerns MCP servers, MCP clients, MCP manifests, MCP transport,
tool descriptions, tool schemas, MCP registries, or MCP dependency
distribution.

## `mcp01:2025` — Token Mismanagement and Secret Exposure

Definition: MCP servers, clients, logs, traces, prompts, context, or
tool outputs expose credentials, API tokens, session identifiers, OAuth
tokens, cloud secrets, repository tokens, or other reusable secrets.

Use when:
- The record shows secrets flowing through MCP prompts, context, tool
  outputs, logs, debug traces, memory, or transport messages.
- A vulnerable MCP component leaks credentials to an attacker-controlled
  endpoint or another tenant/session.
- MCP servers store or forward tokens without isolation, redaction,
  rotation, or scope limits.

Do not use when:
- The record only says data or host information was exfiltrated, but not
  credentials, tokens, or secrets.
- The issue is unauthenticated access without secret exposure; consider
  `mcp07:2025`.

Evidence patterns:
- "token", "secret", "credential", "API key", "OAuth", "bearer",
  "session", "environment variable", "logs", "trace", "redaction",
  "exfiltrate credentials".

Related mappings:
- `asi03` for delegated identity or privilege abuse.
- `llm02:2025` for sensitive information disclosure.
- `asi04` when secret theft is delivered through compromised MCP
  dependencies.

## `mcp02:2025` — Privilege Escalation via Scope Creep

Definition: MCP permissions, scopes, tool capabilities, or delegated
identities expand beyond the user's intent over time or across
contexts, allowing the agent or tool to perform more powerful actions
than originally authorized.

Use when:
- The record describes overly broad scopes, missing least privilege,
  inherited privileges, confused-deputy behavior, or privilege escalation
  through tool chaining.
- A low-risk MCP tool can indirectly reach a high-risk action through
  another tool, schema, or delegated credential.
- Permissions are granted once and reused in contexts where they no
  longer match user intent.

Do not use when:
- The problem is simply missing authentication; use `mcp07:2025`.
- A command injection vulnerability executes with the process's existing
  privileges but there is no separate scope expansion.

Evidence patterns:
- "scope", "privilege escalation", "least privilege", "permission",
  "delegated", "confused deputy", "tool chaining", "excessive access",
  "admin", "write scope".

Related mappings:
- `asi03`
- `asi02` when legitimate tools are misused after scope expansion.
- `llm06:2025` for excessive agency.

## `mcp03:2025` — Tool Poisoning

Definition: MCP tool descriptions, schemas, manifests, metadata, or
contracts are poisoned so a legitimate agent calls tools with a
different semantic effect than intended. The attacker changes what the
agent believes a tool does, rather than exploiting a traditional code
bug.

Use when:
- Tool descriptions, schemas, manifests, or registry metadata contain
  hidden instructions, prompt injection, misleading descriptions, or
  semantic remapping.
- A schema/manifests supply-chain change causes benign workflows to call
  destructive or unauthorized operations.
- Agents accept runtime schema/tool changes without provenance,
  signatures, review, or semantic invariants.

Do not use when:
- The tool implementation has command injection but the schema and
  description are honest; use `mcp05:2025`.
- The attack is ordinary malicious package delivery with no poisoned
  MCP metadata; use `mcp04:2025`.

Evidence patterns:
- "tool poisoning", "schema poisoning", "tool description", "manifest",
  "schema", "metadata", "hidden instruction", "prompt injection",
  "semantic remapping", "archive maps to DELETE", "registry write".

Related mappings:
- `asi01` when goal hijack is the agent-level effect.
- `asi02` when the poisoned tool causes unsafe legitimate tool use.
- `llm01:2025` for prompt injection.
- `AML.T0051.001` for indirect prompt injection through tool metadata.

## `mcp04:2025` — Software Supply Chain Attacks and Dependency Tampering

Definition: MCP servers, plugins, SDKs, connectors, protocol libraries,
tool manifests, packages, dependencies, registries, or build pipelines
are malicious or compromised. The component appears legitimate but can
modify agent behavior, backdoor execution, tamper with protocol
semantics, or exfiltrate context.

Use when:
- The evidence describes a malicious MCP-looking npm/PyPI package,
  typosquat, dependency confusion, compromised maintainer, malicious
  update, poisoned transitive dependency, registry compromise, or build
  pipeline tampering.
- The package presents as MCP infrastructure, an MCP server/client, MCP
  plugin, connector, SDK, or dependency used by such a component.
- The record mentions unsigned components, floating versions, missing
  provenance, unpinned dependencies, or runtime/build-time package fetch.

Do not use when:
- The component is merely a normal software dependency with a CVE and no
  MCP role.
- The issue is command injection in a legitimate MCP server; use
  `mcp05:2025` unless dependency tampering is also present.
- The only evidence is the substring `mcp` in an unrelated package name.

Evidence patterns:
- "malicious package", "typosquat", "dependency confusion",
  "trojanized", "compromised registry", "package registry",
  "maintainer", "plugin", "connector", "SDK", "manifest", "latest",
  "floating version", "hash", "signature", "attestation".

Related mappings:
- `asi04`
- `llm03:2025`
- `ast02:2026` only when a skill distribution path is involved.
- `AML.T0010.001` when the package is AI/agent software.

## `mcp05:2025` — Command Injection and Execution

Definition: An MCP server or tool passes untrusted input into shell,
subprocess, interpreter, template, eval, file path, build, package
manager, or command execution paths. The agent may trigger the sink
through normal tool invocation.

Use when:
- Tool arguments, repository paths, prompt-controlled values, filenames,
  URLs, branch names, environment variables, or config fields reach
  command/code execution.
- The record describes shell injection, OS command injection, RCE,
  arbitrary code execution, template injection, eval, unsafe subprocess,
  or install/import execution in an MCP runtime path.
- The vulnerable package is an MCP server/client/tool and the exploit is
  execution through MCP-facing behavior.

Do not use when:
- A malicious package runs code during install but no MCP tool input or
  MCP runtime command path is involved; prefer `mcp04:2025`.
- The issue is tool metadata poisoning without an execution sink; use
  `mcp03:2025`.

Evidence patterns:
- "command injection", "OS command", "shell", "subprocess", "exec",
  "eval", "RCE", "arbitrary code", "template injection", "tool
  argument", "repository path", "filename", "branch", "URL".

Related mappings:
- `asi05`
- `asi02` when a legitimate MCP tool is driven into unsafe use.
- `llm05:2025` when LLM output reaches a command/code sink.

## `mcp06:2025` — Intent Flow Subversion

Definition: MCP interactions change the user's intended workflow,
planning flow, or approval path. The attack steers an agent through a
sequence that appears valid locally but subverts the higher-level goal.

Use when:
- The record describes manipulation of task plans, action order,
  approval checkpoints, tool-choice flow, or multi-step workflows.
- A tool response causes the agent to skip validation, change goals,
  escalate from read-only to write/destructive actions, or perform a
  different task than the user requested.
- The exploit depends on agent planning over several MCP interactions,
  not just one vulnerable function.

Do not use when:
- The issue is single-step command execution or data disclosure without
  workflow/intent manipulation.
- The issue is hidden instruction in tool metadata; use `mcp03:2025`
  unless the record also shows flow subversion.

Evidence patterns:
- "intent", "workflow", "plan", "approval", "task", "goal", "sequence",
  "multi-step", "skip validation", "read-only to write", "tool choice",
  "reroute".

Related mappings:
- `asi01`
- `asi02`
- `asi08` when subverted workflow causes cascading failure.

## `mcp07:2025` — Insufficient Authentication and Authorization

Definition: MCP servers, endpoints, tools, transports, or admin/control
surfaces lack authentication, enforce authorization incorrectly, or allow
unauthorized users/agents to invoke sensitive tools.

Use when:
- An MCP endpoint is unauthenticated, weakly authenticated, exposed on a
  network interface, lacks per-tool authorization, or accepts requests
  from untrusted origins.
- A user/session/tenant can invoke another user's tools or access another
  tenant's context.
- Authorization decisions are made only at connection setup while tool
  calls need finer-grained checks.

Do not use when:
- The issue is broad legitimate scope granted to an authenticated agent;
  consider `mcp02:2025`.
- The issue is token leakage; use `mcp01:2025`.

Evidence patterns:
- "unauthenticated", "authentication bypass", "authorization bypass",
  "missing auth", "no auth", "exposed endpoint", "public", "tenant",
  "session", "origin", "per-tool authorization", "admin".

Related mappings:
- `asi03`
- `asi02` when unauthorized access drives tool misuse.
- `llm06:2025` if unauthenticated agent agency is excessive.

## `mcp08:2025` — Lack of Audit and Telemetry

Definition: MCP activity is security-relevant but not observable,
attributable, or reconstructable. Logs, traces, schema hashes, tool
versions, caller identity, provenance, or approval decisions are absent
or insufficient for detection and response.

Use when:
- The record is about missing logs, missing schema/tool provenance,
  inability to attribute tool calls, insufficient audit trail, missing
  telemetry for installed MCP servers, or lack of forensic visibility.
- Detection depends on hash/signature changes, unknown outbound domains,
  unauthorized schema/config diffs, or sudden behavior drift but the
  system lacks telemetry.

Do not use when:
- A vulnerability would benefit from logging but the record does not
  identify audit/telemetry as part of the weakness.
- The finding is a normal package CVE with no MCP observability angle.

Evidence patterns:
- "audit", "telemetry", "logs", "trace", "forensic", "attribution",
  "schema hash", "provenance", "unknown domain", "behavior drift",
  "inventory", "config diff".

Related mappings:
- `asi08` when missing telemetry lets failures propagate.
- `ast09:2026` for skill governance/inventory gaps.

## `mcp09:2025` — Shadow MCP Servers

Definition: Unmanaged or unauthorized MCP servers are installed,
configured, exposed, or reachable without security review. They create a
parallel tool surface that may bypass central policy, inventory,
approval, logging, or patch management.

Use when:
- The record involves unknown MCP servers, local developer-installed MCP
  configs, unmanaged plugins/connectors, unauthorized endpoints, or
  servers outside fleet inventory.
- A vulnerable MCP component is reachable because a user/project
  configuration introduced it without governance.
- The risk is discovery/inventory/control-plane bypass rather than the
  intrinsic bug alone.

Do not use when:
- The component is known and managed but vulnerable; map the underlying
  weakness instead.
- The finding is a malicious package candidate with no evidence of
  unmanaged deployment.

Evidence patterns:
- "shadow", "unmanaged", "unauthorized", "unknown server", ".mcp.json",
  "developer config", "local config", "inventory", "approved list",
  "rogue server", "personal token".

Related mappings:
- `asi04` for unreviewed component supply chain.
- `ast09:2026` for governance gaps in skill inventory.
- `llm06:2025` when unmanaged tools expand agency.

## `mcp10:2025` — Context Injection and Over-Sharing

Definition: MCP tools inject unsafe content into the model context or
expose more context than the tool, user, or task requires. The weakness
may be excessive repository/file access, prompt/context injection,
retrieval leakage, or tool output that becomes trusted context.

Use when:
- MCP tool output, resource content, or retrieved data injects
  instructions or misleading context into the agent.
- An MCP server exposes repository files, secrets-adjacent data, prompts,
  memory, tenant context, or broad filesystem/project context beyond the
  user's intent.
- Path traversal/file disclosure through an MCP server results in agent
  context exposure or repository read impact.

Do not use when:
- The issue is token leakage; use `mcp01:2025`.
- The issue is tool metadata poisoning; use `mcp03:2025`.
- The finding is ordinary file disclosure outside an MCP/agent-context
  path.

Evidence patterns:
- "context injection", "over-sharing", "tool output", "resource",
  "repository context", "file disclosure", "path traversal", "prompt",
  "memory", "tenant context", "retrieval", "RAG", "untrusted content".

Related mappings:
- `asi01` when injected context changes agent goals.
- `asi06` when persistent memory/context is poisoned.
- `llm01:2025` for prompt injection.
- `llm02:2025` for sensitive information disclosure.

## General Mapping Notes

- A malicious PyPI/npm package with an MCP-looking name usually maps to
  `mcp04:2025`, `asi04`, and possibly `llm03:2025`.
- Add `mcp05:2025` only when the record describes command/code execution
  through MCP-facing input, not merely package install/import behavior.
- Add `mcp03:2025` only when MCP schemas, manifests, tool descriptions,
  or tool metadata are poisoned or semantically misleading.
- Add `mcp10:2025` for MCP path traversal/file disclosure when the
  agent-specific impact is excessive context exposure or unsafe context
  injection.
- Do not use MCP taxonomy solely because a package name contains `mcp`;
  the package should present as an MCP component or affect MCP
  composition/runtime.
