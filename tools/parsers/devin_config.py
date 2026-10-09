"""Devin CLI's own configuration files (ADR-0070).

`config.json`, `config.local.json`, `mcp_config.json`, `mcp_config.local.json`
and `hooks.v1.json` are JSON with comments, at three levels: user
(`<config root>/`), project (`.devin/`) and project-local (`.devin/*.local.json`).
Devin reads the files it imports from other runtimes with the same loader, so
they are read that way here too.

Two facts about Devin, not about the files' owners, live here:

- **`read_config_from`** switches each import on or off per tool. A switch set
  at a higher-precedence level wins; `null` reads as `true`, and an unset
  switch defaults to `true` (docs: Configuration Import).
- **Devin's `ServerConfig` accepts aliases** the shared MCP parser does not:
  `serverUrl` for `url` (the audited binary validates that "url and serverUrl
  must not disagree") and `transport` for `type`. They are mapped here, for the
  files Devin reads only, so no other kind starts accepting them.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import hooks_json, jsonc, mcp_json

READ_CONFIG_FROM = "read_config_from"

# The `read_config_from` keys whose imports this kind composes. OpenCode and
# Zed are deferred (docs/specs/devin-cli-agent-kind.md "Out of the first pass"),
# and `agents_standard` gates instruction files only.
IMPORT_CLAUDE = "claude"
IMPORT_CURSOR = "cursor"
IMPORT_WINDSURF = "windsurf"
IMPORT_COPILOT = "copilot"

PROJECT_CONFIG_DIR = ".devin"
CONFIG_FILENAME = "config.json"
LOCAL_CONFIG_FILENAME = "config.local.json"
MCP_CONFIG_FILENAME = "mcp_config.json"
LOCAL_MCP_CONFIG_FILENAME = "mcp_config.local.json"
HOOKS_FILENAME = "hooks.v1.json"


def load(path: Path) -> dict:
    """Read one Devin-read JSON-with-comments file. Raises `OSError` or
    `ValueError` (including for a non-object root) so a caller decides
    whether the failure is a coverage gap."""
    data = jsonc.load_path(path)
    if not isinstance(data, dict):
        raise ValueError("configuration must contain an object")
    return data


def import_switch(config: dict, tool: str) -> bool | None:
    """`read_config_from.<tool>` as Devin reads it: `False` disables, `True`
    or `null` enables, and absence (or any other value) says nothing."""
    switches = config.get(READ_CONFIG_FROM)
    if not isinstance(switches, dict) or tool not in switches:
        return None
    value = switches[tool]
    if value is False:
        return False
    if value is True or value is None:
        return True
    return None


class ImportSwitches:
    """Resolve whether an import is on for a directory, nearest level first.

    For a directory `d`, the levels searched are `d/.devin/config.local.json`,
    `d/.devin/config.json`, then the same pair in each ancestor up to and
    including `stop_at`, then `user_config`. The first explicit switch wins;
    none means `True`. Nested project configs take precedence over ancestor
    ones (docs: Configuration Precedence), so a deeper directory is searched
    first.

    A file that does not parse declares no switch here; the composition pass
    reading the same file records it as a coverage gap.
    """

    def __init__(self, *, stop_at: Path | None, user_config: Path | None = None) -> None:
        self._stop_at = stop_at.resolve() if stop_at is not None else None
        self._user_config = user_config
        self._configs: dict[Path, dict] = {}

    def enabled(self, directory: Path | None, tool: str) -> bool:
        for config_path in self._chain(directory):
            value = import_switch(self._config(config_path), tool)
            if value is not None:
                return value
        return True

    def _chain(self, directory: Path | None) -> list[Path]:
        chain: list[Path] = []
        if directory is not None and self._stop_at is not None:
            current = directory.resolve()
            while True:
                config_dir = current / PROJECT_CONFIG_DIR
                chain += [config_dir / LOCAL_CONFIG_FILENAME, config_dir / CONFIG_FILENAME]
                if current == self._stop_at or current.parent == current:
                    break
                if not current.is_relative_to(self._stop_at):
                    break
                current = current.parent
        if self._user_config is not None:
            chain.append(self._user_config)
        return chain

    def _config(self, path: Path) -> dict:
        if path not in self._configs:
            try:
                self._configs[path] = load(path) if path.is_file() else {}
            except (OSError, ValueError):
                self._configs[path] = {}
        return self._configs[path]


def server_map(data: dict, *, allow_flat: bool) -> tuple[dict, str] | None:
    """The MCP server map a file declares, with its locator prefix.

    `mcpServers` wherever it appears. A flat `{name: {command|url}}` root is
    accepted only where the owner's format allows one (`allow_flat`), as
    `mcp_json.parse` does for Claude Code's `.mcp.json`. Raises `ValueError`
    for a non-object `mcpServers`; `None` means the file declares no servers.
    """
    if "mcpServers" in data:
        servers = data["mcpServers"]
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object")
        return servers, "$.mcpServers"
    if (
        allow_flat
        and data
        and all(
            isinstance(v, dict) and ("command" in v or "url" in v or "serverUrl" in v)
            for v in data.values()
        )
    ):
        return data, "$"
    return None


def normalize_server_entry(entry: object) -> object:
    """Map Devin's `ServerConfig` aliases onto the shared parser's names."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    if "url" not in out and isinstance(out.get("serverUrl"), str):
        out["url"] = out["serverUrl"]
    if "type" not in out and isinstance(out.get("transport"), str):
        out["type"] = out["transport"]
    return out


