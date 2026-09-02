"""Tier-1 declared-capability extraction from component refs."""

from __future__ import annotations

import re
import shlex
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from tools.capability import Capability
from tools.component_ref import ComponentRef

__all__ = ["declared_capabilities"]

_SKILL_TOOL_CAPABILITIES = {
    "bash": "shell_exec",
    "shell": "shell_exec",
    "write": "file_write",
    "edit": "file_write",
    "read": "file_read",
    "webfetch": "network_egress",
    "websearch": "network_egress",
}

_NETWORK_CLIENTS = frozenset({"curl", "wget", "nc", "scp", "ssh", "httpie", "http", "rsync"})


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"


def declared_capabilities(ref: ComponentRef) -> tuple[list[Capability], bool]:
    """The capabilities this component's manifest states, and whether it was read.

    The two halves answer different questions and neither may be derived from
    the other (ADR-0041 principle 2, *absence is not falsehood*). `covered` is
    true when a reading mechanism actually applied to this component -- not when
    it produced a capability. An empty list with `covered=True` is the real
    answer "this declaration names none of the taxonomy"; the same list with
    `covered=False` is "nothing here could be read".

    Coverage is decided here, in the dispatch, rather than by a predicate a
    caller applies separately: a predicate over `component_type` would answer
    for the type while the extractor answers for the individual component, and
    the two would drift the moment one branch declines (a stdio MCP server, a
    prompt hook, an unparseable skill).
    """
    extra = ref.extra or {}
    component_type = extra.get("component_type")
    if component_type == "skill":
        return _skill_capabilities(ref)
    if component_type == "hook":
        return _hook_capabilities(ref)
    if component_type == "mcp_server":
        return _mcp_capabilities(ref)
    return [], False


def _capability(name: str, execution_locus: str, evidence: dict[str, Any]) -> Capability:
    return Capability(
        name=name,
        execution_locus=execution_locus,
        method="declared",
        source="openaca",
        source_version=_openaca_version(),
        confidence="high",
        evidence=(evidence,),
    )


def _skill_capabilities(ref: ComponentRef) -> tuple[list[Capability], bool]:
    frontmatter = _read_frontmatter(Path(ref.source_manifest))
    # A failed read is uncovered. Reporting it as covered-and-empty would claim
    # OpenACA read a declaration it could not parse -- the inverse of the bug
    # this function's second return value fixes, and the worse direction of it.
    if frontmatter is None:
        return [], False
    # The mechanism is "parse `allowed-tools`", so it covers a skill only when
    # that field is there to parse. A skill that omits it is *unrestricted* --
    # it inherits the session's whole tool set -- so treating the omission as a
    # declaration of nothing would invite a divergence rule to read every
    # ordinary skill as having exceeded a declaration it never made.
    if not _declares_allowed_tools(frontmatter):
        return [], False
    caps: dict[str, Capability] = {}
    for tool in sorted(_allowed_tools(frontmatter)):
        base = _executable_tool_base(tool).lower()
        name = _SKILL_TOOL_CAPABILITIES.get(base)
        if name is None or name in caps:
            continue
        caps[name] = _capability(
            name,
            "local",
            {
                "kind": "manifest_field",
                "path": ref.source_manifest,
                "field": "allowed-tools",
                "value": tool,
            },
        )
    return list(caps.values()), True


def _hook_capabilities(ref: ComponentRef) -> tuple[list[Capability], bool]:
    command = (ref.extra or {}).get("command")
    # A prompt hook carries no command string, so there is no declared surface
    # to read -- uncovered, not covered-and-empty.
    if not isinstance(command, str) or not command:
        return [], False
    caps = [
        _capability(
            "shell_exec",
            "local",
            {"kind": "manifest_field", "path": ref.source_manifest, "field": "command"},
        )
    ]
    client = _network_client(command)
    if client is not None:
        caps.append(
            _capability(
                "network_egress",
                "local",
                {
                    "kind": "manifest_field",
                    "path": ref.source_manifest,
                    "field": "command",
                    "value": client,
                },
            )
        )
    return caps, True


def _mcp_capabilities(ref: ComponentRef) -> tuple[list[Capability], bool]:
    extra = ref.extra or {}
    # `url` is the canonical remote-MCP signal (ADR-0020); `install_source` is
    # a redundant copy the parser populates for posture rules and isn't
    # guaranteed on every ref (e.g. a re-ingested foreign BOM that models the
    # URL without duplicating it into install_source).
    url = extra.get("url")
    if not isinstance(url, str) or not url:
        install_source = extra.get("install_source")
        url = install_source if isinstance(install_source, str) else ""
    # Only the URL branch reads anything, so only a URL-bearing server is
    # covered here. A stdio server's capabilities live in its tool list, which
    # needs a live connection to obtain -- and ADR-0041 rejects starting the
    # component under assessment. The curated corpus is its only cover.
    if not url.startswith(("http://", "https://")):
        return [], False
    evidence = {"kind": "manifest_field", "field": "url", "value": url}
    return [
        _capability("network_egress", "remote", dict(evidence)),
        _capability("sensitive_data_access", "remote", dict(evidence)),
    ], True


def _network_client(command: str) -> str | None:
    # Tokenize respecting shell quoting so a client name inside a quoted
    # argument (e.g. `echo '; curl ...'`) is not mistaken for an invoked
    # command. punctuation_chars splits shell operators (; | & < >) into their
    # own tokens; a client counts only in command position (first token, or the
    # token right after an operator). Decline on a parse error rather than guess
    # (Principle 2).
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    at_command_position = True
    for token in tokens:
        if token and all(char in "();<>|&" for char in token):
            at_command_position = True
            continue
        if at_command_position:
            # A leading `VAR=value` is a shell env assignment, not the command;
            # the real client can still follow (e.g. `TOKEN=$T curl ...`).
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                continue
            client = Path(token).name
            if client in _NETWORK_CLIENTS:
                return client
            at_command_position = False
    return None


def _read_frontmatter(path: Path) -> dict[str, Any] | None:
    """The parsed frontmatter mapping, or `None` when it could not be read.

    `None` and `{}` are deliberately different: `None` is a failed read (absent
    file, undecodable bytes, no frontmatter block, invalid YAML, a non-mapping
    document) and `{}` cannot occur from a failure. Only the caller can decide
    what a failure means, and it must not be able to mistake one for an empty
    declaration.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        loaded = yaml.safe_load(text[3:end].strip())
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _declares_allowed_tools(frontmatter: dict[str, Any]) -> bool:
    """Whether the frontmatter carries an `allowed-tools` value we can parse.

    The shapes accepted here are exactly the ones `_allowed_tools` reads, so
    coverage cannot claim a field the parser would have ignored. `allowed-tools:`
    with no value parses to `None` and is not a declaration.
    """
    return isinstance(frontmatter.get("allowed-tools"), (str, list))


def _allowed_tools(frontmatter: dict[str, Any]) -> set[str]:
    raw = frontmatter.get("allowed-tools")
    if isinstance(raw, str):
        return set(re.findall(r"[^\s,(]+(?:\([^)]*\))?", raw))
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str) and item}
    return set()


def _executable_tool_base(tool: str) -> str:
    return tool.split("(", 1)[0].strip()
