from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from agent_golden.scripted_dependencies import ScriptedLLMPlan, install_scripted_llm
from agent_golden.sse import SSEEvent, parse_sse_lines
from app.api import chat as chat_api
from app.api import scheduled_tasks as scheduled_tasks_api
from app.core.agent_loop import AgentLoop
from app.db import get_session
from app.db.models import (
    AgentEvent,
    AgentProfile,
    AgentResourceBinding,
    ChatSession,
    Message,
    ModelConfig,
    Skill,
    Tenant,
    User,
)
from app.security.auth import create_access_token

TENANT_ID = "tenant_golden"
USER_ID = "user_golden"
AGENT_ID = "agent_golden"
MODEL_ID = "model_golden"


@dataclass(frozen=True)
class HttpCapture:
    status_code: int
    content_type: str
    response: dict[str, Any] | None
    sse_events: list[SSEEvent]
    session_id: str


class GoldenHarness:
    def __init__(self, database_path: Path, monkeypatch: Any, plan: ScriptedLLMPlan) -> None:
        self.database_path = database_path
        self.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self._configure_sqlite()
        SQLModel.metadata.create_all(self.engine)
        self._seed()

        install_scripted_llm(monkeypatch, plan)
        monkeypatch.setattr(chat_api, "engine", self.engine)
        monkeypatch.setattr(chat_api, "_schedule_session_title_summary", lambda *_args: None)
        monkeypatch.setattr(chat_api, "enqueue_feedback_analysis", lambda *_args: None)
        monkeypatch.setattr(AgentLoop, "_enqueue_memory_capture", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(AgentLoop, "_pace_stream", lambda *_args: None)

        self.app = FastAPI()
        self.app.include_router(chat_api.router)
        self.app.include_router(scheduled_tasks_api.chat_router)
        self.app.dependency_overrides[get_session] = self._session_dependency
        self.client = TestClient(self.app)
        with Session(self.engine) as db:
            user = db.get(User, USER_ID)
            assert user is not None
            self.token = create_access_token(user)

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def turn_payload(
        self,
        message: str,
        *,
        client_turn_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tenant_id": TENANT_ID,
            "agent_id": AGENT_ID,
            "message": message,
            "client_turn_id": client_turn_id,
        }
        if session_id:
            payload["session_id"] = session_id
        return payload

    def post_sync(self, payload: dict[str, Any]) -> HttpCapture:
        response = self.client.post("/api/chat/turn", headers=self.headers, json=payload)
        body = response.json()
        assert isinstance(body, dict)
        return HttpCapture(
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            response=body,
            sse_events=[],
            session_id=str(body.get("session_id") or ""),
        )

    def post_stream(self, payload: dict[str, Any]) -> HttpCapture:
        with self.client.stream(
            "POST",
            "/api/chat/stream",
            headers=self.headers,
            json=payload,
        ) as response:
            events = parse_sse_lines(response.iter_lines())
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")
        session_id = next(
            (
                str(item.data.get("sessionId") or item.data.get("session_id") or "")
                for item in events
                if item.data.get("sessionId") or item.data.get("session_id")
            ),
            "",
        )
        return HttpCapture(
            status_code=status_code,
            content_type=content_type,
            response=None,
            sse_events=events,
            session_id=session_id,
        )

    def history(self, session_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/api/chat/sessions/{session_id}/messages",
            headers=self.headers,
            params={"tenant_id": TENANT_ID},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        return body

    def set_feedback(self, message_id: str, rating: str) -> tuple[int, dict[str, Any]]:
        response = self.client.post(
            f"/api/chat/messages/{message_id}/feedback",
            headers=self.headers,
            json={"tenant_id": TENANT_ID, "rating": rating},
        )
        body = response.json()
        assert isinstance(body, dict)
        return response.status_code, body

    def clear_feedback(self, message_id: str) -> tuple[int, dict[str, Any]]:
        response = self.client.delete(
            f"/api/chat/messages/{message_id}/feedback",
            headers=self.headers,
            params={"tenant_id": TENANT_ID},
        )
        body = response.json()
        assert isinstance(body, dict)
        return response.status_code, body

    def upload_text_attachment(self, filename: str, content: bytes) -> list[dict[str, Any]]:
        response = self.client.post(
            "/api/chat/attachments",
            headers=self.headers,
            params={"tenant_id": TENANT_ID},
            files={"files": (filename, content, "text/plain")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        return body

    def publish_scene_skill(self, content: dict[str, Any]) -> None:
        with Session(self.engine) as db:
            skill = Skill(
                tenant_id=TENANT_ID,
                skill_id=str(content["skill_id"]),
                version=str(content["version"]),
                name=str(content["name"]),
                description=content.get("description"),
                business_domain=content.get("business_domain"),
                content_json=content,
                status="published",
            )
            db.add(skill)
            db.flush()
            db.add(
                AgentResourceBinding(
                    tenant_id=TENANT_ID,
                    agent_id=AGENT_ID,
                    resource_type="skill",
                    resource_id=skill.id,
                    status="active",
                    metadata_json={
                        "scope": "agent_private",
                        "visibility": "agent_private",
                        "owner_agent_id": AGENT_ID,
                    },
                )
            )
            db.commit()

    def create_scheduled_task(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self.client.post(
            "/api/chat/scheduled-tasks",
            headers=self.headers,
            json=payload,
        )
        body = response.json()
        assert isinstance(body, dict)
        return response.status_code, body

    def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        response = self.client.get(
            "/api/chat/scheduled-tasks",
            headers=self.headers,
            params={"tenant_id": TENANT_ID, "agent_id": AGENT_ID},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        return body

    def public_session(self, session_id: str) -> dict[str, Any]:
        response = self.client.get(
            "/api/chat/sessions",
            headers=self.headers,
            params={"tenant_id": TENANT_ID},
        )
        assert response.status_code == 200, response.text
        return next(item for item in response.json() if item["id"] == session_id)

    def session_events(self, session_id: str) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/api/chat/sessions/{session_id}/events",
            headers=self.headers,
            params={"tenant_id": TENANT_ID},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, list)
        return body

    def database_rows(self, session_id: str) -> dict[str, Any]:
        with Session(self.engine) as db:
            session = db.get(ChatSession, session_id)
            assert session is not None
            messages = db.exec(
                select(Message)
                .where(Message.tenant_id == TENANT_ID, Message.session_id == session_id)
                .order_by(Message.created_at, Message.id)
            ).all()
            events = db.exec(
                select(AgentEvent)
                .where(AgentEvent.tenant_id == TENANT_ID, AgentEvent.session_id == session_id)
                .order_by(AgentEvent.created_at, AgentEvent.id)
            ).all()
            return {
                "session": _session_row(session),
                "messages": [_message_row(item) for item in messages],
                "events": [_event_row(index, item) for index, item in enumerate(events)],
            }

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine, "connect")
        def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def _session_dependency(self):
        with Session(self.engine) as db:
            yield db

    def _seed(self) -> None:
        with Session(self.engine) as db:
            db.add(Tenant(id=TENANT_ID, name="Golden Tenant"))
            db.add(
                User(
                    id=USER_ID,
                    tenant_id=TENANT_ID,
                    username="golden",
                    password_hash="unused",
                )
            )
            db.add(
                AgentProfile(
                    id=AGENT_ID,
                    tenant_id=TENANT_ID,
                    name="Golden Agent",
                    is_overall=False,
                    metadata_json={"owner_user_id": USER_ID},
                )
            )
            db.add(
                ModelConfig(
                    id=MODEL_ID,
                    tenant_id=TENANT_ID,
                    name="Golden Model",
                    api_key_encrypted="unused",
                    model="golden-model",
                    is_default=True,
                )
            )
            db.commit()


def _session_row(row: ChatSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "agent_id": row.agent_id,
        "status": row.status,
        "active_skill_id": row.active_skill_id,
        "active_step_id": row.active_step_id,
        "slots": row.slots_json,
        "awaiting_input": row.awaiting_input_json,
        "pending_tasks": row.pending_tasks_json,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _message_row(row: Message) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "role": row.role,
        "content": row.content,
        "metadata": row.metadata_json,
        "created_at": row.created_at.isoformat(),
    }


def _event_row(observed_row_order: int, row: AgentEvent) -> dict[str, Any]:
    return {
        "observed_row_order": observed_row_order,
        "event_id": row.id,
        "event_type": row.event_type,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat(),
        "payload": row.payload_json,
    }
