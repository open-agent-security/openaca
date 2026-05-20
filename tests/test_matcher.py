from tools.component_ref import ComponentRef
from tools.matcher import match


def make_advisory(openaca_id: str, ecosystem: str, name: str, fixed: str) -> dict:
    return {
        "id": openaca_id,
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": name},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": fixed}],
                    }
                ],
            }
        ],
    }


def test_match_npm_in_range():
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-0001"
    assert findings[0].component is ref
    assert findings[0].confidence == "high"


def test_match_npm_at_fixed_version_excluded():
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.2.3",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_match_npm_above_fixed_version_excluded():
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.5.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_unparseable_version_emits_low_confidence():
    """`^1.0.0`-style spec can't resolve to a single version — flag it but don't drop it."""
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="^1.0.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "low"


def test_match_pypi_pinned():
    advisories = [make_advisory("CVE-2026-0004", "PyPI", "aws-mcp-server", "0.3.2")]
    ref = ComponentRef(
        ecosystem="PyPI",
        name="aws-mcp-server",
        version="0.3.0",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.aws",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].confidence == "high"


def test_generic_skill_ref_matches_legacy_claude_skill_advisory():
    advisories = [make_advisory("CVE-2026-SKILL", "claude-skill", "vulnerable-skill", "1.0.0")]
    ref = ComponentRef(
        ecosystem="skill",
        name="vulnerable-skill",
        version="0.9.0",
        component_identity="skill/vulnerable-skill@0.9.0",
        source_manifest="SKILL.md",
        source_locator="$.frontmatter",
    )

    findings = match(refs=[ref], advisories=advisories)

    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-SKILL"
    assert findings[0].confidence == "high"


def test_legacy_claude_skill_ref_matches_generic_skill_advisory():
    advisories = [make_advisory("CVE-2026-SKILL", "skill", "vulnerable-skill", "1.0.0")]
    ref = ComponentRef(
        ecosystem="claude-skill",
        name="vulnerable-skill",
        version="0.9.0",
        component_identity="claude-skill/vulnerable-skill@0.9.0",
        source_manifest="SKILL.md",
        source_locator="$.frontmatter",
    )

    findings = match(refs=[ref], advisories=advisories)

    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-SKILL"
    assert findings[0].confidence == "high"


def test_no_match_when_package_name_differs():
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="npm",
        name="some-other-package",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_no_match_when_ecosystem_differs():
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        ecosystem="PyPI",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.x",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_unpinned_npx_matches_affected_package_with_unknown_confidence():
    """The headline cross-layer behavior: an unpinned mcp.json npx launch
    of a known-vulnerable package emits an 'unknown' finding so the consumer
    knows to pin the version."""
    advisories = [make_advisory("CVE-2026-0003", "npm", "@akoskm/create-mcp-server-stdio", "1.0.4")]
    ref = ComponentRef(
        component_identity="mcp-stdio/npx-unpinned:@akoskm/create-mcp-server-stdio",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.x",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-0003"
    assert findings[0].confidence == "unknown"


def test_unpinned_uvx_matches_pypi_advisory():
    advisories = [make_advisory("CVE-2026-0004", "PyPI", "aws-mcp-server", "0.3.2")]
    ref = ComponentRef(
        component_identity="mcp-stdio/uvx-unpinned:aws-mcp-server",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.aws",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-0004"
    assert findings[0].confidence == "unknown"


def test_binary_component_identity_does_not_match():
    """An mcp-stdio/binary:<path> identity has no package info; must not falsely match."""
    advisories = [make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")]
    ref = ComponentRef(
        component_identity="mcp-stdio/binary:/opt/local/bin/custom",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.custom",
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_in_range_multiple_event_windows():
    """A single OSV range can encode disjoint vulnerable intervals via alternating
    introduced/fixed events. Versions in each window must be detected; versions
    between windows must not be."""
    advisory = {
        "id": "CVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "multi-window-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [
                            {"introduced": "1.0.0"},
                            {"fixed": "1.2.0"},
                            {"introduced": "2.0.0"},
                            {"fixed": "2.1.0"},
                        ],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="multi-window-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("1.1.0")], [advisory])) == 1  # in first window
    assert len(match([ref("2.0.5")], [advisory])) == 1  # in second window
    assert len(match([ref("1.5.0")], [advisory])) == 0  # between windows


def test_in_range_open_ended_no_fixed():
    """An advisory with no fixed event is still-unpatched — all versions at or
    after introduced are vulnerable."""
    advisory = {
        "id": "CVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "unpatched-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "1.0.0"}],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="unpatched-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("1.0.0")], [advisory])) == 1  # at introduced boundary
    assert len(match([ref("9.9.9")], [advisory])) == 1  # well above introduced
    assert len(match([ref("0.9.9")], [advisory])) == 0  # before introduced


def test_in_range_last_affected_inclusive_bound():
    """last_affected closes a window inclusively: versions at and below the bound
    are vulnerable; versions above it must not be flagged as false positives."""
    advisory = {
        "id": "CVE-2026-TEST",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-06T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "last-affected-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"last_affected": "2.1.214"}],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="last-affected-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("2.1.214")], [advisory])) == 1  # at last_affected boundary (inclusive)
    assert len(match([ref("1.0.0")], [advisory])) == 1  # within range
    assert len(match([ref("2.1.215")], [advisory])) == 0  # above last_affected — not vulnerable
    assert len(match([ref("3.0.0")], [advisory])) == 0  # well above — must not false-positive


def test_in_range_limit_exclusive_bound():
    """limit event closes the window with an exclusive upper bound (version < limit)."""
    advisory = {
        "id": "CVE-2026-9999",
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-09T00:00:00Z",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "limit-pkg"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "1.0.0"}, {"limit": "2.0.0"}],
                    }
                ],
            }
        ],
    }

    def ref(v: str) -> ComponentRef:
        return ComponentRef(
            ecosystem="npm",
            name="limit-pkg",
            version=v,
            source_manifest="package.json",
            source_locator="dependencies",
        )

    assert len(match([ref("1.0.0")], [advisory])) == 1  # at introduced boundary (inclusive)
    assert len(match([ref("1.5.0")], [advisory])) == 1  # within range
    assert len(match([ref("2.0.0")], [advisory])) == 0  # at limit — exclusive, not vulnerable
    assert len(match([ref("2.0.1")], [advisory])) == 0  # above limit — not vulnerable
    assert len(match([ref("0.9.9")], [advisory])) == 0  # below introduced — not vulnerable


