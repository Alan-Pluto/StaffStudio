from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_golden.harness import GoldenHarness, HttpCapture
from agent_golden.scripted_dependencies import ScriptedLLMClient, ScriptedLLMPlan


@dataclass(frozen=True)
class LegacyScenarioCapture:
    http: HttpCapture
    message: str
    client_turn_id: str
    request_extra: dict[str, Any] = field(default_factory=dict)
    persisted_pre_state: dict[str, Any] | None = None
    interaction_checks: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)


def plan_for_variant(variant_id: str) -> ScriptedLLMPlan:
    if variant_id == "GT13-llm-error":
        return ScriptedLLMPlan(fail_phases={"Router"})
    if variant_id == "GT02-ask-refresh-continue":
        return ScriptedLLMPlan(
            json_by_phase_and_message={
                "Router": {
                    "我要购买 A1。": {
                        "decision": "start_new_task",
                        "target_skill_id": "skill_purchase_001",
                        "target_step_id": "collect_user_name",
                        "confidence": 1.0,
                        "user_intent": "购买 A1",
                        "reason": "Start purchase flow.",
                        "slot_hints": {"product_id": "A1"},
                    },
                    "我是小明，要买两件。": {
                        "decision": "continue_active",
                        "target_skill_id": "skill_purchase_001",
                        "target_step_id": "collect_user_name",
                        "confidence": 1.0,
                        "user_intent": "补充购买信息",
                        "reason": "Continue purchase flow after refresh.",
                    },
                },
                "Step Agent": {
                    "我要购买 A1。": {
                        "action": "ask_user",
                        "reply": "请告诉我您的姓名和购买数量。",
                        "slot_updates": {"product_id": "A1"},
                        "is_step_completed": False,
                    },
                    "我是小明，要买两件。": {
                        "action": "ask_user",
                        "reply": "请确认：小明购买 A1 两件，是否下单？",
                        "slot_updates": {
                            "user_name": "小明",
                            "product_id": "A1",
                            "quantity": 2,
                        },
                        "next_step_id": "confirm_purchase",
                        "is_step_completed": False,
                    },
                },
            }
        )
    if variant_id in {"GT03-true", "GT03-false"}:
        selected_step = "approve" if variant_id == "GT03-true" else "reject"
        return ScriptedLLMPlan(
            json_by_phase={
                "Router": {
                    "decision": "continue_active",
                    "target_skill_id": "skill_conditional_audit",
                    "target_step_id": "start",
                    "confidence": 1.0,
                    "user_intent": "审核报文",
                    "reason": "Exercise the selected exclusive branch.",
                    "slot_hints": {"message_content": "Golden 审核报文"},
                }
            },
            json_sequence_by_phase={
                "Step Agent": (
                    {
                        "action": "advance",
                        "reply": "审核分支已选择。",
                        "slot_updates": {"message_content": "Golden 审核报文"},
                        "next_step_id": selected_step,
                        "is_step_completed": True,
                    },
                    {
                        "action": "reply",
                        "reply": "审核结果已确认。",
                        "is_step_completed": True,
                    },
                )
            },
        )
    if variant_id == "GT04-merge":
        return ScriptedLLMPlan(
            json_by_phase={
                "Router": {
                    "decision": "continue_active",
                    "target_skill_id": "skill_parallel_audit",
                    "target_step_id": "start",
                    "confidence": 1.0,
                    "user_intent": "并行审核报文",
                    "reason": "Exercise sibling ordering and merge.",
                    "slot_hints": {"message_content": "Golden 并行审核报文"},
                }
            },
            json_sequence_by_phase={
                "Step Agent": (
                    {
                        "action": "advance",
                        "reply": "开始收款方检查。",
                        "slot_updates": {"message_content": "Golden 并行审核报文"},
                        "next_step_id": "check_payee",
                        "is_step_completed": True,
                    },
                    {
                        "action": "advance",
                        "reply": "收款方检查完成。",
                        "next_step_id": "report",
                        "is_step_completed": True,
                    },
                    {
                        "action": "advance",
                        "reply": "敏感词检查完成。",
                        "next_step_id": "report",
                        "is_step_completed": True,
                    },
                    {
                        "action": "reply",
                        "reply": "并行审核报告已生成。",
                        "is_step_completed": True,
                    },
                )
            },
        )
    return ScriptedLLMPlan()


