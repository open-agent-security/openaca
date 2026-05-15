# OWASP Agentic Skills Top 10 2026

Sources:
- https://owasp.org/www-project-agentic-skills-top-10/
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/index.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast01.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast02.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast03.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast04.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast05.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast06.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast07.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast08.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast09.md
- https://github.com/OWASP/www-project-agentic-skills-top-10/blob/main/ast10.md

Accessed: 2026-05-13

Use this only when the affected artifact is an agentic skill, skill
registry entry, skill manifest, reusable behavior bundle, or
cross-platform skill port. Do not apply it to every package, plugin, or
MCP server.

OWASP's mental model is useful for OpenACA review: MCP describes how models
talk to tools; Agentic Skills describes what reusable behavior bundles
actually do. Skills encode workflow, permissions, tool orchestration,
state, and sometimes executable setup/install paths.

## `ast01:2026` — Malicious Skills

Definition: A skill bundle itself is intentionally hostile. The payload
may be executable code, scripts, setup steps, hidden markdown
instructions, persistence through agent memory/identity files, or social
engineering embedded in skill prose.

Use when:
- A `SKILL.md`, `skill.json`, manifest, skill script, or equivalent
  skill package contains credential theft, backdoors, C2, persistence,
  or deceptive instructions.
- The record describes typosquatted or brand-impersonating skills whose
  purpose is to trick users into installing a malicious behavior bundle.
- The evidence shows malicious natural-language instructions, not only
  malicious code.

Do not use when:
- The artifact is only a malicious npm/PyPI package and no skill bundle,
  skill registry, or skill install path is involved.
- The skill is legitimate but vulnerable; map to the underlying weakness
  instead.

Evidence patterns:
- "malicious skill", "SKILL.md", "hidden instructions", "credential
  stealer", "reverse shell", "backdoor", "C2", "memory poisoning",
  "identity file", "SOUL.md", "MEMORY.md".

Related mappings:
- `asi04` for agentic supply chain delivery.
- `asi05` when install/load/runtime reaches code execution.
- `llm01:2025` when natural-language instructions manipulate the agent.
- `llm03:2025` when registry/package distribution is the delivery path.

## `ast02:2026` — Supply Chain Compromise

Definition: The skill delivery path is compromised or lacks provenance.
This includes poisoned registries, dependency confusion, account
takeover, malicious updates, poisoned repository configuration, or
nested dependencies that carry the real payload.

Use when:
- The evidence involves skill registries, install sources, publisher
  identity, nested skill dependencies, repository config files, update
  channels, or dependency confusion.
- A skill's `package.json`, `requirements.txt`, setup hook, or project
  config pulls the malicious payload while the top-level skill appears
  benign.
- Repository-controlled agent configuration executes before a trust gate.

Do not use when:
- The issue is a runtime bug in a legitimate installed skill and there is
  no supply-chain or distribution-channel component.
- The package is merely vulnerable, not compromised or malicious.

Evidence patterns:
- "registry", "publisher", "account takeover", "dependency confusion",
  "typosquat", "nested dependency", "malicious update", "hooks",
  "repository configuration", "package.json", "requirements.txt",
  "install path", "no code signing".

Related mappings:
- `asi04`
- `mcp04:2025` when the compromised artifact presents as an MCP
  component.
- `llm03:2025`
- `AML.T0010.001` when the artifact is AI/agent software.

## `ast03:2026` — Over-Privileged Skills

Definition: A skill receives broader filesystem, shell, network,
database, memory, or credential authority than its stated purpose needs.
The risk is amplified because natural-language intent can steer broad
tool permissions into unintended actions.

Use when:
- Skill permissions are excessive, undeclared, misleading, or accepted
  without review.
- The record describes shared agent-level credentials being used by a
  skill instead of scoped per-skill credentials.
- A skill can write agent identity/memory files, run shell commands, or
  access broad network/filesystem scope unrelated to its declared task.

Do not use when:
- The skill has normal required permissions and the bug is elsewhere.
- The record only says the host process is powerful; there must be
  skill-level permission overreach or missing per-skill scoping.

