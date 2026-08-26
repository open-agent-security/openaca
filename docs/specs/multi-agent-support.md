# Multi-Agent Support — Design

Companion ADRs: [0044](../adrs/0044-agent-bom-root.md) (BOM root),
[0045](../adrs/0045-agent-identity-keying.md) (keying),
[0046](../adrs/0046-agent-coverage.md) (coverage).

Per-kind specs written against this mechanism:
[Cursor Agent Kind](cursor-agent-kind.md).

Mechanism only. A runtime's own config paths, manifest shapes, and precedence
rules belong in a per-kind spec, so a future implementer of a managed or
framework agent never reads coding-agent material to understand this.

## The change

**Today a BOM describes a place. After this change a BOM describes one agent.**

### What this change does

OpenACA supports exactly one agent runtime today: Claude Code. This change does
not add a second one — it changes what a BOM is *about*, so that adding one later
is an extension rather than a re-root.

With one runtime there is still one agent to find, so a scan still emits one
document:

| | Today | After |
|---|---|---|
| Command | `openaca scan endpoint` | **unchanged** |
| Subject | the config directory that was scanned | **the agent that loads the composition** |
| Output | 1 BOM, `target_type: endpoint` | 1 BOM whose subject **is** the agent; `target_type` is gone |
| Document count | one per scan, by construction | one **per agent found** — which is one, today |

So the observable diff for a user is small. What changes is that document count is
now a consequence of discovery rather than an assumption baked into the scanner.

### What it enables