def execute_legacy_variant(
    repo_root: Path,
    harness: GoldenHarness,
    variant_id: str,
    monkeypatch: Any | None = None,
) -> LegacyScenarioCapture:
    if variant_id == "GT01-sync":
        return _plain_chat(
            harness,
            message="你好，请介绍一下自己。",
            client_turn_id="client-gt01-sync",
            stream=False,
        )
    if variant_id == "GT01-sse":
        return _plain_chat(
            harness,
            message="你好，请流式回复。",
            client_turn_id="client-gt01-sse",
            stream=True,
        )
    if variant_id == "GT01-feedback-refresh-toggle":
        return _feedback_refresh_toggle(harness)
    if variant_id == "GT02-ask-refresh-continue":
        return _sop_refresh_continue(repo_root, harness)
    if variant_id in {"GT03-true", "GT03-false"}:
        return _conditional_branch(harness, variant_id)
    if variant_id == "GT04-merge":
        return _parallel_sibling_merge(harness)
    if variant_id == "GT13-llm-error":
        return _llm_error(repo_root, harness)
    if variant_id == "GT15-full":
        if monkeypatch is None:
            raise ValueError("GT15-full capture requires monkeypatch")
        return _scheduled_draft(harness, monkeypatch)
    if variant_id == "GT16-history":
        return _attachment_history(harness)
    raise ValueError(f"unsupported legacy fixture variant: {variant_id!r}")


def _plain_chat(
    harness: GoldenHarness,
    *,
    message: str,
    client_turn_id: str,
    stream: bool,
) -> LegacyScenarioCapture:
    payload = harness.turn_payload(message, client_turn_id=client_turn_id)
    http = harness.post_stream(payload) if stream else harness.post_sync(payload)
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        interaction_checks=[_none_interaction()],
    )


def _feedback_refresh_toggle(harness: GoldenHarness) -> LegacyScenarioCapture:
    message = "请回复后接受评价。"
    client_turn_id = "client-feedback"
    http = harness.post_sync(harness.turn_payload(message, client_turn_id=client_turn_id))
    initial_history = harness.history(http.session_id)
    assistant_message_id = initial_history[-1]["id"]
    up_status, up_response = harness.set_feedback(assistant_message_id, "up")
    up_history = harness.history(http.session_id)
    invalid_status, invalid_response = harness.set_feedback(assistant_message_id, "invalid")
    invalid_history = harness.history(http.session_id)
    clear_status, clear_response = harness.clear_feedback(assistant_message_id)
    cleared_history = harness.history(http.session_id)
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        interaction_checks=[
            {
                "kind": "feedback",
                "realtime_observation": "not_observed",
                "refresh_observation": "history_payload_observed",
                "action_result": {
                    "evidence_id": "feedback-up-invalid-clear",
                    "evidence_origin": "harness_synthetic",
                    "resource_id": assistant_message_id,
                    "action": "rate_up_reject_invalid_clear",
                    "request": {
                        "set_rating": "up",
                        "invalid_rating": "invalid",
                        "clear": True,
                    },
                    "response_status": clear_status,
                    "persisted_state": {
                        "initial_rating": initial_history[-1]["feedback_rating"],
                        "up_status": up_status,
                        "up_response": up_response,
                        "rating_after_up_refresh": up_history[-1]["feedback_rating"],
                        "invalid_status": invalid_status,
                        "invalid_response": invalid_response,
                        "rating_after_invalid_refresh": invalid_history[-1]["feedback_rating"],
                        "clear_response": clear_response,
                        "rating_after_clear_refresh": cleared_history[-1]["feedback_rating"],
                    },
                },
            }
        ],
        facts=[
            {
                "kind": "feedback_history_lifecycle",
                "assistant_message_id": assistant_message_id,
                "db_event_types": [
                    item["event_type"]
                    for item in harness.session_events(http.session_id)
                    if item["event_type"] == "message_feedback_changed"
                ],
            }
        ],
    )


def _llm_error(repo_root: Path, harness: GoldenHarness) -> LegacyScenarioCapture:
    harness.publish_scene_skill(
        json.loads(
            (repo_root / "contracts/agent/v1/corpus/production_seed/purchase.json").read_text(
                encoding="utf-8"
            )
        )
    )
    message = "触发模型异常"
    client_turn_id = "client-llm-error"
    http = harness.post_stream(harness.turn_payload(message, client_turn_id=client_turn_id))
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        interaction_checks=[_none_interaction()],
    )


