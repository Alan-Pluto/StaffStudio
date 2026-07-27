from typing import ClassVar

from app.capabilities.contracts import CapabilityContext, KnowledgeSearchQuery
from app.capabilities.local_knowledge import LocalKnowledgeRuntime
from app.knowledge.schema import KnowledgeChunkRead, KnowledgeSearchResponse


class FakeKnowledgeService:
    calls: ClassVar[list[object]] = []

    def __init__(self, db: object) -> None:
        self.db = db

    def search(self, request: object, model_config: object) -> KnowledgeSearchResponse:
        self.calls.append((request, model_config))
        return KnowledgeSearchResponse(
            chunks=[
                KnowledgeChunkRead(
                    id="chunk-1",
                    tenant_id="tenant-1",
                    knowledge_base_id="kb-1",
                    document_id="doc-1",
                    bucket_id="bucket-1",
                    chunk_index=0,
                    content="报销上限是 1000 元。",
                    source_ref="doc-1#chunk-1",
                    metadata={"source": "policy"},
                    created_at="2026-07-27T00:00:00Z",
                    updated_at="2026-07-27T00:00:00Z",
                )
            ],
            trace=[{"phase": "local"}],
        )


def test_local_knowledge_adapter_preserves_service_owned_result() -> None:
    FakeKnowledgeService.calls = []
    runtime = LocalKnowledgeRuntime(FakeKnowledgeService, db=object(), model_config="model")
    result = runtime.search(
        CapabilityContext(
            request_id="req-1",
            tenant_id="tenant-1",
            agent_id="agent-1",
            user_id=None,
            session_id="session-1",
            turn_id="turn-1",
            channel="web",
        ),
        KnowledgeSearchQuery(query="报销上限"),
    )

    assert result.query_id == "req-1"
    assert result.items[0].source_ref == "doc-1#chunk-1"
    request, model_config = FakeKnowledgeService.calls[0]
    assert request.tenant_id == "tenant-1"
    assert request.query == "报销上限"
    assert model_config == "model"
    citation = runtime.resolve_citation(
        CapabilityContext(
            request_id="req-2",
            tenant_id="tenant-1",
            agent_id="agent-1",
            user_id=None,
            session_id="session-1",
            turn_id="turn-1",
            channel="web",
        ),
        "chunk-1",
    )
    assert citation.content == "报销上限是 1000 元。"
