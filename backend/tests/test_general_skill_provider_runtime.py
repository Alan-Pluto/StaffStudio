from dataclasses import FrozenInstanceError, replace

import pytest

from app.capabilities.local_general_skill import (
    GeneralSkillRuntimeSnapshot,
    package_from_row,
)
from app.core.agent_loop import AgentLoop, AgentLoopPreconditionError
from app.db.models import ChatSession, GeneralSkill
from app.session.session_schema import ChatTurnRequest


class RecordingCatalog:
    provider_id = "recording_catalog"

    def __init__(self, package):
        self.package = package
        self.calls = []

    def get_package(self, context, resource_ref):
        self.calls.append((context, resource_ref))
        return self.package


def _skill() -> GeneralSkill:
    return GeneralSkill(
        id="genskill_weather",
        tenant_id="tenant_demo",
        slug="weather",
        name="天气",
        description="查询天气",
        skill_markdown="# Weather",
        skill_files_json=[
            {
                "path": "SKILL.md",
                "content": "# Weather",
                "size": 9,
                "mime_type": "text/markdown",
            }
        ],
        status="published",
    )


def test_agent_loop_loads_provider_content_into_one_run_snapshot() -> None:
    skill = _skill()
    catalog = RecordingCatalog(package_from_row(skill))
    loop = AgentLoop.__new__(AgentLoop)
    loop.general_skill_catalog = catalog
    session = ChatSession(
        id="session_01",
        tenant_id="tenant_demo",
        user_id="user_01",
        agent_id="agent_01",
    )
    request = ChatTurnRequest(
        tenant_id="tenant_demo",
        session_id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        client_turn_id="turn_01",
        channel="feishu",
        message="北京天气",
    )

    snapshot = loop._general_skill_runtime_snapshot(request, session, skill)

    assert isinstance(snapshot, GeneralSkillRuntimeSnapshot)
    context, resource_ref = catalog.calls[0]
    assert (
        context.tenant_id,
        context.agent_id,
        context.user_id,
        context.session_id,
        context.turn_id,
        context.channel,
    ) == (
        "tenant_demo",
        "agent_01",
        "user_01",
        "session_01",
        "turn_01",
        "feishu",
    )
    assert resource_ref.catalog_binding_id == catalog.provider_id

    skill.skill_files_json[0]["content"] = "changed after load"
    assert snapshot.skill_files_json[0]["content"] == "# Weather"
    with pytest.raises(FrozenInstanceError):
        snapshot.skill_markdown = "changed"  # type: ignore[misc]


def test_provider_package_pin_rejects_content_drift() -> None:
    skill = _skill()
    first = package_from_row(skill)
    skill.skill_markdown = "# Changed"
    second = package_from_row(skill)

    assert first.digest != second.digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("package_id", "genskill_other"),
        ("version", "other-version"),
        ("digest", "sha256:other"),
        ("package_contract_version", "other-contract"),
    ],
)
def test_agent_loop_rejects_provider_package_that_does_not_match_pin(
    field: str,
    value: str,
) -> None:
    skill = _skill()
    package = replace(package_from_row(skill), **{field: value})
    loop = AgentLoop.__new__(AgentLoop)
    loop.general_skill_catalog = RecordingCatalog(package)
    session = ChatSession(
        id="session_01",
        tenant_id="tenant_demo",
        user_id="user_01",
        agent_id="agent_01",
    )
    request = ChatTurnRequest(
        tenant_id="tenant_demo",
        session_id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        client_turn_id="turn_01",
        channel="web",
        message="北京天气",
    )

    with pytest.raises(AgentLoopPreconditionError) as exc_info:
        loop._general_skill_runtime_snapshot(request, session, skill)

    assert exc_info.value.code == "general_skill_content_unavailable"
