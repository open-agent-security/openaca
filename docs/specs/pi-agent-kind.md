# Pi Agent Kind — Surface Audit

Companion to [ADR-0067](../adrs/0067-pi-agent-kind.md) (composition, identity,
trust, and scope) and [ADR-0068](../adrs/0068-pi-declaration-surface-classification.md)
(declaration classification). The mechanism is [Multi-Agent Support](multi-agent-support.md);
delivery is tracked in [Plan 047](../plans/047-pi-agent-kind.md).

This is the per-kind contract: configuration paths, selection semantics,
composition sources, posture applicability, and observability limits. It follows
the [four requirements for a kind spec](multi-agent-support.md#what-a-kind-spec-must-contain),
including shared and explicitly configured cross-runtime paths. Implementation
tasks and completion history belong in the plan.

## What "Pi" means here

The Pi coding agent is one singleton kind, `pi`, with display name `Pi` and BOM
root `root/pi`. Interactive, print, JSON, and RPC modes do not create separate
kinds. The scanner describes file-declared composition; it does not observe a
running Pi process or reconstruct an SDK-created agent.

Packages become `plugin` containers. Their extensions, skills, prompt templates,
and themes become `extension`, `skill`, `command`, and `theme` components.
Standalone resources attach directly to the agent root. A Pi extension is a
code entry point, not a declaration of every tool or command its code registers.

## Evidence standard

**Audited version: Pi 0.85.1.** The implementation plan used that release's
shipped JavaScript. This spec cross-checks the corresponding versioned upstream
source and documentation; it does not claim compatibility with every later Pi
release or verification on every operating system.

| Evidence | Establishes |
|---|---|
| [Package manager source](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/package-manager.ts) | Source parsing, package identities, install locations, discovery, filters, and deltas |
| [Resource loader source](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/resource-loader.ts) | Resource loading, canonical-path deduplication, name collisions, and context files |
| [Trust store](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/trust-manager.ts) and [trust resolution](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/project-trust.ts) | Saved decisions, ancestor lookup, and invocation/extension overrides |
| [Configuration paths](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/config.ts) | Agent-root relocation and runtime data paths |
| [Settings](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/settings.md), [packages](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/packages.md), and [skills](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/skills.md) | Public configuration and resource contracts |
| [Usage](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/docs/usage.md#design-principles) | Extension-based workflows; no built-in MCP surface |

Where examples and executable behavior disagree, the versioned source governs.
In particular, settings documentation shows bare package names, but `parseSource`
requires `npm:` for npm. Bare names fall through to local paths. OpenACA must not
assign npm identity to those declarations merely because they resemble packages.

Pi's use of "pinned" for a Git tag describes its update behavior. OpenACA's
mutable-reference rule asks whether the reference can change: a tag remains
mutable; a full commit SHA does not. These are different contracts.

## Robustness bar

The goal is a correct static inventory for ordinary file-configured installations.
Missing an ordinary resource, selecting a shadowed package, crossing a repository
boundary, or assigning the wrong identity is a correctness issue. So is presenting
untrusted project resources as installed composition.

Selection is not proof of successful loading or execution. The scanner does not
install packages, import extension code, resolve an interactive trust prompt, or
repair a stale install. Unsupported patterns and unreadable resources must not
justify a claim of complete coverage. Exhaustive runtime validation and execution
analysis are outside this contract; specific limitations are recorded below.

## Config roots and discovery

Use `A` for the agent root and `P` for the selected project directory:

| Root | Resolution |
|---|---|
| `A` | `$PI_CODING_AGENT_DIR`, otherwise `~/.pi/agent` |
| Project configuration | `P/.pi` |
| Shared global skills | `~/.agents/skills`, independent of `A` |
| Shared project skills | `.agents/skills` at `P` and each ancestor through the repository root; through the filesystem root when no repository root is found |

Installed discovery requires `A` to exist as a directory. Shared skills alone do
not prove an installed Pi agent exists. Declared discovery requires Pi evidence
inside the scan target: `.pi/settings.json`, supported native resource files,
shared `.agents/skills` files, or a `package.json` containing a `pi` object or the
`pi-package` keyword. An ordinary npm manifest is not Pi evidence.

Managed package trees (`node_modules`, `.pi/npm`, `.pi/git`) do not independently
declare more projects. A Pi package repository is composed as a package;
embedded example and fixture projects do not create additional Pi surfaces.

Classify a manifest by its loading surface before making a package row. A
`package.json` inside native `.pi` resources or shared `.agents/skills` belongs
to that project's resource surface; it does not independently create a plugin
container. In an extension directory, its `pi.extensions` entries can establish
project evidence even when they point outside the native directory. Other resource
fields do not become enabled merely because that manifest exists. A directory
explicitly referenced in `packages[]` still takes the package path through the
selector, even if the directory is also below a native root.

Resolve all project package declarations before composing standalone package
repositories. An in-repository package selected by settings is represented by
that configured occurrence, not by a second implicit container from its manifest.
Compare resolved package roots so aliases do not create an extra container. A
selected package's own project resources remain independently eligible for the
project pass; suppressing the implicit container does not suppress those files.

### CLI surface

```bash
openaca scan endpoint --kind pi --project . --include-posture
openaca bom endpoint --kind pi --output pi.cdx.json
openaca scan repo --target . --include-posture
openaca bom repo --target . --output-dir boms
```

Endpoint commands accept `--kind pi`; repository commands discover kinds
automatically and have no kind selector. Without an endpoint kind selector, Pi
participates alongside other installed kinds. An absent Pi agent directory yields
no Pi agent, not a fallback to another kind's root.

`--kind pi --config-dir ...` is rejected. Relocating `A` does not relocate
`~/.agents/skills`, so that flag could not specify a coherent foreign-home target
([ADR-0054](../adrs/0054-per-kind-root-override.md)). Pi's native environment
variable remains supported with its actual, narrower meaning.

Pi endpoint source units count enabled extensions, skills, prompts, and themes,
including standalone and shared resources. Package containers remain inventory
even when they contribute no enabled files; they are not additional source units.

## Surface audit

`declared` means repository evidence bounded by the target. `installed` means
local installation evidence plus an optional project. Neither means a trace of
what executed. Every included surface below has a limit on what can be inferred.

| Surface and paths | Declared source | Installed source | Observability limit |
|---|---|---|---|
| Settings: `P/.pi/settings.json`, `A/settings.json` | Project declarations within target | Global plus trusted project declarations | CLI and SDK overrides are not observed |
| Packages: settings `packages[]` and package `package.json` | Pi package repositories and referenced contents within target | Selected global/project installs and local sources | A declaration need not have an install; installed contents can differ from the requested ref |
| npm installs: `A/npm/node_modules/<name>`, `P/.pi/npm/node_modules/<name>` | Referenced, readable installs within target only | Scope-specific package manifest and resources | No install, dependency resolution, or transitive dependency inventory |
| Git installs: `A/git/<host>/<path>`, `P/.pi/git/<host>/<path>` | Referenced, readable installs within target only | Scope-specific checkout and package resources | No checkout reconciliation or attestation that HEAD matches the configured ref |
| Extensions: `A/extensions`, `P/.pi/extensions`, manifest/settings paths | Within target | Global and trusted project resources | Entry points are visible; dynamically registered tools, commands, hooks, and resources are not |
| Skills: `A/skills`, `P/.pi/skills`, manifest/settings paths | Within target | Global and trusted project resources | Selection does not prove a skill was offered, invoked, or executed |
| Shared skills: `~/.agents/skills`, project/ancestor `.agents/skills` | Roots within target only; no outside ancestors or home | Global plus trusted project ancestry | Other homes and invocation-only paths are not observed |
| Prompt templates: `A/prompts`, `P/.pi/prompts`, manifest/settings paths | Within target | Global and trusted project resources | A selected template is not an observed command invocation |
| Themes: `A/themes`, `P/.pi/themes`, manifest/settings paths | Within target | Global and trusted project resources | Inventory records available resources, not the active UI theme or full runtime schema validation |
| Saved trust: `A/trust.json` | Not read | Gates project composition | CLI overrides, extension decisions, and interactive choices may supersede saved state |
| Global `defaultProjectTrust` in `A/settings.json` | Not a repository posture surface | Trust fallback and posture input | Describes undecided-project policy, not a trust decision for a particular directory |

### Declared composition

Repository composition reads no user settings or trust store. It inventories what
the repository declares, without asserting that a user trusted or installed it.
Referenced files must remain inside the scan boundary after resolving symlinks;
gitignore exclusions apply unless explicitly disabled by the scan option.

Package declarations remain visible when their install is missing or outside the
target, with `installed=false` and a coverage gap. The scanner does not fetch the
missing package to fill that gap. Native resource directories work without a
settings file. A Pi-bearing package manifest supplies a package container and
its resource declarations, not a license to scan every fixture beneath it.

### Installed composition and trust

Global configuration and resources are eligible independently of project trust.
For a selected project, the static trust resolver canonicalizes its directory and
looks for the nearest saved boolean in `A/trust.json`, walking ancestors. `null`
does not decide trust and permits ancestor lookup. Saved `true` or `false` wins
over the global default.

| Evidence after ancestor lookup | Static verdict | Project resources |
|---|---|---|
| Saved `true` | Trusted | Included |
| Saved `false` | Untrusted | Excluded with gap |
| No saved boolean; global `always` | Trusted | Included |
| No saved boolean; global `never` | Untrusted | Excluded with gap |
| No saved boolean; `ask` or no default | Unresolved | Excluded with gap |
| Unreadable or malformed trust store | Unresolved | Excluded with gap; do not treat it as an empty valid store |

A missing trust file is an empty store, so the default can apply. Project settings
cannot grant their own trust: `defaultProjectTrust` is global-only. Exclusion
removes project settings, native resources, project packages, and shared project
skill roots from installed composition together.

Pi can resolve trust through `--approve`/`--no-approve`, an extension's
`project_trust` event, or an interactive prompt. OpenACA cannot infer these from
files. Under `ask`, non-interactive Pi refuses undecided project resources;
OpenACA reports unresolved because it does not know the invocation mode. Even a
saved allow is evidence about the static fallback, not proof of a live session's
decision.

## Resource selection

Settings expose five arrays. `packages` accepts strings or objects with `source`,
optional resource filters, and optional `autoload`. `extensions`, `skills`,
`prompts`, and `themes` accept strings naming local paths or selection patterns,
not package objects. Relative paths resolve against the declaring settings
directory; package-manifest paths resolve against the package root.

```json
{
  "packages": [
    "npm:@example/resources@1.2.3",
    {
      "source": "git:github.com/example/resources@v1",
      "extensions": ["extensions/*.ts", "!extensions/legacy.ts"],
      "skills": []
    }
  ],
  "skills": ["../shared-skills"]
}
```

### Discovery and manifests

- Extensions discover `.ts`/`.js` files and directory entry points declared by
  `pi.extensions` or `index.ts`/`index.js`. Native extension discovery is shallow;
  it is not a recursive walk of arbitrary source code.
- Skills discover `SKILL.md` directories recursively and direct Markdown skills
  in native skill roots. Shared `.agents/skills` ignores root Markdown files but
  supports nested Markdown skills. A nonempty frontmatter description is needed
  for enabled selection; a retained invalid skill has `enabled=false` and a gap.
- Native prompts and themes discover immediate `.md` and `.json` files.
  Explicit directory and package resource paths can expose nested resources.
- Resource traversal accounts for hidden paths, ignore files (`.gitignore`,
  `.ignore`, `.fdignore`), canonical-path aliases, and cycles. Repository boundary
  and gitignore rules also constrain the resulting graph.

A string package declaration with a `pi` manifest uses that manifest's resource
arrays; without one, convention directories supply resources. A local file can
be an extension package; a local directory without a Pi manifest or resource
directories can itself be an extension entry point.

The object/filter path has distinct fallback rules in Pi's package manager:
an omitted type uses its manifest list when present, otherwise its convention
directory. An explicit filter enumerates manifest candidates, falling back to
the convention directory when that manifest list is absent or empty. Do not
collapse these paths into a generic recursive settings merge.

### Filters, overrides, and precedence

For regular package filters, omission leaves the type's default resources
selected; `[]` disables that type. Positive patterns select candidates,
`!pattern` excludes, `+path` force-includes an exact path, and `-path`
force-excludes an exact path. Filters operate on discovered candidates; they do
not execute code to discover more. OpenACA supports the common `*`, `**`, and `?`
patterns. Advanced bracket, brace, and parenthesized package patterns produce a
coverage gap rather than a promise of full glob compatibility.

Package deduplication uses source identity, excluding version/ref. A project
entry normally replaces the same global package. A project `autoload: false`
entry instead supplies explicit per-file deltas over the global package;
unmentioned files retain their base state. An empty delta changes nothing,
unlike an empty regular filter. One base package container remains, with override
declaration provenance on affected resource files.

Exact-path resource accumulation is first-write: package entries are accumulated
before standalone paths and native defaults. Remaining aliases of the same real
file are ranked by project explicit paths, project defaults, global explicit
paths, global defaults, then package resources. These are two stages; the latter
must not overwrite an earlier exact-path selection decision.

Among enabled resources of the same type, skill, prompt, and theme names use
first-name selection after path resolution. Extensions are not collapsed by
filename stem. Disabled resources can remain in inventory with `enabled=false`;
that state means excluded by observed selection, not a failed execution.

For skills, an explicit frontmatter `name` wins; otherwise Pi 0.85.1 uses the
parent directory name for both `SKILL.md` and direct Markdown files. Two unnamed
files such as `skills/build.md` and `skills/deploy.md` therefore collide as
`skills`, and Pi keeps the first. Do not substitute filename stems to avoid that
collision: [the versioned skill loader](https://github.com/earendil-works/pi/blob/v0.85.1/packages/coding-agent/src/core/skills.ts#L310-L321)
defines the fallback and its `loadSkills` function applies first-name selection.
Explicit distinct frontmatter names preserve both files.

## Identity and advisory matching

Source identity, declaration occurrence, installed version, and mutable-reference
classification are independent:

| Configured source | Deduplication identity | Mutable-reference result |
|---|---|---|
| `npm:pkg@1.2.3`, `npm:@scope/pkg@1.2.3` | npm package name, including scope | Exact version: not mutable |
| `npm:pkg`, `npm:pkg@latest`, `npm:pkg@^1.2.3` | Same npm package name | Mutable |
| `git:github.com/org/repo@<40-hex SHA>` | Host/repository path without ref | Full SHA: not mutable |
| Git tag, branch, or URL without ref | Same host/repository identity | Mutable, including a bare HTTPS package URL |
| Local path or bare name | Absolute lexical path relative to declaring settings | Local source; no mutable-install finding |

HTTPS, SSH, and supported `git:` shorthand spellings normalize to the repository
identity. Local source identity preserves lexical paths; real paths enforce read
boundaries and resource deduplication. HTTPS is interpreted as Git in the Pi
package-source context, not by changing the meaning of all scanner URLs.

The raw configured source survives as `install_source`. The component's `version`
comes from observed package metadata, independently of the requested pin. npm
install evidence checks the package name before accepting its version. Git and
local directory packages also read `package.json` for a version, without replacing
their source identity or reclassifying the declaration as npm. Missing version
evidence remains missing.

Selected npm package identity plus version evidence supports npm advisory
matching. It does not guarantee an advisory exists, coverage of transitive
dependencies, or verification that the installed files match a published release.
An unpinned source with installed version `1.2.3` still earns the mutable finding.
An exact configured version is not fabricated as an observed installed version.

Package resources retain their owning container and declaration provenance.
Occurrence keys follow the shared graph rules; stable package and package-private
resource identities use the central identity machinery. Local resources without
matching coordinates are still inventory. Missing advisory identity alone is not
a composition-discovery gap.

Installed shared skills need a logical root for every ancestor, not only the
topmost repository root. Project-local files use `project/`; global shared files
use `agents/`. Other ancestor `.agents` roots use
`project-ancestor-<distance>/agents/`, where distance counts parents from the
selected project. This keeps intermediate-ancestor and non-Git occurrences stable
across machines without embedding a home or checkout directory in `bom-ref`.

## Files Pi reads that another runtime also uses

`.agents/skills` is a shared convention, not evidence that Pi owns the file.
The same skill can appear in each reading agent's BOM. Explicit `skills` paths
can also point to `.claude/skills` or `.codex/skills`; those roots are not automatic
Pi defaults. Installed scans honor configured paths, while declared scans retain
the repository boundary.

Pi also reads context files using the candidate order `AGENTS.override.md`,
`AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, `CLAUDE.MD` at the agent root and in project
ancestry. Those are instructions, not package or resource declarations in this
graph. Reading `CLAUDE.md` does not imply that Pi loads Claude MCP configuration
or descends Claude plugin manifests.

## Posture rule applicability

| Rule | Source and input | Contract |
|---|---|---|
| `openaca-posture-mutable-install-reference` | Declared and installed selected package refs | Normalize Pi sources before classification; do not rescan shadowed raw package declarations |
| `openaca-posture-project-trust` | Installed global settings only | Report `defaultProjectTrust: "always"` as a global fallback policy, attributed to Pi and the global manifest |

The trust finding does not synthesize a directory or report every saved trust
entry as a finding. Its Pi wording is "Global default allows project trust";
Codex's existing per-directory semantics remain separate. No Pi-native MCP
credential, transport, or auto-approval rule is registered. The adapter risk is
unassessed, not absent. Pi support adds no native policy compiler or policy writer.

## Coverage and deferred surfaces

Both `declared` and `installed` have a `partial` coverage baseline. Static resource
selection cannot enumerate code-registered composition. Parse failures, missing
installs, excluded project trust, unsupported patterns, and unreadable resources
are additional scan gaps. A package being unmatchable is a different concern.

| Surface | Verdict | Cost or reason |
|---|---|---|
| Third-party MCP adapters and their configuration | Deferred by scope; adapter-specific support can be added | Adapter package/extension may be inventoried, but its servers, credentials, transport, and approvals are not assessed |
| CLI-only resources (`-e`, `--skill`, and other resource flags), temporary installs, disable flags, and SDK overrides | Unobservable from these static inputs | The live resource set can differ in either direction |
| Extension registration and extension trust decisions | Runtime observation outside this scanner's contract | Entry-point inventory cannot establish registered tools, successful loading, or actual project trust |
| Advanced glob syntax | Deferred implementation coverage | Package selection may be incomplete; an explicit gap records the unsupported pattern |
| Transitive npm dependencies and package reconciliation | Outside this release's composition pass | No library dependency inventory or assurance that a stale install satisfies the configured reference |
| Coherent foreign-home installed scanning | Deferred pending root-target design | Relocating only the Pi agent directory leaves home-scoped skills behind |
| Context files above, `A/SYSTEM.md`, `P/.pi/SYSTEM.md`, and corresponding `APPEND_SYSTEM.md` | Outside composition taxonomy here | Instructions can affect behavior but do not become resource components or posture findings in this pass |
| `A/auth.json`, `A/models.json`, keybindings, model/UI settings, sessions, and managed helper binaries | Outside this kind's component/posture scope | No credential/provider-policy analysis, session collection, or bundled runtime inventory is claimed |

MCP deferral is a release boundary, not a requirement to wait for one universal
adapter. Bounded support for a specific adapter needs its own schema audit and
selection/trust contract. No additional taxonomy decision is required to start
that audit; an adapter's actual emitted component types determine whether one is
needed.

## Verification map

These tests verify OpenACA's contract; the versioned upstream sources above are
the evidence for Pi's behavior.

| Contract | Existing verification |
|---|---|
| Source normalization and immutable versus mutable references | `tests/test_parsers/test_pi_source.py`, `tests/test_posture_pi.py` |
| Resource discovery, filters, deltas, aliases, boundaries, and version evidence | `tests/test_parsers/test_pi_settings.py` |
| Saved/default trust, malformed stores, and canonical directory lookup | `tests/test_parsers/test_pi_trust.py` |
| Discovery, graph selection, shared skills, BOM export, and round-trip | `tests/test_agent_kinds_pi.py` |
| CLI, advisory matching, collection, and source-unit integration | Pi cases in `tests/test_e2e.py` |
