from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from agent_golden.contract_validation import ExpectedFixture, expected_fixture_matrix
from agent_golden.harness import GoldenHarness
from agent_golden.legacy_scenario_capture import (
    LegacyScenarioCapture,
    execute_legacy_variant,
)
from agent_golden.support import CanonicalNormalizer, load_json


def capture_gt01_legacy_envelopes(
    contract_root: Path,
    harness: GoldenHarness,
    variant_id: str,
) -> dict[str, dict[str, Any]]:
    if variant_id not in {"GT01-sync", "GT01-sse"}:
        raise ValueError(f"unsupported GT01 fixture variant: {variant_id!r}")
    return capture_legacy_envelopes(contract_root, harness, variant_id)


def capture_legacy_envelopes(
    contract_root: Path,
    harness: GoldenHarness,
    variant_id: str,
    monkeypatch: Any | None = None,
    *,
    captured_revision: str | None = None,
) -> dict[str, dict[str, Any]]:
    scenario = execute_legacy_variant(contract_root.parents[2], harness, variant_id, monkeypatch)

    raw_planes = _raw_planes(harness, scenario)
    planes = CanonicalNormalizer.from_profile(
        contract_root / "normalization-profiles.json",
        "agent-golden-v1",
        rfc3339_timestamps=True,
    ).normalize(raw_planes)
    relationship_indices = _relationship_indices(planes, scenario.client_turn_id)
    matrix = expected_fixture_matrix(contract_root)
    expected = {
        item.plane: item
        for item in matrix.values()
        if item.fixture_set == "legacy_characterization" and item.variant_id == variant_id
    }
    return {
        plane: _envelope(
            contract_root,
            item,
            planes.get(plane),
            relationship_indices,
            captured_revision or _repo_revision(contract_root),
        )
        for plane, item in expected.items()
    }


def write_envelopes(envelopes: dict[str, dict[str, Any]], expected: dict[str, Path]) -> None:
    for plane, envelope in envelopes.items():
        path = expected[plane]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _raw_planes(
    harness: GoldenHarness,
    scenario: LegacyScenarioCapture,
) -> dict[str, Any]:
    capture = scenario.http
    rows = harness.database_rows(capture.session_id)
    history = harness.history(capture.session_id)
    public_session = harness.public_session(capture.session_id)
    response = capture.response or next(
        (item.data for item in capture.sse_events if item.event == "complete"),
        {},
    )
    user_event = _unique_matching_event(
        rows["events"],
        lambda item: (
            item["event_type"] == "user_message_received"
            and item["payload"].get("client_turn_id") == scenario.client_turn_id
        ),
        f"DB user event for {scenario.client_turn_id}",
    )
    user_message_id = user_event["payload"]["message_id"]
    assistant_event = _unique_matching_event(
        rows["events"],
        lambda item: (
            item["event_type"] == "assistant_message_created"
            and (
                item["payload"].get("client_turn_id") == scenario.client_turn_id
                or item["payload"].get("turn_id") == user_message_id
            )
        ),
        f"DB assistant event for {scenario.client_turn_id}",
    )
    assistant_message_id = assistant_event["payload"]["message_id"]
    assistant_message = next(item for item in history if item["id"] == assistant_message_id)
    event_names = [item.event for item in capture.sse_events]
    if not event_names:
        termination = "sync_response"
    elif "complete" in event_names:
        termination = "complete"
    elif "error_occurred" in event_names:
        termination = "legacy_error_then_clean_close"
    else:
        termination = event_names[-1]
    return {
        "domain": {
            "request": {
                "tenant_id": "tenant_golden",
                "agent_id": "agent_golden",
                "message": scenario.message,
                "client_turn_id": scenario.client_turn_id,
                "turn_id": user_message_id,
                **scenario.request_extra,
            },
            "router_decision": response.get("router_decision"),
            "step_result": response.get("step_result"),
            "tool_result": response.get("tool_result"),
            "outcome": {
                "reply": response.get("reply") or assistant_message["content"],
                "session_id": capture.session_id,
                "assistant_message_id": assistant_message_id,
            },
            "facts": scenario.facts,
            "termination": termination,
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
            "interaction_checks": scenario.interaction_checks,
        },
    }


