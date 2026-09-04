"""Facade re-export: the scanner's finding value types. See ADR-0028.

`collect_installed_agents` returns `PostureFinding` and `ObservationFinding`
as themselves rather than mapped into some consumer's payload vocabulary, so
both have to be reachable here. `Standards` is not a third entry point but
the type `PostureFinding.standards` already exposes, without which the field
is unusable.

These three are safe to publish because they are `@dataclass(frozen=True)`
records whose behaviour is read-only: the derived `component_label` and
`location` properties on both finding types, and `Standards.to_dict`, which
drops empty taxonomy lists. Nothing mutates, computes over an endpoint, or
performs I/O.

`frozen=True` here is **shallow**. `Standards`' six taxonomy lists, and
`evidence`, `component`, `active_in` and `component_path` on the findings, are
plain `list`/`dict` fields a caller can mutate in place. The contract published
is *OpenACA does not hand the same object to two consumers and does not mutate
one after returning it* — each collection builds its findings fresh — not that
the nested containers are read-only. A consumer that intends to keep a finding
beyond the call and mutate it should copy it.
"""

from tools.observations.finding import ObservationFinding
from tools.posture.finding import PostureFinding, Standards

__all__ = ["ObservationFinding", "PostureFinding", "Standards"]
