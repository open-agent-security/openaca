# OWASP Top 10 for LLM Applications 2025

Sources:
- https://genai.owasp.org/llm-top-10/
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/tree/main/2_0_vulns
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM01_PromptInjection.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM02_SensitiveInformationDisclosure.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM03_SupplyChain.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM04_DataModelPoisoning.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM05_ImproperOutputHandling.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM07_SystemPromptLeakage.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM08_VectorAndEmbeddingWeaknesses.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM09_Misinformation.md
- https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM10_UnboundedConsumption.md

Accessed: 2026-05-13

Use this when the record affects an LLM application or GenAI supply
chain beyond agent-specific behavior. In ASVE overlays, OWASP Agentic,
MCP, and Agentic Skills mappings are often more specific; LLM mappings
are useful for cross-framework interoperability when the LLM application
dimension is present.

## `llm01:2025` — Prompt Injection

Definition: User-controlled or externally sourced content changes model
behavior or output in unintended ways. This includes direct prompts,
indirect instructions from files/web/RAG/email, multimodal hidden
instructions, jailbreaking, payload splitting, multilingual or encoded
instructions, and adversarial suffixes.

Use when:
- The evidence shows direct or indirect prompt content changing LLM,
  agent, tool, or decision behavior.
- Web pages, documents, images, emails, resumes, repository content, RAG
  records, or tool output contain instructions interpreted by the model.
- The injected content causes unauthorized access, sensitive information
  disclosure, tool misuse, command execution, or critical decision
  manipulation.

Do not use when:
- The issue is persistent poisoning of training/retrieval data rather
  than prompt-time influence; consider `llm04:2025` or `llm08:2025`.
- The record only involves a malicious package with no prompt/content
  instruction channel.

Evidence patterns:
- "prompt injection", "indirect prompt", "jailbreak", "hidden
  instruction", "ignore previous", "webpage", "document", "RAG",
  "multimodal", "image", "encoded", "Base64", "adversarial suffix".

Related mappings:
- `asi01` when the injection redirects an autonomous agent's goal or plan.
- `mcp03:2025` when the injection is in MCP tool metadata.
- `mcp10:2025` when MCP context injection is the path.
- `AML.T0051.*`

## `llm02:2025` — Sensitive Information Disclosure

Definition: An LLM application reveals confidential data, secrets,
private context, proprietary information, credentials, or internal
system details through model output, tool output, logs, traces, memory,
RAG, or connected services.

Use when:
- The record describes disclosure of credentials, private user data,
  system prompts, proprietary documents, chat history, tenant data, or
  internal infrastructure details.
- Prompt injection, overbroad retrieval, tool misuse, or weak isolation
  causes sensitive information to appear in model or agent output.
- Disclosure occurs through logs, debug traces, telemetry, memory, or
  exported conversations.

Do not use when:
- The exposed content is specifically the system prompt; use
  `llm07:2025`.
- The record only says basic host information was exfiltrated and does
  not show LLM/GenAI involvement.

Evidence patterns:
- "sensitive information", "secret", "credential", "API key", "token",
  "private data", "PII", "tenant", "chat history", "confidential",
  "logs", "trace", "memory", "data exfiltration".

Related mappings:
- `mcp01:2025` for MCP token/secret exposure.
- `mcp10:2025` for MCP context over-sharing.
- `asi03` when delegated identity or credentials are abused.

## `llm03:2025` — Supply Chain

Definition: An LLM application depends on compromised, vulnerable, or
untrusted components across models, datasets, plugins, agents, adapters,
prompts, vector databases, package dependencies, pipelines, services, or
deployment artifacts.

Use when:
- The affected artifact is part of an LLM/agent application supply
  chain: MCP server, plugin, model adapter, prompt template, dataset,
  vector DB component, agent framework, package, connector, or registry.
- The record describes malicious packages, dependency confusion,
  typosquatting, compromised repositories, poisoned updates, model or
  dataset tampering, insecure plugins, or untrusted services.
- A package is used to build, operate, extend, or compose LLM/agent
  behavior.

Do not use when:
- The vulnerable package is ordinary application software with no LLM,
  AI, agent, or model context.
- The record is about runtime command injection in a legitimate
  component with no supply-chain component; consider `llm05:2025`,
  `asi05`, or `mcp05:2025`.

Evidence patterns:
- "supply chain", "malicious package", "plugin", "model", "adapter",
  "dataset", "vector database", "registry", "typosquat", "dependency
  confusion", "poisoned update", "compromised repository", "pipeline".