def make_identity_advisory(openaca_id: str, component_identity: str) -> dict:
    return {
        "id": openaca_id,
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-05-11T00:00:00Z",
        "database_specific": {"openaca": {"component_identity": component_identity}},
    }


def test_claude_command_identity_match():
    """claude-command refs must route to _match_by_identity, not _match_versioned.
    The parser sets both ecosystem+name (for inventory) but there are no version
    semantics — the advisory carries the target identity, not a version range."""
    advisory = make_identity_advisory("CVE-2026-9001", "claude-command/deploy")
    ref = ComponentRef(
        ecosystem="claude-command",
        name="deploy",
        component_identity="claude-command/deploy",
        source_manifest=".claude/commands/deploy.md",
        source_locator="$",
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-9001"
    assert findings[0].confidence == "high"


def test_claude_agent_identity_match():
    """claude-agent refs route to _match_by_identity."""
    advisory = make_identity_advisory("CVE-2026-9002", "claude-agent/reviewer")
    ref = ComponentRef(
        ecosystem="claude-agent",
        name="reviewer",
        component_identity="claude-agent/reviewer",
        source_manifest=".claude/agents/reviewer.md",
        source_locator="$",
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-9002"
    assert findings[0].confidence == "high"


def test_claude_command_identity_mismatch_no_finding():
    """Different identity string — must not match even if ecosystem+name agree."""
    advisory = make_identity_advisory("CVE-2026-9001", "claude-command/other-command")
    ref = ComponentRef(
        ecosystem="claude-command",
        name="deploy",
        component_identity="claude-command/deploy",
        source_manifest=".claude/commands/deploy.md",
        source_locator="$",
    )
    assert match(refs=[ref], advisories=[advisory]) == []


def test_claude_command_does_not_false_match_via_versioned_path():
    """Regression: before the fix, a claude-command ref with ecosystem+name would
    short-circuit to _match_versioned and potentially match an advisory whose
    affected[*].package.name happens to equal the command name. Must not fire."""
    advisory = make_advisory("CVE-2026-9003", "claude-command", "deploy", "2.0.0")
    ref = ComponentRef(
        ecosystem="claude-command",
        name="deploy",
        component_identity="claude-command/deploy",
        source_manifest=".claude/commands/deploy.md",
        source_locator="$",
    )
    # The versioned advisory uses range semantics; the identity-only ref carries
    # no version.  Under the old (broken) routing this would emit a low-confidence
    # finding.  Under the fixed routing it must produce nothing.
    assert match(refs=[ref], advisories=[advisory]) == []


def test_no_duplicate_findings_when_advisory_has_multiple_ranges():
    """An advisory may list multiple ranges per affected entry (e.g.,
    discrete events). Same component+advisory pair should produce one
    finding, not one per range."""
    advisory = make_advisory("CVE-2026-0001", "npm", "@cyanheads/git-mcp-server", "1.2.3")
    advisory["affected"][0]["ranges"].append(
        {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0.0"}]}
    )
    ref = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1


def test_match_claude_plugin_in_range():
    """Plan 007: claude-plugin ecosystem flows through the existing
    _match_versioned path. Plugin advisories must fire when the ref carries
    ecosystem='claude-plugin' alongside name+version."""
    advisories = [make_advisory("CVE-2026-9999", "claude-plugin", "deployment-tools", "1.3.0")]
    ref = ComponentRef(
        ecosystem="claude-plugin",
        name="deployment-tools",
        version="1.2.0",
        component_identity="claude-plugin/deployment-tools@1.2.0",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.deployment-tools@market[0]",
    )
    findings = match(refs=[ref], advisories=advisories)
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-9999"
    assert findings[0].confidence == "high"
    # Plugin itself is direct — no attribution.
    assert findings[0].attributed_to is None
    assert findings[0].component.attributed_to is None


def test_finding_mirrors_component_attribution():
    """Per ADR-0006: Finding.attributed_to mirrors ComponentRef.attributed_to.
    Test both attribution-set and attribution-None cases."""
    advisory = make_advisory("CVE-2026-9998", "npm", "lodash", "5.0.0")

    via_plugin = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.0",
        attributed_to="claude-plugin/supabase@0.1.6",
        source_manifest="package-lock.json",
        source_locator="$.packages",
    )
    direct = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.0",
        source_manifest="package.json",
        source_locator="dependencies",
    )
    findings = match(refs=[via_plugin, direct], advisories=[advisory])
    assert len(findings) == 2

    via_finding = next(f for f in findings if f.component is via_plugin)
    direct_finding = next(f for f in findings if f.component is direct)

    assert via_finding.attributed_to == "claude-plugin/supabase@0.1.6"
    assert via_finding.attributed_to == via_finding.component.attributed_to

    assert direct_finding.attributed_to is None
    assert direct_finding.component.attributed_to is None
