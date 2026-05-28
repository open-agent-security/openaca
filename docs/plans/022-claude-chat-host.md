# Claude Chat Host Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit endpoint and Agent BOM support for the Claude Desktop Chat tab's local `claude_desktop_config.json` MCP configuration.

**Architecture:** Keep `claude-code` as the default endpoint host and add `claude-chat` as an explicit host profile. Reuse the existing MCP parser for `claude_desktop_config.json`, but pass `runtime_hosts=["claude-chat"]` so scan output, SARIF, and BOM properties distinguish Chat-tab config from Claude Code config.

**Tech Stack:** Python 3.11, Click, pytest, existing `ComponentRef`, `mcp_json`, `scan`, and `bom_cli` modules.

---

## Scope

Implement:

- `openaca scan endpoint --host claude-chat`.
- `openaca bom endpoint --host claude-chat`.
- Host-specific default config resolution for Claude Chat.
- `runtime_hosts: ["claude-chat"]` on components parsed from
  `claude_desktop_config.json`.
- Endpoint posture checks for Claude Chat local MCP manifests when
  `--include-posture` is passed.
- README/PyPI README docs that define `claude-chat` as Claude Desktop Chat
  local MCP config.

Do not implement:

- Desktop Extensions / DXT inventory.
- Cowork configuration.
- Claude Code tab state under `claude-chat`.
- Remote/cloud connector inventory.
- Chat history, account metadata, or OS keychain inspection.

## Files

- Modify: `tools/parsers/mcp_json.py` — add a small wrapper for parsing an
  MCP-shaped file with caller-supplied runtime hosts.
- Modify: `tools/parsers/__init__.py` — route repo-mode
  `claude_desktop_config.json` through the Claude Chat runtime-host wrapper.
- Modify: `tools/scan.py` — add endpoint `--host`, host-specific config
  resolution, `claude-chat` scan path, and summary wording.
- Modify: `tools/bom_cli.py` — mirror endpoint `--host` support for Agent BOM
  generation.
- Modify: `tools/posture/__init__.py` — add a helper or branch for collecting
  Claude Chat's `claude_desktop_config.json` in endpoint posture mode.
- Modify: `tests/test_scan.py` — scan endpoint coverage.
- Modify: `tests/test_bom_cli.py` — BOM endpoint coverage.
- Modify: `README.md` and `PYPI-README.md` — document host selection and
  scope boundaries.
- Modify: `docs/plans/README.md` — register plan 022.
- Modify: `docs/adrs/INDEX.md` — register ADR-0026.

## Tasks

### Task 1: Parser Wrapper For Runtime Host Override

**Files:**

- Modify: `tools/parsers/mcp_json.py`
- Test: `tests/test_parsers/test_claude_desktop_config.py`

- [x] **Step 1: Write the failing parser test**

Add this test to `tests/test_parsers/test_claude_desktop_config.py`:

```python
def test_parse_with_runtime_hosts_stamps_claude_chat(tmp_path):
    manifest = tmp_path / "claude_desktop_config.json"
    manifest.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "inspector": {
                        "command": "npx",
                        "args": ["@mcpjam/inspector@1.4.2"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    refs = mcp_json.parse_with_runtime_hosts(manifest, ["claude-chat"])

    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40mcpjam/inspector@1.4.2"
    assert refs[0].extra["runtime_hosts"] == ["claude-chat"]
```

Also add `import json` and `from tools.parsers import mcp_json` if they are
not already present.

- [x] **Step 2: Run the parser test to verify it fails**

Run:

```bash
uv run pytest tests/test_parsers/test_claude_desktop_config.py::test_parse_with_runtime_hosts_stamps_claude_chat -q
```

Expected: FAIL with `AttributeError: module 'tools.parsers.mcp_json' has no attribute 'parse_with_runtime_hosts'`.

- [x] **Step 3: Implement the parser wrapper**

