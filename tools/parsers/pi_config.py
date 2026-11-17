"""Pi's configuration files (docs/specs/pi-agent-kind.md).

Each reader follows Pi 1.5.2's own loader, read from its shipped package: the
merge and validation of the two `mcp.json` scopes (`dist/extensions/mcp/config.js`,
`pi-mcp/dist/config.js`), the package-source grammar
(`dist/core/package-manager.js`, `dist/utils/git.js`), the `pi` key of a
`package.json` (`dist/core/pi-manifest.js`) and the trust store
(`dist/core/trust-manager.js`). Pi reads every one as strict JSON after a
byte-order mark is removed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml

from tools.component_ref import ComponentRef
from tools.parsers import mcp_json

PROJECT_CONFIG_DIR = ".pi"
SETTINGS_FILENAME = "settings.json"
MCP_FILENAME = "mcp.json"
TRUST_FILENAME = "trust.json"
RESOURCE_TYPES = ("extensions", "skills", "prompts")

_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_OVERRIDE_KEYS = frozenset({"enabled", "exposure", "toolExposure"})
_NPM_SPEC = re.compile(r"^(@?[^@]+(?:/[^@]+)?)(?:@(.+))?$")
_EXACT_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_GIT_PROTOCOL = re.compile(r"^(?:https?|ssh|git)://", re.IGNORECASE)
_SCP = re.compile(r"^git@([^:]+):(.+)$")
_NOT_LOCAL_PREFIXES = ("npm:", "git:", "github:", "http:", "https:", "ssh:", "builtin:")

Note = Callable[[str], None]


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_exact_version(version: object) -> bool:
    return isinstance(version, str) and _EXACT_SEMVER.fullmatch(version) is not None


def is_commit(ref: object) -> bool:
    return isinstance(ref, str) and _COMMIT_SHA.fullmatch(ref) is not None


# --- mcp.json ------------------------------------------------------------------


@dataclass(frozen=True)
class McpServer:
    name: str
    entry: dict
    # The file holding the server's command or URL. An override from the
    # project keeps the user file, whose headers and credentials it inherits.
    source: Path


def merge_mcp_servers(
    files: list[tuple[Path, Literal["user", "project"]]],
    *,
    record_gap: Note,
    note: Note,
) -> list[McpServer]:
    """The servers Pi loads from `files`, user first. A project entry replaces
    a user entry of its name; a project entry with no `command`, `url` or
    `type` overrides `enabled`, `exposure` and `toolExposure` of the user
    entry. An entry Pi rejects is no server, and `note` says why."""
    servers: dict[str, McpServer] = {}
    for path, scope in files:
        if not path.is_file():
            continue
        try:
            data = load(path)
        except (OSError, ValueError) as exc:
            record_gap(f"could not parse {path}: {exc}")
            continue
        entries = data.get("mcpServers", {}) if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            record_gap(f'could not parse {path}: expected an object with an "mcpServers" object')
            continue
        for name, raw in entries.items():
            problem = _merge_one(servers, path, scope, name, raw)
            if problem is not None:
                note(f"{path}: MCP server {name!r} is not loaded: {problem}")
    return list(servers.values())


def _merge_one(
    servers: dict[str, McpServer], path: Path, scope: str, name: str, raw: object
) -> str | None:
    if scope == "project" and isinstance(raw, dict) and _is_override(raw):
        base = servers.get(name)
        if base is None:
            return "an override needs a user server of the same name"
        if set(raw) - _OVERRIDE_KEYS:
            return f"an override can set only {', '.join(sorted(_OVERRIDE_KEYS))}"
        merged = {**base.entry, **raw}
        problem = _invalid(name, merged)
        if problem is None:
            servers[name] = replace(base, entry=merged)
        return problem
    problem = _invalid(name, raw)
    if problem is not None:
        return problem
    assert isinstance(raw, dict)
    clash = next(
        (other for other in servers if other != name and _namespace(other) == _namespace(name)),
        None,
    )
    if clash is not None:
        return f"its name clashes with {clash!r}"
    if scope == "project" and isinstance(raw.get("url"), str) and raw.get("auth"):
        return "auth is allowed only in the user mcp.json"
    servers[name] = McpServer(name=name, entry=raw, source=path)
    return None


def _is_override(entry: dict) -> bool:
    return "command" not in entry and "url" not in entry and "type" not in entry


def _namespace(name: str) -> str:
    return name.replace("-", "_")


def _invalid(name: str, raw: object) -> str | None:
    """Pi's validation of one entry, down to the fields that decide whether a
    server loads at all."""
    if not _SERVER_NAME.fullmatch(name):
        return "a name may hold only letters, digits, '_' and '-'"
    if not isinstance(raw, dict):
        return "the entry must be an object"
    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        return "enabled must be a boolean"
    kind = raw.get("type")
    if kind == "sse":
        return "the SSE transport is not supported"
    url, command = raw.get("url"), raw.get("command")
    if isinstance(url, str) and kind in (None, "http", "streamable-http"):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "url must be an http or https URL"
        if "headers" in raw and not _string_map(raw["headers"]):
            return "headers must map names to strings"
        return None
    if isinstance(command, str) and kind in (None, "stdio"):
        args = raw.get("args", [])
        if not (isinstance(args, list) and all(isinstance(arg, str) for arg in args)):
            return "args must be an array of strings"
        if "env" in raw and not _string_map(raw["env"]):
            return "env must map names to strings"
        return None
    return 'the entry needs "command" (stdio) or "url" (streamable HTTP)'


def _string_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(item, str) for item in value.values())


def server_refs(server: McpServer) -> list[ComponentRef]:
    """One server as the shared MCP parser reads it. Pi's `enabled: false`
    keeps the entry, so the ref records the readable state."""
    refs = mcp_json.parse_mcp_servers(
        {server.name: server.entry}, source_manifest=str(server.source)
    )
    enabled = server.entry.get("enabled") is not False
    return [replace(ref, extra={**ref.extra, "enabled": enabled}) for ref in refs]


def parse_mcp_file(path: Path) -> list[ComponentRef]:
    """A repository's `.pi/mcp.json` as one source unit. A malformed file
    raises, so it counts as a failed unit."""
    data = load(path)
    if not isinstance(data, dict):
        raise ValueError("mcp.json must contain an object")
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError('mcp.json "mcpServers" must be an object')
    return mcp_json.parse_mcp_servers(servers, source_manifest=str(path))


def parse_settings_file(path: Path) -> list[ComponentRef]:
    """A repository's `.pi/settings.json` as one source unit. Its packages and
    resource paths are composed by the graph builder."""
    if not isinstance(load(path), dict):
        raise ValueError("settings.json must contain an object")
    return []


# --- settings.json -------------------------------------------------------------


def load_settings(path: Path, *, record_gap: Note) -> dict:
    if not path.is_file():
        return {}
    try:
        data = load(path)
    except (OSError, ValueError) as exc:
        record_gap(f"could not parse {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        record_gap(f"could not parse {path}: settings must be an object")
        return {}
    return data


def resource_entries(settings: dict, resource_type: str) -> list[str]:
    """The plain paths of one resource array. An entry starting `!`, `+` or
    `-`, or holding `*` or `?`, is a pattern that narrows what loads rather
    than adding a path, and `builtin:` names a built-in extension."""
    value = settings.get(resource_type)
    if resource_type == "skills" and isinstance(value, dict):
        # Settings written before the array form kept skill paths here.
        value = value.get("customDirectories")
    if not isinstance(value, list):
        return []
    return [
        entry
        for entry in value
        if isinstance(entry, str)
        and entry.strip()
        and not entry.startswith(("!", "+", "-", "builtin:"))
        and "*" not in entry
        and "?" not in entry
    ]


def resolve_path(entry: str, base: Path, home: Path | None) -> Path | None:
    """Pi's `resolvePath`: trimmed, a leading `~` naming the home directory,
    relative to `base` otherwise. `None` for a `~` path when no home directory
    may be read, as in a repository scan."""
    text = entry.strip()
    if text.startswith("file://"):
        text = urlparse(text).path
    if text == "~" or text.startswith("~/"):
        return None if home is None else home / text[2:]
    path = Path(text)
    return path if path.is_absolute() else base / path


# --- Packages ------------------------------------------------------------------


@dataclass(frozen=True)
class PackageSource:
    kind: Literal["npm", "git", "local"]
    raw: str
    name: str | None = None
    version: str | None = None
    host: str | None = None
    path: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class PackageEntry:
    index: int
    source: PackageSource
    # `False` only for an object entry with `"autoload": false`: in the project
    # file it filters the user entry of the same package instead of adding one.
    autoload: bool


def package_entries(settings: dict, *, path: Path, record_gap: Note) -> list[PackageEntry]:
    packages = settings.get("packages")
    if packages is None:
        return []
    if not isinstance(packages, list):
        record_gap(f"could not parse {path}: packages must be an array")
        return []
    entries: list[PackageEntry] = []
    for index, entry in enumerate(packages):
        raw = entry.get("source") if isinstance(entry, dict) else entry
        if not isinstance(raw, str) or not raw.strip():
            record_gap(f"could not parse {path}: $.packages[{index}] names no source")
            continue
        autoload = not (isinstance(entry, dict) and entry.get("autoload") is False)
        entries.append(PackageEntry(index=index, source=parse_source(raw), autoload=autoload))
    return entries


def parse_source(source: str) -> PackageSource:
    """`npm:<name>[@<version>]`, a git source, or a local path, in Pi's order."""
    text = source.strip()
    if text.startswith("npm:"):
        spec = text[len("npm:") :].strip()
        match = _NPM_SPEC.match(spec)
        name, version = (match.group(1), match.group(2)) if match else (spec, None)
        return PackageSource("npm", source, name=name, version=version)
    if text.startswith(_NOT_LOCAL_PREFIXES):
        git = _parse_git(text, source)
        if git is not None:
            return git
    return PackageSource("local", source, path=text)


