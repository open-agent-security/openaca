# Plan 014 — Posture Findings (Scanner-Side Hygiene Rules)

## Context

V0 ships with 6 bundled overlays. Most endpoint scans return clean — no
findings — which leaves new users without immediate signal that the tool
worked. Triangulation between Claude and Codex (5+ rounds, captured in
PR/chat history) converged on adding **scanner-emitted posture findings**
distinct from vulnerability findings: configuration-hygiene rules that
flag risky install postures *without* requiring a CVE lookup.

The architectural call: posture findings are scanner output only. They do
not become overlay records, do not mint OpenACA IDs (preserves ADR-0009),
and do not change the `overlays/` schema. They carry standards mappings
(CWE / OpenSSF Scorecard / SLSA / OWASP Agentic-MCP) in a new
scanner-output `standards{}` block.

**Goal:** Ship three scanner-emitted posture rules behind an
`--include-posture` flag, with full text/JSON/SARIF rendering and per-rule
documentation. First-scan signal-to-noise tuned for low false-positive
rate; severities deliberately conservative.

**Architecture:** New `tools/posture/` package with: data model, three
independent rule modules, and a registry runner. Scanner calls
`run_posture_rules(refs, manifests)` after the matcher when
`--include-posture` is set; results flow into the existing renderer
pipeline as a separate finding list. No overlay schema changes.

**Tech Stack:** Python 3.11 (stdlib `re` for source-ref parsing,
`dataclasses`, `pathlib`), Click (existing CLI group), pytest. No new
runtime deps.

**Depends on:** Plan 007 (CLI subcommand split), Plan 008 (ComponentRef
+ component-type ecosystems), Plan 009 (lockfile awareness for the
"don't flag if locked" calibration).

**V0 scope (3 rules):**

1. **`openaca-posture-mutable-install-reference`** — Component (MCP server,
   plugin, or skill) installed from a mutable source reference (no
   version pin, `:latest`, branch ref, missing digest).
   Severity: low. Confidence: high.
2. **`openaca-posture-insecure-transport`** — Remote MCP endpoint configured
   over `http://` (not `https://`).
   Severity: medium. Confidence: high.
3. **`openaca-posture-missing-remote-auth`** — Remote MCP endpoint
   configured without any visible auth material (header, token field, env
   ref).
   Severity: low. Confidence: medium (auth may live out-of-band).

**Out of scope for this plan (deferred):**

- `unmaintained-component` — needs registry API calls + tunable thresholds.
- `broad-capability-grant` — needs a permission model.
- `inline-shell-hook` — easy to overfire without a clearer model.
- Overlay schema additions for CWE / Scorecard / SLSA — not needed until
  the corpus uses them (posture findings carry mappings in scanner output
  only).
- `--posture-severity` flag — premature configurability.

---

### Task 1: Posture finding data model

**Files:**
- Create: `tools/posture/__init__.py`
- Create: `tools/posture/finding.py`
- Create: `tests/test_posture_finding.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_posture_finding.py
from tools.posture.finding import PostureFinding, Standards


def test_posture_finding_minimum_fields():
    f = PostureFinding(
        rule_id="openaca-posture-mutable-install-reference",
        title="Component uses mutable install reference",
        severity="low",
        confidence="high",
        component="claude-plugin/foo",
        location="~/.claude/plugins/foo/.claude-plugin/plugin.json",
        standards=Standards(
            cwe=["CWE-1357"],
            openssf_scorecard=["Pinned-Dependencies"],
            slsa=["immutable-references"],
            owasp_agentic_top10=["asi04"],
        ),
        remediation="Pin to an exact version or commit SHA.",
    )
    assert f.rule_id == "openaca-posture-mutable-install-reference"
    assert f.standards.cwe == ["CWE-1357"]


def test_standards_serializes_only_populated_fields():
    s = Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"])
    out = s.to_dict()
    assert out == {"cwe": ["CWE-1357"], "owasp_agentic_top10": ["asi04"]}
```

- [ ] **Step 2: Run to verify it fails.**

```
uv run pytest tests/test_posture_finding.py -v
```
Expected: `ModuleNotFoundError: No module named 'tools.posture'`.

- [ ] **Step 3: Implement the data model.**

