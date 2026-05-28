---
id: 0026
title: Add a claude-chat host profile for Claude Desktop Chat config
status: accepted
date: 2026-05-28
supersedes: null
superseded-by: null
---

## Context

OpenACA already parses `claude_desktop_config.json` in repo mode by
registering the filename as another MCP-shaped manifest. That covers
committed examples and manifest-backed app config, but it does not give
endpoint users a way to scan the local Claude Desktop Chat tab
configuration in its native install location.

Claude Desktop now exposes more than one surface: Chat, Cowork, and Code.
Those surfaces do not share one complete local configuration model. In
particular, the Code tab uses Claude Code configuration (`~/.claude*`,
project `.claude/*`, project `.mcp.json`), while the Desktop Chat tab's
legacy local MCP servers live in `claude_desktop_config.json`. Treating
"Claude Desktop" as a single endpoint host would blur these scopes and make
scan output misleading.

The user-facing name matters because it becomes part of `runtime_hosts`,
Agent BOM output, docs, and test fixtures. A future contributor could
reasonably suggest `claude-desktop`, `claude-desktop-chat`, or reusing
`claude-code`; the rejected alternatives are plausible enough to record.

## Decision

OpenACA adds a separate endpoint host profile named `claude-chat` for the
Claude Desktop Chat tab's local MCP configuration. `openaca scan endpoint
--host claude-chat` and `openaca bom endpoint --host claude-chat` read
`claude_desktop_config.json`, parse its `mcpServers` with the existing MCP
parser, and stamp emitted components with `runtime_hosts: ["claude-chat"]`.

`claude-code` remains the default endpoint host and keeps the existing
Claude Code install-state resolver. The `claude-chat` profile does not scan
Claude Code tab state, Cowork policy/configuration, Desktop Extensions
install-state, cloud-managed connectors, chat history, account metadata, or
OS keychain contents. Those are separate surfaces and require their own
documented inventory model before OpenACA claims support.

## Alternatives considered

- **Use `claude-desktop` as the host name**: rejected because the Desktop app
  contains Chat, Cowork, and Code surfaces. The name implies broader coverage
  than `claude_desktop_config.json` provides.
- **Use `claude-desktop-chat` as the host name**: rejected because it is
  accurate but unnecessarily long. `claude-chat` is short, maps to the tab,
  and docs can explicitly define it as Claude Desktop Chat rather than web
  chat.
- **Reuse `claude-code` endpoint mode and add `claude_desktop_config.json`
  to its direct-component walk**: rejected because the Code tab and Chat tab
  have different configuration sources. Mixing them would make `active_in`
  and BOM target metadata ambiguous.
- **Auto-detect both Claude Code and Claude Chat in one endpoint scan**:
  rejected for V0 because it creates hidden scan scope and host-specific
  default-path behavior. The existing endpoint command already values
  visible scan scope; users should choose the host profile explicitly when
  they want a non-default host.
- **Include Desktop Extensions and remote connectors in the first
  `claude-chat` implementation**: rejected because their installed/enabled
  state is not represented by `claude_desktop_config.json`. Shipping them in
  the same change would either require undocumented filesystem assumptions or
  overclaim coverage.

## Consequences

This gives endpoint and BOM users a precise way to scan Claude Desktop Chat
local MCP servers without changing the meaning of existing Claude Code scans.
Scan output can now distinguish `active_in: ["claude-code"]` from
`active_in: ["claude-chat"]`, which matters for remediation and inventory.

The cost is another host selector in the CLI and another runtime-host string
for downstream consumers to handle. Documentation must be clear that
`claude-chat` means the local Desktop Chat tab config, not the claude.ai web
chat product and not all Desktop tabs.

This also leaves known gaps visible: Desktop Extensions, Cowork, and remote
connectors remain out of scope until their local or exported inventory shape
is documented.

## When to revisit

- If Claude Desktop exposes a single local inventory file that covers Chat,
  Cowork, Code, extensions, and connectors, reconsider whether the separate
  host profiles should be consolidated.
- If Desktop Extensions publish a stable installed/enabled-state layout,
  add a new plan to extend `claude-chat` beyond `claude_desktop_config.json`.
- If Cowork exposes a local inventory surface suitable for OSS scanning, add
  a separate `claude-cowork` host profile rather than expanding
  `claude-chat`.
- If users consistently confuse `claude-chat` with web chat, revisit the
  host name before V1.