def _sop_refresh_continue(repo_root: Path, harness: GoldenHarness) -> LegacyScenarioCapture:
    harness.publish_scene_skill(
        json.loads(
            (repo_root / "contracts/agent/v1/corpus/production_seed/purchase.json").read_text(
                encoding="utf-8"
            )
        )
    )
    first = harness.post_stream(
        harness.turn_payload("我要购买 A1。", client_turn_id="client-gt02-ask")
    )
    first_history = harness.history(first.session_id)
    first_session = harness.public_session(first.session_id)
    message = "我是小明，要买两件。"
    client_turn_id = "client-gt02-continue"
    second = harness.post_stream(
        harness.turn_payload(
            message,
            client_turn_id=client_turn_id,
            session_id=first.session_id,
        )
    )
    return LegacyScenarioCapture(
        http=second,
        message=message,
        client_turn_id=client_turn_id,
        request_extra={"session_id": first.session_id},
        interaction_checks=[_none_interaction()],
        facts=[
            {
                "kind": "first_turn_refresh_state",
                "first_turn_terminal": first.sse_events[-1].event,
                "assistant_reply": first_history[-1]["content"],
                "active_skill_id": first_session["active_skill_id"],
                "active_step_id": first_session["active_step_id"],
                "awaiting_input": first_session.get("awaiting_input"),
            }
        ],
    )


def _conditional_branch(
    harness: GoldenHarness,
    variant_id: str,
) -> LegacyScenarioCapture:
    harness.publish_scene_skill(_audit_graph_skill(parallel=False))
    selected_step = "approve" if variant_id == "GT03-true" else "reject"
    message = "审核通过。" if variant_id == "GT03-true" else "审核拒绝。"
    client_turn_id = f"client-{variant_id.lower()}"
    session_id = f"session-{variant_id.lower()}"
    pre_state = harness.create_persisted_session(
        session_id,
        active_skill_id="skill_conditional_audit",
        active_step_id="start",
    )
    http = harness.post_sync(
        harness.turn_payload(
            message,
            client_turn_id=client_turn_id,
            session_id=session_id,
        )
    )
    events = harness.session_events(http.session_id)
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        request_extra={"session_id": session_id},
        persisted_pre_state=pre_state,
        interaction_checks=[_none_interaction()],
        facts=[
            {
                "kind": "exclusive_graph_branch",
                "observation_source": "session_events_api",
                "selected_step_id": selected_step,
                "step_transitions": _event_payloads(events, "skill_step_changed"),
                "pending_step_updates": _event_payloads(
                    events, "graph_pending_steps_updated"
                ),
                "llm_phase_order": _llm_phase_order(),
            }
        ],
    )


def _parallel_sibling_merge(harness: GoldenHarness) -> LegacyScenarioCapture:
    harness.publish_scene_skill(_audit_graph_skill(parallel=True))
    message = "并行检查这条报文。"
    client_turn_id = "client-gt04-merge"
    session_id = "session-gt04-merge"
    pre_state = harness.create_persisted_session(
        session_id,
        active_skill_id="skill_parallel_audit",
        active_step_id="start",
    )
    http = harness.post_sync(
        harness.turn_payload(
            message,
            client_turn_id=client_turn_id,
            session_id=session_id,
        )
    )
    events = harness.session_events(http.session_id)
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        request_extra={"session_id": session_id},
        persisted_pre_state=pre_state,
        interaction_checks=[_none_interaction()],
        facts=[
            {
                "kind": "parallel_graph_merge",
                "observation_source": "session_events_api",
                "step_transitions": _event_payloads(events, "skill_step_changed"),
                "pending_step_updates": _event_payloads(
                    events, "graph_pending_steps_updated"
                ),
                "auto_progress_count": len(
                    _event_payloads(events, "graph_auto_progress_started")
                ),
                "llm_phase_order": _llm_phase_order(),
            }
        ],
    )


