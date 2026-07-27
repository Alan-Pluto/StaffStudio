from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

ProviderT = TypeVar("ProviderT")


@dataclass(frozen=True)
class CapabilityBinding(Generic[ProviderT]):
    capability: str
    provider_id: str
    provider_deployment_id: str
    service_contract_version: str
    provider: ProviderT
    operation_versions: tuple[tuple[str, str], ...] = ()
    config_revision: str = "default"
    resolution_reason: str = "configured"

    @property
    def contract_version(self) -> str:
        """Compatibility alias for early adopters of the port."""
        return self.service_contract_version


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Immutable provider selection captured once for an Agent turn."""

    bindings: MappingProxyType
    snapshot_id: str

    def get(self, capability: str) -> CapabilityBinding[Any] | None:
        return cast(CapabilityBinding[Any] | None, self.bindings.get(capability))

    def require(self, capability: str) -> CapabilityBinding[Any]:
        binding = self.get(capability)
        if binding is None:
            raise LookupError(f"capability provider is not configured: {capability}")
        return binding


class CapabilityRegistry:
    """Explicit registry; registration is configuration, not runtime hot swapping."""

    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding[Any]] = {}

    def register(self, binding: CapabilityBinding[Any]) -> None:
        if (
            not binding.capability
            or not binding.provider_id
            or not binding.provider_deployment_id
            or not binding.contract_version
        ):
            raise ValueError(
                "capability, provider_id, provider_deployment_id and contract_version are required"
            )
        if binding.capability in self._bindings:
            raise ValueError(f"capability already registered: {binding.capability}")
        self._bindings[binding.capability] = binding

    def snapshot(
        self,
        requested: set[str] | None = None,
        *,
        supported_contracts: Mapping[str, set[str]] | None = None,
    ) -> CapabilitySnapshot:
        selected = {
            key: value
            for key, value in self._bindings.items()
            if requested is None or key in requested
        }
        if supported_contracts is not None:
            unsupported = [
                f"{key}={binding.service_contract_version}"
                for key, binding in selected.items()
                if binding.service_contract_version
                not in supported_contracts.get(key, set())
            ]
            if unsupported:
                raise ValueError(
                    "unsupported capability contract: " + ", ".join(sorted(unsupported))
                )
        canonical = [
            {
                "capability": key,
                "provider_id": binding.provider_id,
                "provider_deployment_id": binding.provider_deployment_id,
                "service_contract_version": binding.service_contract_version,
                "operation_versions": binding.operation_versions,
                "config_revision": binding.config_revision,
                "resolution_reason": binding.resolution_reason,
            }
            for key, binding in sorted(selected.items())
        ]
        snapshot_id = sha256(
            json.dumps(canonical, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CapabilitySnapshot(MappingProxyType(selected), snapshot_id)
