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


def test_github_commit_sha_does_not_emit_false_low_confidence():
    """A commit SHA is a concrete pinned ref, not a range/spec.

    The matcher can't evaluate GIT ranges (no _in_range support for commit
    SHAs), so github-ecosystem refs with an unparseable version must be
    skipped silently — not emitted as a low-confidence "pin to verify" finding.
    """
    advisories = [make_advisory("GHSA-1234-5678-9abc", "github", "oraios/serena", "1.0.0")]
    ref = ComponentRef(
        ecosystem="github",
        name="oraios/serena",
        version="0123456789abcdef0123456789abcdef01234567",
        source_manifest="mcp.json",
        source_locator="$.mcpServers.serena",
    )
    assert match(refs=[ref], advisories=advisories) == []


def make_git_advisory(openaca_id: str, repo: str, versions: list[str] | None = None) -> dict:
    advisory = {
        "id": openaca_id,
        "type": "vulnerability",
        "summary": "test",
        "modified": "2026-06-02T00:00:00Z",
        "affected": [
            {
                "ranges": [
                    {
                        "type": "GIT",
                        "repo": repo,
                        "events": [{"introduced": "0"}],
                    }
                ],
            }
        ],
    }
    if versions is not None:
        advisory["affected"][0]["versions"] = versions
    return advisory


def test_github_commit_sha_matches_federated_git_advisory_repo():
    sha = "0123456789abcdef0123456789abcdef01234567"
    advisory = make_git_advisory("GHSA-git-commit", "https://github.com/oraios/serena.git")
    advisory["database_specific"] = {
        "openaca": {
            "osv_query_matches": [
                {
                    "kind": "git_commit",
                    "repo": "github.com/oraios/serena",
                    "ref": sha,
                }
            ]
        }
    }
    ref = ComponentRef(
        ecosystem="github",
        name="oraios/serena",
        version=sha,
        source_manifest="mcp.json",
        source_locator="$.mcpServers.serena",
    )

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "GHSA-git-commit"
    assert findings[0].confidence == "high"


def test_github_commit_sha_requires_osv_commit_query_provenance():
    sha = "0123456789abcdef0123456789abcdef01234567"
    advisory = make_git_advisory("GHSA-git-commit", "https://github.com/oraios/serena.git")
    ref = ComponentRef(ecosystem="github", name="oraios/serena", version=sha)

    assert match(refs=[ref], advisories=[advisory]) == []


def test_github_commit_sha_does_not_match_different_git_repo():
    sha = "0123456789abcdef0123456789abcdef01234567"
    advisory = make_git_advisory("GHSA-git-other", "https://github.com/other/repo.git")
    advisory["database_specific"] = {
        "openaca": {
            "osv_query_matches": [
                {
                    "kind": "git_commit",
                    "repo": "github.com/other/repo",
                    "ref": sha,
                }
            ]
        }
    }
    ref = ComponentRef(ecosystem="github", name="oraios/serena", version=sha)

    assert match(refs=[ref], advisories=[advisory]) == []


def test_github_mutable_ref_matches_git_version_list():
    advisory = make_git_advisory(
        "GHSA-git-tag", "https://github.com/oraios/serena.git", versions=["v1.0.0"]
    )
    ref = ComponentRef(ecosystem="github", name="oraios/serena", extra={"git_ref": "v1.0.0"})

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "GHSA-git-tag"
    assert findings[0].confidence == "high"


def test_github_mutable_ref_does_not_match_git_version_absent_from_advisory():
    advisory = make_git_advisory(
        "GHSA-git-tag", "https://github.com/oraios/serena.git", versions=["v2.0.0"]
    )
    ref = ComponentRef(ecosystem="github", name="oraios/serena", extra={"git_ref": "v1.0.0"})

    assert match(refs=[ref], advisories=[advisory]) == []


