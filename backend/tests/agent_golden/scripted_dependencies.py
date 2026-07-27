from __future__ import annotations

import threading
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.llm import LLMError
from app.llm.stage_protocol import STAGE_PROTOCOL_KEY


@dataclass
class ScriptedLLMPlan:
    reply: str = "这是 Golden 测试的稳定回复。"
    stream_chunks: tuple[str, ...] = ("这是 Golden ", "测试的稳定回复。")
    fail_phases: set[str] = field(default_factory=set)
    json_by_phase: dict[str, dict[str, Any]] = field(default_factory=dict)
    json_by_phase_and_message: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    json_sequence_by_phase: dict[str, tuple[dict[str, Any], ...]] = field(
        default_factory=dict
    )


class ScriptedLLMClient:
    """Thread-visible deterministic LLM boundary used by real HTTP workers."""

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _plan: ClassVar[ScriptedLLMPlan] = ScriptedLLMPlan()
    _calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, model_config: object) -> None:
        self.model_config = model_config

    @classmethod
    def configure(cls, plan: ScriptedLLMPlan) -> None:
        with cls._lock:
            cls._plan = deepcopy(plan)
            cls._calls = []

    @classmethod
    def calls(cls) -> list[dict[str, Any]]:
        with cls._lock:
            return deepcopy(cls._calls)

    def generate_json(
        self, _system_prompt: str, payload: dict[str, Any], **_kwargs: Any
    ) -> dict[str, Any]:
        phase = self._phase(payload)
        self._record("json", phase, payload)
        self._raise_if_scripted(phase)
        by_message = self._plan.json_by_phase_and_message.get(phase, {})
        configured_for_message = by_message.get(str(payload.get("user_message") or ""))
        if configured_for_message is not None:
            return deepcopy(configured_for_message)
        sequence = self._plan.json_sequence_by_phase.get(phase)
        if sequence:
            phase_call_count = sum(
                1
                for call in self._calls
                if call["method"] == "json" and call["phase"] == phase
            )
            return deepcopy(sequence[min(phase_call_count - 1, len(sequence) - 1)])
        configured = self._plan.json_by_phase.get(phase)
        if configured is not None:
            return deepcopy(configured)
        if phase == "Router / General Skill Selector":
            return {
                "use_general_skill": False,
                "selected_slug": None,
                "use_knowledge": False,
                "knowledge_query": None,
                "confidence": 1.0,
                "reason": "No scripted capability selected.",
            }
        if phase == "Router":
            return {
                "decision": "answer_only",
                "confidence": 1.0,
                "reason": "Scripted plain chat.",
            }
        if phase == "Step Agent":
            return {
                "action": "reply",
                "reply": self._plan.reply,
                "slot_updates": {},
                "is_step_completed": True,
            }
        if phase == "Reflection":
            return {"action": "pass", "needs_retry": False, "reason": "Scripted pass."}
        raise AssertionError(f"unhandled scripted JSON phase: {phase!r}")

    def generate_text(self, _system_prompt: str, payload: dict[str, Any], **_kwargs: Any) -> str:
        phase = self._phase(payload)
        self._record("text", phase, payload)
        self._raise_if_scripted(phase)
        return self._plan.reply

    def generate_text_stream(
        self,
        _system_prompt: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Iterator[str]:
        phase = self._phase(payload)
        self._record("stream", phase, payload)
        self._raise_if_scripted(phase)
        yield from self._plan.stream_chunks

    @classmethod
    def _raise_if_scripted(cls, phase: str) -> None:
        if phase in cls._plan.fail_phases:
            raise LLMError(f"scripted failure at {phase}")

    @classmethod
    def _record(cls, method: str, phase: str, payload: dict[str, Any]) -> None:
        with cls._lock:
            cls._calls.append({"method": method, "phase": phase, "payload": deepcopy(payload)})

    @staticmethod
    def _phase(payload: dict[str, Any]) -> str:
        stage = payload.get(STAGE_PROTOCOL_KEY)
        if not isinstance(stage, dict):
            return "unscoped"
        return str(stage.get("phase") or "unscoped")


PATCH_TARGETS = (
    "app.api.chat.LLMClient",
    "app.core.agent_loop.LLMClient",
    "app.core.reflection_agent.LLMClient",
    "app.core.response_generator.LLMClient",
    "app.core.router.LLMClient",
    "app.core.step_agent.LLMClient",
    "app.general_skills.runner.LLMClient",
    "app.knowledge.service.LLMClient",
)


def install_scripted_llm(monkeypatch: Any, plan: ScriptedLLMPlan) -> None:
    ScriptedLLMClient.configure(plan)
    for target in PATCH_TARGETS:
        monkeypatch.setattr(target, ScriptedLLMClient)
