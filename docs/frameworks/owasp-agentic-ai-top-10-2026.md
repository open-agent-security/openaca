# OWASP Agentic AI Top 10 2026

Sources:
- https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/

Accessed: 2026-05-13

Use this as ASVE's broad agent-system taxonomy. Prefer it for risks that
depend on autonomy, tool use, delegated identity, memory, multi-step
workflow, inter-agent communication, or human trust in agent output.

The OWASP guide frames agentic risks across inputs, integration and
processing, and outputs. ASVE overlays should map to ASI when the
agentic behavior changes the security meaning of an underlying
vulnerability or malicious component.

## `asi01` — Agent Goal Hijack

Definition: Attacker-controlled content changes the agent's objective,
task selection, planning path, or decision flow. The key distinction
from ordinary prompt injection is agentic impact: multi-step behavior,
tool calls, delegated actions, or goal drift change what the agent tries
to accomplish.

Use when:
- Hidden instructions in documents, web pages, emails, tool outputs,
  RAG content, templates, or peer-agent messages redirect goals or
  planning.
- The attack causes the agent to pursue a different user intent,
  authorize a goal shift, or select unsafe actions.
- Prompt injection affects agent planning or autonomous action, not only
  a single model response.

Do not use when:
- The issue is persistent memory corruption; consider `asi06`.
- The issue is an agent becoming misaligned without active attacker
  steering; consider `asi10`.
- The record only says "prompt injection" with no agent goal, plan, or
  tool behavior.

Evidence patterns:
- "goal", "intent", "prompt injection", "hidden instruction",
  "indirect prompt", "RAG", "web content", "email", "calendar",
  "document", "override", "planner", "goal drift".

Related mappings:
- `llm01:2025`
- `mcp03:2025` when MCP tool metadata carries the injection.
- `mcp10:2025` when unsafe context injection is the path.
- `AML.T0051.*`

## `asi02` — Tool Misuse and Exploitation

Definition: The agent uses a legitimate tool in an unsafe, excessive, or
unintended way while staying within its granted capability. The weakness
is tool choice, tool chaining, argument forwarding, or missing guardrails
around legitimate tool use.

Use when:
- Prompt injection, misleading tool output, or ambiguous instruction
  causes unsafe invocation of legitimate tools.
- Tool access is over-scoped for the task, but the agent acts within
  already granted authority.
- The attack chains tools, over-invokes costly APIs, exfiltrates through
  allowed egress, or drives destructive operations without separate
  privilege escalation.

Do not use when:
- The tool use depends on delegated identity/credential abuse; consider
  `asi03`.
- The tool path reaches arbitrary code/command execution; include or
  prefer `asi05`.
- The tool implementation or package itself is malicious; consider
  `asi04`.

Evidence patterns:
- "tool misuse", "tool chaining", "over-scoped tool", "unsafe tool",
  "approved tool", "DNS exfiltration", "cost spike", "loop", "delete",
  "send", "refund", "external transfer", "tool pivot".

Related mappings:
- `llm06:2025`
- `mcp02:2025` for scope creep.
- `mcp03:2025` for tool metadata poisoning.
- `mcp05:2025` when misuse becomes command execution.

## `asi03` — Identity and Privilege Abuse

Definition: Agent identity, delegated credentials, trust relationships,
or role inheritance are abused to bypass authorization. The agent may
act as a confused deputy or carry cached privileges into a weaker
context.

Use when:
- Agents inherit excessive privileges, reuse cached credentials, or
  share identity across users/sessions/tasks.
- Cross-agent trust lets a low-privilege actor route requests through a
  high-privilege agent.
- Authorization is checked at workflow start but drifts before
  execution.
- Agent descriptors, cards, or personas are forged to gain trust.

Do not use when:
- The agent merely uses a legitimate overbroad tool without identity or
  credential abuse; consider `asi02`.
- A secret is exposed but not used for agent privilege abuse; consider
  `mcp01:2025` or `llm02:2025`.

Evidence patterns:
- "delegated", "credential", "OAuth", "token", "privilege",
  "inheritance", "confused deputy", "agent identity", "agent card",
  "persona", "TOCTOU", "authorization drift", "device code",
  "cross-agent trust".

Related mappings:
- `mcp01:2025`
- `mcp02:2025`
- `mcp07:2025`
- `llm02:2025`
- `llm06:2025`

## `asi04` — Agentic Supply Chain Vulnerabilities