def _parse_git(text: str, source: str) -> PackageSource | None:
    """`git:` with any shorthand, or an explicit `https`, `ssh` or `git` URL."""
    has_prefix = text.startswith("git:")
    url = text[len("git:") :].strip() if has_prefix else text
    if not has_prefix and not _GIT_PROTOCOL.match(url):
        return None
    repo, ref = _split_ref(url)
    scp = _SCP.match(repo)
    if scp:
        host, path = scp.group(1), scp.group(2)
    elif "://" in repo:
        parsed = urlparse(repo)
        host, path = parsed.hostname or "", parsed.path
    else:
        host, _, path = repo.partition("/")
        if "." not in host and host != "localhost":
            return None
    path = path.removesuffix(".git").lstrip("/")
    if not host or len(path.split("/")) < 2 or _unsafe(host, "/") or _unsafe(path, ""):
        return None
    return PackageSource("git", source, host=host, path=path, ref=ref)


def _split_ref(url: str) -> tuple[str, str | None]:
    """A trailing `@<ref>` on the repository path, as Pi's `splitRef`."""
    scp = _SCP.match(url)
    if scp:
        repo_path, ref = _split_at(scp.group(2))
        return (f"git@{scp.group(1)}:{repo_path}", ref) if ref else (url, None)
    if "://" in url:
        parsed = urlparse(url)
        repo_path, ref = _split_at(parsed.path.lstrip("/"))
        if not ref:
            return url, None
        return parsed._replace(path=f"/{repo_path}").geturl().rstrip("/"), ref
    host, slash, rest = url.partition("/")
    if not slash:
        return url, None
    repo_path, ref = _split_at(rest)
    return (f"{host}/{repo_path}", ref) if ref else (url, None)


