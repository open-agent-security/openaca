"""Which agents a component is active in — one definition, four readers.

The agent doing the scanning is the answer (ADR-0044: the BOM's subject carries
what `openaca:runtime_hosts` used to). The stored-property fallback exists only
for `0.4` documents read back off disk, whose emitter has been removed.
"""

from __future__ import annotations

from tools.component_ref import ComponentRef


def active_in(ref: ComponentRef, *, agent_kind: str | None = None) -> list[str]:
    if agent_kind:
        return [agent_kind]
    stored = (ref.extra or {}).get("runtime_hosts")
    if isinstance(stored, list):
        return [value for value in stored if isinstance(value, str)]
    return []
