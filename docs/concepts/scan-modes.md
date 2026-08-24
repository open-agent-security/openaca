# Scan Modes

OpenACA scans two distinct observation contexts. The same component and
advisory can appear in both, but the result answers a different question.

| Mode | Question | Composition source | Common use |
|---|---|---|---|
| `openaca scan repo` | What agents does this repository declare, and what do they load? | `declared` | CI gates, PR checks, source review |
| `openaca scan endpoint` | What agents are installed on this machine right now, and what do they load? | `installed` | Developer laptop scans, managed runner scans, local inventory |

A subcommand names *where to look*, and each produces the composition source
rather than restating it: `endpoint` yields `installed`, `repo` yields `declared`.
`--format json`/`exposure` emit one document per scan — the findings list stays
flat and each agent appears in its `agents[]` entry — while text emits one card
per agent (ADR-0047). That is one document and one card today.

## Repository scans

`openaca scan repo` walks supported manifests under `--target`.

It covers:

- committed project-host config such as `.claude/settings.json`,
  `.claude/skills`, `.claude/commands`, and `.claude/agents`;
- manifest-backed SDK config such as a root `.mcp.json` used by an agent
  framework;
- agent-component package manifests, such as `package.json` or
  `pyproject.toml` inside a Claude Code plugin.

Repository findings mean: this repository declares a component or dependency
that OpenACA can inventory, assess, or match. They do not prove the deployed
application loaded that component at runtime.

**A repo declaring no agent produces no document.** Evidence of a declared agent
is a *file* the runtime owns — `.claude/settings.json`, a skill, a command, a
subagent, `.claude-plugin/plugin.json`, or the project `.mcp.json`. An empty
`.claude/` directory is not evidence (Git does not preserve one, so it is not a
portable declaration), and neither is a bare `mcp.json`, which no runtime owns
exclusively. A repository of ordinary package manifests declares no agent, so
`scan repo` reports that and exits `0` — this is the deliberate asymmetry with
`endpoint`, where an installed runtime with no configuration is a real agent
with zero components.

## Endpoint scans

`openaca scan endpoint` reads installed local state for the active agent host.
For Claude Code, that means user-level config under `~/.claude` by default, or
`$CLAUDE_CONFIG_DIR` when set.

Endpoint scans are closer to installed ground truth because they can see
resolved local state such as installed plugins and configured MCP servers.

An installed runtime with **no configuration at all** is still a real agent: it
is reported with zero components rather than being invisible.

## Project context

Claude Code also supports project-scoped agent config. Endpoint scans include
only user-level config unless you opt into project context:

```bash
openaca scan endpoint --project /path/to/repo
```

Use `--project .` from inside a repo to ask: what would this local Claude Code
installation load when used in this project?

## Same component, different context

The same component identity can carry different meaning depending on scan mode:

- in repo mode, it is declared in source control;
- in endpoint mode, it is installed or active on the scanned machine.

OpenACA keeps the observation context in the scan output and Agent BOM so those
cases do not collapse into one ambiguous result.

## Unpinned components

Unpinned components such as `npx pkg@latest` are inventoried, but they cannot be
matched to version-specific OSV advisories unless OpenACA can resolve an exact
version from a lockfile or install state.

Lockfile-pinned transitive dependencies such as `package-lock.json`, `uv.lock`,
and `bun.lock` carry exact versions and can be matched.
