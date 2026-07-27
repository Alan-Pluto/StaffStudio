import pytest

from app.core.legacy_general_skill_action import LegacyGeneralSkillAction
from app.db.models import ChatSession, GeneralSkill, ModelConfig
from app.general_skills.schema import GeneralSkillRunResponse
from app.session.session_schema import ChatTurnRequest
from app.tools.tool_schema import ToolCall, ToolResult


class Events:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, dict[str, object]]] = []

    def record(
        self, tenant_id: str, session_id: str, event_type: str, payload: dict[str, object]
    ) -> None:
        self.records.append((tenant_id, session_id, event_type, payload))


def _request() -> ChatTurnRequest:
    return ChatTurnRequest(tenant_id="tenant", user_id="user", message="run it")


def _session() -> ChatSession:
    return ChatSession(id="session", tenant_id="tenant")


def _skill() -> GeneralSkill:
    return GeneralSkill(
        tenant_id="tenant",
        slug="weather",
        name="Weather",
        version="1.0.0",
        status="published",
    )


def _model() -> ModelConfig:
    return ModelConfig(tenant_id="tenant", name="model", model="test")


def _execute(
    *,
    tool_name: str = "general_skill.weather",
    skills: list[GeneralSkill] | None = None,
    model_resolver=None,
    runner=None,
) -> ToolResult:
    return LegacyGeneralSkillAction(Events()).execute_tool_call(
        _request(),
        _session(),
        ToolCall(name=tool_name, arguments={"query": "weather"}),
        None,
        None,
        None,
        None,
        tool_prefix="general_skill.",
        list_skills=lambda tenant_id, agent_id: skills if skills is not None else [_skill()],
        model_resolver=model_resolver or (lambda request, agent_id: _model()),
        precondition_error_type=ValueError,
        validator=lambda *args: None,
        runner=runner
        or (
            lambda *args, **kwargs: GeneralSkillRunResponse(
                skill_slug="weather", reply="ok", structured_result={"success": True}
            )
        ),
    )


@pytest.mark.parametrize(
    ("tool_name", "skills", "code"),
    [
        ("general_skill.", [_skill()], "INVALID_GENERAL_SKILL"),
        ("general_skill.missing", [], "GENERAL_SKILL_NOT_FOUND"),
    ],
)
def test_general_skill_lookup_failures(
    tool_name: str, skills: list[GeneralSkill], code: str
) -> None:
    result = _execute(tool_name=tool_name, skills=skills)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == code


def test_general_skill_model_precondition_is_preserved() -> None:
    error = ValueError("disabled")
    error.code = "disabled_model"  # type: ignore[attr-defined]
    error.message = "模型已停用"  # type: ignore[attr-defined]

    result = _execute(model_resolver=lambda request, agent_id: (_ for _ in ()).throw(error))

    assert result.error is not None
    assert result.error.code == "DISABLED_MODEL"
    assert result.error.message == "模型已停用"


def test_general_skill_runner_exception_isolated() -> None:
    result = _execute(
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broken"))
    )

    assert result.error is not None
    assert result.error.code == "GENERAL_SKILL_EXECUTION_ERROR"
    assert result.error.message == "broken"


def test_general_skill_structured_failure_keeps_data() -> None:
    result = _execute(
        runner=lambda *args, **kwargs: GeneralSkillRunResponse(
            skill_slug="weather",
            reply="failed",
            stderr="details",
            structured_result={"success": False, "error": "REMOTE_FAILED"},
        )
    )

    assert result.success is False
    assert result.data is not None
    assert result.error is not None
    assert result.error.code == "REMOTE_FAILED"
    assert result.error.message == "failed"
