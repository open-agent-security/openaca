"""Host selection and root resolution for endpoint-mode entry points.

Every endpoint entry point (`scan endpoint`, `bom endpoint`, remote sync)
resolves the same way through `resolve_endpoint_request`, so one request maps
to one ordered `{host_id: config_root}` map. `endpoint_discovery_roots` then
extends that map with the auxiliary directories endpoint composition reads but
no host owns, giving every discoverable endpoint path a stable normalization
label (and therefore a machine-independent node key).
"""

from __future__ import annotations

from pathlib import Path

import click

from tools.hosts import HOSTS, all_host_ids, detected_hosts

# `--config-dir` names no host. Given without `--host` it applies to the host
# endpoint mode has always meant, which is also what keeps the legacy
# `--config-dir <dir>` invocation working on a machine where nothing is
# detected (the supplied directory IS the root).
_DEFAULT_OVERRIDE_HOST = "claude-code"

# Neutral locator for multi-host endpoint scans, where no single host's config
# root is authoritative. Shared by the remote collector (`TARGET_LOCATOR_ENDPOINT`
# in tools/remote/collector.py) and `bom endpoint`'s multi-host `openaca:target`.
TARGET_LOCATOR_ENDPOINT = "endpoint:user-scope"

# Non-host discovery roots. Labels share the host-label namespace, so they may
# never collide with a registered host id (asserted in `endpoint_auxiliary_roots`).
_SHARED_AGENTS_LABEL = "shared-agents"
_CLAUDE_COMPAT_LABEL = "claude-compat"


def resolve_endpoint_request(
    host_values: tuple[str, ...], config_dir: Path | None
) -> tuple[list[str], dict[str, Path]]:
    """Returns (selected_host_ids, ordered {host_id: config_root}).

    - Explicit `--host` values: validated against `HOSTS`, deduped, ordered by
      `HOSTS` registration order. Omitted: every detected host (or, when an
      explicit `--config-dir` is given, the single default host it applies to).
    - Nothing selected/detected -> `ClickException` naming registered hosts.
    - `config_dir` (explicit `--config-dir`) is allowed only when exactly ONE
      host ends up selected; it becomes that host's config root and counts as
      detected -- `detect()` is NOT consulted for an explicit override (the
      supplied directory IS the root; requiring the default `~/.cursor` to
      also exist would reject valid overrides). With 2+ selected hosts,
      `--config-dir` is a hard error telling the user to pass `--host` to
      disambiguate.
    - Explicit `--host X` with NO `--config-dir` and `detect()` false -> hard
      error.
    """
    selected = _selected_hosts(host_values, config_dir)
    if not selected:
        raise click.ClickException(
            "no agent host detected on this endpoint (looked for: "
            f"{', '.join(all_host_ids())}); pass --host and --config-dir to scan one explicitly"
        )
    if config_dir is not None:
        if len(selected) > 1:
            raise click.ClickException(
                "--config-dir sets one host's config root but "
                f"{len(selected)} hosts are selected ({', '.join(selected)}); "
                "pass a single --host to disambiguate"
            )
        return selected, {selected[0]: config_dir.expanduser()}

    roots: dict[str, Path] = {}
    for host_id in selected:
        adapter = HOSTS[host_id]
        root = adapter.config_root(None) if adapter.detect() else None
        if root is None:
            raise click.ClickException(
                f"host {host_id!r} has no config directory on this endpoint; "
                "pass --config-dir to point at one explicitly"
            )
        roots[host_id] = root
    return selected, roots


def _selected_hosts(host_values: tuple[str, ...], config_dir: Path | None) -> list[str]:
    if not host_values:
        if config_dir is not None:
            return [_DEFAULT_OVERRIDE_HOST]
        return detected_hosts()
    known = all_host_ids()
    requested: set[str] = set()
    for raw in host_values:
        for piece in raw.split(","):
            host_id = piece.strip()
            if not host_id:
                continue
            if host_id not in HOSTS:
                raise click.BadParameter(
                    f"unknown host {host_id!r}; known hosts: {', '.join(known)}"
                )
            requested.add(host_id)
    if not requested:
        raise click.BadParameter(
            f"--host given but contains no usable host name (known hosts: {', '.join(known)})"
        )
    return [host_id for host_id in known if host_id in requested]


