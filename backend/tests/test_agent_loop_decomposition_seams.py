from typing import Any

import app.core.agent_loop as agent_loop_module
from app.core.agent_loop import AgentLoop
from app.core.human_handoff_service import HumanHandoffService
from app.db.models import ChatSession, Skill, Tool
from app.session.session_schema import AwaitingInput, RouterDecision, StepAgentResult
from app.tools.tool_schema import ToolCall, ToolResult


def _loop() -> AgentLoop:
    return AgentLoop.__new__(AgentLoop)


def test_slot_hydration_preserves_agent_loop_private_seams(monkeypatch) -> None:
    loop = _loop()
    calls: list[str] = []
    monkeypatch.setattr(
        loop,
        "_slot_hydration_patch",
        lambda skill, slots, memory: calls.append("patch") or {"user_name": "A"},
    )
    monkeypatch.setattr(
        loop,
        "_trim_satisfied_awaiting_fields",
        lambda decision, slots: calls.append("trim") or [],
    )
    skill = Skill(tenant_id="tenant", skill_id="skill", version="1", name="Skill")
    decision = RouterDecision(
        decision="start_new_task",
        target_skill_id="skill",
        awaiting_input=AwaitingInput(expected_fields=["user_name"]),
    )

    loop._hydrate_router_decision_from_context(
        ChatSession(id="session", tenant_id="tenant"), decision, [skill], []
    )

    assert calls == ["patch", "trim"]


def test_tool_replay_preserves_agent_loop_private_seams(monkeypatch) -> None:
    loop = _loop()
    calls: list[str] = []
    monkeypatch.setattr(
        loop,
        "_tool_call_history",
        lambda slots: calls.append("history") or [],
    )
    monkeypatch.setattr(
        loop,
        "_tool_call_signature",
        lambda call: calls.append("call") or call.name,
    )
    monkeypatch.setattr(
        loop,
        "_tool_history_signature",
        lambda item: calls.append("item") or str(item),
    )
    session = ChatSession(id="session", tenant_id="tenant")
    call = ToolCall(name="orders.create", arguments={})

    loop._record_tool_result_in_slots(
        session, call, ToolResult(tool_name=call.name, success=True)
    )

    assert calls == ["history", "call"]


def test_tool_config_and_citation_projection_preserve_private_seams(monkeypatch) -> None:
    loop = _loop()
    parsed: list[object] = []
    monkeypatch.setattr(
        loop,
        "_idempotency_enabled_value",
        lambda value: parsed.append(value) or True,
    )
    tool = Tool(
        tenant_id="tenant",
        name="orders.create",
        config_json={"idempotency": {"enabled": "custom"}},
    )
    assert loop._tool_idempotency_config(tool) == (True, None)
    assert parsed == ["custom"]

    citations: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(
        loop,
        "_dedupe_knowledge_citations",
        lambda items: citations.append(items) or [{"title": "patched"}],
    )
    metadata = loop._assistant_message_metadata(
        StepAgentResult(
            knowledge_results=[{"chunks": [{"title": "source", "content": "body"}]}]
        ),
        ChatSession(id="session", tenant_id="tenant"),
    )
    assert citations
    assert metadata["knowledge_citations"] == [{"title": "patched"}]


def test_handoff_service_receives_agent_loop_private_callbacks(monkeypatch) -> None:
    loop = _loop()
    loop.db = object()  # type: ignore[assignment]
    loop.events = object()  # type: ignore[assignment]
    calls: list[str] = []
    monkeypatch.setattr(loop, "_current_skill_step", lambda skill, step_id: {})
    monkeypatch.setattr(
        loop,
        "_human_handoff_assignee_user_id",
        lambda tenant_id, agent_id, user_id: calls.append("assignee") or user_id,
    )
    monkeypatch.setattr(
        loop,
        "_human_handoff_context_summary",
        lambda session: calls.append("context") or "summary",
    )
    monkeypatch.setattr(
        loop,
        "_human_handoff_pending_question",
        lambda step, result: calls.append("question") or "question",
    )

    def fake_create(
        service: HumanHandoffService,
        tenant_id: str,
        session: ChatSession,
        result: StepAgentResult,
        **callbacks: Any,
    ) -> object:
        current_step = callbacks["current_step_resolver"]()
        callbacks["assignee_resolver"](tenant_id, session.agent_id, session.user_id)
        callbacks["context_summary"](session)
        callbacks["pending_question"](current_step, result)
        return object()

    monkeypatch.setattr(HumanHandoffService, "create", fake_create)
    skill = Skill(tenant_id="tenant", skill_id="skill", version="1", name="Skill")
    loop._create_human_handoff_request(
        "tenant",
        ChatSession(
            id="session",
            tenant_id="tenant",
            agent_id="agent",
            user_id="user",
            active_step_id="step",
        ),
        skill,
        StepAgentResult(),
    )

    assert calls == ["assignee", "context", "question"]


def test_existing_handoff_short_circuits_before_step_resolution(monkeypatch) -> None:
    class ExistingQuery:
        def where(self, *args: Any) -> "ExistingQuery":
            return self

        def first(self) -> object:
            return object()

    class ExistingDb:
        def exec(self, statement: Any) -> ExistingQuery:
            return ExistingQuery()

    service = HumanHandoffService(ExistingDb(), object())  # type: ignore[arg-type]
    existing = type(
        "ExistingHandoff",
        (),
        {"id": "handoff", "pending_question": "pending"},
    )()
    monkeypatch.setattr(
        service.db,
        "exec",
        lambda statement: type("Result", (), {"first": lambda self: existing})(),
    )
    session = ChatSession(id="session", tenant_id="tenant")

    returned = service.create(
        "tenant",
        session,
        StepAgentResult(),
        current_step_resolver=lambda: (_ for _ in ()).throw(AssertionError("called")),
        assignee_resolver=lambda tenant, agent, user: None,
        context_summary=lambda value: "",
        pending_question=lambda step, result: "",
    )

    assert returned is existing
    assert session.awaiting_input_json == {
        "type": "human_handoff",
        "handoff_id": "handoff",
        "pending_question": "pending",
    }


def test_persona_prompt_preserves_module_level_patch_seams(monkeypatch) -> None:
    loop = _loop()
    agent = type("Agent", (), {"is_overall": False})()
    monkeypatch.setattr(loop, "_get_agent_profile", lambda tenant_id, agent_id: agent)
    monkeypatch.setattr(
        agent_loop_module,
        "_agent_identity_prompt",
        lambda value: "patched persona",
    )

    assert loop._get_persona_prompt("tenant", "agent") == "patched persona"