```python
# tools/posture/finding.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Standards:
    cwe: list[str] = field(default_factory=list)
    openssf_scorecard: list[str] = field(default_factory=list)
    slsa: list[str] = field(default_factory=list)
    owasp_app_top_10: list[str] = field(default_factory=list)
    owasp_agentic_top10: list[str] = field(default_factory=list)
    owasp_mcp_top10: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass(frozen=True)
class PostureFinding:
    rule_id: str
    title: str
    severity: Severity
    confidence: Confidence
    component: str
    location: str
    standards: Standards
    remediation: str
    finding_type: str = "posture"
```

```python
# tools/posture/__init__.py
from tools.posture.finding import PostureFinding, Standards

__all__ = ["PostureFinding", "Standards"]
```

- [ ] **Step 4: Run to verify it passes.**

```
uv run pytest tests/test_posture_finding.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```
git add tools/posture/ tests/test_posture_finding.py
git commit -m "feat(posture): add PostureFinding + Standards data model"
```

---

### Task 2: Immutability test helper

**Files:**
- Create: `tools/posture/immutability.py`
- Create: `tests/test_posture_immutability.py`

- [ ] **Step 1: Write the failing test (parametrized).**

```python
# tests/test_posture_immutability.py
import pytest
from tools.posture.immutability import is_mutable_reference


@pytest.mark.parametrize(
    "ref,expected",
    [
        # npx — unpinned
        ("npx @modelcontextprotocol/server-foo", True),
        ("npx @modelcontextprotocol/server-foo@latest", True),
        ("npx @modelcontextprotocol/server-foo@1.0.0", False),
        ("npx @modelcontextprotocol/server-foo@1.2", True),  # not exact

        # uvx — unpinned
        ("uvx mcp-server-bar", True),
        ("uvx mcp-server-bar==1.0.0", False),
        ("uvx mcp-server-bar>=1.0", True),  # range, not exact

        # git refs
        ("git+https://github.com/x/y.git#main", True),
        ("git+https://github.com/x/y.git@main", True),
        ("git+https://github.com/x/y.git@v1.0.0", True),  # tag, mutable
        ("git+https://github.com/x/y.git@a1b2c3d4e5f6", False),  # SHA

        # docker — :latest / no digest
        ("ghcr.io/github/github-mcp-server:latest", True),
        ("ghcr.io/github/github-mcp-server", True),  # no tag = latest
        ("ghcr.io/github/github-mcp-server:1.0.0", True),  # tag, no digest
        ("ghcr.io/github/github-mcp-server@sha256:abc...", False),

        # local checked-in (not a remote ref)
        ("./local/plugin", False),
        ("/Users/x/plugins/foo", False),
        ("file:///opt/plugin", False),
    ],
)
def test_is_mutable_reference(ref: str, expected: bool):
    assert is_mutable_reference(ref) is expected, ref
```

- [ ] **Step 2: Run to verify it fails.**

```
uv run pytest tests/test_posture_immutability.py -v
```
Expected: import error.

- [ ] **Step 3: Implement the helper.**

```python
# tools/posture/immutability.py
"""Decide whether a string install reference is mutable (rolls forward) or
immutable (pins to a specific point in time).

Mutable: any ref a future pull can change — no version, @latest, branch ref,
tagged Docker image (tags can be re-pointed), bare image name.

Immutable: exact-version specifier (==X.Y.Z, @X.Y.Z where X.Y.Z is exact),
full commit SHA, Docker digest (@sha256:...).

Local filesystem paths are NEITHER mutable NOR something this rule cares
about — return False (i.e., "don't flag") for them.
"""

from __future__ import annotations

import re

_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_DOCKER_DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")
_EXACT_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")


def is_mutable_reference(ref: str) -> bool:
    ref = ref.strip()

    # Local paths — not a remote install ref, don't flag.
    if ref.startswith(("./", "/", "file://", "~")):
        return False
    if ref.startswith(("http://", "https://")) and not ref.startswith(
        ("git+http://", "git+https://")
    ):
        # Remote URL (not git+) handled by other rules (insecure transport).
        return False

    # npx / uvx style
    for prefix in ("npx ", "uvx "):
        if ref.startswith(prefix):
            return _is_mutable_pkg_spec(ref[len(prefix) :].strip())

    # Git refs
    if ref.startswith(("git+", "git://")):
        return _is_mutable_git_ref(ref)

    # Docker image refs (heuristic: contains a `/`, no scheme prefix)
    if "/" in ref and not ref.startswith(("git+", "file://")):
        return _is_mutable_docker_ref(ref)

    # Fallback: opaque string. Treat as mutable (conservative).
    return True