def parse_server(
    name: str, entry: object, *, source_manifest: str, locator_prefix: str
) -> list[ComponentRef]:
    """One named server, strictly. A disabled server yields nothing, which is
    also how a higher level's `"disabled": true` shadows a lower definition."""
    return mcp_json.parse_mcp_servers(
        {name: normalize_server_entry(entry)},
        source_manifest=source_manifest,
        locator_prefix=locator_prefix,
        strict=True,
    )


def _named_servers(
    servers: dict, *, source_manifest: str, locator_prefix: str
) -> list[tuple[str, list[ComponentRef]]]:
    """Every server a map declares, by name, strictly. A disabled server keeps
    its name with no refs: wherever Devin merges servers by name, a disabled
    entry still claims it."""
    named: list[tuple[str, list[ComponentRef]]] = []
    for name, entry in servers.items():
        if not isinstance(name, str):
            raise ValueError("MCP server names must be strings")
        refs = parse_server(
            name, entry, source_manifest=source_manifest, locator_prefix=locator_prefix
        )
        named.append((name, refs))
    return named


def _named_mcp_file(
    path: Path, *, allow_flat: bool = False
) -> list[tuple[str, list[ComponentRef]]]:
    """`_named_servers` over a Devin-read MCP file."""
    found = server_map(load(path), allow_flat=allow_flat)
    if found is None:
        return []
    servers, prefix = found
    return _named_servers(servers, source_manifest=str(path), locator_prefix=prefix)


def parse_mcp_file(path: Path, *, allow_flat: bool = False) -> list[ComponentRef]:
    """Every server a Devin-read MCP file declares, strictly (registry use)."""
    return [ref for _name, refs in _named_mcp_file(path, allow_flat=allow_flat) for ref in refs]


def parse_settings_file(path: Path, *, scope: str = "project") -> list[ComponentRef]:
    """The components a Devin-read settings-shaped file declares, strictly:
    its `mcpServers` and its `hooks` block (registry use)."""
    data = load(path)
    refs: list[ComponentRef] = []
    found = server_map(data, allow_flat=False)
    if found is not None:
        servers, prefix = found
        for name, entry in servers.items():
            if not isinstance(name, str):
                raise ValueError("MCP server names must be strings")
            refs.extend(parse_server(name, entry, source_manifest=str(path), locator_prefix=prefix))
    if "hooks" in data:
        refs.extend(hooks_json.parse_settings_hooks(path, data["hooks"], scope=scope, strict=True))
    return refs


# --- Project layers ----------------------------------------------------------

#: What marks a project root: Devin walks up from its working directory to the
#: first directory holding one of these (docs: Global vs. Local Configuration).
PROJECT_ROOT_MARKERS = (".git", ".jj")


