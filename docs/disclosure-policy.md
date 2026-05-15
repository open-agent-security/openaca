# OpenACA Coordinated Disclosure Policy

OpenACA follows the [OpenSSF coordinated disclosure
guidance](https://openssf.org/) with the project-specific defaults
captured below. The policy defines what we commit to, on what timeline,
and how disputes are handled.

## V0 status

OpenACA V0 documents this policy. **V0 does not operate an active
disclosure program or mint OpenACA vulnerability IDs.** Overlays land
once an upstream record exists (GHSA / CVE / OSV / PYSEC / MAL).
Submissions described here will not be processed at scale until a
later V1 phase introduces an active disclosure lane.

When V0 receives a report that meets the bar for inclusion, the
maintainers will run a single end-to-end coordinated-disclosure case as
part of the V1 readiness gate. That case proves the framework before
active disclosure scales.

## Scope

OpenACA accepts reports for vulnerabilities affecting **agent-stack
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

Email `security@openaca.dev` with:

- Affected component (name + version or commit SHA).
- Reproduction steps or proof-of-concept.
- Impact analysis: what an attacker can reach through the agent stack.
- OWASP Agentic Top 10 categories you believe apply (`asi01`–`asi10`).
- Whether you have already contacted the affected maintainer.

Encrypted submissions: include a PGP key in your initial message; we
will respond with our key and switch to encrypted exchange.

> **Note:** the `security@openaca.dev` mailbox goes live alongside the
> public V0 launch. Until then, file reports as security advisories
> through the GitHub Security tab on `open-agent-security/openaca`.

## Process and timeline

| Stage | Default timeline |
|---|---|
| Acknowledgement | within 5 business days of receipt |
| Maintainer-response checkpoint | 21 days from initial notice to upstream maintainer |
| Embargo | 90 days from acknowledgement (default) |
| Nonresponsive review | 35 days; if maintainer is unresponsive at 35 days, OpenACA re-evaluates publication path |
| Publication | within 7 days of fix availability or embargo expiry |

**Active exploitation** accelerates the timeline. If credible evidence
indicates active exploitation, OpenACA may publish ahead of the default
embargo on a case-by-case basis.

## Dispute lifecycle

Each OpenACA overlay has a status (mirrored in `evidence_level`):

```
published → disputed → modified | upheld | withdrawn
```

- **published**: the overlay is live in the corpus.
- **disputed**: an affected maintainer or downstream contests the
  overlay. OpenACA flips `evidence_level: disputed` and pauses propagation.
- **modified**: OpenACA accepts the dispute and revises the overlay.
- **upheld**: OpenACA rejects the dispute. The overlay stays published with
  the dispute history attached.
- **withdrawn**: OpenACA retracts the overlay (false-positive, duplicate,
  or out-of-scope). `evidence_level: withdrawn`.

A disputed overlay always carries a public dispute history so users
can see what changed and why.

## Attribution and credit

- Reporter credit: OpenACA includes reporter attribution in the published
  record unless the reporter requests anonymity.
- Tooling attribution: where a finding originated from a third-party
  open-source scanner, OpenACA attributes the tool by name and version
  (e.g., "detected during OpenACA triage using `<tool>` v0.X").
  Attribution is descriptive — it does not imply endorsement,
  partnership, or third-party confirmation.

## Upstream submission

OpenACA overlays sit on top of upstream records. When a contributor
discovers a vulnerability in an agent-stack component that does not yet
have an upstream record, the workflow is: file upstream first
(CVE / GHSA / OSV / PYSEC) and land the overlay once the upstream ID is
issued. Where an ecosystem isn't yet served by an upstream pipeline,
OpenACA contributors pursue ecosystem onboarding rather than minting a
parallel ID.

## Out of scope (escalation, indemnity, payment)

OpenACA V0 is an OSS overlay corpus and scanner. We do not:

- Pay bug bounties. Reporters seeking payouts should consider
  bounty-focused programs such as [huntr](https://huntr.com/).
- Provide legal indemnity.
- Act as an intermediary for legal threats; if a maintainer asserts a
  legal claim against a reporter, OpenACA will not relay or escalate it.

## Contact

`security@openaca.dev` for vulnerability reports. For non-security
questions, file a GitHub issue against the repo.
