"""Facade re-export: the collection API. See ADR-0028.

`collect_installed_agents` answers *what is this machine running, and what is
wrong with it* in one call. `CollectedAgent` is the result it returns and
`ScannerUnavailable` the one failure the call has that a caller is expected to
handle — naming a scanner in `external_scanners` whose command is not
installed. The findings the result carries are re-exported from
`openaca.core.findings`.

The machinery behind collection stays internal: the graph, the agent instance,
the discovery context, the kind registry, the warning log and the coverage
counters. `tests/test_core_facade.py` asserts their absence by name.
"""

from tools.collect import CollectedAgent, ScannerUnavailable, collect_installed_agents

__all__ = ["CollectedAgent", "ScannerUnavailable", "collect_installed_agents"]