def test_github_mutable_ref_trusts_osv_git_version_provenance():
    # OSV matched our GIT tag query server-side (tag resolution / GIT ranges),
    # so the record carries no explicit `versions[]` entry for the tag. The
    # matcher must trust the stamped git_version provenance the same way it
    # trusts git_commit — otherwise the fetched record is dropped (false neg).
    advisory = make_git_advisory("GHSA-git-tag", "https://github.com/oraios/serena.git")
    advisory["database_specific"] = {
        "openaca": {
            "osv_query_matches": [
                {
                    "kind": "git_version",
                    "repo": "github.com/oraios/serena",
                    "ref": "v1.0.0",
                }
            ]
        }
    }
    ref = ComponentRef(ecosystem="github", name="oraios/serena", extra={"git_ref": "v1.0.0"})

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "GHSA-git-tag"
    assert findings[0].confidence == "high"


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


def test_source_less_skill_ref_matches_component_identity_advisory():
    advisory = make_identity_advisory("CVE-2026-SKILL", "skill/vulnerable-skill@0.9.0")
    ref = ComponentRef(
        name="vulnerable-skill",
        version="0.9.0",
        component_identity="skill/vulnerable-skill@0.9.0",
        source_manifest="SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill"},
    )

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-SKILL"
    assert findings[0].confidence == "high"


def test_source_less_skill_ref_does_not_match_component_type_ecosystem_advisory():
    advisories = [make_advisory("CVE-2026-SKILL", "skill", "vulnerable-skill", "1.0.0")]
    ref = ComponentRef(
        name="vulnerable-skill",
        version="0.9.0",
        component_identity="skill/vulnerable-skill@0.9.0",
        source_manifest="SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill"},
    )

    assert match(refs=[ref], advisories=advisories) == []


