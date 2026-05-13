# MITRE ATLAS

Sources:
- https://atlas.mitre.org/
- https://github.com/mitre-atlas/atlas-data
- https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml

ATLAS data version referenced: 5.6.0

Accessed: 2026-05-13

Use MITRE ATLAS for adversary behavior that is specific to AI, ML, LLM,
agent, RAG, AI DevOps, AI service, or AI artifact systems. Do not force
an ATLAS mapping onto ordinary software malware, package vulnerabilities,
or generic web-app bugs unless the OSV record shows that the AI system is
part of the attack path.

ATLAS describes attacker techniques. An ASVE overlay should map an ATLAS
technique only when the advisory evidence shows the attacker behavior,
not merely because the vulnerable package is used by an agent.

## Selected IDs For ASVE Review

| Overlay ID | Name | Use When |
| --- | --- | --- |
| `AML.T0010` | AI Supply Chain Compromise | The issue compromises AI-specific hardware, data, software, models, tools, or artifacts before they are used by a victim system. |
| `AML.T0010.001` | AI Software | A malicious or compromised software package is specifically AI, LLM, agent, MCP, model, data, or AI DevOps software. |
| `AML.T0010.002` | Data | The issue affects data used to train, tune, evaluate, retrieve, or operate an AI system. |
| `AML.T0010.003` | Model | The issue affects a model artifact, model weights, model package, or model provenance. |
| `AML.T0010.005` | AI Agent Tool | The supply-chain target is an agent tool such as an MCP server, agent plugin, hosted tool endpoint, or package that expands an agent's tool access. |
| `AML.T0011.002` | Poisoned AI Agent Tool | The victim invokes a malicious or poisoned tool through an agent interaction. |
| `AML.T0051` | LLM Prompt Injection | Malicious prompt content causes an LLM or agent to ignore instructions, bypass controls, or take unintended actions. |
| `AML.T0051.000` | Direct | The adversary submits the malicious prompt directly to the LLM or agent. |
| `AML.T0051.001` | Indirect | The malicious prompt is embedded in retrieved content, documents, webpages, tool metadata, code comments, or another data channel. |
| `AML.T0051.002` | Triggered | The injected prompt is dormant until a later user action, workflow event, retrieval, or tool invocation activates it. |
| `AML.T0053` | AI Agent Tool Invocation | The adversary causes an agent to call a tool it can access. |
| `AML.T0074` | Masquerading | Names, domains, package metadata, registry identity, branding, or file paths are crafted to look legitimate. |
| `AML.T0085` | Data from AI Services | The adversary uses access to an AI-enabled service to collect sensitive data. |
| `AML.T0085.001` | AI Agent Tools | The data collection path is an agent tool such as a repo, file, email, CRM, docs, chat, shell, browser, or SaaS connector. |
| `AML.T0086` | Exfiltration via AI Agent Tool Invocation | The adversary uses a write-capable agent tool to transmit sensitive data out of the victim environment. |
| `AML.T0096` | AI Service API | The adversary uses an AI service API as a command, control, or attack communication channel. |
| `AML.T0098` | AI Agent Tool Credential Harvesting | The adversary uses agent tool access to retrieve credentials, tokens, keys, session data, or auth material. |
| `AML.T0099` | AI Agent Tool Data Poisoning | The adversary plants malicious content where an agent tool can retrieve it. |
| `AML.T0100` | AI Agent Clickbait | Deceptive web content is designed to lure computer-use agents or AI browsers into unintended actions. |
| `AML.T0101` | Data Destruction via AI Agent Tool Invocation | The adversary causes an agent tool to delete, overwrite, encrypt, or otherwise destroy data. |
| `AML.T0103` | Deploy AI Agent | The adversary launches an agent in the victim environment to act on their behalf. |
| `AML.T0104` | Publish Poisoned AI Agent Tool | The adversary publishes a malicious agent tool for others to install or connect. |
| `AML.T0105` | Escape to Host | The attack breaks out of a sandbox, container, VM, or other isolation boundary used by an AI system or agent. |
| `AML.T0108` | AI Agent | The adversary abuses an existing AI agent as a command-and-control mechanism. |
| `AML.T0109` | AI Supply Chain Rug Pull | The adversary first builds trust in an AI component, then ships a malicious update. |
| `AML.T0110` | AI Agent Tool Poisoning | The adversary compromises or alters a tool already integrated with an agent so that it can influence future agent behavior. |

## Classification Rules

### Supply Chain

Use `AML.T0010` when the attack path depends on compromised AI supply.
Prefer a subtechnique when one is precise:

- Use `AML.T0010.001` for malicious AI software, LLM libraries, agent
  frameworks, MCP servers, AI coding assistant extensions, model-serving
  components, or AI DevOps packages.
- Use `AML.T0010.005` when the compromised supply-chain item is an
  agent tool. This is the best default for malicious MCP servers and
  malicious tool plugins that an agent can install or connect to.
- Use `AML.T0104` when the advisory is about publishing the poisoned
  tool into a registry, repository, catalog, or hosted remote-tool
  directory. Pair it with `AML.T0010.005` when the published artifact is
  also the compromised supply-chain item.
- Use `AML.T0109` only when the record shows a trust-building phase
  followed by a malicious update. Do not use it for one-off typosquats
  or newly-published malicious packages.
- Use `AML.T0074` in addition to supply-chain IDs when the evidence
  includes typosquatting, namesquatting, misleading branding, copied
  package metadata, lookalike domains, or deceptive artifact names.

Do not use ATLAS supply-chain mappings for a normal CVE in a common
library merely because that library appears in an agent dependency tree.
The advisory needs AI, agent, model, tool, or AI DevOps relevance.

