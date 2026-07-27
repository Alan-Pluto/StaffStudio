from app.core.slot_hydration_policy import SlotHydrationPolicy
from app.db.models import ChatSession, Skill
from app.session.session_schema import AwaitingInput, PendingTask, RouterDecision


def _skill() -> Skill:
    return Skill(
        tenant_id="tenant_test",
        skill_id="purchase",
        version="1.0.0",
        name="Purchase",
        content_json={
            "required_info": ["user_name"],
            "nodes": [{"node_id": "collect", "expected_user_info": ["quantity"]}],
        },
    )


def test_hydrate_applies_profile_name_to_primary_and_task_frames() -> None:
    decision = RouterDecision(
        decision="continue_active",
        target_skill_id="purchase",
        awaiting_input=AwaitingInput(expected_fields=["user_name", "quantity"]),
        task_frames=[PendingTask(task_id="task-1", target_skill_id="purchase")],
    )
    memory = [
        {
            "kind": "profile",
            "content": "小明",
            "metadata": {"key": "preferred_name"},
        }
    ]

    result = SlotHydrationPolicy.hydrate(
        ChatSession(
            id="session_test",
            tenant_id="tenant_test",
            active_skill_id="purchase",
            slots_json={"quantity": 2},
        ),
        decision,
        [_skill()],
        memory,
    )

    assert decision.slot_hints == {"user_name": "小明"}
    assert decision.awaiting_input is None
    assert decision.task_frames[0].slot_hints == {"user_name": "小明"}
    assert result["awaiting_input_expected_fields"] == []


def test_hydration_does_not_replace_existing_or_empty_list_semantics() -> None:
    skill = _skill()
    memory = [
        {
            "kind": "profile",
            "content": "小明",
            "metadata": {"key": "preferred_name"},
        }
    ]

    assert SlotHydrationPolicy.patch(skill, {"user_name": "已有"}, memory) == {}
    assert SlotHydrationPolicy.patch(skill, {"user_name": []}, memory) == {
        "user_name": "小明"
    }
