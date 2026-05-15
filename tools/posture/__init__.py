"""Scanner-side posture-finding rules (plan 014, ADR-0009).

Posture findings are emitted by the scanner only — they never become overlay
records, never mint OpenACA IDs, and never change the corpus schema. They
carry a `standards{}` block (CWE / OpenSSF Scorecard / SLSA / OWASP) in
scanner output. Gated behind `--include-posture` to keep the default scan
output strictly vulnerability findings.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.gitignore import is_ignored, load_gitignore_spec
from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules import insecure_transport, missing_auth, mutable_install

__all__ = [
    "PostureFinding",
    "Standards",
    "collect_mcp_manifests",
    "run_posture_rules",
]


_MCP_MANIFEST_NAMES: frozenset[str] = frozenset(
    {"mcp.json", ".mcp.json", "claude_desktop_config.json"}
)
_PLUGIN_MANIFEST_NAME = "plugin.json"
_PLUGIN_MANIFEST_PARENT_DIR = ".claude-plugin"


def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    """Run all V0 posture rules and concatenate their findings."""
    findings: list[PostureFinding] = []
    findings.extend(mutable_install.check_mutable_install(refs))
    findings.extend(insecure_transport.check_insecure_transport(manifests))
    findings.extend(missing_auth.check_missing_auth(manifests))
    return findings


def collect_mcp_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    """Walk one or more roots for MCP-shaped manifests and return parsed dicts.

    Used by the URL-shape rules (insecure_transport, missing_auth) that need
    the raw manifest to inspect `mcpServers[*].url` and adjacent fields.
    Parse failures are silently dropped — these rules are best-effort and
    should never abort a scan.

    `.git/` is always skipped regardless of `include_gitignored`, consistent
    with the main repo scanner (`parse_repo_grouped`). When
    `include_gitignored=False`, paths matched by `<root>/.gitignore` are also
    skipped, keeping posture scope consistent with the main repo scan.
    """
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for name in _MCP_MANIFEST_NAMES:
            for path in root.rglob(name):
                if is_ignored(path.relative_to(root), spec):
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict):
                    out.append((path, data))
        for path in root.rglob(_PLUGIN_MANIFEST_NAME):
            if path.parent.name != _PLUGIN_MANIFEST_PARENT_DIR:
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append((path, data))
    return out
