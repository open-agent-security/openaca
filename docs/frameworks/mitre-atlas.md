# MITRE ATLAS

Sources:
- https://atlas.mitre.org/
- https://github.com/mitre-atlas/atlas-data
- https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml

ATLAS data version referenced: 5.6.0

Accessed: 2026-05-13

Use MITRE ATLAS for AI/ML-specific adversary behavior. Do not force an
ATLAS mapping onto ordinary software malware, package vulnerabilities,
or generic web-app bugs unless the record shows AI, ML, LLM, model,
agent, RAG, AI DevOps, or AI service behavior.

## Selected IDs For ASVE Review

| Overlay ID | Name | ASVE Use |
| --- | --- | --- |
| `AML.T0010` | AI Supply Chain Compromise | Compromise of AI-specific hardware, data, software, or model supply chain. |
| `AML.T0010.001` | AI Software | Malicious or compromised package commonly used in AI-enabled systems or AI DevOps. |
| `AML.T0010.002` | Data | Compromised or poisoned AI data source, labels, or training/operational data. |
| `AML.T0010.003` | Model | Compromised, malicious, or poisoned model artifact. |
| `AML.T0051` | LLM Prompt Injection | Malicious prompt causes an LLM to act in unintended ways. |
| `AML.T0051.000` | Direct | Prompt injection submitted directly by the adversary. |
| `AML.T0051.001` | Indirect | Prompt injection embedded in a separate data channel later ingested by the LLM. |
| `AML.T0051.002` | Triggered | Prompt injection activated by a user action or system event. |
| `AML.T0074` | Masquerading | Artifact name, location, or metadata is manipulated to appear legitimate. |
| `AML.T0096` | AI Service API | Adversary communicates through an AI service API on the victim system. |

## Mapping Guidance

- Use `AML.T0010.001` for malicious packages only when the package is
  clearly AI/agent/LLM software, an AI DevOps component, an MCP server,
  agent framework component, or a dependency of such a component.
- Use `AML.T0074` when namesquatting, typosquatting, fake branding, or
  misleading artifact identity is part of the evidence.
- Use `AML.T0051.*` only when prompt content is the attack mechanism.
- Use `AML.T0096` only when the attacker uses an AI service API as part
  of command-and-control or attack communication.
- Omit MITRE ATLAS when a better description is ordinary package malware
  with no AI-specific behavior. ASVE can still map that record to OWASP
  Agentic or MCP supply-chain categories if it affects agent
  composition.

## Example Notes

- Malicious MCP package installed by an agent: `AML.T0010.001` if the
  package is plausibly AI/agent software; add `AML.T0074` when
  namesquatting or deceptive naming is explicit.
- Prompt injection in MCP tool description: `AML.T0051.001` if the agent
  ingests it as tool metadata; `AML.T0051.002` if activated by a later
  workflow event.
- Normal CVE in lodash used by a SaaS app: no ATLAS mapping by default.
