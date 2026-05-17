# Agent Component Identity + Scan Output Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scan output explain agent component identity consistently: what component matched, what source identity was used for matching, how it entered the agent stack, and where it is active.

**Architecture:** Keep advisory overlays unchanged. Scanner output grows a component/source/container/runtime model derived from existing `ComponentRef` data plus new parser metadata. Matching continues to use source identity; rendering explains direct-vs-bundled installation via `declared_by` and `component_path`. Posture findings, if present, use the same output envelope but remain scanner-emitted findings, not overlay records.

**Tech Stack:** Python 3.11 dataclasses, existing `ComponentRef`, Click scan CLI, JSON renderer, text renderer, SARIF renderer, pytest.

**Depends on:** Plan 013 rename completion and Plan 014 posture findings. Also relies on ADR-0012 and ADR-0013 for the overlay-vs-scan-context and identity-vs-observation split.

**Output compatibility:** Pre-V0 breaking change. This plan replaces the current JSON shape that emits vulnerability `findings[]` and posture `posture_findings[]` as adjacent arrays with a single `findings[]` array carrying `finding_type: "vulnerability" | "posture"`. Do not preserve the adjacent-array shape unless a post-review decision explicitly reverses this.

**ADR:** [ADR-0016 — Separate agent component source identity from scan context in output](../adrs/0016-agent-component-identity-and-scan-output.md).

---

## Scope

This plan covers scan output identity, not new detection rules. It intentionally restructures both vulnerability and posture output into one scanner finding envelope.

In scope:

- A stable scanner-output envelope for vulnerability and posture findings.
- A pre-V0 JSON migration from adjacent `findings[]`/`posture_findings[]` arrays to one `findings[]` array with `finding_type`.
- Component/source/container/runtime fields in JSON output.
- Human-readable text output that leads with the risky component and shows install/container context.
- SARIF properties that preserve the same identity metadata for code-scanning consumers.
- Parser metadata needed to distinguish direct install vs plugin-bundled component.
- Tests for direct and plugin-bundled skill/MCP cases.

Out of scope:

- Overlay schema changes.
- New advisory matching strategies beyond existing `ComponentRef` matching.
- Preserving the current adjacent-array JSON shape from Plan 014 posture findings.
- Treating `skills.sh` as an ecosystem or canonical identity.
- Network resolution of mutable refs to immutable commits.
- Private registry integrations.

## Identity Model

Use three distinct layers:

1. **Component identity** — the thing that is vulnerable or risky.
   Example: `skill frontend-design`, `mcp_server filesystem`, `plugin acme-tools`.

2. **Source identity** — the artifact identity used for matching.
   Example: `pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2`, `pkg:github/vercel-labs/agent-skills#skills/frontend-design/SKILL.md`.

3. **Context identity** — how the component entered and runs in the agent stack.
   Example: direct `~/.agents/.skill-lock.json`, plugin `agent-skills -> skill frontend-design`, active in `claude-code`.

Rules:

- Match advisories on source identity, not install path.
- Direct vs plugin-installed changes `declared_by` and `component_path`, not the matched source identity.
- Use official PURLs for official ecosystems (`npm`, `pypi`, `docker`, `github`) when enough source data exists.
- Do not put `openaca` in PURL type names.
- Do not use `skills.sh` as canonical source ecosystem. Skills installed through Vercel `skills` should identify source as `github`, `gitlab`, `git`, `well-known`, `local`, or `node_modules`, matching the `skills-lock.json` / `.skill-lock.json` source model.
- For mutable refs, keep `ref` separate from immutable `revision`. Do not encode `@main` into canonical PURL as if it were a stable version.

## GitHub PURL and Matching Semantics

GitHub-sourced skills use PURL-shaped identifiers only as source locators. Use
`pkg:github/<owner>/<repo>@<commit>#<subpath>` only when the immutable commit
SHA is known. If only a mutable ref such as `main` is known, emit
`pkg:github/<owner>/<repo>#<subpath>` plus separate `ref`, `content_hash`, and
`mutable_reference` fields. Do not encode `@main` as a version.