def _envelope(
    contract_root: Path,
    expected: ExpectedFixture,
    payload: dict[str, Any] | None,
    relationship_indices: dict[str, int],
    captured_revision: str,
) -> dict[str, Any]:
    required = expected.applicability == "required"
    observation_fields = set(
        load_json(contract_root / "field-ownership.json")["legacy_field_observation"][
            "expectations"
        ]
    )
    source_paths = (
        [
            "backend/tests/agent_golden/fixture_writer.py",
            "backend/tests/agent_golden/legacy_scenario_capture.py",
            "backend/tests/agent_golden/harness.py",
            "backend/tests/agent_golden/support.py",
            "backend/tests/agent_golden/scripted_dependencies.py",
            "contracts/agent/v1/normalization-profiles.json",
            "contracts/agent/v1/scenario-catalog.json",
        ]
        if required
        else ["contracts/agent/v1/scenario-catalog.json"]
    )
    source = {
        "kind": "captured_runtime" if required else "explicit_manifest",
        "repo_revision": captured_revision,
        "artifacts": [
            {
                "path": path,
                "sha256": f"sha256:{hashlib.sha256((contract_root.parents[2] / path).read_bytes()).hexdigest()}",
            }
            for path in source_paths
        ],
        "capture_harness": "agent_golden.fixture_writer.capture_legacy_envelopes"
        if required
        else None,
    }
    envelope: dict[str, Any] = {
        "schema_version": "1.0",
        "fixture_set": expected.fixture_set,
        "scenario_id": expected.scenario_id,
        "variant_id": expected.variant_id,
        "plane": expected.plane,
        "applicability": expected.applicability,
        "payload_schema": _payload_schema(expected.plane) if required else None,
        "normalization_profile": "agent-golden-v1",
        "source": source,
        "legacy_field_observation": {
            field: "present" if required and _contains_key(payload, field) else "absent"
            for field in sorted(observation_fields)
        },
        "content_hash": _payload_hash(payload) if required else None,
        "joins": [],
        "happens_before": [],
    }
    if required:
        assert payload is not None
        envelope["payload"] = payload
        _add_relationships(
            envelope,
            expected.variant_id,
            expected.plane,
            relationship_indices,
        )
    else:
        envelope["omission"] = {
            "reason": f"{expected.plane} is {expected.applicability} for {expected.variant_id}."
        }
    return envelope