In `tools/parsers/mcp_json.py`, change `parse` to delegate through a new helper:

```python
def parse(path: Path) -> list[ComponentRef]:
    return parse_with_runtime_hosts(path, ["claude-code"])


def parse_with_runtime_hosts(path: Path, runtime_hosts: list[str]) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("mcpServers"), dict):
        return parse_mcp_servers(
            data["mcpServers"],
            source_manifest=str(path),
            locator_prefix="$.mcpServers",
            runtime_hosts=runtime_hosts,
        )
    if isinstance(data.get("servers"), dict):
        return parse_mcp_servers(
            data["servers"],
            source_manifest=str(path),
            locator_prefix="$.servers",
            runtime_hosts=[],
        )
    if _looks_like_flat_server_map(data):
        return parse_mcp_servers(
            data,
            source_manifest=str(path),
            locator_prefix="$",
            runtime_hosts=[],
        )
    return []
```

Keep the existing `servers` and flat-map behavior host-neutral.

- [x] **Step 4: Run parser tests**

Run:

```bash
uv run pytest tests/test_parsers/test_claude_desktop_config.py tests/test_parsers/test_mcp_json.py -q
```

Expected: PASS.

### Task 2: Scan Endpoint Host Selection

**Files:**

- Modify: `tools/scan.py`
- Test: `tests/test_scan.py`

- [x] **Step 1: Write failing scan tests**

Add tests that create `<tmp>/Claude/claude_desktop_config.json`, run:

```bash
openaca scan endpoint --host claude-chat --config-dir <tmp>/Claude --format json
```

and assert:

```python
assert result.exit_code == 0, result.output
assert f"config_dir={config_dir}" in result.output
assert "host=claude-chat" in result.output
payload = json.loads(result.stdout)
assert payload["stats"]["unit"] == "manifest"
assert payload["stats"]["units"] == 1
assert payload["stats"]["components"] == 1
```

Also add a default-host regression test proving `openaca scan endpoint` still
emits `host=claude-code` or otherwise preserves existing Claude Code behavior.

- [x] **Step 2: Run the scan test to verify it fails**

Run:

```bash
uv run pytest tests/test_scan.py::test_endpoint_claude_chat_reads_desktop_config -q
```

Expected: FAIL because `--host` is not a recognized endpoint option.

- [x] **Step 3: Add host constants and option**

In `tools/scan.py`, add:

```python
_HOST_CHOICES = ("claude-code", "claude-chat")

_host_option = click.option(
    "--host",
    type=click.Choice(_HOST_CHOICES),
    default="claude-code",
    show_default=True,
    help=(
        "Endpoint host profile. claude-code scans Claude Code config; "
        "claude-chat scans Claude Desktop Chat local MCP config."
    ),
)
```

Apply `@_host_option` to the `endpoint` command and add `host: str` to its
function signature.

- [x] **Step 4: Add Claude Chat config resolver**

In `tools/scan.py`, add:

```python
def _resolve_endpoint_config_dir(config_dir: Path | None, host: str = "claude-code") -> Path:
    if config_dir is not None:
        return config_dir.expanduser()
    if host == "claude-chat":
        configured = os.environ.get("CLAUDE_CHAT_CONFIG_DIR")
        if configured:
            return Path(configured).expanduser()
        return Path.home() / "Library" / "Application Support" / "Claude"
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"
```

This keeps the existing Claude Code default and adds a Claude Chat-specific
environment override.

- [x] **Step 5: Add Claude Chat parse path**

In `tools/scan.py`, add a helper:

```python
def _parse_claude_chat(config_dir: Path) -> tuple[list[ComponentRef], list[str], int]:
    manifest = config_dir / "claude_desktop_config.json"
    if not manifest.is_file():
        return [], [], 0
    try:
        return mcp_json.parse_with_runtime_hosts(manifest, ["claude-chat"]), [], 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [f"{manifest}: failed to parse: {exc}"], 1
```

