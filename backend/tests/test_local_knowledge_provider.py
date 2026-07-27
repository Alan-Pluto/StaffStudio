from typing import ClassVar

from app.capabilities.contracts import CapabilityContext, KnowledgeSearchQuery
from app.capabilities.errors import CapabilityProviderError
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


def test_local_knowledge_scope_listing_is_tenant_and_agent_scoped(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.capabilities.local_knowledge.visible_knowledge_base_versions",
        lambda db, tenant_id, agent_id: {
            "kb-1": type(
                "Version",
                (),
                {"name": "Policies", "version": "2.0.0", "metadata_json": {"owner": agent_id}},
            )()
        },
    )
    runtime = LocalKnowledgeRuntime(FakeKnowledgeService, db=object(), model_config=None)
    scopes = runtime.list_scopes(
        CapabilityContext(
            request_id="req-1",
            tenant_id="tenant-1",
            agent_id="agent-1",
            user_id=None,
            session_id="session-1",
            turn_id="turn-1",
            channel="web",
        )
    )
    assert scopes[0].scope_id == "kb-1"
    assert scopes[0].metadata["owner"] == "agent-1"


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

    assert result.query_id.startswith("kquery_")
    assert result.items[0].source_ref == "doc-1#chunk-1"
    assert result.extensions["local_knowledge"]["request_id"] == "req-1"
    request, model_config = FakeKnowledgeService.calls[0]
    assert request.tenant_id == "tenant-1"
    assert request.query == "报销上限"
    assert model_config == "model"
    try:
        runtime.resolve_citation(
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
    except CapabilityProviderError as exc:
        assert exc.info.code == "KNOWLEDGE_CITATION_NOT_DURABLE"
    else:  # pragma: no cover - the adapter must never expose an in-memory citation
        raise AssertionError("non-durable citation unexpectedly resolved")


def test_local_knowledge_adapter_rejects_unknown_query_type() -> None:
    runtime = LocalKnowledgeRuntime(FakeKnowledgeService, db=object(), model_config=None)
    try:
        runtime.search(
            CapabilityContext(
                request_id="req-3",
                tenant_id="tenant-1",
                agent_id=None,
                user_id=None,
                session_id="session-1",
                turn_id="turn-1",
                channel="web",
            ),
            KnowledgeSearchQuery(query="x", query_type="future_mode"),
        )
    except CapabilityProviderError as exc:
        assert exc.info.code == "KNOWLEDGE_UNSUPPORTED_QUERY_TYPE"
    else:  # pragma: no cover - unknown operation semantics must not be downgraded
        raise AssertionError("unknown query type was silently downgraded")