def shared_agents_root() -> Path:
    """`~/.agents` — a cross-tool home-scoped convention (Cursor and Codex
    read it). Not owned by any host, so no host's `--config-dir` override
    relocates it. `Path.home()` honors `$HOME` on POSIX (V0 is POSIX-only,
    ADR-0005), which is also how tests pin it hermetically."""
    return Path.home() / ".agents"


def endpoint_auxiliary_roots(selected: list[str], roots: dict[str, Path]) -> dict[str, Path]:
    """Ordered {label: root} of the non-host discovery roots implied by a
    selection. Deterministic and derived — never CLI input:

    - `shared-agents` -> `shared_agents_root()`, when `cursor` is selected.
    - `claude-compat` -> `HOSTS["claude-code"].config_root(None)`, when
      `cursor` is selected and `claude-code` is NOT (when Claude Code IS
      selected, its own host root already covers the path).
    """
    assert not ({_SHARED_AGENTS_LABEL, _CLAUDE_COMPAT_LABEL} & set(HOSTS)), (
        "auxiliary label collides with a registered host id"
    )
    auxiliary: dict[str, Path] = {}
    if "cursor" not in selected:
        return auxiliary
    auxiliary[_SHARED_AGENTS_LABEL] = shared_agents_root()
    if "claude-code" not in selected:
        claude_compat = HOSTS["claude-code"].config_root(None)
        if claude_compat is not None:
            auxiliary[_CLAUDE_COMPAT_LABEL] = claude_compat
    return auxiliary


def endpoint_normalization_label(host_id: str) -> str:
    """The node-key path prefix for a host's endpoint root. Claude Code keeps
    the historical bare `endpoint` label so its keys never change."""
    return "endpoint" if host_id == "claude-code" else f"endpoint-{host_id}"


def endpoint_discovery_roots(selected: list[str], roots: dict[str, Path]) -> dict[str, Path]:
    """Ordered {normalization_label: root} for every endpoint path the
    selected request can discover. Host entries come first, followed by
    `endpoint_auxiliary_roots` with `endpoint-<aux-label>` labels. Duplicate
    labels and duplicate resolved roots are rejected rather than letting
    matching order make provenance implicit.
    """
    discovery: dict[str, Path] = {}
    seen: dict[Path, str] = {}

    def _put(label: str, root: Path) -> None:
        if label in discovery:
            raise click.ClickException(f"duplicate endpoint discovery label: {label}")
        resolved = root.expanduser().resolve()
        owner = seen.get(resolved)
        if owner is not None:
            raise click.ClickException(
                f"endpoint roots {owner} and {label} resolve to the same directory: {resolved}"
            )
        seen[resolved] = label
        discovery[label] = root

    for host_id in selected:
        root = roots.get(host_id)
        if root is not None:
            _put(endpoint_normalization_label(host_id), root)
    for label, root in endpoint_auxiliary_roots(selected, roots).items():
        _put(f"endpoint-{label}", root)
    return discovery


def claude_compat_agents_dir(selected: list[str], roots: dict[str, Path]) -> Path | None:
    """The `agents/` directory Claude-format subagents are read from: the
    selected Claude Code root when Claude Code is selected, otherwise the
    `claude-compat` auxiliary root (Cursor's compatibility read doesn't depend
    on OpenACA's host selection)."""
    if "claude-code" in roots:
        return roots["claude-code"] / "agents"
    auxiliary = endpoint_auxiliary_roots(selected, roots).get(_CLAUDE_COMPAT_LABEL)
    return auxiliary / "agents" if auxiliary is not None else None