Evidence patterns:
- "over-privileged", "full access", "shared credentials", "permission
  manifest", "network: true", "write access", "SOUL.md", "MEMORY.md",
  "AGENTS.md", "all files", "shell access".

Related mappings:
- `asi03` for identity/privilege abuse.
- `asi02` when overbroad tools are misused.
- `llm06:2025` for excessive agency.

## `ast04:2026` — Insecure Metadata

Definition: Skill metadata is attacker-controlled or unauthenticated and
misleads users, scanners, registries, or installing agents. Metadata may
misrepresent name, author, permissions, risk tier, dependencies, or
behavior.

Use when:
- The record concerns brand impersonation, fake publisher identity,
  understated permissions, spoofed risk tier, poisoned search metadata,
  or metadata hidden from human review but visible to the agent.
- Metadata fields are the trust decision surface rather than incidental
  strings.

Do not use when:
- The package name is malicious but no skill metadata or skill registry
  trust decision is involved.
- The metadata accurately describes a vulnerable implementation.

Evidence patterns:
- "metadata", "description", "author", "publisher", "risk_tier",
  "permissions", "requires", "brand impersonation", "fake", "spoof",
  "zero-width", "base64", "ASCII smuggling", "misleading README".

Related mappings:
- `asi04` when metadata manipulation enables supply-chain install.
- `asi09` when the attack primarily exploits human trust.
- `mcp03:2025` if tool metadata itself poisons agent behavior.

## `ast05:2026` — Unsafe Deserialization

Definition: A skill loader parses skill-provided YAML, JSON, Markdown
frontmatter, manifests, dependency files, or equivalent configuration in
an unsafe way, allowing payloads to run during load/parse/install before
ordinary user action.

Use when:
- The evidence identifies unsafe YAML/JSON/TOML parsing, dangerous
  parser tags, prototype pollution in manifests, or deserialization
  during skill initialization.
- Dependency install files inside the skill act as untrusted code during
  the loader lifecycle.

Do not use when:
- The exploit is ordinary command injection during a tool call and does
  not depend on parsing/loader behavior.
- The record says "malicious package" but not unsafe parser or loader
  behavior.

Evidence patterns:
- "deserialization", "YAML", "JSON", "frontmatter", "manifest",
  "prototype pollution", "`!!python/object`", "`yaml.load`",
  "skill loader", "load time", "parse".

Related mappings:
- `asi05` if deserialization reaches code execution.
- `llm05:2025` only if unsafe model output is passed to a parser/sink.

## `ast06:2026` — Weak Isolation

Definition: Skills execute without sufficient containment from the host,
other skills, or agent runtime. Host-mode execution, missing sandboxing,
unauthenticated local control surfaces, and unsafe hot-reload behavior
turn a skill compromise into system compromise.

Use when:
- The record describes missing container/sandbox isolation, unrestricted
  host filesystem/shell/network access, exposed localhost/WebSocket
  control paths, unsafe workspace precedence, or hot-reload abuse.
- The primary impact is that a compromised skill escapes intended
  boundaries.

Do not use when:
- The skill is malicious but the record does not discuss isolation,
  sandbox bypass, host execution, or process boundaries.
- The issue is overbroad declared permission; consider `ast03:2026`.

Evidence patterns:
- "host mode", "sandbox", "container", "Docker", "isolation",
  "localhost", "WebSocket", "hot reload", "workspace precedence",
  "process boundary", "full host access".

Related mappings:
- `asi05` when weak isolation enables code execution.
- `llm06:2025` for excessive agency in weakly constrained runtimes.

## `ast07:2026` — Update Drift

Definition: Installed skills drift from known-good versions because
updates, patches, versions, hashes, signatures, advisories, or runtime
reload behavior are not controlled. Drift can mean failure to patch or
silent adoption of a malicious update.

Use when:
- The evidence involves patch lag, unverified auto-update, rollback to a
  vulnerable version, mutable version ranges, missing hashes, missing
  signatures, or hot-reloaded skill changes.
- A "fixed" skill version cannot be trusted because the artifact is not
  pinned to immutable content.

