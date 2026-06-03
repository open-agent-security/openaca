"""Facade re-export: OSV query planning + provenance stamping. See ADR-0028."""

from tools.osv_federation import (
    OsvQuery,
    collect_osv_queries,
    stamp_osv_query_provenance,
)

__all__ = ["OsvQuery", "collect_osv_queries", "stamp_osv_query_provenance"]
