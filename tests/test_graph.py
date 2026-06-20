import pytest

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, GraphInvariantError, Node


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


def test_validate_rejects_two_parents():
    g = Graph(
        nodes={
            "t": Node("t", "target", None),
            "a": Node("a", "skill", None),
            "b": Node("b", "skill", None),
            "c": Node("c", "package", None),
        },
        edges=[Edge("t", "a"), Edge("t", "b"), Edge("a", "c"), Edge("b", "c")],  # c has two parents
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_cycle():
    # Isolated cycle, not connected to target: each of a,b has exactly one parent,
    # so the multiple-parent check does NOT fire — cycle detection must catch it.
    g = Graph(
        nodes={
            "t": Node("t", "target", None),
            "a": Node("a", "skill", None),
            "b": Node("b", "package", None),
        },
        edges=[Edge("a", "b"), Edge("b", "a")],  # cycle, no edge from t into it
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_dangling_edge_endpoint():
    g = Graph(nodes={"t": Node("t", "target", None)}, edges=[Edge("t", "ghost")])
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_zero_or_many_targets():
    g = Graph(nodes={"a": Node("a", "skill", None)}, edges=[])
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_disconnected_node():
    # target + a package with no path to it → not "one tree rooted at target"
    g = Graph(
        nodes={"t": Node("t", "target", None), "orphan": Node("orphan", "package", None)},
        edges=[],
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_accepts_well_formed_tree():
    g = Graph(
        nodes={
            "t": Node("t", "target", None),
            "p": Node("p", "plugin", None),
            "s": Node("s", "skill", None),
        },
        edges=[Edge("t", "p"), Edge("p", "s")],
    )
    g.validate()  # must not raise


def test_validate_rejects_many_targets():
    g = Graph(
        nodes={"t1": Node("t1", "target", None), "t2": Node("t2", "target", None)},
        edges=[],
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_lineage_of_root_is_just_root():
    g = Graph(nodes={"t": Node("t", "target", None)}, edges=[])
    assert [n.key for n in g.lineage(g.nodes["t"])] == ["t"]


def test_scope_of_agent_component_returns_agent_component():
    # scope_of is called on EVERY non-root node in Stage 3; an agent-component
    # (e.g. a skill) must return "agent-component" (NOT raise).
    g = Graph(
        nodes={
            "t": Node("t", "target", None),
            "s": Node("s", "skill", _ref("skill/deploy@1", "skill")),
        },
        edges=[Edge("t", "s")],
    )
    assert g.scope_of(g.nodes["s"]) == "agent-component"


def test_nearest_plugin_ancestor_returns_nearest_of_several():
    # target → plugin_outer → skill → plugin_inner → package
    nodes = {
        "t": Node("t", "target", None),
        "po": Node("po", "plugin", _ref("plugin/mp/outer@1", "plugin")),
        "s": Node("s", "skill", _ref("skill/deploy@1", "skill")),
        "pi": Node("pi", "plugin", _ref("plugin/mp/inner@1", "plugin")),
        "pkg": Node("pkg", "package", _ref("pkg:npm/lodash@4.17.20", "package")),
    }
    edges = [Edge("t", "po"), Edge("po", "s"), Edge("s", "pi"), Edge("pi", "pkg")]
    g = Graph(nodes, edges)
    assert g.nearest_plugin_ancestor(g.nodes["pkg"]).key == "pi"  # nearest, not "po"