### Prompt Injection

Use `AML.T0051` when prompt content is the attack mechanism. Choose a
subtechnique by where the malicious prompt enters the system:

- `AML.T0051.000`: attacker directly sends the prompt to the agent,
  chatbot, API, or model.
- `AML.T0051.001`: prompt is hidden in content later retrieved or read
  by the model, including webpages, documents, tickets, emails, repo
  files, tool descriptions, MCP metadata, rules files, comments, or RAG
  content.
- `AML.T0051.002`: prompt is planted earlier and activates only after a
  later event, retrieval, user action, or workflow step.

Prompt injection often co-occurs with `AML.T0053`, `AML.T0086`,
`AML.T0099`, or `AML.T0101`. Add those only when the record shows the
agent tool behavior, data poisoning, exfiltration, or destructive action.

### Agent Tool Abuse

Use these mappings when the advisory evidence shows agent tool access,
not merely the presence of an MCP package:

- `AML.T0053`: the adversary causes an agent to invoke a tool. This is a
  broad action mapping; add a more specific impact technique when known.
- `AML.T0085.001`: the tool is used to retrieve sensitive data from
  connected systems such as repos, document stores, email, chat, ticketing
  systems, browsers, shells, or local files.
- `AML.T0086`: the tool transmits data out of the victim environment
  through an apparently legitimate write action, such as sending email,
  creating a document, posting to chat, opening a URL, or writing to an
  attacker-controlled system.
- `AML.T0098`: the target data is credentials or auth material.
- `AML.T0101`: the tool performs destructive mutation such as deleting,
  overwriting, encrypting, or corrupting data.

For a vulnerable MCP server that permits arbitrary command execution,
map to `AML.T0053` only when the exploit path involves the agent invoking
the server's tool. If the advisory is a conventional server-side command
injection with no agent invocation semantics, omit ATLAS unless another
AI-specific behavior is present.

### Agent Tool Poisoning And Data Poisoning

Use `AML.T0110` when an existing agent tool is compromised or altered
after it is integrated. This is about persistence or long-term influence
through a trusted tool.

Use `AML.T0011.002` when the victim invokes the poisoned tool and that
tool execution is the immediate entry point.

Use `AML.T0099` when malicious content is planted where an agent tool can
retrieve it, such as shared documents, repo files, websites, issues,
tickets, emails, notes, vector-store content, or RAG sources. Add
`AML.T0051.001` when the planted content includes prompt injection.

### Agent Runtime And Host Boundary

Use `AML.T0108` when the adversary uses an existing AI agent as a
command-and-control mechanism, especially when the agent is instructed to
retrieve commands, execute tools, suppress reporting, or maintain
control.

Use `AML.T0103` when the adversary deploys a new agent into the victim
environment to perform actions on their behalf. Do not use it when the
victim merely installs a malicious package that happens to be an agent
tool.

Use `AML.T0105` when the attack escapes an isolation boundary used by an
AI system, such as an agent sandbox, container, browser isolation layer,
VM, local execution jail, or tool runner. Pair it with ATT&CK container
escape mappings outside the overlay if upstream already provides them.

### Web And API Abuse

Use `AML.T0100` when the advisory describes web content crafted to bait a
computer-use agent or AI browser into actions such as clicking, copying,
navigating, approving, downloading, or pasting commands. This should not
be used for ordinary phishing unless the target is an AI browsing or
computer-use agent.

Use `AML.T0096` when an AI service API is the adversary communication
channel or command path. This is for attacker traffic blending into AI
service API usage, not for every vulnerability in an API-backed AI app.

## Evidence Cues

The following advisory evidence is usually enough to consider an ATLAS
mapping:

- Package or component is explicitly an MCP server, agent tool, AI
  plugin, AI coding assistant extension, model package, RAG connector,
  LLM framework, model-serving component, or AI DevOps tool.
- Details mention agent tool invocation, tool descriptions, tool calls,
  MCP, remote tools, local tools, computer-use agents, AI browsers,
  model context, RAG ingestion, prompt injection, jailbreak, tool
  poisoning, or agent configuration changes.
- Impact involves agent permissions, source-code access through tools,
  credential access through tools, exfiltration through tool outputs,
  destructive tool actions, AI service command channels, model/data
  poisoning, or malicious AI artifact publication.

Evidence that is usually not enough:

- Generic package malware with no AI, LLM, model, MCP, or agent
  behavior.
- Ordinary dependency vulnerabilities discovered in software used by an
  agent.
- Generic command injection, SSRF, path traversal, or XSS with no
  agent-specific exploit path.
- CWE or ATT&CK mappings from upstream that describe the underlying
  software weakness but not AI-specific adversary behavior.

## Example Notes

- Malicious MCP package published to npm: `AML.T0010.005` and
  `AML.T0104`; add `AML.T0074` if namesquatting is explicit.
- Benign MCP server compromised after installation so it silently steals
  data: `AML.T0110`; add `AML.T0086` if the exfiltration path is a
  write-capable tool action.
- Prompt injection in an MCP tool description: `AML.T0051.001`; add
  `AML.T0053` if it causes a tool call.
- Prompt injection in a webpage that makes a browser agent click a
  button: `AML.T0051.001` plus `AML.T0100`; add `AML.T0053` if a tool
  invocation is the operative action.
- Agent retrieves poisoned instructions from a shared document through a
  connector: `AML.T0099`; add `AML.T0051.001` if the content is prompt
  injection.
- Prompt injection causes an agent to delete files through a shell tool:
  `AML.T0051.001`, `AML.T0053`, and `AML.T0101`.
- Vulnerable lodash in an app dependency tree: no ATLAS mapping by
  default.
