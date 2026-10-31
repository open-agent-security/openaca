import hashlib
import json

import pytest

from tools.bom import build_agent_bom, graph_from_cyclonedx
from tools.component_ref import ComponentRef


@pytest.mark.parametrize("suffix", ["", ".git", "/"])
def test_plugin_bom_carries_only_a_normalized_marketplace_digest(suffix):
    source = "https://github.com/anthropics/claude-plugins-official" + suffix
    ref = ComponentRef(
        name="discord",
        extra={
            "component_type": "plugin",
            "marketplace": "official",
            "marketplace_source": source,
        },
    )
    document = build_agent_bom([ref]).to_cyclonedx()
    props = {p["name"]: p["value"] for p in document["components"][0]["properties"]}
    expected = hashlib.sha256(b'["github","anthropics/claude-plugins-official"]').hexdigest()
    assert props["openaca:marketplace_source_digest"] == expected
    assert source not in json.dumps(document)
    graph = graph_from_cyclonedx(document)
    recovered = next(node.ref for node in graph.nodes.values() if node.ref)
    assert recovered.extra["marketplace_source_digest"] == expected
    again = build_agent_bom([recovered]).to_cyclonedx()
    assert {p["name"]: p["value"] for p in again["components"][0]["properties"]}[
        "openaca:marketplace_source_digest"
    ] == expected


@pytest.mark.parametrize(
    "source",
    [
        None,
        "official",
        "/private/market",
        "https://user:secret@git.test/a/b",
        "https://git.test/a/b?token=secret",
    ],
)
def test_missing_invalid_or_credentialed_source_has_no_marketplace_digest(source):
    ref = ComponentRef(
        name="discord", extra={"component_type": "plugin", "marketplace_source": source}
    )
    document = build_agent_bom([ref]).to_cyclonedx()
    assert "openaca:marketplace_source_digest" not in json.dumps(document)
