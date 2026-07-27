from dataclasses import dataclass

import pytest

from app.capabilities.contracts import (
    CapabilityContext,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SceneSkillDefinition,
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
        CapabilityBinding("knowledge.search", "local_knowledge", "local-dev", "knowledge.v1", first)
    )

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.require("knowledge.search").provider is first

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            CapabilityBinding("knowledge.search", "remote_knowledge", "remote-prod", "knowledge.v1", FakeKnowledge("remote_knowledge"))
        )

    assert snapshot.require("knowledge.search").provider is first
    assert snapshot.snapshot_id


def test_snapshot_only_contains_requested_capabilities() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v1", object()))
    registry.register(CapabilityBinding("scene_skill.catalog", "local", "local-dev", "scene.v1", object()))

    snapshot = registry.snapshot({"knowledge.search"})
    assert snapshot.get("knowledge.search") is not None
    assert snapshot.get("scene_skill.catalog") is None
    with pytest.raises(LookupError, match="scene_skill.catalog"):
        snapshot.require("scene_skill.catalog")


def test_snapshot_rejects_contracts_outside_consumer_matrix() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v2", object()))

    with pytest.raises(ValueError, match="unsupported capability contract"):
        registry.snapshot(
            {"knowledge.search"},
            supported_contracts={"knowledge.search": {"knowledge.v1"}},
        )


def test_rehydrate_uses_saved_deployment_identity_without_reresolving() -> None:
    registry = CapabilityRegistry()
    provider = object()
    binding = CapabilityBinding("knowledge.search", "local", "local-dev", "knowledge.v1", provider)
    registry.register(binding)
    snapshot = registry.snapshot({"knowledge.search"})
    restored = object()
    registry.register_rehydrator("local", "local-dev", lambda durable: restored)

    assert registry.rehydrate(snapshot.durable_bindings[0]) is restored


def test_knowledge_result_has_service_owned_shape_and_extensions() -> None:
    result = KnowledgeSearchResult(
        query_id="q1",
        outcome="partial",
        extensions={"vendor_x": {"rerank_score": 0.8}},
    )
    assert result.outcome == "partial"
    assert result.extensions["vendor_x"]["rerank_score"] == 0.8


def test_scene_definition_exposes_terminal_nodes_without_shifting_legacy_arguments() -> None:
    definition = SceneSkillDefinition(
        "expense_approval",
        "1.0.0",
        "sha256:definition",
        "collect_amount",
        (),
        (),
        {"vendor_x": {"revision": 1}},
        ("complete",),
    )
    assert definition.extensions["vendor_x"]["revision"] == 1
    assert definition.terminal_node_ids == ("complete",)
