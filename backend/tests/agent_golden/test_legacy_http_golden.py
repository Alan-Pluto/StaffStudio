from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_golden.harness import GoldenHarness
from agent_golden.support import CanonicalNormalizer, assert_json_join


def test_gt01_sync_uses_real_http_auth_database_and_history(
    golden_harness: GoldenHarness,
) -> None:
    capture = golden_harness.post_sync(
        golden_harness.turn_payload("你好，请介绍一下自己。", client_turn_id="client-gt01-sync")
    )

    assert capture.status_code == 200
    assert capture.response is not None
    assert capture.response["reply"] == "这是 Golden 测试的稳定回复。"
    assert capture.session_id
    rows = golden_harness.database_rows(capture.session_id)
    history = golden_harness.history(capture.session_id)
    public_session = golden_harness.public_session(capture.session_id)

    _assert_message_event_joins(rows, "client-gt01-sync")
    assert [item["id"] for item in history] == [item["id"] for item in rows["messages"]]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[-1]["content"] == capture.response["reply"]
    assert public_session["id"] == capture.session_id


def test_gt01_sse_ids_are_durable_and_terminal_history_is_visible(
    golden_harness: GoldenHarness,
) -> None:
    capture = golden_harness.post_stream(
        golden_harness.turn_payload("你好，请流式回复。", client_turn_id="client-gt01-sse")
    )

    assert capture.status_code == 200
    assert capture.content_type.startswith("text/event-stream")
    assert capture.session_id
    assert capture.sse_events
    assert capture.sse_events[-1].event == "complete"
    assert "stream_delta" in [item.event for item in capture.sse_events]

    rows = golden_harness.database_rows(capture.session_id)
    history = golden_harness.history(capture.session_id)
    _assert_message_event_joins(rows, "client-gt01-sse")

    db_event_ids = {item["event_id"] for item in rows["events"]}
    durable_sse_ids = [item.id for item in capture.sse_events if item.id is not None]
    assert durable_sse_ids
    assert set(durable_sse_ids) <= db_event_ids
    assert len(durable_sse_ids) == len(set(durable_sse_ids))
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == capture.sse_events[-1].data["reply"]


