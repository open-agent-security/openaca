from tools.graph import Edge, Graph, Node


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
