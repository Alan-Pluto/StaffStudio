from __future__ import annotations

from typing import Any

from app.capabilities.contracts import KnowledgeRuntime
from app.capabilities.local_knowledge import LocalKnowledgeRuntime
from app.capabilities.registry import (
    CapabilityBinding,
    CapabilityRegistry,
    DurableCapabilityBinding,
)
from app.knowledge import KnowledgeService

LOCAL_KNOWLEDGE_DEPLOYMENT = "local-process"
LOCAL_KNOWLEDGE_CONFIG_REVISION = "legacy-local-v1"
LOCAL_KNOWLEDGE_CONTRACT = "knowledge.v1"


def build_local_capability_registry(
    db: Any,
    model_config: Any | None = None,
    *,
    service_factory: Any = KnowledgeService,
) -> CapabilityRegistry:
    """Build explicit Local bindings; this function does not touch AgentLoop."""

    runtime = LocalKnowledgeRuntime(service_factory, db, model_config)
    registry = CapabilityRegistry()
    operations = (
        ("knowledge.scopes", "knowledge.scopes.v1"),
        ("knowledge.search", "knowledge.search.v1"),
        ("knowledge.citation", "knowledge.citation.v1"),
    )
    for capability, operation_version in operations:
        registry.register(
            CapabilityBinding(
                capability=capability,
                provider_id=runtime.provider_id,
                provider_deployment_id=LOCAL_KNOWLEDGE_DEPLOYMENT,
                service_contract_version=LOCAL_KNOWLEDGE_CONTRACT,
                provider=runtime,
                operation_versions=((capability, operation_version),),
                config_revision=LOCAL_KNOWLEDGE_CONFIG_REVISION,
            )
        )

    def rehydrate(binding: DurableCapabilityBinding) -> KnowledgeRuntime:
        if (
            binding.service_contract_version != LOCAL_KNOWLEDGE_CONTRACT
            or binding.config_revision != LOCAL_KNOWLEDGE_CONFIG_REVISION
        ):
            raise LookupError("local Knowledge binding revision is retired")
        return LocalKnowledgeRuntime(service_factory, db, model_config)

    registry.register_rehydrator(
        runtime.provider_id,
        LOCAL_KNOWLEDGE_DEPLOYMENT,
        rehydrate,
    )
    return registry
