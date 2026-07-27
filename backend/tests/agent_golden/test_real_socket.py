from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from agent_golden.harness import GoldenHarness
from agent_golden.scripted_dependencies import ScriptedLLMPlan
from app.core.agent_loop import AgentLoop

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_gt12_transport_disconnect_worker_finishes_and_history_recovers(
    tmp_path,
    monkeypatch,
) -> None:
    harness = GoldenHarness(
        tmp_path / "disconnect.sqlite3",
        monkeypatch,
        ScriptedLLMPlan(stream_chunks=tuple(f"chunk-{index} " for index in range(80))),
    )
    monkeypatch.setattr(AgentLoop, "_pace_stream", lambda *_args: time.sleep(0.01))
    try:
        with _live_server(harness) as base_url, httpx.Client(
            base_url=base_url,
            headers=harness.headers,
            timeout=10,
        ) as client:
            session_id = ""
            with client.stream(
                "POST",
                "/api/chat/stream",
                json=harness.turn_payload(
                    "真实连接断开测试",
                    client_turn_id="client-real-disconnect",
                ),
            ) as response:
                assert response.status_code == 200
                for event in _iter_sse(response):
                    session_id = str(event["data"].get("sessionId") or session_id)
                    if event["event"] == "stream_delta":
                        break

            assert session_id
            events = _poll_events(client, session_id, terminal={"complete"})
            assert any(item["event_type"] == "complete" for item in events)
            history = client.get(
                f"/api/chat/sessions/{session_id}/messages",
                params={"tenant_id": "tenant_golden"},
            )
            assert history.status_code == 200
            messages = history.json()
            assert [item["role"] for item in messages] == ["user", "assistant"]
            assert "chunk-79" in messages[-1]["content"]
    finally:
        harness.close()


def test_gt12_explicit_cancel_is_visible_on_stream_and_history(
    tmp_path,
    monkeypatch,
) -> None:
    harness = GoldenHarness(
        tmp_path / "cancel.sqlite3",
        monkeypatch,
        ScriptedLLMPlan(stream_chunks=tuple(f"chunk-{index} " for index in range(80))),
    )
    monkeypatch.setattr(AgentLoop, "_pace_stream", lambda *_args: time.sleep(0.01))
    try:
        with _live_server(harness) as base_url, httpx.Client(
            base_url=base_url,
            headers=harness.headers,
            timeout=10,
        ) as stream_client, httpx.Client(
            base_url=base_url,
            headers=harness.headers,
            timeout=10,
        ) as command_client:
            observed: list[str] = []
            session_id = ""
            turn_id = ""
            with stream_client.stream(
                "POST",
                "/api/chat/stream",
                json=harness.turn_payload(
                    "真实显式取消测试",
                    client_turn_id="client-real-cancel",
                ),
            ) as response:
                assert response.status_code == 200
                for event in _iter_sse(response):
                    observed.append(event["event"])
                    session_id = str(event["data"].get("sessionId") or session_id)
                    if event["event"] == "user_message_received":
                        turn_id = str(event["data"]["message_id"])
                        cancel = command_client.post(
                            f"/api/chat/sessions/{session_id}/cancel",
                            json={"tenant_id": "tenant_golden", "turn_id": turn_id},
                        )
                        assert cancel.status_code == 200
                    if event["event"] == "stream_cancelled":
                        break

            assert turn_id
            assert "stream_cancelled" in observed
            events = _poll_events(
                command_client,
                session_id,
                terminal={"stream_cancelled"},
            )
            assert sum(item["event_type"] == "stream_cancelled" for item in events) == 1
            history = command_client.get(
                f"/api/chat/sessions/{session_id}/messages",
                params={"tenant_id": "tenant_golden"},
            ).json()
            assert history[-1]["role"] == "assistant"
            assert history[-1]["content"] == "已停止生成"
    finally:
        harness.close()


def test_gt13_history_visibility_at_error_stream_end_and_assistant_event(
    tmp_path,
    monkeypatch,
) -> None:
    harness = GoldenHarness(
        tmp_path / "error-boundary.sqlite3",
        monkeypatch,
        ScriptedLLMPlan(fail_phases={"Router"}),
    )
    purchase = REPO_ROOT / "contracts/agent/v1/corpus/production_seed/purchase.json"
    harness.publish_scene_skill(json.loads(purchase.read_text(encoding="utf-8")))
    monkeypatch.setattr(AgentLoop, "_pace_stream", lambda *_args: time.sleep(0.02))
    try:
        with _live_server(harness) as base_url, httpx.Client(
            base_url=base_url,
            headers=harness.headers,
            timeout=10,
        ) as stream_client, httpx.Client(
            base_url=base_url,
            headers=harness.headers,
            timeout=10,
        ) as history_client:
            visibility: dict[str, list[str]] = {}
            observed: list[str] = []
            session_id = ""
            with stream_client.stream(
                "POST",
                "/api/chat/stream",
                json=harness.turn_payload(
                    "真实错误边界测试",
                    client_turn_id="client-real-error-boundary",
                ),
            ) as response:
                for event in _iter_sse(response):
                    name = event["event"]
                    observed.append(name)
                    session_id = str(event["data"].get("sessionId") or session_id)
                    if name in {
                        "error_occurred",
                        "stream_end",
                        "assistant_message_created",
                    }:
                        history = history_client.get(
                            f"/api/chat/sessions/{session_id}/messages",
                            params={"tenant_id": "tenant_golden"},
                        )
                        assert history.status_code == 200
                        visibility[name] = [item["role"] for item in history.json()]

            assert "complete" not in observed
            assert visibility["error_occurred"] == ["user"]
            assert visibility["stream_end"] == ["user", "assistant"]
            assert visibility["assistant_message_created"] == ["user", "assistant"]
    finally:
        harness.close()


@contextmanager
def _live_server(harness: GoldenHarness) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            harness.app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("loopback Uvicorn server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("loopback Uvicorn server did not stop")


def _iter_sse(response: httpx.Response) -> Iterator[dict[str, Any]]:
    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                data = json.loads("\n".join(data_lines))
                assert isinstance(data, dict)
                yield {"event": event_name, "data": data}
            event_name = "message"
            data_lines = []
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)


def _poll_events(
    client: httpx.Client,
    session_id: str,
    *,
    terminal: set[str],
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 8
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/chat/sessions/{session_id}/events",
            params={"tenant_id": "tenant_golden"},
        )
        assert response.status_code == 200, response.text
        events = response.json()
        if any(item["event_type"] in terminal for item in events):
            return events
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for terminal events {sorted(terminal)}")