def _add_relationships(
    envelope: dict[str, Any],
    variant_id: str,
    plane: str,
    indices: dict[str, Any],
) -> None:
    if plane == "domain":
        envelope["joins"] = [
            {
                "name": "user-turn-message-identity",
                "rule_id": "legacy.turn_message_identity",
                "references": [
                    {
                        "role": "domain_turn_id",
                        "plane": "domain",
                        "pointer": "/request/turn_id",
                    },
                    {
                        "role": "db_user_message_id",
                        "plane": "db_events",
                        "pointer": f"/events/{indices['db_user']}/payload/message_id",
                    },
                    {
                        "role": "conversation_user_message_id",
                        "plane": "conversation",
                        "pointer": f"/messages/{indices['conversation_user']}/id",
                    },
                ],
            },
            {
                "name": "assistant-message-identity",
                "rule_id": "legacy.turn_message_identity",
                "references": [
                    {
                        "role": "domain_assistant_message_id",
                        "plane": "domain",
                        "pointer": "/outcome/assistant_message_id",
                    },
                    {
                        "role": "db_assistant_message_id",
                        "plane": "db_events",
                        "pointer": f"/events/{indices['db_assistant']}/payload/message_id",
                    },
                    {
                        "role": "conversation_assistant_message_id",
                        "plane": "conversation",
                        "pointer": f"/messages/{indices['conversation_assistant']}/id",
                    },
                ],
            },
        ]
        if variant_id == "GT16-history":
            envelope["joins"].append(
                {
                    "name": "attachment-upload-request-history-identity",
                    "rule_id": "legacy.attachment_identity",
                    "references": [
                        {
                            "role": "domain_request_attachment_id",
                            "plane": "domain",
                            "pointer": "/request/attachments/0/id",
                        },
                        {
                            "role": "conversation_message_attachment_id",
                            "plane": "conversation",
                            "pointer": (
                                f"/messages/{indices['conversation_user']}"
                                "/metadata/attachments/0/id"
                            ),
                        },
                        {
                            "role": "action_persisted_attachment_id",
                            "plane": "conversation",
                            "pointer": (
                                "/interaction_checks/0/action_result/persisted_state/attachment/id"
                            ),
                        },
                    ],
                }
            )
    if plane == "db_events":
        envelope["happens_before"] = [
            {
                "name": "user-event-before-assistant-event",
                "rule_id": "legacy.observed_event_order",
                "order_type": "integer",
                "before": {
                    "role": "db_user_event_order",
                    "plane": "db_events",
                    "pointer": f"/events/{indices['db_user']}/observed_row_order",
                },
                "after": {
                    "role": "db_assistant_event_order",
                    "plane": "db_events",
                    "pointer": f"/events/{indices['db_assistant']}/observed_row_order",
                },
            }
        ]
    if plane == "conversation" and variant_id == "GT01-feedback-refresh-toggle":
        envelope["joins"] = [
            {
                "name": "feedback-target-message-identity",
                "rule_id": "legacy.feedback_target_identity",
                "references": [
                    {
                        "role": "conversation_assistant_message_id",
                        "plane": "conversation",
                        "pointer": f"/messages/{indices['conversation_assistant']}/id",
                    },
                    {
                        "role": "action_feedback_target_id",
                        "plane": "conversation",
                        "pointer": "/interaction_checks/0/action_result/resource_id",
                    },
                    {
                        "role": "feedback_response_message_id",
                        "plane": "conversation",
                        "pointer": (
                            "/interaction_checks/0/action_result/persisted_state/"
                            "up_response/message_id"
                        ),
                    },
                ],
            }
        ]
    if plane == "sse":
        occurrences: dict[str, int] = {}
        joins = []
        for sse_index, db_index in indices.get("durable_sse_db", {}).items():
            event_name = indices["sse_event_names"][sse_index].replace("_", "-")
            occurrence = occurrences.get(event_name, 0) + 1
            occurrences[event_name] = occurrence
            suffix = "" if occurrence == 1 else f"-{occurrence}"
            joins.append(
                {
                    "name": f"durable-{event_name}-event{suffix}-identity",
                    "rule_id": "legacy.durable_event_identity",
                    "references": [
                        {
                            "role": "sse_event_id",
                            "plane": "sse",
                            "pointer": f"/events/{sse_index}/id",
                        },
                        {
                            "role": "db_event_id",
                            "plane": "db_events",
                            "pointer": f"/events/{db_index}/event_id",
                        },
                    ],
                }
            )
        envelope["joins"] = joins
    if plane == "sse" and "sse_complete" in indices:
        envelope["happens_before"] = [
            {
                "name": "user-event-before-complete",
                "rule_id": "legacy.observed_event_order",
                "order_type": "integer",
                "before": {
                    "role": "sse_user_event_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_user']}/sequence",
                },
                "after": {
                    "role": "sse_complete_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_complete']}/sequence",
                },
            }
        ]
    if plane == "sse" and variant_id == "GT13-llm-error":
        envelope["happens_before"] = [
            {
                "name": "error-before-stream-end",
                "rule_id": "legacy.observed_event_order",
                "order_type": "integer",
                "before": {
                    "role": "sse_error_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_error']}/sequence",
                },
                "after": {
                    "role": "sse_stream_end_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_stream_end']}/sequence",
                },
            },
            {
                "name": "stream-end-before-assistant-event",
                "rule_id": "legacy.observed_event_order",
                "order_type": "integer",
                "before": {
                    "role": "sse_stream_end_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_stream_end']}/sequence",
                },
                "after": {
                    "role": "sse_assistant_event_sequence",
                    "plane": "sse",
                    "pointer": f"/events/{indices['sse_assistant']}/sequence",
                },
            },
        ]