def _split_at(path: str) -> tuple[str, str | None]:
    head, at, tail = path.partition("@")
    if not at or not head or not tail:
        return path, None
    return head, tail


def _unsafe(value: str, forbidden: str) -> bool:
    if "\0" in value or "\\" in value or value.startswith("/"):
        return True
    if forbidden and forbidden in value:
        return True
    return ".." in value.split("/")


def pi_manifest(package_json: Path) -> dict[str, list[str]] | None:
    """The `pi` key of a `package.json`: each resource field that is an array
    of strings, or `None` when there is no `pi` object."""
    try:
        data = load(package_json)
    except (OSError, ValueError):
        return None
    pi = data.get("pi") if isinstance(data, dict) else None
    if not isinstance(pi, dict):
        return None
    return {
        field: entries
        for field in (*RESOURCE_TYPES, "themes")
        if isinstance(entries := pi.get(field), list)
        and all(isinstance(entry, str) for entry in entries)
    }


def package_json_field(package_json: Path, field: str) -> str | None:
    try:
        data = load(package_json)
    except (OSError, ValueError):
        return None
    value = data.get(field) if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def git_head(checkout: Path) -> str | None:
    """The commit a git checkout's `HEAD` names: a detached commit, or the
    commit of the branch it points at, from a loose ref or `packed-refs`."""
    git_dir = checkout / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if is_commit(head):
        return head.lower()
    if not head.startswith("ref: "):
        return None
    ref = head[len("ref: ") :].strip()
    if ".." in ref.split("/"):
        return None
    try:
        value = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        value = _packed_ref(git_dir, ref)
    return value.lower() if is_commit(value) else None


def _packed_ref(git_dir: Path, ref: str) -> str:
    try:
        lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        commit, _, name = line.partition(" ")
        if name.strip() == ref:
            return commit
    return ""


# --- trust.json ----------------------------------------------------------------


def load_trust(path: Path) -> dict[str, bool | None]:
    """`{canonical directory: true | false | null}`. Pi refuses any other
    value, so this raises `ValueError` as Pi fails."""
    if not path.is_file():
        return {}
    data = load(path)
    if not isinstance(data, dict):
        raise ValueError("the trust store must be an object")
    if any(value not in (True, False, None) for value in data.values()):
        raise ValueError("a trust decision must be true, false or null")
    return data


def trust_decision(trust: dict[str, bool | None], project: Path) -> bool | None:
    """The nearest decision for `project` or an ancestor. A `null` value is no
    decision, and the walk goes on to the parent."""
    for directory in (project, *project.parents):
        value = trust.get(str(directory))
        if isinstance(value, bool):
            return value
    return None


# --- SKILL.md --------------------------------------------------------------------


def skill_has_description(skill_md: Path) -> bool:
    """Pi does not load a `SKILL.md` whose frontmatter has no non-empty
    `description`."""
    try:
        text = skill_md.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return False
    end = text.find("\n---", 3) if text.startswith("---") else -1
    if end == -1:
        return False
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return False
    description = data.get("description") if isinstance(data, dict) else None
    return isinstance(description, str) and bool(description.strip())