OSV federation is not expected to query GitHub skill PURLs reliably in V0.
Package advisories continue to match via npm/PyPI/Docker PURLs and upstream
OSV package ranges. GitHub skill source identity is for local explanation,
future identity-based matching, and posture findings; hash-based advisory
matching remains out of scope for this plan.

## Output Shape

JSON finding shape:

```json
{
  "finding_type": "vulnerability",
  "id": "GHSA-xxxx-yyyy-zzzz",
  "severity": "medium",
  "confidence": "high",
  "title": "Vulnerable MCP server package",
  "component": {
    "type": "mcp_server",
    "name": "filesystem",
    "source": {
      "ecosystem": "npm",
      "purl": "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2",
      "name": "@modelcontextprotocol/server-filesystem",
      "version": "1.0.2"
    }
  },
  "active_in": ["claude-code"],
  "declared_by": {
    "kind": "plugin",
    "name": "acme-devtools",
    "path": ".claude-plugin/plugin.json"
  },
  "component_path": [
    {"type": "plugin", "name": "acme-devtools"},
    {"type": "mcp_server", "name": "filesystem"}
  ],
  "matched_advisory": {
    "id": "GHSA-xxxx-yyyy-zzzz",
    "aliases": ["CVE-2026-1234"],
    "source": "osv.dev"
  },
  "remediation": "Upgrade @modelcontextprotocol/server-filesystem to a fixed version."
}
```

Posture findings use the same envelope with `finding_type: "posture"` and `rule_id` instead of advisory `id`. Plan 014 currently models posture findings with flat `component: str` and `location: str`; this plan restructures `PostureFinding` so posture output uses the same structured `component`, `declared_by`, `component_path`, and `active_in` fields as vulnerability output. Rule detection stays in Plan 014; output normalization happens here.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `tools/component_ref.py` | Modify | Add and document a `ComponentRef.extra` schema for output-only metadata; avoid constructor churn in this plan. |
| `tools/finding_output.py` | Create | Convert vulnerability `Finding` and posture `PostureFinding` values into normalized output dictionaries. |
| `tools/render.py` | Modify | Use normalized output for JSON, consolidate posture into `findings[]`, and add human-readable component/source/context lines. |
| `tools/sarif.py` | Modify | Add identity metadata to SARIF result properties. |
| `tools/parsers/claude_install.py` | Modify | Populate source/container/runtime metadata for plugin-bundled skills/MCPs where known. |
| `tools/parsers/mcp_json.py` | Modify | Populate source/runtime metadata for direct MCP declarations where known. |
| `tools/posture/finding.py` | Modify | Replace flat posture `component`/`location` fields with structured component context or an adapter-compatible equivalent. |
| `tests/test_finding_output.py` | Create | Unit tests for direct vs plugin-bundled identity output. |
| `tests/test_render.py` | Modify | Text/JSON render assertions for new output shape. |
| `tests/test_sarif.py` | Modify | SARIF property assertions. |
| `tests/test_e2e.py` | Modify | One cross-layer endpoint scan proving plugin-bundled component output. |

## ComponentRef Extra Schema

This plan keeps output metadata in `ComponentRef.extra` instead of adding new
first-class dataclass fields. That is deliberate: these fields describe report
shape, not matching semantics, and adding constructor parameters would churn all
parsers at once. The schema is documented here and should be treated as stable
inside the scanner:

```python
ref.extra = {
    "component_type": "mcp_server" | "skill" | "plugin" | "hook" | "command" | "agent" | "package",
    "runtime_hosts": ["claude-code"],
    "source": {
        "ecosystem": "npm" | "pypi" | "docker" | "github" | "gitlab" | "git" | "well-known" | "local" | "node_modules",
        "purl": "pkg:npm/name",
        "ref": "main",
        "revision": "<commit-sha>",
        "content_hash": "<tree-or-folder-hash>",
        "subpath": "skills/name/SKILL.md",
        "mutable_reference": True,
    },
    "declared_by": {"kind": "manifest" | "plugin" | "skill_lock", "name": "...", "path": "..."},
    "component_path": [
        {"type": "plugin", "name": "..."},
        {"type": "mcp_server", "name": "..."},
    ],
}
```

