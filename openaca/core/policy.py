"""Facade re-export: policy validation, evaluation and compilation. See ADR-0028.

`compile_endpoint_policy` and `render_policy_report` come from
`tools/policy_compile.py` rather than from `tools/policy_cli.py`, so importing
this module does not pull in a command module (nor, transitively,
`tools/bom_cli.py`). `tests/test_core_facade.py` asserts those three by name.

The residual dependency, recorded rather than hidden: the compilation imports
four private helpers (`_agent_scan_prep`, `_filter_agent_scope_refs`,
`_load_osv_with_overlays`, `_refs_from_graph`) from `tools/scan.py`, which also
defines the `scan` command group — so importing this facade does import a module
that builds a Click group. `tools/scan.py` is a hybrid domain/CLI module and
prising those helpers out of it is a scan-side change with its own regression
surface, deferred by plan 045.

Two things `openaca policy compile` does are the command's, not the
compilation's: it prints a note when `--project` was omitted, and it translates
`PolicyValidationError` / `PolicyEvaluationError` into a Click exception. A
programmatic caller wanting the first prints its own, and wanting the second
catches the domain errors. Writing the artifact can also raise `OSError`,
untranslated.
"""

from tools.policy import (
    Decision,
    EndpointComponent,
    Policy,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    evaluate_admission,
    loads,
    parse,
)
from tools.policy_compile import compile_endpoint_policy, render_policy_report

parse_policy = parse
parse_policy_source = loads

__all__ = [
    "Decision",
    "EndpointComponent",
    "Policy",
    "PolicyEvaluationError",
    "PolicyValidationError",
    "apply_risk_gates",
    "compile_endpoint_policy",
    "evaluate_admission",
    "parse_policy",
    "parse_policy_source",
    "render_policy_report",
]