def _relationship_indices(planes: dict[str, Any], client_turn_id: str) -> dict[str, Any]:
    db_events = planes["db_events"]["events"]
    db_user = _unique_matching_index(
        db_events,
        lambda event: (
            event.get("event_type") == "user_message_received"
            and event.get("payload", {}).get("client_turn_id") == client_turn_id
        ),
        "DB user event",
    )
    turn_id = db_events[db_user]["payload"]["message_id"]
    db_assistant = _unique_matching_index(
        db_events,
        lambda event: (
            event.get("event_type") == "assistant_message_created"
            and (
                event.get("payload", {}).get("client_turn_id") == client_turn_id
                or event.get("payload", {}).get("turn_id") == turn_id
            )
        ),
        "DB assistant event",
    )
    assistant_id = db_events[db_assistant]["payload"]["message_id"]
    messages = planes["conversation"]["messages"]
    result = {
        "db_user": db_user,
        "db_assistant": db_assistant,
        "conversation_user": _unique_matching_index(
            messages, lambda message: message.get("id") == turn_id, "conversation user message"
        ),
        "conversation_assistant": _unique_matching_index(
            messages,
            lambda message: message.get("id") == assistant_id,
            "conversation assistant message",
        ),
    }
    sse_events = planes["sse"]["events"]
    if sse_events:
        result["sse_event_names"] = [event["event"] for event in sse_events]
        result["durable_sse_db"] = {
            index: _unique_matching_index(
                db_events,
                lambda db_event, event_id=event["id"]: db_event.get("event_id") == event_id,
                f"DB event for SSE event {index}",
            )
            for index, event in enumerate(sse_events)
            if event.get("id") is not None
        }
        result["sse_user"] = _unique_matching_index(
            sse_events,
            lambda event: (
                event.get("event") == "user_message_received"
                and event.get("data", {}).get("client_turn_id") == client_turn_id
            ),
            "SSE user event",
        )
        complete = [
            index for index, event in enumerate(sse_events) if event.get("event") == "complete"
        ]
        if complete:
            if len(complete) != 1:
                raise AssertionError(f"expected at most one SSE complete event: {complete}")
            result["sse_complete"] = complete[0]
        if any(event.get("event") == "error_occurred" for event in sse_events):
            for key, event_name in (
                ("sse_error", "error_occurred"),
                ("sse_stream_end", "stream_end"),
                ("sse_assistant", "assistant_message_created"),
            ):
                result[key] = _unique_matching_index(
                    sse_events,
                    lambda event, expected=event_name: event.get("event") == expected,
                    f"SSE {event_name} event",
                )
    return result


def _unique_matching_index(items: list[dict[str, Any]], predicate: Any, label: str) -> int:
    matches = [index for index, item in enumerate(items) if predicate(item)]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {label}, found {len(matches)}")
    return matches[0]


def _unique_matching_event(
    events: list[dict[str, Any]], predicate: Any, label: str
) -> dict[str, Any]:
    return events[_unique_matching_index(events, predicate, label)]


def _payload_schema(plane: str) -> str:
    return {
        "provider": "planes/legacy-provider-exchange.schema.json",
        "domain": "planes/domain.schema.json",
        "sse": "planes/sse.schema.json",
        "db_events": "planes/db-events.schema.json",
        "conversation": "planes/conversation.schema.json",
    }[plane]


def _payload_hash(payload: Any) -> str:
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _contains_key(value: Any, field: str) -> bool:
    if isinstance(value, dict):
        return field in value or any(_contains_key(item, field) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, field) for item in value)
    return False


def _repo_revision(contract_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=contract_root.parents[2], text=True
    ).strip()
