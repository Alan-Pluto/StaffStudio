from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityErrorInfo:
    code: str
    message: str
    retryable: bool
    request_id: str | None = None
    provider_id: str | None = None
    extensions: dict[str, object] | None = None


class CapabilityProviderError(RuntimeError):
    """Stable provider failure; callers must branch on code/retryable."""

    def __init__(self, info: CapabilityErrorInfo) -> None:
        super().__init__(info.message)
        self.info = info


__all__ = ["CapabilityErrorInfo", "CapabilityProviderError"]
