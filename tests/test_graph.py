from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node


def _ref(identity, ctype):
    return ComponentRef(component_identity=identity, extra={"component_type": ctype})


def _build():
    nodes = {
        "t": Node("t", "target", None),
        "p": Node("p", "plugin", _ref("plugin/mp/demo@1", "plugin")),
        "s": Node("s", "skill", _ref("skill/deploy@1", "skill")),
        "pkg": Node("pkg", "package", _ref("pkg:npm/lodash@4.17.20", "package")),
        "bare": Node("bare", "package", _ref("pkg:npm/left-pad@1.0.0", "package")),
    }
    edges = [Edge("t", "p"), Edge("p", "s"), Edge("s", "pkg"), Edge("t", "bare")]
    return Graph(nodes, edges)


def test_lineage_walks_to_root():
    g = _build()
    assert [n.key for n in g.lineage(g.nodes["pkg"])] == ["pkg", "s", "p", "t"]


def test_scope_agent_dependency_when_agent_in_lineage():
    g = _build()
    assert g.scope_of(g.nodes["pkg"]) == "agent-dependency"


def test_scope_software_dependency_when_no_agent_ancestor():
    g = _build()
    assert g.scope_of(g.nodes["bare"]) == "software-dependency"


def test_nearest_plugin_ancestor():
    g = _build()
    assert g.nearest_plugin_ancestor(g.nodes["pkg"]).key == "p"
    assert g.nearest_plugin_ancestor(g.nodes["bare"]) is None


def test_graph_roots_and_children():
    target = Node(key="target:/repo", kind="target", ref=None)
    plugin = Node(key="plugin/mp/demo@1", kind="plugin", ref=None)
    skill = Node(key="skill/deploy@1", kind="skill", ref=None)
    g = Graph(
        nodes={n.key: n for n in (target, plugin, skill)},
        edges=[
            Edge(parent="target:/repo", child="plugin/mp/demo@1"),
            Edge(parent="plugin/mp/demo@1", child="skill/deploy@1"),
        ],
    )
    assert g.root.key == "target:/repo"
    assert [c.key for c in g.children_of(target)] == ["plugin/mp/demo@1"]
    assert [c.key for c in g.children_of(plugin)] == ["skill/deploy@1"]
