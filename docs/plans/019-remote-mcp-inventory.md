# Remote MCP Server Inventory Plan

**Goal:** Extend the MCP parser to emit `ComponentRef`s for URL-bearing
MCP entries under the `mcp-remote/<host>/<path>` identity namespace,
per ADR-0020. Closes the round-1 "0 MCP servers" interpretability gap
for testers with HTTP/SSE MCPs.

**Scope:** Parser change + tests + inline doc updates only. No overlay
corpus authorship; no openaca-demo beta-guide edits in this plan (the
guide reorg is separate work).

## Tasks

- [x] ADR-0020 captures the design + rejected alternatives.
- [x] Register plan 019 in the plan index.
- [x] Failing parser tests in `tests/test_parsers/test_mcp_json.py`:
  - HTTP entry (`{"url": "..."}`) emits a ref with
    `mcp-remote/...` identity and `extra.transport == "http"`.
  - SSE entry (`{"type": "sse", "url": "..."}`) emits with
    `extra.transport == "sse"`.
  - StreamableHTTP entry (`{"type": "streamableHttp", "url": "..."}`)
    emits with `extra.transport == "streamableHttp"`.
  - URL normalization: scheme stripped, default port stripped,
    non-default port kept, query/fragment stripped, empty path → `/`,
    host lowercased (path case preserved), credentials stripped.
  - `disabled: true` remote entries skipped.
  - Interpolated URL (`${HOST}`) skipped.
  - Malformed URL (no parseable host) skipped without raising.
  - Entry with both `url` and `command` resolves to remote (URL wins).
  - Ref has `component_type: mcp_server` in `extra` and no
    `ecosystem` set.
- [x] Implement `_normalize_remote_identity(url: str) -> str | None`
  helper in `tools/parsers/mcp_json.py`. Returns `None` on
  interpolation, malformed input, or missing host.
- [x] Implement `_remote_mcp_ref_extra(...)` helper (or reuse
  `_mcp_ref_extra` with extra kwargs) to carry `transport` and `url`.
- [x] Extend `parse_mcp_servers`: add a URL-bearing branch BEFORE the
  command-class dispatch. URL wins over command when both present.
- [x] Verify the existing scan/render path emits remote MCPs under
  `component_type: mcp_server` with `source.status: unknown` (no
  render changes expected; confirm with end-to-end test or smoke).
- [x] Run focused parser tests:
  `uv run pytest tests/test_parsers/test_mcp_json.py -q`
- [x] Run full gate: `ruff format --check .`, `ruff check .`,
  `pyright`, `pytest -q`, `openaca lint overlays/`.

## Out of scope

- Overlay corpus records targeting `mcp-remote/` identities (separate
  corpus authorship work).
- OSV federation for remote MCPs (no PURL → no federation,
  per ADR-0020).
- TOML config support, `env`/`headers`/`oauth` secret-surface
  extraction.
- Interpolation resolution for URLs containing `${ENV_VAR}` —
  conservatively skip, matching the stdio convention.
- Beta guide coverage-table update in openaca-demo (separate PR
  alongside the next pre-release docs polish).
