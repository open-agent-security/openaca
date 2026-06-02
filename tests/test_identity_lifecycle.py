from tools.bom import build_agent_bom, component_refs_from_cyclonedx
from tools.component_ref import ComponentRef
from tools.fleet.collector import _prepare_fleet_bom
from tools.osv_federation import collect_target_purls
from tools.render import render_inventory_tree


def test_github_and_docker_mcp_refs_survive_identity_lifecycle():
    sha = "0123456789abcdef0123456789abcdef01234567"
    refs = [
        ComponentRef(
            ecosystem="github",
            name="oraios/serena",
            version=sha,
            source_manifest=".mcp.json",
            source_locator="mcpServers.serena",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    f"uvx --from git+https://github.com/oraios/serena.git@{sha} "
                    "serena --token secret"
                ),
            },
        ),
        ComponentRef(
            ecosystem="docker",
            name="hashicorp/terraform-mcp-server",
            version="0.4.0",
            source_manifest=".mcp.json",
            source_locator="mcpServers.terraform",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    "docker run -i --rm -e TFE_TOKEN=${TFE_TOKEN} "
                    "hashicorp/terraform-mcp-server:0.4.0"
                ),
            },
        ),
    ]

    bom = build_agent_bom(refs, target_type="endpoint").to_cyclonedx()
    round_tripped = component_refs_from_cyclonedx(bom)

    assert [ref.ecosystem for ref in round_tripped] == ["GitHub", "Docker"]
    assert round_tripped[0].purl == f"pkg:github/oraios/serena@{sha}"
    assert round_tripped[1].purl == "pkg:docker/hashicorp/terraform-mcp-server@0.4.0"
    assert collect_target_purls(round_tripped) == []

    rendered = render_inventory_tree(round_tripped, [], use_unicode=True)
    assert f"oraios/serena@{sha} (stdio via uvx)" in rendered
    assert "hashicorp/terraform-mcp-server@0.4.0 (stdio via docker)" in rendered
    assert "uvx (stdio, args hidden)" not in rendered
    assert "docker (stdio, args hidden)" not in rendered

    prepared = _prepare_fleet_bom(bom)
    github_props = _props_by_name(prepared["components"][0])
    docker_props = _props_by_name(prepared["components"][1])
    assert github_props["openaca:install_source"] == (
        f"uvx git+https://github.com/oraios/serena@{sha}"
    )
    assert docker_props["openaca:install_source"] == ("docker hashicorp/terraform-mcp-server:0.4.0")
    assert "secret" not in github_props["openaca:install_source"]
    assert "TFE_TOKEN" not in docker_props["openaca:install_source"]


def _props_by_name(component):
    return {prop["name"]: prop["value"] for prop in component.get("properties", [])}
