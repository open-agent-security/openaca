"""Per-host endpoint composition seeds (ADR-0044).

One module per host that supports endpoint mode; each exposes a
`seed_endpoint(graph, target, config_root, project_root, normalize, *,
warnings=None)` matching `tools.hosts.EndpointSeedFn`. Dependency direction
is `endpoint_seeds.* -> graph_build -> hosts`: seed modules import the shared
discovery helpers from `tools.graph_build`, and `tools.hosts` binds them
lazily (deferred import) so the registry stays import-cycle free.
"""
