# Scan Modes

OpenACA scans two distinct observation contexts. The same component and
advisory can appear in both, but the result answers a different question.

| Mode | Question | Common use |
|---|---|---|
| `openaca scan repo` | What agent components are declared in this repository? | CI gates, PR checks, source review |
| `openaca scan endpoint` | What agent components are installed on this machine right now? | Developer laptop scans, managed runner scans, local inventory |

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

## Endpoint scans

`openaca scan endpoint` reads installed local state for the active agent host.
For Claude Code, that means user-level config under `~/.claude` by default, or
`$CLAUDE_CONFIG_DIR` when set.

Endpoint scans are closer to installed ground truth because they can see
resolved local state such as installed plugins and configured MCP servers.

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