Do not use when:
- The record only says a package has an affected version range; update
  drift requires a skill lifecycle/update control issue.

Evidence patterns:
- "auto update", "hot reload", "version drift", "patch lag", "rollback",
  "hash", "signature", "pin", "mutable", "unverified update",
  "last verified".

Related mappings:
- `asi04`
- `llm03:2025`
- `ast02:2026` when the update channel is actively compromised.

## `ast08:2026` — Poor Scanning

Definition: Existing scanning fails because skills blend code,
configuration, and natural-language instructions. Pattern matching may
miss semantic, behavioral, obfuscated, or context-dependent malicious
behavior.

Use when:
- The record is about scanner bypass, false trust from scanner output,
  inadequate semantic analysis, obfuscated skill instructions, or
  malicious behavior expressed entirely in prose.
- A scanner skill or registry scanner is itself part of the attack.

Do not use when:
- A vulnerability simply was not previously known.
- The record has no evidence of scanning, detection, bypass, or
  obfuscation relevance.

Evidence patterns:
- "scanner", "pattern matching", "regex", "semantic analysis",
  "behavioral analysis", "obfuscated", "base64", "zero-width",
  "ASCII smuggling", "false security", "passes scan".

Related mappings:
- `asi04` when poor scanning allows supply-chain malware.
- `ast01:2026` when scanner failure permits malicious skills.

## `ast09:2026` — No Governance

Definition: An organization lacks inventory, approval workflow,
ownership, revocation, audit logging, SOC visibility, or policy controls
for skills at enterprise scale. This creates a shadow-agent layer that
security teams cannot manage.

Use when:
- The evidence concerns enterprise deployment, unmanaged skills,
  missing inventory, absent approval flow, orphaned credentials, missing
  audit logs, or lack of revocation.
- The record describes governance failures as the primary reason the
  exposure persists or spreads.

Do not use when:
- The finding is a single component vulnerability with no organizational
  governance or fleet-management angle.

Evidence patterns:
- "inventory", "approval", "audit", "SOC visibility", "governance",
  "revocation", "offboarding", "CMDB", "shadow AI", "unmanaged",
  "enterprise".

Related mappings:
- `asi08` if governance failure enables cascading agent failures.
- `llm06:2025` when unmanaged agency is the core risk.

## `ast10:2026` — Cross-Platform Reuse

Definition: A skill is ported between platforms without preserving or
normalizing security metadata such as permissions, signatures, risk
tier, scan status, trust prompts, or provenance. The same malicious or
unsafe behavior can spread across registries and runtimes.

Use when:
- The evidence involves OpenClaw, Claude Code, Cursor, Codex, VS Code,
  or other platforms reusing a skill or equivalent behavior bundle.
- Security properties are lost during translation, or multiple
  registries/runtimes are abused by the same campaign.
- A platform-agnostic skill payload exploits inconsistent scanning or
  governance across ecosystems.

Do not use when:
- The issue is confined to one platform and there is no porting,
  cross-registry, or cross-runtime evidence.

Evidence patterns:
- "ported", "cross-platform", "multi-platform", "same payload",
  "registry arbitrage", "skills.sh", "ClawHub", "manifest translation",
  "lost permissions", "universal skill format".

Related mappings:
- `asi04`
- `llm03:2025`
- `ast04:2026` when metadata fields are lost or mistranslated.
- `ast08:2026` when scanning coverage differs across platforms.

## General Mapping Notes

- A malicious PyPI/npm package with `mcp` in the name is not enough for
  AST mapping. Prefer `mcp04:2025`, `asi04`, and possibly `llm03:2025`
  unless the record ties the package to a skill artifact, skill
  registry, or skill install path.
- If a package is a dependency of a known skill, `ast02:2026` is usually
  the first AST mapping to consider.
- If a malicious package executes during skill load/install, consider
  `ast02:2026`; add `ast05:2026` only when unsafe loader parsing or
  deserialization is part of the evidence.
- If the evidence is natural-language malicious behavior inside a skill,
  `ast01:2026` is usually primary and `ast08:2026` may apply when
  scanner bypass is explicit.