def _audit_graph_skill(*, parallel: bool) -> dict[str, Any]:
    skill_id = "skill_parallel_audit" if parallel else "skill_conditional_audit"
    nodes = [
        {
            "node_id": "start",
            "type": "condition",
            "name": "审核入口",
            "instruction": "根据审核结果选择后续节点。",
            "expected_user_info": ["message_content"],
            "allowed_actions": ["continue_flow"],
        },
        {
            "node_id": "approve",
            "type": "response",
            "name": "审核通过",
            "instruction": "反馈审核通过。",
            "expected_user_info": [],
            "allowed_actions": ["answer_user"],
        },
        {
            "node_id": "reject",
            "type": "response",
            "name": "审核拒绝",
            "instruction": "反馈审核拒绝。",
            "expected_user_info": [],
            "allowed_actions": ["answer_user"],
        },
    ]
    if parallel:
        nodes[1:1] = [
            {
                "node_id": "check_payee",
                "type": "condition",
                "name": "收款方一致性检查",
                "instruction": "检查收款方是否一致。",
                "expected_user_info": [],
                "allowed_actions": ["continue_flow"],
            },
            {
                "node_id": "check_sensitive",
                "type": "condition",
                "name": "敏感词检查",
                "instruction": "检查敏感词。",
                "expected_user_info": [],
                "allowed_actions": ["continue_flow"],
            },
            {
                "node_id": "report",
                "type": "response",
                "name": "生成报告",
                "instruction": "汇总检查结果。",
                "expected_user_info": [],
                "allowed_actions": ["answer_user"],
            },
        ]
        edges = [
            {
                "source_node_id": "start",
                "next_node_id": "check_payee",
                "condition": "报文已获取",
                "priority": 0,
            },
            {
                "source_node_id": "start",
                "next_node_id": "check_sensitive",
                "condition": "报文已获取",
                "priority": 1,
            },
            {
                "source_node_id": "check_payee",
                "next_node_id": "report",
                "condition": "一致性检查完成",
                "priority": 2,
            },
            {
                "source_node_id": "check_sensitive",
                "next_node_id": "report",
                "condition": "敏感词检查完成",
                "priority": 3,
            },
        ]
        terminal_node_ids = ["report"]
    else:
        edges = [
            {
                "source_node_id": "start",
                "next_node_id": "approve",
                "condition": "审核通过",
                "priority": 0,
            },
            {
                "source_node_id": "start",
                "next_node_id": "reject",
                "condition": "审核拒绝",
                "priority": 1,
            },
        ]
        terminal_node_ids = ["approve", "reject"]
    return {
        "skill_id": skill_id,
        "version": "1.0.0",
        "name": "并行审核" if parallel else "条件审核",
        "required_info": ["message_content"],
        "nodes": nodes,
        "edges": edges,
        "start_node_id": "start",
        "terminal_node_ids": terminal_node_ids,
    }