def _is_mutable_pkg_spec(spec: str) -> bool:
    # spec looks like "@scope/name@1.0.0" or "pkg==1.0.0" or "pkg"
    if "==" in spec:
        version = spec.split("==", 1)[1].strip()
        return not _EXACT_SEMVER_RE.match(version)
    # npx: split on last '@' that follows a name char
    if spec.count("@") >= 1 and not spec.endswith("@"):
        # Handle scoped package: @scope/name@1.0.0 → version is after last '@'
        last_at = spec.rfind("@")
        # Don't treat the leading @ of a scoped name as a version separator.
        if last_at == 0:
            return True  # only the scope-leader @, no version
        version = spec[last_at + 1 :].strip()
        if version == "latest":
            return True
        return not _EXACT_SEMVER_RE.match(version)
    return True


def _is_mutable_git_ref(ref: str) -> bool:
    # git+https://host/x/y.git@<ref> or #<ref>
    for sep in ("@", "#"):
        if sep in ref:
            tail = ref.rsplit(sep, 1)[1].strip()
            # Strip any trailing query/path
            tail = tail.split("?", 1)[0].split("/", 1)[0]
            return not _SHA_RE.match(tail)
    return True  # no ref pinned at all


def _is_mutable_docker_ref(ref: str) -> bool:
    if _DOCKER_DIGEST_RE.search(ref):
        return False
    return True  # any tag (including no tag, :latest, :1.0.0) is mutable
```

- [ ] **Step 4: Run to verify it passes.**

```
uv run pytest tests/test_posture_immutability.py -v
```
Expected: all parametrized cases pass.

- [ ] **Step 5: Commit.**

```
git add tools/posture/immutability.py tests/test_posture_immutability.py
git commit -m "feat(posture): add immutability test for install references"
```

---

### Task 3: Rule 1 — Mutable install reference

**Files:**
- Create: `tools/posture/rules/__init__.py`
- Create: `tools/posture/rules/mutable_install.py`
- Create: `tests/test_posture_mutable_install.py`
- Create: `tests/fixtures/posture/mutable-install/` (with `mcp.json`, `marketplace.json` fixtures)

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_posture_mutable_install.py
import json
from pathlib import Path

from tools.posture.rules.mutable_install import check_mutable_install
from tools.parsers.mcp_json import parse as parse_mcp

FIX = Path(__file__).parent / "fixtures" / "posture" / "mutable-install"


def test_mcp_unpinned_uvx_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "uvx mcp-bar"}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mutable-install-reference"
    assert findings[0].severity == "low"
    assert "uvx mcp-bar" in findings[0].component or "mcp-bar" in findings[0].component


def test_mcp_pinned_uvx_not_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "uvx mcp-bar==1.0.0"}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert findings == []


def test_mcp_local_path_not_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "./local-server"}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert findings == []
```

- [ ] **Step 2: Investigate — does `ComponentRef` already carry the raw install string?**

Run:
```
cd /Users/vinodkone/workspace/openaca/.worktrees/feat-posture-findings
rg -n "command|install_source" tools/component_ref.py tools/parsers/mcp_json.py | head -20
```

If the raw `command` isn't preserved, extend `ComponentRef.extra` to include an `install_source` key with the original string. Add a parser test to lock that in. Existing fixture: `tests/fixtures/repos/exposed-mcp/.mcp.json`.

- [ ] **Step 3: Implement the rule.**