def test_gt01_two_independent_runs_have_identical_canonical_planes(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_golden.scripted_dependencies import ScriptedLLMPlan

    captures: list[str] = []
    for index in range(5):
        harness = GoldenHarness(
            tmp_path / f"determinism-{index}.sqlite3",
            monkeypatch,
            ScriptedLLMPlan(),
        )
        try:
            capture = harness.post_stream(
                harness.turn_payload("稳定性测试", client_turn_id="client-determinism")
            )
            planes = _capture_planes(harness, capture, "稳定性测试", "client-determinism")
            captures.append(CanonicalNormalizer().dumps(planes))
        finally:
            harness.close()

    assert len(set(captures)) == 1


def test_gt01_feedback_survives_history_refresh_and_clear_is_durable(
    golden_harness: GoldenHarness,
) -> None:
    capture = golden_harness.post_sync(
        golden_harness.turn_payload("请回复后接受评价。", client_turn_id="client-feedback")
    )
    history = golden_harness.history(capture.session_id)
    assistant_message_id = history[-1]["id"]
    assert history[-1]["feedback_rating"] is None

    status, feedback = golden_harness.set_feedback(assistant_message_id, "up")
    assert status == 200
    assert feedback["message_id"] == assistant_message_id
    assert feedback["rating"] == "up"
    assert golden_harness.history(capture.session_id)[-1]["feedback_rating"] == "up"

    invalid_status, _ = golden_harness.set_feedback(assistant_message_id, "invalid")
    assert invalid_status == 422
    assert golden_harness.history(capture.session_id)[-1]["feedback_rating"] == "up"

    clear_status, clear_result = golden_harness.clear_feedback(assistant_message_id)
    assert clear_status == 200
    assert clear_result == {"status": "deleted"}
    assert golden_harness.history(capture.session_id)[-1]["feedback_rating"] is None

    changed_events = [
        item
        for item in golden_harness.database_rows(capture.session_id)["events"]
        if item["event_type"] == "message_feedback_changed"
    ]
    assert [item["payload"]["rating"] for item in changed_events] == ["up", None]
    assert all(item["payload"]["message_id"] == assistant_message_id for item in changed_events)


def test_gt16_attachment_upload_and_history_preserve_structured_metadata(
    golden_harness: GoldenHarness,
) -> None:
    attachments = golden_harness.upload_text_attachment(
        "golden-notes.txt", "第一行\n第二行".encode()
    )
    payload = golden_harness.turn_payload(
        "请总结附件。",
        client_turn_id="client-attachment",
    )
    payload["attachments"] = attachments
    capture = golden_harness.post_stream(payload)

    assert capture.sse_events[-1].event == "complete"
    history = golden_harness.history(capture.session_id)
    stored = history[0]["metadata"]["attachments"][0]
    assert stored == attachments[0]
    assert stored["filename"] == "golden-notes.txt"
    assert stored["kind"] == "text"
    assert "第一行" in stored["text"]


def test_gt13_llm_error_is_durable_visible_and_legacy_stream_closes_without_complete(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_golden.scripted_dependencies import ScriptedLLMPlan

    harness = GoldenHarness(
        tmp_path / "llm-error.sqlite3",
        monkeypatch,
        ScriptedLLMPlan(fail_phases={"Router"}),
    )
    try:
        repo_root = Path(__file__).resolve().parents[3]
        harness.publish_scene_skill(
            json.loads(
                (repo_root / "contracts/agent/v1/corpus/production_seed/purchase.json").read_text()
            )
        )
        capture = harness.post_stream(
            harness.turn_payload("触发模型异常", client_turn_id="client-llm-error")
        )
        assert capture.status_code == 200
        assert capture.session_id
        event_names = [item.event for item in capture.sse_events]
        assert event_names.count("error_occurred") == 1
        assert "complete" not in event_names
        assert event_names.index("error_occurred") < event_names.index("stream_end")
        assert event_names[-2:] == ["assistant_message_created", "session_state_changed"]

        rows = harness.database_rows(capture.session_id)
        errors = [item for item in rows["events"] if item["event_type"] == "error_occurred"]
        assert len(errors) == 1
        assert errors[0]["payload"]["code"] == "LLM_ERROR"
        assert errors[0]["payload"]["client_turn_id"] == "client-llm-error"
        assert "scripted failure at Router" in errors[0]["payload"]["message"]

        history = harness.history(capture.session_id)
        assert history[-1]["role"] == "assistant"
        streamed_reply = "".join(
            str(item.data.get("content") or "")
            for item in capture.sse_events
            if item.event == "stream_delta"
        )
        assert history[-1]["content"] == streamed_reply
        assert "模型调用失败" in history[-1]["content"]
    finally:
        harness.close()


def test_gt15_scheduled_draft_matches_realtime_event_and_refreshed_history(
    golden_harness: GoldenHarness,
    monkeypatch,
) -> None:
    from app.api import chat as chat_api
    from app.scheduled_tasks.schema import ScheduledTaskDraftRead

    initial = golden_harness.post_sync(
        golden_harness.turn_payload("先建立会话。", client_turn_id="client-draft-initial")
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
    monkeypatch.setattr(chat_api, "detect_scheduled_task_draft", lambda *_args, **_kwargs: draft)
    payload = golden_harness.turn_payload(
        "每天九点检查 A1 价格。",
        client_turn_id="client-draft",
        session_id=initial.session_id,
    )
    payload["interaction_mode"] = "scheduled_task"
    payload["client_timezone"] = "Asia/Shanghai"

    capture = golden_harness.post_stream(payload)

    assert capture.sse_events[-1].event == "complete"
    draft_events = [item for item in capture.sse_events if item.event == "scheduled_task_draft"]
    assert len(draft_events) == 1
    realtime_draft = draft_events[0].data
    history = golden_harness.history(initial.session_id)
    stored_draft = history[-1]["metadata"]["scheduled_task_draft"]
    assert stored_draft == draft.model_dump(mode="json")
    assert {key: realtime_draft[key] for key in stored_draft} == stored_draft

    rows = golden_harness.database_rows(initial.session_id)
    persisted = [
        item for item in rows["events"] if item["event_type"] == "scheduled_task_draft_created"
    ]
    assert len(persisted) == 1
    assert persisted[0]["payload"]["title"] == stored_draft["title"]
    assert history[-1]["turn_id"] == history[-2]["id"]

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
    invalid_status, _ = golden_harness.create_scheduled_task(create_payload)
    assert invalid_status == 400
    assert "scheduled_task_created" not in golden_harness.history(initial.session_id)[-1]["metadata"]

    create_payload["schedule"] = stored_draft["schedule"]
    created_status, created = golden_harness.create_scheduled_task(create_payload)
    assert created_status == 200
    assert created["source_session_id"] == initial.session_id
    refreshed = golden_harness.history(initial.session_id)
    assert refreshed[-1]["metadata"]["scheduled_task_draft"] == stored_draft
    assert refreshed[-1]["metadata"]["scheduled_task_created"]["id"] == created["id"]


def _capture_planes(
    harness: GoldenHarness,
    capture,
    message: str,
    client_turn_id: str,
) -> dict[str, Any]:
    rows = harness.database_rows(capture.session_id)
    history = harness.history(capture.session_id)
    public_session = harness.public_session(capture.session_id)
    terminal = capture.sse_events[-1] if capture.sse_events else None
    response = capture.response or (terminal.data if terminal else None)
    return {
        "domain": {
            "request": {
                "tenant_id": "tenant_golden",
                "agent_id": "agent_golden",
                "message": message,
                "client_turn_id": client_turn_id,
            },
            "router_decision": response.get("router_decision") if response else None,
            "step_result": response.get("step_result") if response else None,
            "tool_result": response.get("tool_result") if response else None,
            "outcome": {"reply": response.get("reply"), "session_id": capture.session_id},
            "facts": [],
            "termination": terminal.event if terminal else "sync_response",
        },
        "sse": {
            "http": {
                "method": "POST",
                "path": "/api/chat/stream",
                "status": capture.status_code,
                "content_type": capture.content_type,
            },
            "events": [
                {"sequence": index, "id": item.id, "event": item.event, "data": item.data}
                for index, item in enumerate(capture.sse_events)
            ],
        },
        "db_events": {"events": rows["events"]},
        "conversation": {
            "sync_response": capture.response,
            "messages": history,
            "session": public_session,
            "interaction_checks": [
                {
                    "kind": "none",
                    "realtime_observation": "not_observed",
                    "refresh_observation": "not_observed",
                    "action_result": None,
                }
            ],
        },
    }


def _assert_message_event_joins(rows: dict[str, Any], client_turn_id: str) -> None:
    messages = rows["messages"]
    events = rows["events"]
    user_index = next(
        index for index, item in enumerate(events) if item["event_type"] == "user_message_received"
    )
    assistant_index = next(
        index
        for index, item in enumerate(events)
        if item["event_type"] == "assistant_message_created"
    )
    documents = {"messages": messages, "events": events}

    assert_json_join(
        documents,
        [
            ("messages", "/0/id"),
            ("events", f"/{user_index}/payload/message_id"),
            ("events", f"/{user_index}/payload/turn_id"),
            ("events", f"/{user_index}/payload/user_message_id"),
        ],
    )
    assert_json_join(
        documents,
        [
            ("messages", "/1/id"),
            ("events", f"/{assistant_index}/payload/message_id"),
        ],
    )
    assert events[user_index]["payload"]["client_turn_id"] == client_turn_id
    assert messages[0]["id"] != client_turn_id