Only populate keys that are known locally. JSON consumers must tolerate missing
keys. `runtime_hosts` uses machine-readable host IDs such as `claude-code`;
text rendering may convert those IDs to display names. V0 may hardcode `claude-code` because it is the only active runtime host implemented today; adding a second host should replace that assumption with parser-provided host IDs.

## Task 1: Add normalized output model

**Files:**

- Create: `tools/finding_output.py`
- Test: `tests/test_finding_output.py`

- [x] **Step 1: Write failing tests for direct MCP vulnerability output**

Create `tests/test_finding_output.py` with a synthetic npm-backed MCP `ComponentRef`, one advisory dict, and assertions that `finding_to_output()` emits:

- `finding_type: "vulnerability"`
- `component.type: "mcp_server"` from `component.extra["component_type"]`
- `component.source.purl` from `ComponentRef.purl`
- `declared_by.kind: "manifest"`
- `component_path` with only the MCP component
- `matched_advisory.id`

Run:

```bash
uv run pytest tests/test_finding_output.py -q
```

Expected: fail because `tools.finding_output` does not exist.

- [x] **Step 2: Implement `tools/finding_output.py` minimally**

Create helpers:

- `component_type_for(ref: ComponentRef) -> str`
- `source_for(ref: ComponentRef) -> dict`
- `declared_by_for(ref: ComponentRef) -> dict | None`
- `component_path_for(ref: ComponentRef) -> list[dict]`
- `finding_to_output(finding: Finding, advisory: dict | None) -> dict`
- `posture_to_output(finding: PostureFinding) -> dict`

Read only these `ComponentRef.extra` keys initially:

- `component_type`
- `runtime_hosts`
- `declared_by`
- `component_path`
- `source`

Fallback behavior:

- `component.type` defaults to `ref.extra["component_type"]` when present, else `"package"` for package ecosystems, else `"component"`.
- `component.name` defaults to `ref.name` or `ref.component_identity`.
- `component.source` derives from `ref.ecosystem`, `ref.name`, `ref.version`, `ref.purl`, plus `ref.extra["source"]` overlay fields.
- `declared_by` defaults to `{"kind": "manifest", "path": ref.source_manifest}` when `source_manifest` is present.
- `component_path` defaults to `[{"type": component_type, "name": component_name}]`.

- [x] **Step 3: Run tests and commit**

Run:

```bash
uv run pytest tests/test_finding_output.py -q
git add tools/finding_output.py tests/test_finding_output.py
git commit -m "Add normalized scan finding output model"
```

Expected: tests pass.

## Task 2: Preserve source vs container metadata in parsers

**Files:**

- Modify: `tools/parsers/mcp_json.py`
- Modify: `tools/parsers/claude_install.py`
- Test: `tests/test_parsers/test_mcp_json.py`
- Test: `tests/test_parsers/test_claude_install.py`

- [x] **Step 1: Add parser tests for direct MCP metadata**

In `tests/test_parsers/test_mcp_json.py`, add a fixture with a direct MCP server launched via `npx @modelcontextprotocol/server-filesystem@1.0.2`. Assert emitted ref includes:

```python
ref.extra["component_type"] == "mcp_server"
ref.extra["runtime_hosts"] == ["claude-code"]
ref.extra["declared_by"]["kind"] == "manifest"
```

Run:

```bash
uv run pytest tests/test_parsers/test_mcp_json.py -q
```

Expected: fail until metadata is populated.

- [x] **Step 2: Add parser tests for plugin-bundled child metadata**

In `tests/test_parsers/test_claude_install.py`, add or extend a plugin fixture that declares a child skill or MCP. Assert child refs include:

```python
ref.extra["declared_by"]["kind"] == "plugin"
ref.extra["declared_by"]["name"] == "<plugin-name>"
ref.extra["component_path"][0] == {"type": "plugin", "name": "<plugin-name>"}
ref.extra["component_path"][-1]["type"] in {"skill", "mcp_server"}
```

Run:

```bash
uv run pytest tests/test_parsers/test_claude_install.py -q
```

Expected: fail until metadata is populated.

- [x] **Step 3: Populate metadata in parsers**

Update parser `ComponentRef(...)` creation sites surgically:

- Direct MCP refs:
  - `extra["component_type"] = "mcp_server"`
  - `extra["runtime_hosts"] = ["claude-code"]` when parsed from Claude config
  - `extra["declared_by"] = {"kind": "manifest", "path": str(path)}`

