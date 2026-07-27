from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_golden.support import (
    CanonicalNormalizer,
    DeterministicIdFactory,
    MonotonicClock,
    assert_contiguous_order,
    assert_duration_bounds,
    assert_json_join,
    assert_monotonic_timestamps,
    resolve_json_pointer,
)


def test_deterministic_id_factory_is_thread_safe_per_prefix() -> None:
    factory = DeterministicIdFactory()
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: factory.next("event"), range(100)))

    assert len(values) == len(set(values)) == 100
    assert sorted(values) == [f"event_{index:04d}" for index in range(1, 101)]
    assert factory.next("message") == "message_0001"


def test_deterministic_id_factory_producer_keys_ignore_thread_schedule() -> None:
    factory = DeterministicIdFactory()
    producer_keys = [f"worker-{index}" for index in range(100)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        forward = dict(
            zip(
                producer_keys,
                executor.map(lambda key: factory.for_key("event", key), producer_keys),
                strict=True,
            )
        )
        reverse_keys = list(reversed(producer_keys))
        reverse = dict(
            zip(
                reverse_keys,
                executor.map(lambda key: factory.for_key("event", key), reverse_keys),
                strict=True,
            )
        )
    assert forward == reverse


def test_monotonic_clock_is_strict_and_deterministic() -> None:
    clock = MonotonicClock(
        datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC),
        timedelta(milliseconds=10),
    )

    assert clock.now_iso() == "2025-01-02T03:04:05.000Z"
    assert clock.now_iso() == "2025-01-02T03:04:05.010Z"


def test_normalizer_preserves_joins_array_order_nulls_and_duplicates() -> None:
    raw = {
        "event_id": "evt-random",
        "nested": {
            "event_id": "evt-random",
            "skill_id": "skill_purchase",
            "resource_id": "chunk_policy",
            "created_at": "2026-07-26T03:35:43.123456Z",
            "duration_ms": 27,
            "cursor": "opaque:cursor",
            "nullable": None,
        },
        "items": ["second", "first", "first"],
    }

    normalized = CanonicalNormalizer().normalize(raw)

    assert normalized["event_id"] == normalized["nested"]["event_id"]
    assert normalized["nested"]["skill_id"] == "skill_purchase"
    assert normalized["nested"]["resource_id"] == "chunk_policy"
    assert normalized["nested"]["created_at"] == "<time:0001>"
    assert normalized["nested"]["duration_ms"] == "<duration_ms>"
    assert normalized["nested"]["cursor"] == "opaque:cursor"
    assert normalized["nested"]["nullable"] is None
    assert normalized["items"] == ["second", "first", "first"]

    rfc3339 = CanonicalNormalizer(rfc3339_timestamps=True).normalize(raw)
    assert rfc3339["nested"]["created_at"] == "2000-01-01T00:00:00.001Z"
    assert datetime.fromisoformat(rfc3339["nested"]["created_at"])


def test_json_pointer_and_cross_plane_join_assertions() -> None:
    documents = {
        "sse": {"events": [{"id": "evt-1"}]},
        "db": {"events": [{"event_id": "evt-1"}]},
    }

    assert resolve_json_pointer({"a/b": {"~key": 3}}, "/a~1b/~0key") == 3
    with pytest.raises(AssertionError, match="invalid array token"):
        resolve_json_pointer({"items": ["last"]}, "/items/-1")
    assert (
        assert_json_join(
            documents,
            [("sse", "/events/0/id"), ("db", "/events/0/event_id")],
        )
        == "evt-1"
    )

    documents["db"]["events"][0]["event_id"] = "evt-2"
    with pytest.raises(AssertionError, match="cross-plane join mismatch"):
        assert_json_join(
            documents,
            [("sse", "/events/0/id"), ("db", "/events/0/event_id")],
        )


def test_declared_normalization_profile_drives_precise_resource_id_paths() -> None:
    contract_root = Path(__file__).resolve().parents[3] / "contracts" / "agent" / "v1"
    raw = {
        "domain": {
            "request": {
                "session_id": "session-runtime",
                "source_session_id": "session-runtime",
                "attachments": [{"id": "file-runtime"}],
            }
        },
        "conversation": {
            "messages": [
                {
                    "id": "message-runtime",
                    "metadata": {"attachments": [{"id": "file-runtime"}]},
                }
            ],
            "interaction_checks": [
                {
                    "kind": "feedback",
                    "action_result": {
                        "resource_id": "message-runtime",
                        "persisted_state": {
                            "up_response": {
                                "id": "feedback-runtime",
                                "message_id": "message-runtime",
                            }
                        },
                    }
                },
                {
                    "kind": "scheduled_draft",
                    "action_result": {"resource_id": "scheduled-task-draft"},
                },
                {
                    "kind": "attachment",
                    "action_result": {"resource_id": "golden-notes.txt"},
                },
            ],
        },
        "unrelated": {"id": "business-stable-id"},
    }
    normalized = CanonicalNormalizer.from_profile(
        contract_root / "normalization-profiles.json",
        "agent-golden-v1",
    ).normalize(raw)

    request = normalized["domain"]["request"]
    assert request["session_id"] == request["source_session_id"]
    assert (
        request["attachments"][0]["id"]
        == normalized["conversation"]["messages"][0]["metadata"]["attachments"][0]["id"]
    )
    assert normalized["unrelated"]["id"] == "business-stable-id"
    action_result = normalized["conversation"]["interaction_checks"][0]["action_result"]
    assert action_result["resource_id"] == normalized["conversation"]["messages"][0]["id"]
    assert action_result["persisted_state"]["up_response"]["id"] != "feedback-runtime"
    interaction_checks = normalized["conversation"]["interaction_checks"]
    assert interaction_checks[1]["action_result"]["resource_id"] == "scheduled-task-draft"
    assert interaction_checks[2]["action_result"]["resource_id"] == "golden-notes.txt"