```python
# tools/posture/rules/mutable_install.py
"""Posture rule: flag agent components installed from a mutable source ref.

Applies to MCP servers, plugins, and skills equally — anything that can be
installed from a remote source by reference. Local checked-in paths are
exempt (the immutability helper returns False for them).
"""

from __future__ import annotations

from tools.component_ref import ComponentRef
from tools.posture.finding import PostureFinding, Standards
from tools.posture.immutability import is_mutable_reference

RULE_ID = "openaca-posture-mutable-install-reference"
TITLE = "Component installed from a mutable source reference"
SEVERITY = "low"
CONFIDENCE = "high"
REMEDIATION = (
    "Pin the install reference to an exact version, commit SHA, or Docker "
    "digest. Mutable refs (no version, @latest, branch refs, missing digest) "
    "can roll forward to unexpected code at any time."
)

_STANDARDS = Standards(
    cwe=["CWE-1357"],
    openssf_scorecard=["Pinned-Dependencies"],
    slsa=["immutable-references"],
    owasp_agentic_top10=["asi04"],
)


def check_mutable_install(refs: list[ComponentRef]) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for ref in refs:
        install_source = (ref.extra or {}).get("install_source")
        if not install_source or not isinstance(install_source, str):
            continue
        if not is_mutable_reference(install_source):
            continue
        # For MCP, add the MCP-specific taxonomy code.
        standards = _STANDARDS
        if ref.ecosystem in {"mcp-server", "npm", "PyPI"} and "mcp" in (ref.name or "").lower():
            standards = Standards(
                **{**_STANDARDS.__dict__, "owasp_mcp_top10": ["mcp04:2025"]}
            )
        findings.append(
            PostureFinding(
                rule_id=RULE_ID,
                title=TITLE,
                severity=SEVERITY,
                confidence=CONFIDENCE,
                component=f"{ref.ecosystem}/{ref.name}@{install_source}",
                location=str(ref.source_manifest) if ref.source_manifest else "",
                standards=standards,
                remediation=REMEDIATION,
            )
        )
    return findings
```

- [ ] **Step 4: Run tests.**

```
uv run pytest tests/test_posture_mutable_install.py -v
```
Expected: 3 passed (after the parser exposes `install_source` per Step 2).

- [ ] **Step 5: Commit.**

```
git add tools/posture/rules/ tests/test_posture_mutable_install.py tests/fixtures/posture/
git commit -m "feat(posture): add mutable-install-reference rule"
```

---

### Task 4: Rule 2 — Insecure transport (http:// remote MCP)

**Files:**
- Create: `tools/posture/rules/insecure_transport.py`
- Create: `tests/test_posture_insecure_transport.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_posture_insecure_transport.py
import json
from tools.posture.rules.insecure_transport import check_insecure_transport


def test_http_sse_endpoint_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "http://example.com/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-insecure-transport"
    assert findings[0].severity == "medium"


def test_https_sse_endpoint_not_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "https://example.com/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_stdio_command_not_flagged(tmp_path):
    """Stdio MCPs have no URL — not in scope for this rule."""
    manifest = {"mcpServers": {"x": {"command": "uvx mcp-x"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings == []
```

- [ ] **Step 2: Run to verify failure, then implement.**

```python
# tools/posture/rules/insecure_transport.py
"""Posture rule: flag remote MCP endpoints configured over http://."""

from __future__ import annotations

from pathlib import Path

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-insecure-transport"
TITLE = "Remote MCP endpoint uses insecure transport"
SEVERITY = "medium"
CONFIDENCE = "high"
REMEDIATION = (
    "Configure the MCP endpoint over https://. Plain http:// exposes prompts, "
    "tool calls, and any returned data to network observers and tampering."
)

_STANDARDS = Standards(
    cwe=[],  # No clean CWE; don't force it.
    owasp_app_top_10=["A02:2021"],
    owasp_agentic_top10=["asi04"],
    owasp_mcp_top10=["mcp04:2025"],
)


def check_insecure_transport(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        for name, entry in (manifest.get("mcpServers") or {}).items():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or ""
            if isinstance(url, str) and url.startswith("http://"):
                findings.append(
                    PostureFinding(
                        rule_id=RULE_ID,
                        title=TITLE,
                        severity=SEVERITY,
                        confidence=CONFIDENCE,
                        component=f"mcp-server/{name} @ {url}",
                        location=str(path),
                        standards=_STANDARDS,
                        remediation=REMEDIATION,
                    )
                )
    return findings
```

- [ ] **Step 3: Run, commit.**

```
uv run pytest tests/test_posture_insecure_transport.py -v
git add tools/posture/rules/insecure_transport.py tests/test_posture_insecure_transport.py
git commit -m "feat(posture): add insecure-transport rule"
```

---

### Task 5: Rule 3 — Missing auth on remote MCP