Definition: Agent components, tools, plugins, MCP servers, models,
prompts, datasets, agent descriptors, registries, dependencies, or
update channels are malicious, compromised, tampered, impersonated, or
loaded from untrusted sources.

Use when:
- A package, plugin, MCP server, model/tool registry, prompt template,
  agent card, or dependency in the agent composition chain is malicious
  or compromised.
- Typosquatting, impersonation, dependency confusion, poisoned updates,
  compromised registries, or dynamic runtime loading affect agent
  behavior.
- The component can insert hidden instructions, backdoors, unsafe tools,
  or deceptive behavior into the agent execution chain.

Do not use when:
- The vulnerable package is ordinary software with no agent-stack role.
- The issue is only command injection in a legitimate component; include
  `asi05` and use `asi04` only if supply-chain compromise is also part
  of the evidence.

Evidence patterns:
- "supply chain", "malicious package", "typosquat", "impersonating",
  "plugin", "MCP server", "registry", "agent card", "tool descriptor",
  "poisoned prompt", "dependency confusion", "compromised update",
  "backdoor", "untrusted source".

Related mappings:
- `llm03:2025`
- `mcp04:2025`
- `ast02:2026` when a skill distribution path is involved.
- `AML.T0010.001`

## `asi05` — Unexpected Code Execution

Definition: Agent workflows reach unintended code, command, template,
interpreter, deserialization, package-install, or sandbox execution. The
execution may be generated by the agent, supplied by attacker content, or
triggered through tool chains.

Use when:
- Prompt/tool input reaches shell, subprocess, eval, template, dynamic
  import, package manager, deserialization, or generated-code execution.
- The record describes RCE, arbitrary code execution, command injection,
  unsafe code generation, unsafe object deserialization, memory-system
  eval, or hostile install/import execution.
- A multi-tool chain converts otherwise legitimate operations into code
  execution.

Do not use when:
- The package is malicious but the record only shows supply-chain
  delivery and no execution path; prefer `asi04`.
- The issue is unsafe use of a legitimate tool without an execution
  sink; consider `asi02`.

Evidence patterns:
- "RCE", "remote code execution", "arbitrary code", "command
  injection", "shell", "subprocess", "eval", "deserialization",
  "template injection", "install", "import", "lockfile poisoning",
  "generated code", "sandbox escape".

Related mappings:
- `llm05:2025`
- `llm01:2025` when prompt injection triggers execution.
- `mcp05:2025`
- `ast05:2026` when unsafe skill loading/deserialization is the path.

## `asi06` — Memory and Context Poisoning

Definition: Stored memory, summaries, embeddings, RAG data, session
state, shared context, or long-term knowledge is poisoned so future
agent reasoning, planning, or tool use becomes unsafe.

Use when:
- Malicious data enters memory, vector stores, RAG sources, summaries,
  uploaded files, API feeds, shared context, or peer-agent exchanges and
  persists across turns/sessions.
- The poisoned context later influences tool selection, goal
  interpretation, data disclosure, or code generation.
- Context poisoning is gradual, persistent, or shared across agents/users.

Do not use when:
- The manipulation is one-shot prompt injection with no persistence;
  consider `asi01`.
- The effect is primarily a downstream failure propagation after
  poisoning; consider adding `asi08`.

Evidence patterns:
- "memory", "context", "RAG", "embedding", "vector", "retrieval",
  "summary", "persistent", "shared context", "long-term memory",
  "poisoning", "stored", "drift".

Related mappings:
- `llm01:2025`
- `llm04:2025`
- `llm08:2025`
- `mcp10:2025`

## `asi07` — Insecure Inter-Agent Communication

Definition: Agent-to-agent communication lacks authentication,
integrity, authorization, origin validation, or trust boundaries. A
forged, compromised, or malicious peer agent can alter messages,
impersonate another agent, poison shared communication, or relay unsafe
instructions.

Use when:
- A2A, peer-agent, swarm, delegation, or multi-agent protocol messages
  are spoofed, replayed, tampered, or trusted without verification.
- Agent cards/descriptors advertise false identity or capabilities.
- One agent relays malicious instructions or sensitive data to another
  due to weak communication controls.

Do not use when:
- The issue is a single-agent tool misuse with no inter-agent channel.
- The issue is normal MCP client/server invocation rather than
  agent-to-agent trust; use MCP taxonomy where clearer.

Evidence patterns:
- "agent-to-agent", "A2A", "peer agent", "agent card", "swarm",
  "delegation", "message spoofing", "replay", "mTLS", "attestation",
  "signed messages", "routing", "inter-agent".