def test_source_less_plugin_ref_matches_component_identity_advisory():
    advisory = make_identity_advisory("CVE-2026-PLUGIN", "plugin/deployment-tools")
    ref = ComponentRef(
        name="deployment-tools",
        version="1.2.0",
        component_identity="plugin/deployment-tools",
        source_manifest="plugin.json",
        source_locator="$",
        extra={"component_type": "plugin"},
    )

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-PLUGIN"
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
    """Source-less command refs match explicit component_identity advisories."""
    advisory = make_identity_advisory("CVE-2026-9001", "claude-command/deploy")
    ref = ComponentRef(
        name="deploy",
        component_identity="claude-command/deploy",
        source_manifest=".claude/commands/deploy.md",
        source_locator="$",
        extra={"component_type": "command"},
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-9001"
    assert findings[0].confidence == "high"


def test_claude_agent_identity_match():
    """Source-less agent refs match explicit component_identity advisories."""
    advisory = make_identity_advisory("CVE-2026-9002", "claude-agent/reviewer")
    ref = ComponentRef(
        name="reviewer",
        component_identity="claude-agent/reviewer",
        source_manifest=".claude/agents/reviewer.md",
        source_locator="$",
        extra={"component_type": "agent"},
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-9002"
    assert findings[0].confidence == "high"


def test_claude_command_identity_mismatch_no_finding():
    """Different identity string — must not match even if names agree."""
    advisory = make_identity_advisory("CVE-2026-9001", "claude-command/other-command")
    ref = ComponentRef(
        name="deploy",
        component_identity="claude-command/deploy",
        source_manifest=".claude/commands/deploy.md",
        source_locator="$",
        extra={"component_type": "command"},
    )
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


def test_source_less_plugin_ref_does_not_match_component_type_ecosystem_advisory():
    advisories = [make_advisory("CVE-2026-9999", "claude-plugin", "deployment-tools", "1.3.0")]
    ref = ComponentRef(
        name="deployment-tools",
        version="1.2.0",
        component_identity="plugin/deployment-tools",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.deployment-tools@market[0]",
        extra={"component_type": "plugin"},
    )
    assert match(refs=[ref], advisories=advisories) == []


def test_marketplace_qualified_ref_matches_unqualified_advisory():
    """An advisory written as `plugin/<name>` (no marketplace) must match
    marketplace-qualified endpoint refs, so authors can write a single identity
    that covers both repo-mode and endpoint-mode scans."""
    advisory = make_identity_advisory("CVE-2026-XMODE", "plugin/my-plugin")
    ref = ComponentRef(
        name="my-plugin",
        version="1.0.0",
        component_identity="plugin/anthropic/my-plugin",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.my-plugin@anthropic[0]",
        extra={"component_type": "plugin", "marketplace": "anthropic"},
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-XMODE"
    assert findings[0].confidence == "high"


def test_marketplace_qualified_ref_matches_same_marketplace_advisory():
    """Exact marketplace match still works when advisory and ref both carry marketplace."""
    advisory = make_identity_advisory("CVE-2026-EXACT", "plugin/anthropic/my-plugin")
    ref = ComponentRef(
        name="my-plugin",
        version="1.0.0",
        component_identity="plugin/anthropic/my-plugin",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.my-plugin@anthropic[0]",
        extra={"component_type": "plugin", "marketplace": "anthropic"},
    )
    findings = match(refs=[ref], advisories=[advisory])
    assert len(findings) == 1
    assert findings[0].advisory_id == "CVE-2026-EXACT"


def test_marketplace_qualified_ref_does_not_match_different_marketplace_advisory():
    """A marketplace-specific advisory must not match a ref from a different marketplace."""
    advisory = make_identity_advisory("CVE-2026-MISMATCH", "plugin/other-market/my-plugin")
    ref = ComponentRef(
        name="my-plugin",
        version="1.0.0",
        component_identity="plugin/anthropic/my-plugin",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.my-plugin@anthropic[0]",
        extra={"component_type": "plugin", "marketplace": "anthropic"},
    )
    assert match(refs=[ref], advisories=[advisory]) == []


def test_repo_mode_ref_does_not_match_marketplace_specific_advisory():
    """A repo-mode ref (`plugin/<name>`, no marketplace) must not match
    a marketplace-qualified advisory — the ref cannot confirm the marketplace."""
    advisory = make_identity_advisory("CVE-2026-REPOMODE", "plugin/anthropic/my-plugin")
    ref = ComponentRef(
        name="my-plugin",
        version="1.0.0",
        component_identity="plugin/my-plugin",
        source_manifest=".claude-plugin/plugin.json",
        source_locator="$",
        extra={"component_type": "plugin"},
    )
    assert match(refs=[ref], advisories=[advisory]) == []


def test_finding_mirrors_component_attribution():
    """Per ADR-0006: Finding.attributed_to mirrors ComponentRef.attributed_to.
    Test both attribution-set and attribution-None cases."""
    advisory = make_advisory("CVE-2026-9998", "npm", "lodash", "5.0.0")

    via_plugin = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.0",
        attributed_to="plugin/supabase@0.1.6",
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

    assert via_finding.attributed_to == "plugin/supabase@0.1.6"
    assert via_finding.attributed_to == via_finding.component.attributed_to

    assert direct_finding.attributed_to is None
    assert direct_finding.component.attributed_to is None


def test_github_ref_matches_advisory_regardless_of_repo_name_casing():
    """Mixed-case owner/repo must match a lowercase canonical OSV GIT range URL."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    # OSV records use the lowercase canonical URL; user's install used mixed case.
    advisory = make_git_advisory("GHSA-case", "https://github.com/oraios/serena.git")
    advisory["database_specific"] = {
        "openaca": {
            "osv_query_matches": [
                {"kind": "git_commit", "repo": "github.com/oraios/serena", "ref": sha}
            ]
        }
    }
    ref = ComponentRef(ecosystem="github", name="OraIOS/Serena", version=sha)

    findings = match(refs=[ref], advisories=[advisory])

    assert len(findings) == 1
    assert findings[0].advisory_id == "GHSA-case"