Related mappings:
- `asi04`
- `mcp04:2025`
- `ast02:2026` when skill delivery is involved.
- `AML.T0010.*`

## `llm04:2025` — Data and Model Poisoning

Definition: Training, fine-tuning, embedding, retrieval, feedback, or
model data is manipulated so model behavior, integrity, safety, or
reliability is degraded or attacker-influenced.

Use when:
- The evidence involves poisoned training data, fine-tuning data,
  alignment data, feedback loops, model weights, embeddings, RAG corpus,
  benchmark/evaluation data, or data labeling.
- Poisoned data changes model behavior, introduces bias/backdoors,
  affects retrieval relevance, or corrupts future decisions.
- A model artifact itself is tampered or poisoned.

Do not use when:
- The attack is prompt-time injection through retrieved content without
  persistence or data-store poisoning; consider `llm01:2025`.
- The issue is vector DB access/control weakness rather than poisoned
  data; consider `llm08:2025`.

Evidence patterns:
- "data poisoning", "model poisoning", "training data", "fine-tuning",
  "feedback", "alignment", "embedding", "RAG corpus", "backdoor",
  "label", "weights", "model artifact", "poisoned dataset".

Related mappings:
- `asi06` for persistent agent memory/context poisoning.
- `llm08:2025` when vectors/embeddings are the affected data layer.
- `AML.T0010.002` or `AML.T0010.003` for data/model supply chain.

## `llm05:2025` — Improper Output Handling

Definition: LLM output is trusted as code, commands, markup, SQL,
configuration, file paths, API calls, or other structured input without
validation, encoding, sandboxing, or policy enforcement before reaching
a downstream sink.

Use when:
- Model output reaches shell, subprocess, SQL, browser/HTML/JS, template
  engines, interpreters, APIs, file writes, infrastructure config, or
  code execution paths.
- The downstream component treats LLM output as authoritative or safe.
- Prompt injection or malicious content drives the model to produce
  output that another system executes or renders unsafely.

Do not use when:
- The vulnerable sink is inside an MCP tool and the exploit does not
  depend on LLM output; use `mcp05:2025` or `asi05`.
- The issue is the LLM choosing to call a powerful tool safely formatted
  but semantically unsafe; consider `llm06:2025` or `asi02`.

Evidence patterns:
- "improper output handling", "unsanitized output", "XSS", "SQL",
  "command", "shell", "template", "eval", "code execution", "rendered",
  "API call", "file write", "generated code".

Related mappings:
- `asi05` for unexpected code execution in agent workflows.
- `mcp05:2025` for MCP command/code execution.
- `llm01:2025` when prompt injection produces the unsafe output.

## `llm06:2025` — Excessive Agency

Definition: An LLM-based system has too much autonomy, permission, tool
access, or action scope relative to the user's intent and lacks
appropriate approvals, constraints, rate limits, or policy gates.

Use when:
- The system can invoke tools, APIs, code execution, financial actions,
  email, browsing, databases, cloud resources, or external services with
  insufficient constraints.
- The record describes destructive, costly, privacy-impacting, or
  high-impact actions caused by broad agency.
- Missing human approval, weak authorization, overbroad scopes, or poor
  action gating is central to the risk.

Do not use when:
- The issue is specifically delegated identity/credential abuse; prefer
  `asi03`.
- The issue is a narrow command injection vulnerability with no agency
  or broad action-scope problem; use `asi05` or `mcp05:2025`.

Evidence patterns:
- "excessive agency", "autonomy", "tool access", "agent", "API",
  "destructive action", "delete", "send", "purchase", "cloud",
  "approval", "rate limit", "scope", "permissions", "human-in-the-loop".

Related mappings:
- `asi02` for unsafe use of legitimate tools.
- `asi03` for identity/privilege abuse.
- `ast03:2026` for over-privileged skills.
- `mcp02:2025`

## `llm07:2025` — System Prompt Leakage

Definition: Hidden system prompts, developer messages, policies,
guardrails, internal instructions, tool descriptions, routing logic, or
configuration are exposed to users or attackers.

Use when:
- The record describes leakage of system prompts, hidden instructions,
  policy text, internal chain/routing instructions, tool configuration,
  model/deployment details, or prompt templates.
- Prompt extraction enables bypassing safeguards, finding hidden
  capabilities, or improving later attacks.

Do not use when:
- The exposed information is user/private data rather than system
  instructions; use `llm02:2025`.
- The issue is hidden malicious instructions being inserted, not leaked;
  consider `llm01:2025`, `mcp03:2025`, or `asi01`.