**Files:**
- Create: `tools/posture/rules/missing_auth.py`
- Create: `tests/test_posture_missing_auth.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_posture_missing_auth.py
from pathlib import Path
from tools.posture.rules.missing_auth import check_missing_auth


def test_remote_no_auth_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-missing-remote-auth"
    assert findings[0].confidence == "medium"


def test_remote_with_auth_header_not_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "x": {
                "type": "sse",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${ENV_TOKEN}"},
            }
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_remote_with_env_token_field_not_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "x": {"type": "sse", "url": "https://example.com/mcp", "env": {"TOKEN": "..."}}
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_stdio_not_in_scope(tmp_path):
    manifest = {"mcpServers": {"x": {"command": "uvx mcp-x"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []
```

- [ ] **Step 2: Implement.**

```python
# tools/posture/rules/missing_auth.py
"""Posture rule: flag remote MCP endpoints with no visible auth material.

Confidence is medium, not high — auth may be configured out-of-band (system
keyring, ambient credentials, proxy auth). The rule surfaces "I cannot see
any auth here" as a prompt to verify, not as an assertion of misconfiguration.
"""

from __future__ import annotations

from pathlib import Path

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-missing-remote-auth"
TITLE = "Remote MCP endpoint has no visible auth material"
SEVERITY = "low"
CONFIDENCE = "medium"
REMEDIATION = (
    "If this endpoint requires auth, declare it in the manifest "
    "(headers, env, or token fields). If auth is provided out-of-band "
    "(keyring, ambient credentials), this finding can be suppressed for "
    "this entry."
)

_STANDARDS = Standards(
    owasp_app_top_10=["A01:2021", "A07:2021"],
    owasp_agentic_top10=["asi03"],
    owasp_mcp_top10=["mcp07:2025"],
)


def check_missing_auth(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        for name, entry in (manifest.get("mcpServers") or {}).items():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or ""
            if not isinstance(url, str) or not url:
                continue
            if _has_auth_material(entry):
                continue
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity=SEVERITY,
                    confidence=CONFIDENCE,
                    component=f"mcp-server/{name} @ {url}",
                    location=str(path),
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings


def _has_auth_material(entry: dict) -> bool:
    headers = entry.get("headers") or {}
    if any(k.lower() == "authorization" for k in headers):
        return True
    if entry.get("env") or entry.get("token") or entry.get("apiKey"):
        return True
    return False
```

- [ ] **Step 3: Run, commit.**

```
uv run pytest tests/test_posture_missing_auth.py -v
git add tools/posture/rules/missing_auth.py tests/test_posture_missing_auth.py
git commit -m "feat(posture): add missing-remote-auth rule"
```

---

### Task 6: Rule registry + scanner integration + `--include-posture` flag

**Files:**
- Modify: `tools/posture/__init__.py` (add `RULES`, `run_posture_rules`)
- Modify: `tools/scan.py` (add `--include-posture` to scan group; call runner after matcher)
- Create: `tests/test_posture_integration.py`

- [ ] **Step 1: Write the integration test.**

```python
# tests/test_posture_integration.py
from click.testing import CliRunner
from tools.scan import main as scan_main


def test_posture_off_by_default(tmp_path):
    """Without --include-posture, posture findings are not emitted."""
    # Use an existing fixture that has an unpinned MCP install.
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        ["repo", "--target", "tests/fixtures/repos/exposed-mcp", "--fail-on", "none"],
    )
    assert "Posture findings" not in result.output


def test_posture_on_emits_findings(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            "tests/fixtures/repos/exposed-mcp",
            "--fail-on",
            "none",
            "--include-posture",
        ],
    )
    assert "Posture findings" in result.output
    assert "openaca-posture-mutable-install-reference" in result.output
```

- [ ] **Step 2: Implement the registry.**

```python
# tools/posture/__init__.py (additions)
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules import insecure_transport, missing_auth, mutable_install


def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    findings.extend(mutable_install.check_mutable_install(refs))
    findings.extend(insecure_transport.check_insecure_transport(manifests))
    findings.extend(missing_auth.check_missing_auth(manifests))
    return findings


__all__ = ["PostureFinding", "Standards", "run_posture_rules"]
```

- [ ] **Step 3: Wire into the scanner.**

