from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.reflection_agent import ReflectionDecision
from app.db.models import ChatSession, ModelConfig, Skill, Tool
from app.session.session_schema import ChatTurnRequest, RouterDecision, StepAgentResult
from app.tools.tool_schema import ToolCall, ToolResult

EventRecorder = Callable[[str, str, str, dict[str, Any]], None]


class LegacyReflectionCoordinator:
    @staticmethod
    def run_rounds(
        request: ChatTurnRequest,
        chat_session: ChatSession,
        skills: list[Skill],
        tools: list[Tool],
        model_config: ModelConfig,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        max_rounds: int,
        conversation_context: dict[str, object] | None,
        stream_events: list[tuple[str, dict[str, object]]] | None,
        completed_skill_ids_this_turn: set[str] | None,
        memory_context: list[dict[str, object]] | None,
        *,
        round_limit: int,
        context_loader: Callable[[ChatSession], dict[str, object]],
        should_try: Callable[[RouterDecision, StepAgentResult, ToolResult | None], bool],
        reflect_and_retry: Callable[..., tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None, bool]],
        record_event: EventRecorder | None,
    ) -> tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None]:
        if conversation_context is None:
            conversation_context = context_loader(chat_session)
        completed_skill_ids_this_turn = completed_skill_ids_this_turn or set()
        rounds = max(0, min(max_rounds, round_limit))
        if rounds <= 0:
            if should_try(router_decision, step_result, tool_result):
                payload = {
                    "needs_retry": False,
                    "reason": "企业端反思轮数配置为 0，已跳过反思。",
                    "target_skill_id": None,
                    "target_step_id": None,
                    "target_tool_name": None,
                    "skipped": True,
                    "skip_reason": "reflection_disabled",
                }
                if record_event is not None:
                    record_event(
                        request.tenant_id,
                        chat_session.id,
                        "reflection_skipped",
                        payload,
                    )
                if stream_events is not None:
                    stream_events.append(("reflection_decision", payload))
            return active_skill, router_decision, step_result, tool_result

        for round_index in range(rounds):
            if not should_try(router_decision, step_result, tool_result):
                break
            if stream_events is not None and round_index > 0:
                stream_events.append(
                    (
                        "status",
                        {
                            "phase": "reflecting",
                            "text": "正在反思",
                            "reflection_round": round_index + 1,
                            "reflection_max_rounds": rounds,
                        },
                    )
                )
            (
                active_skill,
                router_decision,
                step_result,
                tool_result,
                retried,
            ) = reflect_and_retry(
                request,
                chat_session,
                skills,
                tools,
                model_config,
                active_skill,
                router_decision,
                step_result,
                tool_result,
                conversation_context,
                stream_events,
                completed_skill_ids_this_turn,
                memory_context,
            )
            if not retried:
                break
        return active_skill, router_decision, step_result, tool_result

    @staticmethod
    def reflect_and_retry(
        request: ChatTurnRequest,
        chat_session: ChatSession,
        skills: list[Skill],
        tools: list[Tool],
        model_config: ModelConfig,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        conversation_context: dict[str, object] | None,
        stream_events: list[tuple[str, dict[str, object]]] | None,
        completed_skill_ids_this_turn: set[str] | None,
        memory_context: list[dict[str, object]] | None,
        *,
        context_loader: Callable[[ChatSession], dict[str, object]],
        should_try: Callable[[RouterDecision, StepAgentResult, ToolResult | None], bool],
        review: Callable[..., ReflectionDecision],
        llm_error_type: type[Exception],
        record_event: EventRecorder,
        tool_call_from_reflection: Callable[..., ToolCall | None],
        tool_retry_targets_current_skill: Callable[[ReflectionDecision, ChatSession], bool],
        retry_with_tool_call: Callable[..., tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None]],
        router_decision_from_reflection: Callable[..., RouterDecision | None],
        retry_with_router_decision: Callable[..., tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None]],
    ) -> tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None, bool]:
        if conversation_context is None:
            conversation_context = context_loader(chat_session)
        completed_skill_ids_this_turn = completed_skill_ids_this_turn or set()
        if not should_try(router_decision, step_result, tool_result):
            return active_skill, router_decision, step_result, tool_result, False

        try:
            reflection = review(
                request.message,
                chat_session,
                active_skill,
                router_decision,
                step_result,
                tool_result,
                skills,
                tools,
                model_config,
                conversation_context,
                memory_context,
            )
        except Exception as exc:
            if not isinstance(exc, llm_error_type):
                raise
            record_event(
                request.tenant_id,
                chat_session.id,
                "reflection_error",
                {"message": str(exc)},
            )
            if stream_events is not None:
                stream_events.append(
                    (
                        "reflection_decision",
                        {
                            "needs_retry": False,
                            "reason": f"反思失败：{exc}",
                            "target_skill_id": None,
                            "target_step_id": None,
                            "target_tool_name": None,
                        },
                    )
                )
            return active_skill, router_decision, step_result, tool_result, False

        record_event(
            request.tenant_id,
            chat_session.id,
            "reflection_decision_created",
            reflection.model_dump(),
        )
        if stream_events is not None:
            stream_events.append(
                ("reflection_decision", reflection.model_dump(mode="json"))
            )
        if not reflection.needs_retry:
            return active_skill, router_decision, step_result, tool_result, False

        retry_tool_call = tool_call_from_reflection(
            reflection,
            chat_session,
            tools,
            request.message,
        )
        if retry_tool_call and tool_retry_targets_current_skill(
            reflection, chat_session
        ):
            retry_result = retry_with_tool_call(
                request,
                chat_session,
                active_skill,
                router_decision,
                retry_tool_call,
                reflection.reason,
                stream_events,
                tools,
                model_config,
                conversation_context,
                memory_context,
            )
            return (*retry_result, True)

        retry_router_decision = router_decision_from_reflection(
            reflection,
            chat_session,
            skills,
            router_decision,
            completed_skill_ids_this_turn,
        )
        if retry_router_decision:
            retry_result = retry_with_router_decision(
                request,
                chat_session,
                skills,
                tools,
                retry_router_decision,
                model_config,
                conversation_context,
                stream_events,
                memory_context,
            )
            return (*retry_result, True)

        if retry_tool_call:
            retry_result = retry_with_tool_call(
                request,
                chat_session,
                active_skill,
                router_decision,
                retry_tool_call,
                reflection.reason,
                stream_events,
                tools,
                model_config,
                conversation_context,
                memory_context,
            )
            return (*retry_result, True)

        record_event(
            request.tenant_id,
            chat_session.id,
            "reflection_retry_skipped",
            {
                "reason": reflection.reason,
                "target_skill_id": reflection.target_skill_id,
                "target_tool_name": reflection.target_tool_name,
            },
        )
        return active_skill, router_decision, step_result, tool_result, False

    @staticmethod
    def retry_with_tool_call(
        request: ChatTurnRequest,
        chat_session: ChatSession,
        active_skill: Skill | None,
        router_decision: RouterDecision,
        retry_tool_call: ToolCall,
        retry_reason: str | None,
        stream_events: list[tuple[str, dict[str, object]]] | None,
        tools: list[Tool] | None,
        model_config: ModelConfig | None,
        conversation_context: dict[str, object] | None,
        memory_context: list[dict[str, object]] | None,
        *,
        record_event: EventRecorder,
        execute_tool_cycle: Callable[..., tuple[StepAgentResult, ToolResult | None]],
    ) -> tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None]:
        retry_step_result = StepAgentResult(
            tool_call=retry_tool_call,
            next_step_id=chat_session.active_step_id,
            is_step_completed=True,
        )
        record_event(
            request.tenant_id,
            chat_session.id,
            "reflection_retry_started",
            {
                "mode": "tool",
                "reason": retry_reason,
                "target_tool_name": retry_tool_call.name,
            },
        )
        retry_step_result, retry_tool_result = execute_tool_cycle(
            request,
            chat_session,
            active_skill,
            tools or [],
            model_config,
            retry_step_result,
            stream_events,
            conversation_context=conversation_context,
            memory_context=memory_context,
        )
        return active_skill, router_decision, retry_step_result, retry_tool_result

    @staticmethod
    def retry_with_router_decision(
        request: ChatTurnRequest,
        chat_session: ChatSession,
        skills: list[Skill],
        tools: list[Tool],
        router_decision: RouterDecision,
        model_config: ModelConfig,
        conversation_context: dict[str, object],
        stream_events: list[tuple[str, dict[str, object]]] | None,
        memory_context: list[dict[str, object]] | None,
        *,
        record_event: EventRecorder,
        apply_runtime_decision: Callable[[ChatSession, RouterDecision], None],
        drop_unavailable_state: Callable[[str, ChatSession, list[Skill]], bool],
        should_record_runtime_event: Callable[[RouterDecision, ChatSession, list[Skill], bool], bool],
        record_runtime_event: Callable[..., None],
        commit: Callable[[], None],
        refresh: Callable[[ChatSession], None],
        get_active_skill: Callable[..., Skill | None],
        skill_state_payload: Callable[..., dict[str, object]],
        runtime_stream_context: Callable[..., dict[str, object]],
        run_step_with_context_repair: Callable[..., StepAgentResult],
        execute_knowledge_cycle: Callable[..., StepAgentResult],
        execute_tool_cycle: Callable[..., tuple[StepAgentResult, ToolResult | None]],
    ) -> tuple[Skill | None, RouterDecision, StepAgentResult, ToolResult | None]:
        record_event(
            request.tenant_id,
            chat_session.id,
            "reflection_retry_started",
            {
                "mode": "skill",
                "target_skill_id": router_decision.target_skill_id,
                "target_step_id": router_decision.target_step_id,
                "reason": router_decision.reason,
            },
        )
        record_event(
            request.tenant_id,
            chat_session.id,
            "router_decision_created",
            router_decision.model_dump(),
        )

        before_skill = chat_session.active_skill_id
        before_step = chat_session.active_step_id
        apply_runtime_decision(chat_session, router_decision)
        state_pruned = drop_unavailable_state(
            request.tenant_id, chat_session, skills
        )
        if should_record_runtime_event(
            router_decision, chat_session, skills, state_pruned
        ):
            record_runtime_event(
                request.tenant_id,
                chat_session,
                before_skill,
                before_step,
                router_decision,
            )
        commit()
        refresh(chat_session)

        active_skill = get_active_skill(
            request.tenant_id,
            chat_session.active_skill_id,
            chat_session.agent_id,
        )
        if stream_events is not None:
            stream_events.append(
                (
                    "skill_state",
                    skill_state_payload(
                        chat_session,
                        skills,
                        runtime_stream_context(
                            router_decision,
                            before_skill,
                            before_step,
                            chat_session,
                        ),
                    ),
                )
            )
            stream_events.append(
                (
                    "status",
                    {
                        "phase": "stepping",
                        "text": "正在思考",
                        "active_skill_id": chat_session.active_skill_id,
                        "active_step_id": chat_session.active_step_id,
                    },
                )
            )

        step_result = run_step_with_context_repair(
            request,
            chat_session,
            active_skill,
            tools,
            model_config,
            router_decision,
            memory_context=memory_context,
            conversation_context=conversation_context,
            stream_events=stream_events,
        )
        commit()
        refresh(chat_session)

        tool_result: ToolResult | None = None
        if step_result.knowledge_query:
            step_result = execute_knowledge_cycle(
                request,
                chat_session,
                active_skill,
                tools,
                model_config,
                step_result,
                memory_context,
                conversation_context,
                stream_events,
            )
            commit()
            refresh(chat_session)
        if step_result.tool_call:
            step_result, tool_result = execute_tool_cycle(
                request,
                chat_session,
                active_skill,
                tools,
                model_config,
                step_result,
                stream_events,
                conversation_context=conversation_context,
                memory_context=memory_context,
            )
        return active_skill, router_decision, step_result, tool_result