Evidence patterns:
- "system prompt", "prompt leakage", "developer message", "hidden
  instruction", "policy", "guardrail", "internal prompt", "template",
  "prompt extraction", "routing logic".

Related mappings:
- `llm01:2025` when prompt injection causes or uses the leakage.
- `asi09` when leaked instructions enable human deception.

## `llm08:2025` — Vector and Embedding Weaknesses

Definition: Vector stores, embeddings, retrieval pipelines, similarity
search, chunking, metadata filters, or RAG access controls are weak,
allowing poisoning, leakage, retrieval manipulation, cross-tenant
access, or unsafe context injection.

Use when:
- The evidence involves vector DBs, embeddings, RAG retrieval, chunking,
  metadata filters, nearest-neighbor search, embedding inversion,
  cross-tenant retrieval, or poisoned retrieved context.
- Retrieval returns attacker-controlled or unauthorized content that
  affects LLM output or agent actions.
- Embedding/vector infrastructure exposes sensitive data or enables
  context manipulation.

Do not use when:
- The issue is generic prompt injection with no retrieval/vector
  component; use `llm01:2025`.
- The issue is broader training data poisoning rather than vector/RAG
  layer weakness; consider `llm04:2025`.

Evidence patterns:
- "vector", "embedding", "RAG", "retrieval", "similarity search",
  "chunk", "metadata filter", "nearest neighbor", "cross-tenant",
  "embedding inversion", "retrieved context", "vector database".

Related mappings:
- `asi06`
- `mcp10:2025` for MCP context over-sharing/injection.
- `llm04:2025` when poisoning is persistent in data/model layers.

## `llm09:2025` — Misinformation

Definition: The LLM application produces false, misleading, fabricated,
or unsupported content that users or systems rely on for security,
business, legal, operational, or safety-relevant decisions.

Use when:
- The record describes hallucinated facts, fabricated citations,
  incorrect recommendations, false code/security claims, misleading
  summaries, or unsupported decisions that create security impact.
- Human users or downstream systems trust the output and act on it.
- The issue is reliability of generated information, not primarily
  unauthorized tool use or code execution.

Do not use when:
- The agent is actively hijacked by attacker instructions; consider
  `llm01:2025` or `asi01`.
- The false output is incidental and not security-relevant.

Evidence patterns:
- "misinformation", "hallucination", "fabricated", "false citation",
  "incorrect", "misleading", "unsupported", "recommendation",
  "business decision", "security decision".

Related mappings:
- `asi09` for human-agent trust exploitation.
- `asi10` if misinformation is part of rogue autonomous behavior.

## `llm10:2025` — Unbounded Consumption

Definition: LLM or agent workflows consume uncontrolled compute, tokens,
API calls, memory, storage, network, cost, or time due to missing quotas,
rate limits, recursion controls, loop detection, or budget constraints.

Use when:
- The record describes token/cost exhaustion, infinite loops, recursive
  tool calls, repeated generation, API abuse, resource exhaustion,
  denial of service, or billing spikes.
- Prompt injection, tool misuse, or agent planning causes repeated or
  excessive work.
- Limits, quotas, timeouts, or budget checks are missing or bypassed.

Do not use when:
- The issue is traditional ReDoS or DoS in a package with no LLM/agent
  consumption path.
- The resource impact is incidental to another primary exploit.

Evidence patterns:
- "unbounded consumption", "resource exhaustion", "DoS", "denial of
  service", "token", "cost", "billing", "loop", "recursive", "rate
  limit", "quota", "timeout", "memory", "API calls".

Related mappings:
- `asi02` for loop amplification through tools.
- `asi08` for cascading failures from resource exhaustion.

## General Mapping Notes

- Use LLM taxonomy only when the LLM/GenAI application dimension is
  present. For ordinary software CVEs, rely on upstream CWE/GHSA/CVE and
  omit LLM mappings.
- `llm01:2025`, `asi01`, `mcp03:2025`, and `mcp10:2025` are adjacent:
  use `llm01` for prompt/content manipulation generally, `asi01` for
  agent goal/plan impact, `mcp03` for poisoned MCP metadata, and `mcp10`
  for MCP context injection or over-sharing.
- `llm03:2025`, `asi04`, `mcp04:2025`, and `ast02:2026` are adjacent:
  use `llm03` for LLM/GenAI supply chain, `asi04` for agent
  composition, `mcp04` for MCP components, and `ast02` for skill
  distribution paths.
- `llm05:2025` and `asi05` are adjacent: use `llm05` when LLM output is
  mishandled by another sink; use `asi05` when the agent workflow reaches
  unexpected code execution.
