from app.core.agent_loop import AgentLoop
from app.db.models import ChatSession, Skill


def _skill() -> Skill:
    return Skill(
        tenant_id="tenant_test",
        skill_id="skill_test",
        version="1.0.0",
        name="Test",
        content_json={"nodes": []},
    )


def test_skill_steps_keeps_ordered_nodes_patch_seam() -> None:
    loop = object.__new__(AgentLoop)
    loop._ordered_skill_nodes = lambda _skill: [{"node_id": "patched"}]

    assert loop._skill_steps(_skill())[0]["step_id"] == "patched"


def test_sibling_queue_keeps_edge_patch_seams() -> None:
    loop = object.__new__(AgentLoop)
    calls: list[str] = []
    loop._graph_outgoing_edges = lambda _skill: {
        "start": [
            {"next_node_id": "selected", "condition": "SAME"},
            {"next_node_id": "sibling", "condition": "same"},
        ]
    }

    def edge_condition(edge: dict) -> str:
        calls.append(str(edge["next_node_id"]))
        return str(edge["condition"]).lower()

    loop._edge_condition = edge_condition
    loop._graph_pending_steps = lambda _session: []
    stored: list[list[str]] = []
    loop._store_graph_pending_steps = (
        lambda _tenant_id, _session, pending: stored.append(pending)
    )

    loop._queue_graph_sibling_steps(
        "tenant_test",
        ChatSession(id="session_test", tenant_id="tenant_test"),
        _skill(),
        "start",
        "selected",
    )

    assert calls == ["selected", "sibling"]
    assert stored == [["sibling"]]