**Supporting a second runtime is [out of scope](#out-of-scope) here** — no parser
for one is added. What follows is what the abstraction makes *expressible*, so
that adding a runtime later requires writing its discovery and parsers and nothing
more:

| Situation | Today | Under this design |
|---|---|---|
| Two runtimes on one workstation | only the one with a parser is found; the other is invisible | one BOM each |
| A file both runtimes read | appears once, with no sign the second loads it | appears in both, resolved per agent |
| A repo declaring two runtimes' config | one BOM mixing both | one BOM per declared agent |
| A vulnerable MCP server in that repo | "this repo has it" | "**this agent** has it" |
| An account holding fourteen managed agents | not representable | fourteen BOMs |

Because only one runtime ships, no real scan emits more than one document, so
multi-document emission has to be exercised by test-only synthetic runtimes
rather than by anything shipping.

This spec calls a runtime a **kind** from here on — the term is defined under
[Kind and composition source](#kind-and-composition-source-are-independent), and
it is deliberately narrower than "runtime": two products are the same kind only
if they read the same composition surface with the same schema.

**One word, two scopes.** "Agent" here means the BOM's subject: a runtime plus
the context it loads. The closed component-type set (ADR-0019/0031) also has an
`agent` type, which means a **subagent** the runtime loads — `openaca:identity:
agent/reviewer` — and that is unchanged. The two never collide in a document: the
subject is `metadata.component` carrying `openaca:agent_kind`, while a subagent is
a `components[]` row carrying `openaca:component_type: agent`. The subject's
`bom-ref` is prefixed `root/` rather than `agent/` for the same reason — so no key
shares a namespace with the subagent type. There is no "stack" in the vocabulary:
it described an implementation, not something a reader needs.

## Why

`scan endpoint` resolves one config directory and `scan repo` walks one tree,
each emitting one BOM. Both assume a single runtime: the endpoint seed is
hardcoded to Claude Code and the manifest registry has no runtime tagging.

Fixing that for a second coding agent alone would bake in a second assumption.
The abstraction has to admit, without re-rooting: two coding agents on one
workstation; a managed service where one account holds many agents; a framework
agent where one repo declares many; an agent in a cloud sandbox reading config
from a repo.

So the question is larger than multi-host: **what is the first-order thing a BOM
describes?** Today a place. This makes it an **agent** — one agent runtime
plus the composed context it loads.

Nothing below the root changes: component types (ADR-0019/0031), the composition
graph and attribution-by-containment (ADR-0037), identity and `bom-ref` semantics
(ADR-0038), overlays, OSV federation, posture rule implementations.

## Model

### Kind and composition source are independent

**Kind** — what reads the composition. Two runtimes are the same kind only if
they read the same surface with the same schema, which is why Claude Code and
Cursor are separate kinds, and LangGraph and CrewAI are. A kind is never
qualified by where it runs: Cursor in a cloud sandbox is the Cursor kind, not a
`cursor-cloud` kind.

**Composition source** — whether this BOM's composition was read from an agent
that exists, or from a declaration of one:

| Source | Meaning |
|---|---|
| `installed` | read from a place where the agent is provisioned and can run |
| `declared` | read from a declaration — a repo declares config, nothing is running |

Both are **explicit values**. `declared` is the pull-request-gate case the
reference Action already serves, and why declared results stay out of exposure
counts — which is precisely why it must not be encoded as a missing field. A
producer that dropped the property would turn potential exposure into actual.

**Where it runs is not in the document.** A workstation, a cloud sandbox, and a
managed agent service are three places, and telling them apart changes nothing a
BOM decides. Remediation routing, drift continuity, asset keying, and criticality
are facts about the *asset*, and the registration envelope carries them already —
the hosted asset row records the place as its own type. Coverage, the one BOM
property this axis feeds, splits on installed-versus-declared and not on which
place: an installed Cursor has the same plugin cache and the same
runtime-registration blindness in a sandbox as on a laptop.

### What proves an agent exists

Discovery is per kind, and a kind must state what constitutes evidence of
itself. The rule differs by source, and the difference is not cosmetic:

| Source | Evidence |
|---|---|
| `installed` | the runtime's own config root exists, or a control plane returns a record. An installed runtime with no configuration is a real agent with zero components |
| `declared` | a **file** the kind owns. An empty directory is not evidence — Git does not preserve one, so it is not a portable declaration |

That asymmetry matters for the zero-component case: it is reachable when
installed, and not reachable in a repo. A repo containing only ordinary package
manifests declares no agent at all, and emits no agent BOM.

A manifest that no kind owns exclusively — a bare `mcp.json` at a repo root, say —
is genuinely ambiguous, and this design removed the ownership model that would
have arbitrated it. See [One kind may read another's files](#one-kind-may-read-anothers-files).

### Why `agent_id` exists

**Cardinality is not BOM vocabulary.** It never appears in a document and no
consumer needs the word. It is a scanner-internal declaration on a kind (see
[Internals not visible in a BOM](#internals-not-visible-in-a-bom)), and it is
discussed here for one reason only: it decides whether that kind emits an
`agent_id`.

Some kinds have at most one agent in one place; others have many *of the same
kind* — an account holding any number of Bedrock agents, one `langgraph.json`
declaring several graphs, one CrewAI project declaring several agents. Only that
case needs a discriminator, and `agent_id` is it. Two coding agents on one laptop
is **not** this case: those are different kinds, so kind plus place already
resolves each.

**What it holds.** The identifier the kind's own surface uses to address that
particular agent — not a label a user can rename:

| Kind | Where the id comes from |
|---|---|
| AWS Bedrock agents | `agentId` from `GetAgent` — immutable, pattern `[0-9a-zA-Z]{10}`. **Not** `agentName`, which is a mutable display label |
| LangGraph | the key in `langgraph.json`'s `graphs` map — `{"researcher": "./pkg/a.py:graph"}` yields `researcher`. LangChain's own docs call this map "graph ID to path", and the key is what addresses the graph over the API once deployed, so renaming it is a breaking change for callers rather than a cosmetic edit |
| CrewAI | the agent declared in `agents/<name>.jsonc` |
| Claude Code | **absent** — one agent per place, so nothing to discriminate |

So three graphs in one `langgraph.json` produce three BOMs sharing
`agent_kind: langgraph` and differing only in `agent_id`: `researcher`,
`writer`, `critic`. See the third fragment under
[Sample Agent BOM](#sample-agent-bom).

**The human-readable label is separate**, and it goes in CycloneDX's own
`metadata.component.name` rather than in an `openaca:*` property — every component
carries a `name` already. Two fields, two jobs:

| | Holds | Bedrock | LangGraph | Claude Code |
|---|---|---|---|---|
| `openaca:agent_id` | the stable key | `ABCDEFGHIJ` | `researcher` | *absent* |
| `metadata.component.name` | what a person reads | `payments-triage` | `researcher` | `Claude Code` |

Where a kind's surface exposes both an immutable id and a renameable label they
differ; where it exposes one string, both carry it. A singleton kind has no
discriminator, so its `name` is the kind's own display label. `name` is never an
identifier — `bom-ref` and `agent_id` are.

The field is **named for an id, not a name** because it is part of the instance
key and drift pairs on that key. A renameable label there would make renaming an
agent move its key, so the diff would report a delete plus an add for an unchanged
composition.

A singleton kind **omits** the field rather than emitting it empty, and a kind
that uses it owes a canonicalisation rule (requirement 6 under
[What a kind spec must contain](#what-a-kind-spec-must-contain)) covering
character set, case, and slugging into a filename — Bedrock's ten alphanumerics
need none, a developer-chosen LangGraph key can contain anything.

V0 ships only singleton kinds, so nothing in V0 exercises same-kind multiplicity.
That path is validated on paper and by a synthetic test kind, and guarded by a
regression test before any real such kind ships.

### Identifying one agent takes two layers

A document deliberately carries no place *identity* — a hostname or cloud account
is exactly what the redaction pass strips (see [Privacy](#privacy-boundary)).
Identity of the place lives in the registration envelope instead:

| Layer | Carries | Answers |
|---|---|---|
| registration envelope | asset external id, asset type | **which** place |
| BOM content | `agent_kind`, `composition_source`, `agent_id` | **what sort of** agent, read from a running agent or a declaration |

So an agent's instance key is **(asset, kind, agent id)**. Composition
source is descriptive and *not* part of the key — the asset already implies the
place. The hosted asset row carries the same declared-versus-installed fact as
its own type, so the document's copy earns its place for the standalone case: a
BOM read off disk has no envelope beside it.

**How a consumer identifies one agent.** No single field does it, by design — the
document is de-identified, so identity is assembled from both layers:

| Part | Where it comes from |
|---|---|
| asset | the registration envelope; for a document on disk, wherever the file came from |
| `openaca:agent_kind` | the document |
| `openaca:agent_id` | the document, **when present** — absent for a kind with one agent per place, where asset plus kind already resolve |

Two documents agreeing on all three describe the same agent at two points in
time, and are comparable. Differing in any one, they are different agents — two
runtimes on one machine differ in the kind; two of fourteen Bedrock agents differ
in the agent id; the same runtime on two laptops differs in the asset. Nothing
else is part of the key: not `composition_source`, not the `openaca:target` path,
not the content hash.

Drift pairs on that key, then diffs components on `bom-ref`. This is the reading
the design is *for*; the hosted side does not implement it yet, since an asset
records a single latest BOM and a second agent would displace the first — see
[Backward compatibility](#backward-compatibility).

**An agent's kind is not its identity.** `openaca:identity` means one thing
format-wide: a specific logical component. Two Cursor agents are not two
occurrences of one agent — they are different agents sharing a type, one with
three MCP servers and one with seven. So the kind gets its own property. See
[ADR-0045](../adrs/0045-agent-identity-keying.md).

**Two workstations with identical configuration** are separate instances whose
compositions can be *compared*. Separate, because there are two things to
remediate. Comparable, because the path normaliser strips machine-specific roots,
so the same config yields byte-identical `bom-ref`s — deduplicate on composition
to count distinct configurations, count assets to count affected machines.

Comparable is not the same as *identical*, and the spec claims only the former.
Node-key equality covers the components a BOM carries; it says nothing about
settings that are not components — a model selection, permission rules,
environment, or system instructions. Two agents with the same component set can
still behave differently. Treating component-set equality as full configuration
equality would be a stronger claim than this design supports.

### Coverage

A BOM listing three MCP servers looks authoritative. It should not, unless we
could have seen a fourth. `openaca:composition_coverage` is the agent's own
statement about how much of its composition this scan could observe, reusing
ADR-0041's levels rather than inventing a parallel vocabulary:

| Level | Meaning |
|---|---|
| `complete` | every surface this runtime loads from is file- or API-declared, and the scan read all of them |
| `partial` | something is declared but unreadable, or may exist undeclared |
| `unknown` | no assessment made |

| Agent | Level | Why |
|---|---|---|
| Claude Code, installed | `complete` | every file deciding what it loads is readable |
| Cursor, installed | `partial` | servers arrive from plugins and from built-ins beyond the two well-known paths, and `vscode.cursor.mcp.registerServer()` registers more at runtime that no file records |
| Cursor declared in a repo | `partial` | for fewer reasons — nothing is installed, so no plugin cache and no runtime registration to be blind to |
| A managed agent service | *depends on its API* | if the control-plane record is exhaustive, `complete` — a tool being declared is all coverage asks. Whether such an API admits runtime additions is a fact its kind spec must establish |

Three things that table is showing, each worth stating on its own.

**Coverage resolves per source, not per kind** — rows two and three. The same
runtime is blind to different things depending on whether it is installed or only
declared: an installed Cursor loads servers a repo declaration cannot have, so
the same kind carries different coverage at each. *Where* it is installed makes
no difference.

The Cursor rows are measured, not assumed. On one machine Cursor loaded thirteen
MCP servers while `~/.cursor/mcp.json` declared two: nine came from plugins, each
with its own `mcp.json` under `~/.cursor/plugins/{local,cache}/`, and two were
built-ins attributed to the user config path but absent from that file. Cursor's
documentation records both the runtime registration API and that a disabled server
"won't load or appear in chat". Two consequences for a Cursor kind spec. The plugin
surface is file-declared, so missing it is scanner maturity rather than runtime
opacity — which keeps the level at `partial` either way (ADR-0046) but is a
different reason, and it means parsing only the two well-known paths finds two
servers of thirteen. The per-server enable state was the open question, left
unestablished here.

**That question has since been answered, and the answer is the harder one.**
[ADR-0052](../adrs/0052-cursor-agent-kind.md) records it: activation state is not
a file this investigation failed to locate, it is a **server call**. Local state
persists only enabled ids — numeric and nameless — and no local file records which
component they name.

The distinction is worth keeping because the two halves above age differently.
"Scanner maturity" is a debt: write the parser and coverage improves. "Not on
disk" is a ceiling: no parser closes it, and an offline scan cannot distinguish an
installed-but-disabled component from an installed-and-enabled one. A kind spec
that reports both as one undifferentiated gap tells a future implementer to keep
looking for a file that does not exist.

**Remoteness is not what hides composition; code is** — row four against a
framework agent. A managed service exposes its whole composition over an API,
while a framework running on your own laptop can hide an entire tool set in
Python. That inverts the usual intuition, and it means "managed" is no proxy for
"hard to scan."

**A kind that permits code-authored agents can never claim `complete`, even for a
project that declares everything in config.** Take CrewAI: a project can declare
its agents in `agents/*.jsonc`, and those are fully discoverable. But the same
framework also lets you construct agents in Python, and a scan that reads the
config files cannot prove they are the whole story — an agent built in code
leaves nothing to find. Since coverage is declared once per kind and cannot vary
by how an individual project happened to be written, it has to assume the least
observable way that kind can be used. The alternative is claiming `complete` for
every CrewAI project on the strength of the well-behaved ones.

**Coverage measures discovery, not matchability.** These are independent, and
conflating them overstates gaps. A component we can see but cannot resolve to an
advisory coordinate is *fully discovered* — a remote MCP server is inventoried
under its host-path identity with no OSV federation at all (ADR-0020), and that
does not make its agent `partial`. Likewise, the implementation *behind* a
declared tool is not part of the agent's composition: a managed service's action
group is a component because the agent can call it, while the function it invokes
is a separate artifact, exactly as an MCP server's source code is not composition.
Inventorying agent components and resolving library trees are different layers,
and the thesis says so.

**Coverage is a per-scan verdict, not a static lookup.** The table above is the
*baseline* a kind declares for a source — the best it could achieve. An
individual scan can only be worse: a manifest that failed to parse, a file that
could not be read, an API page that errored, a traversal excluded. Repo parsing
already counts and warns about parse failures without feeding them anywhere; that
signal has to downgrade coverage, or a scan that skipped a manifest still claims
`complete`. The emitted level is therefore `min(baseline, evidence)`.

**The name is deliberately qualified.** `openaca:capability_coverage` already
exists on individual components and draws from the same three levels (ADR-0041),
so an unqualified `coverage` would sit in one document beside it meaning something
else entirely: one is how much of *this agent's composition* the scan could see,
the other is how much of *one component's capabilities* were extracted. Different
subjects, same vocabulary — so the property says which.

Only the level is emitted. The gaps behind a `partial` verdict — activation
unobservable, registration unobservable, materialized at runtime — stay spec
analysis: they are derivable from (kind, source), which
the BOM already carries, and the vocabulary has a known hole — a surface the
scanner does not yet parse fits none of the three, since those are runtime
properties and that is scanner maturity.
See [ADR-0046](../adrs/0046-agent-coverage.md).

### Privacy boundary

An existing contract governs BOM content and this design must not weaken it: the
uploaded BOM replaces the scan target with a neutral literal, a redaction pass
rewrites every absolute path and strips URL paths and credentials, the content
hash is recomputed afterwards, and the backend **rejects** payloads containing
absolute paths. Hostname *is* sent — as the asset's external id in registration.

So the principle is sharper than "no machine information leaves": **BOM content is
de-identified; machine identity lives in the registration envelope.** Hence the
composition source in the document and place identity in the envelope. Agent kind and
node-key prefixes are machine-independent by construction and need no redaction.
An agent id for a managed agent is not machine data, but that does not make
it insensitive: `payments-triage` reveals business structure. It is the same
category as an MCP server name, which already ships, so it is not *newly*
sensitive — but it should be tested against the redaction contract rather than
assumed safe, and hashing it on upload stays available if a consumer needs it.

### One kind may read another's files

One runtime reading another's subagent directory, or a cross-tool convention
several runtimes read as equals, needs no ownership or precedence concept: the
file is a component in **each** reading agent's BOM. One BOM per agent removes the
attribution question an ownership model would have arbitrated.

This also settles two cases that need no new rule. A manifest **no kind owns
exclusively** — a bare `mcp.json` at a repo root — is a component in every reading
agent's BOM, exactly like any shared file; consumers deduplicate on identity. And
a kind with **two manifest formats for one component type** needs nothing either:
the occurrence key is `{normalised source manifest}#{locator}#{what}`, so two
declaration sites are two occurrences sharing an identity, while the same site
reached twice deduplicates by key.

Because the root label names the kind that *owns* the root rather than the kind
reading it, the file carries the **same identity and the same node key** in both
BOMs — correct, since it is one file. Node keys need only be unique within a
document (ADR-0038), and each agent is a separate graph and a separate document,
so there is no collision.

### Internals not visible in a BOM

A kind is a registry entry in scanner code — the generalisation of today's flat
manifest registry. It declares discovery, composition, its manifest patterns (as
polymorphic surface variants, so a control-plane kind holds no empty filesystem
fields), a posture-rule allowlist, cardinality, and coverage. Of that, only kind
and coverage reach a BOM; the rest never leaves the scanner.

Posture rule *reach* becomes structural — an agent's graph holds only its own
manifests — but rule *applicability* still needs declaring, since a settings key
can mean something different, or nothing, in another runtime.

## Evaluation

Three BOM structures against every kind-by-place situation.

**A** — one BOM per place; each component tagged with which agents use it. The
agent is a label, never an object.
**B** — one BOM per agent; the document's subject *is* the agent.
**C** — one BOM per place; the agent is a component row, components hang off it.

✓ fits · ~ works with a cost · ✗ doesn't fit · — doesn't discriminate

| # | Situation | A | B | C |
|---|---|---|---|---|
| | **installed, one machine** | | | |
| 1 | Claude Code alone | — 1 BOM | — 1 BOM | — 1 BOM |
| 2 | Claude Code **+** Cursor | ✓ 1 BOM, tags split them | ✓ 2 BOMs | ✓ 1 BOM, 2 agent rows |
| 3 | Only `.claude/agents/reviewer.md` exists; both load it | ✓ 1 row, tag = both | ~ a row in **both** BOMs | ✓ 2 occurrence rows |
| 4 | Both runtimes have their own copy; Cursor ignores Claude's | ✓ 2 rows, one tag each | ✓ 1 row each | ✓ 1 row per agent |
| 5 | Claude Code + Cursor + LangGraph (3 graphs) | ✓ 1 BOM, 5 in tags | ✓ 5 BOMs | ✓ 1 BOM, 5 agent rows |
| | **installed, a sandbox** | | | |
| 6 | Claude Code in CI — servers and hooks inline in workflow config | — 1 BOM | — 1 BOM | — 1 BOM |
| 7 | Claude Code on the web / Cursor Cloud Agent | — 1 BOM | — 1 BOM | — 1 BOM |
| 8 | Claude Code **+** Cursor in one sandbox | ✓ 1 BOM, tags split | ✓ 2 BOMs | ✓ 1 BOM, 2 rows |
| | **installed, a managed service** | | | |
| 9 | 14 Bedrock agents in one AWS account | ✓ tags name all 14 | ✓ 14 BOMs | ✓ 14 agent rows |
| 10 | One MCP server used by 5 of those 14 | ✓ 5 rows | ✓ server in 5 BOMs | ✓ 5 occurrences |
| 11 | 200 agents in one account | ~ 1 large BOM | ✓ 200 small BOMs | ✗ read one agent, parse all 200 |
| 12 | 3 LangGraph graphs from one image | ✓ 1 BOM | ✓ 3 BOMs | ✓ 3 agent rows |
| | **declared** (a repo) | | | |
| 13 | Repo declares only `.claude/` | — 1 BOM | — 1 BOM | — 1 BOM |
| 14 | Repo declares `.claude/` **and** `.cursor/` | ✓ tags split | ✓ 2 BOMs | ✓ 2 agent rows |
| 15 | Repo declares 3 LangGraph graphs | ✓ 3 in tags | ✓ 3 BOMs | ✓ 3 agent rows |
| | **over time** | | | |
| 16 | One of 14 agents changes, 13 don't | ✗ the BOM diffs — read tags to find which | ✓ exactly 1 of 14 diffs | ✗ the BOM diffs — walk to the agent row |
| 17 | Two laptops, byte-identical config | ✓ comparable via node keys | ✓ same kind, asset distinguishes | ✓ agent rows share a kind |
| 18 | Cursor installed, nothing configured yet | ✗ the agent is only a tag; with no components **Cursor is invisible** | ✓ a BOM with zero components still records it | ✓ a row with no children |

Of 18 situations, 4 do not discriminate:

| | ✓ fits | ~ costs | ✗ fails |
|---|---|---|---|
| **A** | 11 | 1 | **2** |
| **B** | 13 | 1 | **0** |
| **C** | 12 | 0 | **2** |

### Recommendation: B

One BOM per agent — the only structure with no failing situation, winning
the two that matter most in operation: knowing which agent changed (16) and
reporting an agent with nothing configured yet (18).

Construction builds **one graph per agent**, each with the agent as its single
target root. Attribution-by-containment, precedence, and scope classification all
work unchanged, since `scope_of`'s "an agent-component ancestor before the target
root" now means "before the agent root." Emission is then one BOM per graph, with the agent
as `metadata.component`.

**Not one combined graph with agent nodes.** `Graph.validate` rejects a node with
more than one parent, and `Graph.root` requires exactly one `target` node. A file
two runtimes read has the same owner-derived key in both, so under a combined
graph it would be one node with two agent parents — an invariant violation. Two
graphs keep the tree intact *and* keep the key identical across both BOMs, which
is the property that makes the shared file recognisable as one file.

B's one cost is situation 3: a file two agents read is emitted in both BOMs.
Correct rather than wasteful — it is genuinely in both compositions, and
ADR-0038 already permits shared identity across occurrences.

### Rejected

**A** — the agent is never an object, so an agent with nothing configured cannot
be reported and drift cannot localise to one agent of many. Per-agent facts would
need a parallel structure beside `components[]` with no edges. It also revives the
`attributed_to` property ADR-0037 replaced with graph-derived attribution.

**C** — rejected as the emission boundary: unwieldy at 200 agents, any change
diffs the whole document, and it forces the agent into `components[]` needing a
CycloneDX type for which none of `library`, `application`, or `framework` is
honest. It is not viable as the *internal* graph shape either — a combined graph
cannot hold a file two agents read without breaking the single-parent
invariant.

## BOM attribute changes

### Remove

| Property | Why |
|---|---|
| `openaca:agent_host` | mechanically derived from `runtime_hosts` when that list has one entry; its only reader is a test. A second encoding of one fact |
| `openaca:runtime_hosts` | the BOM's subject carries it. Four readers to re-source, all of them the "active in" field: the findings renderer, the mutable-install and skill-capability posture rules, and the SkillSpector posture projection |
| `openaca:target_type` | no value left to carry once every BOM is agent-rooted — `agent` restates that `agent_kind` is present, and `repo` names the invocation rather than the document. See [Why `target_type` goes](#why-target_type-goes) |

### Add, on `metadata.component`

| Property | Why it is not derivable |
|---|---|
| `openaca:agent_kind` | nothing else carries the runtime once identity is ruled out |
| `openaca:composition_source` | **required and explicit** — declared results stay out of exposure counts, so encoding `declared` as a missing property would let a dropped field flip potential exposure into actual. Categorical and non-identifying; place *identity* stays in the envelope |
| `openaca:agent_id` | discriminates same-kind agents in one place; only for kinds with that multiplicity |
| `openaca:composition_coverage` | ADR-0046 — the level only |

### Edit

| Property | Change |
|---|---|
| `openaca:target` | unchanged in meaning, and **kept alongside** `composition_source`: it records *where this agent's composition was read from* (machine-specific locally, a neutral literal on upload), while `composition_source` records whether that was a running agent or a declaration. Different facts, so no drift |
| `openaca:schema_version` | `0.4` → `0.5` |
| node-key root labels | `endpoint/<rel>` becomes `claude-code/<rel>`; each config root gets a label naming **the kind that owns it**. `project/<rel>` is unchanged |
| `metadata.component.name` | today the emitter puts `agent_kind` here, so every agent of one kind would share a name and three LangGraph graphs would all read `langgraph`. It becomes the agent's human-readable label — the kind's display label for a singleton, the agent's own label otherwise. Not `{kind}/{agent_id}`: `bom-ref` and the output filename already carry that pair |

The schema today constrains only `target_type` and `schema_version`, and
`target_type` is being removed — so `schema_version` becomes the sole constrained
property. Otherwise a property is validated as nothing more than a name/value
string pair. So the additions are
*permitted* without schema work — but they must not be left *unvalidated*, because
they are identity and pairing fields rather than annotations. Without rules, a
document could omit `agent_kind`, carry a bogus `composition_source`, duplicate a
property, or omit a required discriminator and still pass.

Required invariants, as conditional schema where expressible and linter rules
otherwise. One of them is neither: the `agent_id` rule needs the kind registry,
so it is a **scanner self-check that runs in the linter**, not a property of the
format. A third party cannot evaluate it from the document alone, and it is
marked below.

| Invariant |
|---|
| `metadata.component`'s `bom-ref` under the agent-root prefix ⟹ `agent_kind` present and non-empty |
| the same ⟹ `composition_coverage` present, one of the three levels |
| `composition_source` present, one of `declared` or `installed` — never absent |
| each `openaca:*` property appears at most once on a component |
| `agent_id` present iff the kind has same-kind multiplicity — required for those, forbidden otherwise. **Registry-dependent**: cardinality is scanner-internal, so this cannot be expressed as schema. Kept because a singleton kind emitting a discriminator means discovery is wrong |
| `metadata.component`'s `bom-ref` is consistent with `agent_kind` and `agent_id` |

**Labels name the root's owner, not the containing agent.** The label exists so
paths under different roots cannot collide within one scan. With two kinds there
are three roots — each runtime's config root, plus the project — so the label has
to say which root a path came from. The *kind* of the containing agent would add
nothing, since each BOM is already single-kind.

Naming labels after the owning root also fixes a gap that exists today: a file
one runtime compat-reads from another's config root is under neither its own
install root nor the project, so it currently falls through to an **absolute
path** — machine-specific, and something the redaction pass then has to catch.
Under kind-named labels it keys as `claude-code/agents/reviewer.md` in *both*
agents' BOMs, which is correct: it is the same file.

This renames every existing `bom-ref` — see
[Backward compatibility](#backward-compatibility).

### Why `target_type` goes

It survives today by answering two questions at once: *what is this document
about* and *how was it produced*. Splitting them is what removes it.

**What it is about** is now always the same answer — an agent — because a repo
scan emits agent-rooted BOMs too (see
[What changes in the scanner](#what-changes-in-the-scanner)). Before that, two
document shapes existed: one whose `metadata.component` is an agent, and one
whose `name` is the scanned directory with no kind and no coverage. A consumer
reading `metadata.component.name` got an agent in the first and a filesystem path
in the second, and `target_type` was the only field distinguishing them. With one
shape there is nothing left to distinguish, and `agent` would only restate that
`agent_kind` is present.

**How it was produced** is carried by `composition_source` instead, which is a
property of the agent rather than of the document. That is the distinction the
value `repo` was standing in for, and it is already load-bearing — declared
results stay out of exposure counts.

So the property is not merely redundant; in the new model it has no defined
value. A declared agent found by a repo scan would be `agent` by its subject and
`repo` by its invocation, and nothing in this design picks between them. Every
other metadata property has a determinate source — kind from the registry,
coverage from `min(baseline, evidence)`, the discriminator from the kind's own
surface. This one no longer does.

Its two readers are both ours, and neither loses information:

| Reader | Re-keys onto |
|---|---|
| `bom_lint`'s agent-metadata gate | `metadata.component`'s `bom-ref` prefix |
| `scan bom`'s inventory-tree selector | `composition_source: declared` — which is what the repo-grouped tree actually wants to know |

The **reader stays** for stored `0.4` documents, exactly as with `agent_host`:
stop the write, not the read.

### Considered and declined

- **Keeping `target_type` as a document-shape tag** — it has a shape to tag only
  while repo scans emit a directory-rooted inventory. Unifying the shapes is a
  prerequisite of this design rather than extra scope, and once unified the tag
  has no second shape to name. Removing it *before* unifying would be the wrong
  order: consumers would have to infer shape from a missing `agent_kind`, and
  absence has many causes.
- **A separate identity for the agent** — `openaca:identity` means a specific
  logical component; a runtime kind is a category. Two meanings in one field.
- **`openaca:definition_digest`** — derivable from the component set, and would
  need the same post-redaction recomputation `content_hash` gets, plus a
  canonicalisation contract the scanner and backend must hold forever.
- **`openaca:composition_coverage_reasons`** — derivable from (kind, source), and the
  vocabulary has a known hole. Additive later.
- **Placement identity** — the redaction contract keeps it in the envelope.

## Sample Agent BOM

**Illustrative — this shows two kinds so the shared-file and per-agent-coverage
properties are visible. Only the first document is emitted by anything shipping
here.** Claude Code has a plugin bundling an MCP server; a second runtime has its
own MCP server and also reads `.claude/agents/reviewer.md`.

```jsonc
// 1 of 2 — coverage complete, no agent_host / runtime_hosts anywhere
{
  "bomFormat": "CycloneDX", "specVersion": "1.7", "version": 1,
  "metadata": {
    "properties": [
      { "name": "openaca:schema_version", "value": "0.5" }
    ],
    "component": {
      "bom-ref": "root/claude-code", "type": "application", "name": "Claude Code",
      "properties": [
        { "name": "openaca:agent_kind",          "value": "claude-code" },
        { "name": "openaca:composition_source",  "value": "installed" },
        { "name": "openaca:composition_coverage",            "value": "complete" }
      ]
    }
  },
  "components": [
    { "bom-ref": "claude-code/settings.json#enabledPlugins#plugin/github",
      "type": "application", "name": "github",
      "properties": [
        { "name": "openaca:identity",       "value": "plugin/github" },
        { "name": "openaca:component_type", "value": "plugin" },
        { "name": "openaca:scope",          "value": "agent-component" } ] },
    { "bom-ref": "claude-code/plugins/github/.mcp.json#github#mcp-server/github",
      "type": "application", "name": "github", "version": "0.4.1",
      "purl": "pkg:npm/%40modelcontextprotocol/server-github@0.4.1",
      "properties": [
        { "name": "openaca:identity",       "value": "mcp-server/github" },
        { "name": "openaca:component_type", "value": "mcp_server" } ] },
    { "bom-ref": "claude-code/agents/reviewer.md#reviewer#agent/reviewer",
      "type": "application", "name": "reviewer",
      "properties": [
        { "name": "openaca:identity",       "value": "agent/reviewer" },
        { "name": "openaca:component_type", "value": "agent" } ] }
  ],
  "dependencies": [
    { "ref": "root/claude-code", "dependsOn": ["…#plugin/github", "…#agent/reviewer"] },
    { "ref": "…#plugin/github",   "dependsOn": ["…#mcp-server/github"] }
  ]
}
```

```jsonc
// 2 of 2 — coverage partial, and reviewer.md appears again
{
  "metadata": {
    "properties": [
      { "name": "openaca:schema_version", "value": "0.5" }
    ],
    "component": {
      "bom-ref": "root/runtime-b", "type": "application", "name": "Runtime B",
      "properties": [
        { "name": "openaca:agent_kind",          "value": "runtime-b" },
        { "name": "openaca:composition_source",  "value": "installed" },
        { "name": "openaca:composition_coverage",            "value": "partial" }
      ]
    }
  },
  "components": [
    { "bom-ref": "runtime-b/mcp.json#playwright#mcp-server/playwright",
      "type": "application", "name": "playwright",
      "properties": [ { "name": "openaca:identity", "value": "mcp-server/playwright" } ] },
    { "bom-ref": "claude-code/agents/reviewer.md#reviewer#agent/reviewer",
      "type": "application", "name": "reviewer",
      "properties": [ { "name": "openaca:identity", "value": "agent/reviewer" } ] }
  ],
  "dependencies": [
    { "ref": "root/runtime-b", "dependsOn": ["…#mcp-server/playwright", "…#agent/reviewer"] }
  ]
}
```

What the pair demonstrates:

- **the agent is the subject** — `metadata.component`, not a `components[]` row,
  so it needs no CycloneDX type for "configuration context"
- **`agent/reviewer` is byte-identical in both BOMs** — same identity *and* same
  node key, because the label names the root's owner (Claude Code) rather than the
  reading runtime. It is one file, and both documents say so. Safe because each
  agent is a separate graph and a separate document
- **different coverage per agent** — the fact that had nowhere to live under A
- **attribution by containment** — the MCP server hangs off the plugin, not off
  the agent, via `dependencies[]` and no parent property
- **no `agent_host` or `runtime_hosts`** — the document's subject carries it
- **no place identity** — only `composition_source: installed`; which machine is
  in the upload envelope
- **`name` is the label, not the key** — `Claude Code` reads to a person while
  `bom-ref` and `agent_id` stay machine-facing

A **same-kind multiplicity** kind adds the discriminator, and is the only case
that does:

```jsonc
"component": {
  "bom-ref": "root/bedrock-agent/ABCDEFGHIJ",
  "type": "application", "name": "payments-triage",
  "properties": [
    { "name": "openaca:agent_kind",          "value": "bedrock-agent" },
    { "name": "openaca:agent_id",            "value": "ABCDEFGHIJ" },
    { "name": "openaca:composition_source",  "value": "installed" },
    { "name": "openaca:composition_coverage",            "value": "partial" }
  ]
}
```

Thirteen other agents in that account carry the same `agent_kind` and differ only
in `agent_id` — which is why identity could not have been the kind. The immutable
`agentId` keys the agent while the renameable `agentName` stays a display label,
so renaming the agent in AWS changes what a reader sees and not what drift pairs
on.

## What changes in the scanner

- The endpoint seed becomes the first kind's compose function.
- `build_graph`'s `mode` string is **not extended**; the agent path is a
  separate `build_agent_graph` taking an agent instance. A string enum growing a
  case per surface is how the single-runtime assumption got baked in. Removing
  `mode` outright waits on the remote collector, which still calls
  `build_graph(..., mode="endpoint")`, so that branch is live rather than dead.
- The flat manifest registry splits per kind, reached through a surface.
- Scan and BOM commands emit as many BOMs as agents found — which is one, until a
  second kind ships. **Findings stay a
  single flat list**, so the SARIF writer and the reference Action's contract —
  one SARIF path, one exit code, one output value — are unaffected.
- **`scan repo` and `bom repo` emit agent-rooted BOMs too.** A repo scan
  discovers the agents a tree declares and emits one BOM each with
  `composition_source: declared` — not one document rooted at the scanned
  directory. The evaluation assumes this
  (situations 13–15); stating it here is what makes the document shape uniform and
  lets `target_type` go. A tree declaring no agent emits nothing, per
  [What proves an agent exists](#what-proves-an-agent-exists), so a repo of ordinary
  package manifests is unchanged in producing no agent BOM.
- **Subcommand names are unchanged.** `scan endpoint` and `bom repo` keep their
  names. A subcommand names *where to look*, and each produces the composition
  source rather than restating it: `endpoint` yields `installed`, `repo` yields
  `declared`. The mapping is not one-to-one going forward — a future account-scoped
  input for a managed kind would be a third subcommand also yielding `installed`.
  `endpoint` is also the security-industry term for a device, and it sits
  consistently beside future scopes like an account or a cluster. Renaming would
  cost a permanent deprecated alias under ADR-0006's no-fallback rule, for a
  cosmetic gain.
- The duplicated endpoint config-dir resolver is **not** consolidated by this
  change. Three copies remain — `tools/scan.py`, `tools/remote/cli.py`, and the
  Claude Code kind's own `config_root` — and folding them into one
  agent-selection helper is deferred. Today's rule stands: an explicit
  `--config-dir` is valid only when exactly one agent resolves.
- Parsers stop knowing the runtime; the agent owns that fact.
- **Findings gain an agent association.** They stay one flat list, but a component
  loaded by two agents is **two occurrences and therefore two findings** — one per
  agent, each carrying the agent it belongs to. Collapsing them would make
  per-agent counts, remediation targets, and "which agent is affected" wrong in
  exactly the case this design exists to fix. Text, JSON, and SARIF each carry the
  association; the exit code is unchanged, since it aggregates severity.
- **The renderer's `host_surface` becomes per-agent.** It is hardcoded to a
  literal runtime name in the endpoint path today, and it is not merely internal:
  it prints as the text card's `host surface:` row *and* is emitted as
  `target.host_surface` in JSON output. With one card per agent it derives from
  the agent's kind.
- **Removing a property means stopping the write, not the read.**
  `component_refs_from_cyclonedx` restores `agent_host` and `runtime_hosts` into
  a ref's `extra`, and stored `0.4` BOMs still carry them, so the reader stays
  and only the emitter drops them.

Preserved: `declared` means potential exposure, not actual — only installed BOMs
are cloud assets and contribute to exposure counts. Components with no agent
ancestor stay dropped (SCA's layer). Dormant and disabled config stays
undiscovered.

## Emitting many documents

Two contracts assume one document per invocation and have to change.

**Output sink.** `--output` is optional today and **stdout is the default**, with
`click.Path(dir_okay=False)` explicitly rejecting a directory. So a
directory-only flag would not cover the default case at all.

The governing rule: *a consumer must never need to know the agent count in
advance to parse the output.* That rules out a conditional shape — one document
sometimes, a wrapper other times.

| Sink | Behaviour |
|---|---|
| stdout (default) | **NDJSON** — one CycloneDX document per line. One agent is a single line, still valid JSON, so `jq` and `json.load` keep working; many agents are line-wise and self-describing |
| files | **`--output-dir <dir>`** — one file per agent, `<kind>[--<definition-name>].cdx.json`. Uniform for one or many |

`--output` is **deprecated, not removed**: it keeps working and errors with a
pointer to `--output-dir` only when more than one agent resolves. A user
following current documentation never sees that error; only a legacy invocation
does, and only when a single path genuinely cannot hold the result.

That break is forced rather than cosmetic — a single file path cannot hold N
documents — which is the line for churning a shipped contract at all. It is why
this changes and the `endpoint` subcommand does not.

Two consequences to accept. Single-agent stdout becomes compact rather than
`indent=2`, which golden files and eyeball-diffing will notice. And filenames
need a filesystem-safe slug of `agent_id` — case, Unicode, separators, and
length are stricter constraints than the instance key needs, so the filename is
slugified while the key keeps the raw value — a rule a same-kind-multiplicity
kind has to define (see [What a kind spec must contain](#what-a-kind-spec-must-contain)).

**Diff pairing.** Diffs match components on `bom-ref` within one document pair.
With many BOMs per scan, the **caller pairs and the diff primitive stays
singular**: pair on (asset, kind, agent id) from metadata, diff each pair
with the existing function, and report an unpaired document as an added or
removed agent. This mirrors `build_agent_bom`, which also stays one-document with
plurality in its callers — so no existing diff behaviour changes.

## Migrating Claude Code

Claude Code is the only kind implemented today, so migrating it is the smallest
useful test of this design. The emitted diff for a Claude-Code-only machine:

| | Today | After |
|---|---|---|
| `target_type` | `endpoint` | **removed** |
| `metadata.component` bom-ref | `openaca:target` | `root/claude-code` |
| added metadata properties | — | `agent_kind`, `composition_source: installed`, `composition_coverage: complete` |
| removed component properties | `agent_host`, `runtime_hosts` | — |
| node-key root label | `endpoint/<rel>` | `claude-code/<rel>` |
| `metadata.component` name | the scanned config path | `Claude Code` |
| `components[]` and `dependencies[]` | | **structurally identical** |
| `schema_version` | `0.4` | `0.5` |

Order matters, because the linter validates metadata against the schema:

1. **Schema first** — add `0.5` to the `schema_version` enum, stop emitting
   `openaca:target_type` while keeping `target_info_from_cyclonedx` for stored
   documents, and re-key `check_agent_metadata` onto the root `bom-ref` prefix.
   Until this lands, `bom_lint` rejects every agent BOM.
2. **Contract, no behaviour change** — the kind registry, with Claude Code
   registered and today's endpoint seed as its compose function.
3. **Graph** — `build_graph` takes an agent instance and returns one graph per
   agent. No output change yet; the single-kind case produces one graph exactly as
   today.
4. **Metadata** — the agent becomes `metadata.component`, named for the kind's
   display label; `agent_host` and `runtime_hosts` come off the components,
   `target_type` stops being written, and `agent_kind`, `composition_source`, and
   `composition_coverage` go on the metadata component (`agent_id` stays absent for a
   singleton kind). Still one document per scan while one kind ships.
5. **Output cardinality** — `scan` and `bom` emit per agent — the repo path
   included, discovering declared agents rather than rooting at the directory —
   which is where the multi-document output contract lands (see
   [Emitting many documents](#emitting-many-documents)).
6. **Findings and renderer** — agent association on findings; `host_surface` per
   agent; the "active in" field re-sources from the agent rather than the removed
   `runtime_hosts`.
7. **Golden file** — recapture and review against the table above. Anything else
   in that diff is a regression.

Steps 3 through 6 are deliberately separate rather than one "emitter" step: each
changes a different contract — internal graph shape, document metadata, output
cardinality, and finding projection — and bundling them makes a regression hard to
localise.

The expensive part of this change is not Claude Code. It is Fleet.

## Backward compatibility

### Safe, verified by inspection

| | Why |
|---|---|
| Removing `agent_host` and `runtime_hosts` | Fleet reads neither, nor `active_in`. The only in-repo reader of `agent_host` is a test; `runtime_hosts` has four real readers — the findings renderer, the mutable-install and skill-capability posture rules, and the SkillSpector posture projection — all of them the "active in" field, and all re-sourced from the agent through one shared helper |
| Removing `target_type` | both readers are ours and neither loses information (see [Why `target_type` goes](#why-target_type-goes)). Fleet reads it nowhere — it keys on `openaca:target` as the root ref, and only a test passes it to the builder. The reader stays for stored `0.4` documents |
| Stored `0.4` BOMs stay readable | nothing in the scanner or in Fleet gates on `schema_version` |
| `scan bom` on either version | `graph_from_cyclonedx` reads whatever bom-ref `metadata.component` carries; `openaca:target` is only the fallback for documents without one |
| The reference Action | findings stay one flat list, so one SARIF path, one exit code, one output value — `action.yml` is untouched |
| Fleet ingestion | `source` stays `endpoint` and `asset_type` stays `endpoint`; an agent BOM is an ordinary BOM row |
| The `openaca.core` facade (ADR-0028) | `build_agent_bom` takes keyword-only arguments after `refs`, so the four agent arguments are additive with defaults and no consumer signature breaks. `BOMComponent` carries only a ref and a `bom-ref` — no runtime fields to lose. `AgentBOM` is not itself an exported name, so new fields on it are invisible to consumers, who use `component_refs()` and `to_cyclonedx()` |
| `target.host_surface` in JSON output | It derives from the agent kind's `display_name`, a human-facing label — for Claude Code that is still the literal `"Claude Code"` it emits today, since the machine-readable identity travels separately as `openaca:agent_kind`. Cardinality is unchanged while one agent scans at a time |

### Breaks once, deliberately

**Every `bom-ref` changes** with the root-label rename. Diffing matches on
`bom-ref`, so the first scan after upgrade reports every component removed and
re-added, and earlier diff history is not comparable across the boundary.

This does not yet apply to uploaded BOMs. The remote collector still calls
`build_graph(config_dir, mode="endpoint")`, so an upload keeps `endpoint/` labels, the
`openaca:target` root, and — as the one remaining emitter of it — `target_type:
endpoint`. Locally emitted and uploaded `bom-ref`s therefore do not agree, and drift
computed on the hosted side is not comparable with a local diff. Migrating the collector
takes the same one-time break; it is specified separately in
[Collector Agent-Rooted Uploads](collector-agent-rooted-uploads.md).

A version-aware diff shim was considered and rejected: it is migration code to
carry and later retire, in order to suppress a one-time event that is noisy
rather than harmful — no data is lost, and the hosted side is not yet operating
at scale. Document it in release notes instead.

### The hosted displacement window

An asset's *latest BOM* is a single column, set on every upload, and it anchors
all three current-state queries plus four dashboard queries. Uploading two agent
BOMs means two requests, and **the second displaces the first** in every
current-state view. The database accepts both rows; the console silently shows
one agent and no error is raised anywhere.

So "agent BOMs ingest under the existing asset types" is true of ingestion and
false of current state.

**Phasing.** While one kind ships, a machine has exactly one agent, so a sync
sends one upload and nothing is displaced. The problem above is reachable only once a
second kind exists.

**Resolution.** Per-agent current state is being built on the hosted side in parallel
with the collector's migration, so the two arrive together and neither waits on the
other. The collector therefore sends one upload per discovered agent with no guard
against the displacement window — [ADR-0050](../adrs/0050-collector-upload-cardinality.md)
records why a guard was rejected. How the hosted side keys per-agent current state is
its own decision and belongs in a hosted-side ADR, not here.

### Gated by kind shape

Two parts of this design are specified only for the shape the first kind
happens to have. Neither is a per-kind authoring decision — both are mechanism
this document defines and does not yet cover — and neither is answerable without
the real surface in hand. Each gates the first kind of its shape, not the second
kind generally.

**A kind with no config root has no node-key scheme.** Root labels name the
config root a path came from, and node keys are those labels (ADR-0045). A
control-plane kind reads no filesystem, so it has no root to label and its keys
need a different basis — most likely the resource id. **Hard trigger:** resolve
before the first control-plane kind ships.

**A kind with same-kind multiplicity has no `agent_id` canonicalisation.** The
id is part of the instance key *and* of an output filename, so it needs rules
for case, Unicode normalisation, whitespace, length, reserved characters, and
duplicates. Leaving this per kind would make the instance key's semantics vary by
kind, which is the opposite of what ADR-0045 keys on. The likely shape is the raw
id for the key and a filesystem-safe slug for the filename.

Naming the field for an id rather than a name already answers the harder half of
this — rename no longer reads as a delete plus an add, because a display label is
never the key. How much canonicalisation is left depends on the kind: a Bedrock
`agentId` is ten alphanumerics and needs none, while a developer-chosen LangGraph
map key can contain anything. **Hard trigger:** resolve before the first
many-per-place kind ships.

## What a kind spec must contain

Four requirements the mechanism imposes, from cross-checking a real per-kind
surface audit against this contract:

1. **Every path the runtime reads, including another runtime's** — an audit of
   only what a runtime owns misses compatibility reads and cross-tool
   conventions, both real components in that agent.
2. **An observability gap per surface.** "Fully supported" is not "fully
   observable": a surface can be completely parsed and still hide activation
   state or admit runtime registration.
3. **A composition source per claim.** Surfaces and coverage resolve per source,
   so a support claim must say for which.
4. **A split verdict for anything deferred.** Deferred by schedule and deferred
   pending a taxonomy ADR are different states — component types are a closed set
   (ADR-0019/0031) and source ecosystems are governed too, so a surface with no
   counterpart in either needs an ADR before a parser is worth writing.

Two further prerequisites are not requirements on a kind spec but unresolved
parts of this mechanism, gated by kind *shape* rather than kind count — see
[Gated by kind shape](#gated-by-kind-shape).

## Out of scope

- Inline MCP servers and hooks in CI workflow configuration remain unparsed — a
  recorded gap, not a scope change. Relatedly, whether a coding agent's CI action
  materialises a config directory on a runner is **unverified** against that
  action's source; it decides whether a CI runner is scannable in place or only
  from the repo that seeds it. Neither matters while only installed and declared
  sources ship.
- Hosted re-rooting — agent rows, latest-BOM-per-agent, and per-agent current state.
  Built in parallel on the hosted side; neither it nor the collector waits on the other.
- The collector's own migration to agent discovery and agent-rooted uploads, specified
  separately in [Collector Agent-Rooted Uploads](collector-agent-rooted-uploads.md).
- Any kind beyond Claude Code. This change migrates the one kind that exists;
  adding a second is a separate change that amends the parser set in `CLAUDE.md`
  and the thesis roadmap then. Managed and framework agents appear here only as
  evaluation exemplars, written as kind declarations in ADR-0044. **Cursor is
  that separate change** — see [Cursor Agent Kind](cursor-agent-kind.md) and
  [ADR-0052](../adrs/0052-cursor-agent-kind.md); this document stays
  mechanism-only and is not amended by it.
- Renaming either existing use of "scope". `openaca:scope` (composition
  classification) and `openaca:plugin_scope` (user versus project) already
  collide today; the composition source is a third scope-like idea but does not
  create the problem. Renaming a shipped property is churn this change does not need — a
  known wart, recorded rather than fixed.
