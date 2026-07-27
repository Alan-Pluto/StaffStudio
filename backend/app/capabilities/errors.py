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

    def to_payload(self) -> dict[str, object]:
        """Map Python ``code`` to the wire contract's ``error_code`` once."""
        return {
            "error_code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "extensions": self.extensions or {},
        }


class CapabilityProviderError(RuntimeError):
    """Stable provider failure; callers must branch on code/retryable."""

    def __init__(self, info: CapabilityErrorInfo) -> None:
        super().__init__(info.message)
        self.info = info


__all__ = ["CapabilityErrorInfo", "CapabilityProviderError"]