- Plugin-bundled child refs:
  - Preserve existing `attributed_to`.
  - Add `extra["declared_by"] = {"kind": "plugin", "name": plugin_name, "path": manifest_path}`
  - Add `extra["component_path"] = [{"type": "plugin", "name": plugin_name}, {"type": child_type, "name": child_name}]`
  - Add `extra["runtime_hosts"] = ["claude-code"]` for endpoint install scan.

Do not change matching behavior.

- [x] **Step 4: Run parser tests and commit**

Run:

```bash
uv run pytest tests/test_parsers/test_mcp_json.py tests/test_parsers/test_claude_install.py -q
git add tools/parsers/mcp_json.py tools/parsers/claude_install.py tests/test_parsers/test_mcp_json.py tests/test_parsers/test_claude_install.py
git commit -m "Attach source and container metadata to component refs"
```

Expected: tests pass.

## Task 3: Upgrade JSON scan output

**Files:**

- Modify: `tools/render.py`
- Modify: `tools/posture/finding.py`
- Test: `tests/test_render.py`
- Test: `tests/test_scan.py`
- Test: posture finding tests added by Plan 014

- [x] **Step 1: Write failing JSON renderer tests**

Add tests that call `render_json()` with:

- a direct vulnerability finding;
- a plugin-bundled vulnerability finding;
- a posture finding produced from the Plan 014 `PostureFinding` model.

Assert JSON output has one top-level `findings[]` array only. It must not emit a sibling `posture_findings[]` array. Each entry includes `finding_type`, `component`, `active_in`, `declared_by`, and `component_path`; vulnerability entries include `matched_advisory`, and posture entries include `rule_id` and `standards`.

Run:

```bash
uv run pytest tests/test_render.py -q -k json
```

Expected: fail until renderer uses normalized output for both vulnerability and posture findings.

- [x] **Step 2: Restructure posture output metadata**

Update `tools/posture/finding.py` so posture findings can carry structured component context instead of only flat `component: str` and `location: str`. Keep rule detection logic from Plan 014 intact; this task changes the data carried to renderers. If a narrow compatibility adapter is needed inside `tools/finding_output.py` while tests migrate, keep it private and remove it before the task is complete.

- [x] **Step 3: Implement JSON rendering through normalized output**

In `tools/render.py`:

- import `finding_to_output` and `posture_to_output`;
- render vulnerability and posture results into one `findings[]` list with a `finding_type` discriminator;
- keep `stats` shape unchanged unless current tests force a focused update;
- remove the top-level `posture_findings[]` JSON field introduced by Plan 014.

This is an intentional pre-V0 breaking change, not a compatibility layer. Update tests and docs in this task rather than carrying the adjacent-array shape forward.

- [x] **Step 4: Run tests and commit**

Run:

```bash
uv run pytest tests/test_render.py tests/test_scan.py -q -k json
git add tools/render.py tools/posture/finding.py tests/test_render.py tests/test_scan.py
git commit -m "Expose component identity context in JSON output"
```

Expected: tests pass.

## Task 4: Upgrade text output for direct and bundled components

**Files:**

- Modify: `tools/render.py`
- Modify: `tools/scan.py`
- Test: `tests/test_render.py`
- Test: `tests/test_scan.py`

- [x] **Step 1: Add failing text-output tests**

Add tests that assert verbose output shows:

Direct:

```text
Component: mcp_server filesystem
Source: pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2
Declared by: <manifest path>
```

Plugin-bundled:

```text
Component: mcp_server filesystem
Declared by: plugin "acme-devtools"
Path: plugin acme-devtools -> mcp_server filesystem
```

Run:

```bash
uv run pytest tests/test_render.py tests/test_scan.py -q -k "verbose or text"
```

Expected: fail until text renderer is updated.

- [x] **Step 2: Implement concise text details**

Keep default summary concise. Add detailed component/source/context lines in verbose mode only.

For each matched finding, render:

- `Component: <type> <name>`
- `Source: <purl or ecosystem:name@version>`
- `Active in: <runtime hosts>` when known
- `Declared by: <manifest/plugin/lock>`
- `Path: <component path>` only when length > 1

