from app.capabilities.local_registry import (
    LOCAL_KNOWLEDGE_CONFIG_REVISION,
    LOCAL_KNOWLEDGE_DEPLOYMENT,
    build_local_capability_registry,
)


def test_local_registry_pins_all_knowledge_operations_to_one_deployment() -> None:
    registry = build_local_capability_registry(db=object(), service_factory=object)
    snapshot = registry.snapshot(
        {"knowledge.scopes", "knowledge.search", "knowledge.citation"},
        supported_contracts={
            "knowledge.scopes": {"knowledge.v1"},
            "knowledge.search": {"knowledge.v1"},
            "knowledge.citation": {"knowledge.v1"},
        },
    )

    assert {binding.provider_deployment_id for binding in snapshot.durable_bindings} == {
        LOCAL_KNOWLEDGE_DEPLOYMENT
    }
    assert {binding.config_revision for binding in snapshot.durable_bindings} == {
        LOCAL_KNOWLEDGE_CONFIG_REVISION
    }
    restored = registry.rehydrate(snapshot.durable_bindings[0])
    assert restored.provider_id == "local_knowledge"


def test_local_registry_rejects_retired_config_revision() -> None:
    registry = build_local_capability_registry(db=object(), service_factory=object)
    snapshot = registry.snapshot({"knowledge.search"})
    durable = snapshot.durable_bindings[0]
    retired = type(durable)(
        capability=durable.capability,
        provider_id=durable.provider_id,
        provider_deployment_id=durable.provider_deployment_id,
        service_contract_version=durable.service_contract_version,
        operation_versions=durable.operation_versions,
        config_revision="retired-v0",
        resolution_reason=durable.resolution_reason,
    )
    try:
        registry.rehydrate(retired)
    except LookupError as exc:
        assert "retired" in str(exc)
    else:  # pragma: no cover - retired bindings must never be revived
        raise AssertionError("retired local binding unexpectedly rehydrated")
