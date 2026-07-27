from dataclasses import dataclass

import pytest

from app.capabilities.contracts import (
    CapabilityContext,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)
from app.capabilities.registry import CapabilityBinding, CapabilityRegistry


@dataclass
class FakeKnowledge:
    provider_id: str = "local_knowledge"

    def search(self, context: CapabilityContext, request: KnowledgeSearchQuery) -> KnowledgeSearchResult:
        return KnowledgeSearchResult(query_id=context.request_id)


def test_snapshot_freezes_provider_selection_for_a_turn() -> None:
    registry = CapabilityRegistry()
    first = FakeKnowledge()
    registry.register(
        CapabilityBinding("knowledge.search", "local_knowledge", "knowledge.v1", first)
    )

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.require("knowledge.search").provider is first

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            CapabilityBinding("knowledge.search", "remote_knowledge", "knowledge.v1", FakeKnowledge("remote_knowledge"))
        )

    assert snapshot.require("knowledge.search").provider is first
    assert snapshot.snapshot_id


def test_snapshot_only_contains_requested_capabilities() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityBinding("knowledge.search", "local", "knowledge.v1", object()))
    registry.register(CapabilityBinding("scene_skill.catalog", "local", "scene.v1", object()))

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.get("knowledge.search") is not None
    assert snapshot.get("scene_skill.catalog") is None
    with pytest.raises(LookupError, match="scene_skill.catalog"):
        snapshot.require("scene_skill.catalog")


def test_knowledge_result_has_service_owned_shape_and_extensions() -> None:
    result = KnowledgeSearchResult(
        query_id="q1",
        outcome="partial",
        extensions={"vendor_x": {"rerank_score": 0.8}},
    )
    assert result.outcome == "partial"
    assert result.extensions["vendor_x"]["rerank_score"] == 0.8
