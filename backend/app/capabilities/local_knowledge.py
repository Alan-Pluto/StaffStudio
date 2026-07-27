from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.capabilities.contracts import (
    CapabilityContext,
    CitationDetail,
    KnowledgeHit,
    KnowledgeRuntime,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)
from app.knowledge.schema import KnowledgeSearchRequest, KnowledgeSearchResponse


class LocalKnowledgeRuntime(KnowledgeRuntime):
    """Adapter for the existing local service; it has no AgentLoop dependency."""

    provider_id = "local_knowledge"

    def __init__(
        self,
        service_factory: Callable[[Any], Any],
        db: Any,
        model_config: Any | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._db = db
        self._model_config = model_config
        self._citation_index: dict[str, CitationDetail] = {}

    def search(
        self, context: CapabilityContext, request: KnowledgeSearchQuery
    ) -> KnowledgeSearchResult:
        legacy_request = KnowledgeSearchRequest(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            query=request.query,
            query_type=request.query_type
            if request.query_type in {"answer", "policy_check", "tool_discovery", "skill_discovery"}
            else "answer",
            scope=dict(request.scope),
            max_chunks=request.max_chunks,
            budget_tokens=request.budget_tokens,
            mode="chat",
        )
        response = self._service_factory(self._db).search(legacy_request, self._model_config)
        if not isinstance(response, KnowledgeSearchResponse):
            raise TypeError("local Knowledge provider returned an invalid response")
        items = tuple(
            KnowledgeHit(
                hit_id=str(chunk.id),
                content=chunk.content,
                source_ref=chunk.source_ref,
                metadata=dict(chunk.metadata or {}),
            )
            for chunk in response.chunks
        )
        self._citation_index.update(
            {
                item.hit_id: CitationDetail(
                    citation_id=item.hit_id,
                    title=None,
                    content=item.content,
                    source_ref=item.source_ref,
                )
                for item in items
            }
        )
        return KnowledgeSearchResult(
            query_id=context.request_id,
            items=items,
            extensions={"local_knowledge": {"trace": response.trace}},
        )

    def resolve_citation(
        self, context: CapabilityContext, provider_citation_ref: str
    ) -> CitationDetail:
        citation = self._citation_index.get(provider_citation_ref)
        if citation is None:
            raise LookupError(f"local citation is not resolvable: {provider_citation_ref}")
        return citation