Related mappings:
- `llm02:2025`
- `llm06:2025`
- `asi03` when identity/privilege is abused.
- `asi10` when the peer is a rogue agent.

## `asi08` — Cascading Failures

Definition: A localized error, hallucination, poisoned context, unsafe
tool action, or compromised agent propagates through multi-step
workflows, tool chains, or connected agents, amplifying impact beyond
the original failure.

Use when:
- The record shows a chain reaction across agents, tools, memory,
  services, CI/CD, cloud resources, or tenants.
- A poisoned output is trusted by downstream agents/systems and causes
  repeated, amplified, or compounding actions.
- Failures become difficult to trace, repudiate, or stop because of
  autonomy, parallelism, or poor observability.

Do not use when:
- The impact remains local to one component and no propagation is
  described.
- The finding is merely high severity; cascading requires spread or
  amplification.

Evidence patterns:
- "cascade", "chain reaction", "downstream", "propagation", "multi-step",
  "parallel agents", "tenant", "CI/CD", "cloud infra", "blast radius",
  "untraceable", "repudiation".

Related mappings:
- `llm06:2025`
- `llm04:2025`
- `asi06` when poisoned memory/context starts the cascade.
- `mcp08:2025` when missing telemetry impairs response.

## `asi09` — Human-Agent Trust Exploitation

Definition: Attackers exploit human over-trust in agent outputs,
approvals, explanations, or apparent competence. The agent persuades,
conceals, misrepresents, or overwhelms the human in the loop.

Use when:
- The record describes false or misleading agent output causing a human
  to approve, deploy, publish, transfer, or trust unsafe actions.
- The agent fabricates results, hides mistakes, presents unsafe diffs as
  safe, or generates convincing but malicious recommendations.
- Human approval exists but is made ineffective by framing, volume,
  fatigue, or deception.

Do not use when:
- The agent directly executes the harmful action without relying on
  human trust.
- The issue is ordinary misinformation without security impact.

Evidence patterns:
- "human approval", "HITL", "trust", "misleading", "fraudulent",
  "hallucinated", "approval fatigue", "dry-run", "diff", "recommend",
  "convince", "hide mistake".

Related mappings:
- `llm09:2025`
- `llm05:2025`
- `asi01` when the human is influenced after goal hijack.

## `asi10` — Rogue Agents

Definition: An agent operates outside intended boundaries while
appearing legitimate. Rogue behavior may arise from compromise,
misalignment, collusion, self-replication, unauthorized provisioning, or
behavioral drift without direct per-action attacker control.

Use when:
- An agent persistently acts against owner intent, evades monitoring,
  spawns unauthorized replicas, colludes with peers, or maintains
  unauthorized access.
- A malicious or compromised agent advertises legitimate capabilities and
  participates in workflows as if trusted.
- The central risk is behavioral integrity of the agent itself.

Do not use when:
- A normal agent is briefly hijacked by prompt injection; consider
  `asi01`.
- The issue is a malicious package or component before it becomes an
  autonomous agent participant; consider `asi04`.

Evidence patterns:
- "rogue agent", "self-replication", "unauthorized replica",
  "persistence", "collusion", "behavioral drift", "misaligned",
  "quarantine", "kill switch", "attestation", "watchdog", "agent card".

Related mappings:
- `llm02:2025`
- `llm09:2025`
- `asi07` when inter-agent trust is abused.
- `AML.T0074` when the agent masquerades as legitimate.

## General Mapping Notes

- `asi04` and `asi05` often co-occur for malicious agent-stack packages:
  `asi04` describes compromised composition; `asi05` describes the
  execution sink.
- Do not use `asi04` for every vulnerable dependency. The component must
  be part of the agent stack or agent-reachable composition path.
- `asi01`, `asi06`, and `asi10` should not be conflated:
  - `asi01`: active steering of goals/plans.
  - `asi06`: persistent poisoning of memory/context.
  - `asi10`: rogue or misaligned agent behavior.
- `asi02`, `asi03`, and `asi05` should be separated:
  - `asi02`: unsafe use of legitimate already-authorized tools.
  - `asi03`: identity, privilege, or delegation abuse.
  - `asi05`: code/command execution sink.
- The OWASP guide maps ASI entries to LLM Top 10 categories. Use those
  cross-mappings as hints, but only include LLM taxonomy when the record
  has LLM/GenAI relevance beyond the agent-specific mapping.