- [x] **Step 3: Run tests and commit**

Run:

```bash
uv run pytest tests/test_render.py tests/test_scan.py -q
git add tools/render.py tools/scan.py tests/test_render.py tests/test_scan.py
git commit -m "Explain component source and containment in text scan output"
```

Expected: tests pass.

## Task 5: Add SARIF identity properties

**Files:**

- Modify: `tools/sarif.py`
- Test: `tests/test_sarif.py`

- [x] **Step 1: Write failing SARIF tests**

Add tests asserting SARIF result `properties` include:

- `component_type`
- `component_name`
- `source_purl`
- `declared_by`
- `component_path`
- `active_in`

Run:

```bash
uv run pytest tests/test_sarif.py -q
```

Expected: fail until SARIF properties are populated.

- [x] **Step 2: Populate SARIF properties from normalized output**

In `tools/sarif.py`, reuse `finding_to_output()` to populate result properties. Keep `ruleId` as advisory ID for vulnerability findings.

Do not change SARIF levels in this task.

- [x] **Step 3: Run tests and commit**

Run:

```bash
uv run pytest tests/test_sarif.py -q
git add tools/sarif.py tests/test_sarif.py
git commit -m "Carry component identity metadata into SARIF"
```

Expected: tests pass.

## Task 6: E2E endpoint output coverage

**Files:**

- Modify: `tests/test_e2e.py`

- [x] **Step 1: Add endpoint e2e test**

Add one endpoint-mode test that builds a minimal Claude config/plugin fixture with a bundled MCP or skill and patches advisory loading so the child component matches. Assert JSON output includes:

- child component identity;
- plugin container in `component_path`;
- `declared_by.kind == "plugin"`;
- `matched_advisory.id`.

Run:

```bash
uv run pytest tests/test_e2e.py -q -k component_path
```

Expected: fail until previous tasks are complete; pass after Tasks 1-5.

- [x] **Step 2: Run e2e and commit**

Run:

```bash
uv run pytest tests/test_e2e.py -q
git add tests/test_e2e.py
git commit -m "Cover plugin-bundled component identity end to end"
```

Expected: tests pass.

## Task 7: Documentation and full verification

**Files:**

- Modify: `README.md` or `docs/specs/openaca-thesis.md` if scan JSON output is documented there.
- Modify: `docs/plans/README.md` only when activating this plan; leave it pending while Plan 014 is active.

- [x] **Step 1: Document scan output fields**

Add a short scanner output section documenting:

- `component`;
- `component.source`;
- `active_in`;
- `declared_by`;
- `component_path`;
- `finding_type`.

State that overlays remain advisory records and do not store local scan context.

- [x] **Step 2: Run full verification gate**

Run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
uv run openaca lint overlays/
```

Expected: all pass.

- [x] **Step 3: Final commit and PR**

Run:

```bash
git add README.md docs/plans/015-agent-component-identity-and-scan-output.md docs/plans/README.md
git commit -m "Plan component identity in scan output"
git push -u origin plan/agent-component-scan-output
gh pr create --draft --title "Plan agent component identity in scan output" --body "Adds the implementation plan for normalized component/source/container scan output."
```

If implementation commits were already made in this branch, use a PR title that describes the implementation instead of the plan-only title.

## Open Questions Before Implementation

- Should `active_in` use host IDs (`claude-code`) or display names (`Claude Code`) in JSON? Recommendation: host IDs in JSON, display names in text.
- Should any legacy JSON fields (`package`, `location`, `attributed_to`) remain after the pre-V0 migration? Recommendation: no unless an implementation test identifies a concrete internal consumer.
- Should posture findings share the exact same renderer code path? Recommendation: yes; Task 3 migrates posture output onto the same envelope after Plan 014 lands.

## Self-Review

- Spec coverage: covers component/source/container/runtime identity, direct vs plugin-bundled cases, JSON/text/SARIF, and no overlay schema changes.
- Placeholder scan: no `TBD`/`TODO`; open questions are explicit pre-implementation decisions.
- Type consistency: `component`, `source`, `declared_by`, `component_path`, `active_in`, and `finding_type` are used consistently across tasks.