class LegacyReflectionPolicy:
    @staticmethod
    def retry_targets_current_skill(
        reflection: ReflectionDecision, chat_session: ChatSession
    ) -> bool:
        return bool(
            reflection.target_tool_name
            and (
                not reflection.target_skill_id
                or reflection.target_skill_id == chat_session.active_skill_id
            )
        )

    @staticmethod
    def router_decision(
        reflection: ReflectionDecision,
        chat_session: ChatSession,
        skills: list[Skill],
        previous_decision: RouterDecision,
        completed_skill_ids_this_turn: set[str] | None,
        *,
        record_event: EventRecorder,
        first_step_id: Callable[[Skill], str | None],
    ) -> RouterDecision | None:
        if not reflection.target_skill_id:
            return None
        completed_skill_ids_this_turn = completed_skill_ids_this_turn or set()
        if (
            reflection.target_skill_id in completed_skill_ids_this_turn
            and chat_session.active_skill_id != reflection.target_skill_id
        ):
            record_event(
                chat_session.tenant_id,
                chat_session.id,
                "reflection_retry_skipped_completed_task",
                {
                    "reason": reflection.reason,
                    "target_skill_id": reflection.target_skill_id,
                    "active_skill_id": chat_session.active_skill_id,
                },
            )
            return None
        target_skill = next(
            (skill for skill in skills if skill.skill_id == reflection.target_skill_id),
            None,
        )
        if not target_skill:
            return None
        decision = (
            "continue_active"
            if chat_session.active_skill_id == target_skill.skill_id
            else "start_new_task"
        )
        return RouterDecision(
            decision=decision,
            target_skill_id=target_skill.skill_id,
            target_step_id=reflection.target_step_id or first_step_id(target_skill),
            confidence=0.7,
            user_intent=previous_decision.user_intent,
            reason=f"反思重试：{reflection.reason or '当前技能或工具可能不匹配用户诉求'}",
        )

    @staticmethod
    def tool_call(
        reflection: ReflectionDecision,
        chat_session: ChatSession,
        tools: list[Tool],
        user_message: str | None,
        *,
        general_skill_tool_prefix: str,
        build_arguments: Callable[[Tool, dict[str, Any]], dict[str, Any]],
        slot_has_value: Callable[[dict[str, Any], str], bool],
    ) -> ToolCall | None:
        if not reflection.target_tool_name:
            return None
        tool = next(
            (
                item
                for item in tools
                if item.enabled and item.name == reflection.target_tool_name
            ),
            None,
        )
        if not tool:
            return None
        if str(getattr(tool, "name", "") or "").startswith(
            general_skill_tool_prefix
        ):
            query = str(user_message or "").strip()
            if not query:
                return None
            return ToolCall(name=tool.name, arguments={"query": query})
        if (
            chat_session.active_skill_id
            and tool.allowed_skills_json
            and chat_session.active_skill_id not in tool.allowed_skills_json
        ):
            return None
        arguments = build_arguments(tool, chat_session.slots_json or {})
        required = [
            str(field) for field in (tool.input_schema or {}).get("required", [])
        ]
        if any(not slot_has_value(arguments, field) for field in required):
            return None
        return ToolCall(name=tool.name, arguments=arguments)

    @staticmethod
    def should_try(
        router_decision: RouterDecision,
        step_result: StepAgentResult,
        tool_result: ToolResult | None,
        *,
        predicate: Callable[[RouterDecision, StepAgentResult, ToolResult | None], bool],
    ) -> bool:
        return predicate(router_decision, step_result, tool_result)
