"""Codex's posture collectors and the `mutable_install` deferral
(plan 043 Task 10).

Both collectors are **installed-only** and read their surface directly. That is
the documented exception to "posture derives from composition": neither surface
produces a `ComponentRef`, so there is nothing to derive from.
"""

from __future__ import annotations

from pathlib import Path

from tools.component_ref import ComponentRef
from tools.posture import (
    collect_codex_project_trust_manifests,
    collect_codex_rules_manifests,
)

CONFIG = """
[marketplaces.mkt]
source_type = "git"
source = "https://example.test/mkt.git"
last_revision = "0620a687ddd518871772eefd78573baff2dcb8cb"

[projects."/home/u/trusted-repo"]
trust_level = "trusted"

[projects."/home/u/other"]
trust_level = "untrusted"
"""


def _root(tmp_path: Path) -> Path:
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "default.rules").write_text(
        'prefix_rule(pattern=["git", "commit"], decision="allow")\n', encoding="utf-8"
    )
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def test_rules_collector_reads_parsed_rules(tmp_path):
    manifests = collect_codex_rules_manifests(_root(tmp_path))

    assert len(manifests) == 1
    assert manifests[0][1]["rules"][0].pattern == ("git", "commit")


def test_rules_collector_on_a_root_with_no_rules_dir(tmp_path):
    assert collect_codex_rules_manifests(tmp_path) == []


def test_rules_collector_skips_a_file_with_no_readable_rules(tmp_path):
    root = _root(tmp_path)
    (root / "rules" / "default.rules").write_text("suffix_rule(x=1)\n", encoding="utf-8")

    assert collect_codex_rules_manifests(root) == []


def test_project_trust_collector_carries_every_project(tmp_path):
    """Filtering to trusted-only is the rule's job, not the collector's — a
    collector that pre-filtered would hide the untrusted entries from any
    future check that needs them."""
    manifests = collect_codex_project_trust_manifests(_root(tmp_path))

    assert manifests[0][1]["projects"] == {
        "/home/u/trusted-repo": "trusted",
        "/home/u/other": "untrusted",
    }


def test_project_trust_collector_on_a_root_with_no_config(tmp_path):
    assert collect_codex_project_trust_manifests(tmp_path) == []


def test_a_malformed_config_is_a_gap_not_a_crash(tmp_path):
    (tmp_path / "config.toml").write_text("{ not toml", encoding="utf-8")

    assert collect_codex_project_trust_manifests(tmp_path) == []


# --- mutable_install: last_revision is NOT an install pin (Task 10C) --------


def test_a_codex_marketplace_plugin_does_not_fire_mutable_install():
    """`last_revision` is deliberately NOT wired into `mutable_install`.

    Claude Code's `gitCommitSha` pins one install. Codex's `last_revision` sits
    on the *marketplace* beside `last_updated` and records its most recent
    sync — observed changing on the audited endpoint within a single session,
    from `b819188d…` to `0620a687…`, while the installed plugins did not
    change. A moving value is not an immutability guarantee, so feeding it to
    the pin branch would report a plugin as pinned when it is not.

    Codex plugin refs never set `gitCommitSha`, so this asserts they do not
    reach that branch by accident either.
    """
    from tools.posture.rules.mutable_install import check_mutable_install

    ref = ComponentRef(
        name="demo",
        version="1.0.0",
        component_identity="plugin/mkt/demo",
        source_manifest="/x/plugins/cache/mkt/demo/1.0.0",
        source_locator="$",
        extra={
            "component_type": "plugin",
            "marketplace": "mkt",
            "last_revision": "0620a687ddd518871772eefd78573baff2dcb8cb",
            "enabled": True,
        },
    )

    findings = check_mutable_install([ref], agent_kind="codex")

    assert findings == []