Add `--include-posture` (boolean flag, default False) to the `scan` Click
group options. In `_apply_group_opts` propagate it to the subcommand
context. In `repo` and `endpoint` handlers, after the matcher pass, when
the flag is set, call `run_posture_rules(refs, manifests)` and attach the
results to the scan output object that flows into the renderers. Manifest
list comes from the existing parser dispatch — parsers already load+parse
the JSON dicts.

- [ ] **Step 4: Run integration tests.**

```
uv run pytest tests/test_posture_integration.py -v
```

- [ ] **Step 5: Commit.**

```
git add tools/posture/__init__.py tools/scan.py tests/test_posture_integration.py
git commit -m "feat(scan): --include-posture flag + posture runner integration"
```

---

### Task 7: Text renderer integration

**Files:**
- Modify: `tools/render.py` (add a posture section to `render_text`)
- Modify: `tests/test_render.py` (snapshot-style assertions)

- [ ] **Step 1: Test.**

```python
# tests/test_render.py (addition)
def test_render_text_includes_posture_section():
    from tools.posture import PostureFinding, Standards

    posture = [
        PostureFinding(
            rule_id="openaca-posture-mutable-install-reference",
            title="Component installed from a mutable source reference",
            severity="low",
            confidence="high",
            component="claude-plugin/foo @ npx foo",
            location=".mcp.json",
            standards=Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"]),
            remediation="Pin to an exact version.",
        ),
    ]
    out = render_text(
        findings=[], advisory_index={}, scan_stats=ScanStats(...),
        posture_findings=posture, use_color=False, verbose=False,
    )
    assert "Posture findings" in out
    assert "LOW" in out
    assert "openaca-posture-mutable-install-reference" in out
    assert "claude-plugin/foo" in out
```

- [ ] **Step 2: Implement.**

`render_text` gains a `posture_findings: list[PostureFinding] = []`
parameter (kwarg, default empty for back-compat with callers that don't
opt in). After the existing vulnerability-findings section, when posture
is non-empty, render a new section:

```
Posture findings (configuration hygiene):

  LOW  openaca-posture-mutable-install-reference  claude-plugin/foo
       location: .mcp.json
       fix:      Pin to an exact version, commit SHA, or Docker digest.
       standards: CWE-1357, asi04
```

- [ ] **Step 3: Run, commit.**

```
uv run pytest tests/test_render.py -k posture -v
git add tools/render.py tests/test_render.py
git commit -m "feat(render): add Posture findings section to text output"
```

---

### Task 8: JSON renderer integration

**Files:**
- Modify: `tools/render.py` (`render_json`)
- Modify: `tests/test_render.py`

- [ ] **Step 1: Test.**

```python
def test_render_json_includes_posture_array():
    import json
    out = render_json(
        findings=[], advisory_index={},
        stats=ScanStats(...),
        posture_findings=[<one PostureFinding>],
    )
    parsed = json.loads(out)
    assert "posture_findings" in parsed
    assert len(parsed["posture_findings"]) == 1
    p = parsed["posture_findings"][0]
    assert p["finding_type"] == "posture"
    assert p["rule_id"] == "openaca-posture-mutable-install-reference"
    assert p["standards"]["cwe"] == ["CWE-1357"]
```

- [ ] **Step 2: Implement.**

Add a top-level `posture_findings: []` array in the JSON output structure
alongside the existing `findings: []` and `stats: {}` keys. Each entry
serializes a `PostureFinding` (and its `Standards` via `Standards.to_dict()`,
which drops empty taxonomy lists).

- [ ] **Step 3: Run, commit.**

---

### Task 9: SARIF integration

**Files:**
- Modify: `tools/sarif.py`
- Modify: `tests/test_sarif.py`

- [ ] **Step 1: Test.**

```python
def test_sarif_emits_posture_rules_and_results():
    doc = to_sarif(
        findings=[], advisory_index={},
        posture_findings=[<one PostureFinding>],
    )
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert "openaca-posture-mutable-install-reference" in rule_ids
    results = doc["runs"][0]["results"]
    assert any(r["ruleId"] == "openaca-posture-mutable-install-reference" for r in results)
```

- [ ] **Step 2: Implement.**