def project_layers(project: Path) -> list[Path]:
    """Every directory whose `.devin/` an installed Devin run in `project`
    loads, nearest first: `project`, then each ancestor up to and including
    the first that holds `.git` or `.jj`. Nested configs take precedence over
    ancestor ones. With no marker above it, `project` is its own root and the
    only layer: nothing outside the directory named is assumed to be Devin's.
    """
    chain: list[Path] = []
    current = project
    while True:
        chain.append(current)
        if any((current / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return chain
        if current.parent == current:
            return [project]
        current = current.parent


# --- Plugin manifest fields ----------------------------------------------------


def plugin_path_is_safe(entry: object) -> bool:
    """A plugin-root-relative path Devin accepts: a string that is not
    absolute, does not start with `~`, and has no `..` segment."""
    if not isinstance(entry, str) or not entry:
        return False
    if entry.startswith(("/", "\\", "~")) or Path(entry).is_absolute():
        return False
    return ".." not in Path(entry).parts


def plugin_skills_field_is_valid(value: object) -> bool:
    """Whether a plugin manifest's `skills` field is one Devin loads. A
    string or a list of strings, every entry a safe path; "an invalid entry
    fails the whole manifest", so this decides whether the plugin loads at
    all, not only its skills."""
    entries = value if isinstance(value, list) else [value]
    return all(plugin_path_is_safe(entry) for entry in entries)


#: The field naming a plugin source of each kind the 3000.11.3 binary
#: accepts ("expected 'github', 'url', 'git-subdir', 'local', or
#: 'account-upload'"); `git-subdir` also needs a `path`.
_SOURCE_FIELDS = {
    "github": "repo",
    "url": "url",
    "git-subdir": "url",
    "local": "path",
    "account-upload": "bundleId",
}

#: Devin's `owner/repo` shorthand for a GitHub source string.
_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$")

#: A GitHub repository URL: HTTPS, `ssh://` or `git://`, with or without
#: `.git`, or the SCP form `git@github.com:owner/repo`.
_GITHUB_URL = re.compile(
    r"(?:(?:https?|ssh|git)://(?:[^@/\s]+@)?github\.com(?::\d+)?/|[^@/\s]+@github\.com:)"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+?)(?:\.git)?/?"
)

#: Only an immutable commit is a version (ADR-0016); any other pin is a `git_ref`.
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def plugin_required_refs(data: dict, *, manifest_path: Path, record_gap) -> list[ComponentRef]:
    """The plugins a Devin plugin manifest requires, one ref per
    `requiredPlugins` entry. Devin installs them "recursively when the plugin
    is installed"; `optionalPlugins` and `forbiddenPlugins` install nothing.
    An entry with no usable source is recorded rather than guessed at."""
    if "requiredPlugins" not in data:
        return []
    entries = data["requiredPlugins"]
    if not isinstance(entries, list):
        record_gap(f"could not parse {manifest_path}: requiredPlugins must be a list")
        return []
    refs: list[ComponentRef] = []
    for index, entry in enumerate(entries):
        locator = f"$.requiredPlugins[{index}]"
        ref = _required_plugin_ref(entry, manifest_path=manifest_path, locator=locator)
        if ref is None:
            record_gap(
                f"could not parse {manifest_path}: {locator} is not a plugin source Devin accepts"
            )
            continue
        refs.append(ref)
    return refs


def _required_plugin_ref(
    entry: object, *, manifest_path: Path, locator: str
) -> ComponentRef | None:
    """One required plugin, identified as every GitHub-sourced component is:
    `owner/repo` in the `github` ecosystem, a commit `sha` as its version and
    any other pin as `git_ref`, a subdirectory as `source_subdirectory`. A
    source elsewhere is named by its location and keeps its pin."""
    subdirectory: str | None = None
    pin: str | None = None
    if isinstance(entry, str):
        location, _, subdirectory = entry.partition("#")
        repo = location if _GITHUB_SHORTHAND.fullmatch(location) else _github_repo(location)
    elif isinstance(entry, dict):
        kind = entry.get("source")
        field = _SOURCE_FIELDS.get(kind) if isinstance(kind, str) else None
        location = entry.get(field) if field else None
        if not isinstance(location, str):
            return None
        if kind == "git-subdir":
            subdirectory = entry.get("path")
            if not isinstance(subdirectory, str) or not subdirectory:
                return None
        repo = location if kind == "github" else None
        if kind in ("url", "git-subdir"):
            repo = _github_repo(location)
        pin = next(
            (
                value
                for value in (entry.get("sha"), entry.get("ref"))
                if isinstance(value, str) and value
            ),
            None,
        )
    else:
        return None
    if not location:
        return None
    version = pin.lower() if repo and pin and _COMMIT_SHA.fullmatch(pin) else None
    extra: dict = {}
    if subdirectory:
        extra["source_subdirectory"] = subdirectory
    if pin and version is None:
        extra["git_ref"] = pin
    name = repo or location
    return ComponentRef(
        ecosystem="github" if repo else None,
        name=name,
        version=version,
        component_identity=f"plugin-dep/{name}",
        source_manifest=str(manifest_path),
        source_locator=locator,
        extra=extra,
    )


def _github_repo(url: str) -> str | None:
    """`owner/repo` when `url` is a GitHub repository URL in any form Devin
    gives one identity: "All GitHub forms for the same repo (`owner/repo`,
    the HTTPS URL, the `.git` URL, the SSH form) refer to the same plugin
    identity" (Devin's plugin dependency reference)."""
    match = _GITHUB_URL.fullmatch(url)
    return match.group("repo") if match else None


def plugin_mcp_refs(
    data: dict,
    *,
    plugin_root: Path,
    manifest_path: Path,
    conventional: tuple[str, ...],
    record_gap,
    usable=lambda path: True,
) -> list[ComponentRef]:
    """The MCP servers a Devin plugin loads, by its documented source rules.

    `mcpServers` takes four shapes: a declaration file, a list of them read in
    order, `{"paths": [...], "exclusive": true}`, or an inline server map.
    The root convention (`conventional`, `.mcp.json`) is read after any
    declared files unless an exclusive declaration or a non-empty inline map
    suppresses it; an empty list or map does not. Unsafe declared paths are
    dropped. A field of any other shape disables MCP for the plugin and is
    recorded. When one server name appears in several sources the first wins,
    a disabled entry included.

    `usable` lets a repo scan skip a gitignored candidate before it can win.
    """
    field = data.get("mcpServers")
    sources: list[list[tuple[str, list[ComponentRef]]]] = []
    declared: list[object] = []
    suppress = False
    if "mcpServers" not in data:
        pass
    elif isinstance(field, str):
        declared = [field]
    elif isinstance(field, list):
        declared = list(field)
    elif isinstance(field, dict) and isinstance(field.get("paths"), list):
        exclusive = field.get("exclusive", False)
        if not isinstance(exclusive, bool):
            record_gap(f"could not parse {manifest_path}: mcpServers.exclusive must be a boolean")
            return []
        declared, suppress = list(field["paths"]), exclusive
    elif isinstance(field, dict):
        suppress = bool(field)
        try:
            sources.append(
                _named_servers(
                    field,
                    source_manifest=str(manifest_path),
                    locator_prefix="$.mcpServers (inlined)",
                )
            )
        except ValueError as exc:
            record_gap(f"could not parse {manifest_path}: {exc}")
            return []
    else:
        record_gap(
            f"could not parse {manifest_path}: mcpServers is not a form Devin accepts; "
            "the plugin's MCP servers do not load"
        )
        return []

    candidates = [
        plugin_root / entry
        for entry in declared
        if isinstance(entry, str) and plugin_path_is_safe(entry)
    ]
    if not suppress:
        candidates += [plugin_root / name for name in conventional]
    for path in candidates:
        if not path.is_file():
            if path.name not in conventional or path.parent != plugin_root:
                record_gap(f"could not parse {path}: referenced MCP manifest is unavailable")
            continue
        if not usable(path):
            continue
        try:
            sources.append(_named_mcp_file(path, allow_flat=True))
        except (OSError, ValueError) as exc:
            record_gap(f"could not parse {path}: {exc}")

    # By declared name, before the disabled flag: a disabled entry in an
    # earlier source still shadows a later definition.
    seen: set[str] = set()
    refs: list[ComponentRef] = []
    for source in sources:
        for name, server_refs in source:
            if name in seen:
                continue
            seen.add(name)
            refs.extend(server_refs)
    return refs