def test_order_time_and_duration_guards_reject_masked_regressions() -> None:
    rows = [
        {"sequence": 0, "created_at": "2025-01-01T00:00:00Z"},
        {"sequence": 1, "created_at": "2025-01-01T00:00:01Z"},
    ]
    assert_contiguous_order(rows, "sequence")
    assert_monotonic_timestamps(rows, "created_at")
    assert_duration_bounds(25, maximum=1000)

    with pytest.raises(AssertionError, match="contiguous"):
        assert_contiguous_order([rows[1], rows[0]], "sequence")
    with pytest.raises(AssertionError, match="monotonic"):
        assert_monotonic_timestamps([rows[1], rows[0]], "created_at")
    with pytest.raises(AssertionError, match="outside expected range"):
        assert_duration_bounds(-1, maximum=1000)


def test_canonical_bytes_change_for_order_null_and_duplicate_mutations() -> None:
    baseline = {"items": ["a", None, "b", "b"]}
    mutations = [
        {"items": ["b", None, "a", "b"]},
        {"items": ["a", "b", "b"]},
        {"items": ["a", None, "b"]},
    ]
    baseline_bytes = CanonicalNormalizer().dumps(baseline)
    assert all(CanonicalNormalizer().dumps(item) != baseline_bytes for item in mutations)


def test_timestamp_normalization_preserves_order_relations() -> None:
    forward = {
        "items": [
            {"created_at": "2025-01-01T00:00:00Z"},
            {"created_at": "2025-01-01T00:00:01Z"},
        ]
    }
    reverse = {"items": list(reversed(forward["items"]))}
    assert CanonicalNormalizer().dumps(forward) != CanonicalNormalizer().dumps(reverse)


def test_contextual_generated_ids_normalize_but_business_ids_do_not() -> None:
    raw = {
        "sse": {"events": [{"id": "evt-random"}]},
        "conversation": {
            "messages": [{"id": "msg-random", "resource_id": "chunk-stable"}],
            "session": {"id": "session-random", "active_skill_id": "skill-stable"},
        },
    }
    normalized = CanonicalNormalizer().normalize(raw)
    assert normalized["sse"]["events"][0]["id"] == "<id:0001>"
    assert normalized["conversation"]["messages"][0]["id"] == "<id:0002>"
    assert normalized["conversation"]["session"]["id"] == "<id:0003>"
    assert normalized["conversation"]["messages"][0]["resource_id"] == "chunk-stable"
    assert normalized["conversation"]["session"]["active_skill_id"] == "skill-stable"


def test_legacy_camel_case_runtime_fields_normalize_without_breaking_joins() -> None:
    raw = {
        "sse": {
            "sessionId": "session-random",
            "newSessionId": "session-random",
            "timestamp": "2026-07-26T03:35:43.123456Z",
        },
        "db": {
            "session_id": "session-random",
            "created_at": "2026-07-26T03:35:43.123456Z",
            "skill_id": "skill-stable",
            "resource_id": "resource-stable",
            "chunk_id": "chunk-stable",
        },
    }

    normalized = CanonicalNormalizer().normalize(raw)

    assert normalized["sse"]["sessionId"] == normalized["sse"]["newSessionId"]
    assert normalized["sse"]["sessionId"] == normalized["db"]["session_id"]
    assert normalized["sse"]["timestamp"] == normalized["db"]["created_at"]
    assert normalized["db"]["skill_id"] == "skill-stable"
    assert normalized["db"]["resource_id"] == "resource-stable"
    assert normalized["db"]["chunk_id"] == "chunk-stable"


def test_normalizer_stabilizes_repo_traceback_paths_and_lines() -> None:
    normalizer = CanonicalNormalizer(
        rules=[{"match": "**.error_traceback", "strategy": "traceback_normalized"}]
    )

    normalized = normalizer.normalize(
        {
            "error_traceback": (
                'Traceback:\n  File "/tmp/work/backend/app/core/agent_loop.py", '
                'line 1744, in handle_turn_stream\napp.llm.LLMError: failed\n'
            )
        }
    )

    assert normalized["error_traceback"] == (
        'Traceback:\n  File "<repo>/backend/app/core/agent_loop.py", '
        'line <line>, in handle_turn_stream\napp.llm.LLMError: failed\n'
    )
