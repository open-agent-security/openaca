"""OpenCode's configuration files (ADR-0083).

`config.json`, `opencode.json` and `opencode.jsonc` share one schema, read as
JSON with comments and trailing commas (`ConfigParse.jsonc`). Before it
validates a file, OpenCode v1.19.5 lowers the V2 keys it understands into V1
ones (`config/v2-compat.ts`). The readers here lower the keys composition reads
the same way, and raise `ValueError` where OpenCode's V1 schema rejects a file,
because OpenCode then does not start.

Everything here reads one file. The order files merge in belongs to the
composer (`tools.graph_build_opencode`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from tools.component_ref import ComponentRef
from tools.parsers import jsonc, mcp_json

CONFIG_DIR = ".opencode"
# `<config>/`'s files, in the order `loadGlobal` merges them.
GLOBAL_FILENAMES = ("config.json", "opencode.json", "opencode.jsonc")
# A project layer's and a `.opencode/` directory's files, in merge order.
PROJECT_FILENAMES = ("opencode.json", "opencode.jsonc")
# OpenCode's worktree is the Git repository's (`Project.resolve`).
PROJECT_ROOT_MARKER = ".git"

AGENT_DIRS = ("agent", "agents")
MODE_DIRS = ("mode", "modes")
COMMAND_DIRS = ("command", "commands")
PLUGIN_DIRS = ("plugin", "plugins")
PLUGIN_SUFFIXES = (".ts", ".js")
SKILL_DIRS = ("skill", "skills")
# The other runtimes' directories whose `skills/` OpenCode reads.
EXTERNAL_SKILL_OWNERS = (".claude", ".agents")

# Plugin packages OpenCode ignores because they are now built in
# (`DEPRECATED_PLUGIN_PACKAGES`, matched by substring as OpenCode matches).
DEPRECATED_PLUGIN_PACKAGES = ("opencode-openai-codex-auth", "opencode-copilot-auth")

# The files a composed MCP server's entry merges from, lowest precedence first.
# Read by the OpenCode posture collector to attribute each header to the file
# that sets it. Never emitted to a BOM.
LAYERS_KEY = "opencode_layers"

_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_NPM_NAME = r"(?:@[a-z0-9~][a-z0-9._~-]*/)?[a-z0-9~][a-z0-9._~-]*"
_REGISTRY_SPEC = re.compile(rf"^(?P<name>{_NPM_NAME})(?:@(?P<version>[^\s]+))?$", re.IGNORECASE)
_ALIAS_SPEC = re.compile(rf"^(?P<alias>{_NPM_NAME})@npm:(?P<target>.+)$", re.IGNORECASE)
_V2_SERVER_KEYS = ("disabled", "codemode")
_V2_OAUTH_KEYS = ("client_id", "client_secret", "callback_port", "redirect_uri")
_TIMEOUT_KEYS = ("startup", "catalog", "execution")


def load(path: Path) -> dict:
    """Read one OpenCode configuration file. An empty file is `{}`, as
    `loadFile` treats it. Raises `OSError` or `ValueError`."""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = jsonc.loads(text)
    if not isinstance(data, dict):
        raise ValueError("configuration must contain an object")
    return data


def merge_deep(target: dict, source: dict) -> dict:
    """remeda `mergeDeep`: an object merges into an object, and any other
    value, an array included, replaces the earlier one."""
    out = dict(target)
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_deep(out[key], value)
        else:
            out[key] = value
    return out


def project_layers(project: Path) -> list[Path]:
    """`project` and each ancestor up to and including the first holding
    `.git`, nearest first. With no `.git` above it, `project` alone: OpenCode
    then searches to the filesystem root, which this scan does not follow
    (docs/specs/opencode-agent-kind.md, Out of the first pass)."""
    project = project.resolve()
    chain: list[Path] = []
    current = project
    while True:
        chain.append(current)
        if (current / PROJECT_ROOT_MARKER).exists():
            return chain
        if current.parent == current:
            return [project]
        current = current.parent


def entry_name(relative: str) -> str:
    """`configEntryNameFromPath` after its directory prefix is stripped: the
    path below `agents/` (or `commands/`), without its extension."""
    stem, dot, _suffix = relative.rpartition(".")
    return stem if dot and stem else relative


# --- MCP servers ---------------------------------------------------------------


@dataclass(frozen=True)
class McpEntry:
    """One server entry a file declares, lowered to OpenCode's V1 shape.
    `prefix` is `$.mcp`, or `$.mcp.servers` for a V2 envelope entry."""

    entry: dict
    prefix: str

    @property
    def type(self) -> str | None:
        value = self.entry.get("type")
        return value if isinstance(value, str) else None


def mcp_entries(data: dict) -> dict[str, McpEntry]:
    """The `mcp` servers a file declares, as `ConfigV2Compat.lower` and the V1
    schema leave them. Raises `ValueError` for an entry the V1 schema rejects."""
    if "mcp" not in data:
        return {}
    mcp = data["mcp"]
    if not isinstance(mcp, dict):
        raise ValueError("mcp must be an object")
    nested = mcp.get("servers")
    envelope = isinstance(nested, dict) and not _is_direct_server(nested)
    timeout = mcp.get("timeout")
    global_timeout = (
        isinstance(timeout, dict)
        and not _is_direct_server(timeout)
        and (not timeout or any(key in timeout for key in _TIMEOUT_KEYS))
    )
    lowered: dict[str, McpEntry] = {}
    for name, value in mcp.items():
        if (name == "servers" and envelope) or (name == "timeout" and global_timeout):
            continue
        if isinstance(value, dict) and _is_v2_server(value):
            value = _lower_server(value) or value
        lowered[name] = McpEntry(value, "$.mcp")
    if envelope:
        assert isinstance(nested, dict)
        for name, value in nested.items():
            if name in lowered:
                continue
            if (
                isinstance(value, dict)
                and isinstance(value.get("enabled"), bool)
                and "disabled" not in value
            ):
                lowered[name] = McpEntry(value, "$.mcp.servers")
                continue
            server = _lower_server(value)
            if server is not None:
                lowered[name] = McpEntry(server, "$.mcp.servers")
    for name, item in lowered.items():
        _validate_server(name, item.entry)
    return lowered


def _is_direct_server(value: dict) -> bool:
    # A `servers` or `timeout` object can itself be a server named so.
    return any(
        key in value and (value[key] is None or not isinstance(value[key], dict))
        for key in ("type", "enabled")
    )


def _is_v2_server(value: dict) -> bool:
    oauth = value.get("oauth")
    return (
        any(key in value for key in _V2_SERVER_KEYS)
        or isinstance(value.get("timeout"), dict)
        or (isinstance(oauth, dict) and any(key in oauth for key in _V2_OAUTH_KEYS))
    )


def _lower_server(value: object) -> dict | None:
    """A V2 server in V1's shape: `enabled` is `disabled !== true` unless the
    entry states `enabled` itself. `None` where the V2 shape is not met, which
    OpenCode drops with a diagnostic."""
    if not isinstance(value, dict):
        return None
    kind = value.get("type")
    if kind == "local":
        command = value.get("command")
        if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
            return None
    elif kind == "remote":
        if not isinstance(value.get("url"), str):
            return None
    else:
        return None
    out = {k: v for k, v in value.items() if k not in ("disabled", "codemode", "timeout")}
    if not isinstance(value.get("enabled"), bool):
        out["enabled"] = value.get("disabled") is not True
    return out


def _validate_server(name: str, entry: object) -> None:
    """The V1 union: a local server, a remote server, or any object with a
    boolean `enabled`, which toggles a server another file declares."""
    if not isinstance(entry, dict):
        raise ValueError(f"MCP server {name!r} must be an object")
    if is_local(entry) or is_remote(entry) or isinstance(entry.get("enabled"), bool):
        return
    raise ValueError(f"MCP server {name!r} must be local, remote or an enabled toggle")


def is_local(entry: dict) -> bool:
    command = entry.get("command")
    return (
        entry.get("type") == "local"
        and isinstance(command, list)
        and all(isinstance(part, str) for part in command)
        and isinstance(entry.get("enabled", True), bool)
    )


def is_remote(entry: dict) -> bool:
    return (
        entry.get("type") == "remote"
        and isinstance(entry.get("url"), str)
        and isinstance(entry.get("enabled", True), bool)
    )


def server_refs(name: str, entry: dict, *, source_manifest: str, prefix: str) -> list[ComponentRef]:
    """One merged server in the shared parser's shape, or `[]` when the merged
    entry names nothing to launch. A local `command` array is the command and
    its arguments; a remote server's transport is `http`, since OpenCode tries
    streamable HTTP before SSE."""
    if is_local(entry) and entry["command"]:
        command = entry["command"]
        shaped: dict = {"command": command[0], "args": list(command[1:])}
        if isinstance(entry.get("environment"), dict):
            shaped["env"] = entry["environment"]
    elif is_remote(entry):
        shaped = {"url": entry["url"]}
    else:
        return []
    return mcp_json.parse_mcp_servers(
        {name: shaped}, source_manifest=source_manifest, locator_prefix=prefix, strict=True
    )


_ENV_REFERENCE = re.compile(r"(?<!\$)\{env:([^}]+)\}")
_FILE_REFERENCE = re.compile(r"(?<!\$)\{file:([^}]+)\}")


def dollar_references(value: object) -> object:
    """Write OpenCode's `{env:NAME}` and `{file:path}` as the `${env:NAME}` and
    path-shaped `${file:…}` that `mcp_header_credential` reads as references.
    OpenCode replaces both before it parses a file (`config/variable.ts`), and
    resolves a bare `{file:path}` against the file's directory, so its body is
    a path. Only the OpenCode collector calls this, so the rule stays strict
    for every other kind."""
    if not isinstance(value, str):
        return value

    def as_path(match: re.Match[str]) -> str:
        body = match.group(1)
        if not body.startswith(("/", "~/", "./", "../")):
            body = f"./{body}"
        return f"${{file:{body}}}"

    return _FILE_REFERENCE.sub(as_path, _ENV_REFERENCE.sub(r"${env:\1}", value))


# --- Plugins -------------------------------------------------------------------


def plugin_specs(data: dict) -> list[tuple[int, str]]:
    """`(index, spec)` for each `plugin` entry: a string, or `[spec, options]`.
    The V2 `plugins` key is one OpenCode v1.19.5 does not load."""
    if "plugin" not in data:
        return []
    plugins = data["plugin"]
    if not isinstance(plugins, list):
        raise ValueError("plugin must be an array")
    specs: list[tuple[int, str]] = []
    for index, item in enumerate(plugins):
        if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict):
            item = item[0]
        if not isinstance(item, str):
            raise ValueError(f"plugin[{index}] must be a string or [string, options]")
        specs.append((index, item))
    return specs


def is_path_spec(spec: str) -> bool:
    """`isPathPluginSpec`."""
    return spec.startswith(("file://", ".")) or Path(spec).is_absolute()


def resolve_path_spec(spec: str, config_file: Path) -> Path:
    """A path spec resolves against the file that declares it
    (`ConfigPlugin.resolvePluginSpec`)."""
    if spec.startswith("file://"):
        return Path(unquote(urlparse(spec).path))
    path = Path(spec)
    return path if path.is_absolute() else (config_file.parent / path)


def is_deprecated_plugin(spec: str) -> bool:
    return any(package in spec for package in DEPRECATED_PLUGIN_PACKAGES)


@dataclass(frozen=True)
class RegistrySpec:
    """A registry spec: `install_name` is the `node_modules/` directory OpenCode
    loads; `package` and `version` are what the spec names."""

    install_name: str
    package: str
    version: str | None
    install_key: str


def registry_spec(spec: str) -> RegistrySpec | None:
    """Parse an npm registry spec as `parsePluginSpecifier` and
    `resolvePluginTarget` do, or `None` for a Git, URL or `file:` spec.

    `install_key` is the `<cache>/packages/` directory: `<name>@latest` for a
    bare name, the spec as written otherwise.
    """
    alias = _ALIAS_SPEC.match(spec)
    if alias is not None:
        target = _REGISTRY_SPEC.match(alias.group("target"))
        if target is None:
            return None
        return RegistrySpec(
            alias.group("alias"), target.group("name"), target.group("version"), spec
        )
    match = _REGISTRY_SPEC.match(spec)
    if match is None:
        return None
    name, version = match.group("name"), match.group("version")
    key = f"{name}@latest" if version is None else spec
    return RegistrySpec(name, name, version, key)


def exact_version(version: str | None) -> str | None:
    return version if version is not None and _EXACT_VERSION.match(version) else None


def installed_package(cache_root: Path, spec: RegistrySpec) -> tuple[str, str] | None:
    """`(name, version)` from the package `Npm.add` installed for `spec`, the
    directory OpenCode loads and never updates (ADR-0084)."""
    manifest = cache_root / "packages" / spec.install_key / "node_modules" / spec.install_name
    try:
        data = json.loads((manifest / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name, version = data.get("name"), data.get("version")
    if not isinstance(name, str) or not re.fullmatch(_NPM_NAME, name, re.IGNORECASE):
        return None
    if not isinstance(version, str) or not _EXACT_VERSION.match(version):
        return None
    return name, version


def local_plugin_name(target: Path) -> str:
    """A local plugin's name: a directory's `package.json` `name`, else the
    directory's name; a file's stem."""
    if target.is_dir():
        try:
            data = json.loads((target / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        name = data.get("name") if isinstance(data, dict) else None
        return name if isinstance(name, str) and name else target.name
    return target.stem or target.name


# --- Agents and commands ---------------------------------------------------------


@dataclass(frozen=True)
class Definition:
    """One agent or command a configuration file declares."""

    name: str
    locator: str
    description: str | None = None
    # `True` or `False` when the entry sets `disable`; `None` when it does not.
    disable: bool | None = None
    # A `mode` entry merges last, after every other source (`loadInstanceState`).
    mode: bool = False


def agent_definitions(data: dict) -> list[Definition]:
    """`agent`, then the V2 `agents` entries `agent` does not name, then the
    deprecated `mode`. Raises `ValueError` where the V1 schema rejects one."""
    out: list[Definition] = []
    agents = _record(data, "agent")
    for name, entry in agents.items():
        out.append(_definition(name, entry, f"$.agent.{name}", "disable"))
    v2 = data.get("agents")
    if isinstance(v2, dict) and ("agent" not in data or isinstance(data["agent"], dict)):
        for name, entry in v2.items():
            if name not in agents and isinstance(entry, dict):
                out.append(_definition(name, entry, f"$.agents.{name}", "disabled"))
    for name, entry in _record(data, "mode").items():
        out.append(_definition(name, entry, f"$.mode.{name}", "disable", mode=True))
    return out


def command_definitions(data: dict) -> list[Definition]:
    """`command`, then the V2 `commands` entries `command` does not name."""
    out: list[Definition] = []
    commands = _record(data, "command")
    for name, entry in commands.items():
        if not isinstance(entry.get("template"), str):
            raise ValueError(f"command {name!r} must have a template")
        out.append(_definition(name, entry, f"$.command.{name}", None))
    v2 = data.get("commands")
    if isinstance(v2, dict) and ("command" not in data or isinstance(data["command"], dict)):
        for name, entry in v2.items():
            if name in commands or not isinstance(entry, dict):
                continue
            if isinstance(entry.get("template"), str):
                out.append(_definition(name, entry, f"$.commands.{name}", None))
    return out


def _record(data: dict, key: str) -> dict:
    if key not in data:
        return {}
    value = data[key]
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise ValueError(f"{key} must be an object of objects")
    return value


def _definition(
    name: str, entry: dict, locator: str, disable_key: str | None, *, mode: bool = False
) -> Definition:
    description = entry.get("description")
    disable = entry.get(disable_key) if disable_key is not None else None
    return Definition(
        name=name,
        locator=locator,
        description=description if isinstance(description, str) else None,
        disable=disable if isinstance(disable, bool) else None,
        mode=mode,
    )


# --- Skills ----------------------------------------------------------------------


def skills_setting(data: dict) -> dict:
    """`skills` as `{paths, urls}`. The V2 array form splits on `http(s)://`."""
    skills = data.get("skills")
    if isinstance(skills, list):
        strings = [s for s in skills if isinstance(s, str)]
        return {
            "paths": [s for s in strings if not re.match(r"^https?://", s, re.IGNORECASE)],
            "urls": [s for s in strings if re.match(r"^https?://", s, re.IGNORECASE)],
        }
    return skills if isinstance(skills, dict) else {}


def string_list(setting: dict, key: str) -> list[str]:
    value = setting.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


# --- Registry ----------------------------------------------------------------------


def parse_file(path: Path) -> list[ComponentRef]:
    """One configuration file's own MCP servers, strictly (registry use). The
    merge across files is composition's."""
    data = load(path)
    plugin_specs(data)
    agent_definitions(data)
    command_definitions(data)
    refs: list[ComponentRef] = []
    for name, item in mcp_entries(data).items():
        if item.type is not None:
            refs += server_refs(name, item.entry, source_manifest=str(path), prefix=item.prefix)
    return refs


def parse_plugin_file(path: Path) -> list[ComponentRef]:
    """A plugin file in a `.opencode/plugin(s)/` directory (registry use)."""
    if not path.is_file():
        raise ValueError(f"{path}: not a file")
    return [local_plugin_ref(path, source_manifest=str(path), locator="$")]


def local_plugin_ref(target: Path, *, source_manifest: str, locator: str) -> ComponentRef:
    name = local_plugin_name(target)
    return ComponentRef(
        name=name,
        component_identity=None,
        source_manifest=source_manifest,
        source_locator=locator,
        extra={
            "component_type": "plugin",
            "component_path": [{"type": "plugin", "name": name}],
            "declared_by": {"kind": "manifest", "path": source_manifest},
        },
    )
