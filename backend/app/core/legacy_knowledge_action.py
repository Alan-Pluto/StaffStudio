from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from sqlmodel import Session

from app.db.models import ChatSession, ModelConfig, Skill, Tool
from app.general_skills.schema import GeneralSkillSelection
from app.knowledge.schema import KnowledgeSearchRequest, KnowledgeSearchResponse
from app.session.session_schema import (
    ChatTurnRequest,
    KnowledgeQuery,
    RouterDecision,
    StepAgentResult,
)

StatusCallback = Callable[[str, dict[str, object]], None]
ScopeIds = Callable[[dict[str, Any], str, str], list[str]]

_STEPS_SEEN: ContextVar[set[tuple[str, str]] | None] = ContextVar(
    "knowledge_steps_seen", default=None
)
_RESULTS_CACHE: ContextVar[dict[tuple[str, str], dict[str, Any]] | None] = ContextVar(
    "knowledge_results_cache", default=None
)
KNOWLEDGE_STEPS_SEEN = _STEPS_SEEN
KNOWLEDGE_RESULTS_CACHE = _RESULTS_CACHE


class LegacyKnowledgeAction:
    def __init__(self, db: Session | None, events: Any = None) -> None:
        self.db = db
        self.events = events

    @staticmethod
    def reset_turn() -> None:
        _STEPS_SEEN.set(set())
        _RESULTS_CACHE.set({})

    def execute_cycle(
        self,
        request: ChatTurnRequest,
        chat_session: ChatSession,
        active_skill: Skill | None,
        tools: list[Tool],
        model_config: ModelConfig,
        step_result: StepAgentResult,
        memory_context: list[dict[str, object]] | None,
        conversation_context: dict[str, object] | None,
        stream_events: list[tuple[str, dict[str, object]]] | None,
        status_callback: StatusCallback | None,
        *,
        key_resolver: Callable[[ChatSession, Skill | None], tuple[str, str]],
        query_claimer: Callable[[ChatSession, Skill | None], bool],
        visible_knowledge_base_ids: Callable[[str, str | None], list[str]],
        requires_resource_filter: Callable[[str, str | None], bool],
        continue_after_query: Callable[..., StepAgentResult],
        scope_ids: ScopeIds,
        knowledge_service_factory: Callable[[Session], Any],
    ) -> StepAgentResult:
        query = step_result.knowledge_query
        if not query or not query.query.strip():
            return step_result
        knowledge_key = key_resolver(chat_session, active_skill)
        if not query_claimer(chat_session, active_skill):
            cached = (_RESULTS_CACHE.get() or {}).get(knowledge_key)
            if cached is None:
                return step_result
            return continue_after_query(
                request,
                chat_session,
                active_skill,
                tools,
                model_config,
                cached,
                memory_context,
                conversation_context,
            )
        query_scope = dict(query.scope or {})
        runtime_forced = bool(query_scope.pop("_runtime_forced", False))
        payload = {
            "phase": "knowledge",
            "text": "正在检索知识",
            "query": query.model_dump(mode="json"),
            "active_skill_id": chat_session.active_skill_id,
            "active_step_id": chat_session.active_step_id,
        }
        self.events.record(request.tenant_id, chat_session.id, "knowledge_query_started", payload)
        if stream_events is not None:
            stream_events.append(("status", payload))
        if status_callback is not None:
            status_callback("knowledge", payload)

        try:
            knowledge_base_ids = visible_knowledge_base_ids(
                request.tenant_id, chat_session.agent_id
            )
            scoped_ids = scope_ids(
                query_scope, "knowledge_base_ids", "knowledge_base_id"
            )
            if scoped_ids:
                knowledge_base_ids = [
                    item for item in knowledge_base_ids if item in scoped_ids
                ]
            if requires_resource_filter(
                request.tenant_id, chat_session.agent_id
            ) and not knowledge_base_ids:
                search_response = KnowledgeSearchResponse(
                    selected_buckets=[],
                    chunks=[],
                    trace=[],
                    route_trace=[],
                    selected_documents=[],
                    expanded_sections=[],
                    evidence_pack=[],
                )
            else:
                search_query = query.query.strip()
                original_message = request.message.strip()
                if not runtime_forced and original_message and original_message not in search_query:
                    search_query = f"{search_query}\n{original_message}"
                if self.db is None:
                    raise RuntimeError("Knowledge search requires a database session")
                search_response = knowledge_service_factory(self.db).search(
                    KnowledgeSearchRequest(
                        tenant_id=request.tenant_id,
                        agent_id=chat_session.agent_id,
                        query=search_query,
                        query_type=query.query_type,
                        desired_evidence=query.desired_evidence,
                        scope=query_scope,
                        mode="chat",
                        knowledge_base_ids=knowledge_base_ids,
                        knowledge_base_version_ids=scope_ids(
                            query_scope,
                            "knowledge_base_version_ids",
                            "knowledge_base_version_id",
                        ),
                        document_ids=scope_ids(
                            query_scope, "document_ids", "document_id"
                        ),
                        max_chunks=max(1, min(query.max_chunks, 12)),
                        max_buckets=4,
                        max_depth=max(1, min(query.max_depth, 4)),
                        need_evidence_pack=True,
                    ),
                    model_config,
                )
        except Exception as exc:
            failed_payload = {
                "phase": "knowledge",
                "active_skill_id": chat_session.active_skill_id,
                "active_step_id": chat_session.active_step_id,
                "error_type": type(exc).__name__,
            }
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "knowledge_query_failed",
                failed_payload,
            )
            if stream_events is not None:
                stream_events.append(("knowledge_query_failed", failed_payload))
            raise
        knowledge_items = self.response_items(
            query, request.message, search_response, runtime_forced=runtime_forced
        )
        cache = _RESULTS_CACHE.get()
        if cache is None:
            cache = {}
            _RESULTS_CACHE.set(cache)
        cache[knowledge_key] = knowledge_items
        self.events.record(
            request.tenant_id,
            chat_session.id,
            "knowledge_query_finished",
            knowledge_items,
        )
        if stream_events is not None:
            for trace in search_response.route_trace or search_response.trace:
                stream_events.append(("status", {"phase": "knowledge", **trace}))
            stream_events.append(("knowledge_result", knowledge_items))
        return continue_after_query(
            request,
            chat_session,
            active_skill,
            tools,
            model_config,
            knowledge_items,
            memory_context,
            conversation_context,
        )

    def continue_after_query(
        self,
        request: ChatTurnRequest,
        chat_session: ChatSession,
        active_skill: Skill | None,
        tools: list[Tool],
        model_config: ModelConfig,
        knowledge_items: dict[str, Any],
        memory_context: list[dict[str, object]] | None,
        conversation_context: dict[str, object] | None,
        *,
        step_runner: Callable[..., StepAgentResult],
        result_applier: Callable[..., None],
    ) -> StepAgentResult:
        result = step_runner(
            request,
            chat_session,
            active_skill,
            tools,
            model_config,
            repair_reason="knowledge_continuation",
            repair_context={
                "reason": "knowledge_continuation",
                "knowledge_results": knowledge_items,
                "instruction": "基于知识结果继续判断下一步动作；如果知识足够，推进、调用工具或回复；如果不足，由模型决定是否继续追问或停止。",
            },
            memory_context=memory_context,
            conversation_context=conversation_context,
            current_knowledge=[knowledge_items],
            allow_general_skill_selection=False,
        )
        result.knowledge_results = [knowledge_items]
        result_applier(request.tenant_id, chat_session, result, active_skill)
        return result

    def auto_step_result(
        self,
        request: ChatTurnRequest,
        chat_session: ChatSession,
        model_config: ModelConfig,
        router_decision: RouterDecision,
        selection: GeneralSkillSelection | None,
        stream_events: list[tuple[str, dict[str, object]]] | None,
        status_callback: StatusCallback | None,
        *,
        items_loader: Callable[..., dict[str, Any] | None],
    ) -> StepAgentResult:
        del router_decision
        if selection is None or not selection.use_knowledge:
            return StepAgentResult()
        query = KnowledgeQuery(
            query=(selection.knowledge_query or request.message).strip(),
            reason=selection.reason or "第二轮能力选择判断需要企业知识",
            max_chunks=8,
            max_depth=3,
        )
        payload = {
            "phase": "knowledge",
            "text": "正在检索业务资料",
            "query": query.model_dump(mode="json"),
            "auto": True,
        }
        self.events.record(request.tenant_id, chat_session.id, "knowledge_query_started", payload)
        if stream_events is not None:
            stream_events.append(("status", payload))
        if status_callback is not None:
            status_callback("knowledge", payload)
        knowledge_items = items_loader(
            request.tenant_id,
            chat_session.agent_id,
            request.message,
            query,
            model_config,
        )
        finished_payload = knowledge_items or self.empty_items(query, request.message)
        self.events.record(
            request.tenant_id,
            chat_session.id,
            "knowledge_query_finished",
            {**finished_payload, "auto": True},
        )
        if stream_events is not None:
            for trace in finished_payload.get("trace") or []:
                stream_events.append(("status", {"phase": "knowledge", **trace}))
            stream_events.append(("knowledge_result", finished_payload))
        return StepAgentResult(
            knowledge_query=query,
            knowledge_results=[knowledge_items] if knowledge_items else [],
        )

    def items_for_message(
        self,
        tenant_id: str,
        agent_id: str,
        message: str,
        query: KnowledgeQuery | None,
        model_config: ModelConfig | None,
        *,
        visible_knowledge_base_ids: Callable[[str, str | None], list[str]],
        requires_resource_filter: Callable[[str, str | None], bool],
        scope_ids: ScopeIds,
        knowledge_service_factory: Callable[[Session], Any],
    ) -> dict[str, Any] | None:
        knowledge_base_ids = visible_knowledge_base_ids(tenant_id, agent_id)
        query_scope = dict((query.scope if query else {}) or {})
        query_scope.pop("_runtime_forced", None)
        scoped_ids = scope_ids(query_scope, "knowledge_base_ids", "knowledge_base_id")
        if scoped_ids:
            knowledge_base_ids = [item for item in knowledge_base_ids if item in scoped_ids]
        if requires_resource_filter(tenant_id, agent_id) and not knowledge_base_ids:
            return None
        knowledge_query = query or KnowledgeQuery(
            query=message,
            reason="用户要求基于业务资料或规则回答",
            max_chunks=8,
            max_depth=3,
        )
        if self.db is None:
            raise RuntimeError("Knowledge search requires a database session")
        response = knowledge_service_factory(self.db).search(
            KnowledgeSearchRequest(
                tenant_id=tenant_id,
                agent_id=agent_id,
                query=knowledge_query.query.strip() or message,
                query_type=knowledge_query.query_type,
                desired_evidence=knowledge_query.desired_evidence,
                scope=query_scope,
                mode="chat",
                knowledge_base_ids=knowledge_base_ids,
                knowledge_base_version_ids=scope_ids(
                    query_scope,
                    "knowledge_base_version_ids",
                    "knowledge_base_version_id",
                ),
                document_ids=scope_ids(query_scope, "document_ids", "document_id"),
                max_chunks=8,
                max_buckets=4,
                max_depth=3,
                need_evidence_pack=True,
            ),
            model_config,
        )
        if not (
            response.selected_concepts
            or response.okf_citations
            or response.evidence_pack
            or response.chunks
        ):
            return None
        return self.response_items(knowledge_query, message, response)

    def normalize_required_step(
        self,
        request: ChatTurnRequest,
        chat_session: ChatSession,
        active_skill: Skill | None,
        step_result: StepAgentResult,
        *,
        current_step: Callable[[Skill, str | None], dict[str, Any] | None],
        slot_satisfied: Callable[[dict[str, Any], str], bool],
    ) -> StepAgentResult:
        if not active_skill:
            return step_result
        step = current_step(active_skill, chat_session.active_step_id)
        if not step or str(step.get("type") or "") != "knowledge_query":
            return step_result
        effective_slots = {**(chat_session.slots_json or {}), **(step_result.slot_updates or {})}
        missing_fields = [
            str(field)
            for field in step.get("expected_user_info", [])
            if not slot_satisfied(effective_slots, str(field))
        ]
        if missing_fields:
            step_result.action = "ask_user" if step_result.reply else "clarify"
            step_result.knowledge_query = None
            step_result.tool_call = None
            step_result.next_step_id = None
            step_result.knowledge_results = []
            step_result.is_step_completed = False
            step_result.handoff = False
            return step_result
        if step_result.knowledge_query is None:
            scope = dict(step.get("knowledge_scope") or {})
            query_fields = scope.pop("query_fields", [])
            query_parts = [
                str(step.get("name") or "").strip(),
                str(step.get("instruction") or "").strip(),
            ]
            if isinstance(query_fields, list):
                for field in query_fields:
                    field_name = str(field).strip()
                    if field_name and slot_satisfied(effective_slots, field_name):
                        query_parts.append(f"{field_name}: {effective_slots[field_name]}")
            scope["_runtime_forced"] = True
            step_result.knowledge_query = KnowledgeQuery(
                query="\n".join(part for part in query_parts if part),
                reason="当前 SOP 节点要求检索知识",
                scope=scope,
                query_type="policy_check",
                max_chunks=8,
                max_depth=3,
            )
            self.events.record(
                request.tenant_id,
                chat_session.id,
                "knowledge_query_forced",
                {
                    "skill_id": active_skill.skill_id,
                    "step_id": chat_session.active_step_id,
                    "reason": "required_knowledge_node_missing_query",
                },
            )
        step_result.action = "query_knowledge"
        step_result.reply = None
        step_result.tool_call = None
        step_result.next_step_id = None
        step_result.knowledge_results = []
        step_result.is_step_completed = False
        step_result.handoff = False
        return step_result

    @staticmethod
    def claim_query(chat_session: ChatSession, active_skill: Skill | None) -> bool:
        if not active_skill or not chat_session.active_step_id:
            return True
        seen = _STEPS_SEEN.get()
        if seen is None:
            seen = set()
            _STEPS_SEEN.set(seen)
        key = (active_skill.skill_id, chat_session.active_step_id)
        if key in seen:
            return False
        seen.add(key)
        return True

    @staticmethod
    def step_key(chat_session: ChatSession, active_skill: Skill | None) -> tuple[str, str]:
        return (
            active_skill.skill_id if active_skill else "",
            str(chat_session.active_step_id or ""),
        )

    @staticmethod
    def response_items(
        query: KnowledgeQuery,
        source_message: str,
        response: KnowledgeSearchResponse,
        *,
        runtime_forced: bool = False,
    ) -> dict[str, Any]:
        return {
            "query": query.model_dump(mode="json"),
            "source_message": None if runtime_forced else source_message,
            "selected_buckets": [
                item.model_dump(mode="json") for item in response.selected_buckets
            ],
            "chunks": [item.model_dump(mode="json") for item in response.chunks],
            "trace": response.route_trace or response.trace,
            "selected_documents": response.selected_documents,
            "selected_concepts": response.selected_concepts,
            "expanded_sections": response.expanded_sections,
            "okf_citations": response.okf_citations,
            "evidence_pack": response.evidence_pack,
        }

    @staticmethod
    def empty_items(query: KnowledgeQuery, source_message: str) -> dict[str, Any]:
        return {
            "query": query.model_dump(mode="json"),
            "source_message": source_message,
            "selected_buckets": [],
            "chunks": [],
            "trace": [],
            "selected_documents": [],
            "selected_concepts": [],
            "expanded_sections": [],
            "okf_citations": [],
            "evidence_pack": [],
        }