def _event_payloads(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event_type"] == event_type]


def _llm_phase_order() -> list[str]:
    return [
        f"{item['method']}:{item['phase']}"
        for item in ScriptedLLMClient.calls()
    ]


def _scheduled_draft(
    harness: GoldenHarness,
    monkeypatch: Any,
) -> LegacyScenarioCapture:
    from app.api import chat as chat_api
    from app.scheduled_tasks.schema import ScheduledTaskDraftRead

    initial = harness.post_sync(
        harness.turn_payload("先建立会话。", client_turn_id="client-draft-initial")
    )
    draft = ScheduledTaskDraftRead(
        should_create=True,
        tenant_id="tenant_golden",
        agent_id="agent_golden",
        title="每日检查价格",
        prompt="检查 A1 价格并汇总",
        schedule_type="daily",
        schedule={"time": "09:00"},
        timezone="Asia/Shanghai",
        confidence=1.0,
        reason="Golden scripted draft",
        source_session_id=initial.session_id,
    )
    monkeypatch.setattr(
        chat_api,
        "detect_scheduled_task_draft",
        lambda *_args, **_kwargs: draft,
    )
    message = "每天九点检查 A1 价格。"
    client_turn_id = "client-draft"
    request_extra = {
        "session_id": initial.session_id,
        "interaction_mode": "scheduled_task",
        "client_timezone": "Asia/Shanghai",
    }
    payload = harness.turn_payload(
        message,
        client_turn_id=client_turn_id,
        session_id=initial.session_id,
    )
    payload.update({key: value for key, value in request_extra.items() if key != "session_id"})
    http = harness.post_stream(payload)

    stored_draft = harness.history(initial.session_id)[-1]["metadata"]["scheduled_task_draft"]
    create_payload = {
        "tenant_id": "tenant_golden",
        "agent_id": "agent_golden",
        "title": stored_draft["title"],
        "prompt": stored_draft["prompt"],
        "description": stored_draft["description"],
        "schedule_type": stored_draft["schedule_type"],
        "schedule": {"time": "not-a-time"},
        "timezone": stored_draft["timezone"],
        "status": "active",
        "concurrency_policy": "forbid",
        "misfire_policy": "coalesce",
        "source_session_id": initial.session_id,
        "metadata": {"created_from": "golden_confirmation"},
    }
    invalid_status, _ = harness.create_scheduled_task(create_payload)
    invalid_history = harness.history(initial.session_id)
    invalid_persisted = invalid_history[-1]["metadata"].get("scheduled_task_created")
    invalid_task_count = len(harness.list_scheduled_tasks())

    create_payload["schedule"] = stored_draft["schedule"]
    created_status, created = harness.create_scheduled_task(create_payload)
    first_refreshed = harness.history(initial.session_id)
    first_created_metadata = first_refreshed[-1]["metadata"]["scheduled_task_created"]
    first_task_count = len(harness.list_scheduled_tasks())
    duplicate_status, duplicate = harness.create_scheduled_task(create_payload)
    refreshed = harness.history(initial.session_id)
    duplicate_metadata = refreshed[-1]["metadata"]["scheduled_task_created"]
    duplicate_task_count = len(harness.list_scheduled_tasks())
    draft_events = [item for item in http.sse_events if item.event == "scheduled_task_draft"]
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        request_extra=request_extra,
        interaction_checks=[
            {
                "kind": "scheduled_draft",
                "realtime_observation": (
                    "transport_event_observed" if len(draft_events) == 1 else "not_observed"
                ),
                "refresh_observation": (
                    "history_payload_observed"
                    if refreshed[-1]["metadata"].get("scheduled_task_draft") == stored_draft
                    else "not_observed"
                ),
                "action_result": {
                    "evidence_id": "scheduled-draft-invalid-confirm",
                    "evidence_origin": "harness_synthetic",
                    "resource_id": "scheduled-task-draft",
                    "action": "confirm_invalid",
                    "request": {**create_payload, "schedule": {"time": "not-a-time"}},
                    "response_status": invalid_status,
                    "persisted_state": {
                        "scheduled_task_created": invalid_persisted,
                        "task_count": invalid_task_count,
                    },
                },
            },
            {
                "kind": "scheduled_draft",
                "realtime_observation": (
                    "transport_event_observed" if len(draft_events) == 1 else "not_observed"
                ),
                "refresh_observation": "history_payload_observed",
                "action_result": {
                    "evidence_id": "scheduled-draft-confirm",
                    "evidence_origin": "harness_synthetic",
                    "resource_id": "scheduled-task-draft",
                    "action": "confirm",
                    "request": create_payload,
                    "response_status": created_status,
                    "persisted_state": {
                        "created_id_matches_history": created["id"] == first_created_metadata["id"],
                        "source_session_id": created["source_session_id"],
                        "title": first_created_metadata["title"],
                        "schedule": first_created_metadata["schedule"],
                        "task_count": first_task_count,
                    },
                },
            },
            {
                "kind": "scheduled_draft",
                "realtime_observation": (
                    "transport_event_observed" if len(draft_events) == 1 else "not_observed"
                ),
                "refresh_observation": "history_payload_observed",
                "action_result": {
                    "evidence_id": "scheduled-draft-duplicate-confirm",
                    "evidence_origin": "harness_synthetic",
                    "resource_id": "scheduled-task-draft",
                    "action": "confirm_duplicate",
                    "request": create_payload,
                    "response_status": duplicate_status,
                    "persisted_state": {
                        "created_distinct_task": duplicate["id"] != created["id"],
                        "history_points_to_duplicate": duplicate_metadata["id"] == duplicate["id"],
                        "task_count": duplicate_task_count,
                    },
                },
            },
        ],
    )


def _attachment_history(harness: GoldenHarness) -> LegacyScenarioCapture:
    attachments = harness.upload_text_attachment("golden-notes.txt", "第一行\n第二行".encode())
    message = "请总结附件。"
    client_turn_id = "client-attachment"
    payload = harness.turn_payload(message, client_turn_id=client_turn_id)
    payload["attachments"] = attachments
    http = harness.post_stream(payload)
    stored = harness.history(http.session_id)[0]["metadata"]["attachments"][0]
    return LegacyScenarioCapture(
        http=http,
        message=message,
        client_turn_id=client_turn_id,
        request_extra={"attachments": attachments},
        interaction_checks=[
            {
                "kind": "attachment",
                "realtime_observation": "not_observed",
                "refresh_observation": (
                    "history_payload_observed" if attachments[0] == stored else "not_observed"
                ),
                "action_result": {
                    "evidence_id": "attachment-history-inspection",
                    "evidence_origin": "harness_synthetic",
                    "resource_id": "golden-notes.txt",
                    "action": "inspect_after_history",
                    "request": {"filename": "golden-notes.txt"},
                    "response_status": 200,
                    "persisted_state": {"attachment": stored},
                },
            }
        ],
    )


def _none_interaction() -> dict[str, Any]:
    return {
        "kind": "none",
        "realtime_observation": "not_observed",
        "refresh_observation": "not_observed",
        "action_result": None,
    }