Import `mcp_json` from `tools.parsers`.

Branch inside `endpoint`: use `parse_install(...)` for `claude-code`; use
`_parse_claude_chat(...)` for `claude-chat`. For the Claude Chat branch, build
the Agent BOM with `source_unit_count=n_found` and
`source_unit_label="manifest"`.

- [x] **Step 6: Preserve visible scan scope**

Update the detected line to include host:

```python
click.echo(
    f"detected host={host}, config_dir={config_dir}, project={project_note} (mode=endpoint)",
    err=True,
)
```

Only emit the existing `--project` reminder for `host == "claude-code"` and
`project is None`; `--project` is not meaningful for Claude Chat.

- [x] **Step 7: Run focused scan tests**

Run:

```bash
uv run pytest tests/test_scan.py::test_endpoint_claude_chat_reads_desktop_config tests/test_scan.py::test_endpoint_subcommand_minimal_install_no_findings -q
```

Expected: PASS.

### Task 3: Claude Chat Posture Checks

**Files:**

- Modify: `tools/scan.py`
- Modify: `tools/posture/__init__.py` if a helper keeps the branch cleaner
- Test: `tests/test_scan.py`

- [x] **Step 1: Write posture coverage test**

Add a test that writes:

```json
{
  "mcpServers": {
    "local": {"url": "http://localhost:3000/mcp"}
  }
}
```

to `claude_desktop_config.json`, runs:

```bash
openaca scan endpoint --host claude-chat --config-dir <dir> --include-posture
```

and asserts the output includes `openaca-posture-insecure-transport`.

- [x] **Step 2: Run the posture test**

Run:

```bash
uv run pytest tests/test_scan.py::test_endpoint_claude_chat_include_posture_checks_desktop_config -q
```

Expected: PASS if the existing endpoint posture collection already feeds
`claude_desktop_config.json` to posture rules; otherwise FAIL before the
implementation step below.

- [x] **Step 3: Feed Claude Chat manifest to posture rules**

If the test fails, in the Claude Chat branch of `endpoint`, when
`include_posture` is true, use the raw manifest tuple:

```python
manifests = []
manifest = config_dir / "claude_desktop_config.json"
if manifest.is_file():
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        manifests.append((manifest, data))
posture_findings = run_posture_rules(refs, manifests, [])
```

Do not run Claude Code settings posture checks for `claude-chat`. In the
implemented change, no new posture code was needed because
`collect_endpoint_mcp_manifests` already includes
`config_dir / "claude_desktop_config.json"`.

- [x] **Step 4: Run focused posture tests**

Run:

```bash
uv run pytest tests/test_scan.py::test_endpoint_claude_chat_include_posture_checks_desktop_config tests/test_posture_insecure_transport.py tests/test_posture_mcp_auto_approve.py -q
```

Expected: PASS.

### Task 4: Agent BOM Endpoint Host Selection

**Files:**

- Modify: `tools/bom_cli.py`
- Test: `tests/test_bom_cli.py`

- [x] **Step 1: Write failing BOM test**

Add a test that runs:

```bash
openaca bom endpoint --host claude-chat --config-dir <dir> --output <bom>
```

against a `claude_desktop_config.json` fixture and asserts:

```python
doc = json.loads(output.read_text(encoding="utf-8"))
assert doc["metadata"]["properties"]
assert any(c.get("purl") == "pkg:npm/%40mcpjam/inspector@1.4.2" for c in doc["components"])
component = next(c for c in doc["components"] if c.get("purl") == "pkg:npm/%40mcpjam/inspector@1.4.2")
props = {p["name"]: p["value"] for p in component["properties"]}
assert json.loads(props["openaca:runtime_hosts"]) == ["claude-chat"]
```

- [x] **Step 2: Run the BOM test to verify it fails**

Run:

```bash
uv run pytest tests/test_bom_cli.py::test_bom_endpoint_claude_chat_emits_desktop_config_components -q
```

Expected: FAIL because `openaca bom endpoint` does not yet accept `--host`.

- [x] **Step 3: Mirror scan host logic in BOM CLI**

In `tools/bom_cli.py`, add the same `_HOST_CHOICES`, `_host_option`, and
host-aware `_resolve_endpoint_config_dir(config_dir, host)` behavior. For
`host == "claude-chat"`, parse:

```python
manifest = config_dir / "claude_desktop_config.json"
refs = mcp_json.parse_with_runtime_hosts(manifest, ["claude-chat"]) if manifest.is_file() else []
bom = build_agent_bom(
    refs,
    target_type="endpoint",
    target=str(config_dir),
    source_unit_count=1 if manifest.is_file() else 0,
    source_unit_label="manifest",
)
```

For `host == "claude-code"`, keep the current `parse_install` behavior and
`active plugin` source-unit label.

- [x] **Step 4: Run BOM tests**

Run:

```bash
uv run pytest tests/test_bom_cli.py -q
```

Expected: PASS.

### Task 5: Documentation And Index Updates

**Files:**

- Modify: `README.md`
- Modify: `PYPI-README.md`
- Modify: `docs/adrs/INDEX.md`
- Modify: `docs/plans/README.md`

- [x] **Step 1: Update README endpoint examples**

Add a Claude Chat example near the endpoint examples:

```bash
# Claude Desktop Chat tab local MCP config.
openaca scan endpoint \
    --host claude-chat \
    --config-dir "$HOME/Library/Application Support/Claude"
```

Add one sentence: "`claude-chat` means Claude Desktop Chat local MCP config
(`claude_desktop_config.json`); it does not include Claude Code tab, Cowork,
Desktop Extensions, or remote connector state."

- [x] **Step 2: Mirror the README wording in PYPI-README.md**

Use the same scope sentence and command example so package-page docs match
repo docs.

- [x] **Step 3: Register ADR-0026**

Add this entry to the Active section of `docs/adrs/INDEX.md`:

```markdown
- [ADR-0026 — Add a claude-chat host profile for Claude Desktop Chat config](0026-claude-chat-host-profile.md): endpoint and Agent BOM scans use `--host claude-chat` for the Desktop Chat tab's local `claude_desktop_config.json`; `claude-code` remains the default, while Cowork, Desktop Extensions, and cloud-managed connectors stay out of scope until their inventory models are documented.
```

- [x] **Step 4: Register plan 022**

In `docs/plans/README.md`, mark plan 016 done if all of its checkboxes remain
complete, then add:

```markdown
| 022 | [Claude Chat host profile](022-claude-chat-host.md) | 🟡 Active | 019, 021 |
```

Keep at most one Active row.

- [x] **Step 5: Run docs-sensitive tests**

Run:

```bash
uv run pytest tests/test_scan.py tests/test_bom_cli.py -q
```

Expected: PASS.

### Task 6: Full Verification

**Files:**

- Modify: `docs/plans/022-claude-chat-host.md`

- [x] **Step 1: Run formatter check**

Run:

```bash
uv run ruff format --check .
```

Expected: PASS.

- [x] **Step 2: Run lint**

Run:

```bash
uv run ruff check .
```

Expected: PASS.

- [x] **Step 3: Run type check**

Run:

```bash
PYTHONPATH=src uv run pyright
```

Expected: PASS.

- [x] **Step 4: Run full tests**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [x] **Step 5: Run overlay lint**

Run:

```bash
uv run openaca lint overlays/
```

Expected: PASS.

- [x] **Step 6: Review diff**

Run:

```bash
git diff --stat
git diff -- docs/adrs/0026-claude-chat-host-profile.md docs/plans/022-claude-chat-host.md tools/ tests/ README.md PYPI-README.md
```

Confirm every changed line traces to `claude-chat` host support.