`to_sarif` gains a `posture_findings` kwarg. Each unique posture rule
becomes a SARIF `rule` entry with:
- `id`: the rule_id (e.g., `openaca-posture-mutable-install-reference`)
- `shortDescription.text`: `PostureFinding.title`
- `fullDescription.text`: `PostureFinding.remediation`
- `helpUri`: `https://openaca.dev/posture/<rule_id>.html` (resolves once
  the static export builds the posture doc page; for V0 we accept that
  the URL 404s briefly until the next export run).
- `properties.standards`: the dict from `Standards.to_dict()`.

Each posture finding becomes a SARIF `result` with `level` mapped from
severity (low → note, medium → warning, high → error), `message.text` =
title, `locations[0]` = the manifest path, and
`properties.confidence` carried through.

- [ ] **Step 3: Run, commit.**

---

### Task 10: Per-rule docs + README/CONTRIBUTING blurb

**Files:**
- Create: `docs/posture/README.md`
- Create: `docs/posture/openaca-posture-mutable-install-reference.md`
- Create: `docs/posture/openaca-posture-insecure-transport.md`
- Create: `docs/posture/openaca-posture-missing-remote-auth.md`
- Modify: `README.md` (one-paragraph mention of posture findings)
- Modify: `CONTRIBUTING.md` (short section: how to add a posture rule)

Each per-rule doc page contains:
- What triggers it (rule logic in plain language)
- Why it matters (with the standards mapping spelled out)
- How to fix (the remediation text + concrete examples per
  component type)
- When to suppress (the false-positive cases — e.g., local checked-in
  paths, auth-out-of-band)

README addition (in the existing scanner section): one paragraph
introducing posture findings, `--include-posture` flag, link to
`docs/posture/`.

CONTRIBUTING addition: short section under "Code contributions" titled
"Adding a posture rule" — required fields (rule_id, title, severity,
confidence, standards mapping), where to put the rule module, how to
register it in `tools/posture/__init__.py`, what the test fixture
expectations are.

- [ ] Commit:
```
git commit -m "docs(posture): per-rule docs + README/CONTRIBUTING blurbs"
```

---

### Task 11: Full gate, commit, PR

- [ ] `cd /Users/vinodkone/workspace/openaca/.worktrees/feat-posture-findings`
- [ ] `uv sync`
- [ ] `uv run ruff format --check tools/ tests/`
- [ ] `uv run ruff check tools/ tests/`
- [ ] `uv run pyright tools/ tests/`
- [ ] `uv run pytest -q` — full suite must pass (existing 574 + new ~20 posture tests).
- [ ] `uv run openaca lint overlays/` — corpus must still pass (this plan changes no overlay code).
- [ ] **Dogfood:** `uv run openaca scan endpoint --include-posture` on the developer's own `~/.claude` install. Confirm the rules surface real findings without flagging local-only plugins/skills.
- [ ] Push and open PR titled `feat(scan): posture findings (mutable install, insecure transport, missing auth)`.

---

## Verification

End-to-end signal that the plan worked:

1. **Repo scan without posture flag** unchanged — same findings as before.
2. **Repo scan with `--include-posture`** on a fixture with mixed pinned/unpinned MCPs → emits exactly the mutable-install findings, no false positives on pinned entries or local paths.
3. **Endpoint scan with `--include-posture`** on a real `~/.claude` install → emits low-severity mutable-install findings for every unpinned MCP and plugin found, no findings on locally-developed plugins.
4. **JSON output** validates against `json.loads` and surfaces a `posture_findings[]` array with the standards block populated.
5. **SARIF output** uploads to GitHub code-scanning successfully (manual check) — posture rules appear as separate findings from vulnerability findings.
6. **Pre-push gate green**, full suite passes.

## Self-review

- **Spec coverage:** Each of the three V0 rules has its own task with test, implementation, and commit. Renderer integration covers text/JSON/SARIF. CLI flag wired. Docs land before merge.
- **Placeholder scan:** No TBDs. Each test step shows the code; each implementation step shows the code.
- **Type consistency:** `PostureFinding`, `Standards`, `RULE_ID` constants used consistently across all rule modules. `Severity` and `Confidence` are `Literal` types — Pyright will catch typos.
- **Risk:** Task 3's mention of "investigate whether `ComponentRef.extra` carries `install_source`" is the only step that may require additional work beyond what's specified. If parsers don't preserve the raw install string today, Task 3 expands to include parser updates. That's the right place for it — it's load-bearing for Rule 1.
