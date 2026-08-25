"""Compile an evaluated OpenACA policy into Claude Code managed settings."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from tools.policy import Decision, McpTarget, PluginTarget, Policy


@dataclass(frozen=True)
class ClaudeCompilation:
    settings: dict
    limitations: tuple[str, ...]


def compile_policy(policy: Policy, decisions: list[Decision]) -> ClaudeCompilation:
    """Render only restrictions Claude documents for managed settings.

    The compiler never emits a plugin enablement entry. An allow entry in
    ``enabledPlugins`` grants capability, while this compiler may only tighten
    host configuration.
    """
    settings: dict = {}
    limitations: list[str] = []

    _compile_mcps(policy, decisions, settings)
    _compile_plugins(policy, decisions, settings, limitations)
    if policy.skills_default == "blocked":
        settings["strictPluginOnlyCustomization"] = ["skills"]
    for decision in decisions:
        if (
            decision.category == "skills"
            and policy.skills_default != "blocked"
            and any(
                reason.startswith(("vulnerability ", "posture ")) for reason in decision.reasons
            )
        ):
            limitations.append(
                f"{_component_label(decision)}: direct skill risk block is "
                "not enforceable by Claude"
            )
        elif (
            decision.category == "other"
            and decision.blocked
            and not _blocked_via_owning_plugin(decision)
        ):
            # A risk finding on a component outside mcps/plugins/skills (for
            # example an agent-dependency package beneath a standalone MCP
            # server) has no plugin owner and no host-native command/URL/
            # plugin identifier of its own. Per spec ("If no exact target
            # exists, preserve the finding and report it as not enforceable"),
            # this must surface, not silently disappear because "other"
            # decisions are never rendered into managed settings. A component
            # blocked solely because its owning plugin is blocked is already
            # covered by that plugin's `enabledPlugins: false` entry, so no
            # limitation is needed there.
            limitations.append(
                f"{_component_label(decision)}: risk block has no host-native "
                "target and is not enforceable by Claude"
            )
    return ClaudeCompilation(settings=settings, limitations=tuple(_dedupe(limitations)))


def _compile_mcps(policy: Policy, decisions: list[Decision], settings: dict) -> None:
    rule = policy.mcps
    allowed = [_mcp_setting(target) for target in rule.allowed if isinstance(target, McpTarget)]
    blocked = [_mcp_setting(target) for target in rule.blocked if isinstance(target, McpTarget)]
    for decision in decisions:
        if (
            decision.category == "mcps"
            and decision.blocked
            and not _blocked_via_owning_plugin(decision)
        ):
            target = _mcp_setting_from_ref(decision)
            if target is not None:
                blocked.append(target)
    if rule.default == "blocked":
        settings["allowManagedMcpServersOnly"] = True
        settings["allowedMcpServers"] = _dedupe_dicts(allowed)
    if blocked:
        settings["deniedMcpServers"] = _dedupe_dicts(blocked)


def _compile_plugins(
    policy: Policy,
    decisions: list[Decision],
    settings: dict,
    limitations: list[str],
) -> None:
    rule = policy.plugins
    blocked_plugins = [
        target.plugin
        for target in rule.blocked
        if isinstance(target, PluginTarget) and target.plugin
    ]
    allowed_marketplaces = [
        target.marketplace
        for target in rule.allowed
        if isinstance(target, PluginTarget) and target.marketplace
    ]
    blocked_marketplaces = [
        target.marketplace
        for target in rule.blocked
        if isinstance(target, PluginTarget) and target.marketplace
    ]
    for decision in decisions:
        if decision.category != "plugins" or not decision.blocked:
            continue
        plugin = _plugin_key(decision)
        if plugin is None:
            limitations.append(
                f"{_component_label(decision)}: plugin block lacks plugin@marketplace"
            )
        else:
            blocked_plugins.append(plugin)

    if blocked_plugins:
        settings["enabledPlugins"] = {plugin: False for plugin in sorted(set(blocked_plugins))}
    if blocked_marketplaces:
        settings["blockedMarketplaces"] = [
            _marketplace_setting(value) for value in _dedupe(blocked_marketplaces)
        ]
    if rule.default == "blocked":
        if allowed_marketplaces:
            settings["strictKnownMarketplaces"] = [
                _marketplace_setting(value) for value in _dedupe(allowed_marketplaces)
            ]
        limitations.append(
            "plugin default block is not enforceable for installed plugins in Claude"
        )
        for target in rule.allowed:
            if isinstance(target, PluginTarget) and target.plugin:
                limitations.append(
                    f"{target.plugin}: plugin allow entry is not enforceable by Claude"
                )


def _mcp_setting(target: McpTarget) -> dict:
    if target.command is not None:
        return {"serverCommand": list(target.command)}
    assert target.url is not None
    return {"serverUrl": target.url}


def _mcp_setting_from_ref(decision: Decision) -> dict | None:
    extra = decision.ref.extra
    url = extra.get("url")
    if isinstance(url, str) and url:
        return {"serverUrl": url}
    raw_command = extra.get("mcp_command")
    if (
        isinstance(raw_command, list)
        and raw_command
        and all(isinstance(part, str) and part for part in raw_command)
    ):
        return {"serverCommand": raw_command}
    install_source = extra.get("install_source")
    if not isinstance(install_source, str) or not install_source:
        return None
    import shlex

    try:
        command = shlex.split(install_source)
    except ValueError:
        return None
    return {"serverCommand": command} if command else None


def _plugin_key(decision: Decision) -> str | None:
    marketplace = decision.ref.extra.get("marketplace")
    if not isinstance(decision.ref.name, str) or not decision.ref.name:
        return None
    if not isinstance(marketplace, str) or not marketplace:
        return None
    return f"{decision.ref.name}@{marketplace}"


def _marketplace_setting(value: str) -> dict:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
        repo = parsed.path.strip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if repo.count("/") == 1:
            return {"source": "github", "repo": repo}
    return {"source": "git", "url": value}


def _blocked_via_owning_plugin(decision: Decision) -> bool:
    """A component blocked purely because its owning plugin is blocked.

    ``tools.policy._admission_decision`` prefixes every reason with
    ``"owning plugin: "`` when a decision is entirely inherited from the
    owning plugin, and never appends an unprefixed reason afterward (risk
    gates on a plugin child redirect the block to the plugin's own decision
    via ``_restriction_target``). Such a component already loses capability
    through that plugin's ``enabledPlugins: false`` entry; compiling its own
    command/URL into a global, identity-agnostic deny list would also block
    unrelated occurrences of the same command/URL (e.g. a different, allowed
    plugin, or a standalone server).
    """
    return bool(decision.reasons) and all(
        reason.startswith("owning plugin: ") for reason in decision.reasons
    )


def _component_label(decision: Decision) -> str:
    return decision.ref.component_identity or decision.ref.name or "<unidentified>"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _dedupe_dicts(values: list[dict]) -> list[dict]:
    seen: set[tuple[tuple[str, object], ...]] = set()
    result: list[dict] = []
    for value in values:
        key = tuple(
            sorted(
                (key, tuple(item) if isinstance(item, list) else item)
                for key, item in value.items()
            )
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
