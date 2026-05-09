# ASVE Coordinated Disclosure Policy

ASVE follows the [OpenSSF coordinated disclosure
guidance](https://openssf.org/) with the project-specific defaults
captured below. The policy defines what we commit to, on what timeline,
and how disputes are handled.

## V0 status

ASVE V0 documents this policy. **V0 does not operate an active
disclosure program.** Submissions described here will not be processed
at scale until V1, which is gated on the readiness criteria in
[`docs/specs/asve-v0-design.md`](specs/asve-v0-design.md) §10.

When V0 receives a report that meets the bar for inclusion, the
maintainers will run a single end-to-end coordinated-disclosure case as
part of the V1 readiness gate. That case proves the framework before
active disclosure scales.

## Scope

ASVE accepts reports for vulnerabilities affecting **agent-stack
components** that are publicly distributed and identifiable by version
or stable hash:

- MCP servers (npm, PyPI, GitHub-hosted, container).
- Claude Code plugins distributed via marketplaces.
- Skill bundles with a stable identifier.
- Agent frameworks and model proxies that integrate into agent runtimes.

Out of scope:

- Vulnerabilities in agent applications themselves (file upstream with
  the application's maintainer).
- Configuration patterns that don't tie to a specific component
  instance — V1 territory under `type: config`.
- AI model behavioral failures unrelated to a specific component.

## How to report

Email `security@asve.dev` with:

- Affected component (name + version or commit SHA).
- Reproduction steps or proof-of-concept.
- Impact analysis: which `agent_impact` dimensions are reachable
  (`repo_read`, `repo_write`, `credential_exfiltration`, `tool_hijack`,
  `memory_poisoning`, `pr_manipulation`, `code_execution`).
- OWASP Agentic Top 10 categories you believe apply (`asi01`–`asi10`).
- Whether you have already contacted the affected maintainer.

Encrypted submissions: include a PGP key in your initial message; we
will respond with our key and switch to encrypted exchange.

> **Note:** the `security@asve.dev` mailbox goes live alongside the
> public V0 launch. Until then, file reports as security advisories
> through the GitHub Security tab on `open-agent-security/asve`.

## Process and timeline

| Stage | Default timeline |
|---|---|
| Acknowledgement | within 5 business days of receipt |
| Maintainer-response checkpoint | 21 days from initial notice to upstream maintainer |
| Embargo | 90 days from acknowledgement (default) |
| Nonresponsive review | 35 days; if maintainer is unresponsive at 35 days, ASVE re-evaluates publication path |
| Publication | within 7 days of fix availability or embargo expiry |

**Active exploitation** accelerates the timeline. If credible evidence
indicates active exploitation, ASVE may publish ahead of the default
embargo on a case-by-case basis.

## Dispute lifecycle

Each ASVE record has a status:

```
published → disputed → modified | upheld | withdrawn
```

- **published**: the record is live in the corpus.
- **disputed**: an affected maintainer or downstream contests the
  record. ASVE marks the record `disputed` and pauses propagation.
- **modified**: ASVE accepts the dispute and revises the record.
- **upheld**: ASVE rejects the dispute. The record stays published with
  the dispute history attached.
- **withdrawn**: ASVE retracts the record (false-positive, duplicate,
  or out-of-scope).

A disputed record always carries a public dispute history so consumers
can see what changed and why.

## Attribution and credit

- Reporter credit: ASVE includes reporter attribution in the published
  record unless the reporter requests anonymity.
- Tooling attribution: where a finding originated from a third-party
  open-source scanner, ASVE attributes the tool by name and version
  (e.g., "detected during ASVE triage using `<tool>` v0.X").
  Attribution is descriptive — it does not imply endorsement,
  partnership, or third-party confirmation.

## Aliases and upstream submission

- Records aliasing existing CVE/GHSA/OSV require no upstream filing —
  the upstream record already exists.
- ASVE-original component vulnerabilities: ASVE will attempt upstream
  disclosure to CVE/GHSA where the affected ecosystem is accepted by
  upstream pipelines. Where upstream pipelines don't accept the
  ecosystem cleanly, ASVE may carry the authoritative record.

## Out of scope (escalation, indemnity, payment)

ASVE V0 is an OSS advisory database. We do not:

- Pay bug bounties. Reporters seeking payouts should consider
  bounty-focused programs such as [huntr](https://huntr.com/).
- Provide legal indemnity.
- Act as an intermediary for legal threats; if a maintainer asserts a
  legal claim against a reporter, ASVE will not relay or escalate it.

## Contact

`security@asve.dev` for vulnerability reports. For non-security
questions, file a GitHub issue against the repo.
